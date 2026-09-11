"""CPN bandwidth-tier scientific composition (LS-STFT -> CPN detector only).

Reuses the accepted frozen scientific implementation
(``app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.preprocessing`` and
``...detector``) WITHOUT copying or modifying it. The composition stops at
physical CPN proposals mapped losslessly to ``DetectionPayload``:

    RecordingInput -> complex IQ -> LS-STFT -> CPNProposal[] -> DetectionPayload[]

No AHLP, no FRN, no ZoomSpec 14-class refinement, no ZoomSpec postprocessing.

Torch / Ultralytics are imported lazily by the frozen modules only when the
detector is actually constructed/executed; importing this module is ML-free.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.errors import PlatformError
from app.pipelines.base import (
    DetectionPayload,
    Pipeline,
    PipelineDefinition,
    PipelineOutput,
    RecordingInput,
)
from app.pipelines.cpn_bandwidth_tier.definition import CPN_BANDWIDTH_TIER_DEFINITION
from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.detector import CPNDetector
from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.preprocessing import (
    LSSTFTNormalization,
    build_ls_stft_spectrogram,
)
from app.recordings.reader import read_segment_from_path

_OUTPUT_LABEL_SPACE_ID = "cpn_bandwidth_tier_v1"
_REQUIRED_TIER_IDS = (0, 1, 2)


def _label_space_error(message: str) -> PlatformError:
    return PlatformError("OUTPUT_LABEL_SPACE_INVALID", message)


def validate_output_label_space(label_space: Any) -> None:
    """Fail closed unless the resolved output label space is exactly Tier 0/1/2.

    The runtime consumes the executor-supplied, resolved ``LabelSpace``; it never
    hard-codes class names independently.
    """
    if label_space is None or getattr(label_space, "id", None) != _OUTPUT_LABEL_SPACE_ID:
        raise _label_space_error(
            f"CPN bandwidth tier requires output label space '{_OUTPUT_LABEL_SPACE_ID}'."
        )
    class_ids = sorted(int(cls.id) for cls in label_space.classes)
    if class_ids != list(_REQUIRED_TIER_IDS):
        raise _label_space_error(
            "CPN bandwidth tier requires output label space class ids exactly 0,1,2."
        )


def _ls_stft_device(device: int | str) -> str:
    """LS-STFT device string (index 0 -> 'cuda'; N>0 -> 'cuda:N'; strings kept)."""
    if isinstance(device, int):
        return "cuda" if device == 0 else f"cuda:{device}"
    return str(device)


class CPNBandwidthTierPipeline(Pipeline):
    def __init__(
        self,
        *,
        detector_checkpoint_path: Path,
        normalization: LSSTFTNormalization,
        label_space: Any,
        device: int | str,
    ) -> None:
        validate_output_label_space(label_space)
        self._normalization = normalization
        self._device = device
        self._detector = CPNDetector(detector_checkpoint_path, device=device)
        self._name_by_id = {int(cls.id): cls.name for cls in label_space.classes}

    @property
    def definition(self) -> PipelineDefinition:
        return CPN_BANDWIDTH_TIER_DEFINITION

    def run(
        self,
        recording: RecordingInput,
        parameters: dict[str, Any],
        workspace: Path,
    ) -> PipelineOutput:
        if parameters:
            raise ValueError("CPN bandwidth tier pipeline does not accept parameters")
        workspace.mkdir(parents=True, exist_ok=True)
        iq = read_segment_from_path(recording.data_path, recording.data_format)
        spectrogram = build_ls_stft_spectrogram(
            iq,
            sample_rate_hz=recording.sample_rate_hz,
            center_frequency_hz=recording.center_frequency_hz,
            frequency_low_hz=recording.frequency_low_hz,
            frequency_high_hz=recording.frequency_high_hz,
            normalization=self._normalization,
            device=_ls_stft_device(self._device),
        )
        proposals = self._detector.detect_batch([spectrogram], batch_size=1)[0]
        detections = [
            DetectionPayload(
                t_start_s=proposal.t_start_s,
                t_end_s=proposal.t_end_s,
                f_low_hz=proposal.f_low_hz,
                f_high_hz=proposal.f_high_hz,
                class_id=int(proposal.bandwidth_tier),
                class_name=self._name_by_id[int(proposal.bandwidth_tier)],
                confidence=proposal.confidence,
                scores={"cpn": proposal.confidence},
            )
            for proposal in proposals
        ]
        return PipelineOutput(
            detections=detections,
            artifacts=[],
            run_metadata={
                "kind": CPN_BANDWIDTH_TIER_DEFINITION.id,
                "task_capability": CPN_BANDWIDTH_TIER_DEFINITION.task_capability,
                "cpn_proposal_count": len(proposals),
                "detection_count": len(detections),
            },
        )
