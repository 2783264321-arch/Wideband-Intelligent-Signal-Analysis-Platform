"""Production remote runner probe tests (Task 12F-B Task 2).

``run_probe`` verifies server readiness WITHOUT loading any model: runtime
commit, asset manifest + asset bytes, SpaceNet dataset root, spacenet_14 label
space, and CUDA/device-0 (torch injected via the ``torch_import`` seam). No GPU.
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
        detector_checkpoint=tmp_path / "det.pt",
        frn_checkpoint=tmp_path / "frn.pt",
        frozen_config_path=tmp_path / "frozen.json",
        ls_stft_normalization_path=tmp_path / "norm.json",
        label_space_root=repo_root / "label_spaces",
        asset_manifest_path=(
            repo_root / "backend" / "app" / "pipelines"
            / "zoomspec_yolo26n_aug_combined_frn_v3" / "asset_manifest.json"
        ),
    )


def _asset_paths(worker: RemoteWorkerContext) -> dict[str, Path]:
    return {
        "detector_checkpoint": worker.detector_checkpoint,
        "frn_checkpoint": worker.frn_checkpoint,
        "frozen_config": worker.frozen_config_path,
        "ls_stft_normalization": worker.ls_stft_normalization_path,
    }


def _write_real_deployment(tmp_path: Path, monkeypatch, *, tamper_asset=None):
    worker = _worker(tmp_path)
    worker.dataset_root_space_net.mkdir(parents=True, exist_ok=True)
    (worker.label_space_root).mkdir(parents=True, exist_ok=True)
    (worker.label_space_root / "spacenet_14.json").write_text(
        json.dumps(_LABEL_SPACE), encoding="utf-8"
    )
    paths = _asset_paths(worker)
    hashes = {}
    for logical, blob in _ASSET_BLOBS.items():
        paths[logical].parent.mkdir(parents=True, exist_ok=True)
        paths[logical].write_bytes(blob)
        hashes[logical] = _sha(blob)
    if tamper_asset:
        paths[tamper_asset].write_bytes(b"tampered-" + _ASSET_BLOBS[tamper_asset])
    manifest = PipelineAssetManifest(
        pipeline_id="zoomspec_yolo26n_aug_combined_frn_v3",
        pipeline_version="1.0.0",
        assets=hashes,
        asset_manifest_sha256="0" * 64,
    )
    computed = compute_asset_manifest_sha256(manifest)
    manifest = PipelineAssetManifest(
        pipeline_id=manifest.pipeline_id,
        pipeline_version=manifest.pipeline_version,
        assets=manifest.assets,
        asset_manifest_sha256=computed,
    )
    worker.asset_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    worker.asset_manifest_path.write_text(
        json.dumps(manifest.__dict__), encoding="utf-8"
    )
    monkeypatch.setattr("subprocess.run", _fake_git_ok)
    return worker, manifest


def test_probe_success_returns_exact_response(tmp_path, monkeypatch):
    worker, manifest = _write_real_deployment(tmp_path, monkeypatch)
    response = probe_module.run_probe(worker, descriptor=worker.runtime_descriptor(), torch_import=FakeTorch)
    assert isinstance(response, RemoteProbeResponseV1)
    assert response.schema_version == 1
    assert response.status == "available"
    assert response.remote_runtime_commit == COMMIT_40
    assert response.asset_manifest_sha256 == manifest.asset_manifest_sha256
    assert response.device == 0


def test_probe_context_invalid_fails_closed(tmp_path, monkeypatch):
    for name in ("WSP_REMOTE_REPO_ROOT", "WSP_REMOTE_JOB_ROOT",
                 "WSP_REMOTE_REQUIRED_RUNTIME_COMMIT", "WSP_REMOTE_SPACENET_ROOT",
                 "WSP_REMOTE_DETECTOR_CHECKPOINT", "WSP_REMOTE_FRN_CHECKPOINT",
                 "WSP_REMOTE_FROZEN_CONFIG", "WSP_REMOTE_LS_STFT_NORMALIZATION"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(PlatformError) as exc:
        _cli_probe(None)
    assert exc.value.code == "REMOTE_WORKER_CONTEXT_INVALID"


def test_probe_runtime_commit_mismatch_fails(tmp_path, monkeypatch):
    worker, _ = _write_real_deployment(tmp_path, monkeypatch)
    monkeypatch.setattr("subprocess.run", _fake_git_bad_commit)
    with pytest.raises(PlatformError) as exc:
        probe_module.run_probe(worker, descriptor=worker.runtime_descriptor(), torch_import=FakeTorch)
    assert exc.value.code == "REMOTE_IMPLEMENTATION_MISMATCH"


def test_probe_asset_manifest_self_hash_mismatch_fails(tmp_path, monkeypatch):
    worker, manifest = _write_real_deployment(tmp_path, monkeypatch)
    tampered = dict(manifest.__dict__)
    tampered["pipeline_version"] = "9.9.9"
    worker.asset_manifest_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(PlatformError) as exc:
        probe_module.run_probe(worker, descriptor=worker.runtime_descriptor(), torch_import=FakeTorch)
    assert exc.value.code == "PIPELINE_ASSET_MISMATCH"


@pytest.mark.parametrize("asset", ["detector_checkpoint", "frn_checkpoint",
                                   "frozen_config", "ls_stft_normalization"])
def test_probe_asset_byte_mismatch_fails(tmp_path, monkeypatch, asset):
    worker, _ = _write_real_deployment(tmp_path, monkeypatch, tamper_asset=asset)
    with pytest.raises(PlatformError) as exc:
        probe_module.run_probe(worker, descriptor=worker.runtime_descriptor(), torch_import=FakeTorch)
    assert exc.value.code == "PIPELINE_ASSET_MISMATCH"


def test_probe_missing_dataset_root_fails(tmp_path, monkeypatch):
    worker, _ = _write_real_deployment(tmp_path, monkeypatch)
    worker.dataset_root_space_net.rmdir()
    with pytest.raises(PlatformError) as exc:
        probe_module.run_probe(worker, descriptor=worker.runtime_descriptor(), torch_import=FakeTorch)
    assert exc.value.code == "REMOTE_PROBE_UNAVAILABLE"


def test_probe_label_space_unavailable_fails(tmp_path, monkeypatch):
    worker, _ = _write_real_deployment(tmp_path, monkeypatch)
    (worker.label_space_root / "spacenet_14.json").unlink()
    with pytest.raises(PlatformError) as exc:
        probe_module.run_probe(worker, descriptor=worker.runtime_descriptor(), torch_import=FakeTorch)
    assert exc.value.code == "LABEL_SPACE_NOT_FOUND"


def test_probe_cuda_unavailable_fails(tmp_path, monkeypatch):
    worker, _ = _write_real_deployment(tmp_path, monkeypatch)
    class NoCuda(FakeCuda):
        @staticmethod
        def is_available():
            return False
    class NoCudaTorch:
        cuda = NoCuda
    with pytest.raises(PlatformError) as exc:
        probe_module.run_probe(worker, descriptor=worker.runtime_descriptor(), torch_import=NoCudaTorch)
    assert exc.value.code == "REMOTE_PROBE_UNAVAILABLE"


def test_probe_device_zero_missing_fails(tmp_path, monkeypatch):
    worker, _ = _write_real_deployment(tmp_path, monkeypatch)
    class NoDevice0(FakeCuda):
        @staticmethod
        def get_device_name(index):
            raise RuntimeError("no such device")
    class NoDevice0Torch:
        cuda = NoDevice0
    with pytest.raises(PlatformError) as exc:
        probe_module.run_probe(worker, descriptor=worker.runtime_descriptor(), torch_import=NoDevice0Torch)
    assert exc.value.code == "REMOTE_PROBE_UNAVAILABLE"


def test_probe_does_not_instantiate_or_load_models(tmp_path, monkeypatch):
    import builtins

    worker, _ = _write_real_deployment(tmp_path, monkeypatch)
    real_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name == "ultralytics" or name.startswith("app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3"):
            raise AssertionError(f"probe must never import {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    response = probe_module.run_probe(worker, descriptor=worker.runtime_descriptor(), torch_import=FakeTorch)
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
# D4 — descriptor-driven readiness
# ---------------------------------------------------------------------------


def test_probe_uses_descriptor_device_index(tmp_path, monkeypatch):
    worker, _ = _write_real_deployment(tmp_path, monkeypatch)
    descriptor = RuntimeDescriptor("remote_gpu", "cuda", 0, "float16")
    response = probe_module.run_probe(worker, descriptor=descriptor, torch_import=FakeTorch)
    assert response.device == 0


def test_probe_cpu_descriptor_skips_cuda(tmp_path, monkeypatch):
    worker, _ = _write_real_deployment(tmp_path, monkeypatch)

    class NoCuda:
        @staticmethod
        def is_available():
            return False

    class NoCudaTorch:
        cuda = NoCuda

    descriptor = RuntimeDescriptor("remote_gpu", "cpu", None, "float32")
    response = probe_module.run_probe(worker, descriptor=descriptor, torch_import=NoCudaTorch)
    assert response.status == "available"
