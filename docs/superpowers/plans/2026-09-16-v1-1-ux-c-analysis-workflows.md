# WISA V1.1 UX-C Analysis Workflows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the four approved analysis workflows (single sample → one
pipeline, sample → two-run comparison, dataset → one pipeline, dataset →
evaluation entry) onto the UX-A shell and the projection-authoritative UX-B
contracts, make executor availability explainable, and bring Local CPU to a
genuinely qualified state on the Windows deployment.

**Architecture:** Two independent streams first (C1 Local CPU qualification,
C2 executor candidate-state UX), then dependent wiring (C3–C7) after UX-A and
UX-B are accepted and merged into the UX-C branch by ordinary merge ancestry
(never by rebasing accepted SHAs). Dataset-contextual flows use the opaque
`dataset_projection_id` end to end — executor selection, experiment creation, and
evaluation scoping all consume the same UX-B projection authority.

**Tech Stack:** React + TypeScript + Vite + Ant Design + Vitest; FastAPI +
pytest; the existing `wisa` CLI operator workflow.

**Spec:**
`docs/superpowers/specs/2026-09-16-v1-1-ux-productization-design.md`

## Global Constraints

```text
Execution environment defaults to Auto.
If no executor is runnable, show candidate-level safe explanations, not only
    the generic "No executor is currently runnable" sentence.
The frontend MUST NOT force-enable a disabled Run button.
Never expose secrets, certificate contents, private interpreter paths, SSH
    details, or raw provider telemetry.
Runnable predicate is authoritative in the backend:
    technical AND configured AND certified AND available.
Dataset-contextual flows MUST carry dataset_projection_id end to end; the
    legacy dataset_name/split/label_space triple MUST NOT be used by V1.1
    contextual workflows.
Local CPU acceptance requires the real operator qualification workflow
    (runtime doctor -> qualify -> certificate install -> registry rebuild ->
    four-predicate verification -> one CPU-only smoke), not just configuration.
Algorithm Lab comparison requires: exactly two selected runs, both completed,
    both for the current recording, AND the recording has Ground Truth.
Business state stays in the URL. No GPU. No Remote-GPU. No sealed-baseline
    changes. No new artifact format.
```

---

## File Map

Create:

```text
backend/tests/test_local_cpu_diagnosis.py
frontend/src/features/execution-environment/NoRunnableExecutorPanel.tsx
frontend/src/features/execution-environment/NoRunnableExecutorPanel.test.tsx
frontend/src/features/algorithm-lab/CompareShortcut.tsx
frontend/src/features/algorithm-lab/CompareShortcut.test.tsx
frontend/src/features/data-library/DatasetAnalysisCompareEntry.tsx
frontend/src/pages/WorkflowNavigation.test.tsx
```

Modify:

```text
frontend/src/pages/SpectrumAnalysisPage.tsx        (no-runnable panel)
frontend/src/features/dataset-experiment/ExperimentCreateForm.tsx  (projection identity + scope)
frontend/src/features/dataset-experiment/types.ts   (projection id pass-through)
frontend/src/pages/ExperimentsPage.tsx              (datasetProjectionId prefill + compare URL)
frontend/src/features/evaluation/ExperimentComparePanel.tsx (URL preselection)
frontend/src/pages/StandaloneSampleDetailPage.tsx   (UX-B file; additive compare shortcut, GT-gated)
frontend/src/features/data-library/DatasetAnalysisHistory.tsx (UX-B file; additive compare entry)
frontend/src/localization/messages.en-US.ts
frontend/src/localization/messages.zh-CN.ts
```

Backend: only `backend/tests/test_local_cpu_diagnosis.py` is created, and only
if the diagnosis proves a defect may
`backend/app/execution_selection/resolver.py` or
`backend/app/analysis/local_executor.py` change. No Remote-GPU code.

---

## Interfaces

Consumes (from UX-B; see the UX-B plan for exact shapes):

