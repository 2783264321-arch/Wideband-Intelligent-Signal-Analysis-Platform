# Frontend V1 Design Specification

Backend/product reference commit: `d4b22ee4f01914974aaf47c1a88e4f8afa461913`
Backend branch: `feature/backend-v1-plan-b-local-gpu`
Design branch: `feature/frontend-v1-design`
Status: **FINAL APPROVED — 2026-09-15**
Independent review checkpoint: `3f02c29`

---

## Status / Scope

This document is the authoritative Frontend V1 design. It was produced from the
completed and human-approved Frontend V1 audit against the backend at `d4b22ee`.

**In scope (this document only):** design decisions, information architecture,
workflow contracts, API/type boundary, feature decomposition, routing design,
state strategy, dependency-stability classification, milestone decomposition,
testing strategy, non-goals, and the acceptance definition.

**Out of scope for this round:**

- Any frontend production source change.
- Any backend production source change.
- The F0–F8 implementation plan (a separate later artifact).
- Any new backend domain entity.
- Any operator/qualification UI.

**Status labels used below:**

```text
GREEN   safe to implement now; backend contract proven at d4b22ee
YELLOW  may change; must stay isolated behind the API/type boundary
RED     do not implement in ordinary frontend V1; wait for Plan C/D or freeze
```

---

## Executive Goal

Make the existing WISA frontend express the three approved Backend V1 product
workflows — **Single Recording**, **Dataset → One Model**, and **Same Dataset →
Different Models** — using backend first as the sole authority for execution
decisions, while preserving the existing page skeleton and reusable components.

The frontend strategy is **preserve → extract → extend**. The frontend is **not**
rewritten, and no dependency modernization is performed in V1.

Two core defects identified by the audit drive this design:

1. **Client-side execution authority.** `frontend/src/features/spectrum/executorPolicy.ts`
   currently decides executor selection in the browser from `executorsSupported`
   and `recommendedExecutor`, including a client-side "remote unavailable → fall
   back to `local_cpu`" rule. It cannot represent `local_gpu` or `auto`, and the
   client fallback contradicts the approved frozen-executor / Auto-vs-fallback
   boundary (spec `## Auto vs Fallback Boundary`). This authority must be retired.
2. **Missing DatasetExperiment workflow.** Backend V1 ships the complete
   `DatasetExperiment` orchestrator (`/api/dataset-experiments`), but the frontend
   has no client, type, or page for it. Only the lower-level evaluation engine
   (`/api/dataset-benchmarks`, `DatasetEvaluation`) is surfaced today.

---

## Current Accepted Baseline

Evidence from the completed audit (frontend identical between the primary
checkout and `d4b22ee`; `git diff --stat 640b358 d4b22ee -- frontend` was empty):

```text
Frontend tests:   14 test files, 70 tests passed, 0 failed   (`npm test -- --run`)
Frontend build:   PASS                                       (`npm run build` = tsc -b && vite build)
```

Current stack (to be preserved):

```text
React
TypeScript
Vite
react-router-dom
Ant Design (@ant-design/icons)
Vitest
@testing-library/react + jest-dom
jsdom
```

Explicitly absent and **not required** for V1:

```text
React Query / data-fetching library
Redux / Zustand / global state library
Mock Service Worker (MSW)
additional router
additional UI library
```

Current source layout (to be preserved):

```text
frontend/src/api/        client.ts, types.ts, client.test.ts
frontend/src/app/        App.tsx, MainLayout.tsx, App.test.tsx
frontend/src/features/   spectrum, signals, signal-detail, algorithm-lab, dataset-benchmarks, imports
frontend/src/pages/      RecordingsPage, SpectrumAnalysisPage, SignalsPage, SignalDetailPage, AlgorithmLabPage
frontend/src/mocks/      demo.ts (dead/unused)
```

Known baseline weaknesses carried into this design (not bugs to fix here):

- `frontend/src/mocks/demo.ts` is imported nowhere (dead fixtures).
- Tests stub the global `fetch` via `vi.stubGlobal`; there is no MSW and none is
  introduced in V1.
- `client.ts` and `types.ts` are hand-written; wire types (`*Wire`) and domain
  types are mapped manually in `client.ts`.

---

## Design Principles

```text
1. Preserve → extract → extend. No rewrite.
2. Backend is the sole execution authority. The frontend renders backend facts;
   it does not compute an independent capability matrix or fall back by itself.
3. Capability-driven rendering. All enable/disable/status state derives from
   backend projections (pipeline projection, executor availability, executor
   selection), never from plugin-id branches.
4. `manual` and `auto` are execution modes; `auto` is never an executor and is
   never persisted.
5. Centralized API client and centralized TypeScript contracts. Pages never call
   `fetch` directly.
6. Bounded backend reason/error codes are always preserved for diagnostics, even
   when a human-readable message is shown.
7. Null / not-applicable is never rendered as a fake numeric zero.
8. Product/operator boundary: ordinary frontend never exposes operator or
   qualification internals.
9. Additive-field tolerance: unknown/optional response fields never break the UI.
10. No plugin-specific hardcoding of execution capability:
    no `if (pipeline.id === …)`, no hardcoded release ids in choices.
11. No new global state architecture; local component state + centralized client.
12. No dependency modernization or version churn in V1.
```

---

## Product / Operator Boundary

The ordinary Frontend V1 MUST NOT expose, display, request, or link to:

```text
private Python interpreter paths
absolute asset paths
SSH keys / known_hosts content
RemoteProfile internals / raw remote editor
certificate JSON internals
runtime qualification UI or evidence
cgroup memory telemetry
PSI
/proc worker telemetry
nvidia-smi diagnostics
Plan B / Plan C / Plan D evidence machinery
```

Boundary rules:

- Only boolean/opaque executor facts may cross into the UI
  (`technical`, `configured`, `certified`, `available`) plus bounded
  `reason_code` / `reason` / `reason_message` strings.
- `environment_ref` (private interpreter path) and certificate fields beyond the
  booleans are never rendered.
- `Settings` is **removed from primary navigation** and MUST NOT become an SSH /
  certificate / runtime / GPU-qualification console.
- A future lightweight **read-only** environment/health indicator may be placed
  outside primary navigation (e.g. a header status popover). That is a later UX
  hardening item (F6), not a configuration surface, and is not designed here.

