# M8.6C-3 Dataset Benchmarks UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the accepted DatasetEvaluation workflow inside Algorithm Lab as a Dataset Benchmarks tab with imported-batch creation, truthful progress, completed metrics/provenance, lightweight comparison, and drill-down into reusable Case Analysis single-run/A-B inspection.

**Architecture:** Keep `/algorithm-lab` as the only top-level route and use query parameters for tab, benchmark, Recording, and frozen run selection. Refactor the current monolithic AlgorithmLab page into controlled Case Analysis and Dataset Benchmarks views. The frontend is a pure client of existing benchmark lifecycle semantics; backend comparability and resolver truth remain authoritative. Add only one backend read-model enhancement: evaluation items include `recording_name` via a join, without a DB column.

**Tech Stack:** React + TypeScript + Vite, Ant Design, React Router, Testing Library/Vitest, FastAPI/Pydantic/SQLAlchemy for the small read-model change.

**Spec:** `docs/superpowers/specs/2026-09-06-m8-6c-dataset-benchmark-ui-real-evaluation-design.md`

## Global Constraints

- Local execution target is Windows. Shell snippets in this plan use POSIX/Bash syntax for heredocs and environment variables; 本地电脑opencode must run those blocks from Git Bash at the repository root (or translate them exactly to PowerShell without changing semantics).
- C-2 must already be accepted with one completed real SpaceNet v2 DatasetEvaluation and `docs/research/m8_6c_real_dataset_benchmark.md` committed.
- Do not add a sixth top-level page or nested benchmark route hierarchy.
- Standard UI creates benchmarks only from ready Imported Batch Catalog entries; Pipeline Snapshot backend support remains hidden in M8.6C.
- Standard UI fixes protocol to `physical_tf_detection_ap_v2`; no protocol selector.
- Completed DatasetEvaluations are immutable.
- Backend is authoritative for imported-batch resolution and benchmark comparability.
- Pending/running polling is approximately once per second and stops immediately for completed/failed/interrupted.
- Frontend displays only truthful backend stages; never synthesize elapsed-time percentages.
- Detection-only classification/class-aware values are `N/A`, never zero.
- Existing v1 evaluations remain viewable and are labeled raw-GT; do not fabricate v2 duplicate accounting.
- One-run Case Analysis is inspection only; two-run mode preserves existing M8.5 comparison semantics.
- Browser smoke reuses the single accepted C-2 real evaluation; do not insert a duplicate real evaluation for UI testing.
- Use TDD and frequent commits.

---

## File Structure

**Backend modify**
- `backend/app/benchmarks/schema.py` — add `recording_name` to item read model.
- `backend/app/benchmarks/service.py` — join Recording name for item read model.
- `backend/tests/test_benchmark_api.py` — assert item API returns human-readable Recording name.

**Frontend create**
- `frontend/src/features/algorithm-lab/CaseAnalysisView.tsx` — extracted controlled case inspection/comparison view with single-run mode.
- `frontend/src/features/algorithm-lab/CaseAnalysisView.test.tsx` — existing A/B behavior + single-run/query hydration coverage.
- `frontend/src/features/dataset-benchmarks/DatasetBenchmarksView.tsx` — benchmark feature coordinator.
- `frontend/src/features/dataset-benchmarks/BenchmarkListTable.tsx` — list/status/actions/selection.
- `frontend/src/features/dataset-benchmarks/BenchmarkCreatePanel.tsx` — catalog → resolve → create & run.
- `frontend/src/features/dataset-benchmarks/BenchmarkDetailView.tsx` — polling, summary, GT provenance, metric groups, per-class, confusions, protocol, items.
- `frontend/src/features/dataset-benchmarks/BenchmarkComparePanel.tsx` — aggregate compare + compatible recording drill-down.
- `frontend/src/features/dataset-benchmarks/DatasetBenchmarksView.test.tsx` — list/create/lifecycle/detail/compare/drill-down behavior.

**Frontend modify**
- `frontend/src/api/types.ts` — dataset benchmark domain/view types.
- `frontend/src/api/client.ts` — dataset benchmark HTTP clients and mappers.
- `frontend/src/pages/AlgorithmLabPage.tsx` — tabs + query-state coordination only.
- `frontend/src/pages/AlgorithmLabPage.test.tsx` — tab/query routing and regression smoke.
- Existing `RunComparisonPanel.tsx`, `RunMetricsCard.tsx`, `CaseComparisonTable.tsx` remain reusable; do not duplicate spectrogram rendering.

No DB migration and no `App.tsx` route addition are expected.

---

### Task 1: Add Recording name to DatasetEvaluation item read model

**Files:**
- Modify: `backend/app/benchmarks/schema.py`
- Modify: `backend/app/benchmarks/service.py`
- Modify: `backend/app/benchmarks/router.py` only if required by return type
- Modify: `backend/tests/test_benchmark_api.py`

**Interfaces:**
- Consumes: existing `GET /api/dataset-benchmarks/{evaluation_id}/items`.
- Produces: every item payload includes `recording_name: str`; persistence schema is unchanged.

- [ ] **Step 1: Add failing API assertion**

Use the existing `_populate() / _prepare() / _create()` fixture in `backend/tests/test_benchmark_api.py`. In `test_create_get_list_items_routes`, replace the current item-only assertion with:

```python
items_response = client.get(f"/api/dataset-benchmarks/{created['id']}/items")
assert items_response.status_code == 200
items = items_response.json()
assert len(items) == 2
assert [(item["recording_id"], item["recording_name"], item["analysis_run_id"]) for item in items] == [
    ("rec_a", "a", "run_a"),
    ("rec_b", "b", "run_b"),
]
```

- [ ] **Step 2: Run focused test and confirm RED**

```bash
pytest tests/test_benchmark_api.py -q
```

Expected: response schema lacks `recording_name`.

- [ ] **Step 3: Add read-model field and joined service projection**

In `backend/app/benchmarks/schema.py`:

```python
class DatasetEvaluationItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    evaluation_id: str
    manifest_order: int
    recording_id: str
    recording_name: str
    analysis_run_id: str | None
    status: str
    gt_count: int
    prediction_count: int
    error_reason: str | None
```

In `backend/app/benchmarks/service.py`, replace the item ORM-only return with a projection:

```python
@dataclass(frozen=True)
class DatasetEvaluationItemView:
    id: str
    evaluation_id: str
    manifest_order: int
    recording_id: str
    recording_name: str
    analysis_run_id: str | None
    status: str
    gt_count: int
    prediction_count: int
    error_reason: str | None


def list_items(self, evaluation_id: str) -> list[DatasetEvaluationItemView]:
    self.get_evaluation(evaluation_id)
    rows = self.session.execute(
        select(DatasetEvaluationItemModel, RecordingModel.name)
        .join(RecordingModel, DatasetEvaluationItemModel.recording_id == RecordingModel.id)
        .where(DatasetEvaluationItemModel.evaluation_id == evaluation_id)
        .order_by(DatasetEvaluationItemModel.manifest_order)
    ).all()
    return [
        DatasetEvaluationItemView(
            id=item.id,
            evaluation_id=item.evaluation_id,
            manifest_order=item.manifest_order,
            recording_id=item.recording_id,
            recording_name=recording_name,
            analysis_run_id=item.analysis_run_id,
            status=item.status,
            gt_count=item.gt_count,
            prediction_count=item.prediction_count,
            error_reason=item.error_reason,
        )
        for item, recording_name in rows
    ]
```

Keep `ConfigDict(from_attributes=True)` on `DatasetEvaluationItemRead`; FastAPI/Pydantic can serialize the dataclass projection by attributes, so the router should continue returning `service.list_items(evaluation_id)` directly. Do not add ad-hoc `__dict__` conversion.

- [ ] **Step 4: Run backend focused regression**

```bash
pytest tests/test_benchmark_api.py tests/test_benchmark_membership.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 1**

```bash
git add backend/app/benchmarks/schema.py backend/app/benchmarks/service.py backend/app/benchmarks/router.py backend/tests/test_benchmark_api.py
git commit -m "feat: expose benchmark recording names"
```

---

### Task 2: Add typed Dataset Benchmark frontend client

**Files:**
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/api/client.ts`

**Interfaces:**
- Produces these client functions:
  - `listDatasetBenchmarks()`
  - `getDatasetBenchmark(evaluationId)`
  - `listDatasetBenchmarkItems(evaluationId)`
  - `listImportedBenchmarkBatches()`
  - `resolveImportedBenchmarkBatch(importFingerprint)`
  - `createDatasetBenchmark(payload)`
  - `runDatasetBenchmark(evaluationId)`
  - `retryDatasetBenchmark(evaluationId)`
  - `compareDatasetBenchmarks(evaluationAId, evaluationBId)`

- [ ] **Step 1: Define frontend domain types before client implementation**

Append to `frontend/src/api/types.ts` concrete types:

```ts
export type DatasetEvaluationStatus = "pending" | "running" | "completed" | "failed" | "interrupted";

export interface GroundTruthProvenance {
  rawCount: number;
  canonicalCount: number;
  duplicatesRemoved: number;
  duplicatePolicy: string;
}

export interface OperatingMetrics {
  tp: number;
  fp: number;
  fn: number;
  precision: number;
  recall: number;
  f1: number;
}

export interface DatasetBenchmarkAggregateMetrics {
  groundTruth?: GroundTruthProvenance;
  classificationApplicable: boolean;
  classificationReason: string | null;
  localization: {
    ap50: number | null;
    ap50_95: number | null;
    operating: OperatingMetrics;
  };
  classificationOnMatched: {
    matchedCount: number;
    classCorrect: number;
    classWrong: number;
    matchedAccuracy: number | null;
  } | null;
  classAware: {
    map50: number | null;
    map50_95: number | null;
    operating: OperatingMetrics;
  } | null;
}

export interface DatasetBenchmarkPerClassMetric {
  classId: number;
  className: string;
  gtCount: number;
  predictionCount: number;
  ap50: number | null;
  ap50_95: number | null;
  operating: OperatingMetrics;
}

export interface DatasetBenchmarkConfusion {
  gtClassId: number;
  gtClassName: string;
  predClassId: number;
  predClassName: string;
  count: number;
}

export interface DatasetEvaluation {
  id: string;
  name: string;
  datasetName: string;
  datasetSplit: string;
  labelSpace: string;
  pipelineId: string;
  pipelineVersion: string;
  status: DatasetEvaluationStatus;
  expectedRecordings: number;
  evaluatedRecordings: number;
  missingRecordings: number;
  coverage: number;
  comparable: boolean;
  recordingManifestHash: string;
  evaluationProtocol: string;
  protocolConfig: Record<string, unknown>;
  aggregateMetrics: DatasetBenchmarkAggregateMetrics | null;
  perClassMetrics: DatasetBenchmarkPerClassMetric[] | null;
  confusion: DatasetBenchmarkConfusion[] | null;
  progressStage: string | null;
  progressCurrent: number | null;
  progressTotal: number | null;
  errorType: string | null;
  errorMessage: string | null;
  createdAt: string | null;
  completedAt: string | null;
}

export interface DatasetEvaluationItem {
  id: string;
  evaluationId: string;
  manifestOrder: number;
  recordingId: string;
  recordingName: string;
  analysisRunId: string | null;
  status: string;
  gtCount: number;
  predictionCount: number;
  errorReason: string | null;
}

export interface ImportedBenchmarkBatch {
  importFingerprint: string;
  pipelineId: string | null;
  pipelineVersion: string | null;
  datasetName: string | null;
  datasetSplit: string | null;
  labelSpace: string | null;
  runCount: number;
  detectionCount: number;
  archiveSha256: string | null;
  resultProvenance: Record<string, unknown>;
  transportProvenance: Record<string, unknown>;
  ready: boolean;
  inconsistencyReasons: string[];
}

export interface ImportedBatchResolution {
  importFingerprint: string;
  datasetName: string;
  datasetSplit: string;
  labelSpace: string;
  pipelineId: string;
  pipelineVersion: string;
  recordingManifestHash: string;
  expectedRecordings: number;
  resolvedRecordings: number;
  missingRecordings: number;
  conflictCount: number;
  entries: Array<{
    manifestOrder: number;
    recordingId: string;
    recordingName: string;
    analysisRunId: string;
    itemKey: string;
  }>;
}

export interface DatasetBenchmarkCompareResult {
  comparable: boolean;
  reasons: string[];
  evaluationAId: string;
  evaluationBId: string;
  aggregateA: DatasetBenchmarkAggregateMetrics | null;
  aggregateB: DatasetBenchmarkAggregateMetrics | null;
  deltas: Record<string, number | null>;
}
```

Use stronger nested types for aggregate/per-class if convenient, but do not change backend field semantics.

- [ ] **Step 2: Implement snake_case wire mappers and client functions**

In `frontend/src/api/client.ts`, add mappers with explicit field names; for example:

```ts
interface OperatingMetricsWire {
  tp: number;
  fp: number;
  fn: number;
  precision: number;
  recall: number;
  f1: number;
}

interface DatasetBenchmarkAggregateWire {
  ground_truth?: {
    raw_count: number;
    canonical_count: number;
    duplicates_removed: number;
    duplicate_policy: string;
  };
  classification_applicable: boolean;
  classification_reason: string | null;
  localization: { ap50: number | null; ap50_95: number | null; operating: OperatingMetricsWire };
  classification_on_matched: {
    matched_count: number;
    class_correct: number;
    class_wrong: number;
    matched_accuracy: number | null;
  } | null;
  class_aware: { map50: number | null; map50_95: number | null; operating: OperatingMetricsWire } | null;
}

interface DatasetBenchmarkPerClassWire {
  class_id: number;
  class_name: string;
  gt_count: number;
  prediction_count: number;
  ap50: number | null;
  ap50_95: number | null;
  operating: OperatingMetricsWire;
}

interface DatasetBenchmarkConfusionWire {
  gt_class_id: number;
  gt_class_name: string;
  pred_class_id: number;
  pred_class_name: string;
  count: number;
}

interface DatasetEvaluationWire {
  id: string;
  name: string;
  dataset_name: string;
  dataset_split: string;
  label_space: string;
  pipeline_id: string;
  pipeline_version: string;
  status: DatasetEvaluationStatus;
  expected_recordings: number;
  evaluated_recordings: number;
  missing_recordings: number;
  coverage: number;
  comparable: boolean;
  recording_manifest_hash: string;
  evaluation_protocol: string;
  protocol_config_json: Record<string, unknown>;
  aggregate_metrics_json: DatasetBenchmarkAggregateWire | null;
  per_class_metrics_json: DatasetBenchmarkPerClassWire[] | null;
  confusion_json: DatasetBenchmarkConfusionWire[] | null;
  progress_stage: string | null;
  progress_current: number | null;
  progress_total: number | null;
  error_type: string | null;
  error_message: string | null;
  created_at?: string | null;
  completed_at?: string | null;
}

function mapOperating(item: OperatingMetricsWire): OperatingMetrics {
  return { tp: item.tp, fp: item.fp, fn: item.fn, precision: item.precision, recall: item.recall, f1: item.f1 };
}

function mapAggregate(item: DatasetBenchmarkAggregateWire | null): DatasetBenchmarkAggregateMetrics | null {
  if (!item) return null;
  return {
    groundTruth: item.ground_truth ? {
      rawCount: item.ground_truth.raw_count,
      canonicalCount: item.ground_truth.canonical_count,
      duplicatesRemoved: item.ground_truth.duplicates_removed,
      duplicatePolicy: item.ground_truth.duplicate_policy,
    } : undefined,
    classificationApplicable: item.classification_applicable,
    classificationReason: item.classification_reason,
    localization: {
      ap50: item.localization.ap50,
      ap50_95: item.localization.ap50_95,
      operating: mapOperating(item.localization.operating),
    },
    classificationOnMatched: item.classification_on_matched ? {
      matchedCount: item.classification_on_matched.matched_count,
      classCorrect: item.classification_on_matched.class_correct,
      classWrong: item.classification_on_matched.class_wrong,
      matchedAccuracy: item.classification_on_matched.matched_accuracy,
    } : null,
    classAware: item.class_aware ? {
      map50: item.class_aware.map50,
      map50_95: item.class_aware.map50_95,
      operating: mapOperating(item.class_aware.operating),
    } : null,
  };
}

function mapPerClass(item: DatasetBenchmarkPerClassWire): DatasetBenchmarkPerClassMetric {
  return {
    classId: item.class_id,
    className: item.class_name,
    gtCount: item.gt_count,
    predictionCount: item.prediction_count,
    ap50: item.ap50,
    ap50_95: item.ap50_95,
    operating: mapOperating(item.operating),
  };
}

function mapConfusion(item: DatasetBenchmarkConfusionWire): DatasetBenchmarkConfusion {
  return {
    gtClassId: item.gt_class_id, gtClassName: item.gt_class_name,
    predClassId: item.pred_class_id, predClassName: item.pred_class_name, count: item.count,
  };
}

function mapDatasetEvaluation(item: DatasetEvaluationWire): DatasetEvaluation {
  return {
    id: item.id,
    name: item.name,
    datasetName: item.dataset_name,
    datasetSplit: item.dataset_split,
    labelSpace: item.label_space,
    pipelineId: item.pipeline_id,
    pipelineVersion: item.pipeline_version,
    status: item.status,
    expectedRecordings: item.expected_recordings,
    evaluatedRecordings: item.evaluated_recordings,
    missingRecordings: item.missing_recordings,
    coverage: item.coverage,
    comparable: item.comparable,
    recordingManifestHash: item.recording_manifest_hash,
    evaluationProtocol: item.evaluation_protocol,
    protocolConfig: item.protocol_config_json,
    aggregateMetrics: mapAggregate(item.aggregate_metrics_json),
    perClassMetrics: item.per_class_metrics_json?.map(mapPerClass) ?? null,
    confusion: item.confusion_json?.map(mapConfusion) ?? null,
    progressStage: item.progress_stage,
    progressCurrent: item.progress_current,
    progressTotal: item.progress_total,
    errorType: item.error_type,
    errorMessage: item.error_message,
    createdAt: item.created_at ?? null,
    completedAt: item.completed_at ?? null,
  };
}
```

Creation must preserve the existing backend contract:

```ts
export async function createDatasetBenchmark(payload: {
  name: string;
  resolution: ImportedBatchResolution;
}): Promise<DatasetEvaluation> {
  return mapDatasetEvaluation(await apiPostJson<DatasetEvaluationWire>("/api/dataset-benchmarks", {
    name: payload.name,
    dataset_name: payload.resolution.datasetName,
    dataset_split: payload.resolution.datasetSplit,
    label_space: payload.resolution.labelSpace,
    recording_manifest_hash: payload.resolution.recordingManifestHash,
    allow_incomplete: false,
    items: payload.resolution.entries.map((entry) => ({
      recording_id: entry.recordingId,
      analysis_run_id: entry.analysisRunId,
    })),
  }));
}
```

Add the remaining wire shapes and concrete clients; do not use `any` for these API boundaries:

```ts
interface DatasetEvaluationItemWire {
  id: string;
  evaluation_id: string;
  manifest_order: number;
  recording_id: string;
  recording_name: string;
  analysis_run_id: string | null;
  status: string;
  gt_count: number;
  prediction_count: number;
  error_reason: string | null;
}

interface ImportedBenchmarkBatchWire {
  import_fingerprint: string;
  pipeline_id: string | null;
  pipeline_version: string | null;
  dataset_name: string | null;
  dataset_split: string | null;
  label_space: string | null;
  run_count: number;
  detection_count: number;
  archive_sha256: string | null;
  result_provenance: Record<string, unknown>;
  transport_provenance: Record<string, unknown>;
  ready: boolean;
  inconsistency_reasons: string[];
}

interface ImportedBatchResolutionWire {
  import_fingerprint: string;
  dataset_name: string;
  dataset_split: string;
  label_space: string;
  pipeline_id: string;
  pipeline_version: string;
  recording_manifest_hash: string;
  expected_recordings: number;
  resolved_recordings: number;
  missing_recordings: number;
  conflict_count: number;
  entries: Array<{
    manifest_order: number; recording_id: string; recording_name: string;
    analysis_run_id: string; item_key: string;
  }>;
}

interface DatasetBenchmarkCompareWire {
  comparable: boolean;
  reasons: string[];
  evaluation_a_id: string;
  evaluation_b_id: string;
  aggregate_a: DatasetBenchmarkAggregateWire | null;
  aggregate_b: DatasetBenchmarkAggregateWire | null;
  deltas: Record<string, number | null>;
}

const mapDatasetEvaluationItem = (item: DatasetEvaluationItemWire): DatasetEvaluationItem => ({
  id: item.id, evaluationId: item.evaluation_id, manifestOrder: item.manifest_order,
  recordingId: item.recording_id, recordingName: item.recording_name,
  analysisRunId: item.analysis_run_id, status: item.status, gtCount: item.gt_count,
  predictionCount: item.prediction_count, errorReason: item.error_reason,
});

const mapImportedBatch = (item: ImportedBenchmarkBatchWire): ImportedBenchmarkBatch => ({
  importFingerprint: item.import_fingerprint, pipelineId: item.pipeline_id,
  pipelineVersion: item.pipeline_version, datasetName: item.dataset_name,
  datasetSplit: item.dataset_split, labelSpace: item.label_space,
  runCount: item.run_count, detectionCount: item.detection_count,
  archiveSha256: item.archive_sha256, resultProvenance: item.result_provenance,
  transportProvenance: item.transport_provenance, ready: item.ready,
  inconsistencyReasons: item.inconsistency_reasons,
});

const mapImportedResolution = (item: ImportedBatchResolutionWire): ImportedBatchResolution => ({
  importFingerprint: item.import_fingerprint, datasetName: item.dataset_name,
  datasetSplit: item.dataset_split, labelSpace: item.label_space,
  pipelineId: item.pipeline_id, pipelineVersion: item.pipeline_version,
  recordingManifestHash: item.recording_manifest_hash, expectedRecordings: item.expected_recordings,
  resolvedRecordings: item.resolved_recordings, missingRecordings: item.missing_recordings,
  conflictCount: item.conflict_count,
  entries: item.entries.map((entry) => ({
    manifestOrder: entry.manifest_order, recordingId: entry.recording_id,
    recordingName: entry.recording_name, analysisRunId: entry.analysis_run_id, itemKey: entry.item_key,
  })),
});

export async function listDatasetBenchmarks(): Promise<DatasetEvaluation[]> {
  return (await apiGet<DatasetEvaluationWire[]>("/api/dataset-benchmarks")).map(mapDatasetEvaluation);
}

export async function getDatasetBenchmark(id: string): Promise<DatasetEvaluation> {
  return mapDatasetEvaluation(await apiGet<DatasetEvaluationWire>(`/api/dataset-benchmarks/${id}`));
}

export async function listDatasetBenchmarkItems(id: string): Promise<DatasetEvaluationItem[]> {
  return (await apiGet<DatasetEvaluationItemWire[]>(`/api/dataset-benchmarks/${id}/items`))
    .map(mapDatasetEvaluationItem);
}

export async function listImportedBenchmarkBatches(): Promise<ImportedBenchmarkBatch[]> {
  return (await apiGet<ImportedBenchmarkBatchWire[]>("/api/dataset-benchmarks/imported-batches"))
    .map(mapImportedBatch);
}

export async function resolveImportedBenchmarkBatch(importFingerprint: string): Promise<ImportedBatchResolution> {
  const wire = await apiPostJson<ImportedBatchResolutionWire>(
    "/api/dataset-benchmarks/resolve-imported-batch",
    { import_fingerprint: importFingerprint },
  );
  return mapImportedResolution(wire);
}

export async function runDatasetBenchmark(id: string): Promise<DatasetEvaluation> {
  return mapDatasetEvaluation(await apiPostJson<DatasetEvaluationWire>(`/api/dataset-benchmarks/${id}/run`, {}));
}

export async function retryDatasetBenchmark(id: string): Promise<DatasetEvaluation> {
  return mapDatasetEvaluation(await apiPostJson<DatasetEvaluationWire>(`/api/dataset-benchmarks/${id}/retry`, {}));
}

export async function compareDatasetBenchmarks(a: string, b: string): Promise<DatasetBenchmarkCompareResult> {
  const wire = await apiPostJson<DatasetBenchmarkCompareWire>("/api/dataset-benchmarks/compare", {
    evaluation_a_id: a, evaluation_b_id: b,
  });
  return {
    comparable: wire.comparable, reasons: wire.reasons, evaluationAId: wire.evaluation_a_id,
    evaluationBId: wire.evaluation_b_id, aggregateA: mapAggregate(wire.aggregate_a),
    aggregateB: mapAggregate(wire.aggregate_b), deltas: wire.deltas,
  };
}
```

`createDatasetBenchmark()` remains the concrete function shown above. Run/retry/compare use the existing endpoints exactly; no frontend-generated protocol field.

- [ ] **Step 3: Type-check with production build before UI consumers exist**

```bash
npm run build
```

Expected: PASS.

- [ ] **Step 4: Commit Task 2**

```bash
git add frontend/src/api/types.ts frontend/src/api/client.ts
git commit -m "feat: add dataset benchmark frontend client"
```

---

### Task 3: Extract Case Analysis and add single-run inspection/query hydration

**Files:**
- Create: `frontend/src/features/algorithm-lab/CaseAnalysisView.tsx`
- Create: `frontend/src/features/algorithm-lab/CaseAnalysisView.test.tsx`
- Modify: `frontend/src/pages/AlgorithmLabPage.tsx` later only enough to render the extracted view during this task
- Move/adapt assertions from: `frontend/src/pages/AlgorithmLabPage.test.tsx`

**Interfaces:**
- Consumes controlled props:

```ts
interface CaseAnalysisViewProps {
  recordingId?: string;
  runAId?: string;
  runBId?: string;
  onRecordingChange: (recordingId?: string) => void;
  onRunAChange: (runId?: string) => void;
  onRunBChange: (runId?: string) => void;
}
```

- Produces single-run inspection for Recording+Run A and existing A/B comparison for Recording+Run A+Run B.

- [ ] **Step 1: Write tests for existing A/B regression plus new single-run mode**

Create `CaseAnalysisView.test.tsx` by moving the existing fetch fixtures/assertions from `AlgorithmLabPage.test.tsx`. Add two new tests:

```ts
type CaseFetchOptions = {
  recordingPage?: Array<typeof recording>;
  directRecordings?: Record<string, typeof recording>;
  runsByRecording?: Record<string, typeof completedRuns>;
};

function setupCaseFetch(options: CaseFetchOptions = {}) {
  const requests: string[] = [];
  const recordingPage = options.recordingPage ?? [recording];
  const directRecordings = options.directRecordings ?? { rec1: recording };
  const runsByRecording = options.runsByRecording ?? { rec1: completedRuns };
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const urlStr = String(url);
    requests.push(urlStr);
    if (urlStr.includes("/api/recordings?limit=")) {
      return new Response(JSON.stringify({ items: recordingPage, total: recordingPage.length }));
    }
    const directMatch = urlStr.match(/\/api\/recordings\/([^/?]+)$/);
    if (directMatch) {
      const item = directRecordings[decodeURIComponent(directMatch[1])];
      if (!item) return new Response("not found", { status: 404 });
      return new Response(JSON.stringify(item));
    }
    if (urlStr.includes("/api/analysis-runs?recording_id=")) {
      const id = new URL(urlStr).searchParams.get("recording_id") ?? "";
      return new Response(JSON.stringify(runsByRecording[id] ?? []));
    }
    if (urlStr.endsWith("/api/algorithm-lab/compare") && init?.method === "POST") {
      return new Response(JSON.stringify(compareResponse));
    }
    if (urlStr.includes("/spectrogram")) return new Response(JSON.stringify(spectrogram));
    if (urlStr.endsWith("/ground-truth")) return new Response(JSON.stringify(groundTruth));
    if (urlStr.endsWith("/analysis-runs/run_a/detections") || urlStr.endsWith("/analysis-runs/run_2500/detections")) {
      return new Response(JSON.stringify(detectionsA));
    }
    if (urlStr.endsWith("/analysis-runs/run_b/detections")) return new Response(JSON.stringify(detectionsB));
    throw new Error(`Unexpected request: ${urlStr}`);
  }));
  return requests;
}

test("loads one frozen run for inspection without requiring Run B", async () => {
  const requests = setupCaseFetch();
  render(
    <MemoryRouter>
      <CaseAnalysisView
        recordingId="rec1"
        runAId="run_a"
        onRecordingChange={() => undefined}
        onRunAChange={() => undefined}
        onRunBChange={() => undefined}
      />
    </MemoryRouter>,
  );
  expect(await screen.findByText(/Select Run B to compare/)).toBeInTheDocument();
  expect(await screen.findByText(/Run A:/)).toBeInTheDocument();
  expect(requests.some((url) => url.endsWith("/api/analysis-runs/run_a/detections"))).toBe(true);
  expect(requests.some((url) => url.endsWith("/api/algorithm-lab/compare"))).toBe(false);
});

test("hydrates a query-selected recording even when it is not in the first 500 list", async () => {
  const selected = { ...recording, id: "rec2500", name: "Sample 2499" };
  const run2500 = { ...completedRuns[0], id: "run_2500", recording_id: "rec2500" };
  const requests = setupCaseFetch({
    recordingPage: [recording],
    directRecordings: { rec2500: selected },
    runsByRecording: { rec2500: [run2500] },
  });
  render(
    <MemoryRouter>
      <CaseAnalysisView
        recordingId="rec2500"
        runAId="run_2500"
        onRecordingChange={() => undefined}
        onRunAChange={() => undefined}
        onRunBChange={() => undefined}
      />
    </MemoryRouter>,
  );
  expect(await screen.findByText("Sample 2499")).toBeInTheDocument();
  expect(requests.some((url) => url.endsWith("/api/recordings/rec2500"))).toBe(true);
  expect(requests.some((url) => url.includes("recording_id=rec2500"))).toBe(true);
});
```

- [ ] **Step 2: Run page test and confirm RED**

```bash
npm test -- --run src/pages/AlgorithmLabPage.test.tsx
```

- [ ] **Step 3: Implement tabs using `useSearchParams`**

The page should be small and contain state coordination only:

```ts
export function AlgorithmLabPage() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "benchmarks" ? "benchmarks" : "case";
  const recordingId = params.get("recording") ?? undefined;
  const runAId = params.get("runA") ?? undefined;
  const runBId = params.get("runB") ?? undefined;
  const benchmarkId = params.get("benchmark") ?? undefined;

  const patch = (changes: Record<string, string | undefined>) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes)) {
      if (value === undefined) next.delete(key); else next.set(key, value);
    }
    setParams(next);
  };

  return (
    <Tabs
      activeKey={tab}
      onChange={(key) => patch({ tab: key })}
      items={[
        {
          key: "case",
          label: "Case Analysis",
          children: (
            <CaseAnalysisView
              recordingId={recordingId}
              runAId={runAId}
              runBId={runBId}
              onRecordingChange={(id) => patch({ recording: id, runA: undefined, runB: undefined })}
              onRunAChange={(id) => patch({ runA: id })}
              onRunBChange={(id) => patch({ runB: id })}
            />
          ),
        },
        {
          key: "benchmarks",
          label: "Dataset Benchmarks",
          children: (
            <DatasetBenchmarksView
              selectedBenchmarkId={benchmarkId}
              onBenchmarkOpen={(id) => patch({ tab: "benchmarks", benchmark: id })}
              onOpenCase={(rec, a, b) => patch({ tab: "case", recording: rec, runA: a, runB: b })}
            />
          ),
        },
      ]}
    />
  );
}
```

When switching tabs, keep benchmark/case query values unless a user action explicitly changes them; this makes browser Back useful. Do not add a new `App.tsx` route.

- [ ] **Step 4: Run page + app route tests**

```bash
npm test -- --run src/pages/AlgorithmLabPage.test.tsx src/app/App.test.tsx
```

Expected: PASS.

- [ ] **Step 5: Commit Task 4**

```bash
git add frontend/src/pages/AlgorithmLabPage.tsx frontend/src/pages/AlgorithmLabPage.test.tsx
git commit -m "feat: add algorithm lab benchmark tab state"
```

---

### Task 5: Build Dataset Benchmark list and Imported Batch create/run flow

**Files:**
- Create: `frontend/src/features/dataset-benchmarks/BenchmarkListTable.tsx`
- Create: `frontend/src/features/dataset-benchmarks/BenchmarkCreatePanel.tsx`
- Create: `frontend/src/features/dataset-benchmarks/DatasetBenchmarksView.tsx`
- Create/extend: `frontend/src/features/dataset-benchmarks/DatasetBenchmarksView.test.tsx`

**Interfaces:**
- `DatasetBenchmarksView` props:

```ts
interface DatasetBenchmarksViewProps {
  selectedBenchmarkId?: string;
  onBenchmarkOpen: (evaluationId?: string) => void;
  onOpenCase: (recordingId: string, runAId: string, runBId?: string) => void;
}
```

- [ ] **Step 1: Write failing list/catalog/create lifecycle test**

Add this concrete test to `DatasetBenchmarksView.test.tsx` (wire payloads intentionally use backend snake_case):

```tsx
test("resolves a ready imported batch and creates then runs a v2 benchmark", async () => {
  const onBenchmarkOpen = vi.fn();
  const onOpenCase = vi.fn();
  const requests: Array<{ url: string; method: string; body?: string }> = [];
  const fingerprint = "a".repeat(64);
  const manifest = "b".repeat(64);
  const entries = Array.from({ length: 2500 }, (_, index) => ({
    manifest_order: index,
    recording_id: `rec_${index}`,
    recording_name: String(index),
    analysis_run_id: `run_${index}`,
    item_key: String(index),
  }));
  const catalog = [{
    import_fingerprint: fingerprint, pipeline_id: "zoomspec_yolo26n_aug_combined_frn_v3",
    pipeline_version: "1.0.0", dataset_name: "SpaceNet", dataset_split: "test",
    label_space: "spacenet_14", run_count: 2500, detection_count: 33373,
    archive_sha256: "c".repeat(64), result_provenance: {}, transport_provenance: {},
    ready: true, inconsistency_reasons: [],
  }];
  const resolution = {
    import_fingerprint: fingerprint, dataset_name: "SpaceNet", dataset_split: "test",
    label_space: "spacenet_14", pipeline_id: "zoomspec_yolo26n_aug_combined_frn_v3",
    pipeline_version: "1.0.0", recording_manifest_hash: manifest,
    expected_recordings: 2500, resolved_recordings: 2500, missing_recordings: 0, conflict_count: 0,
    entries,
  };
  const evaluation = {
    id: "eval_real", name: "Real benchmark", dataset_name: "SpaceNet", dataset_split: "test",
    label_space: "spacenet_14", pipeline_id: "zoomspec_yolo26n_aug_combined_frn_v3",
    pipeline_version: "1.0.0", status: "pending", expected_recordings: 2500,
    evaluated_recordings: 2500, missing_recordings: 0, coverage: 1, comparable: true,
    recording_manifest_hash: manifest, evaluation_protocol: "physical_tf_detection_ap_v2",
    protocol_config_json: { gt_duplicate_policy: "exact_physical_class_dedup" },
    aggregate_metrics_json: null, per_class_metrics_json: null, confusion_json: null,
    progress_stage: null, progress_current: null, progress_total: null, worker_pid: null,
    error_type: null, error_message: null, created_at: "2026-09-06T00:00:00Z",
    started_at: null, completed_at: null,
  };

  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const urlStr = String(url);
    const method = init?.method ?? "GET";
    requests.push({ url: urlStr, method, body: init?.body as string | undefined });
    if (urlStr.endsWith("/api/dataset-benchmarks/imported-batches")) return new Response(JSON.stringify(catalog));
    if (urlStr.endsWith("/api/dataset-benchmarks/resolve-imported-batch")) return new Response(JSON.stringify(resolution));
    if (urlStr.endsWith("/api/dataset-benchmarks") && method === "GET") return new Response("[]");
    if (urlStr.endsWith("/api/dataset-benchmarks") && method === "POST") return new Response(JSON.stringify(evaluation), { status: 201 });
    if (urlStr.endsWith("/api/dataset-benchmarks/eval_real/run")) {
      return new Response(JSON.stringify({ ...evaluation, status: "running", progress_stage: "loading" }), { status: 202 });
    }
    throw new Error(`Unexpected request: ${method} ${urlStr}`);
  }));

  render(
    <MemoryRouter>
      <DatasetBenchmarksView
        onBenchmarkOpen={onBenchmarkOpen}
        onOpenCase={onOpenCase}
      />
    </MemoryRouter>,
  );

  fireEvent.click(await screen.findByRole("button", { name: "New Benchmark" }));
  fireEvent.mouseDown(await screen.findByLabelText("Imported Analysis Batch"));
  fireEvent.click(await screen.findByText(/zoomspec_yolo26n_aug_combined_frn_v3/));
  expect(screen.getByText("physical_tf_detection_ap_v2")).toBeInTheDocument();
  expect(screen.queryByLabelText(/Protocol selector/i)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Resolve" }));
  expect(await screen.findByText("2500 / 2500")).toBeInTheDocument();
  expect(screen.getByText(manifest)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Benchmark Name"), { target: { value: "Real benchmark" } });
  fireEvent.click(screen.getByRole("button", { name: "Create & Run" }));
  await waitFor(() => expect(onBenchmarkOpen).toHaveBeenCalledWith("eval_real"));

  const createCall = requests.find((item) => item.url.endsWith("/api/dataset-benchmarks") && item.method === "POST");
  expect(createCall).toBeDefined();
  const body = JSON.parse(createCall!.body!);
  expect(body.recording_manifest_hash).toBe(manifest);
  expect(body.items).toHaveLength(2500);
  expect(body.evaluation_protocol).toBeUndefined();
  expect(requests.some((item) => item.url.endsWith("/api/dataset-benchmarks/eval_real/run"))).toBe(true);
});
```

- [ ] **Step 2: Run test and confirm RED**

```bash
npm test -- --run src/features/dataset-benchmarks/DatasetBenchmarksView.test.tsx
```

- [ ] **Step 3: Implement list table with status-derived actions**

`BenchmarkListTable.tsx` must render name, pipeline/version, dataset/split, protocol, coverage, status, created time, and class-aware `mAP50:95` only when applicable/completed.

Action rule must be explicit:

```ts
function actionLabel(status: DatasetEvaluationStatus): string {
  if (status === "pending") return "Run";
  if (status === "running") return "View Progress";
  if (status === "completed") return "Open";
  return "Retry";
}
```

Use that rule in a concrete table component:

```tsx
interface BenchmarkListTableProps {
  items: DatasetEvaluation[];
  selectedIds: string[];
  onSelectedIdsChange: (ids: string[]) => void;
  onOpen: (id: string) => void;
  onRun: (id: string) => void;
  onRetry: (id: string) => void;
}

export function BenchmarkListTable(props: BenchmarkListTableProps) {
  return (
    <Table
      rowKey="id"
      dataSource={props.items}
      pagination={{ pageSize: 20 }}
      rowSelection={{
        selectedRowKeys: props.selectedIds,
        onChange: (keys) => props.onSelectedIdsChange(keys.slice(-2).map(String)),
        getCheckboxProps: (row) => ({ disabled: row.status !== "completed" }),
      }}
      columns={[
        { title: "Name", dataIndex: "name" },
        { title: "Pipeline", render: (_, row) => `${row.pipelineId} · ${row.pipelineVersion}` },
        { title: "Dataset", render: (_, row) => `${row.datasetName} / ${row.datasetSplit}` },
        { title: "Protocol", dataIndex: "evaluationProtocol" },
        { title: "Coverage", render: (_, row) => `${row.evaluatedRecordings}/${row.expectedRecordings}` },
        { title: "Status", dataIndex: "status" },
        {
          title: "mAP50:95",
          render: (_, row) => row.status === "completed" && row.aggregateMetrics?.classAware
            ? row.aggregateMetrics.classAware.map50_95?.toFixed(4) ?? "N/A"
            : "—",
        },
        {
          title: "Action",
          render: (_, row) => (
            <Button onClick={() => {
              if (row.status === "pending") props.onRun(row.id);
              else if (row.status === "failed" || row.status === "interrupted") props.onRetry(row.id);
              else props.onOpen(row.id);
            }}>
              {actionLabel(row.status)}
            </Button>
          ),
        },
      ]}
    />
  );
}
```

