"""TASK D3A — generic remote PluginItemExecutor core (injected seams, no ZoomSpec)."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from app.core.errors import PlatformError
from app.datasets.adapter import ResolvedRecordingInput
from app.labels.service import LabelSpaceService
from app.pipelines.base import PipelineDefinition, PipelineOutput, RecordingInput
from app.remote_execution.assets import PipelineAssetManifest, compute_asset_manifest_sha256
from app.remote_execution.model_release import ModelRelease, ResolvedModelRelease
from app.remote_execution.plugin_executor import PluginItemExecutor
from app.remote_execution.request_builder import build_batch, freeze_request_provenance
from app.remote_execution.runtime import RuntimeDescriptor

REPO_ROOT = Path(__file__).resolve().parents[2]
LABEL_ROOT = REPO_ROOT / "label_spaces"
PLUGIN_ID = "generic_plugin"
PLUGIN_VERSION = "1.0"
RUN = "a" * 40
DESCRIPTOR = RuntimeDescriptor(
    executor="remote_gpu",
    device_type="cuda",
    device_index=0,
    precision="float16",
    environment_ref="autodl_primary",
    environment_label="remote:autodl_primary:" + RUN,
)
_PARAM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"threshold": {"type": "number"}},
}


def _definition(*, label_space="spacenet_14", schema=None) -> PipelineDefinition:
    return PipelineDefinition(
        id=PLUGIN_ID,
        name="Generic",
        version=PLUGIN_VERSION,
        label_space=label_space,
        recommended_device="GPU",
        cpu_supported=False,
        stages=(),
        inspectable_stages=(),
        task_capability="detection_classification",
        executors_supported=("remote_gpu",),
        recommended_executor="remote_gpu",
        model_release_required=True,
        parameter_schema=schema if schema is not None else {},
    )


def _manifest(tmp_path: Path, *, content: bytes = b"weights") -> tuple[PipelineAssetManifest, Path]:
    asset = tmp_path / "weights.bin"
    asset.parent.mkdir(parents=True, exist_ok=True)
    asset.write_bytes(content)
    provisional = PipelineAssetManifest(
        pipeline_id=PLUGIN_ID,
        pipeline_version=PLUGIN_VERSION,
        assets={"w": sha256(content).hexdigest()},
        asset_manifest_sha256="0" * 64,
    )
    manifest = replace(provisional, asset_manifest_sha256=compute_asset_manifest_sha256(provisional))
    return manifest, asset


def _resolved(manifest: PipelineAssetManifest) -> ResolvedModelRelease:
    release = ModelRelease(
        plugin_id=PLUGIN_ID,
        plugin_version=PLUGIN_VERSION,
        model_release_id="golden",
        asset_manifest_path=Path("/deploy/asset_manifest.json"),
        asset_manifest_sha256=manifest.asset_manifest_sha256,
    )
    return ResolvedModelRelease(release=release, manifest=manifest)


def _batch(manifest: PipelineAssetManifest, *, release_id="golden", commit=RUN, parameters=None):
    metadata = freeze_request_provenance(
        local_run_id="run_x",
        recording_fingerprint="2" * 64,
        source_data_sha256="1" * 64,
        dataset_name="SpaceNet",
        dataset_split="test",
        dataset_key="0",
        label_space="spacenet_14",
        pipeline_id=PLUGIN_ID,
        pipeline_version=PLUGIN_VERSION,
        required_remote_runtime_commit=commit,
        orchestrator_commit=RUN,
        asset_manifest_sha256=manifest.asset_manifest_sha256,
        remote_profile="autodl_primary",
        model_release_id=release_id,
        parameters=parameters or {},
    )
    return build_batch(metadata)


class _Recorder:
    def __init__(self, result=None):
        self.calls = []
        self.result = result

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


class _FakeRuntime:
    def __init__(self, output=None):
        self.calls = []
        self.output = output or PipelineOutput(detections=[])

    def execute(self, recording, parameters, workspace):
        self.calls.append((recording, parameters, workspace))
        return self.output


class _FakeHandle:
    def __init__(self, definition):
        self.definition = definition
        self.load_runtime_calls = []
        self.runtime = _FakeRuntime()

    def load_runtime(self, *, assets, runtime_descriptor, output_label_space):
        self.load_runtime_calls.append(
            {
                "assets": assets,
                "runtime_descriptor": runtime_descriptor,
                "output_label_space": output_label_space,
            }
        )
        return self.runtime


class _FakeRegistry:
    def __init__(self, handle):
        self.handle = handle

    def get(self, plugin_id, plugin_version):
        assert (plugin_id, plugin_version) == (self.handle.definition.plugin_id, self.handle.definition.plugin_version)
        return self.handle


class _FakeAdapter:
    def __init__(self):
        self.calls = []

    def resolve(self, *, split, key, label_space, expected_fingerprint, expected_source_hash, label_space_root):
        self.calls.append(
            {
                "split": split,
                "key": key,
                "label_space": label_space,
                "expected_fingerprint": expected_fingerprint,
                "expected_source_hash": expected_source_hash,
                "label_space_root": label_space_root,
            }
        )
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


class _FakeAdapterRegistry:
    def __init__(self, adapter):
        self.adapter = adapter

    def get(self, dataset_name):
        assert dataset_name == "SpaceNet"
        return self.adapter


class _FakeStore:
    def __init__(self, resolved):
        self.resolved = resolved
        self.calls = []

    def resolve(self, plugin_id, plugin_version, requested):
        self.calls.append((plugin_id, plugin_version, requested))
        return self.resolved


def _executor(*, batch, definition, store, resolver, publisher, worker=None, adapter=None):
    handle = _FakeHandle(definition)
    adapter = adapter or _FakeAdapter()
    executor = PluginItemExecutor(
        batch=batch,
        worker=worker or SimpleNamespace(required_runtime_commit=RUN, label_space_root=LABEL_ROOT),
        plugin_registry=_FakeRegistry(handle),
        adapter_registry=_FakeAdapterRegistry(adapter),
        model_release_store=store,
        certificate_store=object(),
        runtime_descriptor=DESCRIPTOR,
        trusted_assets_resolver=resolver,
        package_publisher=publisher,
    )
    return executor, handle, adapter


def _valid(tmp_path):
    manifest, asset = _manifest(tmp_path)
    resolved = _resolved(manifest)
    batch = _batch(manifest)
    definition = _definition()
    resolver = _Recorder({"w": asset})
    publisher = _Recorder()
    executor, handle, adapter = _executor(
        batch=batch, definition=definition, store=_FakeStore(resolved),
        resolver=resolver, publisher=publisher,
    )
    return executor, batch, handle, adapter, resolver, publisher, manifest, resolved


def test_executor_calls_load_runtime_with_exact_kwargs(tmp_path):
    executor, batch, handle, adapter, resolver, publisher, manifest, resolved = _valid(tmp_path)
    executor.execute(batch.items[0], tmp_path / "job")

    assert len(handle.load_runtime_calls) == 1
    call = handle.load_runtime_calls[0]
    assert call["assets"] == {"w": tmp_path / "weights.bin"}
    assert call["runtime_descriptor"] == DESCRIPTOR
    assert call["output_label_space"].id == "spacenet_14"
    assert len(handle.runtime.calls) == 1
    recording_input, parameters, workspace = handle.runtime.calls[0]
    assert recording_input.id == "0"
    assert parameters == {}
    assert workspace == tmp_path / "job" / "work" / batch.items[0].item_key

    # trusted-assets + package seams invoked with generic context
    assert resolver.calls[0]["plugin_id"] == PLUGIN_ID
    assert resolver.calls[0]["plugin_version"] == PLUGIN_VERSION
    assert resolver.calls[0]["asset_manifest_sha256"] == manifest.asset_manifest_sha256
    assert len(publisher.calls) == 1
    pkg = publisher.calls[0]
    assert pkg["pipeline_definition"] is handle.definition
    assert pkg["runtime_descriptor"] == DESCRIPTOR
    assert pkg["item"] is batch.items[0]


def test_executor_verifies_batch_runtime_commit(tmp_path):
    manifest, asset = _manifest(tmp_path)
    resolved = _resolved(manifest)
    batch = _batch(manifest, commit="b" * 40)
    resolver, publisher = _Recorder({"w": asset}), _Recorder()
    executor, handle, _ = _executor(
        batch=batch, definition=_definition(), store=_FakeStore(resolved),
        resolver=resolver, publisher=publisher,
        worker=SimpleNamespace(required_runtime_commit=RUN, label_space_root=LABEL_ROOT),
    )
    with pytest.raises(PlatformError) as exc:
        executor.execute(batch.items[0], tmp_path / "job")
    assert exc.value.code == "REMOTE_IMPLEMENTATION_MISMATCH"
    assert handle.load_runtime_calls == []


def test_executor_validates_request_sha256(tmp_path):
    executor, batch, handle, *_ = _valid(tmp_path)
    tampered = batch.model_copy(update={"request_sha256": "9" * 64})
    executor._batch = tampered
    with pytest.raises(PlatformError) as exc:
        executor.execute(tampered.items[0], tmp_path / "job")
    assert exc.value.code == "REMOTE_REQUEST_INVALID"
    assert handle.load_runtime_calls == []


def test_executor_rejects_missing_wire_release_id(tmp_path):
    manifest, asset = _manifest(tmp_path)
    batch = _batch(manifest, release_id=None)
    store = _FakeStore(_resolved(manifest))
    executor, handle, _ = _executor(
        batch=batch, definition=_definition(), store=store,
        resolver=_Recorder({"w": asset}), publisher=_Recorder(),
    )
    with pytest.raises(PlatformError) as exc:
        executor.execute(batch.items[0], tmp_path / "job")
    assert exc.value.code == "MODEL_RELEASE_MISMATCH"
    assert store.calls == []
    assert handle.load_runtime_calls == []


def test_executor_fails_closed_on_release_mismatch(tmp_path):
    manifest, asset = _manifest(tmp_path)
    batch = _batch(manifest, release_id="golden")
    wrong = _resolved(manifest)
    wrong = ResolvedModelRelease(
        release=replace(wrong.release, model_release_id="tuned"), manifest=wrong.manifest
    )
    executor, handle, _ = _executor(
        batch=batch, definition=_definition(), store=_FakeStore(wrong),
        resolver=_Recorder({"w": asset}), publisher=_Recorder(),
    )
    with pytest.raises(PlatformError) as exc:
        executor.execute(batch.items[0], tmp_path / "job")
    assert exc.value.code == "MODEL_RELEASE_MISMATCH"
    assert handle.load_runtime_calls == []


def test_executor_fails_closed_on_manifest_mismatch(tmp_path):
    manifest, asset = _manifest(tmp_path)
    batch = _batch(manifest)
    other_manifest, _ = _manifest(tmp_path / "other", content=b"other-weights")
    executor, handle, _ = _executor(
        batch=batch, definition=_definition(), store=_FakeStore(_resolved(other_manifest)),
        resolver=_Recorder({"w": asset}), publisher=_Recorder(),
    )
    with pytest.raises(PlatformError) as exc:
        executor.execute(batch.items[0], tmp_path / "job")
    assert exc.value.code == "MODEL_RELEASE_MISMATCH"
    assert handle.load_runtime_calls == []


def test_executor_fails_closed_on_asset_mismatch(tmp_path):
    manifest, asset = _manifest(tmp_path)
    batch = _batch(manifest)
    bad_asset = tmp_path / "bad.bin"
    bad_asset.write_bytes(b"tampered")
    executor, handle, _ = _executor(
        batch=batch, definition=_definition(), store=_FakeStore(_resolved(manifest)),
        resolver=_Recorder({"w": bad_asset}), publisher=_Recorder(),
    )
    with pytest.raises(PlatformError) as exc:
        executor.execute(batch.items[0], tmp_path / "job")
    assert exc.value.code == "PIPELINE_ASSET_MISMATCH"
    assert handle.load_runtime_calls == []


def test_executor_invalid_parameters_fail_before_runtime(tmp_path):
    manifest, asset = _manifest(tmp_path)
    batch = _batch(manifest, parameters={"threshold": "not-a-number"})
    executor, handle, _ = _executor(
        batch=batch, definition=_definition(schema=_PARAM_SCHEMA),
        store=_FakeStore(_resolved(manifest)), resolver=_Recorder({"w": asset}), publisher=_Recorder(),
    )
    with pytest.raises(PlatformError) as exc:
        executor.execute(batch.items[0], tmp_path / "job")
    assert exc.value.code == "PLUGIN_PARAMETERS_INVALID"
    assert handle.load_runtime_calls == []


def test_executor_resolves_recording_only_through_adapter(tmp_path):
    executor, batch, handle, adapter, *_ = _valid(tmp_path)
    executor.execute(batch.items[0], tmp_path / "job")
    assert len(adapter.calls) == 1
    call = adapter.calls[0]
    assert call["split"] == "test"
    assert call["key"] == "0"
    assert call["expected_fingerprint"] == "2" * 64
    assert call["expected_source_hash"] == "1" * 64
    assert call["label_space_root"] == LABEL_ROOT
    # GroundTruth never reaches inference: the RecordingInput carries no GT fields.
    recording_input = handle.runtime.calls[0][0]
    assert not hasattr(recording_input, "ground_truth")
    assert not hasattr(recording_input, "signals")


def test_plugin_executor_module_import_is_torch_free():
    code = (
        "import sys; import app.remote_execution.plugin_executor; "
        "assert 'torch' not in sys.modules, 'torch leaked'; "
        "assert 'ultralytics' not in sys.modules, 'ultralytics leaked'; "
        "print('OK')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO_ROOT / "backend",
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
