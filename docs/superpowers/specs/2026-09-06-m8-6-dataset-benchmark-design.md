# M8.6 Dataset-Level Batch Benchmark — Design Spec

Date: 2026-09-06  
Status: Design approved in chat; awaiting written-spec review  
Shared baseline: `feature/v1-core @ f2c28b3b13e3ecfdaf5f366d23ca90d758cd1c4d`

## 1. Purpose

M8.6 adds reproducible, dataset-level benchmarking to the existing offline wideband signal analysis platform. It complements M8.5 rather than replacing it:

- M8.5 answers **why a specific Recording/run succeeded or failed** using class-agnostic physical time-frequency Hungarian matching at IoU 0.5.
- M8.6 answers **how strong a complete Pipeline is over a frozen dataset split** using confidence-ranked AP/mAP plus dataset-level diagnostics.

The platform must remain architecture-agnostic. Benchmark code consumes only standard `Recording`, `GroundTruth`, `AnalysisRun`, and `DetectionResult` semantics. It must not know YOLO, RT-DETR, AHLP, FRN, ZoomSpec, or any future model internals.

## 2. Goals

M8.6 V1 must provide:

1. Frozen dataset membership and frozen `Recording -> AnalysisRun` membership.
2. Reproducible dataset-level evaluation with coverage accounting.
3. Physical time-frequency localization AP50 and AP50:95.
4. Class-aware per-class AP, mAP50, and mAP50:95 for classification-capable pipelines.
5. Dataset-level operating-point localization and class-aware P/R/F1.
6. Dataset-level matched-classification accuracy and confusion aggregation.
7. Background benchmark execution using the existing local subprocess pattern.
8. Batch transport of external results without introducing a second runtime result model.
9. Algorithm Lab UI for benchmark list, detail, per-class metrics, lightweight comparison, and drill-down.
10. A real 2,500-sample SpaceNet benchmark using existing historical predictions as the first conformance case.

## 3. Non-goals

M8.6 V1 does not add:

- a generic `Dataset` domain entity;
- `BatchRun`, `ModelRun`, `TrainingRun`, or a generic experiment-management system;
- Celery, Redis, Postgres, or a distributed queue;
- live GPU inference or model training;
- confidence-threshold sweep, PR-curve plotting, or large interactive confusion heatmaps;
- model-specific evaluation code;
- replacement of M8.5 single-Recording diagnostics.

## 4. Architectural invariants

1. **Pipeline execution remains `AnalysisRun`.**
2. **Batch transport is not a runtime domain model.** A Batch Analysis Package expands into normal `AnalysisRun` rows.
3. **DatasetEvaluation is evaluation, not execution.** It aggregates frozen existing runs.
4. **Algorithms adapt to the platform contract.** The evaluator never branches on architecture family.
5. **Physical coordinates remain seconds and absolute Hz.** No pixel or normalized-coordinate evaluation is allowed.
6. **Completed benchmark results are immutable.** Different predictions/configuration require a new DatasetEvaluation.
7. **No hidden run selection.** Ambiguous candidate runs are never resolved by “latest”, “oldest”, or another implicit rule.

## 5. Domain model

### 5.1 DatasetEvaluation

`DatasetEvaluation` is the persistent benchmark record.

Recommended fields:

- `id`
- `name`
- `dataset_name`
- `dataset_split`
- `label_space`
- `pipeline_id`
- `pipeline_version`
- `status`: `pending | running | completed | failed | interrupted`
- `expected_recordings`
- `evaluated_recordings`
- `missing_recordings`
- `coverage`
- `comparable`
- `recording_manifest_hash`
- `evaluation_protocol`
- `protocol_config_json`
- `aggregate_metrics_json`
- `per_class_metrics_json`
- `confusion_json`
- `progress_stage`
- `progress_current`
- `progress_total`
- `worker_pid`
- `error_type`
- `error_message`
- `created_at`
- `started_at`
- `completed_at`

`evaluation_protocol` for M8.6 V1 is fixed to:

