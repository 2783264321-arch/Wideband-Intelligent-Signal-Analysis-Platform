import json
from pathlib import Path, PurePosixPath
import subprocess

import pytest

from app.core.errors import PlatformError
from app.remote_execution.canonical import compute_request_sha256
from app.remote_execution.job_manager import RemoteGpuJobManager
from app.remote_execution.profile import RemoteProfile
from app.remote_execution.schema import (
    RemoteBatchStatusV1,
    RemoteExecutionBatchV1,
    RemoteExecutionItemV1,
    RemoteRecordingRefV1,
)
from app.remote_execution.transport import RemoteTransportError, SshRunner


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


class MaterializingRecorder:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), dict(kwargs)))
        target = argv[-1]
        # scp downloads end in a local destination path; scp uploads end in a
        # remote "user@host:path" target that must never be touched locally.
        if "@" not in target:
            Path(target).write_bytes(b"data")
        return _ok()


class EnvelopeOnlyRecorder:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), dict(kwargs)))
        local_dst = Path(argv[-1])
        if local_dst.name == "envelope.json":
            local_dst.write_bytes(b"data")
        return _ok()


_FULL_ASSETS = {
    "detector_checkpoint": PurePosixPath("/root/models/best.pt"),
    "frn_checkpoint": PurePosixPath("/root/models/frn.pt"),
    "frozen_config": PurePosixPath("/root/models/frozen_config.json"),
    "ls_stft_normalization": PurePosixPath("/root/models/ls_stft_normalization.json"),
}


def _profile(tmp_path):
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
        required_remote_runtime_commit="a" * 40,
        dataset_roots={"SpaceNet": PurePosixPath("/root/autodl-tmp/SpaceNet_Dataset")},
        asset_paths=dict(_FULL_ASSETS),
    )


def _profile_env(tmp_path, monkeypatch, **overrides):
    key = tmp_path / "id_ed25519"
    key.write_bytes(b"key")
    hosts = tmp_path / "known_hosts"
    hosts.write_bytes(b"hosts")
    env = {
        "WSP_REMOTE_PROFILE_NAME": "autodl_primary",
        "WSP_REMOTE_HOST": "auto.example.com",
        "WSP_REMOTE_PORT": "22",
        "WSP_REMOTE_USER": "root",
        "WSP_REMOTE_SSH_KEY_PATH": str(key),
        "WSP_REMOTE_KNOWN_HOSTS_PATH": str(hosts),
        "WSP_REMOTE_REPO_ROOT": "/root/repo",
        "WSP_REMOTE_JOB_ROOT": "/root/jobs",
        "WSP_REMOTE_PYTHON_PATH": "/opt/wsp-runtime/bin/python",
        "WSP_REMOTE_REQUIRED_RUNTIME_COMMIT": "a" * 40,
        "WSP_REMOTE_DATASET_ROOTS_JSON": json.dumps({"SpaceNet": "/root/autodl-tmp/SpaceNet_Dataset"}),
        "WSP_REMOTE_ASSET_PATHS_JSON": json.dumps({k: v.as_posix() for k, v in _FULL_ASSETS.items()}),
    }
    env.update(overrides)
    for name, value in env.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)


def _load_profile(tmp_path, monkeypatch, settings, **overrides):
    _profile_env(tmp_path, monkeypatch, **overrides)
    return RemoteProfile.from_env(settings)


def _batch(batch_id="batch_x"):
    batch = RemoteExecutionBatchV1(
        schema_version=1,
        batch_id=batch_id,
        required_remote_runtime_commit="a" * 40,
        pipeline={"id": "pipeline_x", "version": "1.0"},
        asset_manifest_sha256="c" * 64,
        items=[RemoteExecutionItemV1(
            item_key="000000",
            request_id="req_1",
            local_run_id="run_x",
            orchestrator_commit="9a6f0feac0b0e6e2ac8ecd65d2e4383479e09f7c",
            recording=RemoteRecordingRefV1(
                dataset_name="SpaceNet", dataset_split="test", dataset_key="0", label_space="spacenet_14",
                expected_recording_fingerprint="a" * 64, expected_source_data_sha256="b" * 64,
            ),
            parameters={},
        )],
        request_sha256="a" * 64,
    )
    batch.request_sha256 = compute_request_sha256(batch)
    return batch


def _status_json():
    return json.dumps({
        "batch_id": "batch_x",
        "status": "running",
        "items": [{"item_key": "000000", "status": "running"}],
    })


# ------------------------------------------------------------------ PROFILE


def test_profile_loads_from_complete_env(tmp_path, monkeypatch, settings):
    profile = _load_profile(tmp_path, monkeypatch, settings)
    assert profile.name == "autodl_primary"
    assert profile.host == "auto.example.com"
    assert profile.port == 22
    assert profile.user == "root"
    assert profile.ssh_key_path == tmp_path / "id_ed25519"
    assert profile.known_hosts_path == tmp_path / "known_hosts"
    assert profile.remote_repo_root == PurePosixPath("/root/repo")
    assert profile.remote_job_root == PurePosixPath("/root/jobs")
    assert profile.dataset_roots == {"SpaceNet": PurePosixPath("/root/autodl-tmp/SpaceNet_Dataset")}
    assert profile.asset_paths == _FULL_ASSETS


def test_profile_missing_host_fails(tmp_path, monkeypatch, settings):
    with pytest.raises(PlatformError) as exc:
        _load_profile(tmp_path, monkeypatch, settings, WSP_REMOTE_HOST=None)
    assert exc.value.code == "REMOTE_EXECUTOR_UNAVAILABLE"


def test_profile_missing_ssh_key_fails(tmp_path, monkeypatch, settings):
    with pytest.raises(PlatformError) as exc:
        _load_profile(tmp_path, monkeypatch, settings,
                      WSP_REMOTE_SSH_KEY_PATH=str(tmp_path / "missing_key"))
    assert exc.value.code == "REMOTE_EXECUTOR_UNAVAILABLE"


def test_profile_missing_known_hosts_fails(tmp_path, monkeypatch, settings):
    with pytest.raises(PlatformError) as exc:
        _load_profile(tmp_path, monkeypatch, settings,
                      WSP_REMOTE_KNOWN_HOSTS_PATH=str(tmp_path / "missing_hosts"))
    assert exc.value.code == "REMOTE_EXECUTOR_UNAVAILABLE"


@pytest.mark.parametrize("port", ["0", "70000", "abc"])
def test_profile_invalid_port_fails(tmp_path, monkeypatch, settings, port):
    with pytest.raises(PlatformError) as exc:
        _load_profile(tmp_path, monkeypatch, settings, WSP_REMOTE_PORT=port)
    assert exc.value.code == "REMOTE_EXECUTOR_UNAVAILABLE"


@pytest.mark.parametrize("root", ["relative/path", "/root/../escape"])
def test_profile_invalid_repo_root_fails(tmp_path, monkeypatch, settings, root):
    with pytest.raises(PlatformError) as exc:
        _load_profile(tmp_path, monkeypatch, settings, WSP_REMOTE_REPO_ROOT=root)
    assert exc.value.code == "REMOTE_EXECUTOR_UNAVAILABLE"