```ts
getDatasetProjection(datasetProjectionId): Promise<DatasetProjectionSummary>
listDatasetAnalysisHistory(datasetProjectionId): Promise<DatasetAnalysisHistoryPage>
listAnalysisRuns(recordingId): Promise<AnalysisRun[]>            // existing
compareAnalysisRuns({ recordingId, runAId, runBId }): Promise<AlgorithmLabCompareResponse> // existing
// UX-B extends the client scope union and request/read types with:
ExecutionSelectionScope = { kind: "dataset_projection"; datasetProjectionId: string } | ...
DatasetExperimentCreateRequest.datasetProjectionId?: string | null
DatasetExperiment.datasetProjectionId: string | null
```

Produces:

```ts
// execution-environment/NoRunnableExecutorPanel.tsx
export interface NoRunnableExecutorPanelProps {
  selection: ExecutorSelection | null;
}
export function NoRunnableExecutorPanel(props: NoRunnableExecutorPanelProps): JSX.Element | null;

// algorithm-lab/CompareShortcut.tsx
export interface CompareShortcutProps {
  recordingId: string;
  hasGroundTruth: boolean;
  runs: AnalysisRun[];
  onCompare: (runAId: string, runBId: string) => void;
}
export function CompareShortcut(props: CompareShortcutProps): JSX.Element;

// dataset-experiment/ExperimentCreateForm.tsx (extended)
export interface DatasetIdentityInput {
  datasetProjectionId: string;
  datasetName: string;
  datasetSplit: string;
  datasetLabelSpace: string;
}
export interface ExperimentCreateFormProps {
  onCreated?: (id: string) => void;
  initialDataset?: DatasetIdentityInput;
}
```

---

## C1 (independent): Local CPU qualification

**Files:**
- Create: `backend/tests/test_local_cpu_diagnosis.py`
- Read: `backend/app/analysis/local_executor.py`,
  `backend/app/execution_selection/resolver.py`,
  `backend/app/remote_execution/runtime.py`, `backend/app/core/config.py`,
  `backend/app/cli.py`, `backend/app/runtime_qualification/*`
- Modify (only if the diagnosis proves a defect):
  `backend/app/execution_selection/resolver.py` or
  `backend/app/analysis/local_executor.py`

**Interfaces:**
- Consumes: `GET /api/executor-selection`; `python -m app.cli` operator workflow.
- Produces: a legitimately certified, available Windows `local_cpu` executor and
  one CPU-only AnalysisRun smoke, or an explicit STOP blocker.

- [ ] Record the current candidate matrix for a `local_cpu`-capable,
      release-less pipeline (`stft_energy_detector`) on a compatible Recording:

```text
GET /api/executor-selection?recording_id=<rec>&pipeline_id=stft_energy_detector
```

  Record `technical / configured / certified / available` and `reason_code` for
  the `local_cpu` candidate.
- [ ] Run the operator diagnostic: `python -m app.cli runtime doctor`.
- [ ] Never reuse the repository's AutoDL-bound certificate
      `local:autodl_primary:cpu:a1237f8faae7`. It does not represent Windows.
- [ ] Configure a **separate Windows CPU inference interpreter** (never the
      control-plane interpreter merely for convenience) via
      `WSP_LOCAL_CPU_PYTHON_PATH`, configure `WSP_LOCAL_CPU_RUNTIME_REF` and the
      Windows-owned `WSP_RUNTIME_FAMILY`, and confirm
      `build_local_providers(settings)` registers `local_cpu`.
- [ ] Run qualification against the real interpreter/runtime identity:

```text
python -m app.cli qualify --plugin stft_energy_detector --executor local_cpu
```

- [ ] Install the operator certificate produced by qualification:

```text
python -m app.cli certificate install --from <evidence_dir>
python -m app.cli certificate list
```

- [ ] Restart / rebuild the control-plane registry as required (the CLI prints
      that a certificate becomes effective on the next registry rebuild).
- [ ] Re-query `/api/executor-selection` and require, for the compatible
      Recording and `stft_energy_detector`:

```text
technical = true
configured = true
certified  = true
available  = true
resolved_executor = local_cpu where the policy selects it
```

- [ ] Run ONE small real CPU-only `stft_energy_detector` AnalysisRun through
      `POST /api/analysis-runs` (manual `local_cpu` or Auto) and verify it reaches
      `completed` with persisted `DetectionResult` rows.