`physical_tf_detection_ap_v1`

### 5.2 DatasetEvaluationItem

One row exists for each Recording in the frozen dataset manifest.

Recommended fields:

- `id`
- `evaluation_id`
- `manifest_order`
- `recording_id`
- `analysis_run_id` nullable for incomplete benchmarks
- `status`: `included | missing_run | invalid`
- `gt_count`
- `prediction_count`
- `error_reason`

The item table is the authoritative frozen run manifest. Reopening a benchmark never re-resolves run membership.

### 5.3 No Dataset table in V1

The existing `Recording` fields `dataset_name`, `dataset_split`, and `label_space` are sufficient for M8.6 V1 dataset selection. A dedicated Dataset entity is deferred until real requirements exceed these fields.

## 6. Frozen Recording manifest

### 6.1 Dataset selection

A benchmark begins by selecting:

- `dataset_name`
- `dataset_split`
- `label_space`

The platform resolves all matching Ground-Truth-bearing Recordings and freezes their deterministic order before selecting runs.

### 6.2 Stable identity and hash

`recording_manifest_hash` must be reproducible across machines and must not include local database IDs or local filesystem paths.

V1 canonical manifest entries should include stable semantic content such as:

- dataset name and split;
- `Recording.name`;
- sample rate;
- center frequency / frequency bounds;
- duration;
- label space;
- canonicalized Ground Truth annotations sorted deterministically.

This makes a changed annotation set produce a different manifest hash even if Recording names are unchanged.

V1 requires `Recording.name` to be unique within a selected `(dataset_name, dataset_split)` snapshot. Duplicate names make the snapshot invalid rather than silently ambiguous.

## 7. Run membership resolution

Every completed DatasetEvaluation is based on an explicit frozen mapping:

`Recording -> AnalysisRun`

M8.6 V1 supports three resolution sources.

### 7.1 Explicit run set

The caller supplies the exact run ID for each Recording. This is the strictest and most reproducible mode.

### 7.2 Imported batch result set

A successful Batch Analysis Package import returns the exact mapping of imported Recording to newly created AnalysisRun. A benchmark can freeze that mapping directly.

This is the preferred first path for the historical 2,500-sample SpaceNet result set.

### 7.3 Pipeline snapshot helper

A convenience resolver may scan by `pipeline_id + pipeline_version` over the frozen Recording manifest.

For every Recording it classifies candidate resolution as:

- exactly one completed run -> uniquely resolved;
- zero completed runs -> missing;
- more than one completed run -> ambiguous.

Ambiguous entries are never auto-selected. The caller must resolve them explicitly or choose another run source.

### 7.4 Freeze semantics

Once DatasetEvaluationItems are created, newly created runs never alter an existing benchmark. A different run set means a new DatasetEvaluation.

## 8. Coverage and comparability

Coverage is a first-class metric:

- `expected_recordings`
- `evaluated_recordings`
- `missing_recordings`
- `coverage = evaluated / expected`

Incomplete benchmarks are allowed for debugging, but must be marked `comparable = false`.

Two completed DatasetEvaluations are directly comparable only when all of the following are equal/true:

- same `dataset_name`;
- same `dataset_split`;
- same `label_space`;
- same `recording_manifest_hash`;
- same `evaluation_protocol` and protocol configuration;
- both have 100% coverage.

Pipeline IDs and versions may differ because cross-pipeline comparison is the purpose of the feature.

## 9. Evaluation protocol: `physical_tf_detection_ap_v1`

### 9.1 Physical time-frequency IoU

A box is:

`[t_start, t_end] x [f_low, f_high]`

Intersection area is time-overlap multiplied by frequency-overlap. Union is the sum of areas minus intersection. IoU is dimensionless. Coordinates remain seconds and absolute Hz.

### 9.2 Distinction from M8.5 matching

M8.5 retains its existing class-agnostic Hungarian one-to-one matching at IoU 0.5 for case-level diagnostics.

M8.6 AP evaluation uses confidence-ranked greedy matching. Hungarian matching must not be used to compute AP/mAP.

