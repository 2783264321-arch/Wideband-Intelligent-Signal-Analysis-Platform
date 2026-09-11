"""M9.2 F-A1 CPN bandwidth-tier plugin runtime factory.

Lightweight declarative module: importing it must NOT import torch/ultralytics or
construct any model. Heavy/scientific imports happen only inside ``build_runtime``
(and deeper, inside the frozen LS-STFT/CPN implementation).

The factory consumes the verified logical assets supplied by the generic
executor (``detector_checkpoint``, ``ls_stft_normalization``), the
deployment-owned ``RuntimeDescriptor``, and the resolved ``output_label_space``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.errors import PlatformError
from app.pipelines.base import PipelineOutput, RecordingInput
from app.pipelines.cpn_bandwidth_tier.definition import CPN_BANDWIDTH_TIER_DEFINITION
from app.pipelines.plugin import PluginDeclaration

_RUNTIME_FACTORY_REF = "app.pipelines.cpn_bandwidth_tier.plugin:build_runtime"

PLUGIN = PluginDeclaration(
    definition=CPN_BANDWIDTH_TIER_DEFINITION,
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


def _require_device(runtime_descriptor):
    """Resolve the scientific device from the deployment descriptor.

    The CPN plugin declares exactly two technical execution capabilities and this
    factory matches that claim (technical executability is NOT certification):

    - ``remote_gpu``/``cuda``/``float16`` with an explicit non-negative integer
      ``device_index`` -> that CUDA index (int).
    - ``local_cpu``/``cpu``/``float32`` with a canonical ``device_index=None`` ->
      the ``"cpu"`` device string.

    Any other / crossed tuple fails closed with ``EXECUTOR_UNAVAILABLE``.
    """
    if runtime_descriptor is None:
        raise PlatformError("EXECUTOR_UNAVAILABLE", "CPN bandwidth tier requires a runtime descriptor.")
    executor = getattr(runtime_descriptor, "executor", None)
    device_type = getattr(runtime_descriptor, "device_type", None)
    precision = getattr(runtime_descriptor, "precision", None)
    index = getattr(runtime_descriptor, "device_index", None)

    if (executor, device_type, precision) == ("remote_gpu", "cuda", "float16"):
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            raise PlatformError(
                "EXECUTOR_UNAVAILABLE",
                "remote_gpu requires an explicit non-negative integer CUDA device index.",
            )
        return index
    if (executor, device_type, precision) == ("local_cpu", "cpu", "float32"):
        if index is not None:
            raise PlatformError(
                "EXECUTOR_UNAVAILABLE",
                "local_cpu requires the canonical CPU descriptor (device_index is null).",
            )
        return "cpu"
    raise PlatformError(
        "EXECUTOR_UNAVAILABLE",
        "CPN bandwidth tier supports only remote_gpu/cuda/float16 or "
        "local_cpu/cpu/float32 runtime descriptors.",
    )


class _CPNRuntime:
    """PluginRuntime adapter delegating to the CPN scientific composition."""

    def __init__(self, pipeline) -> None:
        self._pipeline = pipeline

    def execute(
        self,
        recording: RecordingInput,
        parameters: dict[str, Any],
        workspace: Path,
    ) -> PipelineOutput:
        return self._pipeline.run(recording, parameters, workspace)


def build_runtime(*, assets, runtime_descriptor, output_label_space) -> _CPNRuntime:
    from app.pipelines.cpn_bandwidth_tier.pipeline import (
        CPNBandwidthTierPipeline,
        validate_output_label_space,
    )

    device = _require_device(runtime_descriptor)
    validate_output_label_space(output_label_space)
    detector_checkpoint = _require_asset(assets, "detector_checkpoint")
    normalization = _load_normalization(_require_asset(assets, "ls_stft_normalization"))

    pipeline = CPNBandwidthTierPipeline(
        detector_checkpoint_path=detector_checkpoint,
        normalization=normalization,
        label_space=output_label_space,
        device=device,
    )
    return _CPNRuntime(pipeline)
