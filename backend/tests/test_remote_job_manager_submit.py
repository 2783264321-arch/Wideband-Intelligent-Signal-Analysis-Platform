"""RemoteGpuJobManager.submit preflight ordering and zero-I/O guarantees.

Task 12F-B Task 1: ``submit`` must call ``transport.validate_runner_environment(
"submit")`` BEFORE any SCP upload so a missing/invalid worker deployment causes
ZERO upload/SSH calls. The preflight lives INSIDE the existing transport-error
mapping boundary so a ``RemoteTransportError`` from preflight is mapped to
``PlatformError("REMOTE_SUBMIT_FAILED")`` and never reaches a coordinator as a
raw ``RemoteTransportError``.
"""
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
import subprocess

import pytest

from app.core.errors import PlatformError
from app.analysis.schema import ExecutorAvailabilityRead
from app.remote_execution.canonical import compute_request_sha256
from app.remote_execution.executor import SshRemoteExecutorProbe
from app.remote_execution.job_manager import RemoteGpuJobManager
from app.remote_execution.profile import RemoteProfile
from app.remote_execution.schema import (
    RemoteBatchStatusV1,
    RemoteExecutionBatchV1,
    RemoteExecutionItemV1,
    RemoteRecordingRefV1,
)
from app.remote_execution.transport import SshRunner

RUNTIME_COMMIT = "9a6f0feac0b0e6e2ac8ecd65d2e4383479e09f7c"

_GENERIC_NAMESPACE = "pipeline_x/1.0/" + "b" * 64
_GENERIC_ASSETS = {
    _GENERIC_NAMESPACE: {
        "detector_checkpoint": PurePosixPath("/root/assets/det.pt"),
        "frn_checkpoint": PurePosixPath("/root/assets/frn.pt"),
    }
}


def _ok(*, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr=stderr)


class ProcessRecorder:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = list(responses or [])

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), dict(kwargs)))
        if self.responses:
            return self.responses.pop(0)
        return _ok()


def _profile(tmp_path, *, dataset_roots=None, generic_asset_paths=None):
    key = tmp_path / "id_ed25519"
    key.write_bytes(b"key")
    hosts = tmp_path / "known_hosts"
    hosts.write_bytes(b"hosts")
    return RemoteProfile(
        name="autodl_primary",
        host="auto.example.com",
        port=22,
        user="root",
        ssh_key_path=key,
        known_hosts_path=hosts,
        remote_repo_root=PurePosixPath("/root/repo"),
        remote_job_root=PurePosixPath("/root/jobs"),
        remote_python_path=PurePosixPath("/opt/wsp-runtime/bin/python"),
        required_remote_runtime_commit=RUNTIME_COMMIT,
        dataset_roots=dataset_roots if dataset_roots is not None
        else {"SpaceNet": PurePosixPath("/root/autodl-tmp/SpaceNet_Dataset")},
        generic_asset_paths=(
            generic_asset_paths if generic_asset_paths is not None else dict(_GENERIC_ASSETS)
        ),
    )


class CallRecordingTransport(SshRunner):
    def __init__(self, profile, run_process):
        super().__init__(profile, run_process=run_process)
        self.order = []

    def validate_runner_environment(self, subcommand):
        self.order.append(("validate_runner_environment", subcommand))
        return super().validate_runner_environment(subcommand)

    def upload_file(self, local_path, remote_path):
        self.order.append(("upload_file", str(local_path)))
        return super().upload_file(local_path, remote_path)

    def run_runner(self, subcommand, args=()):
        self.order.append(("run_runner", subcommand, tuple(args)))
        return super().run_runner(subcommand, args)


def _batch(batch_id="batch_x", runtime_commit=RUNTIME_COMMIT):
    batch = RemoteExecutionBatchV1(
        schema_version=1,
        batch_id=batch_id,
        required_remote_runtime_commit=runtime_commit,
        pipeline={"id": "pipeline_x", "version": "1.0"},
        asset_manifest_sha256="c" * 64,
        items=[RemoteExecutionItemV1(
            item_key="000000",
            request_id="req_1",
            local_run_id="run_x",
            orchestrator_commit=RUNTIME_COMMIT,
            recording=RemoteRecordingRefV1(
                dataset_name="SpaceNet", dataset_split="test", dataset_key="0",
                label_space="spacenet_14",
                expected_recording_fingerprint="a" * 64,
                expected_source_data_sha256="b" * 64,
            ),
            parameters={},
        )],
        request_sha256="0" * 64,
    )
    batch.request_sha256 = compute_request_sha256(batch)
    return batch


def _request_file(tmp_path, batch):
    request_file = tmp_path / "request.json"
    request_file.write_bytes(batch.model_dump_json().encode("utf-8"))
    return request_file


def test_submit_missing_spacenet_mapping_zero_io(tmp_path):
    recorder = ProcessRecorder()
    manager = RemoteGpuJobManager(
        _profile(tmp_path, dataset_roots={}), SshRunner(_profile(tmp_path, dataset_roots={}), run_process=recorder)
    )
    batch = _batch()
    with pytest.raises(PlatformError) as exc:
        manager.submit(batch, _request_file(tmp_path, batch))
    assert exc.value.code == "REMOTE_SUBMIT_FAILED"
    assert recorder.calls == []


