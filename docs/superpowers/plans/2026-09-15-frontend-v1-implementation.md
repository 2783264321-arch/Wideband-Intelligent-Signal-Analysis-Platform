# Frontend V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the approved WISA Frontend V1 workflows for single-recording analysis, dataset experiments, experiment comparison, and Algorithm Lab integration while keeping the backend as the sole execution authority.

**Architecture:** Preserve the existing React/Vite/Ant Design application and evolve it incrementally using `preserve → extract → extend`. Centralize backend coupling in the API/type layer, extract execution-environment and experiment features, and reconcile YELLOW contract drift only after Backend V1 API freeze.

**Tech Stack:** React, TypeScript, Vite, react-router-dom, Ant Design, Vitest, Testing Library, jsdom, existing fetch-based API client.

**Spec:** `docs/superpowers/specs/2026-09-15-frontend-v1-design.md`

---

## Global Constraints

Copied from the approved spec; these are normative for every task.

- **Backend is the sole execution authority.** The frontend renders backend
  facts; it never computes an independent capability matrix and never falls back
  between executors.
- **`auto` is a mode, not an executor.** `execution_mode = auto` resolves on the
  backend to one concrete `local_cpu | local_gpu | remote_gpu`. A persisted
  AnalysisRun/DatasetExperiment never has `executor = "auto"`.
- **Auto is the ordinary-user default**, but only when the backend returns a valid
  resolution; on `AUTO_NO_RUNNABLE_EXECUTOR` the UI shows Auto unavailable and
  never silently switches.
- **No plugin-id capability branches.** No `if (pipeline.id === …)`.
- **No hardcoded ModelRelease choices.** Never hardcode `golden`; never infer the
  resolved release.
- **Null / N/A is never rendered as zero.**
- **Centralized API client + centralized TS contracts.** Pages/components never
  call `fetch` directly.
- **Preserve the stack.** React, TypeScript, Vite, react-router-dom, Ant Design,
  Vitest, Testing Library, jsdom, existing fetch-based client. Do NOT introduce
  Redux, Zustand, React Query, MSW, another router, another UI library, or
  dependency modernization.
- **Product/operator boundary.** Ordinary V1 never exposes interpreter paths,
  asset paths, SSH material, certificate internals, cgroup/PSI/nvidia-smi
  telemetry, or Plan B/C evidence.
- **Primary navigation is exactly** `Recordings | Experiments | Algorithm Lab`;
  `Compare` is inside Experiments; `Settings` is not primary navigation.
- **Additive-field tolerance.** Unknown response fields never break the UI.
- **TDD, small tasks, frequent commits.** Each task is independently reviewable
  and normally owns one commit.

---

## Planned File / Responsibility Map

Existing files modified:

```text
frontend/src/api/types.ts                                  add V1 contracts
frontend/src/api/client.ts                                 add V1 client fns + mapping; refactor createAnalysisRun
frontend/src/api/client.test.ts                            extend contract tests
frontend/src/app/App.tsx                                   add Experiments routes
frontend/src/app/MainLayout.tsx                            navigation → Recordings | Experiments | Algorithm Lab
frontend/src/pages/SpectrumAnalysisPage.tsx                consume selector; provenance; polling (incremental)
frontend/src/pages/AlgorithmLabPage.tsx                    accept experiments drilldown URL state; re-home benchmarks tab (F5)
frontend/src/features/algorithm-lab/CaseAnalysisView.tsx   de-duplicate compare orchestration (F5)
```

Existing files retired:

```text
frontend/src/features/spectrum/executorPolicy.ts           DELETED in F1.4 (client-side authority)
frontend/src/features/spectrum/executorPolicy.test.ts      DELETED in F1.4 (replaced by selector tests)
```

New production files (each with a single responsibility):

```text
frontend/src/features/execution-environment/types.ts                 selector value/state types (re-exported domain types)
frontend/src/features/execution-environment/ExecutionEnvironmentSelector.tsx   reusable selector
frontend/src/features/execution-environment/executionEnvironment.ts  pure helpers: option state enumeration/mapping
frontend/src/features/analysis-run/requestBuilder.ts                 pure helper: buildAnalysisRunRequest (camelCase domain)
frontend/src/features/analysis-run/RunStatusBadge.tsx                status badge + bounded error
frontend/src/features/analysis-run/RunProvenanceCard.tsx             resolved executor + mode/reason provenance
frontend/src/features/analysis-run/useRunPolling.ts                  polling lifecycle hook
frontend/src/features/dataset-experiment/types.ts                    re-exports + form value types
frontend/src/features/dataset-experiment/ExperimentCreateForm.tsx    create form
frontend/src/features/dataset-experiment/ExperimentList.tsx          list table
frontend/src/features/dataset-experiment/ExperimentDetail.tsx        detail composition
frontend/src/features/dataset-experiment/ExperimentProgressHeader.tsx status badge + counters
frontend/src/features/dataset-experiment/ExperimentItemTable.tsx      items
frontend/src/features/dataset-experiment/AttemptTimeline.tsx          attempts + retry-failed
frontend/src/features/evaluation/EvaluationMetricsView.tsx           coverage + metrics + N/A
frontend/src/features/evaluation/ExperimentComparePanel.tsx          A/B selection + comparability
frontend/src/features/evaluation/CompareDeltaTable.tsx               deltas + shared-recording drilldown
frontend/src/pages/ExperimentsListPage.tsx                           experiments list page
frontend/src/pages/ExperimentDetailPage.tsx                          experiment detail page
frontend/src/pages/ExperimentComparePage.tsx                         compare page (inside Experiments)
```

New test files (one per production concern):

```text
frontend/src/features/execution-environment/executionEnvironment.test.ts
frontend/src/features/execution-environment/ExecutionEnvironmentSelector.test.tsx
frontend/src/features/analysis-run/analysisRun.test.ts
frontend/src/features/analysis-run/useRunPolling.test.tsx
frontend/src/features/analysis-run/RunStatusBadge.test.tsx
frontend/src/features/analysis-run/RunProvenanceCard.test.tsx
frontend/src/features/dataset-experiment/experimentContract.test.ts
frontend/src/features/dataset-experiment/ExperimentCreateForm.test.tsx
frontend/src/features/dataset-experiment/ExperimentList.test.tsx
frontend/src/features/dataset-experiment/ExperimentDetail.test.tsx
frontend/src/features/dataset-experiment/ExperimentItemTable.test.tsx
frontend/src/features/dataset-experiment/AttemptTimeline.test.tsx
frontend/src/features/evaluation/EvaluationMetricsView.test.tsx
frontend/src/features/evaluation/ExperimentComparePanel.test.tsx
frontend/src/features/evaluation/CompareDeltaTable.test.tsx
frontend/src/app/navigation.test.tsx
frontend/src/api/v1Contract.ts                 (type-consumption fixture; compiled by `tsc -b`, not a Vitest test)
```

Do NOT reorganize the entire frontend; do not move existing folders except the
retirement listed above.

---

## Interface Ledger

Exact intended names/signatures. The same name/signature MUST remain consistent
across every task. Wire types are snake_case; domain types are camelCase; mapping
lives in `api/client.ts`.

`frontend/src/api/types.ts` (new additions):

