"""M9.1 Task 12E — frozen full scientific pipeline composition.

Composes the accepted native stages into the frozen
``zoomspec_yolo26n_aug_combined_frn_v3`` pipeline:

    RecordingInput
    -> whole complex64 IQ (RecordingReader)
    -> build_ls_stft_spectrogram
    -> CPNDetector.detect_batch (one recording = one spectrogram = live batch=1)
    -> for each CPNProposal: AHLP purify_candidate
    -> FRNRefiner.refine_batch (all candidates for the recording, chunks <=64)
    -> postprocess_detections (score threshold >= 0.001, class-aware TF NMS 0.7)
    -> class_id -> class_name via injected LabelSpace
    -> DetectionPayload[]
    -> PipelineOutput

Frozen scientific parameters are internal constants and are NOT caller
configurable: any non-empty ``parameters`` is rejected.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from app.pipelines.base import (
    DetectionPayload,
    Pipeline,
    PipelineDefinition,
    PipelineOutput,
    RecordingInput,
)
from app.labels.service import LabelSpace
from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.ahlp import (
    purify_candidate,
    PurifiedCandidate,
)
from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.detector import (
    CPNDetector,
    CPNProposal,
)
from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.frn import FRNRefiner
from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.postprocess import (
    postprocess_detections,
    _SCORE_THRESHOLD,
)
from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.preprocessing import (
    build_ls_stft_spectrogram,
    LSSTFTNormalization,
)
from app.recordings.reader import read_segment_from_path

_PIPELINE_ID = "zoomspec_yolo26n_aug_combined_frn_v3"
_PIPELINE_VERSION = "1.0.0"
_FRN_BATCH_SIZE = 64
_CPN_BATCH_SIZE = 16  # reserved default; live call uses actual batch=1


@dataclass(frozen=True)
class _CompositionResult:
    payloads: list[DetectionPayload]
    frn_valid_count: int
    score_threshold_survivor_count: int
    post_nms_count: int


class ZoomSpecFrozenPipeline(Pipeline):
    def __init__(
        self,
        *,
        detector_checkpoint_path: Path,
        frn_checkpoint_path: Path,
        normalization: LSSTFTNormalization,
        label_space: LabelSpace,
        device: int | str,
    ) -> None:
        if label_space.id != "spacenet_14":
            raise ValueError("ZoomSpec frozen pipeline requires label_space spacenet_14")
        class_ids = sorted(c.id for c in label_space.classes)
        if class_ids != list(range(14)):
            raise ValueError("ZoomSpec frozen pipeline requires class ids 0..13")
        self._label_space = label_space
        self._normalization = normalization
        self._device = device
        self._detector = CPNDetector(detector_checkpoint_path, device=device)
        self._frn = FRNRefiner(frn_checkpoint_path, device=device)
        self._name_by_id = {c.id: c.name for c in label_space.classes}

    @property
    def definition(self) -> PipelineDefinition:
        return PipelineDefinition(
            id=_PIPELINE_ID,
            name="ZoomSpec YOLO26n + Combined FRN V3",
            version=_PIPELINE_VERSION,
            label_space="spacenet_14",
            recommended_device="GPU",
            cpu_supported=False,
            stages=("ls_stft", "cpn", "ahlp", "frn", "postprocess"),
            inspectable_stages=(),
            task_capability="detection_classification",
            executors_supported=("remote_gpu",),
            recommended_executor="remote_gpu",
        )

    def run(
        self,
        recording: RecordingInput,
        parameters: dict[str, Any],
        workspace: Path,
    ) -> PipelineOutput:
        if parameters:
            raise ValueError("ZoomSpec frozen pipeline does not accept parameters")
        workspace.mkdir(parents=True, exist_ok=True)
        iq = read_segment_from_path(recording.data_path, recording.data_format)
        spectrogram = build_ls_stft_spectrogram(
            iq,
            sample_rate_hz=recording.sample_rate_hz,
            center_frequency_hz=recording.center_frequency_hz,
            frequency_low_hz=recording.frequency_low_hz,
            frequency_high_hz=recording.frequency_high_hz,
            normalization=self._normalization,
            device=str(self._device) if not isinstance(self._device, int) else "cuda",
        )
        proposals = self._detector.detect_batch([spectrogram], batch_size=1)[0]
        comp = self._compose_from_proposals(iq, recording, proposals)
        return PipelineOutput(
            detections=comp.payloads,
            artifacts=[],
            run_metadata={
                "kind": _PIPELINE_ID,
                "task_capability": "detection_classification",
                "cpn_proposal_count": len(proposals),
                "frn_valid_count": comp.frn_valid_count,
                "score_threshold_survivor_count": comp.score_threshold_survivor_count,
                "post_nms_count": comp.post_nms_count,
            },
        )

    def _refine_from_proposals(
        self,
        iq: np.ndarray,
        recording: RecordingInput,
        proposals: Sequence[CPNProposal],
    ) -> list[DetectionPayload]:
        """Private test seam: refine frozen/external CPN proposals into
        DetectionPayloads (used for exact frozen-oracle composition)."""
        return self._compose_from_proposals(iq, recording, proposals).payloads

    def _compose_from_proposals(
        self,
        iq: np.ndarray,
        recording: RecordingInput,
        proposals: Sequence[CPNProposal],
    ) -> _CompositionResult:
        """Refine frozen/external CPN proposals into DetectionPayloads and report
        the distinct scientific stage counts:
        - frn_valid_count: FRNRefinedDetection entries that are not None;
        - score_threshold_survivor_count: valid detections with confidence >= frozen
          0.001 (reuses the postprocess module's frozen threshold);
        - post_nms_count: final kept detections after class-aware TF NMS."""
        candidates: list[PurifiedCandidate] = [
            purify_candidate(
                iq,
                sample_rate_hz=recording.sample_rate_hz,
                center_frequency_hz=recording.center_frequency_hz,
                proposal=prop,
            )
            for prop in proposals
        ]
        refined = self._frn.refine_batch(
            candidates,
            recording_sample_rate_hz=recording.sample_rate_hz,
            recording_duration_s=recording.duration_s,
            frequency_low_hz=recording.frequency_low_hz,
            frequency_high_hz=recording.frequency_high_hz,
            batch_size=_FRN_BATCH_SIZE,
        )
        valid = [det for det in refined if det is not None]
        threshold_survivors = [det for det in valid if det.confidence >= _SCORE_THRESHOLD]
        kept = postprocess_detections(valid)

        payloads: list[DetectionPayload] = []
        for det in kept:
            payloads.append(
                DetectionPayload(
                    t_start_s=det.t_start_s,
                    t_end_s=det.t_end_s,
                    f_low_hz=det.f_low_hz,
                    f_high_hz=det.f_high_hz,
                    class_id=det.class_id,
                    class_name=self._name_by_id[det.class_id],
                    confidence=det.confidence,
                    scores={
                        "cpn": det.proposal.confidence,
                        "frn_signal": det.prediction.signal_probability,
                        "frn_class": det.prediction.class_probability,
                        "fused": det.confidence,
                    },
                )
            )
        return _CompositionResult(
            payloads=payloads,
            frn_valid_count=len(valid),
            score_threshold_survivor_count=len(threshold_survivors),
            post_nms_count=len(payloads),
        )