Do not allow editing completed rows.

- [ ] **Step 4: Implement Imported Batch create panel**

`BenchmarkCreatePanel.tsx` loads catalog, enables only `ready` entries, shows pipeline/dataset/run/detection counts, and calls Resolve before enabling creation.

Display protocol as literal read-only text:

```tsx
<Descriptions.Item label="Evaluation Protocol">
  <Typography.Text code>physical_tf_detection_ap_v2</Typography.Text>
</Descriptions.Item>
<Descriptions.Item label="GT Policy">exact physical/class dedup</Descriptions.Item>
```

Ready check:

```ts
const readyToCreate = Boolean(
  resolution &&
  resolution.resolvedRecordings === resolution.expectedRecordings &&
  resolution.missingRecordings === 0 &&
  resolution.conflictCount === 0 &&
  benchmarkName.trim()
);
```

Core panel state and actions must be explicit:

```tsx
export function BenchmarkCreatePanel({ onCreated }: { onCreated: (id: string) => void }) {
  const [batches, setBatches] = useState<ImportedBenchmarkBatch[]>([]);
  const [fingerprint, setFingerprint] = useState<string>();
  const [resolution, setResolution] = useState<ImportedBatchResolution>();
  const [benchmarkName, setBenchmarkName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const readyToCreate = Boolean(
    resolution &&
    resolution.resolvedRecordings === resolution.expectedRecordings &&
    resolution.missingRecordings === 0 &&
    resolution.conflictCount === 0 &&
    benchmarkName.trim()
  );

  useEffect(() => {
    void listImportedBenchmarkBatches().then(setBatches).catch((e: unknown) => setError(String(e)));
  }, []);

  const resolve = async () => {
    if (!fingerprint) return;
    setBusy(true);
    try { setResolution(await resolveImportedBenchmarkBatch(fingerprint)); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };

  const createAndRun = async () => {
    if (!resolution || !readyToCreate) return;
    setBusy(true);
    try {
      const created = await createDatasetBenchmark({ name: benchmarkName.trim(), resolution });
      const started = await runDatasetBenchmark(created.id);
      onCreated(started.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      {error ? <Alert type="error" message={error} /> : null}
      <Select
        aria-label="Imported Analysis Batch"
        value={fingerprint}
        onChange={(value) => { setFingerprint(value); setResolution(undefined); }}
        options={batches.map((batch) => ({
          value: batch.importFingerprint,
          disabled: !batch.ready,
          label: batch.ready
            ? `${batch.pipelineId} · ${batch.datasetName}/${batch.datasetSplit} · ${batch.runCount} runs`
            : `${batch.importFingerprint.slice(0, 12)}… · invalid provenance`,
        }))}
      />
      <Descriptions column={1} size="small">
        <Descriptions.Item label="Evaluation Protocol"><Typography.Text code>physical_tf_detection_ap_v2</Typography.Text></Descriptions.Item>
        <Descriptions.Item label="GT Policy">exact physical/class dedup</Descriptions.Item>
        {resolution ? <>
          <Descriptions.Item label="Resolved">{resolution.resolvedRecordings} / {resolution.expectedRecordings}</Descriptions.Item>
          <Descriptions.Item label="Missing">{resolution.missingRecordings}</Descriptions.Item>
          <Descriptions.Item label="Conflicts">{resolution.conflictCount}</Descriptions.Item>
          <Descriptions.Item label="Manifest SHA256">{resolution.recordingManifestHash}</Descriptions.Item>
        </> : null}
      </Descriptions>
      <Button onClick={() => void resolve()} disabled={!fingerprint} loading={busy}>Resolve</Button>
      <Input aria-label="Benchmark Name" value={benchmarkName} onChange={(e) => setBenchmarkName(e.target.value)} />
      <Button type="primary" onClick={() => void createAndRun()} disabled={!readyToCreate} loading={busy}>Create & Run</Button>
    </Space>
  );
}
```

- [ ] **Step 5: Implement feature coordinator**

`DatasetBenchmarksView.tsx` owns list/create selection and refreshes after run/retry:

```tsx
export function DatasetBenchmarksView({ selectedBenchmarkId, onBenchmarkOpen, onOpenCase }: DatasetBenchmarksViewProps) {
  const [items, setItems] = useState<DatasetEvaluation[]>([]);
  const [creating, setCreating] = useState(false);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);

  const refresh = useCallback(async () => setItems(await listDatasetBenchmarks()), []);
  useEffect(() => { void refresh(); }, [refresh]);

  const start = async (id: string) => { await runDatasetBenchmark(id); await refresh(); onBenchmarkOpen(id); };
  const retry = async (id: string) => { await retryDatasetBenchmark(id); await runDatasetBenchmark(id); await refresh(); onBenchmarkOpen(id); };

  if (selectedBenchmarkId) {
    return (
      <Card title="Dataset Benchmark">
        <Button onClick={() => onBenchmarkOpen(undefined)}>Back to list</Button>
        <Typography.Paragraph>Benchmark started: {selectedBenchmarkId}</Typography.Paragraph>
      </Card>
    );
  }

  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <Space><Typography.Title level={3}>Dataset Benchmarks</Typography.Title><Button onClick={() => setCreating(true)}>New Benchmark</Button></Space>
      {creating ? <BenchmarkCreatePanel onCreated={(id) => { setCreating(false); void refresh(); onBenchmarkOpen(id); }} /> : null}
      <BenchmarkListTable
        items={items}
        selectedIds={selectedIds}
        onSelectedIdsChange={setSelectedIds}
        onOpen={onBenchmarkOpen}
        onRun={(id) => void start(id)}
        onRetry={(id) => void retry(id)}
      />
    </Space>
  );
}
```

Task 5 intentionally uses the exact `Card` branch shown above for a selected id so the branch compiles and the Create & Run handoff is visible. Task 6 replaces only that `Card` branch with the real `BenchmarkDetailView`; there is no undefined component reference in the Task 5 commit.

- [ ] **Step 6: Run focused tests**

```bash
npm test -- --run src/features/dataset-benchmarks/DatasetBenchmarksView.test.tsx
```

Expected: PASS.

- [ ] **Step 7: Commit Task 5**

```bash
git add frontend/src/features/dataset-benchmarks/BenchmarkListTable.tsx frontend/src/features/dataset-benchmarks/BenchmarkCreatePanel.tsx frontend/src/features/dataset-benchmarks/DatasetBenchmarksView.tsx frontend/src/features/dataset-benchmarks/DatasetBenchmarksView.test.tsx
git commit -m "feat: add dataset benchmark creation flow"
```

---

### Task 6: Build truthful progress and completed Benchmark Detail

**Files:**
- Create: `frontend/src/features/dataset-benchmarks/BenchmarkDetailView.tsx`
- Modify: `frontend/src/features/dataset-benchmarks/DatasetBenchmarksView.tsx`
- Modify: `frontend/src/features/dataset-benchmarks/DatasetBenchmarksView.test.tsx`

**Interfaces:**
- Consumes: `getDatasetBenchmark()`, `listDatasetBenchmarkItems()`, `retryDatasetBenchmark()`, `runDatasetBenchmark()`.
- Produces: progress polling, completed metrics/provenance/per-class/confusion/items, item inspect callback.

- [ ] **Step 1: Add failing polling terminal-state test with fake timers**

Add a small wire factory plus the terminal polling test to `DatasetBenchmarksView.test.tsx`:

```tsx
function benchmarkWire(overrides: Record<string, unknown> = {}) {
  return {
    id: "eval_real", name: "Real benchmark", dataset_name: "SpaceNet", dataset_split: "test",
    label_space: "spacenet_14", pipeline_id: "zoomspec_yolo26n_aug_combined_frn_v3",
    pipeline_version: "1.0.0", status: "running", expected_recordings: 2500, evaluated_recordings: 2500,
    missing_recordings: 0, coverage: 1, comparable: true, recording_manifest_hash: "b".repeat(64),
    evaluation_protocol: "physical_tf_detection_ap_v2",
    protocol_config_json: { gt_duplicate_policy: "exact_physical_class_dedup" },
    aggregate_metrics_json: null, per_class_metrics_json: null, confusion_json: null,
    progress_stage: "class_aware_ap", progress_current: null, progress_total: null, worker_pid: null,
    error_type: null, error_message: null, created_at: "2026-09-06T00:00:00Z",
    started_at: "2026-09-06T00:00:01Z", completed_at: null,
    ...overrides,
  };
}

test("polls only while pending/running and stops after completed", async () => {
  vi.useFakeTimers();
  try {
    let detailCalls = 0;
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const urlStr = String(url);
      if (urlStr.endsWith("/api/dataset-benchmarks/eval_real")) {
        detailCalls += 1;
        return new Response(JSON.stringify(
          detailCalls === 1
            ? benchmarkWire()
            : benchmarkWire({
                status: "completed", progress_stage: "completed", completed_at: "2026-09-06T00:01:00Z",
                aggregate_metrics_json: {
                  ground_truth: { raw_count: 20018, canonical_count: 19962, duplicates_removed: 56, duplicate_policy: "exact_physical_class_dedup" },
                  classification_applicable: true, classification_reason: null,
                  localization: { ap50: 0.7, ap50_95: 0.5, operating: { tp: 1, fp: 0, fn: 0, precision: 1, recall: 1, f1: 1 } },
                  classification_on_matched: { matched_count: 1, class_correct: 1, class_wrong: 0, matched_accuracy: 1 },
                  class_aware: { map50: 0.6, map50_95: 0.4, operating: { tp: 1, fp: 0, fn: 0, precision: 1, recall: 1, f1: 1 } },
                },
                per_class_metrics_json: [], confusion_json: [],
              }),
        ));
      }
      if (urlStr.endsWith("/api/dataset-benchmarks/eval_real/items")) return new Response("[]");
      throw new Error(`Unexpected request: ${urlStr}`);
    }));

    render(<BenchmarkDetailView evaluationId="eval_real" onBack={() => undefined} onOpenCase={() => undefined} />);
    await act(async () => { await Promise.resolve(); });
    expect(screen.getByText("class_aware_ap")).toBeInTheDocument();
    expect(screen.queryByText(/\d+%/)).not.toBeInTheDocument();

    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(detailCalls).toBe(2);
    const callsAtCompletion = detailCalls;
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(detailCalls).toBe(callsAtCompletion);
  } finally {
    vi.useRealTimers();
  }
});
```