```ts
export type ExecutionMode = "manual" | "auto";

export interface ExecutionCandidate {
  executor: string;              // "local_cpu" | "local_gpu" | "remote_gpu"
  technical: boolean;
  configured: boolean;
  certified: boolean;
  available: boolean;
  reasonCode: string | null;
  reasonMessage: string | null;
}

export interface ExecutorSelection {
  requestedMode: ExecutionMode;
  resolvedExecutor: string | null;
  reasonCode: string;
  reason: string;
  workloadClass: string;         // "SMALL" | "GPU_BENEFICIAL" | "UNKNOWN"
  candidates: ExecutionCandidate[];
}

export type ExecutionSelectionScope =
  | { kind: "recording"; recordingId: string }
  | { kind: "dataset"; datasetName: string; datasetSplit: string; datasetLabelSpace: string };

// Public/domain request types are camelCase. snake_case exists ONLY in the
// private wire bodies inside api/client.ts (camelCase → wire translation).
export interface AnalysisRunCreateRequest {
  recordingId: string;
  pipelineId: string;
  executor?: string;
  executionMode?: ExecutionMode;
  modelReleaseId?: string | null;
  parameters: Record<string, unknown>;
}

export interface DatasetExperimentItem {
  id: string;
  experimentId: string;
  manifestOrder: number;
  recordingId: string;
  recordingName: string;
  status: string;                // "queued" | "running" | "completed" | "failed"
  lastErrorType: string | null;
  lastErrorMessage: string | null;
  latestAnalysisRunId: string | null;
  createdAt: string | null;
  updatedAt: string | null;
}

export interface DatasetExperimentAttempt {
  id: string;
  experimentItemId: string;
  attemptNumber: number;
  analysisRunId: string;         // mandatory in the backend Attempt read model
  launchRequestedAt: string | null;
  createdAt: string | null;
}

export interface DatasetExperiment {
  id: string;
  name: string;
  datasetName: string;
  datasetSplit: string;
  datasetLabelSpace: string;
  recordingManifestHash: string;

  pluginId: string;
  pluginVersion: string;
  modelReleaseId: string | null;
  assetManifestSha256: string | null;
  parameters: Record<string, unknown>;

  executor: string;              // frozen concrete execution identity

  evaluationProtocol: string;
  maxConcurrency: number;

  status: string;                // pending|running|evaluating|completed|completed_with_failures|failed
  datasetEvaluationId: string | null;

  errorType: string | null;
  errorMessage: string | null;

  requestedExecutionMode: ExecutionMode | null;
  autoReasonCode: string | null;
  autoReason: string | null;
  workloadClass: string | null;

  expectedItems: number;
  queuedItems: number;
  runningItems: number;
  completedItems: number;
  failedItems: number;
  attemptCount: number;

  createdAt: string | null;
  startedAt: string | null;
  completedAt: string | null;
}

export interface DatasetExperimentCreateRequest {
  name: string;
  datasetName: string;
  datasetSplit: string;
  datasetLabelSpace: string;
  pluginId: string;
  pluginVersion: string;
  executionMode: ExecutionMode;
  executor?: string;
  modelReleaseId?: string | null;
  parameters: Record<string, unknown>;
  evaluationProtocol: string;
  maxConcurrency: number;
}
```

`frontend/src/api/client.ts` (new/changed signatures):

```ts
export async function getExecutorSelection(params: {
  scope: ExecutionSelectionScope;
  pipelineId: string;
  modelReleaseId?: string | null;
}): Promise<import("./types").ExecutorSelection>;

export async function createAnalysisRun(
  request: import("./types").AnalysisRunCreateRequest,
): Promise<import("./types").AnalysisRun>;   // CHANGED signature (was positional)

export async function listDatasetExperiments(): Promise<import("./types").DatasetExperiment[]>;
export async function getDatasetExperiment(id: string): Promise<import("./types").DatasetExperiment>;
export async function createDatasetExperiment(
  request: import("./types").DatasetExperimentCreateRequest,
): Promise<import("./types").DatasetExperiment>;
export async function runDatasetExperiment(id: string): Promise<import("./types").DatasetExperiment>;
export async function retryFailedDatasetExperimentItems(id: string): Promise<import("./types").DatasetExperiment>;
export async function retryDatasetExperimentEvaluation(id: string): Promise<import("./types").DatasetExperiment>;
export async function listDatasetExperimentItems(id: string): Promise<import("./types").DatasetExperimentItem[]>;
export async function listDatasetExperimentItemAttempts(
  experimentId: string, itemId: string,
): Promise<import("./types").DatasetExperimentAttempt[]>;
```

Existing functions retained unchanged: `getExecutorAvailability` (recording-scoped),
`listPipelines`, `getAnalysisRun`, `listAnalysisRuns`, `getDetections`,
`getSpectrogram`, `getRecording`, `getGroundTruth`, dataset-benchmark evaluation
functions, `compareAnalysisRuns`, `PlatformApiError`.

Private wire request bodies (defined inside `api/client.ts` only; NOT exported to
features/pages):

```ts
interface AnalysisRunCreateWire {
  recording_id: string;
  pipeline_id: string;
  executor?: string;
  execution_mode?: ExecutionMode;
  model_release_id?: string | null;
  parameters: Record<string, unknown>;
}

interface DatasetExperimentCreateWire {
  name: string;
  dataset_name: string;
  dataset_split: string;
  dataset_label_space: string;
  plugin_id: string;
  plugin_version: string;
  execution_mode: ExecutionMode;
  executor?: string;
  model_release_id?: string | null;
  parameters: Record<string, unknown>;
  evaluation_protocol: string;
  max_concurrency: number;
}
```

`createAnalysisRun()` and `createDatasetExperiment()` translate the camelCase
domain request into the private wire body. `buildAnalysisRunRequest()` and
`toCreateRequest()` return **domain camelCase** request objects, never wire
objects.

`frontend/src/features/execution-environment/types.ts`:

```ts
import type { ExecutionMode } from "../../api/types";

export interface ExecutionEnvironmentValue {
  mode: ExecutionMode;
  executor: string | null;       // concrete executor when mode === "manual"
}

export interface ExecutionEnvironmentSelectorProps {
  selection: import("../../api/types").ExecutorSelection | null;
  loading: boolean;
  error: string | null;
  value: ExecutionEnvironmentValue;
  onChange: (value: ExecutionEnvironmentValue) => void;
  disabled?: boolean;
}
```

`frontend/src/features/execution-environment/executionEnvironment.ts`:

```ts
export type ExecutorOptionKey = "auto" | "local_cpu" | "local_gpu" | "remote_gpu";

export interface ExecutorOptionState {
  key: ExecutorOptionKey;
  executor: string | null;                      // null for "auto"
  label: string;                                // "Auto" | "Local CPU" | "Local GPU" | "Remote GPU"
  enabled: boolean;
  state: "available" | "not_configured" | "not_certified" | "unsupported" | "temporarily_unavailable" | "unresolved";
  reasonCode: string | null;
  reasonMessage: string | null;
}

export const EXECUTOR_OPTIONS: readonly ExecutorOptionKey[];   // fixed vocabulary, always enumerated

export function optionStateFromCandidate(candidate: import("../../api/types").ExecutionCandidate): ExecutorOptionState;
export function autoOptionState(selection: import("../../api/types").ExecutorSelection | null): ExecutorOptionState;
export function optionsFromSelection(selection: import("../../api/types").ExecutorSelection | null): ExecutorOptionState[];
```

`frontend/src/features/dataset-experiment/types.ts`:

```ts
export interface ExperimentFormValue {
  name: string;
  datasetName: string;
  datasetSplit: string;
  datasetLabelSpace: string;
  pluginId: string;
  pluginVersion: string;
  environment: import("../execution-environment/types").ExecutionEnvironmentValue;
  maxConcurrency: number;
  evaluationProtocol: string;
}

export function toCreateRequest(value: ExperimentFormValue): import("../../api/types").DatasetExperimentCreateRequest;
```

---

## Part I — F0 Contract Foundation

### F0.1 Fresh baseline

**Files:**
- Modify: none
- Test: none (execution-time evidence)

**Interfaces:**
- Consumes: existing repository.
- Produces: recorded baseline evidence.

- [ ] Step 1: run `cd frontend; npm test -- --run`
- [ ] Step 2: run `cd frontend; npm run build`
- [ ] Step 3: record exact test/build results (files, tests passed/failed, build exit)
- [ ] Step 4: if baseline fails, STOP implementation and report; otherwise proceed
- [ ] Step 5: commit — none (evidence recorded in the task report; no file change)

### F0.2 V1 contract + domain types (compile-verified)

**Files:**
- Modify: `frontend/src/api/types.ts`
- Test: `frontend/src/api/v1Contract.ts` (a type-consumption fixture compiled by
  `tsc -b`; not a runtime Vitest test)

**Interfaces:**
- Consumes: backend wire shapes at `d4b22ee`.
- Produces: `ExecutionMode`, `ExecutionCandidate`, `ExecutorSelection`,
  `ExecutionSelectionScope`, `AnalysisRunCreateRequest`,
  `DatasetExperimentItem`, `DatasetExperimentAttempt`, `DatasetExperiment`,
  `DatasetExperimentCreateRequest` (exact fields per Interface Ledger).

