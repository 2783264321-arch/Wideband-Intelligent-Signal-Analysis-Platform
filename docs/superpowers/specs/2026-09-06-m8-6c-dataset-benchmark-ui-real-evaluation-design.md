# M8.6C Dataset Benchmark UI + Real Evaluation Design

Status: approved in chat on 2026-09-06; ready for implementation planning after written-spec review.

Baseline: `feature/v1-core @ 1036e51b70a351dfcae0330c8e5e725844640bc8`.

## 1. Purpose

M8.6C closes the Dataset Benchmark loop on top of M8.6A and M8.6B. It must make the existing benchmark engine scientifically explicit, run the first complete real SpaceNet evaluation from the historical 2500-run batch, and expose the result inside Algorithm Lab without creating a second experiment-management subsystem.

The work is intentionally split into three sequential gates:

1. **M8.6C-1 — Protocol & Membership Foundation**
2. **M8.6C-2 — Real 2500-Sample Benchmark Acceptance**
3. **M8.6C-3 — Dataset Benchmarks UI**

Each gate must pass before the next starts.

## 2. Fixed Architectural Decisions

### 2.1 DatasetEvaluation remains the only dataset-level evaluation domain object

M8.6C does not introduce `BatchRun`, `ModelRun`, `TrainingRun`, `ExperimentManager`, or a persistent batch-import entity. A DatasetEvaluation still freezes ordinary per-Recording `AnalysisRun` membership and aggregates their predictions.

### 2.2 Imported batches are a derived read model, not a new domain model

M8.6B already persists batch provenance in `AnalysisRun.parameters_json["batch_import"]`. M8.6C may query and group those runs into a read-only **Imported Batch Catalog**, but must not add a batch table or batch foreign key.

### 2.3 The first real benchmark membership is frozen by semantic batch fingerprint

The first formal SpaceNet test evaluation must resolve exactly the M8.6B imported runs whose batch semantic fingerprint is:

`c52cf0e752ebcead0a88b53a32a1b8b5172c38a7acb80ecbfc256c55d4c1cfd5`

It must not resolve membership merely from `pipeline_id + pipeline_version`, because an older M9.0 single-sample imported run already creates ambiguity for at least one Recording.

### 2.4 Protocol v1 is immutable; M8.6C introduces protocol v2

`physical_tf_detection_ap_v1` keeps its existing raw-GT semantics. M8.6C introduces `physical_tf_detection_ap_v2`.

The v2 protocol differs from v1 only by the evaluation Ground-Truth view:

- v1: keep all raw GT rows.
- v2: remove exact physical/class duplicates before every formal dataset metric.

All other AP rules remain unchanged in this milestone unless a later, explicitly versioned protocol is introduced.

### 2.5 One canonical GT set feeds all v2 metrics

For v2, the same deduplicated Ground-Truth set must feed:

- localization AP,
- class-aware AP/mAP,
- localization operating P/R/F1,
- matched-classification diagnostics,
- class-aware operating P/R/F1,
- confusion aggregation.

The platform must not use 19,962 GT for AP while using 20,018 GT for diagnostics.

### 2.6 Raw dataset identity stays raw

The database GroundTruth rows are never modified. The recording manifest hash remains based on the raw dataset snapshot. For the current SpaceNet test registration:

- raw platform GT: `20018`
- v2 canonical evaluation GT: `19962`
- exact duplicates removed: `56`
- recording manifest hash: `91496138ee4a10590e6e304e70b9bca8120e70c783c61df7aaad84e42d64181b`

This deliberately separates **snapshot identity** from **evaluation protocol view**.

## 3. Scope and Non-Goals

### In scope

- explicit v1/v2 protocol dispatch,
- deterministic exact GT dedup for v2,
- GT accounting in benchmark results,
- read-only Imported Batch Catalog,
- exact imported-batch resolver,
- one formal 2500-sample SpaceNet test benchmark,
- historical parity audit,
- Algorithm Lab tabs for Case Analysis and Dataset Benchmarks,
- benchmark list/create/run/progress/detail/compare,
- per-class metrics and lightweight confusion display,
- benchmark-to-case drill-down,
- single-run inspection inside Case Analysis.