- [ ] **Step 2: Add failing completed-detail, v1, and detection-only assertions**

Add a completed payload helper and three concrete assertions:

```tsx
const operating = { tp: 10, fp: 2, fn: 3, precision: 0.8333, recall: 0.7692, f1: 0.8 };
const perClassWire = Array.from({ length: 14 }, (_, classId) => ({
  class_id: classId, class_name: `Class ${classId}`, gt_count: 10 + classId, prediction_count: 12 + classId,
  ap50: 0.5, ap50_95: 0.4, operating,
}));

function completedBenchmarkWire({ v1 = false, detectionOnly = false } = {}) {
  return benchmarkWire({
    status: "completed", progress_stage: "completed", completed_at: "2026-09-06T00:01:00Z",
    evaluation_protocol: v1 ? "physical_tf_detection_ap_v1" : "physical_tf_detection_ap_v2",
    protocol_config_json: v1 ? {} : { gt_duplicate_policy: "exact_physical_class_dedup" },
    aggregate_metrics_json: {
      ...(v1 ? {} : { ground_truth: { raw_count: 20018, canonical_count: 19962, duplicates_removed: 56, duplicate_policy: "exact_physical_class_dedup" } }),
      classification_applicable: !detectionOnly,
      classification_reason: detectionOnly ? "detection_only_pipeline" : null,
      localization: { ap50: 0.6, ap50_95: 0.45, operating },
      classification_on_matched: detectionOnly ? null : { matched_count: 100, class_correct: 80, class_wrong: 20, matched_accuracy: 0.8 },
      class_aware: detectionOnly ? null : { map50: 0.49706861157413673, map50_95: 0.37325127587379914, operating },
    },
    per_class_metrics_json: detectionOnly ? [] : perClassWire,
    confusion_json: detectionOnly ? null : [{ gt_class_id: 9, gt_class_name: "LoRa", pred_class_id: 8, pred_class_name: "Zigbee", count: 7 }],
  });
}

function stubCompletedDetail(payload: object) {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const urlStr = String(url);
    if (urlStr.endsWith("/api/dataset-benchmarks/eval_real")) return new Response(JSON.stringify(payload));
    if (urlStr.endsWith("/api/dataset-benchmarks/eval_real/items")) {
      return new Response(JSON.stringify([{
        id: "item_0", evaluation_id: "eval_real", manifest_order: 0, recording_id: "rec_0",
        recording_name: "0", analysis_run_id: "run_0", status: "included", gt_count: 6, prediction_count: 13, error_reason: null,
      }]));
    }
    throw new Error(`Unexpected request: ${urlStr}`);
  }));
}

test("renders completed v2 GT provenance, metrics, per-class rows, confusions and protocol", async () => {
  stubCompletedDetail(completedBenchmarkWire());
  render(<BenchmarkDetailView evaluationId="eval_real" onBack={() => undefined} onOpenCase={() => undefined} />);
  expect(await screen.findByText("End-to-End Class-aware mAP50:95")).toBeInTheDocument();
  expect(screen.getByText("20018")).toBeInTheDocument();
  expect(screen.getByText("19962")).toBeInTheDocument();
  expect(screen.getByText("56")).toBeInTheDocument();
  expect(await screen.findByText("Class 13")).toBeInTheDocument();
  expect(screen.getByText("Top Classification Confusions")).toBeInTheDocument();
  expect(screen.getByText("physical_tf_detection_ap_v2")).toBeInTheDocument();
  expect(screen.getByText("b".repeat(64))).toBeInTheDocument();
});

test("renders old v1 as raw-GT protocol without inventing dedup provenance", async () => {
  stubCompletedDetail(completedBenchmarkWire({ v1: true }));
  render(<BenchmarkDetailView evaluationId="eval_real" onBack={() => undefined} onOpenCase={() => undefined} />);
  expect(await screen.findByText("Raw GT protocol")).toBeInTheDocument();
  expect(screen.queryByText("Exact duplicates removed")).not.toBeInTheDocument();
});

test("renders classification metrics as N/A for detection-only evaluations", async () => {
  stubCompletedDetail(completedBenchmarkWire({ detectionOnly: true }));
  render(<BenchmarkDetailView evaluationId="eval_real" onBack={() => undefined} onOpenCase={() => undefined} />);
  await screen.findByText("Localization");
  expect(screen.getAllByText("N/A").length).toBeGreaterThanOrEqual(2);
});
```

- [ ] **Step 3: Implement polling with cleanup and terminal item loading**

Create `BenchmarkDetailView.tsx` with explicit props/state and one polling chain:

```tsx
interface BenchmarkDetailViewProps {
  evaluationId: string;
  onBack: () => void;
  onOpenCase: (recordingId: string, runAId: string, runBId?: string) => void;
}

export function BenchmarkDetailView({ evaluationId, onBack, onOpenCase }: BenchmarkDetailViewProps) {
  const [evaluation, setEvaluation] = useState<DatasetEvaluation>();
  const [items, setItems] = useState<DatasetEvaluationItem[]>([]);
  const [error, setError] = useState<string>();
  const [pollGeneration, setPollGeneration] = useState(0);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const load = async () => {
      const next = await getDatasetBenchmark(evaluationId);
      if (cancelled) return;
      setEvaluation(next);
      if (next.status === "completed") {
        const nextItems = await listDatasetBenchmarkItems(evaluationId);
        if (!cancelled) setItems(nextItems);
        return;
      }
      if (next.status === "pending" || next.status === "running") {
        timer = setTimeout(() => void load().catch((e) => setError(String(e))), 1000);
      }
    };

    void load().catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [evaluationId, pollGeneration]);

  if (error) return <Alert type="error" showIcon message={error} />;
  if (!evaluation) return <Spin tip="Loading benchmark..." />;

  if (evaluation.status === "pending" || evaluation.status === "running") {
    return (
      <Card title={evaluation.name}>
        <Button onClick={onBack}>Back to list</Button>
        <Descriptions column={1}>
          <Descriptions.Item label="Status">{evaluation.status}</Descriptions.Item>
          <Descriptions.Item label="Stage">{evaluation.progressStage ?? "pending"}</Descriptions.Item>
          <Descriptions.Item label="Protocol">{evaluation.evaluationProtocol}</Descriptions.Item>
          <Descriptions.Item label="Coverage">{evaluation.evaluatedRecordings} / {evaluation.expectedRecordings}</Descriptions.Item>
        </Descriptions>
      </Card>
    );
  }

  if (evaluation.status === "failed" || evaluation.status === "interrupted") {
    const retry = async () => {
      try {
        await retryDatasetBenchmark(evaluation.id);
        const restarted = await runDatasetBenchmark(evaluation.id);
        setEvaluation(restarted);
        setPollGeneration((value) => value + 1);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    };
    return (
      <Card title={evaluation.name}>
        <Button onClick={onBack}>Back to list</Button>
        <Alert
          type="error"
          showIcon
          message={evaluation.errorType ?? "Benchmark failed"}
          description={evaluation.errorMessage ?? undefined}
        />
        <Button onClick={() => void retry()}>Retry</Button>
      </Card>
    );
  }

  const aggregate = evaluation.aggregateMetrics;
  if (!aggregate) return <Alert type="error" message="Completed benchmark has no aggregate metrics." />;
  const gt = aggregate.groundTruth;
  const confusions = [...(evaluation.confusion ?? [])].sort((a, b) => b.count - a.count);
  const perClass = evaluation.perClassMetrics ?? [];

  // The completed return is implemented in Step 4 below.
  return renderCompletedBenchmark({ evaluation, aggregate, gt, perClass, confusions, items, onBack, onOpenCase });
}
```

Do not render a synthetic numeric progress percentage. Only backend `progressStage` / current / total values are truthful.

- [ ] **Step 4: Implement the completed detail hierarchy as a focused render function**

In the same file, add this renderer (small local helpers such as `fmtMetric()` are fine but must preserve `null` as `N/A`):