Type-only RED/GREEN uses the TypeScript compiler, not Vitest (Vitest/esbuild does
not typecheck). `tsconfig.app.json` includes the whole `src`, so compile errors
surface through `npm run build` (`tsc -b`).

- [ ] Step 1: add a type-consumption fixture `frontend/src/api/v1Contract.ts`
  that imports the intended names and constructs representative objects
  (camelCase fields, `requestedExecutionMode: ExecutionMode | null`,
  `DatasetExperimentAttempt.analysisRunId: string`, concrete
  `DatasetExperiment.executor: string`)
- [ ] Step 2: run `cd frontend; npm run build`; expected RED = TypeScript compile
  failure due to missing type exports / incompatible object shape
- [ ] Step 3: add the interfaces exactly as in the Interface Ledger (no extra
  authority fields; derive nothing; do NOT export `runtime_descriptor_json`)
- [ ] Step 4: run `cd frontend; npm run build`; expect GREEN (exit 0)
- [ ] Step 5: run `cd frontend; npm test -- --run` for the surrounding suite;
  expect no regressions
- [ ] Step 6: commit `feat(frontend): add frontend v1 contract types`

---

## Part II — F1 Execution Environment Foundations

### F1.1 `getExecutorSelection` client + scope guardrails + mapping

**Files:**
- Modify: `frontend/src/api/client.ts`
- Test: `frontend/src/api/client.test.ts`

**Interfaces:**
- Consumes: `ExecutionSelectionScope`, `ExecutorSelection`, `ExecutionCandidate`,
  `PlatformApiError`.
- Produces: `getExecutorSelection({ scope, pipelineId, modelReleaseId? })` and the
  internal `assertSelectionScope(scope)` used before request construction.

This task owns the whole executor-selection client concern so F0 never tests a
function that does not yet exist. The discriminated `ExecutionSelectionScope`
union gives compile-time scope safety; `assertSelectionScope` is the runtime guard
for malformed JS/casts.

- [ ] Step 1: failing tests — recording scope calls
  `/api/executor-selection?recording_id=…&pipeline_id=…`; dataset scope calls
  `…?dataset_name=…&dataset_split=…&dataset_label_space=…&pipeline_id=…`;
  optional `model_release_id` included only when provided; a scope with neither or
  both members is rejected before any `fetch` call; a non-2xx
  `{error:{code,message,details}}` produces a `PlatformApiError` preserving
  code/message/details; response maps snake_case → camelCase including
  `candidates` with `technical/configured/certified/available/reason_code/reason_message`
- [ ] Step 2: run `cd frontend; npm test -- --run src/api/client.test.ts`; expected RED
- [ ] Step 3: implement `assertSelectionScope`, `getExecutorSelection`, and
  `mapExecutorSelection`
- [ ] Step 4: run `cd frontend; npm test -- --run src/api/client.test.ts`; PASS
- [ ] Step 5: run `cd frontend; npm test -- --run`; expect no regressions
- [ ] Step 6: commit `feat(frontend): add executor selection client`

### F1.2 `ExecutionEnvironmentSelector`

**Files:**
- Create: `frontend/src/features/execution-environment/types.ts`,
  `frontend/src/features/execution-environment/ExecutionEnvironmentSelector.tsx`,
  `frontend/src/features/execution-environment/executionEnvironment.ts`
- Test: `frontend/src/features/execution-environment/executionEnvironment.test.ts`,
  `frontend/src/features/execution-environment/ExecutionEnvironmentSelector.test.tsx`

**Interfaces:**
- Consumes: `ExecutorSelection`, `ExecutionEnvironmentValue`,
  `ExecutionEnvironmentSelectorProps`.
- Produces: `optionsFromSelection`, `autoOptionState`, `optionStateFromCandidate`,
  `EXECUTOR_OPTIONS`, and the selector component.

- [ ] Step 1: failing pure tests (`executionEnvironment.test.ts`) for the state
  matrix: `technical=false → unsupported`; `configured=false → not_configured`;
  `certified=false → not_certified`; `available=false → temporarily_unavailable`;
  all true `→ available`; Auto with a resolved executor `→ enabled`; Auto with
  `AUTO_NO_RUNNABLE_EXECUTOR → unresolved` and disabled. Also: all four options
  are always enumerated even when a candidate is absent.
- [ ] Step 2: RED
- [ ] Step 3: implement `executionEnvironment.ts`
- [ ] Step 4: pure tests PASS
- [ ] Step 5: failing component test: renders Auto + 3 options; renders bounded
  reason text for disabled options; `onChange` emits `{mode:"manual",executor}`
  when a manual option is chosen and `{mode:"auto",executor:null}` for Auto;
  never renders interpreter paths/certificate internals
- [ ] Step 6: RED, implement `ExecutionEnvironmentSelector.tsx`, PASS
- [ ] Step 7: `npm test -- --run`
- [ ] Step 8: commit `feat(frontend): add execution environment selector`

### F1.3 Request semantics (manual vs auto)

**Files:**
- Create: `frontend/src/features/analysis-run/requestBuilder.ts` (pure helper;
  no `AnalysisRunForm.tsx` component is planned — behavior is composed directly by
  `SpectrumAnalysisPage`, so YAGNI applies)
- Modify: `frontend/src/api/client.ts` (`createAnalysisRun` signature change)
- Test: `frontend/src/features/analysis-run/analysisRun.test.ts`,
  `frontend/src/api/client.test.ts`

**Interfaces:**
- Consumes: `ExecutionEnvironmentValue`, `AnalysisRunCreateRequest`.
- Produces: `buildAnalysisRunRequest({ recordingId, pipelineId, environment, modelReleaseId? }): AnalysisRunCreateRequest` (domain camelCase).

- [ ] Step 1: failing tests: manual → `{executor:"local_cpu"}` and NO
  `executionMode:"auto"`; manual `local_gpu`/`remote_gpu` identical shape; auto →
  `{executionMode:"auto"}` and NO `executor`; `parameters` always `{}`;
  `createAnalysisRun(request)` translates the camelCase domain request into the
  private snake_case wire body and posts it
- [ ] Step 2: run `cd frontend; npm test -- --run src/features/analysis-run/analysisRun.test.ts`; RED
- [ ] Step 3: implement `buildAnalysisRunRequest`; implement the client-side
  camelCase→wire translation and change `createAnalysisRun` signature to accept
  `AnalysisRunCreateRequest`; update all callers
- [ ] Step 4: run `cd frontend; npm test -- --run`; PASS
- [ ] Step 5: run `cd frontend; npm run build`; expect exit 0
- [ ] Step 6: commit `feat(frontend): enforce manual/auto request semantics`

### F1.4 Retire client-side executor policy

**Files:**
- Modify: `frontend/src/pages/SpectrumAnalysisPage.tsx`
- Delete: `frontend/src/features/spectrum/executorPolicy.ts`,
  `frontend/src/features/spectrum/executorPolicy.test.ts`
- Test: `frontend/src/pages/SpectrumAnalysisPage.test.tsx`

**Interfaces:**
- Consumes: `ExecutionEnvironmentSelector`, `getExecutorSelection`,
  `buildAnalysisRunRequest`.
- Produces: Spectrum uses the selector (Auto default) and creates runs from the
  environment value.

- [ ] Step 1: update `SpectrumAnalysisPage.test.tsx` to assert: the selector is
  rendered; executor selection comes from `/api/executor-selection` (not client
  logic); unavailable options are disabled with bounded reasons; no client
  fallback occurs
- [ ] Step 2: RED (old page still imports `executorPolicy`)
- [ ] Step 3: integrate the selector and selection fetch into
  `SpectrumAnalysisPage`; remove `executorPolicy` import; delete the policy file
  and its test
- [ ] Step 4: PASS
- [ ] Step 5: `npm test -- --run`
- [ ] Step 6: commit `refactor(frontend): retire client-side executor policy`

**Review checkpoint: after F1** (human/independent review of the execution
environment foundation before F2).

---

