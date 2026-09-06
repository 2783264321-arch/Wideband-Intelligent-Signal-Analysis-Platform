"""Idempotent ingestion of remote execution results into the SAME AnalysisRun.

The ingestor verifies the strict wire envelope and the exact ZIP byte identity,
then reuses the bounded analysis-package archive/validation primitives and the
shared :class:`AnalysisResultWriter`. It never commits or rolls back; the caller
owns the entire local transaction.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile

from sqlalchemy.orm import Session

from app.analysis.model import AnalysisRunModel
from app.core.errors import PlatformError
from app.imported_runs.archive import extract_package
from app.imported_runs.validation import validate_extracted_package
from app.pipelines.base import DetectionPayload, PipelineOutput
from app.recordings.model import RecordingModel
from app.remote_execution.schema import RemoteExecutionEnvelopeV1
from app.remote_execution.source_hash import compute_file_sha256
from app.remote_execution.validation import AnalysisResultWriter

_REQUIRED_METADATA_KEYS = (
    "request_id",
    "batch_id",
    "item_key",
    "recording_fingerprint",
    "source_data_sha256",
    "orchestrator_commit",
    "required_remote_runtime_commit",
    "asset_manifest_sha256",
)


def _remote_invalid(message: str) -> PlatformError:
    return PlatformError("REMOTE_RESULT_INVALID", message)


def _reject_duplicate_json_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_non_finite_json_constant(value: str):
    raise ValueError(f"non-finite JSON constant: {value!r}")


def parse_remote_execution_envelope_json(raw: bytes | str) -> RemoteExecutionEnvelopeV1:
    """Strict raw-envelope boundary: UTF-8 bytes, duplicate keys rejected at
    every nesting level, non-finite constants rejected, top-level object only.

    Local AnalysisRun identity is NOT verified here; the ingestor owns that.
    """
    try:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        payload = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_non_finite_json_constant,
        )
        if not isinstance(payload, dict):
            raise ValueError("envelope JSON must be a top-level object")
        return RemoteExecutionEnvelopeV1.model_validate(payload)
    except Exception:
        raise _remote_invalid("Remote result envelope is invalid.")


def _verify_envelope_identity(
    run: AnalysisRunModel,
    recording: RecordingModel,
    envelope: RemoteExecutionEnvelopeV1,
    writer: AnalysisResultWriter,
) -> dict:
    metadata = dict(run.execution_metadata_json or {})
    missing = [key for key in _REQUIRED_METADATA_KEYS if key not in metadata]
    if missing:
        raise _remote_invalid("Local execution metadata is missing expected provenance.")

    checks = (
        (envelope.local_run_id == run.id, "envelope local_run_id does not match the AnalysisRun"),
        (envelope.request_id == metadata["request_id"], "envelope request_id does not match local provenance"),
        (envelope.batch_id == metadata["batch_id"], "envelope batch_id does not match local provenance"),
        (envelope.item_key == metadata["item_key"], "envelope item_key does not match local provenance"),
        (envelope.recording_fingerprint == metadata["recording_fingerprint"],
         "envelope recording fingerprint does not match local provenance"),
        (envelope.source_data_sha256 == metadata["source_data_sha256"],
         "envelope source data hash does not match local provenance"),
        (envelope.source_data_sha256 == recording.source_data_sha256,
         "envelope source data hash does not match the Recording"),
        (envelope.orchestrator_commit == metadata["orchestrator_commit"],
         "envelope orchestrator commit does not match local provenance"),
        (envelope.remote_runtime_commit == metadata["required_remote_runtime_commit"],
         "envelope remote runtime commit does not match the required remote runtime"),
        (envelope.asset_manifest_sha256 == metadata["asset_manifest_sha256"],
         "envelope asset manifest hash does not match local provenance"),
        (envelope.pipeline_id == run.pipeline_id, "envelope pipeline id does not match the AnalysisRun"),
        (envelope.pipeline_version == run.pipeline_version, "envelope pipeline version does not match the AnalysisRun"),
        (writer.pipeline_definition.id == run.pipeline_id, "writer pipeline id does not match the AnalysisRun"),
        (writer.pipeline_definition.version == run.pipeline_version, "writer pipeline version does not match the AnalysisRun"),
    )
    for ok, message in checks:
        if not ok:
            raise _remote_invalid(message)
    return metadata


def _validate_manifest_consistency(
    manifest,
    run: AnalysisRunModel,
    recording: RecordingModel,
    envelope: RemoteExecutionEnvelopeV1,
    writer: AnalysisResultWriter,
) -> None:
    if not (manifest.pipeline.id == run.pipeline_id == envelope.pipeline_id == writer.pipeline_definition.id):
        raise _remote_invalid("Remote package pipeline id does not match the AnalysisRun.")
    if not (manifest.pipeline.version == run.pipeline_version == envelope.pipeline_version
            == writer.pipeline_definition.version):
        raise _remote_invalid("Remote package pipeline version does not match the AnalysisRun.")
    if manifest.label_space != recording.label_space or manifest.label_space != writer.pipeline_definition.label_space:
        raise _remote_invalid("Remote package label space does not match the Recording.")
    if manifest.execution.executor != "remote_gpu":
        raise _remote_invalid("Remote package execution executor is not remote_gpu.")
    if manifest.recording.name != recording.name:
        raise _remote_invalid("Remote package recording name does not match the Recording.")
    if manifest.recording.dataset != recording.dataset_name:
        raise _remote_invalid("Remote package recording dataset does not match the Recording.")


def _package_to_pipeline_output(validated, envelope: RemoteExecutionEnvelopeV1) -> PipelineOutput:
    detections = [
        DetectionPayload(
            t_start_s=item.t_start_s,
            t_end_s=item.t_end_s,
            f_low_hz=item.f_low_hz,
            f_high_hz=item.f_high_hz,
            class_id=item.class_id,
            class_name=item.class_name,
            confidence=item.confidence,
            scores=item.scores,
        )
        for item in validated.detections
    ]
    return PipelineOutput(
        detections=detections,
        artifacts=[],
        run_metadata={
            "source": "remote_gpu",
            "request_id": envelope.request_id,
            "batch_id": envelope.batch_id,
            "item_key": envelope.item_key,
            "payload_sha256": envelope.payload_sha256,
            "remote_runtime_commit": envelope.remote_runtime_commit,
        },
    )


def ingest_remote_result(
    session: Session,
    run_id: str,
    envelope: RemoteExecutionEnvelopeV1,
    zip_path: Path,
    writer: AnalysisResultWriter,
) -> str:
    """Verify identity + exact payload hash, then persist into the SAME run.

    Returns the verified ``payload_sha256``. The caller owns the transaction.
    """
    run = session.get(AnalysisRunModel, run_id)
    if run is None:
        raise PlatformError("ANALYSIS_RUN_NOT_FOUND", "Analysis run was not found.", 404)
    recording = session.get(RecordingModel, run.recording_id)
    if recording is None:
        raise PlatformError("RECORDING_NOT_FOUND", "Recording was not found.", 404)

    if run.executor != "remote_gpu":
        raise _remote_invalid("Remote results may only target a remote_gpu AnalysisRun.")

    metadata = _verify_envelope_identity(run, recording, envelope, writer)

    if not zip_path.is_file():
        raise _remote_invalid("Remote result ZIP is missing or is not a regular file.")
    actual_payload_sha256 = compute_file_sha256(zip_path)
    if actual_payload_sha256 != envelope.payload_sha256:
        raise _remote_invalid("Remote result payload SHA256 does not match the envelope.")

    stored_payload = metadata.get("payload_sha256")
    if run.status == "completed":
        if stored_payload == envelope.payload_sha256:
            return envelope.payload_sha256
        raise PlatformError(
            "REMOTE_RESULT_CONFLICT", "Completed AnalysisRun has a conflicting remote result payload."
        )
    if run.status in {"failed", "interrupted"}:
        raise _remote_invalid("Terminal AnalysisRun cannot be resurrected by a remote result.")
    if run.status not in {"pending", "running"}:
        raise _remote_invalid("AnalysisRun is not in a state that accepts a remote result.")

    with tempfile.TemporaryDirectory() as temp_dir:
        try:
            with zip_path.open("rb") as source:
                root = extract_package(source, Path(temp_dir))
            validated = validate_extracted_package(root, recording, writer.label_service)
        except PlatformError as exc:
            if exc.code in {"INVALID_IMPORT_PACKAGE", "LABEL_SPACE_NOT_FOUND"}:
                raise _remote_invalid(exc.message) from exc
            raise
        except Exception:
            raise _remote_invalid("Remote result package is invalid.")
        _validate_manifest_consistency(validated.manifest, run, recording, envelope, writer)
        output = _package_to_pipeline_output(validated, envelope)

    writer.persist(run=run, recording=recording, output=output)

    updated_metadata = dict(metadata)
    updated_metadata["payload_sha256"] = envelope.payload_sha256
    updated_metadata["remote_runtime_commit"] = envelope.remote_runtime_commit
    updated_metadata["remote_started_at"] = (
        envelope.remote_started_at.isoformat() if envelope.remote_started_at else None
    )
    updated_metadata["remote_finished_at"] = (
        envelope.remote_finished_at.isoformat() if envelope.remote_finished_at else None
    )
    run.execution_metadata_json = updated_metadata
    run.hardware_info_json = dict(envelope.hardware)

    return envelope.payload_sha256