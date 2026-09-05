# M8.6A Dataset Benchmark Core

> Date: 2026-09-06
> Scope: backend-only core for reproducible dataset-level benchmarking. Batch Analysis Package and benchmark UI are **not** implemented in M8.6A.

## M8.6A scope

Implemented:

- `DatasetEvaluation` + `DatasetEvaluationItem` persistence (additive migration).
- Deterministic frozen Recording manifest + `recording_manifest_hash`.
- Explicit frozen `Recording -> AnalysisRun` membership (no hidden auto-selection).
- `physical_tf_detection_ap_v1` protocol.
- Confidence-ranked AP/mAP evaluator.
- Dataset operating-point diagnostics derived from M8.5 Hungarian matching.
- Background subprocess worker lifecycle (pending/running/completed/failed/interrupted, retry, startup recovery).
- Backend APIs for prepare / resolve-runs / create / list / get / items / run / retry / compare.
- Tiny end-to-end benchmark acceptance.

Not implemented in M8.6A (deferred to M8.6B / M8.6C):

- Batch Analysis Package v1 transport / importer.
- Import of the 2,500-sample historical SpaceNet result set.
- Algorithm Lab Dataset Benchmarks UI.
- Real 2,500-sample DatasetEvaluation.

## Protocol: `physical_tf_detection_ap_v1`

- Coordinates are physical seconds + absolute Hz.
- AP uses **confidence-ranked greedy matching**, not Hungarian matching.
- AP = 101-point interpolated precision (recall 0.00..1.00).
- IoU thresholds: 0.50, 0.55, ..., 0.95 (AP50:95 is the mean of exactly 10 thresholds).
- No benchmark-level confidence threshold; every stored `DetectionResult.confidence` is used.
- Deterministic tie-break: confidence desc → manifest_order → t_start_s → f_low_hz → t_end_s → f_high_hz → class_id. Detection UUIDs never rank predictions.

## M8.5 Hungarian vs M8.6 AP

- M8.5 `match_predictions()` (class-agnostic Hungarian one-to-one, IoU 0.5) is unchanged and used only for dataset operating-point diagnostics.
- M8.6 AP/mAP never uses Hungarian matching; the two are kept strictly separate.

## Frozen membership

- A DatasetEvaluation freezes an explicit `Recording -> AnalysisRun` map.
- Pipeline snapshot resolution classifies each Recording as `resolved` / `missing` / `ambiguous`; ambiguous runs are never auto-selected.
- New AnalysisRuns created after a freeze never alter an existing evaluation.
- Retry reuses the exact same items / run IDs / protocol / manifest hash.

## Detection-only N/A semantics

- Detection-only pipelines report localization metrics; classification and class-aware metrics are `None` (never `0`, `0%`, or `0.0`).
- Imported spacenet_14 runs are classification-applicable; unknown non-imported runs are `unknown_classification_semantics`.

## Worker lifecycle

- Statuses: `pending`, `running`, `completed`, `failed`, `interrupted`.
- Startup recovery marks stale `running` as `interrupted`.
- `failed` / `interrupted` may be retried with identical frozen membership; `completed` cannot be rerun in place.
- Formal result JSON is written only in one final transaction; a failed run exposes no partial formal metrics.