```tsx
function fmtMetric(value: number | null | undefined): string {
  return value == null ? "N/A" : value.toFixed(4);
}

function renderCompletedBenchmark(args: {
  evaluation: DatasetEvaluation;
  aggregate: DatasetBenchmarkAggregateMetrics;
  gt?: GroundTruthProvenance;
  perClass: DatasetBenchmarkPerClassMetric[];
  confusions: DatasetBenchmarkConfusion[];
  items: DatasetEvaluationItem[];
  onBack: () => void;
  onOpenCase: (recordingId: string, runAId: string, runBId?: string) => void;
}) {
  const { evaluation, aggregate, gt, perClass, confusions, items, onBack, onOpenCase } = args;
  const classAware = aggregate.classAware;
  const matched = aggregate.classificationOnMatched;
  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Space><Button onClick={onBack}>Back to list</Button><Typography.Title level={3}>{evaluation.name}</Typography.Title></Space>

      <Row gutter={16}>
        <Col span={6}><Statistic title="End-to-End Class-aware mAP50:95" value={fmtMetric(classAware?.map50_95)} /></Col>
        <Col span={6}><Statistic title="mAP50" value={fmtMetric(classAware?.map50)} /></Col>
        <Col span={6}><Statistic title="Localization AP50:95" value={fmtMetric(aggregate.localization.ap50_95)} /></Col>
        <Col span={6}><Statistic title="Matched Accuracy" value={fmtMetric(matched?.matchedAccuracy)} /></Col>
      </Row>

      <Card title="Ground Truth Provenance">
        {gt ? (
          <Descriptions column={4}>
            <Descriptions.Item label="Raw annotations">{gt.rawCount}</Descriptions.Item>
            <Descriptions.Item label="Evaluation GT">{gt.canonicalCount}</Descriptions.Item>
            <Descriptions.Item label="Exact duplicates removed">{gt.duplicatesRemoved}</Descriptions.Item>
            <Descriptions.Item label="Policy">{gt.duplicatePolicy}</Descriptions.Item>
          </Descriptions>
        ) : <Typography.Text>Raw GT protocol</Typography.Text>}
      </Card>

      <Row gutter={16}>
        <Col span={8}><Card title="Localization"><Descriptions column={1}>
          <Descriptions.Item label="AP50">{fmtMetric(aggregate.localization.ap50)}</Descriptions.Item>
          <Descriptions.Item label="AP50:95">{fmtMetric(aggregate.localization.ap50_95)}</Descriptions.Item>
          <Descriptions.Item label="P / R / F1">{`${fmtMetric(aggregate.localization.operating.precision)} / ${fmtMetric(aggregate.localization.operating.recall)} / ${fmtMetric(aggregate.localization.operating.f1)}`}</Descriptions.Item>
        </Descriptions></Card></Col>
        <Col span={8}><Card title="Classification on Matched">{matched ? <Descriptions column={1}>
          <Descriptions.Item label="Matched">{matched.matchedCount}</Descriptions.Item>
          <Descriptions.Item label="Correct / Wrong">{matched.classCorrect} / {matched.classWrong}</Descriptions.Item>
          <Descriptions.Item label="Accuracy">{fmtMetric(matched.matchedAccuracy)}</Descriptions.Item>
        </Descriptions> : <Typography.Text>N/A</Typography.Text>}</Card></Col>
        <Col span={8}><Card title="End-to-End">{classAware ? <Descriptions column={1}>
          <Descriptions.Item label="mAP50:95">{fmtMetric(classAware.map50_95)}</Descriptions.Item>
          <Descriptions.Item label="P / R / F1">{`${fmtMetric(classAware.operating.precision)} / ${fmtMetric(classAware.operating.recall)} / ${fmtMetric(classAware.operating.f1)}`}</Descriptions.Item>
        </Descriptions> : <Typography.Text>N/A</Typography.Text>}</Card></Col>
      </Row>

      <Card title="Per-Class Metrics"><Table
        rowKey="classId"
        pagination={false}
        dataSource={[...perClass].sort((a, b) => a.classId - b.classId)}
        columns={[
          { title: "Class", render: (_, row) => `${row.classId} · ${row.className}` },
          { title: "GT", dataIndex: "gtCount" },
          { title: "Pred", dataIndex: "predictionCount" },
          { title: "AP50", render: (_, row) => fmtMetric(row.ap50) },
          { title: "AP50:95", render: (_, row) => fmtMetric(row.ap50_95), sorter: (a, b) => (a.ap50_95 ?? -1) - (b.ap50_95 ?? -1) },
          { title: "P", render: (_, row) => fmtMetric(row.operating.precision) },
          { title: "R", render: (_, row) => fmtMetric(row.operating.recall) },
          { title: "F1", render: (_, row) => fmtMetric(row.operating.f1) },
        ]}
      /></Card>

      <Card title="Top Classification Confusions"><Table
        rowKey={(row) => `${row.gtClassId}-${row.predClassId}`}
        pagination={{ pageSize: 10 }}
        dataSource={confusions}
        columns={[
          { title: "GT", render: (_, row) => `${row.gtClassId} · ${row.gtClassName}` },
          { title: "Pred", render: (_, row) => `${row.predClassId} · ${row.predClassName}` },
          { title: "Count", dataIndex: "count" },
        ]}
      /></Card>

      <Collapse items={[{ key: "protocol", label: "Protocol & Provenance", children: (
        <Descriptions column={1}>
          <Descriptions.Item label="Evaluation protocol">{evaluation.evaluationProtocol}</Descriptions.Item>
          <Descriptions.Item label="Manifest SHA256">{evaluation.recordingManifestHash}</Descriptions.Item>
          <Descriptions.Item label="Pipeline">{evaluation.pipelineId} · {evaluation.pipelineVersion}</Descriptions.Item>
          <Descriptions.Item label="Protocol config"><pre>{JSON.stringify(evaluation.protocolConfig, null, 2)}</pre></Descriptions.Item>
        </Descriptions>
      ) }]} />

      <Card title={`${items.length} Evaluation Items`}><Table
        rowKey="id"
        pagination={{ pageSize: 50 }}
        dataSource={items}
        columns={[
          { title: "Recording", dataIndex: "recordingName" },
          { title: "GT", dataIndex: "gtCount" },
          { title: "Predictions", dataIndex: "predictionCount" },
          { title: "Analysis Run", dataIndex: "analysisRunId" },
          { title: "Action", render: (_, row) => (
            <Button disabled={!row.analysisRunId} onClick={() => row.analysisRunId && onOpenCase(row.recordingId, row.analysisRunId)}>Inspect</Button>
          ) },
        ]}
      /></Card>
    </Space>
  );
}
```

This exact hierarchy keeps `null` classification metrics as `N/A`, leaves v1 without fabricated dedup counts, defaults per-class rows to class id, and uses only a sorted confusion table (no heatmap).

- [ ] **Step 5: Replace Task 5's selected-id Card with the real detail component**

In `DatasetBenchmarksView.tsx`, replace only the Task 5 selected-id branch with:

```tsx
if (selectedBenchmarkId) {
  return (
    <BenchmarkDetailView
      evaluationId={selectedBenchmarkId}
      onBack={() => onBenchmarkOpen(undefined)}
      onOpenCase={onOpenCase}
    />
  );
}
```

Failed/interrupted retry is already implemented inside `BenchmarkDetailView`: it shows backend `errorType`/`errorMessage`, calls `retryDatasetBenchmark(evaluation.id)`, then `runDatasetBenchmark(evaluation.id)`, and resumes the same evaluation id. Completed evaluations never render Retry.

- [ ] **Step 6: Run focused detail tests**

```bash
npm test -- --run src/features/dataset-benchmarks/DatasetBenchmarksView.test.tsx
```

Expected: PASS.

- [ ] **Step 7: Commit Task 6**

```bash
git add frontend/src/features/dataset-benchmarks/BenchmarkDetailView.tsx frontend/src/features/dataset-benchmarks/DatasetBenchmarksView.tsx frontend/src/features/dataset-benchmarks/DatasetBenchmarksView.test.tsx
git commit -m "feat: show dataset benchmark results"
```

---

### Task 7: Add backend-authoritative benchmark comparison and A/B case drill-down

**Files:**
- Create: `frontend/src/features/dataset-benchmarks/BenchmarkComparePanel.tsx`
- Modify: `frontend/src/features/dataset-benchmarks/BenchmarkListTable.tsx`
- Modify: `frontend/src/features/dataset-benchmarks/DatasetBenchmarksView.tsx`
- Modify: `frontend/src/features/dataset-benchmarks/DatasetBenchmarksView.test.tsx`

**Interfaces:**
- Consumes: `compareDatasetBenchmarks()` and both item lists.
- Produces: lightweight aggregate comparison, backend incompatibility reasons, and `recording/runA/runB` drill-down.

- [ ] **Step 1: Add failing incompatible-comparison test**

Add a direct panel test so backend reasons are the only comparability authority:

```tsx
test("shows backend incompatibility reasons and no metric table", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const urlStr = String(url);
    if (urlStr.endsWith("/api/dataset-benchmarks/compare") && init?.method === "POST") {
      return new Response(JSON.stringify({
        comparable: false, reasons: ["evaluation_protocol_mismatch"],
        evaluation_a_id: "eval_a", evaluation_b_id: "eval_b",
        aggregate_a: null, aggregate_b: null, deltas: {},
      }));
    }
    throw new Error(`Unexpected request: ${urlStr}`);
  }));

  render(
    <BenchmarkComparePanel
      evaluationAId="eval_a"
      evaluationBId="eval_b"
      onOpenCase={() => undefined}
    />,
  );
  expect(await screen.findByText("Not comparable")).toBeInTheDocument();
  expect(screen.getByText(/evaluation_protocol_mismatch/)).toBeInTheDocument();
  expect(screen.queryByText("Δ (B-A)")).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Add failing compatible compare + case drill-down test**

Use comparable backend aggregates and exact frozen item mappings:

```tsx
test("shows lightweight comparable metrics and drills into the two frozen runs", async () => {
  const onOpenCase = vi.fn();
  const aggregate = {
    classification_applicable: true, classification_reason: null,
    localization: { ap50: 0.6, ap50_95: 0.45, operating: { tp: 10, fp: 2, fn: 3, precision: 0.8, recall: 0.7, f1: 0.75 } },
    classification_on_matched: { matched_count: 10, class_correct: 8, class_wrong: 2, matched_accuracy: 0.8 },
    class_aware: { map50: 0.5, map50_95: 0.37, operating: { tp: 8, fp: 4, fn: 5, precision: 0.67, recall: 0.62, f1: 0.64 } },
  };
  const itemA = [{ id: "ia", evaluation_id: "eval_a", manifest_order: 0, recording_id: "rec_0", recording_name: "0", analysis_run_id: "run_a", status: "included", gt_count: 6, prediction_count: 13, error_reason: null }];
  const itemB = [{ id: "ib", evaluation_id: "eval_b", manifest_order: 0, recording_id: "rec_0", recording_name: "0", analysis_run_id: "run_b", status: "included", gt_count: 6, prediction_count: 11, error_reason: null }];

  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const urlStr = String(url);
    if (urlStr.endsWith("/api/dataset-benchmarks/compare") && init?.method === "POST") {
      return new Response(JSON.stringify({
        comparable: true, reasons: [], evaluation_a_id: "eval_a", evaluation_b_id: "eval_b",
        aggregate_a: aggregate,
        aggregate_b: { ...aggregate, class_aware: { ...aggregate.class_aware, map50_95: 0.40 } },
        deltas: { class_aware_map50_95: 0.03, class_aware_map50: 0, localization_ap50_95: 0, matched_accuracy: 0 },
      }));
    }
    if (urlStr.endsWith("/api/dataset-benchmarks/eval_a/items")) return new Response(JSON.stringify(itemA));
    if (urlStr.endsWith("/api/dataset-benchmarks/eval_b/items")) return new Response(JSON.stringify(itemB));
    throw new Error(`Unexpected request: ${urlStr}`);
  }));

  render(<BenchmarkComparePanel evaluationAId="eval_a" evaluationBId="eval_b" onOpenCase={onOpenCase} />);
  expect(await screen.findByText("Class-aware mAP50:95")).toBeInTheDocument();
  fireEvent.mouseDown(screen.getByLabelText("Compare Recording"));
  fireEvent.click(await screen.findByTitle("0"));
  fireEvent.click(screen.getByRole("button", { name: "Open Case Comparison" }));
  expect(onOpenCase).toHaveBeenCalledWith("rec_0", "run_a", "run_b");
});
```

- [ ] **Step 3: Implement two-row selection and compare panel**

`BenchmarkListTable` already caps selected completed rows to two. Add a Compare control in `DatasetBenchmarksView` only when `selectedIds.length === 2`, and render the panel below. `BenchmarkComparePanel` must ask the backend whether the pair is comparable before it loads membership:

```tsx
interface BenchmarkComparePanelProps {
  evaluationAId: string;
  evaluationBId: string;
  onOpenCase: (recordingId: string, runAId: string, runBId: string) => void;
}