def test_profile_posix_root_rejects_nul_and_control_chars():
    from app.remote_execution import profile as profile_module

    for bad in ("/root/\x00escape", "/root/\nescape", "/root/\x07escape"):
        with pytest.raises(PlatformError) as exc:
            profile_module._safe_posix_root(bad, "test_root")
        assert exc.value.code == "REMOTE_EXECUTOR_UNAVAILABLE"


def test_profile_missing_remote_python_path_fails(tmp_path, monkeypatch, settings):
    with pytest.raises(PlatformError) as exc:
        _load_profile(tmp_path, monkeypatch, settings, WSP_REMOTE_PYTHON_PATH=None)
    assert exc.value.code == "REMOTE_EXECUTOR_UNAVAILABLE"


@pytest.mark.parametrize("python_path", [
    "python3",                       # bare name, not absolute
    "relative/python",               # relative path
    "/root/runtime dir/python",      # space in path
    "/root/../python",               # .. traversal
    "/root//python",                 # duplicate slash
    "/root/python;id",               # shell metacharacter
    "/root/\\bin\\python",           # backslash
])
def test_profile_invalid_remote_python_path_fails(tmp_path, monkeypatch, settings, python_path):
    with pytest.raises(PlatformError) as exc:
        _load_profile(tmp_path, monkeypatch, settings, WSP_REMOTE_PYTHON_PATH=python_path)
    assert exc.value.code == "REMOTE_EXECUTOR_UNAVAILABLE"


def test_profile_valid_remote_python_path_stored_as_pure_posix(tmp_path, monkeypatch, settings):
    profile = _load_profile(tmp_path, monkeypatch, settings)
    assert profile.remote_python_path == PurePosixPath("/opt/wsp-runtime/bin/python")
    assert isinstance(profile.remote_python_path, PurePosixPath)


@pytest.mark.parametrize("value", ["not-json", "{bad", "[]"])
def test_profile_malformed_mapping_json_fails(tmp_path, monkeypatch, settings, value):
    with pytest.raises(PlatformError) as exc:
        _load_profile(tmp_path, monkeypatch, settings, WSP_REMOTE_DATASET_ROOTS_JSON=value)
    assert exc.value.code == "REMOTE_EXECUTOR_UNAVAILABLE"


def test_profile_mapping_non_string_value_fails(tmp_path, monkeypatch, settings):
    with pytest.raises(PlatformError) as exc:
        _load_profile(tmp_path, monkeypatch, settings,
                      WSP_REMOTE_ASSET_PATHS_JSON=json.dumps({"checkpoint": 123}))
    assert exc.value.code == "REMOTE_EXECUTOR_UNAVAILABLE"


# ------------------------------------------------------------------- SSH


def test_ssh_runner_status_argv(tmp_path):
    profile = _profile(tmp_path)
    recorder = ProcessRecorder()
    runner = SshRunner(profile, run_process=recorder)
    runner.run_runner("status")
    argv, kwargs = recorder.calls[0]
    assert "ssh" in argv
    assert "StrictHostKeyChecking=yes" in argv
    assert f"UserKnownHostsFile={profile.known_hosts_path}" in argv
    assert "BatchMode=yes" in argv
    assert "-i" in argv and str(profile.ssh_key_path) in argv
    assert "-p" in argv and "22" in argv
    assert "root@auto.example.com" in argv
    assert profile.remote_python_path.as_posix() in argv
    assert "-m" in argv
    assert "app.remote_execution.runner" in argv
    assert "status" in argv
    assert kwargs["shell"] is False


def test_ssh_runner_argv_sets_deterministic_module_root(tmp_path):
    """The runner must resolve the deployed checkout deterministically via
    ``env PYTHONPATH=<remote_repo_root>/backend``, not an implicit install."""
    profile = _profile(tmp_path)
    recorder = ProcessRecorder()
    runner = SshRunner(profile, run_process=recorder)
    runner.run_runner("status")
    argv, kwargs = recorder.calls[0]
    # The remote command prefix must establish the backend module root.
    assert "env" in argv
    assert "PYTHONPATH=/root/repo/backend" in argv
    assert "WSP_REMOTE_JOB_ROOT=/root/jobs" in argv
    assert profile.remote_python_path.as_posix() in argv
    assert "-m" in argv
    assert "app.remote_execution.runner" in argv
    assert "status" in argv
    # All existing security properties must remain.
    assert kwargs["shell"] is False
    assert "StrictHostKeyChecking=yes" in argv
    assert f"UserKnownHostsFile={profile.known_hosts_path}" in argv
    assert "BatchMode=yes" in argv


def test_ssh_runner_argv_env_prefix_uses_remote_repo_root(tmp_path):
    key = tmp_path / "id_ed25519"
    key.write_bytes(b"key")
    hosts = tmp_path / "known_hosts"
    hosts.write_bytes(b"hosts")
    profile = RemoteProfile(
        name="autodl_primary",
        host="auto.example.com",
        port=22,
        user="root",
        ssh_key_path=key,
        known_hosts_path=hosts,
        remote_repo_root=PurePosixPath("/opt/platform"),
        remote_job_root=PurePosixPath("/root/jobs"),
        remote_python_path=PurePosixPath("/opt/wsp-runtime/bin/python"),
        required_remote_runtime_commit="a" * 40,
        dataset_roots={"SpaceNet": PurePosixPath("/root/autodl-tmp/SpaceNet_Dataset")},
        asset_paths=dict(_FULL_ASSETS),
    )
    recorder = ProcessRecorder()
    runner = SshRunner(profile, run_process=recorder)
    runner.run_runner("probe")
    argv, _ = recorder.calls[0]
    assert "PYTHONPATH=/opt/platform/backend" in argv
    assert "PYTHONPATH=/root/repo/backend" not in argv


def test_ssh_runner_argv_env_before_python3(tmp_path):
    profile = _profile(tmp_path)
    recorder = ProcessRecorder()
    runner = SshRunner(profile, run_process=recorder)
    runner.run_runner("submit", ("--request-path", "/root/jobs/incoming/batch_x.request.json"))
    argv, _ = recorder.calls[0]
    env_idx = argv.index("env")
    assert argv[env_idx + 1] == "PYTHONPATH=/root/repo/backend"
    assert argv[env_idx + 2] == "WSP_REMOTE_JOB_ROOT=/root/jobs"
    env_segment = argv[env_idx + 1:]
    python_idx = env_segment.index(profile.remote_python_path.as_posix())
    assert python_idx > 2
    assert env_segment[python_idx + 1] == "-m"
    assert env_segment[python_idx + 2] == "app.remote_execution.runner"
    assert env_segment[python_idx + 3] == "submit"


def test_ssh_runner_argv_exact_ordered_segment(tmp_path):
    profile = _profile(tmp_path)
    recorder = ProcessRecorder()
    runner = SshRunner(profile, run_process=recorder)
    runner.run_runner("status", ("--batch-id", "batch_x"))
    argv, kwargs = recorder.calls[0]
    env_idx = argv.index("env")
    segment = argv[env_idx:]
    assert segment == [
        "env",
        "PYTHONPATH=/root/repo/backend",
        "WSP_REMOTE_JOB_ROOT=/root/jobs",
        "/opt/wsp-runtime/bin/python",
        "-m",
        "app.remote_execution.runner",
        "status",
        "--batch-id",
        "batch_x",
    ]
    assert kwargs["shell"] is False


