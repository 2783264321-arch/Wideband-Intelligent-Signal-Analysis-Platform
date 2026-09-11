"""TASK D3B — generic runner._cli_work cutover.

The production ``runner._cli_work`` must dispatch every registered remote-capable
plugin through ``PluginItemExecutor`` built from deployment-owned dependencies.
No ZoomSpec branch, no plugin-id literals. The same runner path must execute a
second, differently-implemented plugin without editing ``runner.py``.
"""
from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_plugin_executor import (
    _FakeHandle,
    _FakeRuntime,
    _manifest,
    _resolved,
    _valid,
)

from app.core.errors import PlatformError
from app.datasets.adapter import ResolvedRecordingInput
from app.pipelines.base import (
    DetectionPayload,
    ExecutionCapability,
    PipelineDefinition,
    PipelineOutput,
    RecordingInput,
)
from app.pipelines.plugin_registry import PluginRegistry
from app.remote_execution import runner as runner_module
from app.remote_execution.model_release import ResolvedModelRelease
from app.remote_execution.plugin_executor import PluginItemExecutor
from app.remote_execution.request_builder import build_batch, freeze_request_provenance
from app.remote_execution.runner import create_or_attach
from app.remote_execution.runtime import RuntimeDescriptor
from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.definition import (
    ZOOMSPEC_FROZEN_DEFINITION,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
LABEL_ROOT = REPO_ROOT / "label_spaces"
RUN = "a" * 40
RUNTIME_COMMIT = "68b1464842d0fb366fc211f53436d0ba49e3fbef"

_SECOND_DEFINITION = PipelineDefinition(
    id="second_remote_plugin",
    name="Second Remote Plugin",
    version="2.0",
    label_space="spacenet_14",
    recommended_device="GPU",
    cpu_supported=False,
    stages=(),
    inspectable_stages=(),
    task_capability="detection_classification",
    executors_supported=("remote_gpu",),
    recommended_executor="remote_gpu",
    input_compatibility=("spacenet_14",),
    model_release_required=True,
    technical_execution_capabilities=(ExecutionCapability("remote_gpu", "cuda", "float16"),),
)


def _batch_for(manifest, definition, *, release_id="golden"):
    metadata = freeze_request_provenance(
        local_run_id="run_x",
        recording_fingerprint="2" * 64,
        source_data_sha256="1" * 64,
        dataset_name="SpaceNet",
        dataset_split="test",
        dataset_key="0",
        label_space="spacenet_14",
        pipeline_id=definition.id,
        pipeline_version=definition.version,
        required_remote_runtime_commit=RUNTIME_COMMIT,
        orchestrator_commit=RUN,
        asset_manifest_sha256=manifest.asset_manifest_sha256,
        remote_profile="autodl_primary",
        model_release_id=release_id,
        parameters={},
    )
    return build_batch(metadata)


class _MultiRegistry:
    def __init__(self, handles):
        self._handles = {
            (h.definition.plugin_id, h.definition.plugin_version): h for h in handles
        }

    def get(self, plugin_id, plugin_version):
        return self._handles[(plugin_id, plugin_version)]


class _Store:
    def __init__(self, by_key):
        self._by_key = by_key
        self.calls = []

    def resolve(self, plugin_id, plugin_version, requested):
        self.calls.append((plugin_id, plugin_version, requested))
        return self._by_key[(plugin_id, plugin_version)]


class _Adapter:
    def __init__(self):
        self.calls = []

    def resolve(self, *, split, key, label_space, expected_fingerprint,
                expected_source_hash, label_space_root):
        self.calls.append((split, key, label_space))
        return ResolvedRecordingInput(
            recording_fingerprint=expected_fingerprint,
            source_data_sha256=expected_source_hash,
            recording_input=RecordingInput(
                id=key,
                data_path=Path("/deploy/data/rec.bin"),
                data_format="float16_interleaved_le",
                sample_rate_hz=1.0,
                center_frequency_hz=0.0,
                frequency_low_hz=0.0,
                frequency_high_hz=1.0,
                duration_s=1.0,
                label_space=label_space,
            ),
        )


class _AdapterRegistry:
    def __init__(self, adapter):
        self.adapter = adapter

    def get(self, dataset_name):
        return self.adapter


def _apply_worker_env(monkeypatch, namespaced_json):
    env = {
        "WSP_REMOTE_REPO_ROOT": str(REPO_ROOT),
        "WSP_REMOTE_JOB_ROOT": "/root/jobs",
        "WSP_REMOTE_REQUIRED_RUNTIME_COMMIT": RUNTIME_COMMIT,
        "WSP_REMOTE_SPACENET_ROOT": "/root/autodl-tmp/SpaceNet_Dataset",
        "WSP_REMOTE_DETECTOR_CHECKPOINT": "/root/models/det.pt",
        "WSP_REMOTE_FRN_CHECKPOINT": "/root/models/frn.pt",
        "WSP_REMOTE_FROZEN_CONFIG": "/root/models/frozen.json",
        "WSP_REMOTE_LS_STFT_NORMALIZATION": "/root/models/norm.json",
        "WSP_REMOTE_ASSET_PATHS_JSON": namespaced_json,
    }
    for name, value in env.items():
        monkeypatch.setenv(name, value)


def _patch_dependencies(monkeypatch, registry, store, adapter_registry):
    from app.datasets import adapter as adapter_module
    from app.pipelines import plugin_registry as registry_module

    monkeypatch.setattr(registry_module, "create_plugin_registry", lambda: registry)
    monkeypatch.setattr(runner_module, "_build_model_release_store", lambda: store)
    monkeypatch.setattr(runner_module, "_build_certificate_store", lambda: object())
    monkeypatch.setattr(
        adapter_module, "create_dataset_adapter_registry", lambda adapters: adapter_registry
    )


def _assert_plugin_executed(tmp_path, batch, handle, definition, class_id, class_name):
    job_root = tmp_path / "jobs" / batch.batch_id
    job_root.parent.mkdir(parents=True, exist_ok=True)
    create_or_attach(batch, job_root)
    rc = runner_module._cli_work(
        SimpleNamespace(batch_id=batch.batch_id, job_root=str(job_root))
    )
    assert rc == 0
    assert len(handle.load_runtime_calls) == 1
    call = handle.load_runtime_calls[0]
    assert call["runtime_descriptor"] == handle_worker_descriptor()
    assert call["output_label_space"].id == definition.resolved_output_label_space
    terminal = job_root / "results" / batch.items[0].item_key
    import json
    import zipfile

    with zipfile.ZipFile(terminal / "analysis_result.zip") as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["pipeline"]["id"] == definition.id
    assert manifest["execution"]["executor"] == "remote_gpu"
    return terminal


def handle_worker_descriptor():
    # The real RemoteWorkerContext.from_env default descriptor (cuda:0/float16).
    return RuntimeDescriptor("remote_gpu", "cuda", 0, "float16")


# ---------------------------------------------------------------------------
# A1/A2 — _cli_work constructs the generic executor (no ZoomSpec dispatch)
# ---------------------------------------------------------------------------


def test_cli_work_builds_generic_plugin_executor(tmp_path, monkeypatch):
    manifest, asset = _manifest(tmp_path)
    batch = _batch_for(manifest, ZOOMSPEC_FROZEN_DEFINITION)
    handle = _FakeHandle(ZOOMSPEC_FROZEN_DEFINITION)
    adapter = _Adapter()
    _apply_worker_env(
        monkeypatch,
        '{"%s/1.0.0/%s": {"w": %s}}'
        % (ZOOMSPEC_FROZEN_DEFINITION.id, manifest.asset_manifest_sha256, '"%s"' % asset),
    )
    _patch_dependencies(
        monkeypatch, _MultiRegistry([handle]),
        _Store({(ZOOMSPEC_FROZEN_DEFINITION.id, ZOOMSPEC_FROZEN_DEFINITION.version): _resolved(manifest)}),
        _AdapterRegistry(adapter),
    )
    job_root = tmp_path / "jobs" / batch.batch_id
    job_root.parent.mkdir(parents=True, exist_ok=True)
    create_or_attach(batch, job_root)
    captured = {}
    monkeypatch.setattr(
        runner_module, "run_work",
        lambda batch_id, root, item_executor: captured.update(executor=item_executor),
    )
    rc = runner_module._cli_work(
        SimpleNamespace(batch_id=batch.batch_id, job_root=str(job_root))
    )
    assert rc == 0
    executor = captured["executor"]
    assert isinstance(executor, PluginItemExecutor)
    from app.remote_execution.package_publisher import publish_package

    assert executor._batch == batch
    assert executor._package_publisher is publish_package


def test_cli_work_has_no_zoomspec_literals():
    source = inspect.getsource(runner_module._cli_work)
    for forbidden in (
        "ZoomSpecRemoteItemExecutor", "zoomspec", "spacenet_14",
        "detector_checkpoint", "frn_checkpoint", "ls_stft_normalization", "cuda:0",
    ):
        assert forbidden not in source, f"runner._cli_work still references '{forbidden}'"


def test_runner_module_has_no_zoomspec_executor_literals():
    source = (REPO_ROOT / "backend" / "app" / "remote_execution" / "runner.py").read_text()
    for forbidden in (
        "ZoomSpecRemoteItemExecutor", "zoomspec_executor", "spacenet_14",
        "detector_checkpoint", "frn_checkpoint", "ls_stft_normalization", "cuda:0",
    ):
        assert forbidden not in source, f"runner.py still references '{forbidden}'"


# ---------------------------------------------------------------------------
# A3/A4 — ZoomSpec + a second plugin through the SAME production runner path
# ---------------------------------------------------------------------------


def test_zoom_spec_and_second_plugin_execute_through_same_runner_path(tmp_path, monkeypatch):
    manifest_a, asset_a = _manifest(tmp_path / "a", content=b"weights-a")
    manifest_b, asset_b = _manifest(tmp_path / "b", content=b"weights-b")

    zoom_handle = _FakeHandle(ZOOMSPEC_FROZEN_DEFINITION)
    zoom_handle.runtime = _FakeRuntime(PipelineOutput(detections=[
        DetectionPayload(0.01, 0.02, 2440600000.0, 2440700000.0, 9, "LoRa 250kHz", 0.9, None),
    ]))
    second_handle = _FakeHandle(_SECOND_DEFINITION)
    second_handle.runtime = _FakeRuntime(PipelineOutput(detections=[
        DetectionPayload(0.03, 0.04, 2440800000.0, 2440900000.0, 6, "BLE LE1M", 0.8, None),
    ]))

    registry = _MultiRegistry([zoom_handle, second_handle])
    store = _Store({
        (ZOOMSPEC_FROZEN_DEFINITION.id, ZOOMSPEC_FROZEN_DEFINITION.version): _resolved(manifest_a),
        (_SECOND_DEFINITION.id, _SECOND_DEFINITION.version): _resolved(manifest_b),
    })
    adapter = _Adapter()
    _apply_worker_env(
        monkeypatch,
        '{"%s/1.0.0/%s": {"w": "%s"}, "%s/2.0/%s": {"w": "%s"}}'
        % (
            ZOOMSPEC_FROZEN_DEFINITION.id, manifest_a.asset_manifest_sha256, asset_a,
            _SECOND_DEFINITION.id, manifest_b.asset_manifest_sha256, asset_b,
        ),
    )
    _patch_dependencies(monkeypatch, registry, store, _AdapterRegistry(adapter))

    batch_a = _batch_for(manifest_a, ZOOMSPEC_FROZEN_DEFINITION)
    batch_b = _batch_for(manifest_b, _SECOND_DEFINITION)
    _assert_plugin_executed(tmp_path, batch_a, zoom_handle, ZOOMSPEC_FROZEN_DEFINITION, 9, "LoRa 250kHz")
    _assert_plugin_executed(tmp_path, batch_b, second_handle, _SECOND_DEFINITION, 6, "BLE LE1M")

    # Same runner path, no plugin-name branching: both handles reached their own
    # runtime exactly once with distinct assets.
    assert len(zoom_handle.load_runtime_calls) == 1
    assert len(second_handle.load_runtime_calls) == 1
    assert zoom_handle.load_runtime_calls[0]["assets"] == {"w": asset_a}
    assert second_handle.load_runtime_calls[0]["assets"] == {"w": asset_b}


# ---------------------------------------------------------------------------
# B — item must belong to the loaded frozen batch
# ---------------------------------------------------------------------------


def test_executor_rejects_item_not_in_frozen_batch(tmp_path):
    executor, batch, handle, adapter, resolver, publisher, manifest, resolved = _valid(tmp_path)
    mismatch = batch.items[0].model_copy(update={"request_id": "other_request"})
    with pytest.raises(PlatformError) as exc:
        executor.execute(mismatch, tmp_path / "job")
    assert exc.value.code == "REMOTE_REQUEST_INVALID"
    assert publisher.calls == []
    assert handle.load_runtime_calls == []


def test_executor_rejects_unknown_item_key(tmp_path):
    executor, batch, *_ = _valid(tmp_path)
    foreign = batch.items[0].model_copy(update={"item_key": "999999"})
    with pytest.raises(PlatformError) as exc:
        executor.execute(foreign, tmp_path / "job")
    assert exc.value.code == "REMOTE_REQUEST_INVALID"


# ---------------------------------------------------------------------------
# E — generic-only remote preflight conclusion (legacy transport blocker)
# ---------------------------------------------------------------------------


def test_generic_only_remote_preflight_is_blocked_by_legacy_assets(tmp_path):
    """Documents D3B_BLOCKED_BY_LEGACY_TRANSPORT_PREFLIGHT: a generic-only profile
    (no flat legacy asset subset) cannot pass SshRunner preflight."""
    import dataclasses

    from app.remote_execution.profile import RemoteProfile
    from app.remote_execution.transport import RemoteTransportError, SshRunner

    key = tmp_path / "k"
    key.write_bytes(b"k")
    hosts = tmp_path / "h"
    hosts.write_bytes(b"h")
    namespace = "%s/1.0.0/%s" % (ZOOMSPEC_FROZEN_DEFINITION.id, "b" * 64)
    generic_only = RemoteProfile(
        name="autodl_primary", host="h", port=22, user="root",
        ssh_key_path=key, known_hosts_path=hosts,
        remote_repo_root=__import__("pathlib").PurePosixPath("/root/repo"),
        remote_job_root=__import__("pathlib").PurePosixPath("/root/jobs"),
        remote_python_path=__import__("pathlib").PurePosixPath("/opt/w/bin/python"),
        required_remote_runtime_commit=RUNTIME_COMMIT,
        dataset_roots={"SpaceNet": __import__("pathlib").PurePosixPath("/root/spacenet")},
        asset_paths={},
        manifest_root=__import__("pathlib").PurePosixPath("/root/manifests"),
        generic_asset_paths={namespace: {"w": __import__("pathlib").PurePosixPath("/root/a/w.pt")}},
    )
    with pytest.raises(RemoteTransportError):
        SshRunner(generic_only).validate_runner_environment("work")