### 9.3 Confidence ordering

AP uses `DetectionResult.confidence` only. Component scores such as proposal score, signal probability, classifier probability, or energy margin are invisible to the benchmark layer.

Predictions are sorted by:

1. confidence descending;
2. frozen Recording manifest order;
3. `t_start_s`;
4. `f_low_hz`;
5. `t_end_s`;
6. `f_high_hz`;
7. `class_id` when relevant.

The evaluator must not rely on DetectionResult UUID ordering.

No additional benchmark-level confidence threshold is applied. All final detections already produced by the Pipeline/Adapter are evaluated.

### 9.4 Greedy matching at one IoU threshold

For each prediction in ranked order:

1. consider GT only from the same Recording;
2. consider only GT not already matched at this IoU threshold;
3. choose the eligible GT with maximum IoU;
4. mark TP if IoU is greater than or equal to the current threshold, else FP.

Each GT can match at most once per threshold.

### 9.5 AP interpolation

AP uses 101-point interpolated precision at recall levels `0.00, 0.01, ..., 1.00`.

At recall level `r`:

`p_interp(r) = max precision at any operating point with recall >= r`

AP is the mean of the 101 interpolated precision values.

### 9.6 IoU thresholds

- AP50: IoU = 0.50
- AP50:95: mean AP over `0.50, 0.55, ..., 0.95` (10 thresholds)

### 9.7 Localization AP

Localization AP ignores class labels and treats all targets as one conceptual `Signal` class.

This yields:

- `localization_ap50`
- `localization_ap50_95`

Localization AP is applicable to detection-only and classification-capable pipelines.

### 9.8 Class-aware AP

For class-aware AP, predictions of class `c` may only match GT of class `c` in the same Recording.

For each GT-present class, compute:

- GT count;
- prediction count;
- AP50;
- AP50:95.

A class with zero GT has AP = null/N/A and is excluded from macro mAP, while any predictions for that class remain visible in prediction counts.

### 9.9 mAP

- `mAP50` = macro mean of valid per-class AP50 values.
- `mAP50_95` = macro mean across valid classes and the 10 IoU thresholds.

For classification-capable pipelines, `mAP50_95` is the primary dataset benchmark metric.

For detection-only pipelines, class-aware AP/mAP is N/A, never zero.

## 10. Dataset operating-point diagnostics

AP/mAP and operating-point metrics answer different questions and must be displayed separately.

### 10.1 Localization operating point

For each Recording, use the existing M8.5 class-agnostic Hungarian matching at IoU 0.5 and aggregate TP/FP/FN across the dataset. Derive Precision, Recall, and F1 from the aggregate counts.

### 10.2 Classification-on-matched

Using the same M8.5 localization pairs:

- `matched_count`
- `class_correct`
- `class_wrong`
- `matched_accuracy`
- aggregated wrong-class confusion pairs

If `matched_count == 0`, matched accuracy is null, not zero.

Detection-only pipelines report classification diagnostics as N/A.

### 10.3 Class-aware operating point

Using the same localization pairing:

- a localization pair with equal class ID contributes one class-aware TP;
- a localization pair with wrong class contributes one FP to the predicted class and one FN to the GT class;
- an unmatched prediction contributes one FP to its predicted class;
- an unmatched GT contributes one FN to its GT class.

Aggregate overall and per-class P/R/F1 from these counts.

This diagnostic does not replace AP/mAP.

## 11. Batch Analysis Package v1

### 11.1 Purpose

Batch Analysis Package v1 is a transport container for many standard Analysis Package v1 items. It does not create a `BatchRun` domain object.

### 11.2 Structure

Conceptual layout:

```text
batch_manifest.json
items/
  000000/
    manifest.json
    detections.json
  000001/
    manifest.json
    detections.json
  ...
```

Each child item remains semantically a valid single-Recording Analysis Package v1.

### 11.3 Batch manifest

The outer manifest contains at least:

- batch schema version;
- batch ID;
- common pipeline ID/name/version;
- label space;
- dataset name/split;
- expected item count;
- Recording manifest hash if known at export time;
- execution metadata;
- provenance such as code commit, config identity, checkpoint SHA256, and split-manifest SHA256;
- deterministic item list containing Recording identity hint and child path.

Server DB IDs are never trusted across machines.

### 11.4 Validation rules

The importer must reject:

- Zip Slip/path traversal;
- duplicate Recording items;
- duplicate child paths;
- missing child manifests/detections;
- child pipeline/version mismatch with batch manifest;
- child label-space mismatch;
- ambiguous or mismatched local Recording identity;
- invalid child Analysis Package schema;
- invalid bbox, confidence, label, or duplicate source detection ID;
- partial-invalid batch contents.

V1 safety bounds:

- maximum 10,000 batch items;
- maximum 1,000,000 total detections;
- existing per-child Analysis Package limits continue to apply.

### 11.5 All-or-nothing import

The importer performs:

1. safe extraction;
2. outer manifest validation;
3. local Recording resolution for every item;
4. full validation of every child package and detection;
5. duplicate/coverage/common-metadata validation;
6. only then one database import transaction.

If one item is invalid, zero AnalysisRuns are created.

### 11.6 Import result

Successful import creates ordinary `AnalysisRun` and `DetectionResult` rows and returns an import summary plus exact `Recording -> AnalysisRun` mapping.

`AnalysisRun.parameters_json` may store lightweight batch provenance such as:

- `batch_id`
- batch schema version
- source item path/key

No new `batch_id` database column is required in V1.

Batch import and benchmark creation remain separate user actions.

## 12. Background benchmark execution

### 12.1 Execution pattern

Benchmark computation uses the existing local subprocess pattern rather than synchronous HTTP or a new queue system.

Conceptual flow:

```text
POST create DatasetEvaluation
  -> pending
POST run
  -> spawn `python -m app.benchmarks.worker <evaluation_id>`
  -> running
  -> load frozen items
  -> compute diagnostics + AP/mAP
  -> atomically persist results
  -> completed
```

### 12.2 Module boundary

Recommended backend layout:

```text
backend/app/benchmarks/
  model.py
  schema.py
  service.py
  router.py
  job_manager.py
  worker.py
```

Generic evaluation math stays under `backend/app/evaluation/`, with a new AP-focused module such as `ap.py`.

### 12.3 No generic Job entity

`DatasetEvaluation.status` is the persistent job state. M8.6 does not introduce a generic task queue or Job database model.

### 12.4 Lifecycle

Statuses:

- `pending`
- `running`
- `completed`
- `failed`
- `interrupted`

On application restart, stale benchmark rows left as `running` without a live worker are marked `interrupted` using the same lifecycle principle already used by AnalysisRun recovery.

### 12.5 Retry

`failed` and `interrupted` evaluations may be retried.

Retry reuses exactly the same frozen DatasetEvaluationItems and protocol. It never re-resolves runs.

`completed` evaluations cannot be rerun in place; changed inputs require a new DatasetEvaluation.

### 12.6 Atomic result persistence

The worker computes and validates all result structures before writing final aggregate/per-class/confusion results and setting `completed` in one transaction.

A failed run must not expose partial formal metrics as completed results.

### 12.7 Progress

V1 stores lightweight progress:

- `progress_stage`
- optional `progress_current`
- optional `progress_total`

Suggested stages:

- loading
- diagnostics
- localization_ap
- class_aware_ap
- finalizing

The UI should prefer truthful stage labels over fake precision percentages.

## 13. API surface

Exact route naming may follow repository conventions, but the semantic operations are:

- prepare/preview dataset Recording manifest;
- resolve/preview run membership;
- create frozen DatasetEvaluation;
- list DatasetEvaluations;
- get DatasetEvaluation detail;
- list DatasetEvaluationItems;
- start benchmark;
- retry failed/interrupted benchmark;
- lightweight compare of two comparable evaluations;
- import Batch Analysis Package and return summary + run mapping.