## Part III — F2 Single Recording V1

### F2.1 AnalysisRun provenance

**Files:**
- Create: `frontend/src/features/analysis-run/RunProvenanceCard.tsx`
- Modify: `frontend/src/api/types.ts` (`AnalysisRun.executionMetadata` typed as a
  read-only optional bag), `frontend/src/pages/SpectrumAnalysisPage.tsx`
- Test: `frontend/src/features/analysis-run/analysisRun.test.ts`

**Interfaces:**
- Consumes: `AnalysisRun.executionMetadata` optional fields
  (`requested_execution_mode`, `auto_reason_code`, `auto_reason`,
  `workload_class`, `remote_profile`, `required_remote_runtime_commit`,
  `payload_sha256`).
- Produces: `RunProvenanceCard` rendering resolved executor + mode/reason.

- [ ] Step 1: failing tests: card shows the concrete resolved executor; shows
  `requested_execution_mode` and `auto_reason_code`/`auto_reason` when present;
  does NOT render any inferred resolved ModelRelease; does NOT render
  interpreter/asset paths
- [ ] Step 2: RED
- [ ] Step 3: implement the card; wire into Spectrum
- [ ] Step 4: PASS
- [ ] Step 5: `npm test -- --run`
- [ ] Step 6: commit `feat(frontend): add analysis run provenance card`

### F2.2 Polling lifecycle

**Files:**
- Create: `frontend/src/features/analysis-run/useRunPolling.ts`
- Modify: `frontend/src/pages/SpectrumAnalysisPage.tsx`
- Test: `frontend/src/features/analysis-run/useRunPolling.test.tsx`

**Interfaces:**
- Consumes: `getAnalysisRun`, `getDetections`.
- Produces: `useRunPolling({ runId, onRun, onDetections })` — polls while
  status is `pending`/`running`, stops on `completed`/`failed`/`interrupted`, and
  cancels on run-id change/unmount.

- [ ] Step 1: failing tests with fake timers: pending → continues; running →
  continues; completed → stops and fetches detections once; failed → stops;
  interrupted → stops; changing `runId` cancels the previous timer (no stale
  update)
- [ ] Step 2: RED
- [ ] Step 3: implement the hook; replace the inline interval in Spectrum
- [ ] Step 4: PASS
- [ ] Step 5: `npm test -- --run`
- [ ] Step 6: commit `feat(frontend): extract analysis run polling lifecycle`

### F2.3 Status badge + bounded errors

**Files:**
- Create: `frontend/src/features/analysis-run/RunStatusBadge.tsx`
- Modify: `frontend/src/pages/SpectrumAnalysisPage.tsx`
- Test: `frontend/src/features/analysis-run/RunStatusBadge.test.tsx`

**Interfaces:**
- Consumes: `AnalysisRun.status`, `errorType`, `errorMessage`.
- Produces: `RunStatusBadge` with mapped label + preserved bounded code.

- [ ] Step 1: failing tests: each status maps to a label; a failed run shows the
  bounded `errorType` (e.g. `ANALYSIS_LAUNCH_AMBIGUOUS`, `INPUT_INCOMPATIBLE`)
  alongside human-readable text; code is never dropped
- [ ] Step 2: RED
- [ ] Step 3: implement `RunStatusBadge`; wire into Spectrum
- [ ] Step 4: PASS
- [ ] Step 5: `npm test -- --run`
- [ ] Step 6: commit `feat(frontend): add run status badge with bounded errors`

### F2.4 Preserve Signals/Signal Detail and deep links

**Files:**
- Modify: `frontend/src/pages/SpectrumAnalysisPage.tsx` (navigation only)
- Test: `frontend/src/pages/SpectrumAnalysisPage.test.tsx`

**Interfaces:**
- Consumes: existing `/signals/:runId` and `/signals/:runId/:detectionId` routes.
- Produces: unchanged deep-link behavior (`?run=`, `?selected=`).

- [ ] Step 1: failing/updated tests asserting `?run=` and `?selected=` deep links
  still resolve and that `SignalsPage`/`SignalDetailPage` remain reachable
- [ ] Step 2: RED
- [ ] Step 3: adjust navigation wiring only
- [ ] Step 4: PASS
- [ ] Step 5: `npm test -- --run`
- [ ] Step 6: commit `test(frontend): lock single recording deep links`

**Review checkpoint: after F2.**

---

## Part IV — F3 Dataset Experiments

### F3.1 dataset-experiment client + mapping

**Files:**
- Modify: `frontend/src/api/client.ts`, `frontend/src/api/types.ts`
- Test: `frontend/src/api/client.test.ts`

**Interfaces:**
- Consumes: `/api/dataset-experiments*` wire shapes.
- Produces: `listDatasetExperiments`, `getDatasetExperiment`,
  `createDatasetExperiment`, `runDatasetExperiment`,
  `retryFailedDatasetExperimentItems`, `retryDatasetExperimentEvaluation`,
  `listDatasetExperimentItems`, `listDatasetExperimentItemAttempts` (all per
  Interface Ledger).

- [ ] Step 1: failing mapping tests for each function (snake_case → camelCase),
  including `recording_manifest_hash`, the concrete `executor`,
  `requested_execution_mode` (nullable), `auto_reason_code`, `auto_reason`,
  `workload_class`, `model_release_id`, `asset_manifest_sha256`, counters, item
  statuses, and attempt fields (mandatory `analysis_run_id`); `runtime_descriptor_json`
  is NOT surfaced in the domain type
- [ ] Step 2: run `cd frontend; npm test -- --run src/api/client.test.ts`; RED
- [ ] Step 3: implement client + mappers (camelCase domain; private wire types)
- [ ] Step 4: run `cd frontend; npm test -- --run`; PASS
- [ ] Step 5: run `cd frontend; npm run build`; expect exit 0
- [ ] Step 6: commit `feat(frontend): add dataset experiment client`

### F3.2 Routes + navigation shell

**Files:**
- Modify: `frontend/src/app/App.tsx`, `frontend/src/app/MainLayout.tsx`
- Create: `frontend/src/pages/ExperimentsListPage.tsx` (thin shell),
  `frontend/src/pages/ExperimentDetailPage.tsx` (thin shell),
  `frontend/src/pages/ExperimentComparePage.tsx` (thin shell)
- Test: `frontend/src/app/navigation.test.tsx`

**Interfaces:**
- Consumes: react-router-dom.
- Produces: routes `/experiments`, `/experiments/:experimentId`,
  `/experiments/compare`; sidebar items exactly `Recordings`, `Experiments`,
  `Algorithm Lab` (no `Settings`).

- [ ] Step 1: failing navigation test: sidebar contains exactly the three
  primary items and no Settings; `/experiments` routes resolve to a shell
- [ ] Step 2: RED
- [ ] Step 3: add routes and update `MainLayout` navigation (keep `/settings`
  route resolvable but out of the sidebar)
- [ ] Step 4: PASS
- [ ] Step 5: `npm test -- --run`
- [ ] Step 6: commit `feat(frontend): add experiments routes and navigation`

### F3.3 Experiment list

**Files:**
- Create: `frontend/src/features/dataset-experiment/ExperimentList.tsx`
- Modify: `frontend/src/pages/ExperimentsListPage.tsx`
- Test: `frontend/src/features/dataset-experiment/ExperimentList.test.tsx`

**Interfaces:**
- Consumes: `listDatasetExperiments`.
- Produces: `ExperimentList` table (name, dataset, plugin, status, counts,
  link to detail).

- [ ] Step 1: failing tests: renders rows from the client; status shown as a
  badge; empty state renders a bounded empty message; error state renders
  `PlatformApiError` display
- [ ] Step 2: RED
- [ ] Step 3: implement
- [ ] Step 4: PASS
- [ ] Step 5: `npm test -- --run`
- [ ] Step 6: commit `feat(frontend): add experiment list`

### F3.4 Create form (exact dataset triple; dataset-scoped selection)

**Files:**
- Create: `frontend/src/features/dataset-experiment/types.ts`,
  `frontend/src/features/dataset-experiment/ExperimentCreateForm.tsx`
