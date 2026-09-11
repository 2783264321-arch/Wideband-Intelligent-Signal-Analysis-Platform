"""M9.2 F-A1 — second real plugin genericity proof: CPN bandwidth tier.

This suite proves that ``cpn_bandwidth_tier/1.0.0`` is discovered, described,
versioned, asset-resolved, and runtime-loadable through the *existing* generic
platform seams (PluginRegistry / PipelineRegistry / LabelSpaceService /
ModelReleaseStore / PluginHandle / PluginItemExecutor) without any CPN-specific
branch in platform core.

The scientific composition reuses the accepted frozen LS-STFT + CPN detector and
STOPS at ``DetectionPayload``: no AHLP, no FRN, no ZoomSpec 14-class refinement.

No training / GPU / torch / ultralytics are required (the detector construction
is monkeypatched in unit tests).
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

import pytest

from app.core.errors import PlatformError
from app.datasets.adapter import ResolvedRecordingInput
from app.evaluation.capability import classification_applicability
from app.labels.service import LabelSpaceService
from app.pipelines.base import PipelineOutput, RecordingInput
from app.pipelines.compatibility import is_input_compatible
from app.pipelines.plugin_registry import create_plugin_registry
from app.pipelines.registry import create_pipeline_registry
from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.detector import CPNProposal
from app.remote_execution.assets import (
    PipelineAssetManifest,
    compute_asset_manifest_sha256,
    load_pipeline_asset_manifest,
)
from app.remote_execution.model_release import (
    ModelReleaseStore,
    load_model_release_defaults,
)
from app.remote_execution.plugin_executor import PluginItemExecutor
from app.remote_execution.request_builder import build_batch, freeze_request_provenance
from app.remote_execution.runtime import RuntimeDescriptor

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
PLUGINS_ROOT = BACKEND_ROOT / "app" / "pipelines"
LABEL_ROOT = REPO_ROOT / "label_spaces"

PLUGIN_MODULE = "app.pipelines.cpn_bandwidth_tier.plugin"
FACTORY_REF = PLUGIN_MODULE + ":build_runtime"
CPN_ID = "cpn_bandwidth_tier"
CPN_VERSION = "1.0.0"
CPN_LABEL_SPACE = "cpn_bandwidth_tier_v1"

DETECTOR_SHA = "eba4fa4b112a0e61cc1013e96f99d1ae82b845f4be1e8b1f80bd2089d1f82311"
NORMALIZATION_SHA = "9b994655a279352b835b96cb00cefde89410fc6130458665dca7070de146d72f"
CPN_MANIFEST_SHA = "7ab8a6a4f5f93247d3997fcf88c4b05d1099361fa8db1555fc8daeeaf7fc55bb"
ZOOMSPEC_MANIFEST_SHA = "16cc0534ed61603a84142da8a04af6642f9e7661848835fe5473199bec38ac08"
RUN = "a" * 40

CPN_PROPOSALS = [
    CPNProposal(
        t_start_s=0.10,
        t_end_s=0.20,
        f_low_hz=2_440_100_000.0,
        f_high_hz=2_440_300_000.0,
        bandwidth_tier=1,
        confidence=0.42,
    ),
    CPNProposal(
        t_start_s=0.30,
        t_end_s=0.55,
        f_low_hz=2_440_600_000.0,
        f_high_hz=2_440_700_000.0,
        bandwidth_tier=2,
        confidence=0.77,
    ),
]


def _cpn_pipeline_module():
    return importlib.import_module("app.pipelines.cpn_bandwidth_tier.pipeline")


def _cpn_plugin_module():
    return importlib.import_module(PLUGIN_MODULE)


def _label_space():
    return LabelSpaceService(LABEL_ROOT).get(CPN_LABEL_SPACE)


def _cpu_descriptor() -> RuntimeDescriptor:
    return RuntimeDescriptor("local_cpu", "cpu", None, "float32")


def _remote_descriptor() -> RuntimeDescriptor:
    return RuntimeDescriptor("remote_gpu", "cuda", 0, "float16")


def _recording() -> RecordingInput:
    return RecordingInput(
        id="rec",
        data_path=Path("/deploy/rec.bin"),
        data_format="float16_interleaved_le",
        sample_rate_hz=1_000_000.0,
        center_frequency_hz=2_440_000_000.0,
        frequency_low_hz=2_440_000_000.0,
        frequency_high_hz=2_441_000_000.0,
        duration_s=1.0,
        label_space="spacenet_14",
    )


def _write_normalization(tmp_path: Path) -> Path:
    path = tmp_path / "ls_stft_normalization.json"
    path.write_text(
        json.dumps(
            {
                "percentile_low": 1.0,
                "percentile_high": 99.0,
                "value_low": 0.0,
                "value_high": 1.0,
            }
        ),
        encoding="utf-8",
    )
    return path


def _patch_science(monkeypatch, proposals):
    """Patch the scientific seams of the CPN pipeline (no torch/ultralytics)."""
    module = _cpn_pipeline_module()
    captured: dict[str, object] = {}

    class _FakeDetector:
        def __init__(self, checkpoint_path, *, device):
            captured["detector_checkpoint"] = checkpoint_path
            captured["detector_device"] = device

        def detect_batch(self, spectrograms, *, batch_size):
            return [list(proposals) for _ in spectrograms]

    def _fake_build_ls_stft(iq, *, sample_rate_hz, center_frequency_hz,
                            frequency_low_hz, frequency_high_hz, normalization, device):
        captured["preprocess_device"] = device
        return SimpleNamespace()

    monkeypatch.setattr(module, "CPNDetector", _FakeDetector)
    monkeypatch.setattr(module, "build_ls_stft_spectrogram", _fake_build_ls_stft)
    monkeypatch.setattr(
        module, "read_segment_from_path",
        lambda path, fmt: __import__("numpy").zeros(8, dtype="complex64"),
    )
    return captured


# ---------------------------------------------------------------------------
# A. Plugin discovery
# ---------------------------------------------------------------------------


def test_plugin_registry_discovers_cpn_identity():
    registry = create_plugin_registry()
    handle = registry.get(CPN_ID, CPN_VERSION)
    definition = handle.definition
    assert definition.id == CPN_ID
    assert definition.plugin_version == CPN_VERSION
    assert handle.declaration.runtime_factory_ref == FACTORY_REF
    matches = [d for d in registry.declarations() if d.definition.id == CPN_ID]
    assert len(matches) == 1


def test_cpn_registered_declaratively_only():
    modules = json.loads((PLUGINS_ROOT / "plugin_modules.json").read_text(encoding="utf-8"))["modules"]
    assert modules.count(PLUGIN_MODULE) == 1
    defaults = json.loads(
        (PLUGINS_ROOT / "model_release_defaults.json").read_text(encoding="utf-8")
    )["defaults"]
    assert defaults[CPN_ID][CPN_VERSION] == "golden"


# ---------------------------------------------------------------------------
# B. Pipeline catalog (no registry.py edit)
# ---------------------------------------------------------------------------


def test_pipeline_catalog_exposes_cpn_automatically():
    catalog = create_pipeline_registry()
    registered = catalog.get(CPN_ID)
    assert registered.definition.id == CPN_ID
    assert registered.definition.version == CPN_VERSION


# ---------------------------------------------------------------------------
# C. Public/read-model catalog
# ---------------------------------------------------------------------------


def test_public_pipeline_read_model_exposes_cpn(client):
    response = client.get("/api/pipelines")
    assert response.status_code == 200
    by_id = {item["id"]: item for item in response.json()}
    assert CPN_ID in by_id
    item = by_id[CPN_ID]
    assert item["plugin_api_version"] == 1
    assert item["label_space"] == CPN_LABEL_SPACE
    assert item["output_label_space"] == CPN_LABEL_SPACE
    assert item["input_compatibility"] == ["spacenet_14"]
    assert item["dataset_adapters"] == ["SpaceNet"]
    assert item["model_release_required"] is True
    assert item["task_capability"] == "detection_classification"
    caps = {
        (c["executor"], c["device_type"], c["precision"])
        for c in item["technical_execution_capabilities"]
    }
    assert ("local_cpu", "cpu", "float32") in caps
    assert ("remote_gpu", "cuda", "float16") in caps


# ---------------------------------------------------------------------------
# D. Lazy import isolation
# ---------------------------------------------------------------------------


def test_create_registries_are_ml_free_subprocess():
    code = (
        "import sys;"
        "from app.pipelines.plugin_registry import create_plugin_registry;"
        "from app.pipelines.registry import create_pipeline_registry;"
        "pr = create_plugin_registry();"
        "cr = create_pipeline_registry();"
        "ids = [d.id for d in cr.list()];"
        "assert 'cpn_bandwidth_tier' in ids, ids;"
        "assert 'torch' not in sys.modules, 'torch leaked';"
        "assert 'ultralytics' not in sys.modules, 'ultralytics leaked';"
        "print('OK')"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(BACKEND_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env,
        cwd=str(BACKEND_ROOT),
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_plugin_declaration_import_is_ml_free_subprocess():
    code = (
        "import sys, importlib;"
        f"m = importlib.import_module('{PLUGIN_MODULE}');"
        "assert getattr(m, 'PLUGIN', None) is not None;"
        "assert 'torch' not in sys.modules;"
        "assert 'ultralytics' not in sys.modules;"
        "print('OK')"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(BACKEND_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env,
        cwd=str(BACKEND_ROOT),
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


# ---------------------------------------------------------------------------
# E. Label-space separation + classification applicability
# ---------------------------------------------------------------------------


def test_label_space_file_semantics():
    label_space = LabelSpaceService(LABEL_ROOT).get(CPN_LABEL_SPACE)
    assert [(c.id, c.name) for c in label_space.classes] == [
        (0, "Narrow"),
        (1, "Mid"),
        (2, "Wide"),
    ]


def test_input_compatibility_differs_from_output_label_space():
    definition = create_plugin_registry().get(CPN_ID, CPN_VERSION).definition
    assert definition.input_compatibility == ("spacenet_14",)
    assert definition.dataset_adapters == ("SpaceNet",)
    assert definition.resolved_output_label_space == CPN_LABEL_SPACE
    assert definition.resolved_output_label_space != definition.input_compatibility[0]
    assert is_input_compatible(definition, "spacenet_14") is True
    assert is_input_compatible(definition, "signal_presence_v1") is False


def test_cpn_output_is_not_spacenet_classification():
    result = classification_applicability(
        SimpleNamespace(pipeline_id=CPN_ID, executor="local_cpu"),
        SimpleNamespace(label_space="spacenet_14"),
        create_pipeline_registry(),
    )
    assert result.applicable is False
    assert result.reason == "label_space_mismatch"


# ---------------------------------------------------------------------------
# F. Runtime mapping
# ---------------------------------------------------------------------------


def test_runtime_maps_proposals_losslessly(monkeypatch, tmp_path):
    captured = _patch_science(monkeypatch, CPN_PROPOSALS)
    module = _cpn_pipeline_module()
    pipeline = module.CPNBandwidthTierPipeline(
        detector_checkpoint_path=tmp_path / "det.pt",
        normalization=SimpleNamespace(
            percentile_low=1.0, percentile_high=99.0, value_low=0.0, value_high=1.0
        ),
        label_space=_label_space(),
        device="cpu",
    )
    output = pipeline.run(_recording(), {}, tmp_path / "ws")
    assert isinstance(output, PipelineOutput)
    assert captured["detector_device"] == "cpu"
    assert captured["preprocess_device"] == "cpu"

    assert [d.class_id for d in output.detections] == [1, 2]
    assert [d.class_name for d in output.detections] == ["Mid", "Wide"]
    d0, d1 = output.detections
    assert (d0.t_start_s, d0.t_end_s) == (0.10, 0.20)
    assert (d0.f_low_hz, d0.f_high_hz) == (2_440_100_000.0, 2_440_300_000.0)
    assert d0.confidence == 0.42
    assert d0.scores == {"cpn": 0.42}
    assert (d1.t_start_s, d1.t_end_s) == (0.30, 0.55)
    assert (d1.f_low_hz, d1.f_high_hz) == (2_440_600_000.0, 2_440_700_000.0)
    assert d1.confidence == 0.77
    assert d1.scores == {"cpn": 0.77}

    assert output.run_metadata == {
        "kind": CPN_ID,
        "task_capability": "detection_classification",
        "cpn_proposal_count": 2,
        "detection_count": 2,
    }


def test_runtime_rejects_non_empty_parameters(monkeypatch, tmp_path):
    _patch_science(monkeypatch, [])
    module = _cpn_pipeline_module()
    pipeline = module.CPNBandwidthTierPipeline(
        detector_checkpoint_path=tmp_path / "det.pt",
        normalization=SimpleNamespace(),
        label_space=_label_space(),
        device="cpu",
    )
    with pytest.raises(ValueError):
        pipeline.run(_recording(), {"threshold": 1}, tmp_path / "ws")


# ---------------------------------------------------------------------------
# G. Runtime assets
# ---------------------------------------------------------------------------


def test_build_runtime_requires_exactly_detector_and_normalization(monkeypatch, tmp_path):
    captured = {}
    module = _cpn_pipeline_module()

    class _FakeDetector:
        def __init__(self, checkpoint_path, *, device):
            captured["detector_checkpoint"] = checkpoint_path
            captured["detector_device"] = device

    monkeypatch.setattr(module, "CPNDetector", _FakeDetector)
    plugin = _cpn_plugin_module()
    assets = {
        "detector_checkpoint": tmp_path / "det.pt",
        "ls_stft_normalization": _write_normalization(tmp_path),
    }
    runtime = plugin.build_runtime(
        assets=assets, runtime_descriptor=_cpu_descriptor(),
        output_label_space=_label_space(),
    )
    assert runtime is not None
    assert captured["detector_checkpoint"] == tmp_path / "det.pt"
    assert captured["detector_device"] == "cpu"


def test_build_runtime_ignores_unneeded_assets(monkeypatch, tmp_path):
    module = _cpn_pipeline_module()

    class _FakeDetector:
        def __init__(self, checkpoint_path, *, device):
            pass

    monkeypatch.setattr(module, "CPNDetector", _FakeDetector)
    plugin = _cpn_plugin_module()
    assets = {
        "detector_checkpoint": tmp_path / "det.pt",
        "ls_stft_normalization": _write_normalization(tmp_path),
        "frn_checkpoint": tmp_path / "frn.pt",
        "frozen_config": tmp_path / "frozen.json",
    }
    runtime = plugin.build_runtime(
        assets=assets, runtime_descriptor=_cpu_descriptor(),
        output_label_space=_label_space(),
    )
    assert runtime is not None


@pytest.mark.parametrize("missing", ["detector_checkpoint", "ls_stft_normalization"])
def test_build_runtime_missing_required_asset_fails_closed(tmp_path, missing):
    plugin = _cpn_plugin_module()
    assets = {
        "detector_checkpoint": tmp_path / "det.pt",
        "ls_stft_normalization": _write_normalization(tmp_path),
    }
    assets.pop(missing)
    with pytest.raises(PlatformError) as exc:
        plugin.build_runtime(
            assets=assets, runtime_descriptor=_cpu_descriptor(),
            output_label_space=_label_space(),
        )
    assert exc.value.code == "PIPELINE_ASSET_MISMATCH"


@pytest.mark.parametrize(
    "bad",
    [
        "{not-json",
        json.dumps({"percentile_low": 1.0}),
        json.dumps(
            {"percentile_low": "x", "percentile_high": 99.0, "value_low": 0.0, "value_high": 1.0}
        ),
    ],
)
def test_build_runtime_bad_normalization_fails_closed(tmp_path, bad):
    plugin = _cpn_plugin_module()
    normalization = tmp_path / "ls_stft_normalization.json"
    normalization.write_text(bad, encoding="utf-8")
    assets = {
        "detector_checkpoint": tmp_path / "det.pt",
        "ls_stft_normalization": normalization,
    }
    with pytest.raises(PlatformError) as exc:
        plugin.build_runtime(
            assets=assets, runtime_descriptor=_cpu_descriptor(),
            output_label_space=_label_space(),
        )
    assert exc.value.code == "PIPELINE_ASSET_MISMATCH"


@pytest.mark.parametrize(
    "bad",
    [
        None,
        RuntimeDescriptor("remote_gpu", "cpu", 0, "float32"),
        RuntimeDescriptor("local_cpu", "cuda", 0, "float16"),
        RuntimeDescriptor("local_cpu", "cpu", 0, "float32"),
        RuntimeDescriptor("local_cpu", "cpu", None, "float16"),
        RuntimeDescriptor("unknown_executor", "cuda", 0, "float16"),
    ],
)
def test_build_runtime_requires_valid_descriptor(tmp_path, bad):
    plugin = _cpn_plugin_module()
    assets = {
        "detector_checkpoint": tmp_path / "det.pt",
        "ls_stft_normalization": _write_normalization(tmp_path),
    }
    with pytest.raises(PlatformError) as exc:
        plugin.build_runtime(
            assets=assets, runtime_descriptor=bad, output_label_space=_label_space()
        )
    assert exc.value.code == "EXECUTOR_UNAVAILABLE"


def test_build_runtime_accepts_remote_gpu_descriptor(monkeypatch, tmp_path):
    captured = {}
    module = _cpn_pipeline_module()

    class _FakeDetector:
        def __init__(self, checkpoint_path, *, device):
            captured["detector_device"] = device

    monkeypatch.setattr(module, "CPNDetector", _FakeDetector)
    plugin = _cpn_plugin_module()
    assets = {
        "detector_checkpoint": tmp_path / "det.pt",
        "ls_stft_normalization": _write_normalization(tmp_path),
    }
    plugin.build_runtime(
        assets=assets, runtime_descriptor=_remote_descriptor(),
        output_label_space=_label_space(),
    )
    assert captured["detector_device"] == 0


def test_build_runtime_rejects_wrong_label_space(tmp_path):
    plugin = _cpn_plugin_module()
    assets = {
        "detector_checkpoint": tmp_path / "det.pt",
        "ls_stft_normalization": _write_normalization(tmp_path),
    }
    wrong = SimpleNamespace(
        id="spacenet_14",
        classes=[SimpleNamespace(id=i, name=f"class_{i}") for i in range(14)],
    )
    with pytest.raises(PlatformError) as exc:
        plugin.build_runtime(
            assets=assets, runtime_descriptor=_cpu_descriptor(), output_label_space=wrong
        )
    assert exc.value.code == "OUTPUT_LABEL_SPACE_INVALID"


def test_build_runtime_rejects_wrong_tier_ids(tmp_path):
    plugin = _cpn_plugin_module()
    assets = {
        "detector_checkpoint": tmp_path / "det.pt",
        "ls_stft_normalization": _write_normalization(tmp_path),
    }
    wrong = SimpleNamespace(
        id=CPN_LABEL_SPACE,
        classes=[SimpleNamespace(id=0, name="Narrow"), SimpleNamespace(id=2, name="Wide")],
    )
    with pytest.raises(PlatformError) as exc:
        plugin.build_runtime(
            assets=assets, runtime_descriptor=_cpu_descriptor(), output_label_space=wrong
        )
    assert exc.value.code == "OUTPUT_LABEL_SPACE_INVALID"


# ---------------------------------------------------------------------------
# H. ModelRelease / AssetManifest
# ---------------------------------------------------------------------------


def test_cpn_asset_manifest_identity_and_assets():
    manifest = load_pipeline_asset_manifest(PLUGINS_ROOT / CPN_ID / "asset_manifest.json")
    assert manifest.pipeline_id == CPN_ID
    assert manifest.pipeline_version == CPN_VERSION
    assert set(manifest.assets) == {"detector_checkpoint", "ls_stft_normalization"}
    assert manifest.assets["detector_checkpoint"] == DETECTOR_SHA
    assert manifest.assets["ls_stft_normalization"] == NORMALIZATION_SHA
    assert manifest.asset_manifest_sha256 == CPN_MANIFEST_SHA
    assert manifest.asset_manifest_sha256 != ZOOMSPEC_MANIFEST_SHA


def test_cpn_release_record_is_plugin_owned():
    record = json.loads(
        (PLUGINS_ROOT / CPN_ID / "model_releases" / "golden.json").read_text(encoding="utf-8")
    )
    assert record == {
        "plugin_id": CPN_ID,
        "plugin_version": CPN_VERSION,
        "model_release_id": "golden",
        "asset_manifest_path": "asset_manifest.json",
        "asset_manifest_sha256": CPN_MANIFEST_SHA,
    }


def test_cpn_golden_release_resolves_exactly():
    store = ModelReleaseStore(
        PLUGINS_ROOT,
        load_model_release_defaults(PLUGINS_ROOT / "model_release_defaults.json"),
    )
    assert store.default_release_id(CPN_ID, CPN_VERSION) == "golden"
    resolved = store.resolve(CPN_ID, CPN_VERSION, "golden")
    assert resolved.release.plugin_id == CPN_ID
    assert resolved.release.plugin_version == CPN_VERSION
    assert resolved.release.model_release_id == "golden"
    assert resolved.release.asset_manifest_sha256 == CPN_MANIFEST_SHA
    assert resolved.manifest.asset_manifest_sha256 == CPN_MANIFEST_SHA
    assert resolved.manifest.pipeline_id == CPN_ID
    assert resolved.manifest.pipeline_version == CPN_VERSION
    assert set(resolved.manifest.assets) == {"detector_checkpoint", "ls_stft_normalization"}


# ---------------------------------------------------------------------------
# I. Generic PluginItemExecutor
# ---------------------------------------------------------------------------


def _write_executor_assets(tmp_path: Path):
    blobs = {
        "detector_checkpoint": b"detector-weights",
        "ls_stft_normalization": json.dumps(
            {"percentile_low": 1.0, "percentile_high": 99.0, "value_low": 0.0, "value_high": 1.0}
        ).encode("utf-8"),
    }
    paths: dict[str, Path] = {}
    hashes: dict[str, str] = {}
    for name, blob in blobs.items():
        path = tmp_path / name
        path.write_bytes(blob)
        paths[name] = path
        hashes[name] = hashlib.sha256(blob).hexdigest()
    provisional = PipelineAssetManifest(
        pipeline_id=CPN_ID, pipeline_version=CPN_VERSION, assets=hashes,
        asset_manifest_sha256="0" * 64,
    )
    manifest = PipelineAssetManifest(
        pipeline_id=CPN_ID, pipeline_version=CPN_VERSION, assets=hashes,
        asset_manifest_sha256=compute_asset_manifest_sha256(provisional),
    )
    return manifest, paths


class _Store:
    def __init__(self, resolved):
        self._resolved = resolved
        self.calls: list = []

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
                sample_rate_hz=1_000_000.0, center_frequency_hz=2_440_000_000.0,
                frequency_low_hz=2_440_000_000.0, frequency_high_hz=2_441_000_000.0,
                duration_s=1.0, label_space=label_space,
            ),
        )


class _AdapterRegistry:
    def get(self, dataset_name):
        return _Adapter()


class _Recorder:
    def __init__(self):
        self.calls: list = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)


def _freeze_batch(manifest, *, parameters=None):
    metadata = freeze_request_provenance(
        local_run_id="run_x", recording_fingerprint="2" * 64,
        source_data_sha256="1" * 64, dataset_name="SpaceNet", dataset_split="test",
        dataset_key="0", label_space="spacenet_14",
        pipeline_id=CPN_ID, pipeline_version=CPN_VERSION,
        required_remote_runtime_commit=RUN, orchestrator_commit=RUN,
        asset_manifest_sha256=manifest.asset_manifest_sha256,
        remote_profile="autodl_primary", model_release_id="golden",
        parameters=parameters or {},
    )
    return build_batch(metadata)


def _executor(manifest, paths, publisher, *, descriptor, parameters=None):
    batch = _freeze_batch(manifest, parameters=parameters)

    def _resolve_assets(*, plugin_id, plugin_version, asset_manifest_sha256, manifest):
        return {name: paths[name] for name in manifest.assets}

    worker = SimpleNamespace(
        required_runtime_commit=RUN,
        label_space_root=LABEL_ROOT,
        resolve_assets=_resolve_assets,
    )
    resolved = SimpleNamespace(
        release=SimpleNamespace(model_release_id="golden"), manifest=manifest
    )
    executor = PluginItemExecutor(
        batch=batch, worker=worker, plugin_registry=create_plugin_registry(),
        adapter_registry=_AdapterRegistry(), model_release_store=_Store(resolved),
        certificate_store=object(), runtime_descriptor=descriptor,
        trusted_assets_resolver=worker.resolve_assets, package_publisher=publisher,
    )
    return executor, batch


def test_generic_executor_runs_cpn_via_real_factory(monkeypatch, tmp_path):
    manifest, paths = _write_executor_assets(tmp_path)
    captured = _patch_science(monkeypatch, [CPN_PROPOSALS[0]])
    publisher = _Recorder()
    executor, batch = _executor(
        manifest, paths, publisher, descriptor=_cpu_descriptor()
    )

    executor.execute(batch.items[0], tmp_path / "job")

    assert len(publisher.calls) == 1
    call = publisher.calls[0]
    output = call["output"]
    assert isinstance(output, PipelineOutput)
    assert len(output.detections) == 1
    detection = output.detections[0]
    assert (detection.class_id, detection.class_name) == (1, "Mid")
    assert detection.confidence == 0.42
    assert detection.scores == {"cpn": 0.42}
    assert call["pipeline_definition"].id == CPN_ID
    assert call["output_label_space"].id == CPN_LABEL_SPACE
    assert captured["detector_device"] == "cpu"


def test_generic_executor_rejects_non_empty_parameters(monkeypatch, tmp_path):
    manifest, paths = _write_executor_assets(tmp_path)
    _patch_science(monkeypatch, [])
    publisher = _Recorder()
    executor, batch = _executor(
        manifest, paths, publisher, descriptor=_cpu_descriptor(),
        parameters={"threshold": 0.5},
    )
    with pytest.raises(PlatformError) as exc:
        executor.execute(batch.items[0], tmp_path / "job")
    assert exc.value.code == "PLUGIN_PARAMETERS_INVALID"
    assert publisher.calls == []


def test_generic_executor_rejects_incompatible_input_label_space(monkeypatch, tmp_path):
    manifest, paths = _write_executor_assets(tmp_path)
    _patch_science(monkeypatch, [])
    publisher = _Recorder()
    # Freeze a signal_presence_v1 recording -> CPN accepts only spacenet_14.
    metadata = freeze_request_provenance(
        local_run_id="run_x", recording_fingerprint="2" * 64,
        source_data_sha256="1" * 64, dataset_name="SpaceNet", dataset_split="test",
        dataset_key="0", label_space="signal_presence_v1",
        pipeline_id=CPN_ID, pipeline_version=CPN_VERSION,
        required_remote_runtime_commit=RUN, orchestrator_commit=RUN,
        asset_manifest_sha256=manifest.asset_manifest_sha256,
        remote_profile="autodl_primary", model_release_id="golden", parameters={},
    )
    batch = build_batch(metadata)

    def _resolve_assets(*, plugin_id, plugin_version, asset_manifest_sha256, manifest):
        return {name: paths[name] for name in manifest.assets}

    worker = SimpleNamespace(
        required_runtime_commit=RUN, label_space_root=LABEL_ROOT,
        resolve_assets=_resolve_assets,
    )
    resolved = SimpleNamespace(
        release=SimpleNamespace(model_release_id="golden"), manifest=manifest
    )
    executor = PluginItemExecutor(
        batch=batch, worker=worker, plugin_registry=create_plugin_registry(),
        adapter_registry=_AdapterRegistry(), model_release_store=_Store(resolved),
        certificate_store=object(), runtime_descriptor=_cpu_descriptor(),
        trusted_assets_resolver=worker.resolve_assets, package_publisher=publisher,
    )
    with pytest.raises(PlatformError) as exc:
        executor.execute(batch.items[0], tmp_path / "job")
    assert exc.value.code == "INPUT_INCOMPATIBLE"
    assert publisher.calls == []


# ---------------------------------------------------------------------------
# J. Zero-core-knowledge guard
# ---------------------------------------------------------------------------


def test_cpn_literal_absent_from_generic_production_core():
    plugin_dir = PLUGINS_ROOT / CPN_ID
    offenders = []
    for path in (BACKEND_ROOT / "app").rglob("*.py"):
        if plugin_dir in path.parents:
            continue
        if "cpn_bandwidth_tier" in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == []


def test_control_plane_core_unchanged_by_second_plugin():
    # The generic core modules must not contain either concrete plugin id literal.
    core = [
        "backend/app/pipelines/registry.py",
        "backend/app/pipelines/plugin_registry.py",
        "backend/app/analysis/service.py",
        "backend/app/analysis/router.py",
        "backend/app/remote_execution/runner.py",
        "backend/app/remote_execution/plugin_executor.py",
        "backend/app/remote_execution/package_publisher.py",
    ]
    for rel in core:
        source = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert CPN_ID not in source, f"{rel} contains {CPN_ID}"
        assert "zoomspec_yolo26n" not in source, f"{rel} contains a plugin-id branch"
