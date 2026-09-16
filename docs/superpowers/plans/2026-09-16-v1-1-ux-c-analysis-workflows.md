# WISA V1.1 UX-C Analysis Workflows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the four approved analysis workflows (single sample → one
pipeline, sample → two-run comparison, dataset → one pipeline, dataset →
evaluation entry) onto the UX-A shell and UX-B Data Library contracts, and make
executor availability explainable — with the Local CPU question answered by
evidence, not by a frontend workaround.

**Architecture:** Two independent streams first (C1 Local CPU diagnosis, C2
executor candidate-state UX), then dependent wiring (C3–C7) after UX-A and UX-B
are accepted. UX-C consumes UX-B dataset projection / analysis-history client
functions and never creates a second dataset representation. The existing
`SpectrumAnalysisPage`, `AlgorithmLabPage`, `ExperimentsPage`, and
`ExecutionEnvironmentSelector` are reused.

**Tech Stack:** React + TypeScript + Vite + Ant Design + Vitest; FastAPI +
pytest for the diagnosis tests.

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
The Local CPU question is evidence-driven; do not assume a backend defect.
Never reintroduce Remote-GPU as the solution.
Comparison requires exactly two compatible completed runs for the same recording.
Dataset experiment creation initiated from a dataset page prefills dataset
    identity (name, split, label space); the user does not retype it.
Business state stays in the URL. No GPU. No sealed-baseline changes.
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
frontend/src/features/execution-environment/ExecutionEnvironmentSelector.tsx   (reuse states)
frontend/src/pages/SpectrumAnalysisPage.tsx        (no-runnable panel)
frontend/src/features/dataset-experiment/ExperimentCreateForm.tsx  (initialDataset prop)
frontend/src/features/dataset-experiment/types.ts   (no shape change unless required)
frontend/src/pages/ExperimentsPage.tsx              (datasetProjectionId prefill + compare URL)
frontend/src/features/evaluation/ExperimentComparePanel.tsx (URL preselection)
frontend/src/pages/StandaloneSampleDetailPage.tsx   (UX-B file; one additive compare shortcut)
frontend/src/features/data-library/DatasetAnalysisHistory.tsx (UX-B file; additive compare entry)
frontend/src/pages/AlgorithmLabPage.tsx             (no analytical change; already UX-A owner)
frontend/src/localization/messages.en-US.ts
frontend/src/localization/messages.zh-CN.ts
```

---

## Interfaces

Consumes (from UX-A):

```ts
useTheme, useSidebarCollapsed           // not required by workflow wiring
readAlgorithmLabRoute                    // not required; Algorithm Lab already URL-driven
```

Consumes (from UX-B):

```ts
listDatasetProjections, getDatasetProjection, listDatasetSamples,
listDatasetAnalysisHistory, listStandaloneSamples
DatasetProjectionSummary, DatasetAnalysisHistoryItem
listAnalysisRuns(recordingId)            // existing client
compareAnalysisRuns({ recordingId, runAId, runBId })  // existing client
```

Produces:

```ts
// execution-environment/NoRunnableExecutorPanel.tsx
export interface NoRunnableExecutorPanelProps {
  selection: ExecutorSelection | null;
  contextKey?: MessageKey;   // e.g. "executionEnv.noRunnableForPipeline"
}
export function NoRunnableExecutorPanel(props: NoRunnableExecutorPanelProps): JSX.Element | null;

// algorithm-lab/CompareShortcut.tsx
export interface CompareShortcutProps {
  recordingId: string;
  runs: AnalysisRun[];
  onCompare: (runAId: string, runBId: string) => void;
}
export function CompareShortcut(props: CompareShortcutProps): JSX.Element;