- Modify: `frontend/src/pages/ExperimentsListPage.tsx`
- Test: `frontend/src/features/dataset-experiment/ExperimentCreateForm.test.tsx`,
  `frontend/src/features/dataset-experiment/experimentContract.test.ts`

**Interfaces:**
- Consumes: `getExecutorSelection` (dataset scope), `optionsFromSelection`,
  `createDatasetExperiment`, `toCreateRequest`.
- Produces: `ExperimentFormValue`, `toCreateRequest`, `ExperimentCreateForm`.

- [ ] Step 1: failing tests: `toCreateRequest` returns a camelCase domain
  request with `datasetName`/`datasetSplit`/`datasetLabelSpace` exactly as
  entered; `parameters` is `{}`; `evaluationProtocol` and `maxConcurrency` set;
  manual environment sets `executor` and no `executionMode:"auto"`; auto
  environment sets `executionMode:"auto"` and no `executor`; the client-translated
  wire body contains `dataset_name`/`dataset_split`/`dataset_label_space`
  (`plugin_id`/`plugin_version`) and does NOT contain
  `recording_manifest_hash`/`asset_manifest_sha256`/`runtime_descriptor_json`;
  `modelReleaseId` omitted unless supplied externally; NO hardcoded
  `SpaceNet`/`test`/`spacenet_14`
- [ ] Step 2: run `cd frontend; npm test -- --run src/features/dataset-experiment/ExperimentCreateForm.test.tsx`; RED
- [ ] Step 3: implement; call `getExecutorSelection` with dataset scope for the
  selector; never call `getExecutorAvailability`
- [ ] Step 4: run `cd frontend; npm test -- --run`; PASS
- [ ] Step 5: run `cd frontend; npm run build`; expect exit 0
- [ ] Step 6: commit `feat(frontend): add dataset experiment create form`

### F3.5 Run / start lifecycle

**Files:**
- Modify: `frontend/src/features/dataset-experiment/ExperimentList.tsx`,
  `frontend/src/pages/ExperimentDetailPage.tsx`
- Test: `frontend/src/features/dataset-experiment/ExperimentDetail.test.tsx`

**Interfaces:**
- Consumes: `runDatasetExperiment`, `retryFailedDatasetExperimentItems`.
- Produces: run/retry actions with lifecycle-aware enablement.

- [ ] Step 1: failing tests: a `pending` experiment exposes Run; a `failed`/
  `completed_with_failures` experiment exposes Retry Failed; actions call the
  correct client function and refresh the experiment
- [ ] Step 2: RED
- [ ] Step 3: implement
- [ ] Step 4: PASS
- [ ] Step 5: `npm test -- --run`
- [ ] Step 6: commit `feat(frontend): add experiment run and retry-failed actions`

### F3.6 Experiment detail + progress header

**Files:**
- Create: `frontend/src/features/dataset-experiment/ExperimentProgressHeader.tsx`,
  `frontend/src/features/dataset-experiment/ExperimentDetail.tsx`
- Modify: `frontend/src/pages/ExperimentDetailPage.tsx`
- Test: `frontend/src/features/dataset-experiment/ExperimentDetail.test.tsx`

**Interfaces:**
- Consumes: `getDatasetExperiment`, counters, `status`, the concrete `executor`,
  `requestedExecutionMode`/`autoReasonCode`/`autoReason`/`workloadClass`,
  `errorType`.
- Produces: progress header (status badge + counters + frozen executor) and detail
  composition.

- [ ] Step 1: failing tests: renders statuses
  `pending|running|evaluating|completed|completed_with_failures|failed`;
  renders counters `expected/queued/running/completed/failed/attempt_count` as
  provided (never recomputed as authority); renders the concrete `executor`
  (frozen execution identity) and, when present, `requestedExecutionMode` +
  `autoReasonCode`/`autoReason`; handles `requestedExecutionMode === null`;
  renders bounded `errorType` on failure; polling continues while non-terminal and
  stops on terminal
- [ ] Step 2: run `cd frontend; npm test -- --run src/features/dataset-experiment/ExperimentDetail.test.tsx`; RED
- [ ] Step 3: implement
- [ ] Step 4: run `cd frontend; npm test -- --run`; PASS
- [ ] Step 5: run `cd frontend; npm run build`; expect exit 0
- [ ] Step 6: commit `feat(frontend): add experiment detail and progress`

### F3.7 Item table

**Files:**
- Create: `frontend/src/features/dataset-experiment/ExperimentItemTable.tsx`
- Modify: `frontend/src/features/dataset-experiment/ExperimentDetail.tsx`
- Test: `frontend/src/features/dataset-experiment/ExperimentItemTable.test.tsx`

**Interfaces:**
- Consumes: `listDatasetExperimentItems`.
- Produces: item table with `queued|running|completed|failed` badges and links to
  the linked AnalysisRun.

- [ ] Step 1: failing tests: renders items; each item status maps to a badge;
  a completed item links to `/signals/{latestAnalysisRunId}`; a failed item
  shows bounded `lastErrorType`/`lastErrorMessage`
- [ ] Step 2: RED
- [ ] Step 3: implement
- [ ] Step 4: PASS
- [ ] Step 5: `npm test -- --run`
- [ ] Step 6: commit `feat(frontend): add experiment item table`

### F3.8 Attempts + retry-failed

**Files:**
- Create: `frontend/src/features/dataset-experiment/AttemptTimeline.tsx`
- Modify: `frontend/src/features/dataset-experiment/ExperimentDetail.tsx`
- Test: `frontend/src/features/dataset-experiment/AttemptTimeline.test.tsx`

**Interfaces:**
- Consumes: `listDatasetExperimentItemAttempts`,
  `retryFailedDatasetExperimentItems`.
- Produces: per-item attempt timeline; retry action refreshes.

`DatasetExperimentAttempt.analysisRunId` is mandatory; `latestAnalysisRunId` is
derived from the newest attempt. Launch ambiguity is NEVER inferred locally from a
missing run id.

- [ ] Step 1: failing tests: an attempt renders `attemptNumber`,
  `analysisRunId` and `launchRequestedAt`, and links to the AnalysisRun; retry-
  failed calls the client and refreshes the experiment; the component never
  computes an ambiguity state from a null run id
- [ ] Step 2: run `cd frontend; npm test -- --run src/features/dataset-experiment/AttemptTimeline.test.tsx`; RED
- [ ] Step 3: implement (display + link only; no local ambiguity inference)
- [ ] Step 4: run `cd frontend; npm test -- --run`; PASS
- [ ] Step 5: run `cd frontend; npm run build`; expect exit 0
- [ ] Step 6: commit `feat(frontend): add experiment attempts and retry`

Launch ambiguity is surfaced only from authoritative backend state: an
`AnalysisRun.errorType === "ANALYSIS_LAUNCH_AMBIGUOUS"` (status `interrupted`),
or an item-level bounded error projection if one is actually returned. F3 does not
manufacture that state locally.

### F3.9 Linked evaluation entry

**Files:**
- Modify: `frontend/src/features/dataset-experiment/ExperimentDetail.tsx`
- Test: `frontend/src/features/dataset-experiment/ExperimentDetail.test.tsx`

**Interfaces:**
- Consumes: `datasetEvaluationId`, `retryDatasetExperimentEvaluation`,
  `getDatasetBenchmark`.
- Produces: linked-evaluation section on the experiment detail.

- [ ] Step 1: failing tests: when `datasetEvaluationId` is set, the detail shows
  the linked evaluation summary (via F4 view or a minimal placeholder that
  renders `status` + coverage); `evaluating` state disables retry-evaluation;
  `retryDatasetExperimentEvaluation` calls the correct endpoint (not the generic
  benchmark retry)
- [ ] Step 2: RED
- [ ] Step 3: implement
- [ ] Step 4: PASS
- [ ] Step 5: `npm test -- --run`
- [ ] Step 6: commit `feat(frontend): link experiment evaluation`

**Review checkpoint: after F3.**

---

## Part V — F4 Evaluation + Multi-model Compare

### F4.1 Evaluation metrics view (N/A semantics)