Rationale: `d4b22ee`'s spec `## API / Frontend Executor Contract` explicitly says
the production API must not return private interpreter/asset paths, SSH material,
`environment_ref`, certificate internals, or raw telemetry, and the UI has no
justification to recreate them.

---

## Frontend Information Architecture

Approved conceptual IA (authoritative):

```text
WISA
│
├── Recordings
│    │
│    ├── Recording Library
│    │
│    └── Spectrum Workbench
│          │
│          ├── Pipeline
│          ├── Execution Environment
│          ├── Run Analysis
│          ├── Detection Overlay
│          └── Signals / Signal Detail
│
├── Experiments
│    │
│    ├── Experiments
│    │     ├── Create
│    │     ├── Progress
│    │     ├── Items
│    │     ├── Attempts
│    │     └── Evaluation
│    │
│    └── Compare
│          ├── Experiment A
│          ├── Experiment B
│          ├── Aggregate Metrics
│          └── Open Recording in Algorithm Lab
│
└── Algorithm Lab
     │
     ├── Recording
     ├── Run A / Run B
     ├── GT
     ├── TF overlays
     └── Per-case comparison
```

Product mental model:

```text
Recordings     → single-signal analysis
Experiments    → dataset/model experiments and aggregate comparison
Algorithm Lab  → single-recording deep A/B analysis
```

`Compare` is not an independent data source; it is derived from two Experiments,
so it lives **inside** `Experiments` and is never promoted to primary navigation.

---

## Navigation Model

Approved primary navigation (exactly three destinations):

```text
Recordings
Experiments
Algorithm Lab
```

Explicitly NOT primary navigation:

```text
Compare      → belongs inside Experiments
Settings     → removed from primary navigation in V1
```

Navigation rules:

- Selecting `Recordings` lands on the Recording Library.
- Selecting `Experiments` lands on the Experiments list; `Compare` is reachable
  from within Experiments (tab or sub-route), not from the sidebar.
- Selecting `Algorithm Lab` lands on the per-recording A/B workspace.
- The existing `/settings` **route may remain** (implementation policy decides
  whether to keep, hide, or redirect it); it must not appear in primary
  navigation and must not become an operator console.
- The sidebar must not imply configurability that does not exist.

---

## Workflow A — Single Recording

```text
Recording
↓
Spectrum
↓
Pipeline / Plugin
↓
Execution Environment
↓
AnalysisRun
↓
status
↓
detections
↓
spectrum overlays
↓
signal/detail inspection
↓
execution provenance
```

Reused surfaces (preserve):

```text
pages/RecordingsPage            discovery / ingest / open spectrum
pages/SpectrumAnalysisPage      single-run workbench (to be refactored to consume
                                ExecutionEnvironmentSelector instead of executorPolicy)
pages/SignalsPage               detection table for a run
pages/SignalDetailPage          single detection + FFT/waveform/GT
features/spectrum/SpectrogramViewer
features/signals/SignalResultsPanel
features/signal-detail/LineSeriesChart
```

Contract:

- **Execution Environment** is chosen via the shared `ExecutionEnvironmentSelector`
  (see "Execution Environment UX"); default mode is `auto` for ordinary users.
- **Create run** uses `POST /api/analysis-runs`:
  ```text
  { recording_id, pipeline_id,
    executor?: "local_cpu"|"local_gpu"|"remote_gpu",
    execution_mode?: "manual"|"auto" (default "manual"),
    model_release_id?, parameters }
  ```
  - `manual`: send the exact chosen `executor`; do NOT send `execution_mode="auto"`.
  - `auto`: send `execution_mode="auto"` with NO `executor` (backend rejects
    `auto` + explicit executor with `EXECUTION_REQUEST_INVALID`).
- The **persisted** `AnalysisRun.executor` is always one concrete executor; the
  UI never displays or sends `executor = "auto"`.
- **Provenance rendering**: surface the resolved executor, and when present,
  `requested_execution_mode`, `auto_reason_code`, `auto_reason`,
  `workload_class` from execution metadata; also render existing remote/hardware
  summary facts (profile, device name, runtime commit — opaque allowed facts only).
- **Status lifecycle**: `pending → running → completed | failed | interrupted`
  (poll while active; stop polling on terminal status).
- **No client fallback**: if the backend reports the Auto resolution is not
  runnable, the UI shows Auto as unavailable/unresolved and lets the user inspect
  Manual options; it never silently switches executor.

Explicitly removed from this workflow: the client-side decision logic in
`features/spectrum/executorPolicy.ts` (see F1).

---

## Workflow B — Dataset Experiment

Frontend currently lacks this entirely; it is the biggest V1 addition.

```text
Frozen Dataset Membership
↓
Plugin / Model
↓
Execution Environment
↓
DatasetExperiment
↓
Items / Attempts / AnalysisRuns
↓
Evaluation
↓
coverage + metrics + provenance
```

Contract:

- **Create** uses `POST /api/dataset-experiments`:
  ```text
  { name,
    dataset_name, dataset_split, dataset_label_space,
    plugin_id, plugin_version,
    model_release_id?,
    executor?, execution_mode?: "manual"|"auto" (default "manual"),
    parameters, evaluation_protocol, max_concurrency }
  ```
  Frozen identity fields (`recording_manifest_hash`, `asset_manifest_sha256`,
  `runtime_descriptor_json`) are backend-resolved and MUST NOT be sent.
- **Run** uses `POST /api/dataset-experiments/{id}/run` (202).
- **Retry** uses `POST /api/dataset-experiments/{id}/retry-failed` (202) and
  `POST /api/dataset-experiments/{id}/retry-evaluation` (202).
- **Read back**: `GET /api/dataset-experiments`, `/{id}`, `/{id}/items`,
  `/{id}/items/{item_id}/attempts`.
- **Experiment status** rendered as a badge:
  `pending → running → evaluating → completed | completed_with_failures | failed`.
- **Item status**: `queued | running | completed | failed`.
- **Attempts**: shown per failed/selected item (attempt number, linked run id,
  launch/created timestamps) so operators and users can see retries.
- **Counters**: render `expected_items`, `queued_items`, `running_items`,
  `completed_items`, `failed_items`, `attempt_count` (backend-derived counters;
  treat as YELLOW — display only, never recompute as authority).
- **Linked evaluation**: the experiment links a `DatasetEvaluation` via
  `dataset_evaluation_id`; the evaluation surface (F4) renders coverage/metrics.