// dataset-experiment/ExperimentCreateForm.tsx (extended props)
export interface DatasetIdentityInput {
  datasetName: string;
  datasetSplit: string;
  datasetLabelSpace: string;
}
export interface ExperimentCreateFormProps {
  onCreated?: (id: string) => void;
  initialDataset?: DatasetIdentityInput;
}
```

Backend diagnosis produces:

```text
backend/tests/test_local_cpu_diagnosis.py  -> durable outcome record for spec follow-up B
```

---

## C1 (independent): Local CPU non-runnable diagnosis

**Files:**
- Create: `backend/tests/test_local_cpu_diagnosis.py`
- Read: `backend/app/analysis/local_executor.py`,
  `backend/app/execution_selection/resolver.py`,
  `backend/app/remote_execution/runtime.py`, `backend/app/core/config.py`
- Modify (only if the diagnosis proves a defect): `backend/app/analysis/local_executor.py`
  or `backend/app/execution_selection/resolver.py`

**Interfaces:**
- Consumes: `build_local_providers(settings)`, `collect_candidates(...)`,
  `GET /api/executor-selection`.
- Produces: a recorded diagnosis outcome and, if required, a TDD backend fix.

- [ ] Run the control plane with the **current Windows deployment environment**
      (no GPU) and query the candidate matrix for a pipeline that declares
      `local_cpu` in `technical_execution_capabilities`:

```text
GET /api/executor-selection?recording_id=<rec>&pipeline_id=<pipeline>
```

- [ ] Record, for the `local_cpu` candidate, the exact booleans
      `technical / configured / certified / available` and the bounded
      `reason_code`. The first `false` predicate is the failure point.
- [ ] Write the diagnosis test that pins the configuration predicate:

```python
from app.analysis.local_executor import build_local_providers

def test_local_cpu_absent_when_interpreter_and_ref_unset(settings):
    # Default Windows control-plane Settings have no local interpreter configured.
    assert "local_cpu" not in build_local_providers(settings)

def test_local_cpu_registered_when_interpreter_and_ref_configured(settings, tmp_path):
    settings.local_cpu_python_path = tmp_path / "python.exe"
    settings.local_cpu_runtime_ref = "local:test:cpu:abc123"
    assert "local_cpu" in build_local_providers(settings)
```

- [ ] Run `pytest backend/tests/test_local_cpu_diagnosis.py -v`.
- [ ] Apply the outcome branch exactly as determined by evidence:

```text
Outcome A — configuration/setup issue (expected when configured == false):
    Document the required environment variables in the deployment notes:
        WSP_LOCAL_CPU_PYTHON_PATH
        WSP_LOCAL_CPU_RUNTIME_REF
        WSP_RUNTIME_FAMILY            (when the operator authority is enabled)
    Do not change application logic. Keep the two diagnosis tests as the
    durable outcome record. Commit: "fix(ux-c): document local_cpu runtime config".

Outcome B — backend logic defect (only if configured/certified are true but a
    wrong predicate excludes local_cpu):
    Add a failing test that reproduces the wrong exclusion in
    backend/tests/test_execution_selection_resolver.py, then fix
    collect_candidates / resolve_auto_execution in TDD order.
    Commit: "fix(ux-c): correct local_cpu runnable predicate".

Outcome C — pipeline genuinely CPU-incompatible (technical == false):
    Add a test asserting the candidate stays technical=false, and rely on the
    C2 UX to explain the state. Commit: "test(ux-c): pin local_cpu inapplicable".