**Files:**
- Create: `frontend/src/features/evaluation/EvaluationMetricsView.tsx`
- Modify: `frontend/src/features/dataset-experiment/ExperimentDetail.tsx`
- Test: `frontend/src/features/evaluation/EvaluationMetricsView.test.tsx`

**Interfaces:**
- Consumes: `DatasetEvaluation` (coverage, `aggregateMetrics`, `perClassMetrics`,
  `confusion`); classification applicability is read from
  `aggregateMetrics?.classificationApplicable` and
  `aggregateMetrics?.classificationReason` (NOT top-level fields).
- Produces: `EvaluationMetricsView`.

- [ ] Step 1: failing tests: coverage shown; localization `ap50`/`ap50_95` shown;
  `aggregateMetrics === null` renders an explicit metrics-unavailable/pending/empty
  state without inventing numbers; a null metric renders `N/A` (never `0`);
  `aggregateMetrics.classificationApplicable === false` renders classification
  metrics as `N/A` + the bounded `aggregateMetrics.classificationReason`
  (`detection_only_pipeline` | `label_space_mismatch` |
  `unknown_classification_semantics`); per-class/confusion sections render an
  explicit empty/inapplicable state rather than a bare empty table
- [ ] Step 2: run `cd frontend; npm test -- --run src/features/evaluation/EvaluationMetricsView.test.tsx`; RED
- [ ] Step 3: implement; reuse existing `features/dataset-benchmarks` formatting
  helpers where they already exist
- [ ] Step 4: run `cd frontend; npm test -- --run`; PASS
- [ ] Step 5: run `cd frontend; npm run build`; expect exit 0
- [ ] Step 6: commit `feat(frontend): add evaluation metrics with N/A semantics`

### F4.2 Experiment A/B selection + comparability

**Files:**
- Create: `frontend/src/features/evaluation/ExperimentComparePanel.tsx`
- Modify: `frontend/src/pages/ExperimentComparePage.tsx`
- Test: `frontend/src/features/evaluation/ExperimentComparePanel.test.tsx`

**Interfaces:**
- Consumes: `listDatasetExperiments`, `compareDatasetBenchmarks`.
- Produces: `ExperimentComparePanel` (pick two experiments → evaluate compare).

- [ ] Step 1: failing tests: two completed experiments can be selected and
  compared; non-comparable results render every bounded `reasons[]` entry and
  invent no numeric delta; compare uses the linked `datasetEvaluationId`s
- [ ] Step 2: RED
- [ ] Step 3: implement
- [ ] Step 4: PASS
- [ ] Step 5: `npm test -- --run`
- [ ] Step 6: commit `feat(frontend): add experiment compare selection`

### F4.3 Deltas + shared-recording drilldown

**Files:**
- Create: `frontend/src/features/evaluation/CompareDeltaTable.tsx`
- Modify: `frontend/src/features/evaluation/ExperimentComparePanel.tsx`
- Test: `frontend/src/features/evaluation/CompareDeltaTable.test.tsx`

**Interfaces:**
- Consumes: `DatasetBenchmarkCompareResult` (`deltas`, aggregates),
  `listDatasetBenchmarkItems`.
- Produces: `CompareDeltaTable` with null→`N/A` deltas and a shared-recording
  "Open in Algorithm Lab" link.

- [ ] Step 1: failing tests: localization deltas shown; null delta → `N/A`;
  classification delta `N/A` when inapplicable; a shared recording offers a link
  to `/algorithm-lab?recording=…&runA=…&runB=…`; no invented numbers
- [ ] Step 2: RED
- [ ] Step 3: implement
- [ ] Step 4: PASS
- [ ] Step 5: `npm test -- --run`
- [ ] Step 6: commit `feat(frontend): add compare deltas and drilldown`

---

## Part VI — F5 Algorithm Lab Integration

### F5.1 Lock existing compare behavior with regression tests

**Files:**
- Modify: `frontend/src/features/algorithm-lab/CaseAnalysisView.test.tsx`
- Test: (same)

**Interfaces:**
- Consumes: `compareAnalysisRuns`.
- Produces: regression coverage for the current per-recording A/B behavior.

- [ ] Step 1: add tests asserting current compare behavior (IoU 0.5, comparison
  states, class-correct `N/A`) without changing production code
- [ ] Step 2: run focused test; expect PASS (these lock existing behavior)
- [ ] Step 3: no production change
- [ ] Step 4: run `npm test -- --run`
- [ ] Step 5: commit `test(frontend): lock algorithm lab compare behavior`

### F5.2 De-duplicate compare orchestration

**Files:**
- Modify: `frontend/src/features/algorithm-lab/CaseAnalysisView.tsx`
- Test: `frontend/src/features/algorithm-lab/CaseAnalysisView.test.tsx`

**Interfaces:**
- Consumes: unchanged public props.
- Produces: single compare path (no duplicate `runCompare` + auto-effect).

- [ ] Step 1: confirm F5.1 tests are GREEN (behavior locked)
- [ ] Step 2: refactor to one compare effect; keep behavior identical
- [ ] Step 3: run focused test; expect PASS
- [ ] Step 4: `npm test -- --run`
- [ ] Step 5: commit `refactor(frontend): de-duplicate algorithm lab compare`

### F5.3 Experiments → Algorithm Lab drilldown

**Files:**
- Modify: `frontend/src/pages/AlgorithmLabPage.tsx`,
  `frontend/src/features/evaluation/CompareDeltaTable.tsx` (link consumer only)
- Test: `frontend/src/pages/AlgorithmLabPage.test.tsx`

**Interfaces:**
- Consumes: URL params `recording`, `runA`, `runB`.
- Produces: Algorithm Lab opens the per-recording case from a compare drilldown.

- [ ] Step 1: failing test: navigating with `recording`/`runA`/`runB` opens the
  case tab with the correct selection; the dataset-benchmarks tab is re-homed
  under Experiments without breaking existing `/algorithm-lab?tab=…` links
- [ ] Step 2: RED
- [ ] Step 3: implement
- [ ] Step 4: PASS
- [ ] Step 5: `npm test -- --run`
- [ ] Step 6: commit `feat(frontend): link experiment compare to algorithm lab`

**Review checkpoint: after F4/F5 (i.e. after F5.3).** Human/independent F4/F5
integration review runs once F5.3 is complete, before F6.

---

## Part VII — F6 UX / Status / Error Hardening

### F6.1 Converge on `PlatformApiError`

**Files:**
- Modify: `frontend/src/pages/RecordingsPage.tsx`,
  `frontend/src/pages/SignalsPage.tsx`,
  `frontend/src/pages/SignalDetailPage.tsx`,
  `frontend/src/pages/SpectrumAnalysisPage.tsx`,
  `frontend/src/features/imports/ImportRunModal.tsx`,
  `frontend/src/features/algorithm-lab/CaseAnalysisView.tsx`,
  `frontend/src/api/client.ts` (`compareAnalysisRuns`, `importAnalysisPackage`
  must rethrow `PlatformApiError`, not a plain `Error`)
- Test: `frontend/src/features/imports/ImportRunModal.test.tsx`,
  `frontend/src/pages/SpectrumAnalysisPage.test.tsx`

**Interfaces:**
- Consumes: `PlatformApiError`.
- Produces: consistent `PlatformApiError.display` rendering and preserved codes.

- [ ] Step 1: failing tests: `compareAnalysisRuns` and `importAnalysisPackage`
  rethrow `PlatformApiError`; pages render `code: message` (not a collapsed
  generic message)
- [ ] Step 2: RED
- [ ] Step 3: implement; replace `reason instanceof Error ? reason.message`
  collapse in the listed pages with `PlatformApiError`-aware rendering
- [ ] Step 4: PASS
- [ ] Step 5: `npm test -- --run`
- [ ] Step 6: commit `fix(frontend): preserve platform error codes across pages`

### F6.2 Status / error model

**Files:**
- Create: `frontend/src/features/analysis-run/statusModel.ts` (shared mapping)
- Modify: `frontend/src/features/analysis-run/RunStatusBadge.tsx`,
  `frontend/src/features/dataset-experiment/ExperimentProgressHeader.tsx`
- Test: `frontend/src/features/analysis-run/statusModel.test.ts`

