"""TASK D3B fix round — generic remote bootstrap closure.

Generic-only deployments (namespaced assets, no legacy ZoomSpec flat assets) must
construct the worker context, pass transport preflight, and resolve assets; the
remote probe must be plugin/release/manifest scoped so multiple release-bound
plugins coexist without a global single-manifest assumption.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_plugin_executor import _FakeHandle, _manifest, _resolved

from app.core.errors import PlatformError
from app.pipelines.base import PipelineDefinition
from app.remote_execution import runner as runner_module
from app.remote_execution.executor import SshRemoteExecutorProbe
from app.remote_execution.profile import RemoteProfile
from app.remote_execution.schema import RemoteProbeResponseV1
from app.remote_execution.worker_context import RemoteWorkerContext


def _resolved_for(manifest, definition, release_id):
    resolved = _resolved(manifest)
    release = replace(
        resolved.release,
        plugin_id=definition.id,
        plugin_version=definition.version,
        model_release_id=release_id,
    )
    return replace(resolved, release=release)

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_COMMIT = "68b1464842d0fb366fc211f53436d0ba49e3fbef"

_DEF_A = PipelineDefinition(
    id="plugin_a", name="A", version="1.0", label_space="spacenet_14",
    recommended_device="GPU", cpu_supported=False, stages=(), inspectable_stages=(),
    executors_supported=("remote_gpu",), recommended_executor="remote_gpu",
    input_compatibility=("spacenet_14",), dataset_adapters=("SpaceNet",),
    model_release_required=True,
)
_DEF_B = PipelineDefinition(
    id="plugin_b", name="B", version="2.0", label_space="spacenet_14",
    recommended_device="GPU", cpu_supported=False, stages=(), inspectable_stages=(),
    executors_supported=("remote_gpu",), recommended_executor="remote_gpu",
    input_compatibility=("spacenet_14",), dataset_adapters=("SpaceNet",),
    model_release_required=True,
)


def _worker_env(monkeypatch, namespaced):
    env = {
        "WSP_REMOTE_REPO_ROOT": str(REPO_ROOT),
        "WSP_REMOTE_JOB_ROOT": "/root/jobs",
        "WSP_REMOTE_REQUIRED_RUNTIME_COMMIT": RUNTIME_COMMIT,
        "WSP_REMOTE_SPACENET_ROOT": str(REPO_ROOT),
        "WSP_REMOTE_ASSET_PATHS_JSON": json.dumps(namespaced),
    }
    for name in ("WSP_REMOTE_DETECTOR_CHECKPOINT", "WSP_REMOTE_FRN_CHECKPOINT",
                 "WSP_REMOTE_FROZEN_CONFIG", "WSP_REMOTE_LS_STFT_NORMALIZATION"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)


class _ProbeStore:
    def __init__(self, by_release):
        self._by_release = by_release
        self.requests = []

    def resolve(self, plugin_id, plugin_version, requested):
        self.requests.append((plugin_id, plugin_version, requested))
        return self._by_release[(plugin_id, plugin_version, requested)]


def _patch_remote_resolution(monkeypatch, handles, store):
    from app.pipelines import plugin_registry as registry_module
    from app.remote_execution import probe as probe_module

    registry = SimpleNamespace(get=lambda pid, ver: handles[(pid, ver)])
    monkeypatch.setattr(registry_module, "create_plugin_registry", lambda: registry)
    monkeypatch.setattr(runner_module, "_build_model_release_store", lambda: store)
    captured = {}

    def fake_run_probe(worker, *, descriptor, plugin_definition, manifest, assets, torch_import=None):
        captured["plugin_definition"] = plugin_definition
        captured["manifest"] = manifest
        captured["assets"] = assets
        captured["descriptor"] = descriptor
        return RemoteProbeResponseV1(
            schema_version=1, status="available",
            remote_runtime_commit=worker.required_runtime_commit,
            asset_manifest_sha256=manifest.asset_manifest_sha256,
            device=descriptor.device_index,
        )

    monkeypatch.setattr(probe_module, "run_probe", fake_run_probe)
    return captured


def _probe_args(definition, release_id, manifest_sha):
    return SimpleNamespace(
        plugin_id=definition.id, plugin_version=definition.version,
        model_release_id=release_id, asset_manifest_sha256=manifest_sha,
    )


def test_cli_probe_resolves_two_release_manifests_independently(tmp_path, monkeypatch, capsys):
    manifest_a, asset_a = _manifest(tmp_path / "a", content=b"a-weights")
    manifest_b, asset_b = _manifest(tmp_path / "b", content=b"b-weights")
    _worker_env(monkeypatch, {
        f"plugin_a/1.0/{manifest_a.asset_manifest_sha256}": {"w": str(asset_a)},
        f"plugin_b/2.0/{manifest_b.asset_manifest_sha256}": {"w": str(asset_b)},
    })
    handle_a = _FakeHandle(_DEF_A)
    handle_b = _FakeHandle(_DEF_B)
    store = _ProbeStore({
        ("plugin_a", "1.0", "release_a"): _resolved_for(manifest_a, _DEF_A, "release_a"),
        ("plugin_b", "2.0", "release_b"): _resolved_for(manifest_b, _DEF_B, "release_b"),
    })
    captured = _patch_remote_resolution(
        monkeypatch, {("plugin_a", "1.0"): handle_a, ("plugin_b", "2.0"): handle_b}, store
    )

    rc_a = runner_module._cli_probe(_probe_args(_DEF_A, "release_a", manifest_a.asset_manifest_sha256))
    rc_b = runner_module._cli_probe(_probe_args(_DEF_B, "release_b", manifest_b.asset_manifest_sha256))
    assert rc_a == 0 and rc_b == 0
    assert store.requests == [
        ("plugin_a", "1.0", "release_a"),
        ("plugin_b", "2.0", "release_b"),
    ]
    assert captured["manifest"].asset_manifest_sha256 == manifest_b.asset_manifest_sha256
    assert captured["assets"] == {"w": asset_b}
    # Probe must NOT load the plugin runtime.
    assert handle_a.load_runtime_calls == []
    assert handle_b.load_runtime_calls == []


def test_cli_probe_rejects_wrong_manifest_identity(tmp_path, monkeypatch):
    manifest, asset = _manifest(tmp_path, content=b"w")
    _worker_env(monkeypatch, {f"plugin_a/1.0/{manifest.asset_manifest_sha256}": {"w": str(asset)}})
    _patch_remote_resolution(
        monkeypatch, {("plugin_a", "1.0"): _FakeHandle(_DEF_A)},
        _ProbeStore({("plugin_a", "1.0", "release_a"): _resolved_for(manifest, _DEF_A, "release_a")}),
    )
    with pytest.raises(PlatformError) as exc:
        runner_module._cli_probe(_probe_args(_DEF_A, "release_a", "e" * 64))
    assert exc.value.code in {"MODEL_RELEASE_MISMATCH", "PIPELINE_ASSET_MISMATCH"}


def test_cli_probe_release_less_fails_closed(tmp_path, monkeypatch):
    manifest, asset = _manifest(tmp_path, content=b"w")
    _worker_env(monkeypatch, {f"plugin_a/1.0/{manifest.asset_manifest_sha256}": {"w": str(asset)}})
    release_less = PipelineDefinition(
        id="plugin_a", name="A", version="1.0", label_space="spacenet_14",
        recommended_device="GPU", cpu_supported=False, stages=(), inspectable_stages=(),
        executors_supported=("remote_gpu",), recommended_executor="remote_gpu",
        model_release_required=False,
    )
    _patch_remote_resolution(
        monkeypatch, {("plugin_a", "1.0"): _FakeHandle(release_less)}, _ProbeStore({})
    )
    with pytest.raises(PlatformError) as exc:
        runner_module._cli_probe(_probe_args(release_less, None, manifest.asset_manifest_sha256))
    assert exc.value.code == "MODEL_RELEASE_MISMATCH"


# ---------------------------------------------------------------------------
# Control-plane probe: per-plugin/release manifest identity, one provider
# ---------------------------------------------------------------------------


class _EchoTransport:
    def __init__(self):
        self.calls = []

    def run_runner(self, subcommand, args=()):
        self.calls.append((subcommand, tuple(args)))
        tokens = dict(zip(args[::2], args[1::2]))
        stdout = json.dumps({
            "schema_version": 1,
            "status": "available",
            "remote_runtime_commit": RUNTIME_COMMIT,
            "asset_manifest_sha256": tokens["--asset-manifest-sha256"],
            "device": 0,
        }).encode()
        return SimpleNamespace(stdout=stdout)


class _FixedTransport:
    def __init__(self, manifest_sha):
        self.manifest_sha = manifest_sha

    def run_runner(self, subcommand, args=()):
        stdout = json.dumps({
            "schema_version": 1, "status": "available",
            "remote_runtime_commit": RUNTIME_COMMIT,
            "asset_manifest_sha256": self.manifest_sha, "device": 0,
        }).encode()
        return SimpleNamespace(stdout=stdout)


def _release(release_id, manifest_sha):
    return SimpleNamespace(
        release=SimpleNamespace(model_release_id=release_id),
        manifest=SimpleNamespace(asset_manifest_sha256=manifest_sha),
    )


def _profile():
    return RemoteProfile(
        name="autodl_primary", host="h", port=22, user="u",
        ssh_key_path=Path("/k"), known_hosts_path=Path("/kh"),
        remote_repo_root=Path("/repo"), remote_job_root=Path("/jobs"),
        remote_python_path=Path("/py"), required_remote_runtime_commit=RUNTIME_COMMIT,
        dataset_roots={"SpaceNet": Path("/sn")},
        generic_asset_paths={"plugin_a/1.0/" + "a" * 64: {"w": Path("/assets/w.pt")}},
    )


def test_two_release_bound_plugins_probe_through_one_adapter():
    transport = _EchoTransport()
    probe = SshRemoteExecutorProbe(_profile(), transport, expected_runtime_commit=RUNTIME_COMMIT)
    rec = SimpleNamespace(id="rec", label_space="spacenet_14", source_data_sha256="c" * 64)
    a = probe.availability(rec, _DEF_A, "c" * 64, _release("release_a", "a" * 64))
    b = probe.availability(rec, _DEF_B, "c" * 64, _release("release_b", "b" * 64))
    assert a.available is True and b.available is True
    tokens_a = dict(zip(transport.calls[0][1][::2], transport.calls[0][1][1::2]))
    tokens_b = dict(zip(transport.calls[1][1][::2], transport.calls[1][1][1::2]))
    assert tokens_a["--asset-manifest-sha256"] == "a" * 64
    assert tokens_b["--asset-manifest-sha256"] == "b" * 64
    assert tokens_a["--model-release-id"] == "release_a"
    assert tokens_b["--model-release-id"] == "release_b"


def test_wrong_manifest_identity_rejected():
    transport = _FixedTransport("e" * 64)
    probe = SshRemoteExecutorProbe(_profile(), transport, expected_runtime_commit=RUNTIME_COMMIT)
    rec = SimpleNamespace(id="rec", label_space="spacenet_14", source_data_sha256="c" * 64)
    availability = probe.availability(rec, _DEF_A, "c" * 64, _release("release_a", "a" * 64))
    assert availability.available is False
    assert availability.reason_code == "PIPELINE_ASSET_MISMATCH"


def test_no_global_single_manifest_startup_assumption():
    """The global M9.1 single-manifest probe wiring is retired; manifest identity
    is per-call through the resolved ModelRelease."""
    import inspect

    from app import main

    assert not hasattr(main, "_resolve_remote_expected_manifest_sha256")
    params = inspect.signature(SshRemoteExecutorProbe.__init__).parameters
    assert "expected_manifest_sha256" not in params