- [ ] Add and keep the fail-closed unit tests (these are not the acceptance
      proof, only regression guards):

```python
from app.analysis.local_executor import build_local_providers

def test_local_cpu_absent_when_interpreter_and_ref_unset(settings):
    assert "local_cpu" not in build_local_providers(settings)

def test_local_cpu_registered_when_interpreter_and_ref_configured(settings, tmp_path):
    settings.local_cpu_python_path = tmp_path / "python.exe"
    settings.local_cpu_runtime_ref = "local:test:cpu:abc123"
    assert "local_cpu" in build_local_providers(settings)
```

- [ ] If any authority/probe step cannot complete legitimately, STOP and report
      the exact failed step. Do not fabricate a runtime identity or certificate.
- [ ] No GPU; no Remote-GPU; no machine-specific interpreter path or operator
      certificate is committed to Git.
- [ ] Run `pytest backend/tests/test_local_cpu_diagnosis.py -v`.
- [ ] Commit: `fix(ux-c): qualify Windows local_cpu execution`

---

## C2 (independent): Executor candidate-state UX

**Files:**
- Create: `frontend/src/features/execution-environment/NoRunnableExecutorPanel.tsx`,
  `NoRunnableExecutorPanel.test.tsx`
- Modify: `frontend/src/pages/SpectrumAnalysisPage.tsx`,
  `frontend/src/localization/messages.en-US.ts`,
  `frontend/src/localization/messages.zh-CN.ts`

**Interfaces:**
- Consumes: `optionsFromSelection(selection)`.
- Produces: `NoRunnableExecutorPanel` and keys `executionEnv.noRunnableTitle`,
  `executionEnv.noRunnableHint`.

- [ ] Add message keys:

```ts
"executionEnv.noRunnableTitle": "No runnable execution environment",
  // zh: "没有可用的执行环境"
"executionEnv.noRunnableHint": "This pipeline has no runnable execution environment. See each environment below.",
  // zh: "该算法流水线当前没有可用的执行环境。请查看下方各执行环境状态。"
```

- [ ] Write the failing test:

```tsx
const selection = {
  requestedMode: "auto", resolvedExecutor: null, reasonCode: "AUTO_NO_RUNNABLE_EXECUTOR",
  reason: "No runnable executor.", workloadClass: "small",
  candidates: [
    { executor: "local_cpu", technical: true, configured: false, certified: false, available: false, reasonCode: null, reasonMessage: null },
    { executor: "local_gpu", technical: true, configured: false, certified: false, available: false, reasonCode: null, reasonMessage: null },
    { executor: "remote_gpu", technical: true, configured: false, certified: false, available: false, reasonCode: null, reasonMessage: null },
  ],
};

test("lists candidate states instead of a generic sentence", () => {
  render(renderWithLocalization(<NoRunnableExecutorPanel selection={selection} />));
  expect(screen.getByTestId("no-runnable-executor-panel")).toBeInTheDocument();
  expect(screen.getByText(/Local CPU/)).toBeInTheDocument();
  expect(screen.getByText(/Local GPU/)).toBeInTheDocument();
  expect(screen.getByText(/Remote GPU/)).toBeInTheDocument();
});

test("returns null when an executor is runnable", () => {
  const runnable = { ...selection, resolvedExecutor: "local_cpu",
    candidates: [{ ...selection.candidates[0], configured: true, certified: true, available: true }] };
  const { container } = render(renderWithLocalization(<NoRunnableExecutorPanel selection={runnable} />));
  expect(container).toBeEmptyDOMElement();
});
```

- [ ] Implement the panel using `optionsFromSelection` and existing
      `executionEnv.*` labels; never render paths, secrets, certificate
      contents, SSH details, or telemetry.
- [ ] Render `NoRunnableExecutorPanel` in `SpectrumAnalysisPage.tsx` when a
      selection exists but no option is enabled; do NOT enable Run.
- [ ] Run `npx vitest run src/features/execution-environment`; observe pass.
- [ ] Commit: `feat(ux-c): explain non-runnable executor candidates`