def test_submit_missing_spacenet_root_zero_io(tmp_path):
    """D3B: the four legacy flat assets are optional; the SpaceNet dataset root
    mapping is the remaining full-worker preflight requirement."""
    recorder = ProcessRecorder()
    profile = _profile(tmp_path, dataset_roots={})
    manager = RemoteGpuJobManager(profile, SshRunner(profile, run_process=recorder))
    batch = _batch()
    with pytest.raises(PlatformError) as exc:
        manager.submit(batch, _request_file(tmp_path, batch))
    assert exc.value.code == "REMOTE_SUBMIT_FAILED"
    assert recorder.calls == []


def test_submit_preflight_transport_error_maps_to_platform_error(tmp_path):
    """Preflight raising RemoteTransportError must be mapped to a PlatformError
    inside submit so the Coordinator never receives a raw RemoteTransportError."""
    profile = _profile(tmp_path, dataset_roots={})
    transport = SshRunner(profile, run_process=ProcessRecorder())
    manager = RemoteGpuJobManager(profile, transport)
    batch = _batch()
    with pytest.raises(PlatformError) as exc:
        manager.submit(batch, _request_file(tmp_path, batch))
    assert exc.value.code == "REMOTE_SUBMIT_FAILED"
    assert exc.value.message == "Remote submit transport failed."
    assert not isinstance(exc.value, Exception.__class__)


def test_submit_frozen_runtime_commit_mismatch_zero_io(tmp_path):
    """Frozen request runtime commit != configured remote runtime commit ->
    REMOTE_SUBMIT_FAILED with ZERO upload/SSH and NO runner_code. The
    coordinator treats this as an uncertain submit and reconciles the SAME
    batch instead of spawning the frozen request under a new runtime."""
    recorder = ProcessRecorder()
    profile = _profile(tmp_path)  # required_remote_runtime_commit == RUNTIME_COMMIT
    manager = RemoteGpuJobManager(profile, SshRunner(profile, run_process=recorder))
    batch = _batch(runtime_commit="a" * 40)
    with pytest.raises(PlatformError) as exc:
        manager.submit(batch, _request_file(tmp_path, batch))
    assert exc.value.code == "REMOTE_SUBMIT_FAILED"
    assert recorder.calls == []
    assert "runner_code" not in exc.value.details
    assert "a" * 40 not in exc.value.message
    assert RUNTIME_COMMIT not in exc.value.message


def test_submit_valid_ordering_preflight_then_upload_then_runner(tmp_path):
    recorder = ProcessRecorder()
    profile = _profile(tmp_path)
    transport = CallRecordingTransport(profile, recorder)
    manager = RemoteGpuJobManager(profile, transport)
    batch = _batch()
    manager.submit(batch, _request_file(tmp_path, batch))
    actions = [call[0] for call in transport.order]
    preflight_idx = actions.index("validate_runner_environment")
    upload_idx = actions.index("upload_file")
    runner_idx = actions.index("run_runner")
    assert preflight_idx == 0
    assert preflight_idx < upload_idx < runner_idx
    assert transport.order[preflight_idx][1] == "submit"
    assert transport.order[runner_idx][1] == "submit"
    assert len(recorder.calls) == 2


def test_status_still_works_with_missing_scientific_mappings(tmp_path):
    recorder = ProcessRecorder(responses=[
        _ok(stdout=json.dumps({
            "batch_id": "batch_x",
            "status": "running",
            "items": [{"item_key": "000000", "status": "running"}],
        })),
    ])
    profile = _profile(tmp_path, dataset_roots={})
    manager = RemoteGpuJobManager(profile, SshRunner(profile, run_process=recorder))
    status = manager.status("batch_x")
    assert isinstance(status, RemoteBatchStatusV1)
    assert status.batch_id == "batch_x"
    assert len(recorder.calls) == 1
    argv = recorder.calls[0][0]
    assert "status" in argv


def test_probe_missing_mapping_returns_transport_unavailable(tmp_path):
    from app.pipelines.base import PipelineDefinition
    from types import SimpleNamespace

    profile = _profile(tmp_path, dataset_roots={})
    recorder = ProcessRecorder()
    transport = SshRunner(profile, run_process=recorder)
    probe = SshRemoteExecutorProbe(
        profile, transport,
        expected_runtime_commit=RUNTIME_COMMIT,
    )
    pipeline = PipelineDefinition(
        id="zoomspec_yolo26n_aug_combined_frn_v3",
        name="z", version="1.0.0", label_space="spacenet_14",
        recommended_device="GPU", cpu_supported=False,
        stages=(), inspectable_stages=(),
        task_capability="detection_classification",
        executors_supported=("remote_gpu",), recommended_executor="remote_gpu",
    )
    recording = SimpleNamespace(id="rec", label_space="spacenet_14", source_data_sha256="d" * 64)
    release = SimpleNamespace(
        release=SimpleNamespace(model_release_id="golden"),
        manifest=SimpleNamespace(asset_manifest_sha256="c" * 64),
    )
    availability = probe.availability(recording, pipeline, "d" * 64, release)
    assert isinstance(availability, ExecutorAvailabilityRead)
    assert availability.available is False
    assert availability.reason_code == "REMOTE_TRANSPORT_UNAVAILABLE"
    assert recorder.calls == []