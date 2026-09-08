from pathlib import Path

import pytest

from app.core.errors import PlatformError
from app.core.config import Settings
from app.remote_execution.profile import RemoteProfile


def _settings() -> Settings:
    return Settings()


def _write_assets(tmp_path: Path):
    key = tmp_path / "id_ed25519"
    key.write_bytes(b"key")
    hosts = tmp_path / "known_hosts"
    hosts.write_bytes(b"hosts")
    return key, hosts


def _base_env(tmp_path, monkeypatch, *, required_runtime_commit):
    key, hosts = _write_assets(tmp_path)
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
        "WSP_REMOTE_REQUIRED_RUNTIME_COMMIT": required_runtime_commit,
    }
    for name, value in env.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    return key, hosts


def test_valid_runtime_commit_accepted(tmp_path, monkeypatch):
    _base_env(tmp_path, monkeypatch, required_runtime_commit="a" * 40)
    profile = RemoteProfile.from_env(_settings())
    assert profile.required_remote_runtime_commit == "a" * 40


def test_missing_runtime_commit_is_unavailable(tmp_path, monkeypatch):
    _base_env(tmp_path, monkeypatch, required_runtime_commit=None)
    with pytest.raises(PlatformError) as exc:
        RemoteProfile.from_env(_settings())
    assert exc.value.code == "REMOTE_EXECUTOR_UNAVAILABLE"


def test_invalid_runtime_commit_rejected(tmp_path, monkeypatch):
    for bad in ("a" * 39, "a" * 41, "z" * 40, "A" * 40, ""):
        _base_env(tmp_path, monkeypatch, required_runtime_commit=bad)
        with pytest.raises(PlatformError) as exc:
            RemoteProfile.from_env(_settings())
        assert exc.value.code == "REMOTE_EXECUTOR_UNAVAILABLE"


def test_runtime_commit_not_derived_from_local_git(tmp_path, monkeypatch):
    # A local git commit would be lowercase hex of arb length/context; the field
    # must come from the env var only, not from `git rev-parse HEAD`.
    _base_env(tmp_path, monkeypatch, required_runtime_commit="b" * 40)
    profile = RemoteProfile.from_env(_settings())
    assert profile.required_remote_runtime_commit == "b" * 40