- **Auto freeze**: the environment is resolved once at creation and frozen for the
  whole experiment; the UI does not re-resolve per item.

Experiment create form composition:

```text
name
dataset identity        → exact triple (see Dataset Identity, current contract)
plugin + version        → from /api/pipelines projection
ModelRelease            → backend-projected on the experiment; no selector (see ModelRelease Policy)
Execution Environment   → ExecutionEnvironmentSelector (default Auto; dataset-scoped selection)
parameters              → opaque request object; generic V1 sends {} (see Parameter Contract)
max_concurrency         → bounded numeric input (backend validates)
```

Dataset identity (current contract):

```text
dataset_name
dataset_split
dataset_label_space
```

No generic dataset catalog/list endpoint exists at the reference commit
(`/api/datasets` exposes SpaceNet **registration** only; there is no
`GET /api/datasets` or `GET /api/datasets/catalog`). These three fields are
supplied explicitly or prefilled from an already-known backend object/context that
exposes them. The frontend MUST NOT hardcode `SpaceNet` / `test` / `spacenet_14`
as universal product choices. An authoritative dataset catalog is a future YELLOW
dependency reconciled at F7.

Parameter contract (current):

```text
`parameters` is an opaque request dictionary supported by the API.

Until the backend exposes an authoritative parameter schema, generic Frontend V1
MUST NOT invent plugin-specific parameter forms or schemas.

F1–F6 generic AnalysisRun / DatasetExperiment creation sends the
backend/default-compatible empty object {} (or preserves an already-existing
control that is backed by an explicit current backend contract).

Do NOT hardcode per-plugin parameter controls from plugin ids.
A future authoritative pipeline parameter schema is a YELLOW dependency
reconciled at F7.
```

---

## Workflow C — Experiment Comparison

```text
Experiment A
+
Experiment B
↓
DatasetBenchmark comparison
↓
aggregate deltas
↓
per-recording drilldown
↓
Algorithm Lab
```

Contract:

- **Aggregate compare** uses `POST /api/dataset-benchmarks/compare` with
  `{ evaluation_a_id, evaluation_b_id }` → response:
  ```text
  { comparable, reasons[], evaluation_a_id, evaluation_b_id,
    aggregate_a, aggregate_b,
    deltas { localization_ap50, localization_ap50_95,
             class_aware_map50, class_aware_map50_95,
             matched_accuracy } }
  ```
- **Comparability**: render `comparable` and, when false, the bounded
  `reasons[]` (e.g. `dataset_name_mismatch`, `dataset_split_mismatch`,
  `label_space_mismatch`, `recording_manifest_hash_mismatch`,
  `evaluation_protocol_mismatch`, `protocol_config_mismatch`,
  `evaluation_a_not_completed`, `evaluation_a_incomplete`, and the B equivalents).
- **Deltas**: null deltas are `N/A`, never zero. Localization comparison is always
  valid for detection models; classification deltas may be `N/A`/null when a model
  is classification-inapplicable (e.g. CPN vs ZoomSpec).
- **Per-recording drilldown**: use `GET /api/dataset-benchmarks/{eval}/items` for
  both evaluations to pick a shared recording, then open
  `POST /api/algorithm-lab/compare` via the Algorithm Lab route.
- **No new domain entity**: comparison is a read model over two existing
  experiments/evaluations. No `MultiModelExperiment`.
- Compare lives inside `Experiments`; it is not a primary nav destination.

---

## Algorithm Lab Role

Algorithm Lab is the **per-recording deep comparison** workspace, not a
dataset-level comparator.

```text
/algorithm-lab
  ├── Recording
  ├── Run A / Run B (completed runs for that recording)
  ├── Ground truth
  ├── Time-frequency overlays (spectrum)
  └── Per-case comparison (IoU matching, comparison states)
```

Contract:

- `POST /api/algorithm-lab/compare`
  `{ recording_id, run_a_id, run_b_id, iou_threshold: 0.5 }` (V1 supports only 0.5).
- Comparison states: `both_detected | a_only | b_only | both_missed`.
- Class-correctness may be `null` (show `N/A`), never coerced to false/zero.
- Reuse existing components:
  `features/algorithm-lab/CaseAnalysisView`, `CaseComparisonTable`,
  `RunComparisonPanel`, `RunMetricsCard`, plus `SpectrogramViewer`.
- Dataset aggregate comparison remains in `Experiments`/`Compare`; Algorithm Lab
  is reached from there via a cross-link containing `recording`, `runA`, `runB`.
- De-duplicate the two compare code paths currently in `CaseAnalysisView`
  (`runCompare` and the auto-effect) without changing behavior.

---

## Execution Environment UX

One reusable component: **`ExecutionEnvironmentSelector`** (feature
`features/execution-environment/`), used by both Single AnalysisRun and
DatasetExperiment.

### Stable product vocabulary (option enumeration)

The four options are a fixed product vocabulary, NOT a frontend capability matrix:

```text
Auto
Local CPU     (local_cpu)
Local GPU     (local_gpu)
Remote GPU    (remote_gpu)
```

`executors_supported` is a deployment-qualified **backend projection** and is a
useful hint, but it is **NOT the complete option universe**: it may omit executors
that are technically unsupported, not configured, or not certified. The selector
therefore MUST NOT hide a Manual option solely because it is absent from
`executors_supported`. All four options are always enumerable; each option's
usability comes exclusively from backend facts.

### Backend facts (source of option state)

```text
pipeline.technicalExecutionCapabilities
pipeline.executorsSupported         (hint only; never the sole enumeration source)
pipeline.recommendedExecution
GET /api/executor-selection         (Auto explanation + candidate matrix)
GET /api/executor-availability      (per exact executor; RECORDING-scoped only)
```

Per-option facts come from the backend booleans; the UI does not invent a matrix:

```text
technical    plugin declares the capability
configured   provider is registered in this deployment
certified    exact certificate exists for this release/runtime
available    live probe succeeded for this input
reason_code / reason_message   bounded, human-readable, no secrets
```

### Scope-specific contracts (do not mix)

`GET /api/executor-availability` is **recording-scoped** and requires
`recording_id` + `pipeline_id` + `executor`; it has no dataset scope. Never call it
for a DatasetExperiment.