def test_ssh_runner_argv_has_no_bare_python3(tmp_path):
    profile = _profile(tmp_path)
    recorder = ProcessRecorder()
    runner = SshRunner(profile, run_process=recorder)
    runner.run_runner("status", ("--batch-id", "batch_x"))
    argv, _ = recorder.calls[0]
    env_idx = argv.index("env")
    segment = argv[env_idx:]
    assert "python3" not in segment


def test_ssh_runner_argv_uses_custom_remote_python(tmp_path):
    key = tmp_path / "id_ed25519"
    key.write_bytes(b"key")
    hosts = tmp_path / "known_hosts"
    hosts.write_bytes(b"hosts")
    profile = RemoteProfile(
        name="autodl_primary",
        host="auto.example.com",
        port=22,
        user="root",
        ssh_key_path=key,
        known_hosts_path=hosts,
        remote_repo_root=PurePosixPath("/root/repo"),
        remote_job_root=PurePosixPath("/root/jobs"),
        remote_python_path=PurePosixPath("/srv/runtime/python"),
        required_remote_runtime_commit="a" * 40,
        dataset_roots={"SpaceNet": PurePosixPath("/root/autodl-tmp/SpaceNet_Dataset")},
        asset_paths=dict(_FULL_ASSETS),
    )
    recorder = ProcessRecorder()
    runner = SshRunner(profile, run_process=recorder)
    runner.run_runner("probe")
    argv, _ = recorder.calls[0]
    assert "/srv/runtime/python" in argv
    assert "/opt/wsp-runtime/bin/python" not in argv


def test_ssh_runner_argv_uses_custom_job_root(tmp_path):
    key = tmp_path / "id_ed25519"
    key.write_bytes(b"key")
    hosts = tmp_path / "known_hosts"
    hosts.write_bytes(b"hosts")
    profile = RemoteProfile(
        name="autodl_primary",
        host="auto.example.com",
        port=22,
        user="root",
        ssh_key_path=key,
        known_hosts_path=hosts,
        remote_repo_root=PurePosixPath("/root/repo"),
        remote_job_root=PurePosixPath("/srv/jobs"),
        remote_python_path=PurePosixPath("/opt/wsp-runtime/bin/python"),
        required_remote_runtime_commit="a" * 40,
        dataset_roots={"SpaceNet": PurePosixPath("/root/autodl-tmp/SpaceNet_Dataset")},
        asset_paths=dict(_FULL_ASSETS),
    )
    recorder = ProcessRecorder()
    runner = SshRunner(profile, run_process=recorder)
    runner.run_runner("status", ("--batch-id", "batch_x"))
    argv, kwargs = recorder.calls[0]
    assert "WSP_REMOTE_JOB_ROOT=/srv/jobs" in argv
    assert "WSP_REMOTE_JOB_ROOT=/root/jobs" not in argv
    assert kwargs["shell"] is False


def test_ssh_never_uses_insecure_host_key_policy(tmp_path):
    profile = _profile(tmp_path)
    recorder = ProcessRecorder()
    runner = SshRunner(profile, run_process=recorder)
    for subcommand in ("probe", "submit", "status", "work"):
        runner.run_runner(subcommand)
    all_tokens = [token for argv, _ in recorder.calls for token in argv]
    assert "StrictHostKeyChecking=no" not in all_tokens


@pytest.mark.parametrize("subcommand", ["bash", "sh", "git", "ls", "rm", "reboot"])
def test_unknown_runner_subcommand_rejected_before_subprocess(tmp_path, subcommand):
    profile = _profile(tmp_path)
    recorder = ProcessRecorder()
    runner = SshRunner(profile, run_process=recorder)
    with pytest.raises(PlatformError):
        runner.run_runner(subcommand)
    assert recorder.calls == []


def test_unsafe_identifier_argument_rejected_before_subprocess(tmp_path):
    profile = _profile(tmp_path)
    recorder = ProcessRecorder()
    runner = SshRunner(profile, run_process=recorder)
    with pytest.raises(PlatformError):
        runner.run_runner("status", ("--batch-id", "../escape"))
    assert recorder.calls == []


def test_ssh_nonzero_subprocess_fails(tmp_path):
    profile = _profile(tmp_path)
    recorder = ProcessRecorder(responses=[
        subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="err"),
    ])
    runner = SshRunner(profile, run_process=recorder)
    with pytest.raises(RuntimeError):
        runner.run_runner("status")


# ------------------------------------------------------------------- SCP


def test_upload_uses_secure_scp_argv(tmp_path):
    profile = _profile(tmp_path)
    recorder = ProcessRecorder()
    runner = SshRunner(profile, run_process=recorder)
    local = tmp_path / "request.json"
    local.write_bytes(b"{}")
    runner.upload_file(local, PurePosixPath("/root/jobs/incoming/batch_x.request.json"))
    argv, kwargs = recorder.calls[0]
    assert "scp" in argv
    assert "-P" in argv and "22" in argv
    assert "-i" in argv and str(profile.ssh_key_path) in argv
    assert "StrictHostKeyChecking=yes" in argv
    assert f"UserKnownHostsFile={profile.known_hosts_path}" in argv
    assert "BatchMode=yes" in argv
    assert kwargs["shell"] is False
    assert "-r" not in argv
    assert "root@auto.example.com:/root/jobs/incoming/batch_x.request.json" in argv


def test_download_uses_secure_scp_argv(tmp_path):
    profile = _profile(tmp_path)
    recorder = MaterializingRecorder()
    runner = SshRunner(profile, run_process=recorder)
    local = tmp_path / "out" / "envelope.json"
    runner.download_file(PurePosixPath("/root/jobs/batch_x/results/000000/envelope.json"), local)
    argv, kwargs = recorder.calls[0]
    assert "scp" in argv
    assert "StrictHostKeyChecking=yes" in argv
    assert f"UserKnownHostsFile={profile.known_hosts_path}" in argv
    assert kwargs["shell"] is False
    assert local.is_file()


@pytest.mark.parametrize("bad", ["../x", "/root/jobs/../escape", "relative/path"])
def test_unsafe_remote_path_rejected_before_subprocess(tmp_path, bad):
    profile = _profile(tmp_path)
    recorder = ProcessRecorder()
    runner = SshRunner(profile, run_process=recorder)
    local = tmp_path / "request.json"
    local.write_bytes(b"{}")
    with pytest.raises(PlatformError):
        runner.upload_file(local, PurePosixPath(bad))
    with pytest.raises(PlatformError):
        runner.download_file(PurePosixPath(bad), tmp_path / "out" / "x")
    assert recorder.calls == []


def test_upload_local_source_missing_fails_before_subprocess(tmp_path):
    profile = _profile(tmp_path)
    recorder = ProcessRecorder()
    runner = SshRunner(profile, run_process=recorder)
    with pytest.raises(PlatformError):
        runner.upload_file(tmp_path / "missing.json", PurePosixPath("/root/jobs/incoming/batch_x.request.json"))
    assert recorder.calls == []


# ---------------------------------------------------------------- MANAGER


