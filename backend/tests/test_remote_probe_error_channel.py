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
RELEASE_ID = "golden"

PIPELINE = PipelineDefinition(
    id="generic_remote_plugin",
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


def _release(manifest_sha: str = MANIFEST_SHA, release_id: str = RELEASE_ID):
    return SimpleNamespace(
        release=SimpleNamespace(model_release_id=release_id),
        manifest=SimpleNamespace(asset_manifest_sha256=manifest_sha),
    )


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
        dataset_roots={"SpaceNet": Path("/sn")},
        asset_paths={
            "detector_checkpoint": Path("/m/det.pt"),
            "frn_checkpoint": Path("/m/frn.pt"),
            "frozen_config": Path("/m/frozen_config.json"),
            "ls_stft_normalization": Path("/m/norm.json"),
        },
    )


def _probe_json(manifest_sha: str = MANIFEST_SHA, runtime_commit: str = RUNTIME_COMMIT) -> bytes:
    return json.dumps({
        "schema_version": 1,
        "status": "available",
        "remote_runtime_commit": runtime_commit,
        "asset_manifest_sha256": manifest_sha,
        "device": 0,
    }).encode("utf-8")


def _make_probe(run_process):
    transport = SshRunner(_profile(), run_process=run_process)
    return SshRemoteExecutorProbe(
        _profile(), transport, expected_runtime_commit=RUNTIME_COMMIT
    )


def _result(returncode, stdout=b"", stderr=b""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_probe_success_maps_available_true():
    probe = _make_probe(lambda *a, **k: _result(0, stdout=_probe_json()))
    availability = probe.availability(_recording(), PIPELINE, "c" * 64, _release())
    assert isinstance(availability, ExecutorAvailabilityRead)
    assert availability.executor == "remote_gpu"
    assert availability.available is True
    assert availability.reason_code is None
    assert availability.reason_message is None


def test_probe_requests_exact_plugin_release_manifest_tokens():
    captured = {}

    def run_process(argv, **kwargs):
        captured["argv"] = list(argv)
        return _result(0, stdout=_probe_json())

    probe = _make_probe(run_process)
    probe.availability(_recording(), PIPELINE, "c" * 64, _release())
    argv = captured["argv"]
    joined = " ".join(argv)
    assert "--plugin-id generic_remote_plugin" in joined
    assert "--plugin-version 1.0.0" in joined
    assert f"--model-release-id {RELEASE_ID}" in joined
    assert f"--asset-manifest-sha256 {MANIFEST_SHA}" in joined


def test_probe_release_less_remote_is_unavailable():
    probe = _make_probe(lambda *a, **k: _result(0, stdout=_probe_json()))
    availability = probe.availability(_recording(), PIPELINE, "c" * 64, None)
    assert availability.available is False
    assert availability.reason_code == "MODEL_RELEASE_MISMATCH"


def test_probe_runtime_commit_mismatch_is_unavailable():
    wrong = _probe_json(runtime_commit="d" * 40)
    probe = _make_probe(lambda *a, **k: _result(0, stdout=wrong))
    availability = probe.availability(_recording(), PIPELINE, "c" * 64, _release())
    assert availability.available is False
    assert availability.reason_code == "REMOTE_IMPLEMENTATION_MISMATCH"


def test_probe_manifest_mismatch_is_unavailable():
    wrong = _probe_json(manifest_sha="e" * 64)
    probe = _make_probe(lambda *a, **k: _result(0, stdout=wrong))
    availability = probe.availability(_recording(), PIPELINE, "c" * 64, _release())
    assert availability.available is False
    assert availability.reason_code == "PIPELINE_ASSET_MISMATCH"


def test_probe_structured_runner_failure_maps_explicit_unavailable():
    probe = _make_probe(lambda *a, **k: _result(1, stdout=b"", stderr=b"REMOTE_IMPLEMENTATION_MISMATCH: runtime mismatch\n"))
    availability = probe.availability(_recording(), PIPELINE, "c" * 64, _release())
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
    availability = probe.availability(_recording(), PIPELINE, "c" * 64, _release())
    assert availability.available is False
    assert "Traceback" not in (availability.reason_message or "")
    assert ".py" not in (availability.reason_message or "")


def test_probe_malformed_stdout_maps_unavailable():
    probe = _make_probe(lambda *a, **k: _result(0, stdout=b"not json\n"))
    availability = probe.availability(_recording(), PIPELINE, "c" * 64, _release())
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