```text
Single Recording
  /executor-selection      { recording_id, pipeline_id, model_release_id? }
  /executor-availability   { recording_id, pipeline_id, executor }   (optional exact probe)

DatasetExperiment
  /executor-selection      { dataset_name, dataset_split, dataset_label_space,
                             pipeline_id, model_release_id? }
  NO /executor-availability call for dataset scope
```

For DatasetExperiment, the `/executor-selection` candidate matrix supplies the
`technical / configured / certified / available / reason` facts used both for the
Auto explanation and for Manual option enable/disable preview. The authoritative
final validation remains `POST /api/dataset-experiments` (Auto request or exact
Manual executor).

### Derived UI states (mapping backend facts, not client authority)

```text
available                technical && configured && certified && available
not configured           technical && !configured
not certified            technical && configured && !certified
unsupported              !technical
temporarily unavailable  configured/certified but probe failed
recommended              backend recommended flag / resolved executor
selected                 user choice or Auto resolution
```

### Rules

- Default selection is **Auto** when the backend returns a valid Auto resolution.
- If Auto is unresolved (`AUTO_NO_RUNNABLE_EXECUTOR`), Auto is rendered
  **unavailable/unresolved**; the user may switch to a Manual option that is
  itself enabled/disabled from backend facts. The frontend never substitutes.
- Manual options are enabled only when the backend reports them usable for this
  input; disabled options show the bounded reason.
- The selector must be usable without a live GPU (Auto simply resolves to a
  runnable CPU option, or reports no-runnable).
- The selector never exposes interpreter/asset paths, SSH material, certificate
  internals, or telemetry.

---

## Manual / Auto Contract

```text
execution_mode = auto
        ↓  (backend resolver; never the browser)
resolved executor = local_cpu | local_gpu | remote_gpu
```

- `auto` and `manual` are **request/audit concepts**; the authoritative and
  persisted identity is always the resolved executor.
- A persisted `AnalysisRun` / `DatasetExperiment` **never** has
  `executor = "auto"`.
- Manual semantics: exact executor by name; backend fails closed with the exact
  bounded reason (`EXECUTION_CAPABILITY_UNAVAILABLE`, `EXECUTION_NOT_CERTIFIED`,
  `INPUT_INCOMPATIBLE`, remote probe reason). No substitution.
- Auto semantics: resolved once before persistence (single run) or once at
  experiment creation and frozen for the whole experiment. No client fallback, no
  per-item re-resolution.
- Provenance: the frontend displays the resolved executor and, where available,
  `requested_execution_mode`, `auto_reason_code`, `auto_reason`, `workload_class`.
- Client-side Selection Boundary (design rule):
  ```text
  ALLOWED    present backend facts and the user's explicit choice
  FORBIDDEN  any logic of the form "if unavailable then use another executor"
  FORBIDDEN  any plugin-id / pipeline-id based executor decision
  ```

Auto explanation UX (progressive disclosure):

```text
Collapsed (ordinary user):
  Auto — Recommended: <resolved executor label>

Expanded (on demand):
  resolved_executor
  reason
  reason_code
  workload_class
  candidate matrix:
    Local CPU     configured / certified / available (+ reason_message)
    Local GPU     configured / certified / available (+ reason_message)
    Remote GPU    configured / certified / available (+ reason_message)
```

Adoption note: progressive disclosure is adopted because the backend response is
small (one `workload_class`, one `reason_code`, ≤3 candidates) and the spec
requires explainability; the collapsed line keeps ordinary users unburdened while
the expanded panel preserves the bounded codes for diagnostics.

---

## ModelRelease Policy

Approved V1 policy:

- The frontend does **not** require a ModelRelease picker in V1.
- The backend resolves/defaults the ModelRelease. The API layer MUST support the
  optional `model_release_id` request field for `POST /api/analysis-runs` and
  `POST /api/dataset-experiments` (pass-through), but ordinary V1 UI MUST NOT
  present a hardcoded list of release ids and MUST NOT infer the resolved release
  from request values, plugin knowledge, defaults, or `golden`.
- **Resolved-release provenance is displayed only where the backend read model
  actually projects it.** The contract differs by resource at the reference commit:

  ```text
  DatasetExperimentRead   projects model_release_id + asset_manifest_sha256
                          → experiment provenance renders the backend-projected
                            release identity NOW

  AnalysisRunRead         does NOT currently project model_release_id
                          → single-run resolved-release provenance is a YELLOW
                            backend-contract gap; do NOT invent it
  ```
- Single AnalysisRun V1 contract:
  ```text
  - API client supports optional model_release_id request pass-through.
  - Ordinary UI has no hardcoded release selector.
  - If no authoritative release choice surface exists, send no explicit release.
  - Do NOT display resolved ModelRelease provenance unless the read model exposes
    the resolved identity.
  ```
- A user-facing selector is allowed **only** when the backend exposes a stable,
  authoritative list of selectable releases for a plugin/version (a future
  additive contract). Until then, no selector and no free-text entry.
- Never hardcode values such as `golden` into the UI as choices.

---

## Status / Error Model

The frontend may translate statuses into readable UX but must **preserve bounded
backend codes**.

Central rule:

```text
PlatformApiError { status, code, message, details }
  → human-readable text
  + bounded code retained for diagnostics
```

The existing `PlatformApiError.display` (`code: message`) is the standard surface;
all pages should converge on it (today only the dataset-benchmarks features use it).

Status models to render:

```text
AnalysisRun:        pending | running | completed | failed | interrupted
DatasetExperiment:  pending | running | evaluating | completed
                    | completed_with_failures | failed
DatasetExperimentItem: queued | running | completed | failed
DatasetEvaluation:  pending | running | completed | failed | interrupted
```

Bounded codes/classes that must remain distinguishable in the UI:

```text
unsupported                 EXECUTION_CAPABILITY_UNAVAILABLE (technical/registered)
not configured              configured = false
not certified               EXECUTION_NOT_CERTIFIED
temporarily unavailable     probe reason (bounded reason_code/reason_message)
input incompatible          INPUT_INCOMPATIBLE
run failed                  bounded run failure error_type
run interrupted             ANALYSIS_INTERRUPTED / interrupted status
launch ambiguous            pending + launch_requested_at set, no worker (G7)
evaluation failed           bounded evaluation error_type (e.g. BENCHMARK_FAILED)
auto no runnable            AUTO_NO_RUNNABLE_EXECUTOR
```

Rules:

- Do not collapse failures into a single generic message.
- Do not discard `reason_code` / `error_type`; retain and render them (inline or
  behind an info affordance).
- Polling only while a resource is non-terminal; clear timers on unmount and on
  identity change (existing `SpectrumAnalysisPage` polling pattern is the model).

---

## Evaluation / N/A Semantics

- Inapplicable metrics are **visible** with `N/A` plus the backend reason; they
  are never hidden and never converted to zero.
- Classification metrics carry `classification_applicable` +
  `classification_reason`; render `N/A — <reason>` when not applicable. Bounded
  reasons include `detection_only_pipeline`, `label_space_mismatch`,
  `unknown_classification_semantics`.
- Preferred comparison presentation:

  ```text
  Metric                    Experiment A   Experiment B
  Localization AP50         0.61           0.68
  Classification mAP        N/A            0.72
  Matched Accuracy          N/A            0.81
  ```

  with the reason shown alongside the `N/A` (e.g. `label_space_mismatch`).

- The aggregate evaluation read model exposes `classification_applicable` and
  `classification_reason`; localization metrics (`ap50`, `ap50_95`, operating
  point) are always present for detection models.
- Null metric values (including per-case IoU) render as `N/A`/`—`, never `0`.
- Coverage and comparability (`coverage`, `comparable`, `reasons[]`) are shown
  explicitly.

---

## API Boundary

Frontend/backend coupling is concentrated in:

```text
frontend/src/api/client.ts   wire→domain mapping + fetch calls
frontend/src/api/types.ts    domain TypeScript contracts
```

Rules:

- Pages/components never call `fetch` directly; they call client functions.
- Wire types (`*Wire`, snake_case) are mapped to domain types (camelCase) in the
  client; mapping functions are the single translation layer.
- Unknown/additive response fields are tolerated (ignore unknown; never crash).
- Bounded reason/error codes are preserved through mapping into domain types.
- If `client.ts` becomes too large, the design permits splitting into focused
  modules (e.g. `api/executorSelection.ts`, `api/experiments.ts`,
  `api/evaluation.ts`) behind a stable import surface; this is an implementation
  decision, not a requirement.
- **No OpenAPI code generation in V1** unless implementation discovers a concrete
  need; handwritten client/types are acceptable and are the current contract.

Endpoint families consumed by Frontend V1 (from backend `d4b22ee`):

```text
/api/health
/api/recordings, /api/recordings/{id}, /api/recordings/{id}/spectrogram,
  /api/recordings/{id}/waveform, /api/recordings/{id}/ground-truth
/api/datasets/spacenet/register
/api/pipelines
/api/executor-availability   (recording-scoped: recording_id + pipeline_id + executor)
/api/executor-selection      (recording_id OR dataset_name+dataset_split+dataset_label_space)
/api/analysis-runs, /api/analysis-runs/{id}, /api/analysis-runs/{id}/detections
/api/detections/{id}, /api/detections/{id}/fft
/api/imported-runs  (+ /api/imported-runs/batch)
/api/dataset-experiments (+ /{id}, /{id}/items, /{id}/items/{item}/attempts,
  /{id}/run, /{id}/retry-failed, /{id}/retry-evaluation)
/api/dataset-benchmarks (+ /{id}, /{id}/items, /compare)
/api/algorithm-lab/compare
```

---

## Frontend Architecture

Preserve the existing top-level layout:

```text
frontend/src/api/
frontend/src/app/
frontend/src/features/
frontend/src/pages/
```

Target feature decomposition (extract, do not rewrite):

```text
features/
├── execution-environment/    ExecutionEnvironmentSelector + status/reason rendering
├── analysis-run/             run creation form, status badge, provenance, polling hook
├── dataset-experiment/       create form, list, detail, items, attempts, progress
├── evaluation/               coverage, aggregate/per-class metrics, N/A semantics, compare
└── algorithm-lab/            per-recording A/B (existing, evolved)
```

Existing features retained:

```text
features/spectrum/          SpectrogramViewer (reused by Spectrum + Algorithm Lab)
features/signals/           SignalResultsPanel, derived helpers, spectrum navigation
features/signal-detail/     LineSeriesChart
features/imports/           ImportRunModal
features/dataset-benchmarks/  evolved/re-homed into Experiments (evaluation surfaces)
```

Boundaries:

- **Pages** are composition/routing boundaries; they must not own backend policy.
- **Features** own UI + local behavior for a product capability and consume the
  client; they do not call `fetch`.
- The API client/types own all backend coupling.

Retirement:

- `features/spectrum/executorPolicy.ts` client-side selection authority is
  retired in F1 (its tests are re-pointed at `ExecutionEnvironmentSelector`
  behavior sourced from backend facts).

---

## Component Responsibility Map

| Component (target) | Responsibility | Consumes (backend facts) | Forbidden |
|---|---|---|---|
| `ExecutionEnvironmentSelector` | Present Auto + 3 executors; derive enabled/status; explain Auto | pipeline projection, executor-selection, executor-availability | any client fallback; any plugin-id branch; any capability matrix |
| `AutoExplanationPanel` | Progressive-disclosure Auto details | `resolved_executor`, `reason`, `reason_code`, `workload_class`, `candidates[]` | inventing reasons; exposing secrets |
| `AnalysisRunForm` | Create a single run | pipeline projection, selector result, optional `model_release_id` | choosing executor itself; sending `auto`+executor together |
| `RunStatusBadge` | Render lifecycle + bounded error code | AnalysisRun `status`, `error_type`, `error_message` | collapsing codes into generic text |
| `RunProvenanceCard` | Show resolved executor + mode/reason | execution metadata (`requested_execution_mode`, `auto_reason_code`, `auto_reason`, `workload_class`) | rendering private paths/environment_ref |
| `ExperimentCreateForm` | Create a DatasetExperiment | pipelines, dataset identity (exact triple), selector result, `max_concurrency`, protocol | sending frozen identity fields; release picker hardcoding; inventing parameter schemas |
| `ExperimentList` / `ExperimentDetail` | List/detail/monitor experiments | experiment read model + counters + items | recomputing counters as authority |
| `ExperimentItemTable` | Items + statuses | `DatasetExperimentItemRead` | inventing statuses |
| `AttemptTimeline` | Attempts per item | `DatasetExperimentAttemptRead` | exposing launch internals beyond given fields |
| `EvaluationMetricsView` | Coverage + metrics + N/A | `DatasetEvaluationRead`, aggregate/per-class/confusion | fake zeros; hiding inapplicable metrics |
| `ExperimentComparePanel` | A vs B aggregate comparison | compare response (`comparable`, `reasons`, `deltas`) | comparing non-comparable experiments; inventing deltas |
| `CaseAnalysisView` (existing) | Per-recording A/B | algorithm-lab compare, spectrogram, GT | dataset-level aggregation (belongs to Experiments) |
| `SpectrogramViewer` (existing) | TF overlays | spectrogram meta + boxes | executor/capability logic |
| `SignalResultsPanel` (existing) | Detection list | detections | capability logic |