def test_submit_uploads_once_and_runs_fixed_runner(tmp_path):
    profile = _profile(tmp_path)
    recorder = ProcessRecorder()
    manager = RemoteGpuJobManager(profile, SshRunner(profile, run_process=recorder))
    batch = _batch()
    request_file = tmp_path / "request.json"
    request_file.write_bytes(batch.model_dump_json().encode("utf-8"))
    manager.submit(batch, request_file)
    assert len(recorder.calls) == 2
    upload_argv = recorder.calls[0][0]
    assert "scp" in upload_argv
    assert "root@auto.example.com:/root/jobs/incoming/batch_x.request.json" in upload_argv
    runner_argv = recorder.calls[1][0]
    assert "app.remote_execution.runner" in runner_argv
    assert "submit" in runner_argv
    assert "--request-path" in runner_argv
    assert "/root/jobs/incoming/batch_x.request.json" in runner_argv
    assert "git" not in runner_argv


def test_submit_mismatched_request_rejected_with_zero_transport(tmp_path):
    profile = _profile(tmp_path)
    recorder = ProcessRecorder()
    manager = RemoteGpuJobManager(profile, SshRunner(profile, run_process=recorder))
    batch = _batch()
    other = _batch(batch_id="batch_y")
    request_file = tmp_path / "request.json"
    request_file.write_bytes(other.model_dump_json().encode("utf-8"))
    with pytest.raises(PlatformError) as exc:
        manager.submit(batch, request_file)
    assert exc.value.code == "REMOTE_SUBMIT_FAILED"
    assert recorder.calls == []


def test_status_parses_valid_json(tmp_path):
    profile = _profile(tmp_path)
    recorder = ProcessRecorder(responses=[_ok(stdout=_status_json())])
    manager = RemoteGpuJobManager(profile, SshRunner(profile, run_process=recorder))
    status = manager.status("batch_x")
    assert isinstance(status, RemoteBatchStatusV1)
    assert status.batch_id == "batch_x"
    assert status.status == "running"
    assert status.items[0].item_key == "000000"
    runner_argv = recorder.calls[0][0]
    assert "status" in runner_argv
    assert "--batch-id" in runner_argv
    assert "batch_x" in runner_argv


def test_status_nonzero_fails(tmp_path):
    profile = _profile(tmp_path)
    recorder = ProcessRecorder(responses=[
        subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="err"),
    ])
    manager = RemoteGpuJobManager(profile, SshRunner(profile, run_process=recorder))
    with pytest.raises(PlatformError) as exc:
        manager.status("batch_x")
    assert exc.value.code == "REMOTE_STATUS_UNAVAILABLE"


def test_status_malformed_json_fails(tmp_path):
    profile = _profile(tmp_path)
    recorder = ProcessRecorder(responses=[_ok(stdout="not-json{")])
    manager = RemoteGpuJobManager(profile, SshRunner(profile, run_process=recorder))
    with pytest.raises(PlatformError) as exc:
        manager.status("batch_x")
    assert exc.value.code == "REMOTE_STATUS_UNAVAILABLE"


def test_status_duplicate_json_key_fails(tmp_path):
    profile = _profile(tmp_path)
    recorder = ProcessRecorder(responses=[_ok(stdout='{"batch_id":"batch_x","batch_id":"batch_y"}')])
    manager = RemoteGpuJobManager(profile, SshRunner(profile, run_process=recorder))
    with pytest.raises(PlatformError) as exc:
        manager.status("batch_x")
    assert exc.value.code == "REMOTE_STATUS_UNAVAILABLE"


def test_status_extra_schema_field_fails(tmp_path):
    profile = _profile(tmp_path)
    raw = json.dumps({"batch_id": "batch_x", "status": "running", "items": [], "extra": 1})
    recorder = ProcessRecorder(responses=[_ok(stdout=raw)])
    manager = RemoteGpuJobManager(profile, SshRunner(profile, run_process=recorder))
    with pytest.raises(PlatformError) as exc:
        manager.status("batch_x")
    assert exc.value.code == "REMOTE_STATUS_UNAVAILABLE"


def test_status_unsafe_batch_id_rejected_before_subprocess(tmp_path):
    profile = _profile(tmp_path)
    recorder = ProcessRecorder()
    manager = RemoteGpuJobManager(profile, SshRunner(profile, run_process=recorder))
    with pytest.raises(PlatformError):
        manager.status("../escape")
    assert recorder.calls == []


def test_download_two_exact_files(tmp_path):
    profile = _profile(tmp_path)
    recorder = MaterializingRecorder()
    manager = RemoteGpuJobManager(profile, SshRunner(profile, run_process=recorder))
    result_dir = manager.download("batch_x", "000000", tmp_path / "download")
    assert result_dir == tmp_path / "download" / "000000"
    assert (result_dir / "envelope.json").is_file()
    assert (result_dir / "analysis_result.zip").is_file()
    assert len(recorder.calls) == 2
    for argv, _ in recorder.calls:
        assert "scp" in argv
        assert "root@auto.example.com:/root/jobs/batch_x/results/000000/" in " ".join(argv)
        assert "-r" not in argv
        assert "*" not in argv


def test_download_missing_file_fails(tmp_path):
    profile = _profile(tmp_path)
    recorder = EnvelopeOnlyRecorder()
    manager = RemoteGpuJobManager(profile, SshRunner(profile, run_process=recorder))
    with pytest.raises(PlatformError) as exc:
        manager.download("batch_x", "000000", tmp_path / "download")
    assert exc.value.code == "REMOTE_DOWNLOAD_FAILED"


def test_download_unsafe_item_key_rejected_before_subprocess(tmp_path):
    profile = _profile(tmp_path)
    recorder = ProcessRecorder()
    manager = RemoteGpuJobManager(profile, SshRunner(profile, run_process=recorder))
    with pytest.raises(PlatformError):
        manager.download("batch_x", "../escape", tmp_path / "d")
    assert recorder.calls == []


def test_download_nonzero_scp_fails(tmp_path):
    profile = _profile(tmp_path)
    recorder = ProcessRecorder(responses=[
        subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="err"),
    ])
    manager = RemoteGpuJobManager(profile, SshRunner(profile, run_process=recorder))
    with pytest.raises(PlatformError) as exc:
        manager.download("batch_x", "000000", tmp_path / "d")
    assert exc.value.code == "REMOTE_DOWNLOAD_FAILED"


def test_no_remote_repo_mutation_commands(tmp_path):
    profile = _profile(tmp_path)
    recorder = ProcessRecorder(responses=[_ok(), _ok(), _ok(stdout=_status_json())])
    manager = RemoteGpuJobManager(profile, SshRunner(profile, run_process=recorder))
    batch = _batch()
    request_file = tmp_path / "request.json"
    request_file.write_bytes(batch.model_dump_json().encode("utf-8"))
    manager.submit(batch, request_file)
    manager.status("batch_x")
    all_tokens = [token for argv, _ in recorder.calls for token in argv]
    for forbidden in ("git", "checkout", "pull", "reset", "fetch"):
        assert forbidden not in all_tokens
    assert "StrictHostKeyChecking=no" not in all_tokens


# ------------------------------------------- CORRECTIVE: remote shell boundary


@pytest.mark.parametrize("root", [
    "/root/jobs/a;id",
    "/root/jobs/a b",
    "/root/jobs/$HOME",
    "/root/jobs/$(id)",
    "/root/jobs/a&b",
    "/root/jobs/a|b",
    "/root/jobs/a#b",
    "/root/jobs/a'b",
    '/root/jobs/a"b',
    "/root/jobs/a`b",
    "/root/jobs/a>b",
    "/root/jobs/a<b",
    r"/root/jobs/a\b",
    "/root/./escape",
    "/root//double",
])
def test_profile_rejects_remote_shell_unsafe_paths(tmp_path, monkeypatch, settings, root):
    with pytest.raises(PlatformError) as exc:
        _load_profile(tmp_path, monkeypatch, settings, WSP_REMOTE_JOB_ROOT=root)
    assert exc.value.code == "REMOTE_EXECUTOR_UNAVAILABLE"


