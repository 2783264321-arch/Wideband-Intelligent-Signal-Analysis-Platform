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
from app.remote_execution.transport import SshRunner


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
        dataset_roots={"SpaceNet": PurePosixPath("/root/autodl-tmp/SpaceNet_Dataset")},
        asset_paths={"detector_checkpoint": PurePosixPath("/root/models/best.pt")},
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
        "WSP_REMOTE_DATASET_ROOTS_JSON": json.dumps({"SpaceNet": "/root/autodl-tmp/SpaceNet_Dataset"}),
        "WSP_REMOTE_ASSET_PATHS_JSON": json.dumps({"detector_checkpoint": "/root/models/best.pt"}),
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
        required_remote_runtime_commit="9a6f0feac0b0e6e2ac8ecd65d2e4383479e09f7c",
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
    assert profile.asset_paths == {"detector_checkpoint": PurePosixPath("/root/models/best.pt")}


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
    assert "python3" in argv
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
    assert "python3" in argv
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
        dataset_roots={"SpaceNet": PurePosixPath("/root/autodl-tmp/SpaceNet_Dataset")},
        asset_paths={"detector_checkpoint": PurePosixPath("/root/models/best.pt")},
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
    assert argv[env_idx + 2] == "python3"
    assert argv[env_idx + 3] == "-m"
    assert argv[env_idx + 4] == "app.remote_execution.runner"
    assert argv[env_idx + 5] == "submit"


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