---

## Routing Design

Preserve existing routes; add Experiments routes incrementally. No aesthetic
renames or migrations.

```text
/recordings                         existing — Recording Library
/spectrum/:recordingId              existing — Spectrum Workbench
/signals/:runId                     existing — run detections
/signals/:runId/:detectionId        existing — signal detail
/algorithm-lab                      existing — per-recording A/B
/experiments                        NEW — Experiments list
/experiments/:experimentId          NEW — experiment detail (progress/items/attempts/evaluation)
/experiments/compare                NEW — Experiment A vs B aggregate compare
/settings                           existing route retained; NOT in primary nav
```

Rules:

- Choose the simplest structure consistent with current React Router usage
  (nested routes with query params for tab/drilldown state, as the current
  `AlgorithmLabPage` does with `tab`/`recording`/`runA`/`runB`/`benchmark`).
- The Experiments/Compare surface may use a nested route or a query-param tab;
  the implementation plan decides, but `Compare` must remain under `Experiments`.
- Existing links and bookmarks (e.g. `/algorithm-lab?tab=…`) should be preserved
  where practical; any re-homing of the dataset-benchmarks list under Experiments
  must not break existing deep links (implementation may add a redirect/alias).
- Routes are not implemented in this round.

---

## State Management Strategy

- **No global state library.** Continue with local component state + props +
  URL/query-param state for tab/selection (the existing pattern).
- Data fetching stays in the API client; pages/features own loading/error/empty
  state per surface.
- Polling: while a resource is non-terminal, poll at a bounded interval; clear on
  unmount and on identity change; stop on terminal status. Mirror the existing
  `SpectrumAnalysisPage` / `BenchmarkDetailView` patterns.
- Cross-feature navigation uses URL params to carry identity (recording, run A/B,
  experiment ids), avoiding hidden shared mutable state.
- Derived UI facts (labels, N/A, badges) are pure functions of backend data.

---

## Backend Dependency Stability

### GREEN — safe to implement now

```text
Recording list/detail/import + SpaceNet register
Spectrogram / Waveform / FFT
Ground truth read
DetectionResult
Pipeline / Plugin projection (executors_supported, recommended_executor,
  technical_execution_capabilities, model_release_required)
AnalysisRun create/read/list/detections
DatasetExperiment / DatasetExperimentItem / Attempt entity existence
DatasetEvaluation / DatasetBenchmark existence
Algorithm Lab compare
executor names local_cpu | local_gpu | remote_gpu
execution modes manual | auto
main endpoint families
```

### YELLOW — isolate behind API/type layer

```text
executor-selection detailed response shape
  (requested_mode, resolved_executor, reason_code, reason, workload_class, candidates[])
candidate matrix detail (technical/configured/certified/available per candidate)
reason fields (auto_reason, reason_message)
some provenance metadata (execution metadata optional fields)
DatasetExperiment derived counters (queued/running/completed/failed/attempt_count)
dataset-benchmark compare optional/null metrics and deltas keys
aggregate_metrics_json sub-shape (localization/classification_on_matched/class_aware/ground_truth)
AnalysisRun resolved model_release_id provenance projection
  (AnalysisRunRead does NOT currently expose it; do not infer)
optional future authoritative dataset catalog / list surface
  (no GET /api/datasets or /api/datasets/catalog at the reference commit)
optional future pipeline parameter_schema / authoritative parameter UI metadata
  (PipelineDefinitionRead does NOT currently expose parameter_schema)
```

Isolation requirement: only the API client/types may name these fields; UI code
consumes normalized domain types and must tolerate their absence (render `N/A`).

### RED — do not implement ordinary frontend

```text
runtime qualification UI
certificate installation UI
SSH key / known_hosts UI
raw remote profile editor
cgroup / PSI UI
nvidia-smi diagnostics
Plan B / Plan C evidence UI
```

---

## Backend / Frontend Parallel Development Model

```text
BACKEND TRACK                         FRONTEND TRACK
─────────────────────────────         ─────────────────────────────
Plan B remediation                    F0  Frontend Contract Foundation
Plan C remote_gpu                     F1  Execution Environment
H5.5 GPU→no-GPU handoff               F2  Single Recording V1
Plan D                                F3  Dataset Experiments
API freeze                            F4  Evaluation + Compare
                                      F5  Algorithm Lab Integration
                                      F6  UX / Status / Error Hardening
        │                                         │
        └──────────── parallel ───────────────────┘
                          │
                Backend API freeze
                          ↓
                F7  Backend API Freeze Reconciliation
                          ↓
                F8  Frontend V1 Acceptance
```

Rule: **Frontend F0–F6 do not wait for the backend seal.** They may proceed
against the `d4b22ee`-shaped contract, keeping YELLOW fields isolated behind the
API/type boundary. Only F7 (reconciliation) and F8 (acceptance) wait for the
backend API freeze.

---

## F0–F8 Milestones

### F0 — Frontend Contract Foundation

- **Goal:** lock baseline evidence, contract inventory, and engineering rules.
- **Deliverable:** recorded baseline (tests/build), documented contract inventory
  (endpoint families + GREEN/YELLOW/RED), and the engineering constraint list.
- **Main areas:** `frontend/src/api/*`, docs.
- **User-visible:** none.
- **Backend deps:** existing endpoints.
- **Can start now?** YES.
- **Reason:** read-only; backend stable enough at `d4b22ee`.
- **Engineering rules locked at F0:**
  ```text
  preserve React / TypeScript / Vite / Ant Design / Vitest / Testing Library
  preserve the existing fetch-based client
  do NOT introduce Redux / Zustand / React Query / MSW / another router / another UI library
  do NOT modernize dependencies as part of Frontend V1
  do NOT rewrite routing
  ```

### F1 — Execution Environment Foundations

