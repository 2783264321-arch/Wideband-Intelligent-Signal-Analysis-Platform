"""ZoomSpecRemoteItemExecutor tests (Task 12F-B Task 5).

CPU/no-GPU: assets/resolver/label-space/runtime-info and the pipeline are all
faked/monkeypatched. The executor must own pipeline identity (from
``batch.pipeline``), empty parameters, double-identity resolution (via
``resolve_space_net``), the workspace under ``job_root``, and atomic
publication of envelope + ZIP accepted by the existing runner verification.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app.core.errors import PlatformError
from app.pipelines.base import PipelineOutput, RecordingInput
from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.definition import ZOOMSPEC_FROZEN_DEFINITION
from app.remote_execution import zoomspec_executor
from app.remote_execution.canonical import compute_request_sha256
from app.remote_execution.resolver import ResolvedSpaceNetInput
from app.remote_execution.result_ingestor import parse_remote_execution_envelope_json
from app.remote_execution.runner import _verify_terminal_result
from app.remote_execution.schema import (
    RemoteExecutionBatchV1,
    RemoteExecutionEnvelopeV1,
    RemoteExecutionItemV1,
    RemoteRecordingRefV1,
)
from app.remote_execution.worker_context import RemoteWorkerContext

ORCHESTRATOR_COMMIT = "9a6f0feac0b0e6e2ac8ecd65d2e4383479e09f7c"
COMMIT = "68b1464842d0fb366fc211f53436d0ba49e3fbef"
MANIFEST_SHA = "c" * 64
BACKEND_ROOT = str(Path(__file__).resolve().parents[1])

FAKE_HARDWARE = {
    "device_index": 0,
    "device_type": "cuda",
    "device_name": "Fake GPU",
    "torch_version": "2.0.0",
    "cuda_version": "12.4",
}


def _make_batch(
    *,
    pipeline_id=ZOOMSPEC_FROZEN_DEFINITION.id,
    pipeline_version=ZOOMSPEC_FROZEN_DEFINITION.version,
    parameters=None,
    batch_id="batch_x",
    item_key="000000",
    runtime_commit=COMMIT,
    dataset_name="SpaceNet",
    label_space="spacenet_14",
):
    batch = RemoteExecutionBatchV1(
        schema_version=1,
        batch_id=batch_id,
        required_remote_runtime_commit=runtime_commit,
        pipeline={"id": pipeline_id, "version": pipeline_version},
        asset_manifest_sha256=MANIFEST_SHA,
        items=[RemoteExecutionItemV1(
            item_key=item_key,
            request_id="req_1",
            local_run_id="run_x",
            orchestrator_commit=ORCHESTRATOR_COMMIT,
            recording=RemoteRecordingRefV1(
                dataset_name=dataset_name, dataset_split="test", dataset_key="0",
                label_space=label_space,
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


def _recording_input() -> RecordingInput:
    return RecordingInput(
        id="0",
        data_path=Path("/fake/0.bin"),
        data_format="complex64_le",
        sample_rate_hz=1_000_000.0,
        center_frequency_hz=2_441_000_000.0,
        frequency_low_hz=2_440_500_000.0,
        frequency_high_hz=2_441_500_000.0,
        duration_s=0.1,
        label_space="spacenet_14",
    )


def _fake_resolved():
    return ResolvedSpaceNetInput(
        recording_fingerprint="a" * 64,
        source_data_sha256="b" * 64,
        recording_input=_recording_input(),
    )


class FakePipeline:
    def __init__(self):
        self.calls = []

    def run(self, recording_input, parameters, workspace):
        self.calls.append((recording_input, parameters, workspace))
        return PipelineOutput(detections=[])


class FakeLabelSpace:
    def __init__(self, captured):
        self.captured = captured

    def get(self, label_space_id):
        self.captured["label_space_ids"].append(label_space_id)
        return type("LabelSpace", (), {"id": label_space_id, "version": 1, "classes": ()})


def _fake_manifest():
    return type("Manifest", (), {"asset_manifest_sha256": MANIFEST_SHA})


def _setup(tmp_path, monkeypatch, *, real_publish=False):
    worker = _worker(tmp_path)
    worker.ls_stft_normalization_path.parent.mkdir(parents=True, exist_ok=True)
    worker.ls_stft_normalization_path.write_text(json.dumps({
        "percentile_low": 1.0,
        "percentile_high": 99.0,
        "value_low": 0.0,
        "value_high": 1.0,
    }), encoding="utf-8")
    captured = {
        "verify_manifest": None,
        "resolver_args": None,
        "pipeline_constructed": None,
        "pipeline": None,
        "label_space_ids": [],
        "publish_kwargs": None,
        "hardware": FAKE_HARDWARE,
    }
    label_space = FakeLabelSpace(captured)

    def fake_verify(manifest_path, asset_paths, repo_root, required_runtime_commit):
        captured["verify_manifest"] = {
            "manifest_path": manifest_path,
            "asset_paths": asset_paths,
            "repo_root": repo_root,
            "required_runtime_commit": required_runtime_commit,
        }
        return _fake_manifest()

    def fake_resolve(dataset_root, split, key, label_space_id, expected_fingerprint, expected_source_hash, label_space_root):
        captured["resolver_args"] = {
            "dataset_root": dataset_root,
            "split": split,
            "key": key,
            "label_space": label_space_id,
            "expected_fingerprint": expected_fingerprint,
            "expected_source_hash": expected_source_hash,
            "label_space_root": label_space_root,
        }
        return _fake_resolved()

    def fake_publish(**kwargs):
        captured["publish_kwargs"] = kwargs

    monkeypatch.setattr(zoomspec_executor, "verify_asset_manifest", fake_verify)
    monkeypatch.setattr(zoomspec_executor, "resolve_space_net", fake_resolve)
    monkeypatch.setattr(zoomspec_executor, "LabelSpaceService", lambda root: label_space)
    if not real_publish:
        monkeypatch.setattr(zoomspec_executor, "publish_result", fake_publish)
    return worker, captured


def _make_executor(tmp_path, monkeypatch, batch=None, *, pipeline_factory=None, runtime_info_provider=None, real_publish=False):
    worker, captured = _setup(tmp_path, monkeypatch, real_publish=real_publish)

    def factory(worker_arg, normalization, label_space_arg, device):
        captured["pipeline_constructed"] = {
            "normalization": normalization,
            "label_space": label_space_arg,
            "device": device,
        }
        pipeline = FakePipeline()
        captured["pipeline"] = pipeline
        return pipeline

    pipeline_factory = pipeline_factory or factory
    runtime_info_provider = runtime_info_provider or (lambda: captured["hardware"])
    executor = zoomspec_executor.ZoomSpecRemoteItemExecutor(
        batch=batch or _make_batch(),
        worker=worker,
        pipeline_factory=pipeline_factory,
        runtime_info_provider=runtime_info_provider,
    )
    return executor, worker, captured


def _job_root(tmp_path, batch_id="batch_x"):
    jobs = tmp_path / "jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    return jobs / batch_id


def test_execute_requires_batch_pipeline_id_match(tmp_path, monkeypatch):
    executor, worker, captured = _make_executor(
        tmp_path, monkeypatch, batch=_make_batch(pipeline_id="wrong_pipeline")
    )
    with pytest.raises(PlatformError) as exc:
        executor.execute(_make_batch(pipeline_id="wrong_pipeline").items[0], _job_root(tmp_path))
    assert exc.value.code == "REMOTE_IMPLEMENTATION_MISMATCH"
    assert captured["pipeline_constructed"] is None


def test_execute_requires_batch_pipeline_version_match(tmp_path, monkeypatch):
    executor, worker, captured = _make_executor(
        tmp_path, monkeypatch, batch=_make_batch(pipeline_version="9.9.9")
    )
    with pytest.raises(PlatformError) as exc:
        executor.execute(_make_batch(pipeline_version="9.9.9").items[0], _job_root(tmp_path))
    assert exc.value.code == "REMOTE_IMPLEMENTATION_MISMATCH"
    assert captured["pipeline_constructed"] is None


def test_execute_uses_batch_pipeline_identity_only(tmp_path, monkeypatch):
    batch = _make_batch()
    executor, worker, captured = _make_executor(tmp_path, monkeypatch, batch=batch)
    executor.execute(batch.items[0], _job_root(tmp_path))
    assert captured["publish_kwargs"] is not None
    assert captured["publish_kwargs"]["batch"].pipeline.id == ZOOMSPEC_FROZEN_DEFINITION.id


def test_execute_rejects_non_empty_parameters(tmp_path, monkeypatch):
    batch = _make_batch(parameters={"x": 1})
    executor, worker, captured = _make_executor(tmp_path, monkeypatch, batch=batch)
    with pytest.raises(PlatformError) as exc:
        executor.execute(batch.items[0], _job_root(tmp_path))
    assert exc.value.code == "REMOTE_REQUEST_INVALID"
    assert captured["pipeline_constructed"] is None
    assert captured["publish_kwargs"] is None


def test_execute_runtime_drift_fails_before_any_work(tmp_path, monkeypatch):
    """Defense in depth: frozen request runtime != worker deployment runtime ->
    REMOTE_IMPLEMENTATION_MISMATCH before verify_asset_manifest / resolver /
    pipeline construction / publication."""
    batch = _make_batch(runtime_commit="a" * 40)
    executor, worker, captured = _make_executor(tmp_path, monkeypatch, batch=batch)
    with pytest.raises(PlatformError) as exc:
        executor.execute(batch.items[0], _job_root(tmp_path))
    assert exc.value.code == "REMOTE_IMPLEMENTATION_MISMATCH"
    assert captured["verify_manifest"] is None
    assert captured["resolver_args"] is None
    assert captured["pipeline_constructed"] is None
    assert captured["publish_kwargs"] is None


def test_execute_wrong_dataset_name_fails_before_resolver(tmp_path, monkeypatch):
    batch = _make_batch(dataset_name="OtherSet")
    executor, worker, captured = _make_executor(tmp_path, monkeypatch, batch=batch)
    with pytest.raises(PlatformError) as exc:
        executor.execute(batch.items[0], _job_root(tmp_path))
    assert exc.value.code == "REMOTE_REQUEST_INVALID"
    assert captured["resolver_args"] is None
    assert captured["pipeline_constructed"] is None
    assert captured["publish_kwargs"] is None


def test_execute_wrong_label_space_fails_before_resolver(tmp_path, monkeypatch):
    batch = _make_batch(label_space="signal_presence_v1")
    executor, worker, captured = _make_executor(tmp_path, monkeypatch, batch=batch)
    with pytest.raises(PlatformError) as exc:
        executor.execute(batch.items[0], _job_root(tmp_path))
    assert exc.value.code == "REMOTE_REQUEST_INVALID"
    assert captured["resolver_args"] is None
    assert captured["pipeline_constructed"] is None
    assert captured["publish_kwargs"] is None


def test_execute_valid_spacenet_contract_unchanged(tmp_path, monkeypatch):
    batch = _make_batch()
    executor, worker, captured = _make_executor(tmp_path, monkeypatch, batch=batch)
    executor.execute(batch.items[0], _job_root(tmp_path))
    assert captured["resolver_args"] is not None
    assert captured["publish_kwargs"] is not None


def test_execute_loads_label_space_from_frozen_definition(tmp_path, monkeypatch):
    batch = _make_batch()
    executor, worker, captured = _make_executor(tmp_path, monkeypatch, batch=batch)
    executor.execute(batch.items[0], _job_root(tmp_path))
    assert captured["label_space_ids"] == [ZOOMSPEC_FROZEN_DEFINITION.label_space]
    assert captured["label_space_ids"] == ["spacenet_14"]


def test_execute_verifies_runtime_commit_and_assets_fail_closed(tmp_path, monkeypatch):
    worker, captured = _setup(tmp_path, monkeypatch)

    def boom(manifest_path, asset_paths, repo_root, required_runtime_commit):
        raise PlatformError("REMOTE_IMPLEMENTATION_MISMATCH", "runtime mismatch")

    monkeypatch.setattr(zoomspec_executor, "verify_asset_manifest", boom)
    batch = _make_batch()
    executor = zoomspec_executor.ZoomSpecRemoteItemExecutor(
        batch=batch, worker=worker,
        pipeline_factory=lambda *a, **k: FakePipeline(),
        runtime_info_provider=lambda: FAKE_HARDWARE,
    )
    with pytest.raises(PlatformError) as exc:
        executor.execute(batch.items[0], _job_root(tmp_path))
    assert exc.value.code == "REMOTE_IMPLEMENTATION_MISMATCH"
    assert captured["pipeline_constructed"] is None
    assert captured["publish_kwargs"] is None


def test_execute_calls_pipeline_with_empty_params_and_device_zero(tmp_path, monkeypatch):
    batch = _make_batch()
    executor, worker, captured = _make_executor(tmp_path, monkeypatch, batch=batch)
    executor.execute(batch.items[0], _job_root(tmp_path))
    assert captured["pipeline_constructed"]["device"] == 0
    pipeline = captured["pipeline_constructed"]
    assert pipeline["normalization"].percentile_low == 1.0
    assert pipeline["normalization"].value_high == 1.0
    publish = captured["publish_kwargs"]
    assert publish["batch"] is batch
    assert publish["item"].item_key == "000000"


def test_execute_verifies_recording_fingerprint_and_source_hash(tmp_path, monkeypatch):
    batch = _make_batch()
    executor, worker, captured = _make_executor(tmp_path, monkeypatch, batch=batch)
    executor.execute(batch.items[0], _job_root(tmp_path))
    resolver_args = captured["resolver_args"]
    assert resolver_args["dataset_root"] == worker.dataset_root_space_net
    assert resolver_args["split"] == "test"
    assert resolver_args["key"] == "0"
    assert resolver_args["label_space"] == "spacenet_14"
    assert resolver_args["expected_fingerprint"] == "a" * 64
    assert resolver_args["expected_source_hash"] == "b" * 64
    assert resolver_args["label_space_root"] == worker.label_space_root


def test_execute_ground_truth_never_reaches_inference(tmp_path, monkeypatch):
    batch = _make_batch()
    executor, worker, captured = _make_executor(tmp_path, monkeypatch, batch=batch)
    executor.execute(batch.items[0], _job_root(tmp_path))
    recording_input, parameters, workspace = captured["pipeline"].calls[0]
    assert recording_input == _recording_input()
    assert not hasattr(recording_input, "ground_truth")
    assert parameters == {}


def test_execute_hardware_comes_from_runtime_info_provider(tmp_path, monkeypatch):
    batch = _make_batch()
    executor, worker, captured = _make_executor(
        tmp_path, monkeypatch, batch=batch,
        runtime_info_provider=lambda: {"custom": "runtime"},
    )
    executor.execute(batch.items[0], _job_root(tmp_path))
    assert captured["publish_kwargs"]["hardware"] == {"custom": "runtime"}


def test_execute_records_remote_started_and_finished_at(tmp_path, monkeypatch):
    batch = _make_batch()
    executor, worker, captured = _make_executor(tmp_path, monkeypatch, batch=batch)
    executor.execute(batch.items[0], _job_root(tmp_path))
    publish = captured["publish_kwargs"]
    started_at = publish["remote_started_at"]
    finished_at = publish["remote_finished_at"]
    assert isinstance(started_at, datetime) and started_at.tzinfo is not None
    assert isinstance(finished_at, datetime) and finished_at.tzinfo is not None
    assert started_at.utcoffset() is not None
    assert finished_at.utcoffset() is not None
    assert finished_at >= started_at


def test_execute_workspace_is_under_job_root(tmp_path, monkeypatch):
    batch = _make_batch()
    executor, worker, captured = _make_executor(tmp_path, monkeypatch, batch=batch)
    job_root = _job_root(tmp_path)
    executor.execute(batch.items[0], job_root)
    recording_input, parameters, workspace = captured["pipeline"].calls[0]
    assert workspace == job_root / "work" / "000000"
    assert job_root in workspace.parents


def test_execute_publishes_terminal_envelope_and_zip(tmp_path, monkeypatch):
    batch = _make_batch()
    executor, worker, captured = _make_executor(
        tmp_path, monkeypatch, batch=batch, real_publish=True
    )
    job_root = _job_root(tmp_path)
    executor.execute(batch.items[0], job_root)
    _verify_terminal_result(batch, batch.items[0], job_root)  # must not raise
    result_dir = job_root / "results" / "000000"
    assert (result_dir / "envelope.json").is_file()
    assert (result_dir / "analysis_result.zip").is_file()
    parsed = parse_remote_execution_envelope_json(
        (result_dir / "envelope.json").read_bytes()
    )
    assert parsed.remote_runtime_commit == batch.required_remote_runtime_commit
    assert parsed.asset_manifest_sha256 == batch.asset_manifest_sha256


def test_executor_module_does_not_import_torch_or_ultralytics_at_import():
    code = (
        "import sys; import app.remote_execution.zoomspec_executor;"
        "assert 'torch' not in sys.modules, 'torch imported';"
        "assert 'ultralytics' not in sys.modules, 'ultralytics imported'"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = BACKEND_ROOT + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr