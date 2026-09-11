"""Production remote runner probe (Task 12F-B Task 2).

Verifies real server readiness WITHOUT loading any model:
- deployed runtime commit equals the required runtime commit;
- asset manifest self-hash + all four asset file SHA256s match;
- SpaceNet dataset root exists;
- ``spacenet_14`` label space is loadable;
- CUDA is available and device 0 is usable.

Torch is imported lazily inside the CUDA check (the ``torch_import`` seam lets
CPU tests inject a fake). No YOLO/FRN/ZoomSpec pipeline is ever constructed.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.errors import PlatformError
from app.labels.service import LabelSpaceService
from app.remote_execution.assets import (
    load_pipeline_asset_manifest,
    verify_assets,
    verify_remote_runtime_commit,
)
from app.remote_execution.schema import RemoteProbeResponseV1
from app.remote_execution.worker_context import RemoteWorkerContext


def _probe_unavailable(message: str) -> PlatformError:
    return PlatformError("REMOTE_PROBE_UNAVAILABLE", message)


def _require_dataset_root(path: Path) -> None:
    if not path.exists() or not path.is_dir():
        raise _probe_unavailable("SpaceNet dataset root is not available on the remote runtime.")


def _require_label_space(label_space_root: Path) -> None:
    LabelSpaceService(label_space_root).get("spacenet_14")


def _require_cuda_device(descriptor, torch_import: Any) -> None:
    torch = torch_import if torch_import is not None else __import__("torch")
    if not torch.cuda.is_available():
        raise _probe_unavailable("CUDA is not available on the remote runtime.")
    index = descriptor.device_index if descriptor.device_index is not None else 0
    try:
        torch.cuda.get_device_name(index)
    except Exception:
        raise _probe_unavailable(f"CUDA device {index} is not usable on the remote runtime.")


def _require_accelerator(descriptor, torch_import: Any) -> None:
    """Descriptor-driven readiness: CUDA probes its exact device index; a CPU
    descriptor performs no CUDA check (CPU readiness is owned by the local
    inference worker probe, not this remote probe)."""
    if getattr(descriptor, "device_type", None) == "cuda":
        _require_cuda_device(descriptor, torch_import)


def run_probe(
    worker: RemoteWorkerContext,
    *,
    descriptor,
    torch_import: Any = None,
) -> RemoteProbeResponseV1:
    """Fail-closed server readiness verification. Never loads models.

    ``descriptor`` is the deployment-owned RuntimeDescriptor; readiness is checked
    against its exact executor/device_type/device_index/precision.
    """
    verify_remote_runtime_commit(worker.repo_root, worker.required_runtime_commit)
    manifest = load_pipeline_asset_manifest(worker.asset_manifest_path)
    verify_assets(manifest, {
        "detector_checkpoint": worker.detector_checkpoint,
        "frn_checkpoint": worker.frn_checkpoint,
        "frozen_config": worker.frozen_config_path,
        "ls_stft_normalization": worker.ls_stft_normalization_path,
    })
    _require_dataset_root(worker.dataset_root_space_net)
    _require_label_space(worker.label_space_root)
    _require_accelerator(descriptor, torch_import)
    # The verified commit value is legitimate only because verify_remote_runtime_commit
    # just proved the deployed repo HEAD equals worker.required_runtime_commit.
    return RemoteProbeResponseV1(
        schema_version=1,
        status="available",
        remote_runtime_commit=worker.required_runtime_commit,
        asset_manifest_sha256=manifest.asset_manifest_sha256,
        device=descriptor.device_index if descriptor.device_index is not None else 0,
    )