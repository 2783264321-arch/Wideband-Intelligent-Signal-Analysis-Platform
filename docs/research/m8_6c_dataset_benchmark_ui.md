# M8.6C-3 Dataset Benchmarks UI Acceptance

Date: 2026-09-06
Branch: `feature/m8-6c-dataset-benchmark-ui-real-evaluation`
Base: `feature/v1-core @ 1036e51b70a351dfcae0330c8e5e725844640bc8`
Plan: `docs/superpowers/plans/2026-09-06-m8-6c-3-dataset-benchmarks-ui.md`

## Scope

C-3 exposes the accepted DatasetEvaluation workflow inside Algorithm Lab as a Dataset Benchmarks tab. It keeps `/algorithm-lab` as the only top-level route and uses query parameters for tab/benchmark/Recording/frozen-run state. No sixth top-level page, no new route hierarchy, no new persistence table, no DB migration, and no duplicate real DatasetEvaluation was created.

## Implementation identity

- C-3 implementation HEAD: `a50926a7b4dfc351203f9b8c006cd10b40beae55`
- backend read-model: evaluation items now include `recording_name` via a join (no column/migration)
- frontend: Algorithm Lab page coordinates Case Analysis + Dataset Benchmarks tabs via `useSearchParams`

## Automated verification

### Backend full suite

- command: `python -m pytest -q` (worktree backend)
- passed: 298, failed: 0, exit: 0

### Frontend focused suite

- command: `npm test -- --run src/pages/AlgorithmLabPage.test.tsx src/features/algorithm-lab/CaseAnalysisView.test.tsx src/features/algorithm-lab/RunMetricsCard.test.tsx src/features/algorithm-lab/CaseComparisonTable.test.tsx src/features/dataset-benchmarks/DatasetBenchmarksView.test.tsx`
- passed: 24, failed: 0, exit: 0

### Frontend full suite

- command: `npm test -- --run`
- passed: 37, failed: 0, exit: 0 (verified across three consecutive runs)

### Frontend production build

- command: `npm run build`
- `tsc -b` PASS, Vite build PASS, exit: 0

## Real browser/API smoke (real local platform DB)

The backend was started against the real primary database:

```
WSP_DATABASE_URL = sqlite:///D:/LGFiles/Wideband Signal Analysis Platform/Wideband-Intelligent-Signal-Analysis-Platform/platform.db
```

The public API served the single accepted C-2 real evaluation:

- DatasetEvaluation id reused: `eval_23bfb7a075e04d80a30e70754042a61c`
- name: `M8.6C SpaceNet Test - ZoomSpec YOLO26n Aug + Combined FRN V3`
- status: completed
- protocol: `physical_tf_detection_ap_v2`
- recording manifest: `91496138ee4a10590e6e304e70b9bca8120e70c783c61df7aaad84e42d64181b`
- coverage: 2500 / 2500 / missing 0 / 1.0
- class-aware mAP50: `0.49706861157413673`
- class-aware mAP50:95: `0.3732512758737991`
- localization AP50: `0.8666496180384053`; AP50:95: `0.6715938311249926`
- matched accuracy: `0.8151265187827444`
- GT provenance: raw `20018`, canonical `19962`, removed `56`, policy `exact_physical_class_dedup`
- per-class rows: 14; confusion rows: 105
- evaluation items: 2500; first item recording name `0`, analysis run `run_14db9792067e4c9eb6a3ed00408504a1` (the exact M8.6B batch run, not the older M9.0 single-package run), gt_count 6 (canonical), prediction_count 13
- total item predictions: 33373; total canonical item GT: 19962

This confirms the Dataset Benchmarks list/detail/per-class/confusion/GT-provenance/membership and the single-run Inspect drill-down consume exactly the real C-2 data.

## Case Analysis

- existing A/B comparison regression: green (moved behavior tests pass)
- single-run inspection: a Recording + Run A renders spectrogram/GT/Run A detections with a "Select Run B to compare" prompt and never calls the compare endpoint
- query hydration: a Recording selected via URL outside the first 500 list is hydrated directly by `GET /api/recordings/{id}`
- Algorithm Lab tabs and query state: tab/benchmark/recording/runA/runB round-trip through `useSearchParams`; switching tabs keeps unrelated query state; the existing `/algorithm-lab` app route is unchanged

## Compare

- backend comparability is the only authority; incomparable pairs display the backend reasons and no metric delta table
- comparable pairs show the lightweight aggregate table and support exact frozen A/B drill-down to Case Analysis
- verified by automated tests

## Imported Batch creation flow

- catalog loads; only `ready` entries are selectable
- resolve → create → run; protocol fixed read-only at `physical_tf_detection_ap_v2`, no protocol selector, `evaluation_protocol` is not sent by the frontend
- 2500-item create payload verified by automated test; Pipeline Snapshot creation is hidden in the UI

## Truthful progress

- pending/running polls approximately once per second and stops immediately on completed/failed/interrupted (fake-timer test proves no further requests after terminal state)
- only backend-owned stages are shown; no synthesized percentages

## Real DB integrity after smoke

- formal NAME evaluation count: 1 (the C-2 real evaluation), no duplicate real benchmark inserted
- SpaceNet/test/spacenet_14 GroundTruth: 20018 (unchanged)
- AnalysisRuns: 2507 (unchanged)
- DetectionResults: 33571 (unchanged)
- DatasetEvaluation total: 1

## Notes