Preparation APIs must expose missing and ambiguous runs before freezing.

## 14. Algorithm Lab UI

M8.6 stays inside Algorithm Lab rather than adding a new top-level navigation item.

Suggested navigation:

- Run Comparison (M8.5)
- Dataset Benchmarks (M8.6)

### 14.1 Benchmark list

Show at least:

- pipeline/name/version;
- dataset/split;
- coverage;
- localization AP50/AP50:95;
- class-aware mAP50/mAP50:95 when applicable;
- status;
- comparability state.

Detection-only pipelines show class-aware fields as N/A.

### 14.2 Benchmark detail

Sections:

1. Identity/protocol/status
2. Coverage
3. Localization
4. Classification-on-matched
5. Class-aware end-to-end
6. Per-class metrics table
7. Top confusion pairs table
8. DatasetEvaluationItem drill-down table

V1 prioritizes correct tables/numbers over elaborate charts.

### 14.3 Per-class table

Show:

- class ID/name;
- GT count;
- prediction count;
- AP50;
- AP50:95;
- operating-point Precision/Recall/F1.

Support sorting by AP50:95 and Recall.

### 14.4 Drill-down

A DatasetEvaluationItem must link back to the Recording/run analysis path, allowing the user to move from weak dataset metrics to sample-level Spectrum Analysis / Algorithm Lab inspection.

### 14.5 Lightweight benchmark comparison

Two evaluations may be compared only when `comparable = true` under the rules in Section 8.

Show side-by-side values and deltas for key aggregate metrics plus a per-class delta table. V1 does not require radar/bar-chart visualization.

## 15. Implementation phases

### M8.6A — Dataset Benchmark Core

Scope:

- DatasetEvaluation + DatasetEvaluationItem models and additive migration;
- Recording manifest canonicalization/hash;
- explicit/frozen run membership;
- deterministic AP evaluator;
- dataset diagnostics;
- background worker lifecycle;
- backend APIs;
- tiny synthetic tests with manually verifiable answers.

No Batch Package and no full UI in this phase.

### M8.6B — Batch Analysis Package

Scope:

- Batch Analysis Package v1 schema;
- all-or-nothing importer;
- reusable child Analysis Package validation;
- legacy historical batch adapter/exporter on the server side;
- import of the 2,500 SpaceNet historical result items into normal AnalysisRuns.

### M8.6C — Benchmark UI + Real Acceptance

Scope:

- Algorithm Lab Dataset Benchmarks UI;
- list/detail/per-class/confusion/item drill-down;
- lightweight benchmark comparison;
- real 2,500-sample DatasetEvaluation;
- comparison against historical reference metrics as a parity investigation, not as copied ground truth.

## 16. Testing strategy

### 16.1 AP evaluator unit tests

Must cover at least:

- perfect detections -> AP = 1;
- all false positives -> AP = 0;
- duplicate prediction -> first eligible match TP, duplicate FP;
- wrong class with perfect bbox -> localization TP but class-aware FP / GT remains unmatched for class AP;
- cross-Recording overlap never matches;
- deterministic same-confidence ordering;
- class with zero GT -> AP null/N/A;
- GT exists but no predictions -> AP = 0;
- IoU exactly 0.50 matches at AP50;
- IoU 0.49 does not match at AP50;
- AP50:95 averages exactly 10 thresholds;
- 101-point interpolation matches a hand-computed fixture.

### 16.2 Membership/reproducibility tests

Must cover:

- deterministic Recording manifest order/hash;
- local DB IDs/paths do not affect hash;
- GT annotation change changes hash;
- duplicate Recording name in one snapshot is rejected;
- ambiguous pipeline snapshot is surfaced, never auto-selected;
- frozen membership does not change when a newer run is later created;
- retry uses the same frozen membership.

### 16.3 Worker tests

Must cover:

- pending -> running -> completed;
- exception -> failed with error metadata;
- stale running -> interrupted on startup recovery;
- failed/interrupted retry -> running using same items;
- completed benchmark cannot rerun;
- final results are committed atomically.

