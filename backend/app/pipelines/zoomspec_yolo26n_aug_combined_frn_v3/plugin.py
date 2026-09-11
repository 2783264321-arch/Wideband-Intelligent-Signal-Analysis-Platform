"""E1 ZoomSpec plugin runtime factory.

Lightweight declarative module: importing it must NOT import torch/ultralytics
or construct any model. Heavy/scientific imports happen only inside
``build_runtime`` (and deeper, inside ``ZoomSpecFrozenPipeline``).

The factory consumes the verified logical assets supplied by
``PluginItemExecutor`` (``detector_checkpoint``, ``frn_checkpoint``,
``ls_stft_normalization``), the deployment-owned ``RuntimeDescriptor``, and the
resolved ``output_label_space``; it delegates all science to the existing
``ZoomSpecFrozenPipeline``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.errors import PlatformError
from app.pipelines.base import PipelineOutput, RecordingInput
from app.pipelines.plugin import PluginDeclaration
from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.definition import (
    ZOOMSPEC_FROZEN_DEFINITION,
)

_RUNTIME_FACTORY_REF = (
    "app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.plugin:build_runtime"
)

PLUGIN = PluginDeclaration(
    definition=ZOOMSPEC_FROZEN_DEFINITION,
    runtime_factory_ref=_RUNTIME_FACTORY_REF,
)


def _asset_mismatch(message: str) -> PlatformError:
    return PlatformError("PIPELINE_ASSET_MISMATCH", message)


def _require_asset(assets: Any, logical_name: str) -> Path:
    if not isinstance(assets, dict) and not hasattr(assets, "get"):
        raise _asset_mismatch("Verified asset mapping is not configured.")
    raw = assets.get(logical_name)
    if raw is None:
        raise _asset_mismatch(f"Missing required asset '{logical_name}'.")
    try:
        return Path(raw)
    except (TypeError, ValueError) as exc:
        raise _asset_mismatch(f"Asset '{logical_name}' is not a valid path.") from exc


def _load_normalization(path: Path):
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.preprocessing import (
        LSSTFTNormalization,
    )

    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise _asset_mismatch("ls_stft_normalization asset is invalid.") from exc
    try:
        return LSSTFTNormalization(
            percentile_low=float(payload["percentile_low"]),
            percentile_high=float(payload["percentile_high"]),
            value_low=float(payload["value_low"]),
            value_high=float(payload["value_high"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise _asset_mismatch("ls_stft_normalization asset is invalid.") from exc


def _require_device_index(runtime_descriptor) -> int:
    """ZoomSpec's E1 factory is bound to its declared/certified remote capability.

    Fails closed on any descriptor other than remote_gpu/cuda/float16 with an
    explicit non-negative integer device index. Never assumes/defaults device 0.
    """
    if runtime_descriptor is None:
        raise PlatformError("EXECUTOR_UNAVAILABLE", "ZoomSpec requires a runtime descriptor.")
    if (
        getattr(runtime_descriptor, "executor", None) != "remote_gpu"
        or getattr(runtime_descriptor, "device_type", None) != "cuda"
        or getattr(runtime_descriptor, "precision", None) != "float16"
    ):
        raise PlatformError(
            "EXECUTOR_UNAVAILABLE",
            "ZoomSpec requires the certified remote_gpu/cuda/float16 runtime descriptor.",
        )
    index = getattr(runtime_descriptor, "device_index", None)
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        raise PlatformError(
            "EXECUTOR_UNAVAILABLE",
            "ZoomSpec requires an explicit non-negative integer CUDA device index.",
        )
    return index


class _ZoomSpecRuntime:
    """PluginRuntime adapter delegating to the frozen scientific pipeline."""

    def __init__(self, pipeline) -> None:
        self._pipeline = pipeline

    def execute(
        self,
        recording: RecordingInput,
        parameters: dict[str, Any],
        workspace: Path,
    ) -> PipelineOutput:
        return self._pipeline.run(recording, parameters, workspace)


def build_runtime(*, assets, runtime_descriptor, output_label_space) -> _ZoomSpecRuntime:
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.pipeline import (
        ZoomSpecFrozenPipeline,
    )

    device_index = _require_device_index(runtime_descriptor)
    detector_checkpoint = _require_asset(assets, "detector_checkpoint")
    frn_checkpoint = _require_asset(assets, "frn_checkpoint")
    normalization = _load_normalization(_require_asset(assets, "ls_stft_normalization"))

    pipeline = ZoomSpecFrozenPipeline(
        detector_checkpoint_path=detector_checkpoint,
        frn_checkpoint_path=frn_checkpoint,
        normalization=normalization,
        label_space=output_label_space,
        device=device_index,
    )
    return _ZoomSpecRuntime(pipeline)
