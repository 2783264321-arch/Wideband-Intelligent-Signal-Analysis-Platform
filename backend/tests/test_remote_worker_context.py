"""RemoteWorkerContext parsing tests (Task 12F-B Task 1).

The worker context is constructed on the remote server from exact fixed scalar
environment variable names only. It must never carry SSH/credential fields and
must fail closed (PlatformError REMOTE_WORKER_CONTEXT_INVALID) on any missing
or unsafe scalar field. No GPU, no filesystem I/O.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from app.core.errors import PlatformError
from app.remote_execution.worker_context import (
    ENV_DETECTOR_CHECKPOINT,
    ENV_FRN_CHECKPOINT,
    ENV_FROZEN_CONFIG,
    ENV_JOB_ROOT,
    ENV_LS_STFT_NORMALIZATION,
    ENV_REPO_ROOT,
    ENV_REQUIRED_RUNTIME_COMMIT,
    ENV_SPACENET_ROOT,
    RemoteWorkerContext,
    is_complete_worker_env,
    required_worker_env_vars,
)

REPO_ROOT = "/root/repo"
JOB_ROOT = "/root/jobs"
SPACENET_ROOT = "/root/autodl-tmp/SpaceNet_Dataset"
DETECTOR = "/root/models/detector.pt"
FRN = "/root/models/frn.pt"
FROZEN = "/root/models/frozen_config.json"
NORM = "/root/models/ls_stft_normalization.json"
COMMIT = "a" * 40

_CREDENTIAL_FIELDS = {"host", "user", "port", "ssh_key_path", "known_hosts_path"}


def _full_env():
    return {
        ENV_REPO_ROOT: REPO_ROOT,
        ENV_JOB_ROOT: JOB_ROOT,
        ENV_REQUIRED_RUNTIME_COMMIT: COMMIT,
        ENV_SPACENET_ROOT: SPACENET_ROOT,
        ENV_DETECTOR_CHECKPOINT: DETECTOR,
        ENV_FRN_CHECKPOINT: FRN,
        ENV_FROZEN_CONFIG: FROZEN,
        ENV_LS_STFT_NORMALIZATION: NORM,
    }


def _apply(monkeypatch, env):
    for name in _full_env():
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)


def test_context_from_env_valid_full(monkeypatch):
    _apply(monkeypatch, _full_env())
    context = RemoteWorkerContext.from_env()
    assert context.repo_root == Path(REPO_ROOT)
    assert context.job_root == Path(JOB_ROOT)
    assert context.required_runtime_commit == COMMIT
    assert context.dataset_root_space_net == Path(SPACENET_ROOT)
    assert context.detector_checkpoint == Path(DETECTOR)
    assert context.frn_checkpoint == Path(FRN)
    assert context.frozen_config_path == Path(FROZEN)
    assert context.ls_stft_normalization_path == Path(NORM)
    assert context.label_space_root == Path(REPO_ROOT) / "label_spaces"
    assert context.asset_manifest_path == (
        Path(REPO_ROOT) / "backend" / "app" / "pipelines"
        / "zoomspec_yolo26n_aug_combined_frn_v3" / "asset_manifest.json"
    )


def test_context_contains_no_ssh_credential_fields(monkeypatch):
    _apply(monkeypatch, _full_env())
    context = RemoteWorkerContext.from_env()
    field_names = {field.name for field in dataclasses.fields(context)}
    assert field_names.isdisjoint(_CREDENTIAL_FIELDS)
    representation = repr(context).lower()
    assert "ssh" not in representation
    assert "known_hosts" not in representation
    assert "@" not in representation
    assert ":" not in representation.replace("/", "")


def test_context_missing_spacenet_root_fails_closed(monkeypatch):
    env = _full_env()
    env.pop(ENV_SPACENET_ROOT)
    _apply(monkeypatch, env)
    with pytest.raises(PlatformError) as exc:
        RemoteWorkerContext.from_env()
    assert exc.value.code == "REMOTE_WORKER_CONTEXT_INVALID"


@pytest.mark.parametrize("name", [
    ENV_DETECTOR_CHECKPOINT,
    ENV_FRN_CHECKPOINT,
    ENV_FROZEN_CONFIG,
    ENV_LS_STFT_NORMALIZATION,
])
def test_context_missing_asset_scalar_fails_closed(monkeypatch, name):
    env = _full_env()
    env.pop(name)
    _apply(monkeypatch, env)
    with pytest.raises(PlatformError) as exc:
        RemoteWorkerContext.from_env()
    assert exc.value.code == "REMOTE_WORKER_CONTEXT_INVALID"


@pytest.mark.parametrize("bad", [
    "/root/../escape",
    "/root/a b",
    "/root/a;id",
    "/root//double",
    "/root/./escape",
])
def test_context_unsafe_path_rejected(monkeypatch, bad):
    env = _full_env()
    env[ENV_SPACENET_ROOT] = bad
    _apply(monkeypatch, env)
    with pytest.raises(PlatformError) as exc:
        RemoteWorkerContext.from_env()
    assert exc.value.code == "REMOTE_WORKER_CONTEXT_INVALID"


@pytest.mark.parametrize("bad", ["not-a-commit", "a" * 39, "A" * 40, "", "a" * 41])
def test_context_invalid_runtime_commit_rejected(monkeypatch, bad):
    env = _full_env()
    env[ENV_REQUIRED_RUNTIME_COMMIT] = bad
    _apply(monkeypatch, env)
    with pytest.raises(PlatformError) as exc:
        RemoteWorkerContext.from_env()
    assert exc.value.code == "REMOTE_WORKER_CONTEXT_INVALID"


def test_required_worker_env_vars_exact():
    assert required_worker_env_vars() == (
        ENV_REPO_ROOT,
        ENV_JOB_ROOT,
        ENV_REQUIRED_RUNTIME_COMMIT,
        ENV_SPACENET_ROOT,
        ENV_DETECTOR_CHECKPOINT,
        ENV_FRN_CHECKPOINT,
        ENV_FROZEN_CONFIG,
        ENV_LS_STFT_NORMALIZATION,
    )


def test_is_complete_worker_env_full_true():
    assert is_complete_worker_env(_full_env()) is True


@pytest.mark.parametrize("name", [
    ENV_REPO_ROOT,
    ENV_JOB_ROOT,
    ENV_REQUIRED_RUNTIME_COMMIT,
    ENV_SPACENET_ROOT,
    ENV_DETECTOR_CHECKPOINT,
    ENV_FRN_CHECKPOINT,
    ENV_FROZEN_CONFIG,
    ENV_LS_STFT_NORMALIZATION,
])
def test_is_complete_worker_env_missing_false(name):
    env = _full_env()
    env.pop(name)
    assert is_complete_worker_env(env) is False