- **Goal:** introduce the reusable `ExecutionEnvironmentSelector`; add
  executor-selection client/types; support manual/auto; retire client-side
  executor authority.
- **Deliverable:** selector + API/type layer + reason-code rendering;
  `executorPolicy.ts` decision authority removed.
- **Main areas:** `features/execution-environment/*`, `api/client.ts`, `api/types.ts`,
  `pages/SpectrumAnalysisPage.tsx`, tests.
- **User-visible:** choose Auto / Local CPU / Local GPU / Remote GPU with
  availability/certification status and reasons; Auto explanation.
- **Backend deps:** `/api/executor-selection`, `/api/executor-availability`
  (recording-scoped), `/api/pipelines` (YELLOW fields isolated).
- **Can start now?** YES.
- **Reason:** removes the authority violation immediately; YELLOW fields are
  isolated behind the client/types.

### F2 — Single Recording V1

- **Goal:** complete Workflow A.
- **Deliverable:** Recording → Pipeline → Environment → AnalysisRun → status →
  detections → provenance, with optional `model_release_id` request pass-through.
  Single-run **resolved-release provenance is NOT rendered** while
  `AnalysisRunRead` does not project `model_release_id` (YELLOW; see ModelRelease
  Policy); do not infer the resolved release.
- **Main areas:** `pages/SpectrumAnalysisPage.tsx`, `features/analysis-run/*`,
  `api/client.ts`, `features/signals/*`.
- **User-visible:** run a single recording via Auto or Manual; see resolved
  executor and provenance; inspect detections/signals. Generic creation sends an
  empty `parameters` object.
- **Backend deps:** `POST /api/analysis-runs` (`executor` or `execution_mode`),
  `GET /api/analysis-runs/{id}`, `.../detections`.
- **Can start now?** YES.
- **Reason:** backend accepts both modes; optional `model_release_id` request field
  exists; no read-model release projection is required for this milestone.

### F3 — Dataset Experiments

- **Goal:** deliver Workflow B.
- **Deliverable:** experiments list/create/run/detail; items; attempts; progress
  and status lifecycle; linked evaluation reference; backend-projected
  `model_release_id` + `asset_manifest_sha256` provenance (available on
  `DatasetExperimentRead` now). Dataset identity is the exact triple; generic
  creation sends an empty `parameters` object.
- **Main areas:** `features/dataset-experiment/*`, `pages/Experiments*`,
  `api/client.ts`, `api/types.ts`.
- **User-visible:** create an experiment over a frozen dataset with a model and an
  environment; monitor items/attempts; see failure reasons and the
  backend-projected release identity.
- **Backend deps:** `/api/dataset-experiments*`; dataset identity is an exact
  triple (no catalog endpoint exists yet).
- **Can start now?** YES.
- **Reason:** endpoints exist at `d4b22ee`; derived counters are YELLOW and
  isolated; no dataset catalog or parameter schema is required.

### F4 — Evaluation + Multi-model Compare

- **Goal:** deliver evaluation reading and Workflow C aggregate comparison.
- **Deliverable:** coverage + localization/classification metrics with N/A
  semantics; Experiment A vs B comparison with comparability reasons and deltas;
  per-recording drilldown entry point.
- **Main areas:** `features/evaluation/*` (evolving `features/dataset-benchmarks/*`),
  `api/client.ts`.
- **User-visible:** evaluation metrics (N/A where inapplicable) and experiment
  comparison with deltas.
- **Backend deps:** `/api/dataset-benchmarks/{id}`, `/items`, `/compare`.
- **Can start now?** YES.
- **Reason:** evaluation engine and compare read model exist; compare schema is
  YELLOW and isolated.

### F5 — Algorithm Lab Integration

- **Goal:** keep per-recording A/B as the deep-dive; cross-link from Experiments.
- **Deliverable:** preserved Algorithm Lab with duplicate compare orchestration
  removed; drilldown from Compare/Experiment to Algorithm Lab.
- **Main areas:** `features/algorithm-lab/*`, `pages/AlgorithmLabPage.tsx`.
- **User-visible:** open a shared recording from an experiment comparison into
  Algorithm Lab.
- **Backend deps:** `/api/algorithm-lab/compare`.
- **Can start now?** YES.
- **Reason:** existing functionality is preserved and only re-linked.

### F6 — UX / Status / Error Hardening

- **Goal:** consistent status/error/loading UX and reason-code preservation.
- **Deliverable:** uniform `PlatformApiError.display` usage; reason-code
  preservation; loading/empty/error states; status badges; polling consistency;
  basic desktop UX/accessibility cleanup; no fake zero metrics.
- **Main areas:** `api/client.ts`, all pages/features.
- **User-visible:** consistent, code-preserving errors and clean empty states.
- **Backend deps:** none new.
- **Can start now?** YES.
- **Reason:** purely frontend.

### F7 — Backend API Freeze Reconciliation

- **Goal:** reconcile frontend client/types with the frozen Backend V1 API.
- **Deliverable:** resolved YELLOW drift; contract-accurate types; no broad
  redesign. Specifically reconcile:
  ```text
  executor-selection shape + candidate matrix + reason fields
  DatasetExperiment derived counters
  benchmark compare deltas / aggregate_metrics_json sub-shape
  AnalysisRun resolved model_release_id projection
    → if final AnalysisRunRead exposes model_release_id: add read-only resolved
      ModelRelease provenance; otherwise do NOT invent it
  optional authoritative dataset catalog / list surface
    → if the backend exposes a catalog: upgrade exact dataset identity input into
      an authoritative selector; otherwise retain explicit exact identity fields
  optional pipeline parameter_schema / parameter UI metadata
    → if the backend exposes an authoritative schema: optionally introduce
      schema-driven controls; otherwise keep generic default/opaque parameters
  ```
- **Main areas:** `api/client.ts`, `api/types.ts`, tests.
- **User-visible:** none directly (may enable release provenance, dataset selector,
  or schema-driven parameters if the final backend provides them).
- **Backend deps:** final backend API freeze / seal.
- **Can start now?** **NO.**
- **Reason:** must not begin before the backend contract freeze; no backend
  endpoint is invented in this design.

### F8 — Frontend V1 Acceptance

- **Goal:** final acceptance.
- **Deliverable:** Workflow A/B/C accepted; Algorithm Lab drilldown accepted;
  frontend tests pass; TypeScript build passes; production build passes.