export function BenchmarkComparePanel({ evaluationAId, evaluationBId, onOpenCase }: BenchmarkComparePanelProps) {
  const [result, setResult] = useState<DatasetBenchmarkCompareResult>();
  const [itemsA, setItemsA] = useState<DatasetEvaluationItem[]>([]);
  const [itemsB, setItemsB] = useState<DatasetEvaluationItem[]>([]);
  const [recordingId, setRecordingId] = useState<string>();
  const [error, setError] = useState<string>();

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      const compared = await compareDatasetBenchmarks(evaluationAId, evaluationBId);
      if (cancelled) return;
      setResult(compared);
      setRecordingId(undefined);
      if (!compared.comparable) { setItemsA([]); setItemsB([]); return; }
      const [a, b] = await Promise.all([
        listDatasetBenchmarkItems(evaluationAId),
        listDatasetBenchmarkItems(evaluationBId),
      ]);
      if (!cancelled) { setItemsA(a); setItemsB(b); }
    };
    void load().catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
    return () => { cancelled = true; };
  }, [evaluationAId, evaluationBId]);

  if (error) return <Alert type="error" message={error} />;
  if (!result) return <Spin tip="Comparing benchmarks..." />;
  if (!result.comparable) {
    return <Alert type="warning" showIcon message="Not comparable" description={result.reasons.join(", ")} />;
  }

  const a = result.aggregateA;
  const b = result.aggregateB;
  if (!a || !b) return <Alert type="error" message="Comparable benchmark pair is missing aggregate metrics." />;
  const fmt = (value: number | null | undefined) => value == null ? "N/A" : value.toFixed(4);
  const derivedDelta = (x: number | null | undefined, y: number | null | undefined) =>
    x == null || y == null ? null : y - x;
  const metricRows = [
    { key: "map5095", metric: "Class-aware mAP50:95", a: a.classAware?.map50_95, b: b.classAware?.map50_95, delta: result.deltas.class_aware_map50_95 },
    { key: "map50", metric: "Class-aware mAP50", a: a.classAware?.map50, b: b.classAware?.map50, delta: result.deltas.class_aware_map50 },
    { key: "loc", metric: "Localization AP50:95", a: a.localization.ap50_95, b: b.localization.ap50_95, delta: result.deltas.localization_ap50_95 },
    { key: "matched", metric: "Matched Accuracy", a: a.classificationOnMatched?.matchedAccuracy, b: b.classificationOnMatched?.matchedAccuracy, delta: result.deltas.matched_accuracy },
    { key: "f1", metric: "Class-aware F1", a: a.classAware?.operating.f1, b: b.classAware?.operating.f1, delta: derivedDelta(a.classAware?.operating.f1, b.classAware?.operating.f1) },
  ];

  const byB = new Map(itemsB.map((item) => [item.recordingId, item]));
  const options = itemsA.flatMap((left) => {
    const right = byB.get(left.recordingId);
    return left.analysisRunId && right?.analysisRunId
      ? [{ value: left.recordingId, label: left.recordingName, runAId: left.analysisRunId, runBId: right.analysisRunId }]
      : [];
  });
  const selected = options.find((option) => option.value === recordingId);

  return (
    <Card title="Benchmark Comparison">
      <Table
        rowKey="key"
        pagination={false}
        dataSource={metricRows}
        columns={[
          { title: "Metric", dataIndex: "metric" },
          { title: "A", render: (_, row) => fmt(row.a) },
          { title: "B", render: (_, row) => fmt(row.b) },
          { title: "Δ (B-A)", render: (_, row) => fmt(row.delta) },
        ]}
      />
      <Space>
        <Select aria-label="Compare Recording" value={recordingId} onChange={setRecordingId} options={options} style={{ width: 260 }} />
        <Button
          disabled={!selected}
          onClick={() => selected && onOpenCase(selected.value, selected.runAId, selected.runBId)}
        >
          Open Case Comparison
        </Button>
      </Space>
    </Card>
  );
}
```

The F1 delta may be derived from the two backend aggregate payloads only after `result.comparable === true`; all comparability decisions and the persisted benchmark values still come from the backend. Do not build a leaderboard.

In `DatasetBenchmarksView`, add:

```tsx
const [showCompare, setShowCompare] = useState(false);

{selectedIds.length === 2 ? <Button onClick={() => setShowCompare(true)}>Compare Selected</Button> : null}
{showCompare && selectedIds.length === 2 ? (
  <BenchmarkComparePanel
    evaluationAId={selectedIds[0]}
    evaluationBId={selectedIds[1]}
    onOpenCase={(recordingId, runAId, runBId) => onOpenCase(recordingId, runAId, runBId)}
  />
) : null}
```

- [ ] **Step 4: Implement exact case URL callback wiring**

In `AlgorithmLabPage`, `onOpenCase(recordingId, runAId, runBId)` must set:

```ts
patch({
  tab: "case",
  recording: recordingId,
  runA: runAId,
  runB: runBId,
});
```

One-benchmark item Inspect calls the same callback with no Run B; compare drill-down passes both.

- [ ] **Step 5: Run benchmark + page tests**

```bash
npm test -- --run src/features/dataset-benchmarks/DatasetBenchmarksView.test.tsx src/pages/AlgorithmLabPage.test.tsx src/features/algorithm-lab/CaseAnalysisView.test.tsx
```

Expected: PASS.

- [ ] **Step 6: Commit Task 7**

```bash
git add frontend/src/features/dataset-benchmarks/BenchmarkComparePanel.tsx frontend/src/features/dataset-benchmarks/BenchmarkListTable.tsx frontend/src/features/dataset-benchmarks/DatasetBenchmarksView.tsx frontend/src/features/dataset-benchmarks/DatasetBenchmarksView.test.tsx frontend/src/pages/AlgorithmLabPage.tsx frontend/src/pages/AlgorithmLabPage.test.tsx
git commit -m "feat: compare dataset benchmarks and drill down"
```

---

### Task 8: C-3 regression, build, and real browser smoke gate

**Files:**
- Modify/create research note only if C-3 evidence is appended to an existing M8.6C note or captured in a dedicated note.
- Recommended create: `docs/research/m8_6c_dataset_benchmark_ui.md`

**Interfaces:**
- Consumes: all completed C-3 frontend/backend changes and the one accepted C-2 evaluation.
- Produces: final UI acceptance evidence with no duplicate real benchmark.

- [ ] **Step 1: Run full backend regression**

From `backend/`:

```bash
pytest -q
```

Expected: PASS. Record exact count and warnings.

- [ ] **Step 2: Run focused frontend feature suite**

From `frontend/`:

```bash
npm test -- --run src/pages/AlgorithmLabPage.test.tsx src/features/algorithm-lab/CaseAnalysisView.test.tsx src/features/algorithm-lab/RunMetricsCard.test.tsx src/features/algorithm-lab/CaseComparisonTable.test.tsx src/features/dataset-benchmarks/DatasetBenchmarksView.test.tsx
```

Expected: PASS.

- [ ] **Step 3: Run full frontend suite fresh**

```bash
npm test -- --run
```

Expected: PASS. Do not silently reuse the M9.0 frontend timeout waiver; C-3 changes frontend production code and needs fresh evidence.

- [ ] **Step 4: Run production build**

```bash
npm run build
```

Expected: `tsc -b` and Vite build both PASS.

- [ ] **Step 5: Run real browser smoke using the existing C-2 DatasetEvaluation only**

Start normal backend/frontend dev processes. In the browser verify this exact story without creating another real benchmark:

```text
Algorithm Lab opens
Case Analysis still performs existing A/B comparison
Dataset Benchmarks tab lists the accepted C-2 real evaluation
Open it and confirm completed status
primary class-aware mAP50:95 matches C-2 acceptance note
raw/canonical/removed GT == 20018/19962/56
14 per-class rows are reachable
Top Confusions renders
Protocol section says physical_tf_detection_ap_v2
membership list shows human Recording names
Inspect a membership row -> Case Analysis single-run view with exact frozen Run A
choose Run B -> existing M8.5 comparison works
if a second comparable test evaluation exists from fixtures/dev data, compare and A/B drill-down works; otherwise rely on automated compare test and do not create a duplicate real benchmark solely for smoke
```

- [ ] **Step 6: Record evidence**

Create `docs/research/m8_6c_dataset_benchmark_ui.md` containing:

```text
commit SHA
backend full-suite result
frontend focused result
frontend full-suite result
frontend build result
real DatasetEvaluation id reused from C-2
observed primary metric and GT provenance
single-run drill-down result
A/B Case Analysis regression result
confirmation that no duplicate real C-2 evaluation was inserted
```

- [ ] **Step 7: Commit UI acceptance note**

```bash
git add docs/research/m8_6c_dataset_benchmark_ui.md
git commit -m "docs: record m8.6c dataset benchmark ui acceptance"
```

- [ ] **Step 8: Final C-3 gate**

C-3 is PASS only when:

```text
backend full suite green
frontend focused suite green
frontend full suite green
frontend production build green
Case Analysis A/B regression green
single-run inspection green
Imported Batch create flow tests green
truthful polling tests green
real C-2 benchmark renders correct metric/provenance values
one-benchmark drill-down green
comparable A/B drill-down automated test green
no new persistence table or top-level route
no duplicate real benchmark created for smoke
working tree clean
```

Stop and report evidence for integration review; do not merge/fast-forward until the normal verification-before-completion and branch-finishing workflows are applied.