@pytest.mark.parametrize("root", [
    "/root/jobs/a;id",
    "/root/jobs/a b",
    "/root/jobs/a&b",
    "/root/jobs/a#b",
    "/root/./escape",
])
def test_profile_mapping_rejects_shell_unsafe_values(tmp_path, monkeypatch, settings, root):
    with pytest.raises(PlatformError) as exc:
        _load_profile(tmp_path, monkeypatch, settings,
                      WSP_REMOTE_DATASET_ROOTS_JSON=json.dumps({"SpaceNet": root}))
    assert exc.value.code == "REMOTE_EXECUTOR_UNAVAILABLE"


@pytest.mark.parametrize("job_root", [
    "/root/autodl-tmp/Wideband-Intelligent-Signal-Analysis-Platform",
    "/root/autodl-tmp/SpaceNet_Dataset",
])
def test_profile_accepts_realistic_job_roots(tmp_path, monkeypatch, settings, job_root):
    profile = _load_profile(tmp_path, monkeypatch, settings, WSP_REMOTE_JOB_ROOT=job_root)
    assert profile.remote_job_root == PurePosixPath(job_root)


def test_profile_accepts_realistic_deep_mapping_paths(tmp_path, monkeypatch, settings):
    deep = "/root/autodl-tmp/Claude/runs/cpn/ls_stft_yolo26n_aug_warm/weights/best.pt"
    profile = _load_profile(
        tmp_path, monkeypatch, settings,
        WSP_REMOTE_DATASET_ROOTS_JSON=json.dumps({"SpaceNet": deep}),
    )
    assert profile.dataset_roots["SpaceNet"] == PurePosixPath(deep)


@pytest.mark.parametrize("bad", [
    "/root/jobs/a;id",
    "/root/jobs/a b",
    "/root/jobs/$HOME",
    "/root/jobs/$(id)",
    "/root/jobs/a&b",
    "/root/jobs/a|b",
    "/root/jobs/a#b",
    "/root/jobs/a'b",
    '/root/jobs/a"b',
    "/root/jobs/a`b",
    "/root/jobs/a>b",
    "/root/jobs/a<b",
    r"/root/jobs/a\b",
    "/root/./escape",
    "/root//double",
])
def test_transport_rejects_shell_unsafe_remote_paths(tmp_path, bad):
    profile = _profile(tmp_path)
    recorder = ProcessRecorder()
    runner = SshRunner(profile, run_process=recorder)
    local = tmp_path / "request.json"
    local.write_bytes(b"{}")
    with pytest.raises(PlatformError):
        runner.upload_file(local, bad)
    with pytest.raises(PlatformError):
        runner.download_file(bad, tmp_path / "out" / "x")
    assert recorder.calls == []


@pytest.mark.parametrize("bad", [
    "/root/jobs/a;id",
    "/root/jobs/a b",
    "/root/jobs/$(id)",
    "/root/jobs/a#b",
    "/root/./escape",
])
def test_runner_rejects_shell_unsafe_request_path_before_subprocess(tmp_path, bad):
    profile = _profile(tmp_path)
    recorder = ProcessRecorder()
    runner = SshRunner(profile, run_process=recorder)
    with pytest.raises(PlatformError):
        runner.run_runner("submit", ("--request-path", bad))
    assert recorder.calls == []


def test_transport_accepts_realistic_autodl_paths(tmp_path):
    profile = _profile(tmp_path)
    recorder = MaterializingRecorder()
    runner = SshRunner(profile, run_process=recorder)
    local = tmp_path / "request.json"
    local.write_bytes(b"{}")
    deep = PurePosixPath("/root/autodl-tmp/Claude/runs/cpn/ls_stft_yolo26n_aug_warm/weights/best.pt")
    runner.upload_file(local, deep)
    runner.download_file(deep, tmp_path / "out" / "best.pt")
    assert len(recorder.calls) == 2


# --------------------------------------- CORRECTIVE: status identity checks


def test_status_wrong_batch_id_fails(tmp_path):
    profile = _profile(tmp_path)
    raw = json.dumps({"batch_id": "batch_y", "status": "running", "items": []})
    recorder = ProcessRecorder(responses=[_ok(stdout=raw)])
    manager = RemoteGpuJobManager(profile, SshRunner(profile, run_process=recorder))
    with pytest.raises(PlatformError) as exc:
        manager.status("batch_x")
    assert exc.value.code == "REMOTE_STATUS_UNAVAILABLE"


def test_status_duplicate_item_key_fails(tmp_path):
    profile = _profile(tmp_path)
    raw = json.dumps({
        "batch_id": "batch_x",
        "status": "running",
        "items": [
            {"item_key": "000000", "status": "running"},
            {"item_key": "000000", "status": "completed"},
        ],
    })
    recorder = ProcessRecorder(responses=[_ok(stdout=raw)])
    manager = RemoteGpuJobManager(profile, SshRunner(profile, run_process=recorder))
    with pytest.raises(PlatformError) as exc:
        manager.status("batch_x")
    assert exc.value.code == "REMOTE_STATUS_UNAVAILABLE"


# ----------------------- TASK 12F-B TASK 1: scalar worker env bridge


def _full_env_tokens(argv):
    env_idx = argv.index("env")
    segment = argv[env_idx + 1:]
    python_idx = segment.index("/opt/wsp-runtime/bin/python")
    return segment[:python_idx]


@pytest.mark.parametrize("subcommand", ["probe", "submit", "work"])
def test_probe_submit_work_argv_contain_full_scalar_worker_env(tmp_path, subcommand):
    profile = _profile(tmp_path)
    recorder = ProcessRecorder()
    runner = SshRunner(profile, run_process=recorder)
    if subcommand == "submit":
        runner.run_runner("submit", ("--request-path", "/root/jobs/incoming/batch_x.request.json"))
    elif subcommand == "work":
        runner.run_runner("work", ("--batch-id", "batch_x", "--job-root", "/root/jobs/batch_x"))
    else:
        runner.run_runner("probe")
    argv, _ = recorder.calls[0]
    env_tokens = _full_env_tokens(argv)
    expected = [
        "PYTHONPATH=/root/repo/backend",
        "WSP_REMOTE_JOB_ROOT=/root/jobs",
        "WSP_REMOTE_REPO_ROOT=/root/repo",
        f"WSP_REMOTE_REQUIRED_RUNTIME_COMMIT={'a' * 40}",
        "WSP_REMOTE_SPACENET_ROOT=/root/autodl-tmp/SpaceNet_Dataset",
        "WSP_REMOTE_DEVICE_TYPE=cuda",
        "WSP_REMOTE_DEVICE_INDEX=0",
        "WSP_REMOTE_PRECISION=float16",
        "WSP_REMOTE_ENVIRONMENT_REF=autodl_primary",
        "WSP_REMOTE_ENVIRONMENT_LABEL=autodl_primary",
        "WSP_REMOTE_DETECTOR_CHECKPOINT=/root/models/best.pt",
        "WSP_REMOTE_FRN_CHECKPOINT=/root/models/frn.pt",
        "WSP_REMOTE_FROZEN_CONFIG=/root/models/frozen_config.json",
        "WSP_REMOTE_LS_STFT_NORMALIZATION=/root/models/ls_stft_normalization.json",
    ]
    assert env_tokens == expected


