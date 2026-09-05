from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AlgorithmLabCompareRequest(BaseModel):
    recording_id: str
    run_a_id: str
    run_b_id: str
    iou_threshold: float = 0.5

    @model_validator(mode="after")
    def validate_request(self) -> "AlgorithmLabCompareRequest":
        if self.run_a_id == self.run_b_id:
            raise ValueError("run_a_id and run_b_id must be distinct.")
        if self.iou_threshold != 0.5:
            raise ValueError("M8 supports only iou_threshold = 0.5.")
        return self


class DetectionMetricsRead(BaseModel):
    tp: int
    fp: int
    fn: int
    precision: float
    recall: float
    f1: float
    mean_matched_iou: float | None


class PhysicalBoxRead(BaseModel):
    t_start_s: float
    t_end_s: float
    f_low_hz: float
    f_high_hz: float


class RunMatchStateRead(BaseModel):
    matched: bool
    detection_id: str | None = None
    iou: float | None = None
    class_id: int | None = None
    class_name: str | None = None
    confidence: float | None = None
    class_correct: bool | None = None
    bbox: PhysicalBoxRead | None = None


class CaseRead(BaseModel):
    ground_truth_id: str
    class_id: int
    class_name: str
    bbox: PhysicalBoxRead
    comparison: Literal["both_detected", "a_only", "b_only", "both_missed"]
    run_a: RunMatchStateRead
    run_b: RunMatchStateRead


class ClassificationConfusionRead(BaseModel):
    gt_class_id: int
    gt_class_name: str
    pred_class_id: int
    pred_class_name: str
    count: int


class ClassificationMetricsRead(BaseModel):
    matched_count: int
    class_correct: int
    class_wrong: int
    matched_accuracy: float | None
    confusions: list[ClassificationConfusionRead]


class ClassAwareMetricsRead(BaseModel):
    tp: int
    fp: int
    fn: int
    precision: float
    recall: float
    f1: float


class RunComparisonRead(BaseModel):
    run_id: str
    pipeline_id: str
    pipeline_name: str
    metrics: DetectionMetricsRead
    classification_applicable: bool
    classification_reason: str | None = None
    classification: ClassificationMetricsRead | None = None
    class_aware: ClassAwareMetricsRead | None = None


class AlgorithmLabCompareResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    recording_id: str
    iou_threshold: float
    run_a: RunComparisonRead
    run_b: RunComparisonRead
    cases: list[CaseRead]