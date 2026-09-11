"""Production remote ZoomSpec item executor (Task 12F-B Task 5).

Implements the existing ``ItemExecutor`` protocol (``execute(item, job_root)``)
and is constructed only on the remote work path. Batch-wide immutable fields
are injected in the constructor after loading the frozen batch.

Scientific identity comes from ``batch.pipeline`` (id/version must match the
frozen ``ZOOMSPEC_FROZEN_DEFINITION``); ``item.recording`` supplies only the
dataset/split/key/label-space and the double identity (fingerprint + source
hash). GroundTruth participates only in fingerprint verification and never
reaches inference.

GPU libraries are imported lazily INSIDE ``execute``/``default_runtime_info_provider``;
importing this module stays torch/ultralytics-free.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.core.errors import PlatformError
from app.labels.service import LabelSpaceService
from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.definition import ZOOMSPEC_FROZEN_DEFINITION
from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.preprocessing import LSSTFTNormalization
from app.remote_execution.assets import verify_asset_manifest
from app.remote_execution.package_publisher import build_analysis_package_zip
from app.remote_execution.result_publisher import publish_result
from app.remote_execution.resolver import resolve_space_net
from app.remote_execution.runtime import RuntimeDescriptor
from app.remote_execution.schema import RemoteExecutionBatchV1, RemoteExecutionItemV1
from app.remote_execution.worker_context import RemoteWorkerContext

_DEVICE_INDEX = 0


def _load_normalization(path: Path) -> LSSTFTNormalization:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise PlatformError("PIPELINE_ASSET_MISMATCH", "ls_stft_normalization asset is invalid.")
    try:
        return LSSTFTNormalization(
            percentile_low=float(payload["percentile_low"]),
            percentile_high=float(payload["percentile_high"]),
            value_low=float(payload["value_low"]),
            value_high=float(payload["value_high"]),
        )
    except (KeyError, TypeError, ValueError):
        raise PlatformError("PIPELINE_ASSET_MISMATCH", "ls_stft_normalization asset is invalid.")


def _default_pipeline_factory(
    worker: RemoteWorkerContext,
    normalization: LSSTFTNormalization,
    label_space,
    device: int,
):
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.pipeline import ZoomSpecFrozenPipeline
    return ZoomSpecFrozenPipeline(
        detector_checkpoint_path=worker.detector_checkpoint,
        frn_checkpoint_path=worker.frn_checkpoint,
        normalization=normalization,
        label_space=label_space,
        device=device,
    )


def default_runtime_info_provider() -> dict:
    """Production provider; imports torch lazily INSIDE this call."""
    import torch
    return {
        "device_index": _DEVICE_INDEX,
        "device_type": "cuda",
        "device_name": torch.cuda.get_device_name(_DEVICE_INDEX),
        "torch_version": str(torch.__version__),
        "cuda_version": str(torch.version.cuda),
    }


class ZoomSpecRemoteItemExecutor:
    """Implements the existing ItemExecutor protocol (execute(item, job_root))."""

    def __init__(
        self,
        *,
        batch: RemoteExecutionBatchV1,
        worker: RemoteWorkerContext,
        pipeline_factory=None,
        runtime_info_provider=None,
    ) -> None:
        self._batch = batch
        self._worker = worker
        self._pipeline_factory = pipeline_factory or _default_pipeline_factory
        self._runtime_info_provider = runtime_info_provider or default_runtime_info_provider

    def execute(self, item: RemoteExecutionItemV1, job_root: Path) -> None:
        remote_started_at = datetime.now(timezone.utc)
        worker = self._worker
        batch = self._batch

        # Frozen runtime provenance guard (defense in depth, BEFORE any
        # assets/resolver/model work): the frozen request must target this
        # worker deployment's runtime.
        if batch.required_remote_runtime_commit != worker.required_runtime_commit:
            raise PlatformError(
                "REMOTE_IMPLEMENTATION_MISMATCH",
                "Frozen request runtime does not match the worker deployment.",
            )

        manifest = verify_asset_manifest(
            worker.asset_manifest_path,
            {
                "detector_checkpoint": worker.detector_checkpoint,
                "frn_checkpoint": worker.frn_checkpoint,
                "frozen_config": worker.frozen_config_path,
                "ls_stft_normalization": worker.ls_stft_normalization_path,
            },
            worker.repo_root,
            worker.required_runtime_commit,
        )
        if batch.pipeline.id != ZOOMSPEC_FROZEN_DEFINITION.id:
            raise PlatformError(
                "REMOTE_IMPLEMENTATION_MISMATCH",
                "Batch pipeline id does not match the frozen ZoomSpec pipeline.",
            )
        if batch.pipeline.version != ZOOMSPEC_FROZEN_DEFINITION.version:
            raise PlatformError(
                "REMOTE_IMPLEMENTATION_MISMATCH",
                "Batch pipeline version does not match the frozen ZoomSpec pipeline.",
            )
        if manifest.asset_manifest_sha256 != batch.asset_manifest_sha256:
            raise PlatformError(
                "PIPELINE_ASSET_MISMATCH",
                "Deployed asset manifest does not match the frozen batch.",
            )
        if item.parameters != {}:
            raise PlatformError(
                "REMOTE_REQUEST_INVALID",
                "Frozen ZoomSpec execution accepts no parameters.",
            )

        recording = item.recording
        # Frozen SpaceNet recording contract (BEFORE any resolver/model work).
        if recording.dataset_name != "SpaceNet":
            raise PlatformError(
                "REMOTE_REQUEST_INVALID",
                "Frozen ZoomSpec execution requires the SpaceNet dataset.",
            )
        if recording.label_space != ZOOMSPEC_FROZEN_DEFINITION.label_space:
            raise PlatformError(
                "REMOTE_REQUEST_INVALID",
                "Frozen ZoomSpec execution requires the spacenet_14 label space.",
            )

        resolved = resolve_space_net(
            worker.dataset_root_space_net,
            recording.dataset_split,
            recording.dataset_key,
            recording.label_space,
            recording.expected_recording_fingerprint,
            recording.expected_source_data_sha256,
            worker.label_space_root,
        )

        label_space = LabelSpaceService(worker.label_space_root).get(
            ZOOMSPEC_FROZEN_DEFINITION.label_space
        )
        normalization = _load_normalization(worker.ls_stft_normalization_path)
        pipeline = self._pipeline_factory(worker, normalization, label_space, _DEVICE_INDEX)

        workspace = Path(job_root) / "work" / item.item_key
        output = pipeline.run(resolved.recording_input, {}, workspace)

        runtime_info = self._runtime_info_provider()
        zip_path = build_analysis_package_zip(
            output,
            pipeline_definition=ZOOMSPEC_FROZEN_DEFINITION,
            label_space=ZOOMSPEC_FROZEN_DEFINITION.resolved_output_label_space,
            runtime_descriptor=RuntimeDescriptor(
                executor="remote_gpu",
                device_type="cuda",
                device_index=_DEVICE_INDEX,
                precision="float16",
            ),
            parameters=item.parameters,
            recording_name=recording.dataset_key,
            dataset_name=recording.dataset_name,
            workspace=workspace,
        )

        remote_finished_at = datetime.now(timezone.utc)
        if remote_finished_at < remote_started_at:
            raise PlatformError(
                "REMOTE_RESULT_INVALID",
                "remote_finished_at precedes remote_started_at.",
            )
        publish_result(
            job_root=Path(job_root),
            item=item,
            batch=batch,
            zip_path=zip_path,
            remote_runtime_commit=worker.required_runtime_commit,
            asset_manifest_sha256=manifest.asset_manifest_sha256,
            hardware=runtime_info,
            remote_started_at=remote_started_at,
            remote_finished_at=remote_finished_at,
        )