**Interfaces:**
- Consumes: bounded statuses/reason codes.
- Produces: `describeStatus(kind, value)` mapping for
  `unsupported | not configured | not certified | temporarily unavailable |
  input incompatible | run failed | run interrupted | launch ambiguous |
  evaluation failed | AUTO_NO_RUNNABLE_EXECUTOR`.

- [ ] Step 1: failing tests: each bounded class maps to a readable label while
  preserving the raw code
- [ ] Step 2: RED
- [ ] Step 3: implement and wire into badges
- [ ] Step 4: PASS
- [ ] Step 5: `npm test -- --run`
- [ ] Step 6: commit `feat(frontend): add unified status/error model`

### F6.3 Loading/empty/polling/badges/accessibility

**Files:**
- Modify: the pages/features listed in F6.1 plus new experiment/evaluation
  features
- Test: `frontend/src/app/navigation.test.tsx` and affected feature tests

**Interfaces:**
- Consumes: existing patterns.
- Produces: consistent loading/empty states, polling teardown, status badges,
  basic accessibility (labels, keyboard for selector).

- [ ] Step 1: failing tests: every list surface has an explicit empty state; the
  selector is keyboard-navigable and labelled; polling stops on unmount
- [ ] Step 2: RED
- [ ] Step 3: implement
- [ ] Step 4: PASS
- [ ] Step 5: `npm test -- --run`; `npm run build`
- [ ] Step 6: commit `feat(frontend): harden ux states and polling`

### F6.4 Final primary navigation state

**Files:**
- Modify: `frontend/src/app/MainLayout.tsx` (if needed)
- Test: `frontend/src/app/navigation.test.tsx`

**Interfaces:**
- Produces: primary nav exactly `Recordings | Experiments | Algorithm Lab`.

- [ ] Step 1: failing test asserting the exact three-item nav and absence of
  Settings and Compare
- [ ] Step 2: RED (if not already satisfied) / confirm GREEN
- [ ] Step 3: implement only if needed
- [ ] Step 4: PASS
- [ ] Step 5: `npm test -- --run`; `npm run build`
- [ ] Step 6: commit `test(frontend): assert final primary navigation`

**Review checkpoint: after F6.** If the backend is not frozen, STOP here; do not
execute F7/F8.

---

## Part VIII — F7 API Freeze Reconciliation

> **DO NOT EXECUTE BEFORE BACKEND V1 API FREEZE.**

### F7.1 Reconcile YELLOW contract drift

**Files:**
- Modify: `frontend/src/api/client.ts`, `frontend/src/api/types.ts`,
  `frontend/src/api/v1Contract.ts`
- Test: `frontend/src/api/client.test.ts`

**Interfaces:**
- Consumes: final frozen Backend V1 API.
- Produces: contract-accurate types/mappers; no redesign.

- [ ] Step 1: compare frontend types/mappers against the frozen API for:
  `executor-selection` response; candidate matrix; reason fields; provenance;
  `DatasetExperiment` counters; benchmark compare optional fields;
  `aggregate_metrics_json` sub-shape
- [ ] Step 2: write failing mapping tests for each drift found; RED
- [ ] Step 3: fix mappers/types only; PRESERVE all existing UI behavior and the
  Auto/fallback/N-A invariants
- [ ] Step 4: PASS
- [ ] Step 5: `npm test -- --run`; `npm run build`
- [ ] Step 6: commit `chore(frontend): reconcile v1 contracts with frozen api`

### F7.2 Conditional capabilities

**Files:**
- Modify: `frontend/src/api/types.ts`, `frontend/src/api/client.ts`, and the
  consuming feature only if the condition is true
- Test: affected feature tests

**Interfaces:**
- Consumes: final frozen API.
- Produces: conditional upgrades, each independently committed.

- [ ] Step 1: IF final `AnalysisRunRead` exposes resolved `model_release_id`:
  add read-only resolved-release provenance to `RunProvenanceCard` (test:
  renders resolved release; otherwise keep absent)
- [ ] Step 2: IF a dataset catalog endpoint exists: upgrade the exact dataset
  triple input into an authoritative selector (test: selector uses the catalog;
  otherwise retain explicit exact identity fields)
- [ ] Step 3: IF `PipelineDefinitionRead` exposes `parameter_schema`:
  optionally add schema-driven controls (test: generic `{}` remains the fallback;
  no plugin-id branches)
- [ ] Step 4: run `npm test -- --run`; `npm run build`
- [ ] Step 5: commit each implemented condition separately under its own
  descriptive `feat(frontend):` message; if a condition is false, record it in
  the task report and commit nothing for it

**Review checkpoint: after F7.**

---

## Part IX — F8 Frontend V1 Acceptance

> **DO NOT EXECUTE BEFORE F7.**

### F8.1 Workflow acceptance

**Files:**
- Test: workflow acceptance tests (page/feature level)

**Interfaces:**
- Consumes: the completed implementation.
- Produces: acceptance evidence for Workflows A/B/C and Algorithm Lab.

- [ ] Step 1: Workflow A: Recording → environment (Auto/Manual) → AnalysisRun →
  status → detections → overlays → detail → provenance
- [ ] Step 2: Workflow B: frozen dataset triple → model → dataset-scoped
  environment → DatasetExperiment → items/attempts → evaluation (coverage +
  metrics + N/A)
- [ ] Step 3: Workflow C: Experiment A vs B → comparable + reasons + deltas →
  shared-recording drilldown → Algorithm Lab
- [ ] Step 4: Algorithm Lab: per-recording A/B with GT + overlays + per-case
  states
- [ ] Step 5: run `cd frontend; npm test -- --run`; expect all pass
- [ ] Step 6: run `cd frontend; npm run build`; expect exit 0
- [ ] Step 7: commit acceptance tests only if new tests were added
  (`test(frontend): add frontend v1 workflow acceptance`)

### F8.2 Acceptance invariants (behavioral) + explicit source audit

**Files:**
- Modify/Test: reuse and extend behavioral tests already created in F1/F2/F4/F6
  (no artificial source-string unit test is created)

**Interfaces:**
- Produces: behavioral acceptance evidence for the design invariants, plus a
  manually-reviewed source audit.

No `frontend/src/app/guardrails.test.ts` source-string scanner is planned. The
invariants are proven behaviorally; textual checks are performed as explicit
review commands, not fake runtime unit tests.

Behavioral acceptance tests (extend the existing feature/page tests):

- [ ] Step 1: prove no plugin-id authority — feed the same backend capability
  facts with two different arbitrary pipeline ids and assert identical executor UI
  behavior (extend `ExecutionEnvironmentSelector.test.tsx`)
- [ ] Step 2: prove no client fallback — given an unavailable executor the UI
  disables/substitutes nothing and never returns another executor
  (extend `ExecutionEnvironmentSelector.test.tsx` / `SpectrumAnalysisPage.test.tsx`)
- [ ] Step 3: prove N/A semantics — a null metric renders `N/A` and
  `aggregateMetrics.classificationApplicable === false` renders `N/A` + reason,
  never `0` (extend `EvaluationMetricsView.test.tsx` / `CompareDeltaTable.test.tsx`)
- [ ] Step 4: prove no operator internals rendered — a response carrying
  `environment_ref` / absolute interpreter path fields renders none of them
  (extend the relevant provenance/detail tests)
- [ ] Step 5: prove no hardcoded ModelRelease choices — the models/release UI
  offers no literal `golden` choice (extend the create-form tests)
- [ ] Step 6: prove primary navigation is exactly
  `Recordings | Experiments | Algorithm Lab` (extend `navigation.test.tsx`)
- [ ] Step 7: run `cd frontend; npm test -- --run`; expect all pass
- [ ] Step 8: run `cd frontend; npm run build`; expect exit 0
- [ ] Step 9: commit only if new behavioral tests were added
  (`test(frontend): add frontend v1 acceptance invariants`)

Explicit final source audit (acceptance evidence, NOT automated unit tests):

- [ ] Step 10: run bounded review commands (e.g. `git grep` for suspicious
  patterns) and SEMANTICALLY review every match; a grep match is evidence to
  inspect, never an automatic failure. Suggested checks:
  ```text
  git grep -n "executorPolicy" frontend/src
  git grep -n "executor-availability" frontend/src/features/dataset-experiment frontend/src/pages/ExperimentsListPage.tsx
  git grep -n "golden" frontend/src
  git grep -n "environment_ref" frontend/src
  ```