- **Main areas:** acceptance suite + build gates.
- **User-visible:** accepted Frontend V1.
- **Backend deps:** backend seal.
- **Can start now?** **NO.**
- **Reason:** depends on F7.

---

## Testing Strategy

Future strategy (no tests written this round); uses existing tools only.

```text
API contract / mapping tests
  wire→domain mapping in api/client.ts; PlatformApiError parsing; reason-code preservation

ExecutionEnvironmentSelector state matrix
  technical/configured/certified/available combinations → enable/disable + labels + reasons

manual vs auto request tests
  manual sends exact executor and no `auto`; auto sends `execution_mode=auto` with no executor;
  never sends both

single-recording workflow tests
  create/status/detections/provenance; Auto unresolved shows unavailable (no fallback)

DatasetExperiment lifecycle tests
  create/run/items/attempts/status transitions incl. evaluating/completed_with_failures

N/A metric semantics
  classification inapplicable → visible N/A + reason; no zeros

comparison null-delta semantics
  null deltas → N/A; comparability reasons rendered

Algorithm Lab drilldown
  cross-link carries recording/runA/runB correctly

bounded reason-code preservation
  representative codes surfaced, not collapsed

build/typecheck
  `npm run build` (tsc -b && vite build) remains a gate
```

Tooling constraints:

```text
Continue with Vitest + Testing Library + jsdom.
Continue stubbing fetch (vi.stubGlobal) as today; MSW is NOT required and not introduced.
Repurpose or delete the dead frontend/src/mocks/demo.ts as shared fixtures in the implementation plan.
```

---

## Non-Goals

Frontend V1 explicitly excludes:

```text
training UI / model training management
certificate editor
SSH key manager / known_hosts editor
runtime environment editor
qualification evidence dashboard
cgroup / PSI / nvidia-smi diagnostics
real-time RF acquisition
multi-user authentication / RBAC
cloud billing
new backend domain entities
MultiModelExperiment
new global state architecture
dependency modernization
```

---

## Backend API Freeze / Reconciliation Boundary

- F0–F6 target the `d4b22ee`-shaped contract and are **not** blocked by the
  backend seal.
- YELLOW fields are the only allowed drift surface and must remain isolated in
  `api/client.ts` / `api/types.ts`.
- F7 is the single reconciliation point after the backend API freeze: reconcile
  YELLOW drift, confirm GREEN contracts, and adjust types without redesign.
- F8 acceptance follows F7.
- No frontend work depends on Plan B / Plan C / Plan D internals.
- No breaking backend change is expected to require a frontend redesign; additive
  fields are tolerated throughout.

---

## Frontend V1 Acceptance Definition

Frontend V1 is accepted when, after F7:

```text
Workflow A accepted   Recording → environment (Auto/Manual) → AnalysisRun →
                      status → detections → overlays → detail → provenance
Workflow B accepted   Frozen dataset → model → environment → DatasetExperiment →
                      items/attempts → evaluation (coverage + metrics + N/A)
Workflow C accepted   Experiment A vs Experiment B → comparable + reasons +
                      deltas → per-recording drilldown → Algorithm Lab
Algorithm Lab accepted  per-recording A/B with GT + overlays + per-case states

Frontend tests pass       (`npm test -- --run`)
TypeScript build passes   (`tsc -b`)
Production build passes   (`npm run build`)
```

Acceptance invariants:

```text
no client-side executor fallback
no plugin-id capability hardcoding
no hardcoded ModelRelease choices
no null/N/A rendered as zero
no operator/qualification internals exposed
primary navigation is exactly Recordings | Experiments | Algorithm Lab
Compare is inside Experiments
Auto is the default execution mode and backend-authoritative
```

---

## Spec Self-Review Record

Performed before commit:

1. **No contradictions** between sections (navigation, workflows, milestones,
   stability) — consistent.
2. **No unapproved architectural change** — Approach C, three-destination
   navigation, Auto default, backend-only executor authority, ModelRelease
   read-only default, Settings removed, N/A visible: all as approved.
3. **No hidden client executor policy** — the spec requires retiring
   `executorPolicy.ts` authority and forbids fallback and plugin-id branches.
4. **No hard-coded plugin capability logic** — capability derives from pipeline
   projection + executor availability + executor selection.
5. **Compare not promoted** — Compare is defined inside Experiments and forbidden
   from primary navigation.
6. **Settings not an operator console** — removed from primary nav; operator
   configuration explicitly non-goal; future indicator is read-only only.
7. **ModelRelease not hardcoded** — backend-resolved default; optional field
   pass-through; resolved-release provenance rendered ONLY where the backend read
   model projects it (DatasetExperiment now; AnalysisRun is a YELLOW gap that is
   never inferred); selector only if backend exposes an authoritative choice list.
8. **Null/N/A not converted to zero** — explicit rule in Evaluation/N/A Semantics
   and in component responsibilities.
9. **No qualification internals** — Product/Operator Boundary lists and forbids
   them.
10. **F0–F6 not waiting for backend seal** — explicitly stated in the Parallel
    Development Model and milestones.
11. **F7/F8 do not start before API freeze** — marked `Can start now? NO`.
12. **No implementation-level over-specification** — contracts and
    responsibilities specified; React signatures deliberately omitted.
13. **Backend-change tolerance present** — GREEN/YELLOW/RED with isolation rule
    and additive-field tolerance principle.

Contract-accuracy corrective (second review):

14. **`executors_supported` is NOT the executor option universe** — the four
    options are a fixed product vocabulary; `executors_supported` is a hint only;
    options are not hidden by absence, and state comes from backend candidate facts.
15. **No DatasetExperiment call to recording-scoped `/executor-availability`** —
    the scope-specific contracts specify dataset scope uses `/executor-selection`
    with the dataset triple.
16. **No generic dataset catalog assumed** — dataset identity is the exact triple;
    no `GET /api/datasets` / `/catalog` is claimed; no SpaceNet hardcoding; a
    catalog is a YELLOW F7 item.
17. **No AnalysisRun resolved `model_release_id` claim** — `AnalysisRunRead` does
    not expose it; provenance is never inferred; the DatasetExperiment distinction
    is explicit.
18. **No `parameter_schema` claim** — `PipelineDefinitionRead` does not expose it;
    generic V1 sends `{}`; no invented parameter editor; a schema is a YELLOW F7
    item.

No contradictions introduced elsewhere in the document; no unresolved issue
remains before commit.
