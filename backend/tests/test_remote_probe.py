"""Production remote runner probe tests (generic, D3B fix round).

``run_probe`` verifies server readiness WITHOUT loading any model: runtime
commit, the exact resolved ModelRelease manifest + asset bytes, the deployed
dataset root, definition label spaces, and CUDA at descriptor.device_index
(torch injected via the ``torch_import`` seam). No GPU.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app.core.errors import PlatformError
from app.pipelines.base import PipelineDefinition
from app.remote_execution import probe as probe_module
from app.remote_execution.assets import (
    PipelineAssetManifest,
    compute_asset_manifest_sha256,
)
from app.remote_execution.runner import _cli_probe
from app.remote_execution.runtime import RuntimeDescriptor
from app.remote_execution.schema import RemoteProbeResponseV1
from app.remote_execution.worker_context import RemoteWorkerContext

COMMIT_40 = "9a6f0feac0b0e6e2ac8ecd65d2e4383479e09f7c"
BACKEND_ROOT = str(Path(__file__).resolve().parents[1])

_ASSET_BLOBS = {
    "detector_checkpoint": b"detector-weights-bytes",
    "frn_checkpoint": b"frn-weights-bytes",
    "frozen_config": b"frozen-config-yaml",
    "ls_stft_normalization": json.dumps({
        "percentile_low": 1.0,
        "percentile_high": 99.0,
        "value_low": 0.0,
        "value_high": 1.0,
    }).encode("utf-8"),
}

_LABEL_SPACE = {
    "id": "spacenet_14",
    "version": 1,
    "classes": [{"id": i, "name": f"class_{i}"} for i in range(14)],
}


def _sha(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def _definition() -> PipelineDefinition:
    return PipelineDefinition(
        id="generic_remote_plugin",
        name="Generic Remote",
        version="1.0",
        label_space="spacenet_14",
        recommended_device="GPU",
        cpu_supported=False,
        stages=(),
        inspectable_stages=(),
        task_capability="detection_classification",
        executors_supported=("remote_gpu",),
        recommended_executor="remote_gpu",
        input_compatibility=("spacenet_14",),
        dataset_adapters=("SpaceNet",),
        model_release_required=True,
    )


class FakeCuda:
    available = True
    device_name = "NVIDIA RTX 5090"

    @staticmethod
    def is_available():
        return FakeCuda.available

    @staticmethod
    def get_device_name(index):
        if not FakeCuda.is_available():
            raise RuntimeError("CUDA not available")
        if index != 0:
            raise RuntimeError("no such device")
        return FakeCuda.device_name


class FakeTorch:
    cuda = FakeCuda


def _fake_git_ok(argv, **kwargs):
    return subprocess.CompletedProcess(
        args=argv, returncode=0, stdout=COMMIT_40 + "\n", stderr=""
    )


def _fake_git_bad_commit(argv, **kwargs):
    return subprocess.CompletedProcess(
        args=argv, returncode=0, stdout="d" * 40 + "\n", stderr=""
    )


def _worker(tmp_path: Path) -> RemoteWorkerContext:
    repo_root = tmp_path / "repo"
    return RemoteWorkerContext(
        repo_root=repo_root,
        job_root=tmp_path / "jobs",
        required_runtime_commit=COMMIT_40,
        dataset_root_space_net=tmp_path / "spacenet",
        label_space_root=repo_root / "label_spaces",
    )


def _write_real_deployment(tmp_path: Path, monkeypatch, *, tamper_asset=None):
    worker = _worker(tmp_path)
    worker.dataset_root_space_net.mkdir(parents=True, exist_ok=True)
    (worker.label_space_root).mkdir(parents=True, exist_ok=True)
    (worker.label_space_root / "spacenet_14.json").write_text(
        json.dumps(_LABEL_SPACE), encoding="utf-8"
    )
    assets: dict[str, Path] = {}
    hashes = {}
    for logical, blob in _ASSET_BLOBS.items():
        path = tmp_path / f"{logical}.bin"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob)
        assets[logical] = path
        hashes[logical] = _sha(blob)
    if tamper_asset:
        assets[tamper_asset].write_bytes(b"tampered-" + _ASSET_BLOBS[tamper_asset])
    provisional = PipelineAssetManifest(
        pipeline_id="generic_remote_plugin",
        pipeline_version="1.0",
        assets=hashes,
        asset_manifest_sha256="0" * 64,
    )
    manifest = PipelineAssetManifest(
        pipeline_id=provisional.pipeline_id,
        pipeline_version=provisional.pipeline_version,
        assets=provisional.assets,
        asset_manifest_sha256=compute_asset_manifest_sha256(provisional),
    )
    monkeypatch.setattr("subprocess.run", _fake_git_ok)
    return worker, manifest, assets


def test_probe_success_returns_exact_response(tmp_path, monkeypatch):
    worker, manifest, assets = _write_real_deployment(tmp_path, monkeypatch)
    response = probe_module.run_probe(
        worker, descriptor=worker.runtime_descriptor(),
        plugin_definition=_definition(), manifest=manifest, assets=assets,
        torch_import=FakeTorch,
    )
    assert isinstance(response, RemoteProbeResponseV1)
    assert response.schema_version == 1
    assert response.status == "available"
    assert response.remote_runtime_commit == COMMIT_40
    assert response.asset_manifest_sha256 == manifest.asset_manifest_sha256
    assert response.device == 0


def test_probe_context_invalid_fails_closed(tmp_path, monkeypatch):
    for name in ("WSP_REMOTE_REPO_ROOT", "WSP_REMOTE_JOB_ROOT",
                 "WSP_REMOTE_REQUIRED_RUNTIME_COMMIT", "WSP_REMOTE_SPACENET_ROOT"):
        monkeypatch.delenv(name, raising=False)
    from types import SimpleNamespace

    with pytest.raises(PlatformError) as exc:
        _cli_probe(SimpleNamespace(
            plugin_id="generic_remote_plugin", plugin_version="1.0",
            model_release_id="golden", asset_manifest_sha256="a" * 64,
        ))
    assert exc.value.code == "REMOTE_WORKER_CONTEXT_INVALID"


def test_probe_runtime_commit_mismatch_fails(tmp_path, monkeypatch):
    worker, manifest, assets = _write_real_deployment(tmp_path, monkeypatch)
    monkeypatch.setattr("subprocess.run", _fake_git_bad_commit)
    with pytest.raises(PlatformError) as exc:
        probe_module.run_probe(
            worker, descriptor=worker.runtime_descriptor(),
            plugin_definition=_definition(), manifest=manifest, assets=assets,
            torch_import=FakeTorch,
        )
    assert exc.value.code == "REMOTE_IMPLEMENTATION_MISMATCH"


@pytest.mark.parametrize("asset", ["detector_checkpoint", "frn_checkpoint",
                                   "frozen_config", "ls_stft_normalization"])
def test_probe_asset_byte_mismatch_fails(tmp_path, monkeypatch, asset):
    worker, manifest, assets = _write_real_deployment(tmp_path, monkeypatch, tamper_asset=asset)
    with pytest.raises(PlatformError) as exc:
        probe_module.run_probe(
            worker, descriptor=worker.runtime_descriptor(),
            plugin_definition=_definition(), manifest=manifest, assets=assets,
            torch_import=FakeTorch,
        )
    assert exc.value.code == "PIPELINE_ASSET_MISMATCH"


def test_probe_missing_dataset_root_fails(tmp_path, monkeypatch):
    worker, manifest, assets = _write_real_deployment(tmp_path, monkeypatch)
    worker.dataset_root_space_net.rmdir()
    with pytest.raises(PlatformError) as exc:
        probe_module.run_probe(
            worker, descriptor=worker.runtime_descriptor(),
            plugin_definition=_definition(), manifest=manifest, assets=assets,
            torch_import=FakeTorch,
        )
    assert exc.value.code == "REMOTE_PROBE_UNAVAILABLE"


def test_probe_label_space_unavailable_fails(tmp_path, monkeypatch):
    worker, manifest, assets = _write_real_deployment(tmp_path, monkeypatch)
    (worker.label_space_root / "spacenet_14.json").unlink()
    with pytest.raises(PlatformError) as exc:
        probe_module.run_probe(
            worker, descriptor=worker.runtime_descriptor(),
            plugin_definition=_definition(), manifest=manifest, assets=assets,
            torch_import=FakeTorch,
        )
    assert exc.value.code == "LABEL_SPACE_NOT_FOUND"


def test_probe_cuda_unavailable_fails(tmp_path, monkeypatch):
    worker, manifest, assets = _write_real_deployment(tmp_path, monkeypatch)
    class NoCuda(FakeCuda):
        @staticmethod
        def is_available():
            return False
    class NoCudaTorch:
        cuda = NoCuda
    with pytest.raises(PlatformError) as exc:
        probe_module.run_probe(
            worker, descriptor=worker.runtime_descriptor(),
            plugin_definition=_definition(), manifest=manifest, assets=assets,
            torch_import=NoCudaTorch,
        )
    assert exc.value.code == "REMOTE_PROBE_UNAVAILABLE"


def test_probe_device_zero_missing_fails(tmp_path, monkeypatch):
    worker, manifest, assets = _write_real_deployment(tmp_path, monkeypatch)
    class NoDevice0(FakeCuda):
        @staticmethod
        def get_device_name(index):
            raise RuntimeError("no such device")
    class NoDevice0Torch:
        cuda = NoDevice0
    with pytest.raises(PlatformError) as exc:
        probe_module.run_probe(
            worker, descriptor=worker.runtime_descriptor(),
            plugin_definition=_definition(), manifest=manifest, assets=assets,
            torch_import=NoDevice0Torch,
        )
    assert exc.value.code == "REMOTE_PROBE_UNAVAILABLE"


def test_probe_does_not_instantiate_or_load_models(tmp_path, monkeypatch):
    import builtins

    worker, manifest, assets = _write_real_deployment(tmp_path, monkeypatch)
    real_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name == "ultralytics":
            raise AssertionError(f"probe must never import {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    response = probe_module.run_probe(
        worker, descriptor=worker.runtime_descriptor(),
        plugin_definition=_definition(), manifest=manifest, assets=assets,
        torch_import=FakeTorch,
    )
    assert response.status == "available"


def test_runner_module_import_does_not_import_torch_or_ultralytics():
    code = (
        "import sys; import app.remote_execution.runner;"
        "assert 'torch' not in sys.modules, 'torch imported';"
        "assert 'ultralytics' not in sys.modules, 'ultralytics imported'"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = BACKEND_ROOT + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr

# ---------------------------------------------------------------------------
# descriptor-driven readiness
# ---------------------------------------------------------------------------


def test_probe_uses_descriptor_device_index(tmp_path, monkeypatch):
    worker, manifest, assets = _write_real_deployment(tmp_path, monkeypatch)
    descriptor = RuntimeDescriptor("remote_gpu", "cuda", 0, "float16")
    response = probe_module.run_probe(
        worker, descriptor=descriptor, plugin_definition=_definition(),
        manifest=manifest, assets=assets, torch_import=FakeTorch,
    )
    assert response.device == 0


def test_probe_non_cuda_descriptor_fails_closed(tmp_path, monkeypatch):
    worker, manifest, assets = _write_real_deployment(tmp_path, monkeypatch)

    class NoCuda:
        @staticmethod
        def is_available():
            return False

    class NoCudaTorch:
        cuda = NoCuda

    descriptor = RuntimeDescriptor("remote_gpu", "cpu", None, "float32")
    with pytest.raises(PlatformError) as exc:
        probe_module.run_probe(
            worker, descriptor=descriptor, plugin_definition=_definition(),
            manifest=manifest, assets=assets, torch_import=NoCudaTorch,
        )
    assert exc.value.code == "REMOTE_PROBE_UNAVAILABLE"


def test_probe_cuda_none_index_fails_closed(tmp_path, monkeypatch):
    worker, manifest, assets = _write_real_deployment(tmp_path, monkeypatch)
    descriptor = RuntimeDescriptor("remote_gpu", "cuda", None, "float16")
    with pytest.raises(PlatformError) as exc:
        probe_module.run_probe(
            worker, descriptor=descriptor, plugin_definition=_definition(),
            manifest=manifest, assets=assets, torch_import=FakeTorch,
        )
    assert exc.value.code == "REMOTE_PROBE_UNAVAILABLE"


def test_probe_uses_configured_nonzero_index(tmp_path, monkeypatch):
    worker, manifest, assets = _write_real_deployment(tmp_path, monkeypatch)

    class NonZeroCuda:
        @staticmethod
        def is_available():
            return True

        @staticmethod
        def get_device_name(index):
            if index != 3:
                raise RuntimeError("no such device")
            return "NVIDIA RTX 5090"

    class NonZeroTorch:
        cuda = NonZeroCuda

    descriptor = RuntimeDescriptor("remote_gpu", "cuda", 3, "float16")
    response = probe_module.run_probe(
        worker, descriptor=descriptor, plugin_definition=_definition(),
        manifest=manifest, assets=assets, torch_import=NonZeroTorch,
    )
    assert response.status == "available"
    assert response.device == 3

    bad = RuntimeDescriptor("remote_gpu", "cuda", 1, "float16")
    with pytest.raises(PlatformError):
        probe_module.run_probe(
            worker, descriptor=bad, plugin_definition=_definition(),
            manifest=manifest, assets=assets, torch_import=NonZeroTorch,
        )