- [ ] Step 11: record the audit outcome (matches + semantic verdict) in the F8
  task report; fix any real violation without changing the approved design

**Review checkpoint: at F8.**

---

## Review Checkpoints (explicit stop gates)

```text
after F0   — contract foundation review
after F1   — execution environment review
after F2   — single recording review
after F3   — dataset experiments review
after F4/F5 — evaluation/compare + algorithm lab review
after F6   — hardening review; STOP if backend not frozen
after F7   — reconciliation review
at F8      — acceptance review
```

If the backend is not frozen after F6, STOP at F6 and do not execute F7/F8.

---

## Commit Strategy

- One commit per independently reviewable task (the Step 6 commit in each task).
- Do NOT plan a single F3 commit or a single Frontend V1 commit.
- Commit prefixes: `feat(frontend):`, `fix(frontend):`, `refactor(frontend):`,
  `test(frontend):`, `chore(frontend):`.

---

## Implementation Branch (future)

Recommended future implementation branch: `feature/frontend-v1`, created from the
independently approved PLAN HEAD. Do NOT create it in this round.

---

## TDD / Test Strategy

- Every production change starts with a failing test (RED) and ends with a
  focused GREEN run plus the surrounding suite.
- **Command convention (normative):** a task's "Step 2: RED" / focused run means
  `cd frontend; npm test -- --run <the task's Test file(s)>`; the surrounding
  regression is `cd frontend; npm test -- --run` (all tests).
- **Type/build authority:** `cd frontend; npm run build` (`tsc -b && vite build`).
  Vitest is not a typechecker. Do not invent a separate typecheck command and do
  not use `npx` (no opportunistic downloads).
- API contract/mapping tests live in `api/client.test.ts`; the F0.2 type fixture
  `api/v1Contract.ts` is compiled by `npm run build` (not a runtime Vitest test).
- Selector state-matrix tests live in `executionEnvironment.test.ts` and
  `ExecutionEnvironmentSelector.test.tsx`.
- Workflow tests are page/feature level.
- Tests stub `fetch` via `vi.stubGlobal` (existing convention); no MSW.
- `npm run build` is a gate after F0.2, F1.3, F1.4, F3.x, F4.x, F6.x, F7.x, and
  throughout F8.

---

## Plan Self-Review

### Spec coverage

| Design requirement | Task(s) |
|---|---|
| F0 baseline + compile-verified types | F0.1, F0.2 |
| ExecutionEnvironmentSelector (fixed vocabulary, backend state) | F1.2 |
| executor-selection client + scope guardrails (discriminated scope) | F1.1 |
| manual/auto request semantics | F1.3 |
| retire client executor policy | F1.4 |
| Workflow A (run/status/detections/provenance) | F2.1–F2.4 |
| ModelRelease: no selector, no inference; experiment provenance | F2.1, F3.1, F3.6, F7.2 |
| Parameters = {} | F1.3, F3.4 |
| Workflow B (experiments) | F3.1–F3.9 |
| Dataset exact triple; no hardcoded SpaceNet | F3.4, F7.2 |
| DatasetExperiment MUST NOT call executor-availability | F3.4 |
| DatasetExperiment frozen `executor` + `recording_manifest_hash` | F3.1, F3.6 |
| Workflow C (evaluation + compare + drilldown) | F4.1–F4.3 |
| Evaluation N/A semantics (from `aggregateMetrics`) | F4.1, F4.3 |
| Algorithm Lab role + dedup + drilldown | F5.1–F5.3 |
| F6 status/error + nav | F6.1–F6.4 |
| F7 reconciliation (blocked) | F7.1, F7.2 |
| F8 acceptance (blocked, behavioral) | F8.1, F8.2 |
| Stack constraints / no new deps | Global Constraints; enforced across all tasks |
| Operator boundary | F1.2, F2.1, F8.2 |

### Placeholder scan

Searched this plan for `TBD`, `TODO`, `implement later`, `appropriate`,
`similar to`, `etc.` — no implementation placeholders remain. Every task names
exact files, interfaces, and verification commands.

### Type/interface consistency

`ExecutionCandidate`, `ExecutorSelection`, `ExecutionSelectionScope`,
`ExecutionEnvironmentValue`, `ExecutionEnvironmentSelectorProps`,
`AnalysisRunCreateRequest`, `DatasetExperimentCreateRequest`,
`DatasetExperiment`, `DatasetExperimentItem`,
`DatasetExperimentAttempt` (mandatory `analysisRunId: string`), and the
evaluation/compare types use one definition each (Interface Ledger). Public/domain
request types are camelCase; snake_case exists only in the private wire bodies
inside `api/client.ts`. `DatasetExperiment.executor` is a required concrete
string; `requestedExecutionMode` is `ExecutionMode | null`. No drift between
F1/F2/F3/F4 tasks.

### Backend contract accuracy

This plan does NOT assume any of the following (per the corrected spec):

```text
executors_supported is the complete option universe     — NOT assumed (F1.2 enumerates fixed options)
DatasetExperiment calls executor-availability           — NOT done (F3.4 dataset scope only)
generic dataset catalog exists                          — NOT assumed (F3.4 exact triple)
AnalysisRunRead exposes model_release_id                — NOT assumed (F2.1/F7.2 conditional)
PipelineDefinitionRead exposes parameter_schema         — NOT assumed (F1.3/F3.4 parameters={}; F7.2 conditional)
DatasetEvaluation top-level classificationApplicable    — NOT assumed (F4.1 reads aggregateMetrics.*)
Attempt.analysisRunId optional                         — NOT assumed (required string)
launch ambiguity from a missing run id                  — NOT assumed (F3.8 uses backend error only)
```

### Design invariants

Confirmed: Auto default; backend-only executor authority; no fallback; Compare
under Experiments; Settings not primary nav; N/A + reason; F0–F6 start now;
F7/F8 blocked until freeze.

### Contract corrections applied (second review)

1. `DatasetExperiment` now includes the concrete `executor` and
   `recordingManifestHash`; nullable fields kept nullable
   (`requestedExecutionMode: ExecutionMode | null`); `runtime_descriptor_json` is
   not surfaced.
2. `DatasetExperimentAttempt.analysisRunId` is a required `string`.
3. Create request types are camelCase; snake_case lives only in private wire
   bodies in `api/client.ts` with explicit translation in
   `createAnalysisRun`/`createDatasetExperiment`.
4. F0.2 uses a compile-time RED/GREEN via `npm run build`, not a Vitest assertion
   of erased types.
5. F0.3 removed; execution-selection scope/query/mapping/error behavior is owned
   entirely by F1.1 (no test for a not-yet-created function).
6. F4.1 reads classification applicability from
   `aggregateMetrics.classificationApplicable` / `.classificationReason`.
7. F3.8 never infers launch ambiguity from a missing run id; ambiguity comes only
   from authoritative backend error state.
8. F8.2 uses behavioral acceptance tests plus an explicitly reviewed source audit;
   no source-string guardrail unit test (`guardrails.test.ts` removed).
9. The F4/F5 review checkpoint is now after F5.3.
10. YAGNI: no unused `AnalysisRunForm.tsx`; `buildAnalysisRunRequest` lives in the
    pure module `features/analysis-run/requestBuilder.ts`.
11. Command convention: focused runs are `cd frontend; npm test -- --run <file>`;
    `npm run build` is the Type/build authority; no `npx`.

### Task count (updated)

F0 = 2 tasks (F0.1, F0.2); F1 = 4; F2 = 4; F3 = 9; F4 = 3; F5 = 3; F6 = 4;
F7 = 2 (blocked); F8 = 2 (blocked). Total = 33 task headings.

---

## Changed Files (this corrective round)

```text
docs/superpowers/plans/2026-09-15-frontend-v1-implementation.md   corrected
```

The approved design spec `docs/superpowers/specs/2026-09-15-frontend-v1-design.md`
is NOT modified by this round. No frontend source/test change, no backend change,
no dependency change.
