from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PHYSICAL_TF_PROTOCOL = "physical_tf_detection_ap_v1"
IOU_THRESHOLDS_V1 = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95]
PROTOCOL_CONFIG_V1 = {
    "iou_thresholds": IOU_THRESHOLDS_V1,
    "ap_interpolation": "101_point_max_precision",
    "ap_recall_points": 101,
    "confidence_field": "DetectionResult.confidence",
    "diagnostic_matching": "hungarian_class_agnostic",
    "diagnostic_iou_threshold": 0.5,
    "ranking_tie_break": [
        "confidence_desc",
        "manifest_order",
        "t_start_s",
        "f_low_hz",
        "t_end_s",
        "f_high_hz",
        "class_id",
    ],
}


class DatasetSelection(BaseModel):
    dataset_name: str = Field(min_length=1)
    dataset_split: str = Field(min_length=1)
    label_space: str = Field(min_length=1)


class RunResolutionRequest(DatasetSelection):
    pipeline_id: str = Field(min_length=1)
    pipeline_version: str = Field(min_length=1)


class FrozenRunItemInput(BaseModel):
    recording_id: str
    analysis_run_id: str | None


class DatasetEvaluationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    dataset_name: str
    dataset_split: str
    label_space: str
    recording_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    allow_incomplete: bool = False
    items: list[FrozenRunItemInput]


class DatasetManifestEntryRead(BaseModel):
    manifest_order: int
    recording_id: str
    recording_name: str
    gt_count: int


class DatasetManifestPreviewRead(BaseModel):
    dataset_name: str
    dataset_split: str
    label_space: str
    recording_manifest_hash: str
    expected_recordings: int
    entries: list[DatasetManifestEntryRead]


class RunResolutionEntryRead(BaseModel):
    manifest_order: int
    recording_id: str
    recording_name: str
    resolution: Literal["resolved", "missing", "ambiguous"]
    candidate_run_ids: list[str]


class RunResolutionPreviewRead(BaseModel):
    dataset_name: str
    dataset_split: str
    label_space: str
    pipeline_id: str
    pipeline_version: str
    recording_manifest_hash: str
    entries: list[RunResolutionEntryRead]


class DatasetEvaluationItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    evaluation_id: str
    manifest_order: int
    recording_id: str
    analysis_run_id: str | None
    status: str
    gt_count: int
    prediction_count: int
    error_reason: str | None


class DatasetEvaluationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    dataset_name: str
    dataset_split: str
    label_space: str
    pipeline_id: str
    pipeline_version: str
    status: str
    expected_recordings: int
    evaluated_recordings: int
    missing_recordings: int
    coverage: float
    comparable: bool
    recording_manifest_hash: str
    evaluation_protocol: str
    protocol_config_json: dict
    aggregate_metrics_json: dict | None
    per_class_metrics_json: list[dict] | None
    confusion_json: list[dict] | None
    progress_stage: str | None
    progress_current: int | None
    progress_total: int | None
    worker_pid: int | None
    error_type: str | None
    error_message: str | None
    created_at: object | None = None
    started_at: object | None = None
    completed_at: object | None = None


class DatasetBenchmarkCompareRequest(BaseModel):
    evaluation_a_id: str
    evaluation_b_id: str


class DatasetBenchmarkCompareResponse(BaseModel):
    comparable: bool
    reasons: list[str]
    evaluation_a_id: str
    evaluation_b_id: str
    aggregate_a: dict | None
    aggregate_b: dict | None
    deltas: dict[str, float | None]