---

## C3 (dependent): Data Library sample → one pipeline

**Files:**
- Modify: `frontend/src/features/data-library/StandaloneSampleList.tsx`,
  `frontend/src/pages/StandaloneSampleDetailPage.tsx` (UX-B files; additive
  "Analyze" action)
- Test: `frontend/src/pages/WorkflowNavigation.test.tsx`

**Interfaces:**
- Consumes: UX-B standalone list, existing `SpectrumAnalysisPage` Auto default.
- Produces: an "Analyze" primary action routing to `/spectrum/:recordingId`.

- [ ] Apply after UX-A and UX-B are merged into the UX-C branch.
- [ ] Ensure "Analyze" is the primary standalone-sample action routing to
      `/spectrum/<recordingId>`.
- [ ] Write a failing workflow test asserting the workspace opens with Auto and
      the Run action available (mock fetch for recording/spectrogram/pipelines/
      executor-selection).
- [ ] Run focused test; observe pass.
- [ ] Commit: `feat(ux-c): analyze from a Data Library sample`

---

## C4 (dependent): Sample Analysis History → two-run comparison

**Files:**
- Create: `frontend/src/features/algorithm-lab/CompareShortcut.tsx`,
  `CompareShortcut.test.tsx`
- Modify: `frontend/src/pages/StandaloneSampleDetailPage.tsx` (one additive
  render of `CompareShortcut`, passing `hasGroundTruth`)
- Test: `frontend/src/features/algorithm-lab/CompareShortcut.test.tsx`

**Interfaces:**
- Consumes: `listAnalysisRuns(recordingId)`, `getRecording(recordingId)` for
  `hasGroundTruth`.
- Produces: `CompareShortcut`; routes to
  `/algorithm-lab?recording=<id>&runA=<a>&runB=<b>`.

- [ ] Define compatibility precisely:

```text
Compare is enabled only when:
  exactly two runs are selected
  AND both status === "completed"
  AND both belong to the current recording
  AND the recording has Ground Truth (hasGroundTruth === true)
Otherwise Compare is disabled with a concise localized reason.
No manual run-id entry is permitted.
```

- [ ] Add the message key:

```ts
"algorithmLab.compareRequiresGroundTruth": "Comparison requires Ground Truth for this recording.",
  // zh: "该信号记录需要真值标注（GT）才能进行对比。"
```

- [ ] Write the failing tests:

```tsx
test("routes to Algorithm Lab with recording and both completed runs", async () => {
  const onCompare = vi.fn();
  render(renderWithLocalization(<CompareShortcut recordingId="rec_1" hasGroundTruth
    runs={[run({ id: "a", status: "completed" }), run({ id: "b", status: "completed" })]}
    onCompare={onCompare} />));
  await user.click(screen.getByRole("checkbox", { name: "a" }));
  await user.click(screen.getByRole("checkbox", { name: "b" }));
  await user.click(screen.getByRole("button", { name: "Compare" }));
  expect(onCompare).toHaveBeenCalledWith("a", "b");
});

test("no Ground Truth disables Compare with a reason", () => {
  render(renderWithLocalization(<CompareShortcut recordingId="rec_1" hasGroundTruth={false}
    runs={[run({ id: "a", status: "completed" }), run({ id: "b", status: "completed" })]}
    onCompare={vi.fn()} />));
  expect(screen.getByRole("button", { name: "Compare" })).toBeDisabled();
  expect(screen.getByTestId("compare-gt-required")).toHaveTextContent(/Ground Truth/);
});

test("running runs cannot be selected", () => {
  render(renderWithLocalization(<CompareShortcut recordingId="rec_1" hasGroundTruth
    runs={[run({ id: "a", status: "completed" }), run({ id: "c", status: "running" })]}
    onCompare={vi.fn()} />));
  expect(screen.getByRole("checkbox", { name: "c" })).toBeDisabled();
});
```

- [ ] Implement `CompareShortcut`; in `StandaloneSampleDetailPage`, load the
      recording via `getRecording` and pass `hasGroundTruth`.