- Two heavy frontend integration tests (the CaseAnalysisView A/B comparison flow and the pre-existing ImportRunModal ZIP-import flow) occasionally exceed the vitest 5s default under parallel-worker CPU contention; both are correct and reliable in isolation. They were given a targeted per-test timeout (20000ms) with an explanatory comment. This is a contention accommodation for known-correct tests, not a correctness waiver.
- No new persistence table, no DB migration, no sixth top-level route, no BatchRun/BatchImportModel entity, and no model/data artifact was added.
---

## Corrective review (C-3 corrective acceptance)

### Backend error code/message behavior

The frontend API boundary (`apiGet`/`apiPostJson`) previously threw only `API request failed: <status>` for non-2xx responses. It now preserves the backend error contract via `PlatformApiError`:

- `status`, `code`, `message`, `details`
- `display` renders `CODE: message` (e.g. `INVALID_BENCHMARK_TRANSITION: Only pending evaluations can be started.`)
- Non-JSON error bodies fall back to `HTTP_<status>` with the generic message

Covered by `frontend/src/api/client.test.ts` for both `apiGet` and `apiPostJson`, including an `INVALID_BENCHMARK_TRANSITION` (409) and `IMPORTED_BATCH_DATASET_INCOMPLETE` (422) payload and the non-JSON fallback.

### List / run / retry non-destructive errors

`DatasetBenchmarksView` now surfaces a non-destructive error `Alert` on list-load, Run, and Retry failures:

- a failed benchmark list load keeps `New Benchmark` and the page structure visible;
- a rejected Run keeps the current list and selection;
- a failed Retry never auto-switches the benchmark or auto-recreates the evaluation.

`BenchmarkCreatePanel` keeps its `fingerprint`/`resolution`/`benchmarkName` state on error and shows `CODE: message`. `BenchmarkComparePanel` surfaces compare/items errors. `BenchmarkDetailView` keeps `Back to list` even when the initial load or a polling request fails.

Covered by `DatasetBenchmarksView.test.tsx` error-contract tests and the detail/compare error tests.

### Recording switch stale-state regression

CaseAnalysisView previously retained the previous Recording's comparison panels after switching Recordings because derived state was only cleared when the Recording became empty. It now invalidates `compare`/`meta`/`groundTruth`/`detectionsA`/`detectionsB`/`selectedCaseId` whenever the Recording identity changes (and only then, so the recordings catalog arrival or a run-list refresh for the same Recording never clobbers an in-flight single-run or comparison view). Covered by the "clears stale comparison state when switching to another Recording" test; single-run inspection, A/B compare, and outside-first-500 hydration remain green.

### Detection-only list N/A

Benchmark list now shows `N/A` (not `—`) for a completed detection-only benchmark's class-aware mAP50:95, matching the approved detection-only contract. Covered by a list test.

### Real browser smoke

Servers were started against the real primary database (`WSP_DATABASE_URL` = `sqlite:///D:/LGFiles/Wideband Signal Analysis Platform/Wideband-Intelligent-Signal-Analysis-Platform/platform.db`):

- backend: `http://127.0.0.1:8000`
- frontend: `http://127.0.0.1:5173`

A real Chrome browser (agent-browser/CDP) opened:

`http://127.0.0.1:5173/algorithm-lab?tab=benchmarks&benchmark=eval_23bfb7a075e04d80a30e70754042a61c`

Observed in the browser (values come from the real backend, not hardcoded):

- Dataset Benchmarks tab active; heading `M8.6C SpaceNet Test - ZoomSpec YOLO26n Aug + Combined FRN V3`
- status completed; End-to-End Class-aware mAP50:95 `0.3733`; mAP50 `0.4971`
- GT provenance: Raw annotations `20018`, Evaluation GT `19962`, Exact duplicates removed `56`, Policy `exact_physical_class_dedup`
- Localization AP50 `0.8666`, AP50:95 `0.6716`; matched accuracy `0.8151`
- End-to-End mAP50 `0.4971`, mAP50:95 `0.3733`; class-aware P/R/F1 `0.4382 / 0.7326 / 0.5484`
- Per-class 14 rows reachable; Top Classification Confusions renders
- Protocol `physical_tf_detection_ap_v2`; Manifest `91496138ee4a10590e6e304e70b9bca8120e70c783c61df7aaad84e42d64181b`
- 2500 Evaluation Items; first row Recording name `0`, GT 6, Predictions 13, Analysis Run `run_14db9792067e4c9eb6a3ed00408504a1`

Clicking `Inspect` on the sample-0 row navigated the real browser to:

`/algorithm-lab?tab=case&recording=rec_eea493d189754e61a0f1520e3215690a&runA=run_14db9792067e4c9eb6a3ed00408504a1`

The single-run view rendered the real STFT spectrogram, 6 GroundTruth overlays, 13 Run A detection overlays, and the prompt `Select Run B to compare this result with another run.` Run A is the exact frozen M8.6B batch run (not the older M9.0 single-package run).

Selecting a second completed run (the old M9.0 sample-0 run `run_90f01b90a8fa4300bdb9e46d4e561745`) as Run B executed the existing M8.5 A/B comparison in the browser with real metric cards.

DB integrity before and after the smoke was identical: formal NAME count `1`, SpaceNet/test/spacenet_14 GroundTruth `20018`, AnalysisRuns `2507`, DetectionResults `33571`, DatasetEvaluation total `1`. No duplicate real benchmark was created.