### Not in scope

- live GPU inference,
- retraining or re-running YOLO/FRN/AHLP,
- a persistent BatchRun or BatchImport domain entity,
- a generic experiment manager,
- a new top-level navigation page,
- large interactive confusion matrices,
- PR curves or threshold sweeps,
- training history,
- distributed queues,
- automatic selection of the newest run when membership is ambiguous,
- UI exposure of Pipeline Snapshot creation in M8.6C,
- hard-coded historical ZoomSpec scores in product logic.

## 4. M8.6C-1 — Protocol & Membership Foundation

### 4.1 Preserve the raw loader boundary

`BenchmarkInputLoader` remains a faithful loader of the frozen database snapshot. It must continue to return raw GroundTruth rows. It must not silently deduplicate inside the loader.

```text
GroundTruthModel
    -> BenchmarkInputLoader (raw)
    -> protocol dispatcher
        -> v1 raw GT
        -> v2 canonicalized GT
    -> diagnostics + AP
```

This keeps protocol behavior explicit and makes it possible to replay an existing v1 evaluation without changing its meaning.

### 4.2 Exact duplicate definition

Canonicalization is performed separately inside each Recording. Within that Recording, the v2 Ground-Truth duplicate key contains:

```text
t_start_s
+t_end_s
+f_low_hz
+f_high_hz
+class_id
```

Local `recording_id` is only the scope boundary and never participates in the semantic key or canonical ordering.

Rules:

- equality is exact after the project’s stable float canonicalization semantics;
- use stable `.17g`-equivalent canonical float representation for duplicate-key construction;
- no IoU-based deduplication;
- no epsilon;
- no tolerance rounding;
- same box with a different `class_id` is not a duplicate;
- the same coordinates in two different Recordings are not duplicates;
- `class_name` is display data and does not participate in duplicate identity;
- output order is deterministic and independent of database insertion order; global ordering uses frozen `manifest_order` plus semantic physical/class fields, never local UUID ordering.

A protocol utility returns both the canonical GT sequence and accounting, conceptually:

```text
GroundTruthCanonicalizationResult
  raw_count
  canonical_count
  removed_count
  ground_truths
```

### 4.3 Protocol constants and config

The schema explicitly distinguishes both versions.

```json
physical_tf_detection_ap_v1
{
  "gt_duplicate_policy": "keep_all",
  "ground_truth_view": "raw"
}
```

```json
physical_tf_detection_ap_v2
{
  "gt_duplicate_policy": "exact_physical_class_dedup",
  "ground_truth_view": "evaluation_canonical"
}
```

Both retain the existing shared rules:

- physical time-frequency IoU,
- thresholds 0.50 through 0.95 in steps of 0.05,
- confidence-ranked greedy AP matching,
- 101-point interpolated precision,
- `DetectionResult.confidence` as the only AP confidence,
- deterministic ranking tie-break,
- Hungarian class-agnostic operating diagnostics at IoU 0.5.

The ordinary `POST /api/dataset-benchmarks` creation path defaults to v2 after M8.6C; the standard UI exposes no protocol selector. Existing stored v1 evaluations remain readable and runnable/retriable according to their frozen `evaluation_protocol`. Legacy v1 rows whose persisted `protocol_config_json` predates the new descriptive GT-policy keys are valid and must continue to mean raw GT; do not rewrite their stored config.

### 4.4 Worker protocol dispatch

The benchmark worker dispatches behavior from `evaluation.evaluation_protocol` and the frozen protocol config; it must not calculate every evaluation according to the process-wide newest default.

For v2, canonicalization occurs after raw loading and before any diagnostics or AP computation. The resulting per-sample and aggregate canonical GT view is used everywhere downstream.

### 4.5 Result provenance and GT accounting

No database migration is required for aggregate GT accounting. Store v2 Ground-Truth provenance in the existing `aggregate_metrics_json`, for example:

```json
{
  "ground_truth": {
    "raw_count": 20018,
    "canonical_count": 19962,
    "duplicates_removed": 56,
    "duplicate_policy": "exact_physical_class_dedup"
  }
}
```

`DatasetEvaluationItem.gt_count` means **the GT count actually used by that evaluation protocol** throughout the evaluation lifecycle:

- v1 item count = raw GT count;
- v2 item count = canonical GT count.

Creation computes the protocol-specific per-Recording count from the frozen manifest so pending/running/completed states do not change the meaning of `gt_count`; the worker may verify the count during finalization.

Do not add per-item raw/canonical database columns solely for this milestone.

### 4.6 Imported Batch Catalog

Add a read-only catalog derived from completed imported AnalysisRuns with `parameters_json.batch_import.import_fingerprint`. The recommended API surface is `GET /api/dataset-benchmarks/imported-batches`.

The catalog groups by exact semantic import fingerprint and exposes enough data for users to recognize the result set, including:

- import fingerprint,
- pipeline id/version,
- dataset name/split,
- label space,
- run count,
- detection count,
- archive SHA256 when present,
- result provenance,
- transport provenance.

The catalog excludes normal local CPU runs and imported single-run packages without a batch fingerprint. Incomplete or inconsistent groups are not presented as ready-to-evaluate batches.

### 4.7 Imported Batch Resolver

Add a benchmark resolver endpoint conceptually:

`POST /api/dataset-benchmarks/resolve-imported-batch`

Input:

```json
{
  "import_fingerprint": "<64 hex chars>"
}
```

The server derives dataset/split/label-space/pipeline metadata from the matching runs rather than trusting duplicated client input.

The resolver verifies:

- all matched runs are `executor=imported`,
- all are `status=completed`,
- exactly one semantic batch group is selected,
- unique item keys,
- unique Recordings,
- one run per included Recording,
- one pipeline id/version,
- consistent dataset/split/label-space,
- exact full coverage of the current frozen Recording manifest,
- the current frozen manifest is rebuilt and the selected run Recordings exactly cover that universe. The resolver returns that current manifest hash and `create_evaluation` re-validates it. If a future imported-run provenance record explicitly persists the batch `recording_manifest_hash`, it must also match; existing M8.6B runs are not retroactively rejected merely because that outer-manifest field was not copied into each run.

On success it returns a frozen preview compatible with the existing DatasetEvaluation create contract:

- dataset metadata,
- pipeline id/version,
- recording manifest hash,
- exact `recording_id -> analysis_run_id` mapping,
- coverage/resolution summary.

The resolver never silently substitutes another completed run.

Suggested resolver errors:

- `IMPORTED_BATCH_NOT_FOUND`
- `IMPORTED_BATCH_STATE_INCONSISTENT`
- `IMPORTED_BATCH_DATASET_INCOMPLETE`

These failures never mutate or clean up existing AnalysisRuns.

### 4.8 C-1 acceptance tests

At minimum verify:

- v1 preserves all GT rows;
- v2 removes exact physical/class duplicates;
- a minuscule coordinate difference is not a duplicate;
- same box/different class is not a duplicate;
- same box/different Recording is not a duplicate;
- canonical result is independent of DB insertion order;
- v2 AP and all operating diagnostics use the same canonical GT;
- raw DB GroundTruth count is unchanged;
- Imported Batch Catalog excludes non-batch runs;
- resolver selects the exact 2500-run historical batch;
- duplicate item keys fail;
- duplicate Recording mapping fails;
- mixed pipeline id/version fails;
- incomplete coverage fails;
- old v1 evaluation behavior remains unchanged.

C-1 passes when protocol semantics are deterministic, raw data is untouched, v1 is preserved, v2 canonicalization is explicit, and batch membership is exact.

## 5. M8.6C-2 — Real 2500-Sample Benchmark Acceptance

### 5.1 Frozen real inputs

The first real DatasetEvaluation uses the already imported M8.6B batch:

- platform dataset name: `SpaceNet`
- platform split: `test`
- underlying source split: SpaceNet `advanced/test`
- recordings: `2500`
- predictions: `33373`
- pipeline: `zoomspec_yolo26n_aug_combined_frn_v3`
- pipeline version: `1.0.0`
- label space: `spacenet_14`
- batch import fingerprint: `c52cf0e752ebcead0a88b53a32a1b8b5172c38a7acb80ecbfc256c55d4c1cfd5`
- recording manifest hash: `91496138ee4a10590e6e304e70b9bca8120e70c783c61df7aaad84e42d64181b`
- raw GT: `20018`
- v2 canonical GT: `19962`
- exact duplicates removed: `56`

No model inference, training, NMS recomputation, DSP, or GPU work occurs in C-2.

### 5.2 Formal DatasetEvaluation creation

Use the imported-batch resolver to produce an exact 2500-item mapping, then create one normal DatasetEvaluation using `physical_tf_detection_ap_v2`.

The older M9.0 single-sample run must not appear in membership merely because it shares pipeline identity.

### 5.3 Worker path

Keep the existing truthful benchmark lifecycle:

```text
loading
-> diagnostics
-> localization_ap
-> class_aware_ap (when applicable)
-> finalizing
-> completed
```

C-2 does not create a SpaceNet-specific evaluator. It runs the normal benchmark worker against the frozen standard DatasetEvaluation.

### 5.4 Input/coverage gate before metric interpretation

Before discussing mAP, verify all input invariants:

- expected_recordings = 2500;
- evaluated_recordings = 2500;
- missing_recordings = 0;
- coverage = 1.0;
- predictions = 33373;
- raw GT = 20018;
- canonical GT = 19962;
- duplicates removed = 56;
- classification applicable = true;
- protocol = `physical_tf_detection_ap_v2`;
- all 2500 runs map to the exact semantic batch fingerprint;
- the database still contains 20018 raw GroundTruth rows.

Any mismatch stops C-2 before parity interpretation.

### 5.5 Formal benchmark result set

The completed evaluation persists:

- Ground-Truth provenance/accounting;
- localization AP50 and AP50:95;
- localization TP/FP/FN, precision, recall, F1;
- matched-classification count/correct/wrong/accuracy;
- class-aware mAP50 and mAP50:95;
- class-aware TP/FP/FN, precision, recall, F1;
- per-class metrics for the SpaceNet 14-class label space;
- confusion aggregation.

For a detection+classification pipeline, the primary dataset metric is **class-aware mAP50:95**. Localization AP is a component metric, not the final 14-class recognition metric.

### 5.6 Historical reference

Historical frozen report values are reference-only:

- images = 2500;
- canonical GT = 19962;
- predictions = 33373;
- historical mAP50 = `0.49706861157413673`;
- historical mAP50:95 = `0.37325127587379914`;
- duplicate policy = exact physical/class duplicates removed.

The product must not hard-code these expected numbers.

### 5.7 Historical parity audit

After the platform evaluation completes, compare:

- platform class-aware mAP50 vs historical mAP50;
- platform class-aware mAP50:95 vs historical mAP50:95.

Record raw values and deltas. A strict comparison tolerance of `1e-12` may classify floating-point parity, but no historical value is allowed to alter platform predictions, GT, confidence, or evaluator behavior.

Audit outcomes:

- **PARITY CONFIRMED** — identical apart from allowed floating-point error;
- **PARITY DIFFERENCE EXPLAINED** — inputs agree and a concrete evaluator-protocol difference explains the delta;
- **PARITY DIFFERENCE UNEXPLAINED** — inputs agree but the delta is not yet explained.

C-2 must not pass with `PARITY DIFFERENCE UNEXPLAINED`.

### 5.8 Ordered parity investigation

If parity is not immediately confirmed, investigate in this order:

1. frozen Recording/Run membership;
2. canonical GroundTruth identity and count;
3. prediction identity/count, physical boxes, class ids, and confidence preservation;
4. global confidence ranking and tie-breaks;
5. physical time-frequency IoU;
6. greedy matching and equal-IoU tie behavior;
7. AP integration/interpolation;
8. per-class macro averaging and zero-GT treatment.

Do not tune the platform implementation merely to reproduce the old scalar score.

### 5.9 Server audit is conditional and read-only

Normally C-2 runs locally. Use **服务器opencode** only if all frozen input counts agree but the metric delta remains unexplained. The server audit may read historical `evaluate_detections.py` and related legacy files to establish evaluator semantics. It must not retrain, infer, modify legacy directories, or recompute model outputs.

### 5.10 Reproducibility without duplicate UI records

Create only one formal real DatasetEvaluation. Determinism is shown by:

- unit/integration tests from C-1, and
- one acceptance-level recomputation on the same frozen inputs without inserting a second formal DatasetEvaluation.

The recomputed metric payload must match the persisted formal result.

### 5.11 Research acceptance note

Store historical parity evidence in a research note, not product logic, for example:

`docs/research/m8_6c_real_dataset_benchmark.md`

The note records:

- platform commit,
- database/snapshot provenance,
- batch fingerprint,
- manifest hash,
- protocol/version/config,
- input counts,
- platform metrics,
- historical values,
- deltas,
- parity conclusion,
- any legacy evaluator findings.

### 5.12 C-2 pass criteria

C-2 passes only when:

- the formal benchmark completes with 100% coverage;
- the exact 2500/33373/20018->19962 invariants hold;
- aggregate/per-class/confusion results are complete;
- deterministic recomputation agrees;
- historical parity is either confirmed or explicitly explained.

## 6. M8.6C-3 — Dataset Benchmarks UI

### 6.1 Keep one Algorithm Lab top-level entry

Do not add a sixth top-level page. Keep `/algorithm-lab` and split the page into two first-level tabs:

- **Case Analysis** — existing M8.5 one-Recording inspection/comparison;
- **Dataset Benchmarks** — M8.6 dataset-level evaluation.

Refactor the current monolithic page so `AlgorithmLabPage` owns only tab/URL coordination. Business views are isolated, conceptually:

```text
AlgorithmLabPage
  CaseAnalysisView
  DatasetBenchmarksView
    BenchmarkList
    BenchmarkCreate
    BenchmarkDetail
    BenchmarkCompare
```

### 6.2 URL state

Use query parameters rather than a new nested route hierarchy:

```text
/algorithm-lab?tab=case
/algorithm-lab?tab=benchmarks
/algorithm-lab?tab=benchmarks&benchmark=eval_xxx
/algorithm-lab?tab=case&recording=rec_xxx&runA=run_xxx
/algorithm-lab?tab=case&recording=rec_xxx&runA=run_xxx&runB=run_yyy
```

Refreshing or sharing a URL preserves the selected tab and selected benchmark/case context. Case Analysis query-parameter hydration must fetch the selected Recording directly by id when necessary, rather than assuming it is present in the page’s existing first-500 Recording list; this is required for drill-down from any of the 2500 SpaceNet benchmark items.

### 6.3 Dataset Benchmark list

The benchmark list shows at least:

- name,
- pipeline + version,
- dataset/split,
- protocol,
- coverage,
- status,
- primary class-aware mAP50:95 when applicable and completed,
- created time.

Status actions:

- pending -> Run;
- running -> View Progress;
- completed -> Open;
- failed/interrupted -> Retry.

Completed evaluations remain immutable.

### 6.4 M8.6C creation path is Imported Batch only

The M8.6C UI exposes only the Imported Batch creation path. The existing Pipeline Snapshot resolver stays available in backend APIs but is not exposed in this milestone.

Creation flow:

```text
Imported Batch Catalog
-> choose batch
-> resolve exact membership
-> show resolution summary
-> enter benchmark name
-> Create & Run
-> benchmark detail
```

The UI never asks users to paste a semantic fingerprint manually. The fingerprint remains visible as provenance.

New benchmarks created through this UI always use `physical_tf_detection_ap_v2`; there is no v1/v2 protocol selector in the standard UI.

### 6.5 Resolve summary before creation

After resolve, show:

- dataset/split,
- label space,
- pipeline id/version,
- resolved Recordings / expected Recordings,
- missing/conflict counts,
- recording manifest hash,
- ready/not-ready status.

The UI must not create a benchmark if resolver output is incomplete or inconsistent.

### 6.6 Create & Run behavior

`Create & Run` performs the existing two-step domain flow:

1. create DatasetEvaluation with the exact frozen items;
2. start the evaluation;
3. navigate to its detail view.

It does not create a separate batch job entity.

### 6.7 Running view

While status is pending/running, poll the evaluation detail approximately once per second. Stop polling immediately on completed/failed/interrupted.

Display only backend-owned truthful stages:

- loading,
- diagnostics,
- localization_ap,
- class_aware_ap,
- finalizing.

The frontend must not infer fake percentage progress from elapsed time.

### 6.8 Benchmark detail hierarchy

Completed benchmark detail is organized from research headline to provenance:

1. **Summary**
   - primary End-to-End class-aware mAP50:95;
   - class-aware mAP50;
   - localization AP50:95;
   - matched-classification accuracy.
2. **Ground Truth Provenance**
   - raw annotations;
   - evaluation GT;
   - duplicates removed;
   - duplicate policy.
3. **Metric groups**
   - Localization;
   - Classification on Matched;
   - End-to-End/Class-aware.
4. **Per-Class table**
   - class id/name;
   - GT count;
   - prediction count;
   - AP50;
   - AP50:95;
   - P/R/F1;
   - stable default sort by class id, optional sort by AP50:95.
5. **Top Confusions**
   - lightweight sorted GT -> Pred confusion rows;
   - no large interactive heatmap.
6. **Protocol & Provenance**
   - evaluation protocol;
   - GT duplicate policy;
   - IoU thresholds;
   - AP interpolation;
   - confidence field;
   - recording manifest hash;
   - pipeline id/version.
7. **Evaluation Items**
   - 2500 Recording membership rows.

### 6.9 Membership read model

The evaluation-item API exposes `recording_name` in addition to existing ids/counts. This is a read-model enhancement only; do not add a database column.

Use ordinary frontend pagination for the 2500 items; server-side pagination is not required for this milestone.

### 6.10 Case Analysis single-run inspection

Current Case Analysis requires two runs before rendering comparison output. M8.6C adds a small single-run inspection mode so one benchmark item can be inspected without inventing a new page.

With Recording selected, Run A selected, and Run B empty, show:

- spectrogram,
- GroundTruth,
- Run A detections,
- prompt to choose Run B for comparison.

No new evaluation metric is computed in single-run mode. It reuses existing spectrogram, GroundTruth, and DetectionResult APIs.

With Run B present, keep the existing M8.5 A/B comparison semantics unchanged.

### 6.11 Benchmark-to-case drill-down

From one DatasetEvaluation item:

```text
recording + frozen AnalysisRun
-> /algorithm-lab?tab=case&recording=...&runA=...
```

From a comparable two-benchmark context:

```text
same recording
+ evaluation A frozen run
+ evaluation B frozen run
-> /algorithm-lab?tab=case&recording=...&runA=...&runB=...
```

This makes dataset-level diagnosis and case-level diagnosis one workflow rather than two separate products.

### 6.12 Benchmark comparison

Use the existing backend compare endpoint as the authority for comparability. The frontend must not independently decide compatibility.

Comparable requires the backend’s existing rules including:

- both completed;
- both full coverage;
- same dataset;
- same split;
- same label space;
- same recording manifest hash;
- same evaluation protocol;
- same protocol config.

For comparable evaluations, show a lightweight A/B table containing primary class-aware metrics, localization metrics, matched accuracy, and deltas.

For incompatible evaluations, show backend reasons and do not present misleading deltas.

### 6.13 Compatibility behavior

- Existing v1 evaluations remain viewable and clearly labeled as raw-GT protocol.
- The UI must not fabricate v2 duplicate accounting for v1.
- Detection-only pipelines show classification and class-aware metrics as N/A, never zero.
- Resolver, spawn, worker, and retry failures display backend error information and never silently switch runs or recreate evaluations.

### 6.14 C-3 test and browser gate

At minimum verify:

- existing M8.5 Case Analysis A/B flow still works;
- single-run inspect works;
- Dataset Benchmarks tab/list loads;
- Imported Batch Catalog loads;
- resolve succeeds for the historical 2500-run batch;
- v2 protocol is fixed/read-only in the UI;
- creation and run lifecycle are correct;
- polling stops on terminal state;
- completed summary renders the actual C-2 result;
- raw/canonical/removed GT counts render correctly;
- 14 per-class rows render correctly;
- confusion rows render correctly;
- incompatible benchmark comparison stays blocked;
- one-benchmark drill-down pre-fills Recording + Run A;
- two-benchmark drill-down pre-fills Recording + Run A + Run B;
- frontend tests pass;
- backend regression tests pass;
- frontend production build passes;
- real browser smoke uses the single C-2 formal benchmark rather than inserting another duplicate real evaluation.

## 7. API and Data-Model Boundaries

Expected additions/changes are intentionally narrow:

- protocol constants/config/dispatcher;
- GT canonicalization utility;
- worker use of protocol-specific GT view;
- aggregate GT provenance;
- read-only imported-batch catalog endpoint;
- imported-batch resolver endpoint;
- evaluation-item read model includes Recording name;
- frontend client/types/components for dataset benchmarks;
- Case Analysis single-run inspection.

No new persistence table is required by this design.

## 8. Error-Handling Principles

- Fail closed on ambiguous or incomplete membership.
- Never auto-select newest/oldest candidate AnalysisRun.
- Never mutate raw GroundTruth to make benchmark numbers match.
- Never modify imported DetectionResults during evaluation.
- Never reinterpret an existing evaluation under a newer protocol.
- On worker failure, keep benchmark lifecycle and recovery semantics from M8.6A.
- On historical parity difference, investigate protocol semantics rather than changing the score target.

## 9. Implementation Order and Gates

Implementation follows this exact order:

### Gate C-1

Implement and verify protocol v2, canonical GT view, GT accounting, Imported Batch Catalog, and imported-batch resolver. Do not run the formal 2500-sample benchmark until C-1 tests pass.

### Gate C-2

Use the real local database and existing M8.6B imported runs to create and run one formal v2 DatasetEvaluation. Verify frozen input counts and conduct the historical parity audit. Do not begin UI implementation until the result is trustworthy and parity is confirmed or explained.

### Gate C-3

Build the Dataset Benchmarks UI around the already accepted C-2 API/result contract, add single-run Case Analysis inspection, run regression/build/browser smoke, then prepare final integration.

## 10. Final M8.6C Acceptance

M8.6C is complete only when all three gates pass and the following end-to-end story is demonstrated:

```text
M8.6B imported historical result batch
-> exact semantic batch resolver
-> frozen 2500-run DatasetEvaluation
-> physical_tf_detection_ap_v2
-> raw 20018 GT -> canonical 19962 GT
-> 33373 frozen predictions evaluated
-> historical parity confirmed or explained
-> completed benchmark visible in Algorithm Lab
-> per-class/provenance/comparison available
-> any benchmark item can drill down to Case Analysis
```

This is the V1 dataset-level research-evaluation loop. It stays architecture-neutral, preserves frozen provenance, and avoids expanding into a generic experiment-management platform.
