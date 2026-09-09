"""Remote executor negative/error-semantics tests (Task 12F-B Task 7).

Lock the error semantics end to end (CPU/no-GPU, using fakes): explicit
PlatformError codes from request/runtime/asset/fingerprint/source mismatches
surface as the runner item status ``failed``; pipeline/scientific exceptions
map to ``PIPELINE_EXECUTION_FAILED``; terminal artifact corruption maps to
``REMOTE_RESULT_CORRUPTED``/``interrupted``. No traceback or arbitrary local
path ever leaks into protocol error messages / status.

Each test is recorded as either a real RED->fix->GREEN cycle or COVERAGE
CONFIRMED (pass-first). No fabricated RED evidence.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.errors import PlatformError
from app.pipelines.base import PipelineOutput, RecordingInput
from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.definition import ZOOMSPEC_FROZEN_DEFINITION
from app.remote_execution import zoomspec_executor as ze
from app.remote_execution.canonical import compute_request_sha256
from app.remote_execution.resolver import ResolvedSpaceNetInput
from app.remote_execution.runner import create_or_attach, reconcile_status, run_work
from app.remote_execution.schema import (
    RemoteExecutionBatchV1,
    RemoteExecutionItemV1,
    RemoteRecordingRefV1,
)
from app.remote_execution.worker_context import RemoteWorkerContext

ORCHESTRATOR_COMMIT = "9a6f0feac0b0e6e2ac8ecd65d2e4383479e09f7c"
COMMIT = "68b1464842d0fb366fc211f53436d0ba49e3fbef"
MANIFEST_SHA = "c" * 64


def _make_batch(*, parameters=None, batch_id="batch_x", item_key="000000"):
    batch = RemoteExecutionBatchV1(
        schema_version=1,
        batch_id=batch_id,
        required_remote_runtime_commit=COMMIT,
        pipeline={"id": ZOOMSPEC_FROZEN_DEFINITION.id, "version": ZOOMSPEC_FROZEN_DEFINITION.version},
        asset_manifest_sha256=MANIFEST_SHA,
        items=[RemoteExecutionItemV1(
            item_key=item_key,
            request_id="req_1",
            local_run_id="run_x",
            orchestrator_commit=ORCHESTRATOR_COMMIT,
            recording=RemoteRecordingRefV1(
                dataset_name="SpaceNet", dataset_split="test", dataset_key="0",
                label_space="spacenet_14",
                expected_recording_fingerprint="a" * 64,
                expected_source_data_sha256="b" * 64,
            ),
            parameters=parameters or {},
        )],
        request_sha256="0" * 64,
    )
    batch.request_sha256 = compute_request_sha256(batch)
    return batch


def _worker(tmp_path: Path) -> RemoteWorkerContext:
    repo_root = tmp_path / "repo"
    return RemoteWorkerContext(
        repo_root=repo_root,
        job_root=tmp_path / "jobs",
        required_runtime_commit=COMMIT,
        dataset_root_space_net=tmp_path / "spacenet",
        detector_checkpoint=tmp_path / "det.pt",
        frn_checkpoint=tmp_path / "frn.pt",
        frozen_config_path=tmp_path / "frozen.json",
        ls_stft_normalization_path=tmp_path / "norm.json",
        label_space_root=repo_root / "label_spaces",
        asset_manifest_path=(
            repo_root / "backend" / "app" / "pipelines"
            / "zoomspec_yolo26n_aug_combined_frn_v3" / "asset_manifest.json"
        ),
    )


def _job_root(tmp_path, batch_id="batch_x") -> Path:
    jobs = tmp_path / "jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    return jobs / batch_id


class RaisingPipeline:
    def __init__(self, exc=None):
        self.exc = exc

    def run(self, recording_input, parameters, workspace):
        raise self.exc if self.exc is not None else RuntimeError("synthetic pipeline failure")


class SuccessPipeline:
    def run(self, recording_input, parameters, workspace):
        return PipelineOutput(detections=[])


class FakeLabelSpace:
    def get(self, label_space_id):
        return type("LabelSpace", (), {"id": label_space_id, "version": 1, "classes": ()})


def _resolved():
    return ResolvedSpaceNetInput(
        recording_fingerprint="a" * 64,
        source_data_sha256="b" * 64,
        recording_input=RecordingInput(
            id="0",
            data_path=Path("/fake/0.bin"),
            data_format="complex64_le",
            sample_rate_hz=1_000_000.0,
            center_frequency_hz=2_441_000_000.0,
            frequency_low_hz=2_440_500_000.0,
            frequency_high_hz=2_441_500_000.0,
            duration_s=0.1,
            label_space="spacenet_14",
        ),
    )


def _make_executor(
    tmp_path,
    monkeypatch,
    batch,
    *,
    manifest_error=None,
    resolve_error=None,
    pipeline=None,
    real_publish=False,
):
    worker = _worker(tmp_path)
    worker.ls_stft_normalization_path.parent.mkdir(parents=True, exist_ok=True)
    worker.ls_stft_normalization_path.write_text(json.dumps({
        "percentile_low": 1.0,
        "percentile_high": 99.0,
        "value_low": 0.0,
        "value_high": 1.0,
    }), encoding="utf-8")
    fake_pipeline = pipeline if pipeline is not None else RaisingPipeline()

    def fake_verify(manifest_path, asset_paths, repo_root, required_runtime_commit):
        if manifest_error is not None:
            raise manifest_error
        return type("Manifest", (), {"asset_manifest_sha256": MANIFEST_SHA})()

    def fake_resolve(*args):
        if resolve_error is not None:
            raise resolve_error
        return _resolved()

    monkeypatch.setattr(ze, "verify_asset_manifest", fake_verify)
    monkeypatch.setattr(ze, "resolve_space_net", fake_resolve)
    monkeypatch.setattr(ze, "LabelSpaceService", lambda root: FakeLabelSpace())
    if not real_publish:
        monkeypatch.setattr(ze, "publish_result", lambda **kwargs: None)

    def factory(worker_arg, normalization, label_space_arg, device):
        return fake_pipeline

    executor = ze.ZoomSpecRemoteItemExecutor(
        batch=batch, worker=worker, pipeline_factory=factory,
        runtime_info_provider=lambda: {"device": "fake"},
    )
    return executor


def _run(tmp_path, monkeypatch, batch, **executor_kwargs):
    job_root = _job_root(tmp_path)
    create_or_attach(batch, job_root)
    executor = _make_executor(tmp_path, monkeypatch, batch, **executor_kwargs)
    run_work(batch.batch_id, job_root, executor)
    status = reconcile_status(batch.batch_id, job_root)
    return status, job_root


def _assert_no_leak(message, tmp_path):
    assert message is not None
    assert "Traceback" not in message
    assert "File " not in message
    assert str(tmp_path) not in message


# COVERAGE CONFIRMED (pass-first): the executor already raises REMOTE_REQUEST_INVALID
# for non-empty parameters and run_work maps it to item failed.
def test_request_mismatch_maps_to_item_failed(tmp_path, monkeypatch):
    batch = _make_batch(parameters={"x": 1})
    status, _ = _run(tmp_path, monkeypatch, batch)
    assert status.items[0].status == "failed"
    assert status.items[0].error_code == "REMOTE_REQUEST_INVALID"
    _assert_no_leak(status.items[0].error_message, tmp_path)


# COVERAGE CONFIRMED (pass-first): verify_asset_manifest already raises
# REMOTE_IMPLEMENTATION_MISMATCH and run_work maps it to item failed.
def test_runtime_mismatch_maps_to_item_failed(tmp_path, monkeypatch):
    batch = _make_batch()
    status, _ = _run(
        tmp_path, monkeypatch, batch,
        manifest_error=PlatformError("REMOTE_IMPLEMENTATION_MISMATCH", "runtime mismatch"),
    )
    assert status.items[0].status == "failed"
    assert status.items[0].error_code == "REMOTE_IMPLEMENTATION_MISMATCH"
    _assert_no_leak(status.items[0].error_message, tmp_path)


# COVERAGE CONFIRMED (pass-first): verify_asset_manifest already raises
# PIPELINE_ASSET_MISMATCH and run_work maps it to item failed.
def test_asset_mismatch_maps_to_item_failed(tmp_path, monkeypatch):
    batch = _make_batch()
    status, _ = _run(
        tmp_path, monkeypatch, batch,
        manifest_error=PlatformError("PIPELINE_ASSET_MISMATCH", "asset mismatch"),
    )
    assert status.items[0].status == "failed"
    assert status.items[0].error_code == "PIPELINE_ASSET_MISMATCH"
    _assert_no_leak(status.items[0].error_message, tmp_path)


# COVERAGE CONFIRMED (pass-first): resolve_space_net raises the fingerprint
# mismatch and run_work maps it to item failed.
def test_fingerprint_mismatch_maps_to_item_failed(tmp_path, monkeypatch):
    batch = _make_batch()
    status, _ = _run(
        tmp_path, monkeypatch, batch,
        resolve_error=PlatformError("RECORDING_FINGERPRINT_MISMATCH", "fingerprint mismatch"),
    )
    assert status.items[0].status == "failed"
    assert status.items[0].error_code == "RECORDING_FINGERPRINT_MISMATCH"
    _assert_no_leak(status.items[0].error_message, tmp_path)


# COVERAGE CONFIRMED (pass-first): resolve_space_net raises the source hash
# mismatch and run_work maps it to item failed.
def test_source_hash_mismatch_maps_to_item_failed(tmp_path, monkeypatch):
    batch = _make_batch()
    status, _ = _run(
        tmp_path, monkeypatch, batch,
        resolve_error=PlatformError("SOURCE_DATA_HASH_MISMATCH", "source hash mismatch"),
    )
    assert status.items[0].status == "failed"
    assert status.items[0].error_code == "SOURCE_DATA_HASH_MISMATCH"
    _assert_no_leak(status.items[0].error_message, tmp_path)


# COVERAGE CONFIRMED (pass-first): a generic pipeline exception is isolated per
# item as PIPELINE_EXECUTION_FAILED without any traceback in status.
def test_pipeline_execution_failure_maps_to_item_failed(tmp_path, monkeypatch):
    batch = _make_batch()
    status, _ = _run(
        tmp_path, monkeypatch, batch,
        pipeline=RaisingPipeline(RuntimeError("synthetic scientific failure")),
    )
    assert status.items[0].status == "failed"
    assert status.items[0].error_code == "PIPELINE_EXECUTION_FAILED"
    assert status.items[0].error_message == "Remote pipeline execution failed."
    _assert_no_leak(status.items[0].error_message, tmp_path)
    assert "synthetic scientific failure" not in (status.items[0].error_message or "")


# COVERAGE CONFIRMED (pass-first): a PlatformError raised inside pipeline.run
# keeps its explicit code without leaking a traceback.
def test_pipeline_platform_error_keeps_explicit_code(tmp_path, monkeypatch):
    batch = _make_batch()
    status, _ = _run(
        tmp_path, monkeypatch, batch,
        pipeline=RaisingPipeline(PlatformError("PIPELINE_EXECUTION_FAILED", "scientific error")),
    )
    assert status.items[0].status == "failed"
    assert status.items[0].error_code == "PIPELINE_EXECUTION_FAILED"
    _assert_no_leak(status.items[0].error_message, tmp_path)


# COVERAGE CONFIRMED (pass-first): terminal artifact corruption (tampered zip)
# is write-once: interrupted with REMOTE_RESULT_CORRUPTED, never regenerated.
def test_terminal_artifact_corruption_maps_to_interrupted(tmp_path, monkeypatch):
    batch = _make_batch()
    job_root = _job_root(tmp_path)
    create_or_attach(batch, job_root)
    executor = _make_executor(
        tmp_path, monkeypatch, batch, real_publish=True, pipeline=SuccessPipeline()
    )
    run_work(batch.batch_id, job_root, executor)
    payload_path = job_root / "results" / "000000" / "analysis_result.zip"
    payload_path.write_bytes(b"tampered")
    run_work(batch.batch_id, job_root, executor)
    status = reconcile_status(batch.batch_id, job_root)
    assert status.status == "interrupted"
    assert status.items[0].status == "interrupted"
    assert status.items[0].error_code == "REMOTE_RESULT_CORRUPTED"
    assert payload_path.read_bytes() == b"tampered"  # never regenerated
    _assert_no_leak(status.items[0].error_message, tmp_path)