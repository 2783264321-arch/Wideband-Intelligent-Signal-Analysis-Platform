"""E2A — generic ZoomSpec remote execution (replaces the retired
``test_zoomspec_remote_executor.py``).

Drives a frozen ZoomSpec ``RemoteExecutionBatchV1`` through the production
generic path:

    PluginItemExecutor
      -> production create_plugin_registry()
      -> exact ZoomSpec PluginHandle
      -> ModelReleaseStore exact release resolution
      -> trusted manifest asset resolution + verify_assets
      -> DatasetAdapter
      -> E1 build_runtime
      -> _ZoomSpecRuntime.execute
      -> production publish_package
      -> terminal envelope + Analysis Package v1 ZIP

Only the expensive scientific model construction/inference is monkeypatched.
No GPU / torch / ultralytics. ``app.remote_execution.zoomspec_executor`` and
``ZoomSpecRemoteItemExecutor`` are not imported/used.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from app.core.errors import PlatformError
from app.datasets.adapter import ResolvedRecordingInput
from app.labels.service import LabelSpaceService
from app.pipelines.base import PipelineOutput, RecordingInput
from app.pipelines.plugin_registry import create_plugin_registry
from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3 import pipeline as pipeline_module
from app.remote_execution.assets import (
    PipelineAssetManifest,
    compute_asset_manifest_sha256,
)
from app.remote_execution.model_release import (
    ModelReleaseStore,
    load_model_release_defaults,
)
from app.remote_execution.package_publisher import publish_package
from app.remote_execution.plugin_executor import PluginItemExecutor
from app.remote_execution.request_builder import build_batch, freeze_request_provenance
from app.remote_execution.result_ingestor import parse_remote_execution_envelope_json
from app.remote_execution.runner import _verify_terminal_result
from app.remote_execution.runtime import RuntimeDescriptor

ZP_ID = "zoomspec_yolo26n_aug_combined_frn_v3"
ZP_VERSION = "1.0.0"
GOLDEN_SHA = "16cc0534ed61603a84142da8a04af6642f9e7661848835fe5473199bec38ac08"
REPO_ROOT = Path(__file__).resolve().parents[2]
LABEL_ROOT = REPO_ROOT / "label_spaces"
PIPELINES_ROOT = REPO_ROOT / "backend" / "app" / "pipelines"
RUNTIME_COMMIT = "68b1464842d0fb366fc211f53436d0ba49e3fbef"
ORCHESTRATOR_COMMIT = "9a6f0feac0b0e6e2ac8ecd65d2e4383479e09f7c"


def _write_assets(tmp_path: Path):
    blobs = {
        "detector_checkpoint": b"detector-weights",
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


class FakeScientificPipeline:
    """No-GPU scientific seam: records the exact frozen-pipeline construction."""

    constructions: list[dict] = []

    def __init__(self, *, detector_checkpoint_path, frn_checkpoint_path,
                 normalization, label_space, device):
        self.calls: list = []
        type(self).constructions.append({
            "detector": detector_checkpoint_path, "frn": frn_checkpoint_path,
            "normalization": normalization, "label_space": label_space,
            "device": device,
        })

    def run(self, recording, parameters, workspace):
        self.calls.append((recording, parameters, workspace))
        return PipelineOutput(detections=[], run_metadata={"fake": True})


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
                sample_rate_hz=1.0, center_frequency_hz=0.0,
                frequency_low_hz=0.0, frequency_high_hz=1.0,
                duration_s=1.0, label_space=label_space,
            ),
        )


class _AdapterRegistry:
    def get(self, dataset_name):
        return _Adapter()


class _Recorder:
    def __init__(self, exc=None):
        self.calls: list = []
        self._exc = exc

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self._exc is not None:
            raise self._exc


def _batch(*, pipeline_id=ZP_ID, parameters=None, asset_manifest_sha256="0" * 64,
           runtime_commit=RUNTIME_COMMIT):
    metadata = freeze_request_provenance(
        local_run_id="run_x", recording_fingerprint="2" * 64,
        source_data_sha256="1" * 64, dataset_name="SpaceNet", dataset_split="test",
        dataset_key="0", label_space="spacenet_14",
        pipeline_id=pipeline_id, pipeline_version=ZP_VERSION,
        required_remote_runtime_commit=runtime_commit,
        orchestrator_commit=ORCHESTRATOR_COMMIT,
        asset_manifest_sha256=asset_manifest_sha256,
        remote_profile="autodl_primary", model_release_id="golden",
        parameters=parameters or {},
    )
    return build_batch(metadata)


def _executor(monkeypatch, tmp_path, manifest, paths, *, descriptor=None,
              publisher=None, batch=None, store=None):
    monkeypatch.setattr(pipeline_module, "ZoomSpecFrozenPipeline", FakeScientificPipeline)
    FakeScientificPipeline.constructions.clear()
    descriptor = descriptor or RuntimeDescriptor("remote_gpu", "cuda", 2, "float16")
    batch = batch or _batch(asset_manifest_sha256=manifest.asset_manifest_sha256)

    def _resolve_assets(*, plugin_id, plugin_version, asset_manifest_sha256, manifest):
        if not manifest.assets:
            raise PlatformError("PIPELINE_ASSET_MISMATCH", "empty manifest")
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
    executor = PluginItemExecutor(
        batch=batch, worker=worker, plugin_registry=create_plugin_registry(),
        adapter_registry=_AdapterRegistry(),
        model_release_store=store or _Store(resolved),
        certificate_store=object(), runtime_descriptor=descriptor,
        trusted_assets_resolver=worker.resolve_assets,
        package_publisher=publisher or _Recorder(),
    )
    return executor, batch


def test_generic_zoomspec_remote_happy_path(monkeypatch, tmp_path):
    manifest, paths = _write_assets(tmp_path)
    job_root = tmp_path / "job"
    executor, batch = _executor(monkeypatch, tmp_path, manifest, paths,
                                publisher=publish_package)

    executor.execute(batch.items[0], job_root)

    # Terminal files accepted by the frozen runner verification.
    _verify_terminal_result(batch, batch.items[0], job_root)
    result_dir = job_root / "results" / batch.items[0].item_key
    envelope = parse_remote_execution_envelope_json((result_dir / "envelope.json").read_bytes())
    assert envelope.pipeline_id == ZP_ID
    assert envelope.pipeline_version == ZP_VERSION
    assert envelope.remote_runtime_commit == RUNTIME_COMMIT
    assert envelope.asset_manifest_sha256 == manifest.asset_manifest_sha256

    import zipfile

    with zipfile.ZipFile(result_dir / "analysis_result.zip") as archive:
        package = json.loads(archive.read("manifest.json"))
    assert package["schema_version"] == 1
    assert package["pipeline"]["id"] == ZP_ID
    assert package["label_space"] == "spacenet_14"
    assert package["parameters"] == {}
    assert package["execution"] == {
        "executor": "remote_gpu", "device": "cuda:2", "environment": None,
    }
    assert package["recording"]["name"] == "0"

    # E1 factory received the exact verified assets, descriptor device, label space.
    construction = FakeScientificPipeline.constructions[0]
    assert construction["detector"] == paths["detector_checkpoint"]
    assert construction["frn"] == paths["frn_checkpoint"]
    assert construction["device"] == 2
    assert construction["label_space"].id == "spacenet_14"

    # Recording resolution went through the adapter only.
    assert construction["normalization"].percentile_low == 1.0


def test_generic_zoomspec_remote_release_resolution_is_exact(monkeypatch, tmp_path):
    manifest, paths = _write_assets(tmp_path)
    store = _Store(SimpleNamespace(
        release=SimpleNamespace(model_release_id="golden"), manifest=manifest
    ))
    executor, batch = _executor(monkeypatch, tmp_path, manifest, paths, store=store)

    executor.execute(batch.items[0], tmp_path / "job")

    assert store.calls == [(ZP_ID, ZP_VERSION, "golden")]


def test_generic_zoomspec_remote_unknown_plugin_fails_closed(monkeypatch, tmp_path):
    manifest, paths = _write_assets(tmp_path)
    batch = _batch(pipeline_id="not_a_plugin",
                   asset_manifest_sha256=manifest.asset_manifest_sha256)
    executor, batch = _executor(monkeypatch, tmp_path, manifest, paths, batch=batch)
    with pytest.raises(PlatformError) as exc:
        executor.execute(batch.items[0], tmp_path / "job")
    assert exc.value.code == "PLUGIN_NOT_FOUND"
    assert FakeScientificPipeline.constructions == []


def test_generic_zoomspec_remote_non_empty_parameters_fail_closed(monkeypatch, tmp_path):
    manifest, paths = _write_assets(tmp_path)
    batch = _batch(parameters={"threshold": 0.5},
                   asset_manifest_sha256=manifest.asset_manifest_sha256)
    executor, batch = _executor(monkeypatch, tmp_path, manifest, paths, batch=batch)
    with pytest.raises(PlatformError) as exc:
        executor.execute(batch.items[0], tmp_path / "job")
    assert exc.value.code == "PLUGIN_PARAMETERS_INVALID"
    assert FakeScientificPipeline.constructions == []


def test_generic_zoomspec_remote_runtime_factory_failure_propagates(monkeypatch, tmp_path):
    manifest, paths = _write_assets(tmp_path)
    # Non-certified descriptor -> E1 build_runtime fails closed.
    bad = RuntimeDescriptor("remote_gpu", "cpu", 0, "float32")
    executor, batch = _executor(monkeypatch, tmp_path, manifest, paths, descriptor=bad)
    with pytest.raises(PlatformError) as exc:
        executor.execute(batch.items[0], tmp_path / "job")
    assert exc.value.code == "EXECUTOR_UNAVAILABLE"


def test_generic_zoomspec_remote_publisher_failure_propagates(monkeypatch, tmp_path):
    manifest, paths = _write_assets(tmp_path)
    publisher = _Recorder(exc=PlatformError("REMOTE_RESULT_CONFLICT", "terminal exists"))
    batch = _batch(asset_manifest_sha256=manifest.asset_manifest_sha256)
    executor, batch = _executor(monkeypatch, tmp_path, manifest, paths,
                                publisher=publisher, batch=batch)
    with pytest.raises(PlatformError) as exc:
        executor.execute(batch.items[0], tmp_path / "job")
    assert exc.value.code == "REMOTE_RESULT_CONFLICT"


def test_real_model_release_store_resolves_exact_golden_identity():
    store = ModelReleaseStore(
        PIPELINES_ROOT, load_model_release_defaults(PIPELINES_ROOT / "model_release_defaults.json")
    )
    resolved = store.resolve(ZP_ID, ZP_VERSION, "golden")
    assert resolved.release.model_release_id == "golden"
    assert resolved.manifest.asset_manifest_sha256 == GOLDEN_SHA
    with pytest.raises(PlatformError):
        store.resolve(ZP_ID, ZP_VERSION, "does_not_exist")


def test_generic_zoomspec_execution_does_not_import_legacy_executor(tmp_path):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "backend") + os.pathsep + env.get("PYTHONPATH", "")
    code = (
        "import importlib, sys;"
        "importlib.import_module('app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.plugin');"
        "from app.remote_execution.plugin_executor import PluginItemExecutor;"
        "assert 'app.remote_execution.zoomspec_executor' not in sys.modules;"
        "assert 'torch' not in sys.modules and 'ultralytics' not in sys.modules"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
