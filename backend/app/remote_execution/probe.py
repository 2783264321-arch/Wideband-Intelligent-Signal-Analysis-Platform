"""Generic production remote runner probe.

Verifies real server readiness WITHOUT loading any model or plugin runtime:

- deployed runtime commit equals the required runtime commit;
- the exact ModelRelease manifest-declared asset bytes match;
- the deployed dataset root required by the plugin's ``DatasetAdapter`` exists;
- the plugin's output/input label spaces are loadable;
- CUDA is available at ``descriptor.device_index``.

All plugin/release identity (definition, manifest, resolved assets) is supplied by
the generic plugin/release path; this module carries no ZoomSpec literals. Torch is
imported lazily only inside the CUDA check (the ``torch_import`` seam lets CPU
tests inject a fake). No scientific inference occurs.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.errors import PlatformError
from app.labels.service import LabelSpaceService
from app.remote_execution.assets import verify_assets, verify_remote_runtime_commit
from app.remote_execution.schema import RemoteProbeResponseV1
from app.remote_execution.worker_context import RemoteWorkerContext


def _probe_unavailable(message: str) -> PlatformError:
    return PlatformError("REMOTE_PROBE_UNAVAILABLE", message)


def _require_dataset_roots(worker: RemoteWorkerContext, definition) -> None:
    """The deployment must provide a root for every adapter the plugin declares.

    Dataset deployment is still SpaceNet-oriented (generalization is a later
    task); the only deployed root is the SpaceNet root.
    """
    if not definition.dataset_adapters:
        return
    root = worker.dataset_root_space_net
    if not root.exists() or not root.is_dir():
        raise _probe_unavailable("Required dataset root is not available on the remote runtime.")


def _require_label_spaces(worker: RemoteWorkerContext, definition) -> None:
    service = LabelSpaceService(worker.label_space_root)
    ids = [definition.resolved_output_label_space, *definition.input_compatibility]
    for label_space_id in dict.fromkeys(ids):
        service.get(label_space_id)


def _require_cuda_device(descriptor, torch_import: Any) -> None:
    index = descriptor.device_index
    if index is None or index < 0:
        raise _probe_unavailable("Remote CUDA device_index must be a configured non-negative integer.")
    torch = torch_import if torch_import is not None else __import__("torch")
    if not torch.cuda.is_available():
        raise _probe_unavailable("CUDA is not available on the remote runtime.")
    try:
        torch.cuda.get_device_name(index)
    except Exception:
        raise _probe_unavailable(f"CUDA device {index} is not usable on the remote runtime.")


def _require_accelerator(descriptor, torch_import: Any) -> None:
    """Descriptor-driven readiness: remote_gpu is CUDA-only in M9.2. A non-cuda
    descriptor fails closed; a CUDA descriptor probes its exact device_index."""
    if getattr(descriptor, "device_type", None) != "cuda":
        raise _probe_unavailable("remote_gpu is CUDA-only; non-cuda descriptors are unsupported.")
    _require_cuda_device(descriptor, torch_import)


def run_probe(
    worker: RemoteWorkerContext,
    *,
    descriptor,
    plugin_definition,
    manifest,
    assets: dict[str, Path],
    torch_import: Any = None,
) -> RemoteProbeResponseV1:
    """Fail-closed server readiness verification. Never loads models/runtime.

    ``manifest`` is the exact resolved ModelRelease manifest and ``assets`` the
    resolved logical -> path mapping for it; ``plugin_definition`` supplies the
    input/output label-space and dataset-adapter identities.
    """
    verify_remote_runtime_commit(worker.repo_root, worker.required_runtime_commit)
    verify_assets(manifest, dict(assets))
    _require_dataset_roots(worker, plugin_definition)
    _require_label_spaces(worker, plugin_definition)
    _require_accelerator(descriptor, torch_import)
    return RemoteProbeResponseV1(
        schema_version=1,
        status="available",
        remote_runtime_commit=worker.required_runtime_commit,
        asset_manifest_sha256=manifest.asset_manifest_sha256,
        device=descriptor.device_index,
    )