def test_status_argv_contains_minimal_env_only(tmp_path):
    profile = _profile(tmp_path)
    recorder = ProcessRecorder()
    runner = SshRunner(profile, run_process=recorder)
    runner.run_runner("status", ("--batch-id", "batch_x"))
    argv, _ = recorder.calls[0]
    env_tokens = _full_env_tokens(argv)
    assert env_tokens == [
        "PYTHONPATH=/root/repo/backend",
        "WSP_REMOTE_JOB_ROOT=/root/jobs",
    ]
    for forbidden in ("WSP_REMOTE_SPACENET_ROOT", "WSP_REMOTE_DETECTOR_CHECKPOINT",
                      "WSP_REMOTE_FRN_CHECKPOINT", "WSP_REMOTE_FROZEN_CONFIG",
                      "WSP_REMOTE_LS_STFT_NORMALIZATION"):
        assert forbidden not in env_tokens


def test_status_invokes_ssh_with_missing_asset_mappings(tmp_path):
    profile = _profile(tmp_path)
    profile = RemoteProfile(
        name=profile.name, host=profile.host, port=profile.port, user=profile.user,
        ssh_key_path=profile.ssh_key_path, known_hosts_path=profile.known_hosts_path,
        remote_repo_root=profile.remote_repo_root, remote_job_root=profile.remote_job_root,
        remote_python_path=profile.remote_python_path,
        required_remote_runtime_commit=profile.required_remote_runtime_commit,
        dataset_roots={}, asset_paths={},
    )
    recorder = ProcessRecorder()
    runner = SshRunner(profile, run_process=recorder)
    runner.run_runner("status", ("--batch-id", "batch_x"))
    assert len(recorder.calls) == 1


def test_validate_runner_environment_zero_io(tmp_path):
    profile = _profile(tmp_path)
    recorder = ProcessRecorder()
    runner = SshRunner(profile, run_process=recorder)
    for subcommand in ("probe", "submit", "work", "status"):
        runner.validate_runner_environment(subcommand)
    assert recorder.calls == []
    # Failure path also performs zero I/O.
    empty = RemoteProfile(
        name=profile.name, host=profile.host, port=profile.port, user=profile.user,
        ssh_key_path=profile.ssh_key_path, known_hosts_path=profile.known_hosts_path,
        remote_repo_root=profile.remote_repo_root, remote_job_root=profile.remote_job_root,
        remote_python_path=profile.remote_python_path,
        required_remote_runtime_commit=profile.required_remote_runtime_commit,
        dataset_roots={}, asset_paths={},
    )
    failing = SshRunner(empty, run_process=recorder)
    for subcommand in ("probe", "submit", "work"):
        with pytest.raises(RemoteTransportError):
            failing.validate_runner_environment(subcommand)
    assert recorder.calls == []


def test_validate_runner_environment_full_worker_for_probe_submit_work(tmp_path):
    profile = _profile(tmp_path)
    empty = RemoteProfile(
        name=profile.name, host=profile.host, port=profile.port, user=profile.user,
        ssh_key_path=profile.ssh_key_path, known_hosts_path=profile.known_hosts_path,
        remote_repo_root=profile.remote_repo_root, remote_job_root=profile.remote_job_root,
        remote_python_path=profile.remote_python_path,
        required_remote_runtime_commit=profile.required_remote_runtime_commit,
        dataset_roots={}, asset_paths={},
    )
    runner = SshRunner(empty, run_process=ProcessRecorder())
    for subcommand in ("probe", "submit", "work"):
        with pytest.raises(RemoteTransportError):
            runner.validate_runner_environment(subcommand)


def test_validate_runner_environment_minimal_for_status(tmp_path):
    profile = _profile(tmp_path)
    empty = RemoteProfile(
        name=profile.name, host=profile.host, port=profile.port, user=profile.user,
        ssh_key_path=profile.ssh_key_path, known_hosts_path=profile.known_hosts_path,
        remote_repo_root=profile.remote_repo_root, remote_job_root=profile.remote_job_root,
        remote_python_path=profile.remote_python_path,
        required_remote_runtime_commit=profile.required_remote_runtime_commit,
        dataset_roots={}, asset_paths={},
    )
    runner = SshRunner(empty, run_process=ProcessRecorder())
    runner.validate_runner_environment("status")  # must not raise


@pytest.mark.parametrize("subcommand", ["probe", "submit", "work"])
def test_validate_runner_environment_without_legacy_assets_ok(tmp_path, subcommand):
    """D3B: the four legacy flat assets are no longer required for the generic
    production path; only the SpaceNet dataset root mapping is."""
    profile = _profile(tmp_path)
    generic_only = RemoteProfile(
        name=profile.name, host=profile.host, port=profile.port, user=profile.user,
        ssh_key_path=profile.ssh_key_path, known_hosts_path=profile.known_hosts_path,
        remote_repo_root=profile.remote_repo_root, remote_job_root=profile.remote_job_root,
        remote_python_path=profile.remote_python_path,
        required_remote_runtime_commit=profile.required_remote_runtime_commit,
        dataset_roots={"SpaceNet": PurePosixPath("/root/autodl-tmp/SpaceNet_Dataset")},
        asset_paths={},
        generic_asset_paths={
            "zoomspec_yolo26n_aug_combined_frn_v3/1.0.0/" + "b" * 64: {
                "detector_checkpoint": PurePosixPath("/root/assets/det.pt")
            }
        },
    )
    recorder = ProcessRecorder()
    runner = SshRunner(generic_only, run_process=recorder)
    runner.validate_runner_environment(subcommand)  # must not raise
    assert recorder.calls == []


def test_generic_only_runner_env_prefix_omits_legacy_and_carries_generic(tmp_path):
    profile = _profile(tmp_path)
    generic_only = RemoteProfile(
        name=profile.name, host=profile.host, port=profile.port, user=profile.user,
        ssh_key_path=profile.ssh_key_path, known_hosts_path=profile.known_hosts_path,
        remote_repo_root=profile.remote_repo_root, remote_job_root=profile.remote_job_root,
        remote_python_path=profile.remote_python_path,
        required_remote_runtime_commit=profile.required_remote_runtime_commit,
        dataset_roots={"SpaceNet": PurePosixPath("/root/autodl-tmp/SpaceNet_Dataset")},
        asset_paths={},
        generic_asset_paths={
            "zoomspec_yolo26n_aug_combined_frn_v3/1.0.0/" + "b" * 64: {
                "detector_checkpoint": PurePosixPath("/root/assets/det.pt")
            }
        },
    )
    prefix = SshRunner(generic_only)._runner_env_prefix(
        "work", PurePosixPath("/root/repo/backend"), PurePosixPath("/root/jobs")
    )
    assert not any(p.startswith("WSP_REMOTE_DETECTOR_CHECKPOINT=") for p in prefix)
    assert not any(p.startswith("WSP_REMOTE_FRN_CHECKPOINT=") for p in prefix)
    assert any(p.startswith("WSP_REMOTE_ASSET_PATHS_JSON=") for p in prefix)