```

- [ ] Confirm no Remote-GPU path is introduced in any branch.

---

## C2 (independent): Executor candidate-state UX

**Files:**
- Create: `frontend/src/features/execution-environment/NoRunnableExecutorPanel.tsx`,
  `NoRunnableExecutorPanel.test.tsx`
- Modify: `frontend/src/pages/SpectrumAnalysisPage.tsx`,
  `frontend/src/localization/messages.en-US.ts`,
  `frontend/src/localization/messages.zh-CN.ts`

**Interfaces:**
- Consumes: `optionsFromSelection(selection)` from
  `features/execution-environment/executionEnvironment.ts`.
- Produces: `NoRunnableExecutorPanel` and two message keys
  `executionEnv.noRunnableTitle`, `executionEnv.noRunnableHint`.

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

- [ ] Implement the panel using `optionsFromSelection` and the existing
      `executionEnv.*` state labels; show each option's label, state, and
      `reasonMessage`. Never render paths, secrets, certificate contents, SSH
      details, or telemetry.
- [ ] In `SpectrumAnalysisPage.tsx`, render `NoRunnableExecutorPanel` when
      `boundSelection` exists and no option is enabled. Do **not** enable the
      Run button.
- [ ] Run `npx vitest run src/features/execution-environment`; observe pass.
- [ ] Commit: `feat(ux-c): explain non-runnable executor candidates`

---

## C3 (dependent): Data Library sample → one pipeline

**Files:**
- Modify: `frontend/src/features/data-library/StandaloneSampleList.tsx` and
  `frontend/src/pages/StandaloneSampleDetailPage.tsx` (UX-B files; additive
  "Analyze" action)
- Test: `frontend/src/pages/WorkflowNavigation.test.tsx`

**Interfaces:**
- Consumes: `listStandaloneSamples`, `getRecording`, existing
  `SpectrumAnalysisPage` Auto default.
- Produces: an "Analyze" action routing to `/spectrum/:recordingId`.

- [ ] Apply after UX-B is accepted.
- [ ] Add the "Analyze" action to the standalone sample row and detail page,
      routing to `/spectrum/<recordingId>`. (B's "Open Analysis Workspace"
      already routes there; "Analyze" is the task-model label and must be the
      primary action.)
- [ ] Write a failing workflow test:

```tsx
test("Analyze from a standalone sample opens the spectrum workspace at Auto", async () => {
  mockFetch.standaloneSamples([{ id: "rec_1", name: "sample-a", ... }]);
  mockFetch.executorSelection({ requestedMode: "auto", resolvedExecutor: "local_cpu",
    reasonCode: "AUTO_LOCAL_CPU_PREFERRED", reason: "Local CPU preferred.", workloadClass: "small",
    candidates: [candidate("local_cpu", true)] });
  render(<App />, { route: "/data-library" });
  await user.click(await screen.findByRole("button", { name: "Analyze" }));
  expect(await screen.findByText("Spectrum Analysis")).toBeInTheDocument();
  expect(screen.getByText("Auto")).toBeInTheDocument();
});
```

- [ ] Run focused test; observe pass.
- [ ] Commit: `feat(ux-c): analyze from a Data Library sample`

---

## C4 (dependent): Sample Analysis History → two-run comparison

**Files:**
- Create: `frontend/src/features/algorithm-lab/CompareShortcut.tsx`,
  `CompareShortcut.test.tsx`
- Modify: `frontend/src/pages/StandaloneSampleDetailPage.tsx` (one additive
  import/render of `CompareShortcut`)
- Test: `frontend/src/features/algorithm-lab/CompareShortcut.test.tsx`

**Interfaces:**
- Consumes: `listAnalysisRuns(recordingId)`.
- Produces: `CompareShortcut`, routing to
  `/algorithm-lab?recording=<id>&runA=<a>&runB=<b>`.

- [ ] Define compatibility precisely (from existing behavior):

```text
A run is selectable for comparison when:
  run.status === "completed"
  AND run.recordingId === the current recording
Exactly two selectable runs must be chosen; the Compare action is disabled
otherwise. No manual run-id entry is permitted.
```

- [ ] Write the failing test:

```tsx
test("routes to Algorithm Lab with recording and both completed runs", async () => {
  const onCompare = vi.fn();
  render(renderWithLocalization(<CompareShortcut recordingId="rec_1" runs={[
    run({ id: "a", status: "completed" }),
    run({ id: "b", status: "completed" }),
    run({ id: "c", status: "running" }),
  ]} onCompare={onCompare} />));
  await user.click(screen.getByRole("checkbox", { name: "a" }));
  await user.click(screen.getByRole("checkbox", { name: "b" }));
  await user.click(screen.getByRole("button", { name: "Compare" }));
  expect(onCompare).toHaveBeenCalledWith("a", "b");
});

