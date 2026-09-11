"""RemoteWorkerContext parsing tests.

The worker context is constructed on the remote server from fixed scalar
environment variables only. It must never carry SSH/credential fields and must
fail closed (``REMOTE_WORKER_CONTEXT_INVALID``) on any missing/unsafe scalar or
malformed asset configuration. No GPU, no filesystem I/O.

E2B: ``WSP_REMOTE_ASSET_PATHS_JSON`` is generic-namespaced ONLY; the retired
legacy flat ZoomSpec scalar deployment fields/helpers no longer exist.
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.errors import PlatformError
from app.remote_execution.worker_context import (
    ENV_ASSET_PATHS_JSON,
    ENV_DEVICE_TYPE,
    ENV_ENVIRONMENT_LABEL,
    ENV_ENVIRONMENT_REF,
    ENV_JOB_ROOT,
    ENV_MANIFEST_ROOT,
    ENV_REPO_ROOT,
    ENV_REQUIRED_RUNTIME_COMMIT,
    ENV_SPACENET_ROOT,
    RemoteWorkerContext,
)

REPO_ROOT = "/root/repo"
JOB_ROOT = "/root/jobs"
SPACENET_ROOT = "/root/autodl-tmp/SpaceNet_Dataset"
COMMIT = "a" * 40

_PLUGIN_ID = "zoomspec_yolo26n_aug_combined_frn_v3"
_PLUGIN_VERSION = "1.0.0"
_GENERIC_SHA = "b" * 64
_GENERIC_NAMESPACE = f"{_PLUGIN_ID}/{_PLUGIN_VERSION}/{_GENERIC_SHA}"

_CREDENTIAL_FIELDS = {"host", "user", "port", "ssh_key_path", "known_hosts_path"}


def _core_env():
    return {
        ENV_REPO_ROOT: REPO_ROOT,
        ENV_JOB_ROOT: JOB_ROOT,
        ENV_REQUIRED_RUNTIME_COMMIT: COMMIT,
        ENV_SPACENET_ROOT: SPACENET_ROOT,
    }


def _apply(monkeypatch, env):
    for name in (
        ENV_REPO_ROOT, ENV_JOB_ROOT, ENV_REQUIRED_RUNTIME_COMMIT, ENV_SPACENET_ROOT,
        ENV_ASSET_PATHS_JSON, ENV_MANIFEST_ROOT, ENV_DEVICE_TYPE,
        ENV_ENVIRONMENT_REF, ENV_ENVIRONMENT_LABEL,
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)


def test_context_from_env_valid_core(monkeypatch):
    _apply(monkeypatch, _core_env())
    context = RemoteWorkerContext.from_env()
    assert context.repo_root == Path(REPO_ROOT)
    assert context.job_root == Path(JOB_ROOT)
    assert context.required_runtime_commit == COMMIT
    assert context.dataset_root_space_net == Path(SPACENET_ROOT)
    assert context.label_space_root == Path(REPO_ROOT) / "label_spaces"
    assert context.asset_paths == {}
    assert context.manifest_root is None


def test_context_contains_no_ssh_credential_fields(monkeypatch):
    _apply(monkeypatch, _core_env())
    context = RemoteWorkerContext.from_env()
    field_names = {field.name for field in dataclasses.fields(context)}
    assert field_names.isdisjoint(_CREDENTIAL_FIELDS)
    representation = repr(context).lower()
    assert "ssh" not in representation
    assert "known_hosts" not in representation
    assert "@" not in representation


def test_context_has_no_legacy_asset_fields(monkeypatch):
    _apply(monkeypatch, _core_env())
    context = RemoteWorkerContext.from_env()
    for legacy in (
        "detector_checkpoint", "frn_checkpoint", "frozen_config_path",
        "ls_stft_normalization_path", "asset_manifest_path",
    ):
        assert not hasattr(context, legacy)


def test_worker_context_module_has_no_legacy_symbols():
    import app.remote_execution.worker_context as wc
    for legacy in (
        "ENV_DETECTOR_CHECKPOINT", "ENV_FRN_CHECKPOINT",
        "ENV_FROZEN_CONFIG", "ENV_LS_STFT_NORMALIZATION",
        "required_worker_env_vars", "is_complete_worker_env",
        "_LEGACY_ASSET_NAME_RE",
    ):
        assert not hasattr(wc, legacy), legacy


@pytest.mark.parametrize("name", [
    ENV_REPO_ROOT, ENV_JOB_ROOT, ENV_REQUIRED_RUNTIME_COMMIT, ENV_SPACENET_ROOT,
])
def test_context_missing_core_field_fails_closed(monkeypatch, name):
    env = _core_env()
    env.pop(name)
    _apply(monkeypatch, env)
    with pytest.raises(PlatformError) as exc:
        RemoteWorkerContext.from_env()
    assert exc.value.code == "REMOTE_WORKER_CONTEXT_INVALID"


@pytest.mark.parametrize("bad", [
    "/root/../escape", "/root/a b", "/root/a;id", "/root//double", "/root/./escape",
])
def test_context_unsafe_path_rejected(monkeypatch, bad):
    env = _core_env()
    env[ENV_SPACENET_ROOT] = bad
    _apply(monkeypatch, env)
    with pytest.raises(PlatformError) as exc:
        RemoteWorkerContext.from_env()
    assert exc.value.code == "REMOTE_WORKER_CONTEXT_INVALID"


@pytest.mark.parametrize("bad", ["not-a-commit", "a" * 39, "A" * 40, "", "a" * 41])
def test_context_invalid_runtime_commit_rejected(monkeypatch, bad):
    env = _core_env()
    env[ENV_REQUIRED_RUNTIME_COMMIT] = bad
    _apply(monkeypatch, env)
    with pytest.raises(PlatformError) as exc:
        RemoteWorkerContext.from_env()
    assert exc.value.code == "REMOTE_WORKER_CONTEXT_INVALID"


def test_generic_namespaced_mapping_accepted_and_resolved(monkeypatch):
    env = _core_env()
    env[ENV_MANIFEST_ROOT] = "/root/manifests"
    env[ENV_ASSET_PATHS_JSON] = json.dumps({
        _GENERIC_NAMESPACE: {
            "detector_checkpoint": "/root/assets/det.pt",
            "frn_checkpoint": "/root/assets/frn.pt",
        }
    })
    _apply(monkeypatch, env)
    context = RemoteWorkerContext.from_env()
    assert context.manifest_root == Path("/root/manifests")
    resolved = context.resolve_assets(
        plugin_id=_PLUGIN_ID, plugin_version=_PLUGIN_VERSION,
        asset_manifest_sha256=_GENERIC_SHA,
        manifest=SimpleNamespace(assets={"detector_checkpoint": "x"}),
    )
    assert resolved == {"detector_checkpoint": Path("/root/assets/det.pt")}


@pytest.mark.parametrize("flat_json", [
    json.dumps({"detector_checkpoint": "/root/assets/det.pt"}),
    json.dumps({
        "detector_checkpoint": "/root/assets/det.pt",
        _GENERIC_NAMESPACE: {"w": "/root/assets/w.pt"},
    }),
])
def test_flat_asset_deployment_rejected(monkeypatch, flat_json):
    """E2B: the legacy flat shape (single or mixed) is no longer valid."""
    env = _core_env()
    env[ENV_ASSET_PATHS_JSON] = flat_json
    _apply(monkeypatch, env)
    with pytest.raises(PlatformError) as exc:
        RemoteWorkerContext.from_env()
    assert exc.value.code == "REMOTE_WORKER_CONTEXT_INVALID"


def test_unknown_asset_namespace_fails_closed(monkeypatch):
    env = _core_env()
    env[ENV_ASSET_PATHS_JSON] = json.dumps({_GENERIC_NAMESPACE: {"w": "/root/assets/w.pt"}})
    _apply(monkeypatch, env)
    context = RemoteWorkerContext.from_env()
    with pytest.raises(PlatformError) as exc:
        context.resolve_assets(
            plugin_id="other", plugin_version="1", asset_manifest_sha256="c" * 64,
            manifest=SimpleNamespace(assets={"w": "x"}),
        )
    assert exc.value.code == "REMOTE_WORKER_CONTEXT_INVALID"


def test_missing_declared_generic_asset_fails_closed(monkeypatch):
    env = _core_env()
    env[ENV_ASSET_PATHS_JSON] = json.dumps({_GENERIC_NAMESPACE: {"other": "/root/assets/o.pt"}})
    _apply(monkeypatch, env)
    context = RemoteWorkerContext.from_env()
    with pytest.raises(PlatformError) as exc:
        context.resolve_assets(
            plugin_id=_PLUGIN_ID, plugin_version=_PLUGIN_VERSION,
            asset_manifest_sha256=_GENERIC_SHA, manifest=SimpleNamespace(assets={"w": "x"}),
        )
    assert exc.value.code == "REMOTE_WORKER_CONTEXT_INVALID"


@pytest.mark.parametrize("bad_mapping", [
    {_GENERIC_NAMESPACE: {"w": "/root/../escape"}},
    {_GENERIC_NAMESPACE: {"w": 123}},
    {_GENERIC_NAMESPACE: {"bad name": "/root/assets/w.pt"}},
    {_GENERIC_NAMESPACE: {}},
    {"not-a-namespace": {"w": "/root/assets/w.pt"}},
    {_GENERIC_SHA[:63]: {"w": "/root/assets/w.pt"}},
])
def test_malformed_generic_mapping_rejected(monkeypatch, bad_mapping):
    env = _core_env()
    env[ENV_ASSET_PATHS_JSON] = json.dumps(bad_mapping)
    _apply(monkeypatch, env)
    with pytest.raises(PlatformError) as exc:
        RemoteWorkerContext.from_env()
    assert exc.value.code == "REMOTE_WORKER_CONTEXT_INVALID"


def test_non_cuda_remote_worker_rejected(monkeypatch):
    env = _core_env()
    env[ENV_DEVICE_TYPE] = "cpu"
    _apply(monkeypatch, env)
    with pytest.raises(PlatformError) as exc:
        RemoteWorkerContext.from_env()
    assert exc.value.code == "REMOTE_WORKER_CONTEXT_INVALID"


def test_worker_descriptor_carries_environment_identity(monkeypatch):
    env = _core_env()
    env[ENV_ENVIRONMENT_REF] = "remote:autodl_primary:abc"
    env[ENV_ENVIRONMENT_LABEL] = "autodl_primary"
    _apply(monkeypatch, env)
    descriptor = RemoteWorkerContext.from_env().runtime_descriptor()
    assert descriptor.executor == "remote_gpu"
    assert (descriptor.device_type, descriptor.device_index) == ("cuda", 0)
    assert descriptor.environment_ref == "remote:autodl_primary:abc"
    assert descriptor.environment_label == "autodl_primary"