### 16.4 Batch importer tests

Must cover:

- Zip Slip;
- duplicate Recording items;
- missing manifest/detections;
- invalid child package;
- pipeline/version mismatch;
- label-space mismatch;
- Recording identity mismatch;
- duplicate detection source ID;
- invalid confidence/bbox/label;
- 2,499 valid + 1 invalid -> zero AnalysisRuns created;
- successful import returns exact run mapping and creates only normal AnalysisRuns.

### 16.5 Frontend tests

Must cover:

- benchmark list states;
- detection-only N/A behavior;
- incomplete/comparable state;
- running/failed/interrupted/completed UI;
- per-class table rendering and sorting;
- benchmark comparison eligibility;
- item drill-down navigation.

## 17. Real SpaceNet acceptance

First real conformance benchmark:

- dataset: SpaceNet `advanced/test`;
- expected Recordings: 2,500;
- pipeline: Enhanced YOLOv26n + AHLP + Combined FRN V3 historical result set;
- expected historical prediction rows: 33,373;
- label space: `spacenet_14`;
- target coverage: 100%.

The platform must independently compute all M8.6 metrics from imported standard DetectionResults and local GT.

Historical full-test values approximately:

- mAP50 = 0.4970686
- mAP50:95 = 0.3732513

are references only. They must not be copied into DatasetEvaluation results.

If platform metrics differ materially, investigate protocol differences such as confidence filtering, matching semantics, AP interpolation, NMS, IoU implementation, or class handling. Do not alter the platform evaluator merely to force numerical agreement.

## 18. Performance expectations

M8.6 must remain CPU-only and responsive at the application level.

For roughly:

- 2,500 Recordings;
- 33k predictions;
- 20k GT;

benchmark computation should run in a background process and avoid N+1 database access. A tens-of-seconds computation is acceptable for this offline research workbench; sub-5-second runtime is not a V1 requirement.

The hard requirements are stability, determinism, non-blocking UI, and reproducibility.

## 19. Compatibility and migration

- Database changes are additive only.
- Existing AnalysisRun, DetectionResult, Analysis Package v1, M6 importer semantics, M8.5 matching semantics, and Pipeline contracts remain intact.
- Batch import may refactor reusable validation internals only if behavior remains backward compatible and existing M6 tests remain green.
- `main` is not part of this milestone workflow; work continues from the shared `feature/v1-core` baseline.

## 20. Acceptance criteria by phase

### M8.6A PASS

- tiny deterministic AP fixtures prove protocol math;
- frozen dataset/run manifest is deterministic and immutable;
- dataset diagnostics and AP/mAP backend outputs are correct;
- background lifecycle and retry/recovery work;
- full backend regression remains green;
- no BatchRun/Redis/Celery introduced.

### M8.6B PASS

- batch package validates all children before any ORM write;
- one invalid item leaves zero imported runs;
- 2,500 historical items import into ordinary AnalysisRuns with 100% identity coverage;
- no large model/data artifacts enter Git;
- existing single Analysis Package import remains backward compatible.

### M8.6C PASS

- Algorithm Lab can create/view dataset benchmarks and inspect per-class metrics;
- detection-only N/A semantics are correct;
- incomplete benchmark is clearly non-comparable;
- benchmark comparison enforces comparability rules;
- real SpaceNet 2,500-sample benchmark completes from platform data;
- computed metrics and any legacy-reference delta are documented transparently;
- frontend/backend regression gates are green under the project's established verification policy.

## 21. Deferred follow-ups

After M8.6, likely follow-ups include:

- M9.1 live frozen-pipeline inference feeding the same standard AnalysisRuns;
- M9.2 tuning/new schemes compared through DatasetEvaluation;
- richer leaderboard/Experiment management only if real workflow demands it;
- PR curves and confusion heatmaps only if they materially improve analysis;
- a dedicated Dataset entity only when current Recording metadata stops being sufficient.