test("running runs cannot be selected and Compare stays disabled", () => {
  render(renderWithLocalization(<CompareShortcut recordingId="rec_1" runs={[
    run({ id: "a", status: "completed" }),
    run({ id: "c", status: "running" }),
  ]} onCompare={vi.fn()} />));
  expect(screen.getByRole("checkbox", { name: "c" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Compare" })).toBeDisabled();
});
```

- [ ] Implement `CompareShortcut` and have `StandaloneSampleDetailPage` pass
      `onCompare={(a, b) => navigate(\`/algorithm-lab?recording=${recordingId}&runA=${a}&runB=${b}\`)}`.
- [ ] Add an integration assertion that the Algorithm Lab workspace hydrates
      from those query params (reuse the existing Algorithm Lab behavior).
- [ ] Run `npx vitest run src/features/algorithm-lab src/pages`; observe pass.
- [ ] Commit: `feat(ux-c): two-run comparison shortcut`

---

## C5 (dependent): Dataset → one pipeline with contextual prefill

**Files:**
- Modify: `frontend/src/features/dataset-experiment/ExperimentCreateForm.tsx`,
  `frontend/src/pages/ExperimentsPage.tsx`,
  `frontend/src/localization/messages.en-US.ts`,
  `frontend/src/localization/messages.zh-CN.ts`
- Test: `frontend/src/features/dataset-experiment/ExperimentCreateForm.test.tsx`,
  `frontend/src/pages/WorkflowNavigation.test.tsx`

**Interfaces:**
- Consumes: `getDatasetProjection(datasetProjectionId)` (UX-B),
  `DatasetIdentityInput`.
- Produces: `ExperimentCreateFormProps.initialDataset`; `ExperimentsPage`
  support for `?datasetProjectionId=<id>`.

- [ ] Add message keys:

```ts
"experiment.datasetIdentityLocked": "Dataset identity from the selected dataset",
  // zh: "数据集标识来自所选数据集"
```

- [ ] Write the failing form test:

```tsx
test("prefills dataset identity and never requires retyping", async () => {
  render(renderWithLocalization(<ExperimentCreateForm
    initialDataset={{ datasetName: "SpaceNet", datasetSplit: "test", datasetLabelSpace: "spacenet_14" }}
    onCreated={vi.fn()} />));
  expect(screen.getByDisplayValue("SpaceNet")).toBeInTheDocument();
  expect(screen.getByDisplayValue("test")).toBeInTheDocument();
  expect(screen.getByDisplayValue("spacenet_14")).toBeInTheDocument();
});
```

- [ ] Extend `ExperimentCreateForm` props to `ExperimentCreateFormProps` and
      when `initialDataset` is present render the identity fields as read-only
      and seed their state from it.
- [ ] In `ExperimentsPage`, read `searchParams.get("datasetProjectionId")`;
      when present, call `getDatasetProjection(id)`, open the create modal, and
      pass `initialDataset` derived from the projection
      (`datasetName`, `datasetSplit`, `labelSpace ?? ""`).
- [ ] Write the failing page test:

```tsx
test("dataset page prefill opens experiment creation with identity filled", async () => {
  mockFetch.datasetProjection({ datasetProjectionId: "dsproj_1", datasetName: "SpaceNet",
    datasetSplit: "test", labelSpace: "spacenet_14", ... });
  render(<App />, { route: "/experiments?datasetProjectionId=dsproj_1" });
  expect(await screen.findByDisplayValue("SpaceNet")).toBeInTheDocument();
});
```

- [ ] Run focused tests; observe pass.
- [ ] Commit: `feat(ux-c): dataset-contextual experiment creation`

---

## C6 (dependent): Dataset → evaluation / comparison entry

**Files:**
- Create: `frontend/src/features/data-library/DatasetAnalysisCompareEntry.tsx`
- Modify: `frontend/src/features/evaluation/ExperimentComparePanel.tsx`
  (URL preselection), `frontend/src/pages/ExperimentsPage.tsx`
- Test: `frontend/src/features/evaluation/ExperimentComparePanel.test.tsx`,
  `frontend/src/pages/WorkflowNavigation.test.tsx`

**Interfaces:**
- Consumes: `listDatasetAnalysisHistory` (UX-B), existing
  `compareDatasetBenchmarks`, existing `EvaluationMetricsView`.
- Produces: navigation contract
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

- [ ] In `ExperimentComparePanel`, read `a`/`b` from `useSearchParams` and, when
      both are valid completed experiment evaluations, preselect and run the
      comparison once.
- [ ] In the dataset Analysis History surface (UX-B's
      `DatasetAnalysisHistory.tsx`), add an additive compare entry for exactly
      two selected evaluation-kind items → navigate to the compare URL above.
      Do not invent a new comparison engine; reuse `compareDatasetBenchmarks`.
- [ ] Run focused tests; observe pass.
- [ ] Commit: `feat(ux-c): dataset evaluation and compare entry`

---

## C7: Track boundary

- [ ] Focused backend (only if C1 made a backend change):
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

## Local CPU Diagnosis Outcome Record

This section is completed during task C1 with the actual finding:

```text
Deployment: Windows control plane, no GPU
Pipeline probed: <pipeline_id@version>
Observed candidate: technical=<bool> configured=<bool> certified=<bool> available=<bool> reason_code=<code>
First false predicate: <predicate>
Outcome branch: <A configuration | B backend defect | C genuinely inapplicable>
Action taken: <description>
Durable record: backend/tests/test_local_cpu_diagnosis.py + commit message
Remote-GPU reintroduced: no
```

---

## Self-Review Checklist

```text
[ ] Auto remains the default execution environment.
[ ] No frontend force-enable of the Run button.
[ ] No secrets/paths/certificate/SSH/telemetry exposed by the candidate panel.
[ ] Local CPU handled by evidence; Remote-GPU not reintroduced.
[ ] Comparison uses existing backend compare; no invented comparisons.
[ ] Dataset identity is prefilled from UX-B projection; no retyping required.
[ ] UX-C consumes UX-B contracts; no duplicate dataset representation.
[ ] Business state remains in the URL.
[ ] No GPU; no sealed-baseline changes.
```