def test_run_runner_internally_validates_before_ssh(tmp_path):
    profile = _profile(tmp_path)
    # Missing the required SpaceNet dataset root -> internal preflight fails
    # BEFORE any SSH invocation.
    missing_dataset = RemoteProfile(
        name=profile.name, host=profile.host, port=profile.port, user=profile.user,
        ssh_key_path=profile.ssh_key_path, known_hosts_path=profile.known_hosts_path,
        remote_repo_root=profile.remote_repo_root, remote_job_root=profile.remote_job_root,
        remote_python_path=profile.remote_python_path,
        required_remote_runtime_commit=profile.required_remote_runtime_commit,
        dataset_roots={},
        asset_paths={},
    )
    recorder = ProcessRecorder()
    runner = SshRunner(missing_dataset, run_process=recorder)
    with pytest.raises(RemoteTransportError):
        runner.run_runner("submit", ("--request-path", "/root/jobs/incoming/batch_x.request.json"))
    assert recorder.calls == []


def test_worker_env_values_round_trip(tmp_path):
    profile = _profile(tmp_path)
    recorder = ProcessRecorder()
    runner = SshRunner(profile, run_process=recorder)
    runner.run_runner("probe")
    argv, _ = recorder.calls[0]
    env_tokens = _full_env_tokens(argv)
    mapping = {token.split("=", 1)[0]: token.split("=", 1)[1] for token in env_tokens}
    assert mapping["WSP_REMOTE_REPO_ROOT"] == profile.remote_repo_root.as_posix()
    assert mapping["WSP_REMOTE_JOB_ROOT"] == profile.remote_job_root.as_posix()
    assert mapping["WSP_REMOTE_SPACENET_ROOT"] == profile.dataset_roots["SpaceNet"].as_posix()
    assert mapping["WSP_REMOTE_DETECTOR_CHECKPOINT"] == profile.asset_paths["detector_checkpoint"].as_posix()
    assert mapping["WSP_REMOTE_FRN_CHECKPOINT"] == profile.asset_paths["frn_checkpoint"].as_posix()
    assert mapping["WSP_REMOTE_FROZEN_CONFIG"] == profile.asset_paths["frozen_config"].as_posix()
    assert mapping["WSP_REMOTE_LS_STFT_NORMALIZATION"] == profile.asset_paths["ls_stft_normalization"].as_posix()
    assert mapping["WSP_REMOTE_REQUIRED_RUNTIME_COMMIT"] == profile.required_remote_runtime_commit
    assert mapping["PYTHONPATH"] == "/root/repo/backend"


@pytest.mark.parametrize("subcommand", ["probe", "submit"])
def test_missing_spacenet_mapping_fails_before_ssh(tmp_path, subcommand):
    profile = _profile(tmp_path)
    empty = RemoteProfile(
        name=profile.name, host=profile.host, port=profile.port, user=profile.user,
        ssh_key_path=profile.ssh_key_path, known_hosts_path=profile.known_hosts_path,
        remote_repo_root=profile.remote_repo_root, remote_job_root=profile.remote_job_root,
        remote_python_path=profile.remote_python_path,
        required_remote_runtime_commit=profile.required_remote_runtime_commit,
        dataset_roots={}, asset_paths={},
    )
    recorder = ProcessRecorder()
    runner = SshRunner(empty, run_process=recorder)
    with pytest.raises(RemoteTransportError):
        runner.run_runner(subcommand)
    assert recorder.calls == []


@pytest.mark.parametrize("missing", ["detector_checkpoint", "frn_checkpoint",
                                     "frozen_config", "ls_stft_normalization"])
def test_missing_legacy_asset_mapping_is_optional_and_not_forwarded(tmp_path, missing):
    """D3B: a missing legacy flat asset is no longer a preflight failure and is
    silently omitted from the legacy env bridge (generic assets are separate)."""
    profile = _profile(tmp_path)
    assets = dict(_FULL_ASSETS)
    assets.pop(missing)
    partial = RemoteProfile(
        name=profile.name, host=profile.host, port=profile.port, user=profile.user,
        ssh_key_path=profile.ssh_key_path, known_hosts_path=profile.known_hosts_path,
        remote_repo_root=profile.remote_repo_root, remote_job_root=profile.remote_job_root,
        remote_python_path=profile.remote_python_path,
        required_remote_runtime_commit=profile.required_remote_runtime_commit,
        dataset_roots={"SpaceNet": PurePosixPath("/root/autodl-tmp/SpaceNet_Dataset")},
        asset_paths=assets,
    )
    recorder = ProcessRecorder()
    runner = SshRunner(partial, run_process=recorder)
    runner.run_runner("probe")
    assert len(recorder.calls) == 1
    argv, _ = recorder.calls[0]
    env_tokens = _full_env_tokens(argv)
    env_name = {
        "detector_checkpoint": "WSP_REMOTE_DETECTOR_CHECKPOINT",
        "frn_checkpoint": "WSP_REMOTE_FRN_CHECKPOINT",
        "frozen_config": "WSP_REMOTE_FROZEN_CONFIG",
        "ls_stft_normalization": "WSP_REMOTE_LS_STFT_NORMALIZATION",
    }[missing]
    assert not any(t.startswith(env_name + "=") for t in env_tokens)


def test_no_config_details_leak_in_transport_errors(tmp_path):
    profile = _profile(tmp_path)
    empty = RemoteProfile(
        name=profile.name, host=profile.host, port=profile.port, user=profile.user,
        ssh_key_path=profile.ssh_key_path, known_hosts_path=profile.known_hosts_path,
        remote_repo_root=profile.remote_repo_root, remote_job_root=profile.remote_job_root,
        remote_python_path=profile.remote_python_path,
        required_remote_runtime_commit=profile.required_remote_runtime_commit,
        dataset_roots={}, asset_paths={},
    )
    runner = SshRunner(empty, run_process=ProcessRecorder())
    with pytest.raises(RemoteTransportError) as exc:
        runner.validate_runner_environment("submit")
    message = str(exc.value)
    assert "/root" not in message
    assert "auto.example.com" not in message
    assert "SpaceNet_Dataset" not in message


def test_no_credential_field_in_remote_env(tmp_path):
    """Only the remote env-assignment segment (after the SSH 'env' token and
    before the remote python) is inspected for credential leakage. SSH transport
    arguments (host/user/key/known_hosts) are legitimate and excluded."""
    profile = _profile(tmp_path)
    recorder = ProcessRecorder()
    runner = SshRunner(profile, run_process=recorder)
    runner.run_runner("probe")
    argv, _ = recorder.calls[0]
    env_tokens = _full_env_tokens(argv)
    values = [token.split("=", 1)[1] for token in env_tokens]
    assert profile.host not in values
    assert profile.user not in values
    assert str(profile.ssh_key_path) not in values
    assert str(profile.known_hosts_path) not in values
    names = [token.split("=", 1)[0] for token in env_tokens]
    for forbidden in ("WSP_REMOTE_HOST", "WSP_REMOTE_USER",
                      "WSP_REMOTE_SSH_KEY_PATH", "WSP_REMOTE_KNOWN_HOSTS_PATH"):
        assert forbidden not in names


def test_no_json_mapping_in_remote_command(tmp_path):
    profile = _profile(tmp_path)
    recorder = ProcessRecorder()
    runner = SshRunner(profile, run_process=recorder)
    runner.run_runner("probe")
    argv, _ = recorder.calls[0]
    joined = " ".join(argv)
    assert "WSP_REMOTE_DATASET_ROOTS_JSON" not in joined
    assert "WSP_REMOTE_ASSET_PATHS_JSON" not in joined