- [ ] Never navigate into a comparison guaranteed to return
      `INVALID_COMPARISON` (422).
- [ ] Run focused tests; observe pass.
- [ ] Commit: `feat(ux-c): two-run comparison shortcut with Ground Truth gate`

---

## C5 (dependent): Dataset → one pipeline with projection prefill

**Files:**
- Modify: `frontend/src/features/dataset-experiment/ExperimentCreateForm.tsx`,
  `frontend/src/features/dataset-experiment/types.ts`,
  `frontend/src/pages/ExperimentsPage.tsx`,
  `frontend/src/localization/messages.en-US.ts`,
  `frontend/src/localization/messages.zh-CN.ts`
- Test: `frontend/src/features/dataset-experiment/ExperimentCreateForm.test.tsx`,
  `frontend/src/pages/WorkflowNavigation.test.tsx`

**Interfaces:**
- Consumes: `getDatasetProjection(datasetProjectionId)` (UX-B);
  the UX-B client `dataset_projection` executor-selection scope.
- Produces: `ExperimentCreateFormProps.initialDataset` incl.
  `datasetProjectionId`; requests carrying `datasetProjectionId`.

- [ ] Add the message key:

```ts
"experiment.datasetIdentityLocked": "Dataset identity from the selected dataset",
  // zh: "数据集标识来自所选数据集"
```

- [ ] Extend `DatasetIdentityInput` with `datasetProjectionId`; when
      `initialDataset` is present, render dataset identity read-only and use the
      projection for both the executor selection and the create request.
- [ ] `toCreateRequest` includes `datasetProjectionId` when present. When
      `initialDataset.datasetProjectionId` is present, the executor selection
      call uses:

```ts
getExecutorSelection({ scope: { kind: "dataset_projection", datasetProjectionId }, pipelineId })
```

- [ ] Write the failing form test:

```tsx
test("prefills projection identity and sends dataset_projection_id", async () => {
  mockFetch.executorSelection(datasetProjectionSelection("dsproj_1", { resolvedExecutor: "local_cpu" }));
  render(renderWithLocalization(<ExperimentCreateForm
    initialDataset={{ datasetProjectionId: "dsproj_1", datasetName: "SpaceNet",
                      datasetSplit: "test", datasetLabelSpace: "spacenet_14" }}
    onCreated={vi.fn()} />));
  expect(screen.getByDisplayValue("SpaceNet")).toBeInTheDocument();
  expect(screen.getByDisplayValue("test")).toBeInTheDocument();
  expect(screen.getByDisplayValue("spacenet_14")).toBeInTheDocument();
  expect(mockFetch.lastExecutorSelectionQuery()).toContain("dataset_projection_id=dsproj_1");
});
```

- [ ] In `ExperimentsPage`, read `searchParams.get("datasetProjectionId")`; when
      present, call `getDatasetProjection(id)`, open the create modal, and pass
      `initialDataset` from the projection.
- [ ] Write the failing page test:

```tsx
test("dataset page prefill opens creation with projection identity", async () => {
  mockFetch.datasetProjection({ datasetProjectionId: "dsproj_1", datasetName: "SpaceNet",
    datasetSplit: "test", labelSpace: "spacenet_14", ... });
  render(<App />, { route: "/experiments?datasetProjectionId=dsproj_1" });
  expect(await screen.findByDisplayValue("SpaceNet")).toBeInTheDocument();
});
```

- [ ] Run focused tests; observe pass.
- [ ] Commit: `feat(ux-c): projection-scoped experiment creation`

---

## C6 (dependent): Dataset → evaluation / comparison entry

**Files:**
- Create: `frontend/src/features/data-library/DatasetAnalysisCompareEntry.tsx`
- Modify: `frontend/src/features/evaluation/ExperimentComparePanel.tsx`
  (URL preselection), `frontend/src/pages/ExperimentsPage.tsx`
- Test: `frontend/src/features/evaluation/ExperimentComparePanel.test.tsx`,
  `frontend/src/pages/WorkflowNavigation.test.tsx`

**Interfaces:**
- Consumes: `listDatasetAnalysisHistory` (UX-B; items include
  `kind: "imported_batch"`), existing `compareDatasetBenchmarks`,
  `EvaluationMetricsView`.
