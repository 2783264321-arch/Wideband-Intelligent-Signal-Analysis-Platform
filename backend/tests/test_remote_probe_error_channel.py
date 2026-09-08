import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.analysis.schema import ExecutorAvailabilityRead
from app.pipelines.base import PipelineDefinition
from app.remote_execution.executor import SshRemoteExecutorProbe
from app.remote_execution.profile import RemoteProfile
from app.remote_execution.transport import RemoteRunnerExit, RemoteTransportError, SshRunner

RUNTIME_COMMIT = "a" * 40
MANIFEST_SHA = "b" * 64

PIPELINE = PipelineDefinition(
    id="zoomspec_yolo26n_aug_combined_frn_v3",
    name="z",
    version="1.0.0",
    label_space="spacenet_14",
    recommended_device="GPU",
    cpu_supported=False,
    stages=(),
    inspectable_stages=(),
    task_capability="detection_classification",
    executors_supported=("remote_gpu",),
    recommended_executor="remote_gpu",
)


def _recording():
    return SimpleNamespace(id="rec", label_space="spacenet_14", source_data_sha256="c" * 64)


def _profile() -> RemoteProfile:
    return RemoteProfile(
        name="autodl_primary",
        host="host",
        port=22,
        user="u",
        ssh_key_path=Path("/tmp/key"),
        known_hosts_path=Path("/tmp/known_hosts"),
        remote_repo_root=Path("/repo"),
        remote_job_root=Path("/jobs"),
        remote_python_path=Path("/py"),
        required_remote_runtime_commit="a" * 40,
        dataset_roots={},
        asset_paths={},
    )


def _probe_json() -> bytes:
    return json.dumps({
        "schema_version": 1,
        "status": "available",
        "remote_runtime_commit": RUNTIME_COMMIT,
        "asset_manifest_sha256": MANIFEST_SHA,
        "device": 0,
    }).encode("utf-8")


def _make_probe(run_process):
    transport = SshRunner(_profile(), run_process=run_process)
    return SshRemoteExecutorProbe(_profile(), transport,
                                  expected_runtime_commit=RUNTIME_COMMIT,
                                  expected_manifest_sha256=MANIFEST_SHA)


def _result(returncode, stdout=b"", stderr=b""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_probe_success_maps_available_true():
    probe = _make_probe(lambda *a, **k: _result(0, stdout=_probe_json()))
    availability = probe.availability(_recording(), PIPELINE, "c" * 64)
    assert isinstance(availability, ExecutorAvailabilityRead)
    assert availability.executor == "remote_gpu"
    assert availability.available is True
    assert availability.reason_code is None
    assert availability.reason_message is None


def test_probe_runtime_commit_mismatch_is_unavailable():
    wrong = json.dumps({"schema_version": 1, "status": "available",
                        "remote_runtime_commit": "d" * 40,
                        "asset_manifest_sha256": MANIFEST_SHA, "device": 0}).encode()
    probe = _make_probe(lambda *a, **k: _result(0, stdout=wrong))
    availability = probe.availability(_recording(), PIPELINE, "c" * 64)
    assert availability.available is False
    assert availability.reason_code == "REMOTE_IMPLEMENTATION_MISMATCH"


def test_probe_manifest_mismatch_is_unavailable():
    wrong = json.dumps({"schema_version": 1, "status": "available",
                        "remote_runtime_commit": RUNTIME_COMMIT,
                        "asset_manifest_sha256": "e" * 64, "device": 0}).encode()
    probe = _make_probe(lambda *a, **k: _result(0, stdout=wrong))
    availability = probe.availability(_recording(), PIPELINE, "c" * 64)
    assert availability.available is False
    assert availability.reason_code == "PIPELINE_ASSET_MISMATCH"


def test_probe_structured_runner_failure_maps_explicit_unavailable():
    probe = _make_probe(lambda *a, **k: _result(1, stdout=b"", stderr=b"REMOTE_IMPLEMENTATION_MISMATCH: runtime mismatch\n"))
    availability = probe.availability(_recording(), PIPELINE, "c" * 64)
    assert availability.available is False
    assert availability.reason_code == "REMOTE_IMPLEMENTATION_MISMATCH"


def test_runner_error_requires_single_uppercase_code_line():
    # Two stderr lines -> generic RemoteTransportError (not RemoteRunnerExit)
    call = {"n": 0}

    def run_process(argv, **kwargs):
        call["n"] += 1
        return _result(1, stdout=b"", stderr=b"CODE: msg\nEXTRA: more\n")

    transport = SshRunner(_profile(), run_process=run_process)
    with pytest.raises(RemoteTransportError):
        transport.run_runner("status", ("--batch-id", "abc"))


def test_generic_ssh_failure_stays_remote_transport_error():
    def run_process(argv, **kwargs):
        return _result(255, stdout=b"", stderr=b"ssh: Connection refused\n")

    transport = SshRunner(_profile(), run_process=run_process)
    with pytest.raises(RemoteTransportError):
        transport.run_runner("status", ("--batch-id", "abc"))


def test_probe_does_not_leak_stderr_traceback():
    probe = _make_probe(lambda *a, **k: _result(1, stdout=b"", stderr=b"Traceback (most recent call last):\n  File \"/x.py\", line 1\n"))
    availability = probe.availability(_recording(), PIPELINE, "c" * 64)
    assert availability.available is False
    assert "Traceback" not in (availability.reason_message or "")
    assert ".py" not in (availability.reason_message or "")


def test_probe_malformed_stdout_maps_unavailable():
    probe = _make_probe(lambda *a, **k: _result(0, stdout=b"not json\n"))
    availability = probe.availability(_recording(), PIPELINE, "c" * 64)
    assert availability.available is False
    assert availability.reason_code == "REMOTE_PROBE_UNAVAILABLE"


def test_runner_error_line_creates_nonzero_runner_exit():
    def run_process(argv, **kwargs):
        return _result(1, stdout=b"", stderr=b"REMOTE_PROBE_UNAVAILABLE: something happened\n")

    transport = SshRunner(_profile(), run_process=run_process)
    with pytest.raises(RemoteRunnerExit) as exc:
        transport.run_runner("probe", ())
    assert exc.value.returncode == 1
    assert exc.value.code == "REMOTE_PROBE_UNAVAILABLE"