# ---------------------------------------------------------------------------
# D4 — narrow optional generic config bridge (legacy preserved)
# ---------------------------------------------------------------------------


def _unquote_assignment(token: str) -> str:
    """Undo the value-only quoting applied by the transport's remote-shell bridge."""
    name, sep, value = token.partition("=")
    if len(value) >= 2 and value.startswith("'") and value.endswith("'"):
        value = value[1:-1].replace("'\"'\"'", "'")
    return f"{name}{sep}{value}"


def _assignment(prefix, name):
    return next(p for p in prefix if p.split("=", 1)[0] == name)


def test_runner_env_prefix_forwards_generic_namespaced_assets(tmp_path):
    import dataclasses
    import json

    from app.remote_execution.transport import SshRunner
    from app.remote_execution.worker_context import (
        ENV_ASSET_PATHS_JSON,
        ENV_DEVICE_INDEX,
        ENV_MANIFEST_ROOT,
    )

    namespace = "p/1.0.0/" + "b" * 64
    profile = dataclasses.replace(
        _profile(tmp_path),
        manifest_root=PurePosixPath("/root/manifests"),
        generic_asset_paths={namespace: {"w": PurePosixPath("/root/assets/w.pt")}},
        device_index=1,
    )
    prefix = SshRunner(profile)._runner_env_prefix(
        "work", PurePosixPath("/root/repo/backend"), PurePosixPath("/root/jobs")
    )
    assert f"{ENV_MANIFEST_ROOT}=/root/manifests" in prefix
    assert f"{ENV_DEVICE_INDEX}=1" in prefix
    asset_line = _assignment(prefix, ENV_ASSET_PATHS_JSON)
    assert json.loads(_unquote_assignment(asset_line).split("=", 1)[1]) == {
        namespace: {"w": "/root/assets/w.pt"}
    }


def test_runner_env_prefix_legacy_has_no_generic_env_but_carries_descriptor(tmp_path):
    from app.remote_execution.transport import SshRunner
    from app.remote_execution.worker_context import (
        ENV_ASSET_PATHS_JSON,
        ENV_ENVIRONMENT_LABEL,
        ENV_ENVIRONMENT_REF,
        ENV_MANIFEST_ROOT,
    )

    prefix = SshRunner(_profile(tmp_path))._runner_env_prefix(
        "work", PurePosixPath("/root/repo/backend"), PurePosixPath("/root/jobs")
    )
    assert not any(p.startswith(ENV_ASSET_PATHS_JSON + "=") for p in prefix)
    assert not any(p.startswith(ENV_MANIFEST_ROOT + "=") for p in prefix)
    assert f"{ENV_ENVIRONMENT_REF}=autodl_primary" in prefix
    assert f"{ENV_ENVIRONMENT_LABEL}=autodl_primary" in prefix


def test_runner_env_prefix_legacy_and_generic_coexist(tmp_path):
    import dataclasses
    import json

    from app.remote_execution.transport import SshRunner
    from app.remote_execution.worker_context import ENV_ASSET_PATHS_JSON

    namespace = "p/1.0.0/" + "b" * 64
    profile = dataclasses.replace(
        _profile(tmp_path),
        manifest_root=PurePosixPath("/root/manifests"),
        generic_asset_paths={namespace: {"w": PurePosixPath("/root/assets/w.pt")}},
    )
    prefix = SshRunner(profile)._runner_env_prefix(
        "work", PurePosixPath("/root/repo/backend"), PurePosixPath("/root/jobs")
    )
    assert "WSP_REMOTE_DETECTOR_CHECKPOINT=/root/models/best.pt" in prefix
    namespaced = json.loads(
        _unquote_assignment(_assignment(prefix, ENV_ASSET_PATHS_JSON)).split("=", 1)[1]
    )
    assert namespaced == {namespace: {"w": "/root/assets/w.pt"}}
    runner = SshRunner(profile, run_process=ProcessRecorder())
    runner.validate_runner_environment("work")


def test_runner_env_prefix_remote_shell_roundtrip_preserves_namespaced_json(tmp_path):
    """Run the real env-prefix tokens through ``sh -c`` the way OpenSSH does."""
    import dataclasses
    import json
    import shlex
    import shutil
    import subprocess as sp

    from app.remote_execution.transport import SshRunner
    from app.remote_execution.worker_context import ENV_ASSET_PATHS_JSON

    sh = shutil.which("sh")
    assert sh is not None
    namespace = "p/1.0.0/" + "b" * 64
    profile = dataclasses.replace(
        _profile(tmp_path),
        manifest_root=PurePosixPath("/root/manifests"),
        generic_asset_paths={namespace: {"w": PurePosixPath("/root/assets/w.pt")}},
    )
    prefix = SshRunner(profile)._runner_env_prefix(
        "work", PurePosixPath("/root/repo/backend"), PurePosixPath("/root/jobs")
    )
    # OpenSSH concatenates the remote command args with single spaces and hands the
    # string to the login shell; emulate with `sh -c "env <assignments> sh -c '...'"`.
    remote_command = " ".join(prefix) + " " + sh + " -c " + shlex.quote('printf %s "$WSP_REMOTE_ASSET_PATHS_JSON"')
    completed = sp.run([sh, "-c", remote_command], capture_output=True, text=True, check=True)
    assert completed.stdout == json.dumps(
        {namespace: {"w": "/root/assets/w.pt"}}, separators=(",", ":"), sort_keys=True
    )
    assert json.loads(completed.stdout) == {namespace: {"w": "/root/assets/w.pt"}}


def test_profile_and_worker_descriptor_identity_after_remote_shell(tmp_path):
    """The worker reconstructs the exact deployment-owned descriptor after the
    env bridge, including environment identity, even without generic assets."""
    import dataclasses
    import shlex
    import shutil
    import subprocess as sp

    from app.remote_execution.transport import SshRunner
    from app.remote_execution.worker_context import RemoteWorkerContext

    sh = shutil.which("sh")
    assert sh is not None
    profile = dataclasses.replace(_profile(tmp_path), device_index=3)
    prefix = SshRunner(profile)._runner_env_prefix(
        "work", PurePosixPath("/root/repo/backend"), PurePosixPath("/root/jobs")
    )
    remote = " ".join(prefix) + " " + sh + " -c " + shlex.quote("env")
    stdout = sp.run([sh, "-c", remote], capture_output=True, text=True, check=True).stdout
    env = {}
    for line in stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            env[key] = value
    worker = RemoteWorkerContext.from_env(env)
    assert worker.runtime_descriptor() == profile.runtime_descriptor()
    assert worker.runtime_descriptor().environment_ref == profile.name
    assert worker.runtime_descriptor().environment_label == profile.name


def test_remote_profile_descriptor_cuda_only_and_index_required(tmp_path):
    import dataclasses

    base = _profile(tmp_path)
    with pytest.raises(PlatformError):
        dataclasses.replace(base, device_type="cpu").runtime_descriptor()
    with pytest.raises(PlatformError):
        dataclasses.replace(base, device_index=None).runtime_descriptor()
    assert dataclasses.replace(base, device_index=2).runtime_descriptor().device_index == 2
