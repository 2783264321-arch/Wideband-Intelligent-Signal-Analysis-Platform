"""D5 generic Analysis Package v1 publisher for remote plugin results.

Reuses the existing Analysis Package v1 schema exactly (``Manifest`` /
``PackageDetection``); there is no second result schema. A ``PipelineOutput`` is
serialized into a ZIP whose root ``manifest.json`` + referenced
``detections.json`` are accepted by the existing ``validate_extracted_package`` /
``result_ingestor`` path.

The publisher is generic: pipeline identity comes from the ``PipelineDefinition``,
the label space from the resolved **output** label space, and execution metadata
exactly from ``RuntimeDescriptor.public_projection()`` (never the private
``environment_ref``, precision, or any deployment path). ``publish_package`` is
the production callable that matches ``PluginItemExecutor``'s injected
package-publisher seam and delegates final atomic/write-once persistence to
``result_publisher.publish_result`` — there is no second result path.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
import json
import zipfile

from app.imported_runs.schema import (
    ExecutionMetadata,
    Manifest,
    PackageDetection,
    PipelineMetadata,
    RecordingMetadata,
    ResultPaths,
)
from app.pipelines.base import DetectionPayload, PipelineOutput
from app.remote_execution.result_publisher import publish_result

_DETECTIONS_FILENAME = "detections.json"
_ZIP_FILENAME = "analysis_result.zip"


def manifest_for(
    *,
    pipeline_definition,
    label_space: str,
    recording_name: str,
    dataset_name: str,
    runtime_descriptor,
    parameters: dict[str, Any] | None = None,
) -> Manifest:
    """Exact Analysis Package v1 manifest from generic deployment inputs.

    Execution is derived only from ``runtime_descriptor.public_projection()``
    (executor / device / environment); private runtime data never enters here.
    """
    projection = runtime_descriptor.public_projection()
    return Manifest(
        schema_version=1,
        pipeline=PipelineMetadata(
            id=pipeline_definition.id,
            name=pipeline_definition.name,
            version=pipeline_definition.version,
        ),
        label_space=label_space,
        recording=RecordingMetadata(name=recording_name, dataset=dataset_name),
        execution=ExecutionMetadata(**projection),
        results=ResultPaths(detections=_DETECTIONS_FILENAME),
        parameters=dict(parameters or {}),
    )


def _detection_to_package(detection: DetectionPayload) -> PackageDetection:
    """1:1 DetectionPayload -> PackageDetection preserving every field."""
    return PackageDetection(
        t_start_s=detection.t_start_s,
        t_end_s=detection.t_end_s,
        f_low_hz=detection.f_low_hz,
        f_high_hz=detection.f_high_hz,
        class_id=detection.class_id,
        class_name=detection.class_name,
        confidence=detection.confidence,
        scores=detection.scores,
    )


def build_analysis_package_zip(
    output: PipelineOutput,
    *,
    pipeline_definition,
    label_space: str,
    runtime_descriptor,
    parameters: dict[str, Any] | None,
    recording_name: str,
    dataset_name: str,
    workspace: Path,
) -> Path:
    """Serialize ``PipelineOutput`` -> detections.json + manifest.json inside a
    ZIP under ``workspace`` and return the zip path."""
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    zip_path = workspace / _ZIP_FILENAME
    manifest = manifest_for(
        pipeline_definition=pipeline_definition,
        label_space=label_space,
        recording_name=recording_name,
        dataset_name=dataset_name,
        runtime_descriptor=runtime_descriptor,
        parameters=parameters,
    )
    detections = [_detection_to_package(detection) for detection in output.detections]
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(manifest.model_dump(mode="json"), sort_keys=True),
        )
        archive.writestr(
            _DETECTIONS_FILENAME,
            json.dumps(
                {"detections": [detection.model_dump(mode="json") for detection in detections]},
                sort_keys=True,
            ),
        )
    return zip_path


def _label_space_id(output_label_space: Any) -> str:
    """The D3A seam passes a resolved ``LabelSpace``; accept a raw id too."""
    if isinstance(output_label_space, str):
        return output_label_space
    return output_label_space.id


def _public_hardware(runtime_descriptor) -> dict:
    """Descriptor-derived hardware provenance: public fields only."""
    projection = runtime_descriptor.public_projection()
    return {"executor": projection["executor"], "device": projection["device"]}


def publish_package(
    *,
    output: PipelineOutput,
    pipeline_definition,
    output_label_space: Any,
    runtime_descriptor,
    item,
    batch,
    job_root,
    workspace: Path,
    asset_manifest_sha256: str,
    remote_started_at: datetime,
    remote_finished_at: datetime,
    hardware: dict | None = None,
) -> None:
    """Production package-publisher callable for ``PluginItemExecutor``.

    Builds the generic Analysis Package v1 ZIP from the frozen item parameters and
    the resolved output label space, then delegates atomic/write-once persistence
    to ``publish_result``.
    """
    label_space_id = _label_space_id(output_label_space)
    zip_path = build_analysis_package_zip(
        output,
        pipeline_definition=pipeline_definition,
        label_space=label_space_id,
        runtime_descriptor=runtime_descriptor,
        parameters=item.parameters,
        recording_name=item.recording.dataset_key,
        dataset_name=item.recording.dataset_name,
        workspace=Path(workspace),
    )
    publish_result(
        job_root=Path(job_root),
        item=item,
        batch=batch,
        zip_path=zip_path,
        remote_runtime_commit=batch.required_remote_runtime_commit,
        asset_manifest_sha256=asset_manifest_sha256,
        hardware=dict(hardware) if hardware is not None else _public_hardware(runtime_descriptor),
        remote_started_at=remote_started_at,
        remote_finished_at=remote_finished_at,
    )