- Produces: navigation
  `/experiments?tab=compare&a=<evaluationAId>&b=<evaluationBId>` and
  `/experiments?tab=benchmarks&benchmark=<evaluationId>`.

- [ ] Write the failing tests:

```tsx
test("compare preselection reads a and b from the URL", async () => {
  mockFetch.experiments([experiment({ datasetEvaluationId: "eval_a" }), experiment({ datasetEvaluationId: "eval_b" })]);
  mockFetch.compare(evalResult("eval_a", "eval_b"));
  render(<App />, { route: "/experiments?tab=compare&a=eval_a&b=eval_b" });
  expect(await screen.findByTestId("compare-delta-table")).toBeInTheDocument();
});

test("single evaluation entry opens the evaluation surface", async () => {
  render(<App />, { route: "/experiments?tab=benchmarks&benchmark=eval_a" });
  expect(await screen.findByTestId("benchmark-detail-view")).toBeInTheDocument();
});
```

- [ ] In `ExperimentComparePanel`, read `a`/`b` from `useSearchParams` and
      preselect and compare once when both are valid completed evaluations.
- [ ] In `DatasetAnalysisCompareEntry`, allow selecting exactly two
      evaluation-kind history items and navigate to the compare URL; reuse
      `compareDatasetBenchmarks` (no new engine).
- [ ] Run focused tests; observe pass.
- [ ] Commit: `feat(ux-c): dataset evaluation and compare entry`

---

## C7: Track boundary

- [ ] Focused backend (only if C1 changed backend code):
      `pytest backend/tests/test_local_cpu_diagnosis.py backend/tests/test_execution_selection_resolver.py backend/tests/test_executor_selection_api.py -v`
- [ ] Focused frontend:
      `npx vitest run src/features/execution-environment`
      `npx vitest run src/features/algorithm-lab`
      `npx vitest run src/features/dataset-experiment`
      `npx vitest run src/pages`
- [ ] Full frontend: `npm test -- --run`
- [ ] Production build: `npm run build`
- [ ] Commit: `chore(ux-c): track boundary verification`

---

## Local CPU Acceptance Checklist

Completed during C1; all items must be true, otherwise STOP and report the
exact failed step:

```text
[ ] Current candidate matrix recorded before changes.
[ ] runtime doctor executed; observed provider/identity state recorded.
[ ] Separate Windows CPU inference interpreter configured (not the control plane).
[ ] Windows-owned runtime family configured.
[ ] Legitimate Windows runtime_ref derived for the real interpreter identity.
[ ] AutoDL runtime_ref local:autodl_primary:cpu:a1237f8faae7 not reused.
[ ] qualification completed for stft_energy_detector @ local_cpu.
[ ] operator certificate installed and listed.
[ ] control-plane registry rebuilt/restarted.
[ ] technical = true for local_cpu.
[ ] configured = true for local_cpu.
[ ] certified = true for local_cpu.
[ ] available = true for local_cpu.
[ ] resolved_executor = local_cpu where policy selects it.
[ ] one CPU-only STFT Energy AnalysisRun reached completed with DetectionResults.
[ ] no GPU used.
[ ] no Remote-GPU path introduced.
[ ] no machine-specific interpreter path or certificate committed to Git.
```

---

## Self-Review Checklist

```text
[ ] Auto remains the default execution environment.
[ ] No frontend force-enable of the Run button.
[ ] No secrets/paths/certificate/SSH/telemetry exposed by the candidate panel.
[ ] Dataset-contextual flows carry dataset_projection_id end to end.
[ ] Dataset identity is prefilled from the UX-B projection; no retyping.
[ ] Comparison is disabled when the recording has no Ground Truth.
[ ] Local CPU acceptance includes real qualification + a CPU-only smoke.
[ ] UX-C consumes UX-B contracts; no duplicate dataset representation.
[ ] Accepted SHAs are integrated by merge ancestry, never rebase.
[ ] No GPU; no Remote-GPU; no sealed-baseline changes; no new artifact format.
```
