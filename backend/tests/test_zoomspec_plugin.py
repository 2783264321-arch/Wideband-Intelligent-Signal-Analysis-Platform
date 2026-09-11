"""TASK E1 — ZoomSpec plugin runtime factory + registration.

Covers (a) the accepted E1-A CUDA device-index plumbing characterization and
(b) the E1-B ZoomSpec ``plugin.py`` declaration, lazy ``build_runtime`` factory,
declarative discovery, normalization/asset handling, and the full generic
``PluginItemExecutor`` -> ZoomSpec runtime path. No GPU / torch / ultralytics
scientific execution is performed.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from app.core.errors import PlatformError
from app.datasets.adapter import ResolvedRecordingInput
from app.labels.service import LabelSpaceService
from app.pipelines.base import PipelineOutput, RecordingInput
from app.pipelines.plugin_registry import create_plugin_registry
from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3 import pipeline as pipeline_module
from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.definition import (
    ZOOMSPEC_FROZEN_DEFINITION,
)
from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.frn import _is_cuda_device
from app.remote_execution.assets import (
    PipelineAssetManifest,
    compute_asset_manifest_sha256,
)
from app.remote_execution.plugin_executor import PluginItemExecutor
from app.remote_execution.request_builder import build_batch, freeze_request_provenance
from app.remote_execution.runtime import RuntimeDescriptor

PLUGIN_MODULE = "app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.plugin"
FACTORY_REF = PLUGIN_MODULE + ":build_runtime"
ZP_ID = "zoomspec_yolo26n_aug_combined_frn_v3"
ZP_VERSION = "1.0.0"
REPO_ROOT = Path(__file__).resolve().parents[2]
LABEL_ROOT = REPO_ROOT / "label_spaces"
RUNTIME_COMMIT = "68b1464842d0fb366fc211f53436d0ba49e3fbef"


def _label_space():
    return SimpleNamespace(
        id="spacenet_14",
        classes=[SimpleNamespace(id=i, name=f"class_{i}") for i in range(14)],
    )


def _recording():
    return SimpleNamespace(
        id="rec",
        data_path="ignored",
        data_format="float16_interleaved_le",
        sample_rate_hz=1_000_000.0,
        center_frequency_hz=2.4e9,
        frequency_low_hz=2.4e9,
        frequency_high_hz=2.41e9,
        duration_s=1.0,
        label_space="spacenet_14",
    )


# ---------------------------------------------------------------------------
# E1-A — LS-STFT/CPN/FRN exact device plumbing (accepted)
# ---------------------------------------------------------------------------


def _spy_pipeline(monkeypatch):
    captured: dict[str, object] = {}

    class _FakeDetector:
        def __init__(self, checkpoint_path, *, device):
            captured["detector_device"] = device

        def detect_batch(self, spectrograms, *, batch_size):
            return [[] for _ in spectrograms]

    class _FakeFRN:
        def __init__(self, checkpoint_path, *, device):
            captured["frn_device"] = device

        def refine_batch(self, candidates, **kwargs):
            return []

    def _fake_build_ls_stft(iq, *, sample_rate_hz, center_frequency_hz,
                            frequency_low_hz, frequency_high_hz, normalization, device):
        captured["preprocess_device"] = device
        return SimpleNamespace()

    monkeypatch.setattr(pipeline_module, "CPNDetector", _FakeDetector)
    monkeypatch.setattr(pipeline_module, "FRNRefiner", _FakeFRN)
    monkeypatch.setattr(pipeline_module, "build_ls_stft_spectrogram", _fake_build_ls_stft)
    monkeypatch.setattr(pipeline_module, "read_segment_from_path", lambda path, fmt: np.zeros(8, dtype=np.complex64))
    return captured


def _run(monkeypatch, device):
    captured = _spy_pipeline(monkeypatch)
    pipeline = pipeline_module.ZoomSpecFrozenPipeline(
        detector_checkpoint_path="det",
        frn_checkpoint_path="frn",
        normalization=object(),
        label_space=_label_space(),
        device=device,
    )
    pipeline.run(_recording(), {}, Path("/tmp/e1_ws"))
    return captured


def test_descriptor_index_3_propagates_exactly(monkeypatch):
    """RuntimeDescriptor.device_index=3 reaches LS-STFT, CPN and FRN as CUDA device 3."""
    captured = _run(monkeypatch, 3)
    assert captured["detector_device"] == 3
    assert captured["frn_device"] == 3
    assert captured["preprocess_device"] == "cuda:3"


def test_int_index_3_characterization_preprocessing_targets_device_3(monkeypatch):
    """Characterization: with int 3, CPN/FRN receive 3 and LS-STFT receives the
    explicit ``"cuda:3"`` device string."""
    captured = _run(monkeypatch, 3)
    assert captured["detector_device"] == 3
    assert captured["frn_device"] == 3
    assert captured["preprocess_device"] == "cuda:3"


def test_ls_stft_device_mapping():
    """Device plumbing mapping: int 0 -> 'cuda'; int N>0 -> 'cuda:N'; strings
    preserved unchanged. Non-scientific."""
    assert pipeline_module._ls_stft_device(0) == "cuda"
    assert pipeline_module._ls_stft_device(3) == "cuda:3"
    assert pipeline_module._ls_stft_device("cuda") == "cuda"
    assert pipeline_module._ls_stft_device("cuda:3") == "cuda:3"
    assert pipeline_module._ls_stft_device("cpu") == "cpu"


def test_int_frn_device_is_cuda():
    """Existing FRN int-device CUDA/autocast semantics are untouched."""
    assert _is_cuda_device(0) is True
    assert _is_cuda_device(3) is True


def test_int_index_0_preserves_accepted_m91_semantics(monkeypatch):
    """Index 0 already works: LS-STFT "cuda" == torch device 0, and CPN/FRN get 0."""
    captured = _run(monkeypatch, 0)
    assert captured["preprocess_device"] == "cuda"
    assert captured["detector_device"] == 0
    assert captured["frn_device"] == 0
    assert _is_cuda_device(0) is True


# ---------------------------------------------------------------------------
# E1-B — declaration + discovery
# ---------------------------------------------------------------------------


def test_plugin_declaration_module_exposes_frozen_definition():
    mod = importlib.import_module(PLUGIN_MODULE)
    assert mod.PLUGIN.definition is ZOOMSPEC_FROZEN_DEFINITION
    assert mod.PLUGIN.runtime_factory_ref == FACTORY_REF


def test_plugin_modules_json_registers_plugin_module_only():
    payload = json.loads(
        (REPO_ROOT / "backend" / "app" / "pipelines" / "plugin_modules.json").read_text()
    )
    modules = payload["modules"]
    assert PLUGIN_MODULE in modules
    assert "app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.definition" not in modules
    assert modules.count(PLUGIN_MODULE) == 1


def test_create_plugin_registry_discovers_e1_factory_once():
    registry = create_plugin_registry()
    handle = registry.get(ZP_ID, ZP_VERSION)
    assert handle.declaration.runtime_factory_ref == FACTORY_REF
    matches = [d for d in registry.declarations() if d.definition.id == ZP_ID]
    assert len(matches) == 1


def _subprocess(code: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "backend") + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)


def test_plugin_module_import_is_ml_free():
    code = (
        "import importlib, sys;"
        f"importlib.import_module('{PLUGIN_MODULE}');"
        "assert 'torch' not in sys.modules, 'torch imported';"
        "assert 'ultralytics' not in sys.modules, 'ultralytics imported'"
    )
    result = _subprocess(code)
    assert result.returncode == 0, result.stderr


def test_create_plugin_registry_is_ml_free():
    code = (
        "import sys; from app.pipelines.plugin_registry import create_plugin_registry;"
        "create_plugin_registry();"
        "assert 'torch' not in sys.modules, 'torch imported';"
        "assert 'ultralytics' not in sys.modules, 'ultralytics imported'"
    )
    result = _subprocess(code)
    assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# E1-B — build_runtime factory
# ---------------------------------------------------------------------------


def _descriptor(index: int = 0) -> RuntimeDescriptor:
    return RuntimeDescriptor(
        "remote_gpu", "cuda", index, "float16",
        environment_ref="deploy-ref", environment_label="autodl_primary",
    )


def _factory_assets(tmp_path: Path) -> dict:
    norm = tmp_path / "ls_stft_normalization.json"
    norm.write_text(json.dumps({
        "percentile_low": 1.0, "percentile_high": 99.0,
        "value_low": 0.0, "value_high": 1.0,
    }))
    return {
        "detector_checkpoint": tmp_path / "detector.pt",
        "frn_checkpoint": tmp_path / "frn.pt",
        "ls_stft_normalization": norm,
        "frozen_config": tmp_path / "frozen_config.json",
    }


def _patch_pipeline(monkeypatch):
    captured: dict = {}

    class _FakePipeline:
        def __init__(self, *, detector_checkpoint_path, frn_checkpoint_path,
                     normalization, label_space, device):
            captured.update(
                detector=detector_checkpoint_path, frn=frn_checkpoint_path,
                normalization=normalization, label_space=label_space, device=device,
            )
            self.runs = 0

        def run(self, recording, parameters, workspace):
            self.runs += 1
            captured["run"] = (recording, parameters, workspace)
            return PipelineOutput(detections=[])

    monkeypatch.setattr(pipeline_module, "ZoomSpecFrozenPipeline", _FakePipeline)
    return captured


@pytest.mark.parametrize("descriptor", [
    RuntimeDescriptor("local_cpu", "cpu", 0, "float32"),
    RuntimeDescriptor("remote_gpu", "cpu", 0, "float16"),
    RuntimeDescriptor("remote_gpu", "cuda", None, "float16"),
    RuntimeDescriptor("remote_gpu", "cuda", -1, "float16"),
    RuntimeDescriptor("remote_gpu", "cuda", 0, "float32"),
    None,
])
def test_build_runtime_requires_certified_descriptor(tmp_path, descriptor):
    zp = importlib.import_module(PLUGIN_MODULE)
    with pytest.raises(PlatformError) as exc:
        zp.build_runtime(
            assets=_factory_assets(tmp_path),
            runtime_descriptor=descriptor,
            output_label_space=_label_space(),
        )
    assert exc.value.code == "EXECUTOR_UNAVAILABLE"


@pytest.mark.parametrize("index", [0, 3])
def test_build_runtime_wires_assets_label_space_and_device(monkeypatch, tmp_path, index):
    zp = importlib.import_module(PLUGIN_MODULE)
    captured = _patch_pipeline(monkeypatch)
    assets = _factory_assets(tmp_path)
    label_space = _label_space()

    runtime = zp.build_runtime(
        assets=assets, runtime_descriptor=_descriptor(index),
        output_label_space=label_space,
    )

    assert captured["detector"] == assets["detector_checkpoint"]
    assert captured["frn"] == assets["frn_checkpoint"]
    assert captured["device"] == index
    assert captured["label_space"] is label_space
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.preprocessing import LSSTFTNormalization
    assert captured["normalization"] == LSSTFTNormalization(1.0, 99.0, 0.0, 1.0)

    recording = _recording()
    workspace = tmp_path / "ws"
    output = runtime.execute(recording, {}, workspace)
    assert isinstance(output, PipelineOutput)
    assert captured["run"] == (recording, {}, workspace)


def test_build_runtime_missing_required_asset_fails_closed(monkeypatch, tmp_path):
    zp = importlib.import_module(PLUGIN_MODULE)
    _patch_pipeline(monkeypatch)
    assets = _factory_assets(tmp_path)
    assets.pop("detector_checkpoint")
    with pytest.raises(PlatformError) as exc:
        zp.build_runtime(assets=assets, runtime_descriptor=_descriptor(0), output_label_space=_label_space())
    assert exc.value.code == "PIPELINE_ASSET_MISMATCH"


@pytest.mark.parametrize("bad_normalization", [
    "{not-json",
    json.dumps({"percentile_low": 1.0}),
    json.dumps({"percentile_low": "x", "percentile_high": 99.0, "value_low": 0.0, "value_high": 1.0}),
])
def test_build_runtime_bad_normalization_fails_closed(monkeypatch, tmp_path, bad_normalization):
    zp = importlib.import_module(PLUGIN_MODULE)
    _patch_pipeline(monkeypatch)
    assets = _factory_assets(tmp_path)
    assets["ls_stft_normalization"].write_text(bad_normalization)
    with pytest.raises(PlatformError) as exc:
        zp.build_runtime(assets=assets, runtime_descriptor=_descriptor(0), output_label_space=_label_space())
    assert exc.value.code == "PIPELINE_ASSET_MISMATCH"


def test_build_runtime_does_not_consume_frozen_config(monkeypatch, tmp_path):
    zp = importlib.import_module(PLUGIN_MODULE)
    _patch_pipeline(monkeypatch)
    assets = _factory_assets(tmp_path)
    assets.pop("frozen_config")
    runtime = zp.build_runtime(assets=assets, runtime_descriptor=_descriptor(0), output_label_space=_label_space())
    assert runtime is not None


def test_handle_load_runtime_resolves_the_real_e1_factory(monkeypatch, tmp_path):
    captured = _patch_pipeline(monkeypatch)
    handle = create_plugin_registry().get(ZP_ID, ZP_VERSION)
    runtime = handle.load_runtime(
        assets=_factory_assets(tmp_path), runtime_descriptor=_descriptor(3),
        output_label_space=_label_space(),
    )
    zp = importlib.import_module(PLUGIN_MODULE)
    assert isinstance(runtime, zp._ZoomSpecRuntime)
    assert captured["device"] == 3


def test_plugin_module_does_not_depend_on_legacy_executor():
    source = (REPO_ROOT / "backend" / "app" / "pipelines"
              / "zoomspec_yolo26n_aug_combined_frn_v3" / "plugin.py").read_text()
    assert "zoomspec_executor" not in source
    assert "ZoomSpecRemoteItemExecutor" not in source


# ---------------------------------------------------------------------------
# E1-B — full generic PluginItemExecutor -> ZoomSpec runtime path
# ---------------------------------------------------------------------------


def _write_assets(tmp_path: Path):
    blobs = {
        "detector_checkpoint": b"det-weights",
        "frn_checkpoint": b"frn-weights",
        "frozen_config": b"frozen-config",
        "ls_stft_normalization": json.dumps({
            "percentile_low": 1.0, "percentile_high": 99.0,
            "value_low": 0.0, "value_high": 1.0,
        }).encode("utf-8"),
    }
    paths: dict[str, Path] = {}
    hashes: dict[str, str] = {}
    for name, blob in blobs.items():
        path = tmp_path / name
        path.write_bytes(blob)
        paths[name] = path
        hashes[name] = hashlib.sha256(blob).hexdigest()
    provisional = PipelineAssetManifest(
        pipeline_id=ZP_ID, pipeline_version=ZP_VERSION,
        assets=hashes, asset_manifest_sha256="0" * 64,
    )
    manifest = PipelineAssetManifest(
        pipeline_id=ZP_ID, pipeline_version=ZP_VERSION,
        assets=hashes, asset_manifest_sha256=compute_asset_manifest_sha256(provisional),
    )
    return manifest, paths


class _Store:
    def __init__(self, resolved):
        self._resolved = resolved
        self.calls = []

    def resolve(self, plugin_id, plugin_version, requested):
        self.calls.append((plugin_id, plugin_version, requested))
        return self._resolved


class _Adapter:
    def resolve(self, *, split, key, label_space, expected_fingerprint,
                expected_source_hash, label_space_root):
        return ResolvedRecordingInput(
            recording_fingerprint=expected_fingerprint,
            source_data_sha256=expected_source_hash,
            recording_input=RecordingInput(
                id=key, data_path=Path("/deploy/rec.bin"),
                data_format="float16_interleaved_le",
                sample_rate_hz=1.0, center_frequency_hz=0.0,
                frequency_low_hz=0.0, frequency_high_hz=1.0,
                duration_s=1.0, label_space=label_space,
            ),
        )


class _AdapterRegistry:
    def get(self, dataset_name):
        return _Adapter()


class _Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)


def test_generic_executor_runs_zoomspec_through_e1_factory(monkeypatch, tmp_path):
    manifest, paths = _write_assets(tmp_path)
    captured = _patch_pipeline(monkeypatch)
    metadata = freeze_request_provenance(
        local_run_id="run_x", recording_fingerprint="2" * 64,
        source_data_sha256="1" * 64, dataset_name="SpaceNet", dataset_split="test",
        dataset_key="0", label_space="spacenet_14",
        pipeline_id=ZP_ID, pipeline_version=ZP_VERSION,
        required_remote_runtime_commit=RUNTIME_COMMIT, orchestrator_commit="a" * 40,
        asset_manifest_sha256=manifest.asset_manifest_sha256,
        remote_profile="autodl_primary", model_release_id="golden", parameters={},
    )
    batch = build_batch(metadata)
    descriptor = RuntimeDescriptor("remote_gpu", "cuda", 3, "float16")

    def _resolve_assets(*, plugin_id, plugin_version, asset_manifest_sha256, manifest):
        return {name: paths[name] for name in manifest.assets}

    worker = SimpleNamespace(
        required_runtime_commit=RUNTIME_COMMIT,
        label_space_root=LABEL_ROOT,
        resolve_assets=_resolve_assets,
        runtime_descriptor=lambda: descriptor,
    )
    resolved = SimpleNamespace(
        release=SimpleNamespace(model_release_id="golden"), manifest=manifest
    )
    publisher = _Recorder()
    executor = PluginItemExecutor(
        batch=batch, worker=worker, plugin_registry=create_plugin_registry(),
        adapter_registry=_AdapterRegistry(), model_release_store=_Store(resolved),
        certificate_store=object(), runtime_descriptor=descriptor,
        trusted_assets_resolver=worker.resolve_assets, package_publisher=publisher,
    )

    executor.execute(batch.items[0], tmp_path / "job")

    assert len(publisher.calls) == 1
    assert captured["device"] == 3
    assert captured["detector"] == paths["detector_checkpoint"]
    assert captured["frn"] == paths["frn_checkpoint"]
    assert captured["label_space"].id == "spacenet_14"
    assert isinstance(captured["label_space"], type(LabelSpaceService(LABEL_ROOT).get("spacenet_14")))
    assert captured["run"][1] == {}


# ---------------------------------------------------------------------------
# E3 — ZoomSpec local_cpu technical capability (NOT certified/runnable)
# ---------------------------------------------------------------------------

_CPU_RUNTIME_REF = "local:autodl_primary:cpu:a1237f8faae7"
_REMOTE_RUNTIME_REF = "remote:autodl_primary:5bb5be4b04d04a071bc9d8f4f61172595ecee037"


def _cpu_descriptor() -> RuntimeDescriptor:
    return RuntimeDescriptor(
        "local_cpu", "cpu", None, "float32",
        environment_ref="/usr/bin/python3", environment_label=_CPU_RUNTIME_REF,
    )


def test_definition_declares_remote_and_cpu_technical_capabilities():
    caps = {
        (c.executor, c.device_type, c.precision)
        for c in ZOOMSPEC_FROZEN_DEFINITION.technical_execution_capabilities
    }
    assert ("remote_gpu", "cuda", "float16") in caps
    assert ("local_cpu", "cpu", "float32") in caps
    assert ZOOMSPEC_FROZEN_DEFINITION.cpu_supported is False
    assert ZOOMSPEC_FROZEN_DEFINITION.recommended_execution == "remote_gpu"


def test_no_zoomspec_local_cpu_certificate_exists():
    certs = json.loads(
        (REPO_ROOT / "backend" / "app" / "pipelines" / "execution_certificates.json").read_text()
    )["certificates"]
    assert [
        c for c in certs
        if c["plugin_id"] == ZP_ID and c["plugin_version"] == ZP_VERSION and c["executor"] == "local_cpu"
    ] == []


def test_build_runtime_accepts_canonical_local_cpu_descriptor(monkeypatch, tmp_path):
    zp = importlib.import_module(PLUGIN_MODULE)
    captured = _patch_pipeline(monkeypatch)
    runtime = zp.build_runtime(
        assets=_factory_assets(tmp_path), runtime_descriptor=_cpu_descriptor(),
        output_label_space=_label_space(),
    )
    assert captured["device"] == "cpu"
    assert runtime is not None


def test_cpu_descriptor_reaches_ls_stft_cpn_frn_as_cpu(monkeypatch, tmp_path):
    """End-to-end CPU device propagation through the REAL frozen pipeline:
    local_cpu/cpu/float32 -> LS-STFT/CPN/FRN all target the CPU device."""
    zp = importlib.import_module(PLUGIN_MODULE)
    captured = _spy_pipeline(monkeypatch)
    runtime = zp.build_runtime(
        assets=_factory_assets(tmp_path), runtime_descriptor=_cpu_descriptor(),
        output_label_space=_label_space(),
    )
    runtime.execute(_recording(), {}, tmp_path / "ws")
    assert captured["preprocess_device"] == "cpu"
    assert captured["detector_device"] == "cpu"
    assert captured["frn_device"] == "cpu"


@pytest.mark.parametrize("bad", [
    RuntimeDescriptor("remote_gpu", "cpu", 0, "float32"),
    RuntimeDescriptor("local_cpu", "cuda", 0, "float16"),
    RuntimeDescriptor("local_cpu", "cpu", 0, "float32"),
    RuntimeDescriptor("local_cpu", "cpu", None, "float16"),
    RuntimeDescriptor("local_cpu", "cpu", -1, "float32"),
    RuntimeDescriptor("local_cpu", "cpu", True, "float32"),
    RuntimeDescriptor("remote_gpu", "cuda", True, "float16"),
    RuntimeDescriptor("unknown_executor", "cuda", 0, "float16"),
    None,
])
def test_build_runtime_rejects_crossed_or_invalid_descriptors(monkeypatch, tmp_path, bad):
    zp = importlib.import_module(PLUGIN_MODULE)
    _patch_pipeline(monkeypatch)
    with pytest.raises(PlatformError) as exc:
        zp.build_runtime(
            assets=_factory_assets(tmp_path), runtime_descriptor=bad,
            output_label_space=_label_space(),
        )
    assert exc.value.code == "EXECUTOR_UNAVAILABLE"


def test_local_worker_cpu_descriptor_matches_factory_contract(monkeypatch, tmp_path):
    from app.analysis.local_executor import LocalInferenceWorkerProvider

    provider = LocalInferenceWorkerProvider(
        interpreter=tmp_path / "py", runtime_ref=_CPU_RUNTIME_REF,
        work_root=tmp_path / "work", executor_kind="local_cpu",
    )
    desc = provider.runtime_descriptor()
    assert (desc.executor, desc.device_type, desc.device_index, desc.precision) == (
        "local_cpu", "cpu", None, "float32",
    )
    zp = importlib.import_module(PLUGIN_MODULE)
    captured = _patch_pipeline(monkeypatch)
    zp.build_runtime(
        assets=_factory_assets(tmp_path), runtime_descriptor=desc,
        output_label_space=_label_space(),
    )
    assert captured["device"] == "cpu"


class _StubProvider:
    def __init__(self, name, runtime_ref, descriptor, *, calls):
        self.name = name
        self._runtime_ref = runtime_ref
        self._descriptor = descriptor
        self._calls = calls

    @property
    def runtime_ref(self):
        return self._runtime_ref

    def runtime_descriptor(self):
        return self._descriptor

    def availability(self, *args, **kwargs):
        self._calls.append("availability")
        return SimpleNamespace(available=True)

    def launch(self, *args, **kwargs):
        return 1


def _real_cert_store():
    from app.remote_execution.runtime import (
        ExecutionCertificateStore,
        load_execution_certificates,
    )

    certs = load_execution_certificates(
        REPO_ROOT / "backend" / "app" / "pipelines" / "execution_certificates.json"
    )
    return ExecutionCertificateStore(certs)


def test_projection_excludes_zoomspec_local_cpu_without_certificate():
    from app.remote_execution.runtime import ExecutorRegistry

    calls: list = []
    remote = _StubProvider(
        "remote_gpu", _REMOTE_RUNTIME_REF,
        RuntimeDescriptor("remote_gpu", "cuda", 0, "float16"), calls=[],
    )
    local = _StubProvider("local_cpu", _CPU_RUNTIME_REF, _cpu_descriptor(), calls=calls)
    registry = ExecutorRegistry(
        {"remote_gpu": remote, "local_cpu": local}, _real_cert_store()
    )
    supported, recommended = registry.deployment_qualified_executors(
        ZOOMSPEC_FROZEN_DEFINITION, "golden"
    )
    assert "local_cpu" not in supported
    assert "remote_gpu" in supported
    assert recommended == "remote_gpu"
    assert calls == []  # configuration projection never probes


def test_availability_for_local_cpu_is_not_certified_before_probe():
    from app.remote_execution.runtime import ExecutorRegistry

    calls: list = []
    local = _StubProvider("local_cpu", _CPU_RUNTIME_REF, _cpu_descriptor(), calls=calls)
    registry = ExecutorRegistry({"local_cpu": local}, _real_cert_store())
    release = SimpleNamespace(
        release=SimpleNamespace(model_release_id="golden"),
        manifest=SimpleNamespace(asset_manifest_sha256="a" * 64),
    )
    recording = SimpleNamespace(id="r", label_space="spacenet_14", source_data_sha256="b" * 64)
    availability = registry.availability_for(
        ZOOMSPEC_FROZEN_DEFINITION, release, recording, "local_cpu"
    )
    assert availability.available is False
    assert availability.reason_code == "EXECUTION_NOT_CERTIFIED"
    assert calls == []  # live provider readiness is never reached without a certificate
