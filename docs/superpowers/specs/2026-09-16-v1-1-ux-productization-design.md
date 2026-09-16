# WISA V1.1 UX and Productization Design

Status: **FINAL APPROVED — 2026-09-16**
Sealed V1 integration baseline: `integration/v1-candidate` @ `067c6c020db241076718cc85d9716ce764ba09cb`
Sealed V1 baseline tree: `2a54007409f9e06899207ac1488cf046302bba9e`
Documentation branch: `docs/v1-1-ux-productization`
Future integration branch: `integration/v1-1-ux-candidate` (not created by this document)

This document is an **architectural design specification**. It defines the
approved UX direction, information architecture, lifecycle contracts, state
boundaries, and architectural seams for WISA V1.1. It is not an implementation
plan and does not prescribe task sequencing beyond the architectural boundaries
that implementation planning will refine.

The sealed V1 baseline above is immutable. This specification is produced from
exactly that baseline and does not modify, move, or reinterpret it.

---

## 1. Context and Problem

WISA V1 backend functionality is substantially complete. The frontend, however,
currently exposes backend and domain concepts too directly, so users must learn
the platform's internal entity model before they can accomplish ordinary
signal-analysis work.

Current top-level user-facing concepts include:

```text
Recordings
Dataset Experiments
Algorithm Lab
AnalysisRun
imported run / batch import
executor selection
```

The current Data Library surface (`frontend/src/pages/RecordingsPage.tsx`) presents
four peer action buttons — register dataset, import existing run, batch import,
import recording — that are conceptually unrelated tasks sharing one visual
weight. The current navigation (`frontend/src/app/MainLayout.tsx`) is organized
around backend surfaces rather than around what a user is trying to do.

This creates unnecessary learning cost. The central product problem is:

```text
The UI is organized around backend entities and APIs rather than the user's
task model.
```

The V1.1 objective is **not** visual decoration. The objective is to organize the
product around the user's actual progression:

```text
data → analysis → results → comparison/evaluation
```

A user must not need to understand internal entities such as `RecordingModel`,
`DatasetExperimentAttempt`, `ImportedBatchResolution`, executor certification, or
batch-transport resolution before using the platform. Those concepts may remain
visible to advanced users on demand, but they are not the entry vocabulary of the
product.

---

## 2. Design Principles

The following principles are normative for V1.1 and are recorded explicitly:

```text
1.  User task model over database model.
2.  Progressive disclosure: basic workflows first, engineering details only
    when needed.
3.  Dataset and standalone recording are different user objects.
4.  Existing validated backend/domain contracts should be reused unless a
    concrete UX lifecycle requirement requires a new API.
5.  No unnecessary new dataset persistence model for V1.1.
6.  Preserve bilingual zh-CN / en-US support.
7.  Simple, restrained, information-dense UI.
8.  Business state belongs in the URL where appropriate.
9.  User preferences belong in persistent client preferences.
10. Temporary interaction state stays ephemeral.
11. Destructive actions are explicit, scoped, and fail-safe.
12. No GPU dependency is introduced into the Windows/control-plane app.
13. Remote-GPU orchestration remains historical/experimental and is not
    restored as the V1.1 primary workflow.
```

These principles are consistent with the accepted Frontend V1 design
(`docs/superpowers/specs/2026-09-15-frontend-v1-design.md`): the backend remains
the sole execution authority, `Auto` remains a mode rather than an executor, the
frontend never performs silent executor fallback, and no operator/qualification
internals are exposed.

---

## 3. Top-Level Information Architecture

The user-facing concept **"信号记录 / Recordings"** is replaced by
**"数据管理 / Data Library"**.

Target navigation:

```text
WISA
├── Data Library / 数据管理
├── Dataset Experiments / 数据集实验
├── Algorithm Lab / 算法评测实验室
├── User Guide / 使用指南
└── Settings / 设置
```

Rules:

- The sidebar must be collapsible.
- The sidebar collapsed preference is persisted as a client preference.
- The spectrum/sample analysis page is **not** a first-level navigation item. It
  is a workspace entered from a selected sample.
- `Settings` is a first-class destination for **user preferences** (locale, theme,
  collapsed sidebar, and related client preferences). It does not become an
  operator console; the product/operator boundary defined in the Frontend V1
  design is preserved.
- The navigation must not imply configurability or capabilities that do not
  exist.

---

## 4. Data Library

Data Library has two first-level tabs:

```text
1. Datasets / 数据集
2. Standalone Samples / 独立样本
```

The Data Library must **not** render all dataset member Recordings as
first-level peer cards. A dataset that contains 2500 `RecordingModel` rows is
presented as a single dataset object at the root level, and its members are
browsed inside that dataset.

### 4.1 Dataset Projection

For V1.1, do **not** introduce a new datasets table unless implementation-time
analysis proves it unavoidable. Instead, use the existing Recording metadata to
form a **dataset projection** — a read model over existing recording metadata, not
a new persistence entity.

**Display identity is not stable identity.** `dataset_name` and `dataset_split`
are user-facing/display components and are **not** sufficient as a globally unique
projection key.

Current SpaceNet registration (`backend/app/datasets/service.py`) writes, for each
member sample:

```text
source         = "spacenet"
dataset_name   = "SpaceNet"
dataset_split  = <split>
label_space    = "spacenet_14"
external_path  = <resolved sample path>
```

and duplicate-registration detection compares each exact `external_path`. Two
independently registered dataset roots can therefore legitimately share the same
`dataset_name` and `dataset_split` while referring to different physical dataset
instances. For example:

```text
D:\SpaceNet-A\test
E:\SpaceNet-B\test
```

Both would display as `SpaceNet / test`, but they are different datasets.

The projection must therefore expose a stable, opaque **dataset projection
identity**, conceptually:

```text
dataset_projection_id
```

The identity must distinguish independently registered dataset instances. Its
deterministic inputs use already-available metadata and must include enough
information to distinguish physical/logical datasets. For external SpaceNet
registrations the inputs must distinguish at least:

```text
source
dataset_name
dataset_split
label_space
normalized dataset-root identity
```

The exact encoding/hash algorithm belongs to implementation planning. A raw
filesystem path must **not** be exposed as the URL identity. The frontend uses the
opaque `dataset_projection_id` for routing and API operations while still
displaying friendly fields such as `SpaceNet` and `test`.

Example:

```text
SpaceNet + test (root A)  → dataset projection P1
SpaceNet + test (root B)  → dataset projection P2
```

Even though both display as `SpaceNet / test`, they never collapse into one
visible dataset, and the platform never merges their members.

`(dataset_name, dataset_split)` may still be used as a **display grouping hint**,
but it is not a stable identity and must not be used as the sole routing/API key.

Relevant existing fields on the recording read model include:

```text
dataset_name
dataset_split
label_space
source
external_path
```

Each visible dataset object may internally still consist of 2500 `RecordingModel`
rows.

### 4.2 Dataset List

Dataset cards/rows should show aggregate information such as:

```text
dataset name
split
sample count
label space
ground-truth availability
source
external/local status
location where safe/useful
```

Primary actions:

```text
Browse Samples
Create Dataset Experiment
Import Analysis Results
More menu
```

The Data Library root page must not render 2500 SpaceNet samples directly.

### 4.3 Dataset Detail

Dataset detail contains three areas:

```text
Overview
Samples
Analysis History
```

**Overview** shows useful aggregate metadata for the dataset projection
(identity, sample count, label space, ground-truth availability, source,
external/local status).

**Samples**:

```text
server-side pagination/filtering where required
search
bounded page size
sample name/id
physical observation range
duration
GT presence
analysis count where available
actions: view / analyze
```

**Analysis History** exposes dataset-level analysis outcomes without forcing the
user to understand internal run records first. Useful facts include:

```text
pipeline
version
source/executor class when relevant
coverage/completion
status
timestamp
```

Contextual actions in dataset detail:

```text
Create Experiment
Import Batch Analysis Results
Open Evaluation
```

### 4.4 Standalone Sample List

Standalone samples are recordings that are not members of any dataset
projection. They are shown as a bounded searchable/paginated list or as compact
cards.

Useful fields:

```text
name
source
sampling rate
center frequency
physical frequency range
duration
format
analysis history count/status
```

Primary actions:

```text
Open Analysis Workspace
Analysis History
More
```

---

## 5. Add Data vs Import Results

The current four-peer-button presentation is removed:

```text
register dataset
import existing run
batch import
import recording
```

It is replaced by two user concepts:

```text
+ Add Data
Import Analysis Results
```

**Add Data** contains:

```text
Import Standalone IQ
Register Dataset
```

**Import Analysis Results** contains:

```text
Single-sample Analysis Result
Dataset Batch Analysis Result
```

User-facing terminology is user-oriented, while existing internal contracts are
preserved:

```text
AnalysisRun   remains the internal domain object for a single analysis result
BAPv1         remains the batch transport contract for batch analysis results
```

`Import Run` is not used as a primary user-facing label. The preferred label is
**Import Analysis Results**.

---

## 6. Standalone IQ Import

Standalone raw IQ import requires acquisition context because raw IQ files do not
necessarily contain acquisition metadata. The import form therefore requires:

```text
name
file
data format
sampling rate (Fs)
center frequency (Fc)
optional label-space metadata if applicable
```

The UI must explain **why** Fs and Fc are required: raw IQ files do not always
carry acquisition metadata in-band, so the platform cannot infer them reliably.
The UI must not imply that these fields are always present in source files or that
the platform reads them automatically.

---

## 7. SpaceNet Metadata Semantics

### 7.1 Current Adapter Behavior

`backend/app/datasets/spacenet.py` currently derives acquisition parameters from
the SpaceNet JSON `observation_range` as follows:

```text
sample_rate_hz      = frequency_high - frequency_low
center_frequency_hz = (frequency_low + frequency_high) / 2
```

with `observation_range` expressed as `[low_mhz, high_mhz]` and converted to Hz.

This is a **platform-derived interpretation**. The JSON `observation_range`
itself does **not** logically prove that Fs equals the observation bandwidth.

### 7.2 Source vs Derived Metadata

V1.1 must conceptually distinguish:

```text
Source Metadata   metadata that is present in / implied by the dataset's own
                  declared contract (for SpaceNet: observation_range)

Derived Metadata  values the platform computes from source metadata using its
                  own assumptions (for SpaceNet: Fs and Fc as currently derived)
```

For SpaceNet:

- `observation_range` is **source metadata**.
- The derived Fs/Fc must be **labelled as derived** unless and until the official
  dataset contract is independently verified to guarantee the relationship.

### 7.3 Follow-Up

A technical follow-up item is recorded (see Section 25, item A): verify whether
the SpaceNet official dataset contract explicitly guarantees that complex-IQ
sampling rate equals the observation bandwidth.

This verification does **not** block the UX restructuring. If the contract does
not support the assumption, the adapter contract must be revisited separately.

---

## 8. Data Lifecycle and Deletion

Current V1 lacks sufficient delete/unregister lifecycle APIs. V1.1 introduces
explicit lifecycle behavior. All destructive operations are explicit, scoped,
and fail-safe.

### 8.1 Destructive Operation Dependency Guard

Before any destructive operation that would remove:

```text
a standalone Recording
one or more dataset-member Recordings
an AnalysisRun
```

the backend must perform a **dependency preflight**.

```text
DELETE Recording / Remove Dataset / DELETE AnalysisRun
                    │
                    ▼
           Dependency preflight
                    │
            ┌───────┴───────┐
            │               │
     no retained       retained
     dependency        dependency
            │               │
            ▼               ▼
      execute deletion   409 Conflict
                         + explicit blocker
```

Existing referential facts make this mandatory rather than optional:

```text
RecordingModel.analysis_runs   relationship cascade="all, delete-orphan"
RecordingModel.ground_truth    relationship cascade="all, delete-orphan"
DatasetEvaluationItemModel     FKs recording_id and analysis_run_id
DatasetExperimentItemModel     FK recording_id
DatasetExperimentAttemptModel  FK analysis_run_id
```

(`backend/app/recordings/model.py`, `backend/app/benchmarks/model.py`,
`backend/app/dataset_experiments/model.py`.)

Because owned children cascade while evaluation/experiment records hold their own
foreign keys, a naive `session.delete(recording)` would let a parent deletion
bypass the AnalysisRun dependency guard. That is forbidden: parent-resource
deletion must not bypass the AnalysisRun guard.

If the operation would invalidate a retained platform object such as:

```text
DatasetEvaluation
benchmark result
experiment/result provenance
another retained resource defined by the dependency graph
```

the operation must **FAIL CLOSED** with a structured conflict response that gives
the UI enough safe information to explain which dependent resource blocks
deletion. The backend must not silently cascade through retained
historical/evaluation objects.

Only exclusively owned child resources that are not independently retained or
referenced may be cascaded/deleted according to the lifecycle contract. The exact
dependency graph and endpoint error schema belong to implementation planning;
these guarantees are architectural design requirements.

### 8.2 Standalone Sample Deletion

Standalone WISA-managed IQ may be deleted, subject to the dependency preflight in
Section 8.1.

When the preflight passes, the operation may remove:

```text
Recording database row
exclusively owned AnalysisRuns
DetectionResults owned only by those runs
GroundTruth owned by the Recording
WISA-managed source IQ storage
```

The user must receive an **explicit irreversible confirmation** that names what
will be removed. The platform must **not** silently delete externally managed
source files.

### 8.3 Dataset Removal

Dataset members must **not** be casually deleted one-by-one from the dataset
root workflow. The dataset-level action is:

```text
Remove Dataset / 移除数据集
```

Dataset removal is one logical operation over the dataset projection. The
dependency preflight is performed across **all** members before any mutation:

```text
if any member is blocked by a retained dependency:
    fail atomically with a conflict; do not partially unregister
```

Dataset removal must never partially unregister a dataset.

For an externally registered dataset such as SpaceNet, when preflight passes:

```text
remove the WISA database representation atomically
remove related platform-owned metadata according to defined referential rules
DO NOT delete original .bin/.json files from the user's external dataset path
```

The confirmation UI must explicitly state that the external source files remain
untouched.

### 8.4 Analysis Run Deletion

V1.1 adds user-facing deletion of an analysis result/run, with conservative
referential-integrity behavior and the same Section 8.1 dependency preflight.

If a run is referenced by a retained evaluation/benchmark/result that would be
invalidated by the deletion:

```text
fail closed
return a conflict
explain which dependent resource prevents deletion
```

The platform must not silently cascade and invalidate historical evaluations.
The exact backend dependency rules are an implementation-plan concern; the
**fail-safe behavior itself is part of the V1.1 design contract**.

---

## 9. Core User Workflows

The frontend must optimize for four workflows.

### 9.1 Single Sample → One Pipeline

```text
Data Library
→ sample
→ Analyze
→ choose Pipeline
→ execution defaults to Auto
→ Run
→ inspect spectrogram and detections
```

Executor mechanics are secondary. Basic users should not need to understand:

```text
executor registry
deployment certificate
remote profile
```

### 9.2 Single Sample → Pipeline Comparison

Sample Analysis History permits selecting two compatible completed runs.

```text
select Run A
select Run B
→ Compare
```

The UI routes to Algorithm Lab with the correct:

```text
recording
runA
runB
```

The user must not manually copy run IDs.

### 9.3 Dataset → One Pipeline

```text
Dataset detail
→ Create Experiment
```

Dataset identity is prefilled from the selected dataset projection. When an
experiment is initiated from a dataset page, the user should **not** manually
type:

```text
dataset_name
dataset_split
label_space
```

Primary choice:

```text
Pipeline
```

Advanced options may include:

```text
execution environment
concurrency
advanced parameters
```

### 9.4 Dataset → Multi-Pipeline Evaluation

Dataset Analysis History exposes existing dataset-level results. Users can select
compatible results and enter evaluation/benchmark workflows.

Internal concepts such as `DatasetEvaluation`, imported batch resolution, and
per-recording `AnalysisRun` assembly remain implementation details and are not
required user vocabulary.

---

## 10. Execution Environment UX

Execution environment defaults to **Auto**. Executor engineering details are
moved behind progressive disclosure.

When no executor is runnable, the UI must not only display:

```text
"No executor is currently runnable for this request."
```

Instead it must explain the candidate state. Example presentation:

```text
Current Pipeline has no runnable execution environment.

Local CPU
    Unavailable — <safe platform-owned reason>

Local GPU
    Not configured

Remote GPU
    Not enabled for the V1 production workflow
```

Detailed diagnostics may be expandable. Reasons must be bounded, human-readable,
and free of secrets, private interpreter/asset paths, SSH material, certificate
internals, and raw telemetry (consistent with the Frontend V1 operator boundary).

Important constraint: the frontend must **not** force-enable a disabled Run
button. The backend selection contract remains authoritative.

There is a separate **functional investigation requirement**: determine why
`local_cpu` is not entering the runnable executor set in the current Windows
deployment. The backend selection contract currently requires runnable
candidates to be:

```text
technical AND configured AND certified AND available
```

Implementation must diagnose the **actual failed predicate** at the real
backend/runtime execution-selection seam rather than papering over it in the UI.
Remote-GPU orchestration is not reintroduced as part of this investigation.

---

## 11. Algorithm Lab State Persistence

Business workspace state belongs in the URL.

Algorithm Lab state includes:

```text
recording
runA
runB
```

Current direct links using these query parameters are conceptually correct. The
navigation problem is that re-entering Algorithm Lab through the sidebar currently
navigates to the bare route and loses query state.

V1.1 should retain and restore the last meaningful Algorithm Lab workspace.

Preferred principle:

```text
business state:                    URL
last visited workspace route:      persistent/session navigation memory
temporary rendered result:         rehydrate from backend using IDs
```

Transient React component state must not be the only source of truth.

Expected behavior:

```text
Algorithm Lab
→ Data Library
→ Algorithm Lab
```

returns to the previous recording/run comparison and rehydrates the result from
the backend using the persisted IDs.

---

## 12. Global State Rule

The following classification is normative:

| State kind | Examples |
|---|---|
| **URL** | active business resource; `recording`; `runA`; `runB`; `experiment`; `benchmark`; meaningful tab/filter when shareability/navigation matters |
| **Persistent client preferences** | locale; theme; sidebar collapsed state; last meaningful workspace route where needed |
| **Ephemeral React state** | modal open/closed; loading; hover state; cursor position; temporary error display |

Rules:

- Business identity and shareable navigation state live in the URL.
- User preferences live in persistent client preferences.
- Temporary interaction state stays ephemeral.
- Do not indiscriminately store all state in `localStorage`.
- Do not make transient React component state the only source of truth for a
  business workspace.

---

## 13. App Shell

Sidebar:

```text
collapsible
clear icons
Data Library
Dataset Experiments
Algorithm Lab
User Guide
Settings
```

Header:

```text
current context/page
theme control
language control
guide/help shortcut
```

Rules:

- Keep branding restrained.
- Do not duplicate the full WISA title unnecessarily in both sidebar and header.
- Theme and language controls live in the header and reflect persisted
  preferences.

---

## 14. Theme

Support three modes:

```text
system
light
dark
```

The preference is persisted. Use Ant Design theme infrastructure rather than
maintaining two unrelated manual style systems.

All key components must remain readable in both light and dark modes:

```text
Layout
Sider
Header
Cards
Tables
Forms
Alerts
Modals
spectrogram workspace chrome
overlays and legends
```

Theme is a usability/accessibility feature, not a decorative redesign.

---

## 15. User Guide

A first-class route is added:

```text
/guide
```

The User Guide is **application documentation**, not developer/API
documentation. It explains task workflows such as:

```text
Analyze one IQ file
Analyze a dataset
Compare two pipelines
Import server-generated analysis results
```

The guide uses Markdown source content and supports both:

```text
user-guide.zh-CN.md
user-guide.en-US.md
```

The displayed language follows the application's existing locale setting. If
implementation requires a Markdown renderer, choose the smallest well-maintained
solution appropriate to the existing React/Vite stack. Do not build a custom
Markdown parser.

---

## 16. Page Copy

Each major destination should include:

```text
clear title
one-sentence purpose
obvious primary action
```

Approved examples:

```text
Data Library:
Manage standalone IQ samples and registered datasets, and start analysis.

Dataset Experiments:
Run a pipeline over a dataset and track batch analysis progress.

Algorithm Lab:
Compare completed analysis results and inspect detection differences.
```

Avoid exposing backend terminology where a user-oriented phrase is clearer.

---

## 17. Spectrogram Viewer

### 17.1 Current Issues

```text
wheel zoom conflicts with normal page scrolling
viewer aspect ratio is hard-coded
GT overlay is visually weak
prediction/selection visual language is inconsistent
model input resolution risks being confused with display resolution
```

The current implementation (`frontend/src/features/spectrum/SpectrogramViewer.tsx`)
calls `event.preventDefault()` on wheel and zooms the viewer, uses a hard-coded
`aspectRatio: "16 / 8"`, renders GT as a thin dashed current-color stroke, and
renders detections in yellow/white without an explicit legend.

### 17.2 Approved Behavior

Mouse wheel:

```text
normal page scrolling
```

Viewer zoom:

```text
explicit controls
```

Controls:

```text
zoom out
zoom percentage
zoom in
fit
reset
```

Overlay semantics:

```text
Ground Truth:               clear green visual treatment
Prediction:                 clear orange visual treatment
Selected prediction:        high-contrast thicker highlight
legend:                     visible
```

Exact colors may follow theme tokens, but the semantic distinction must remain
clear in **both light and dark modes**.

### 17.3 Resolution Concepts

The viewer must explicitly distinguish three unrelated concepts:

```text
model input size        the tensor size a detection model may consume (e.g. 640x640)
spectrogram raster size  the actual pixel/raster dimensions of the rendered spectrogram
browser display size    the CSS size of the viewer on screen
```

The viewer must **not** be forced to 640x640 merely because a detection model may
use 640x640 input. Where spectrogram metadata provides a better basis, the viewer
should respect spectrogram/display geometry instead of the current hard-coded
2:1 assumption.

---

## 18. Dataset / Sample Analysis History

Analysis History is a **first-class user concept**.

Sample history shows:

```text
pipeline
version
status
executor/source when useful
timestamp
action to open result
action to compare compatible runs
action to delete where allowed
```

Dataset history summarizes dataset-level results without requiring users to
inspect 2500 run records individually.

Contextual navigation connects:

```text
Data Library
→ Experiment
→ Evaluation
→ per-sample Algorithm Lab case
```

---

## 19. Backend API Changes Allowed for V1.1

The backend is not frozen forever; the V1 baseline is frozen. V1.1 may add
narrowly scoped backend APIs required by the approved UX.

Expected categories include:

```text
dataset projection/summary query exposing a stable opaque dataset_projection_id
filtered/paginated recording query
analysis-history query/projection if existing APIs are insufficient
standalone Recording deletion
dataset unregister/remove
AnalysisRun deletion
safe dependency/conflict reporting
```

Contracts for these additions:

- The dataset projection query must return a stable, opaque
  `dataset_projection_id` distinct from `dataset_name`/`dataset_split`. A raw
  filesystem path must not be used as the URL/API identity.
- Every destructive endpoint (Recording deletion, dataset removal, AnalysisRun
  deletion) must implement the Section 8.1 dependency preflight and return a
  structured fail-closed conflict when a retained dependent resource blocks the
  operation. Dataset removal must be all-or-nothing.
- These additions do not create or require a persisted dataset table.

Constraints:

- Do not redesign the entire persistence model merely to support presentation.
- Do not replicate `platform.db`.
- Do not revive remote-GPU orchestration.

---

## 20. V1.1 Non-Goals

Explicit non-goals:

```text
no flashy visual redesign
no new distributed scheduler
no return to Windows-driven remote GPU orchestration
no platform database synchronization between hosts
no replacement of BAPv1
no new second artifact package schema
no mandatory GPU on Windows
no arbitrary deletion of dataset member samples
no deletion of externally owned SpaceNet source files
no new Dataset table unless implementation analysis proves the projection
    insufficient
no forced 640x640 viewer
no frontend workaround that falsely marks an unavailable executor runnable
```

---

## 21. Development Structure

Four implementation tracks define the work structure and improve development
efficiency.

### Track UX-A — App Shell

```text
sidebar
navigation
guide
theme
preference persistence
workspace-route restoration
```

### Track UX-B — Data Library and Lifecycle

```text
dataset projection
dataset/sample browsing
filters/pagination/search
import action restructuring
deletion/unregister APIs and UI
analysis history surface
```

### Track UX-C — Analysis Workflows

```text
single-sample run flow
dataset experiment entry flow
pipeline comparison shortcuts
executor UX
local_cpu root-cause diagnosis/fix if required
```

### Track UX-D — Spectrogram Viewer

```text
explicit zoom controls
wheel behavior
overlays
legend
responsive/display geometry
```

Track boundaries:

- Tracks **A** and **D** should be independently implementable where possible.
- Track **B** may contain narrowly scoped backend API work.
- Track **C** may contain a backend execution-selection correction if the Local CPU
  diagnosis proves a backend defect.

---

## 22. Branching / Integration

The sealed V1 baseline remains immutable:

```text
integration/v1-candidate
067c6c020db241076718cc85d9716ce764ba09cb
tree 2a54007409f9e06899207ac1488cf046302bba9e
```

The documentation branch for this specification is:

```text
docs/v1-1-ux-productization
```

Later implementation branches should originate from the agreed V1.1 planning
baseline, not mutate the sealed historical feature branches.

Target future integration branch:

```text
integration/v1-1-ux-candidate
```

That branch is not created as part of this documentation task. No implementation
merge is part of this specification task.

---

## 23. Testing and Acceptance Philosophy

Existing engineering discipline is preserved:

```text
TDD for feature/bugfix implementation
focused tests during microchanges
full frontend test/build at track boundaries
Linux backend full regression at integration boundary
Windows native POSIX-only historical test limitations must not cause unrelated
    UX code to be rewritten
no GPU required for V1.1 UX implementation
no real-GPU E2E rerun unless implementation unexpectedly changes an
    inference/artifact contract
```

Acceptance must validate both:

```text
1. functional correctness
2. user workflow continuity
```

UX-oriented tests are added for:

```text
navigation persistence
dataset grouping
pagination/search
context-prefilled experiment creation
compare shortcut routing
theme preference
sidebar state
guide localization
deletion confirmation/conflict states
viewer zoom behavior
no wheel hijacking
light/dark overlay readability where practical
```

Deletion and dataset-identity acceptance coverage is required for:

```text
two independently registered dataset roots with identical display fields
    remain two distinct dataset projections
blocked standalone deletion when retained dependents exist
blocked dataset removal when any member has retained dependents
no partial dataset removal
successful deletion after dependencies permit it
external dataset source files unchanged after removal
```

---

## 24. Success Criteria

V1.1 succeeds when a user can understand and execute the primary workflows
without learning WISA's backend domain model first.

Specifically:

```text
1.  A 2500-sample dataset appears as one dataset at the root level.
2.  Dataset members are browsed inside that dataset.
3.  Standalone IQ samples remain independently manageable.
4.  Users can add data without confusing that action with importing analysis
    results.
5.  Users can remove/delete supported resources safely.
6.  A user can start sample analysis from a sample.
7.  A user can start dataset analysis from a dataset without retyping dataset
    identity.
8.  A user can compare two completed sample runs without manually entering IDs.
9.  Returning to Algorithm Lab restores the previous meaningful workspace.
10. Run-unavailable states explain why rather than presenting only a generic
    disabled button.
11. Local CPU availability is correctly diagnosed and, if defective, fixed at
    the real backend/runtime seam.
12. The spectrogram viewer no longer hijacks page wheel scrolling.
13. GT/prediction overlays are clearly distinguishable.
14. Light/dark/system theme works coherently.
15. A first-time user can use the in-app guide to understand the major
    workflows.
16. Independently registered dataset roots that share display fields
    (`dataset_name`, `dataset_split`) never collapse into one dataset; dataset
    routing uses a stable opaque projection identity rather than display fields.
17. Destructive operations fail closed with an explicit blocker when retained
    dependents exist, dataset removal is all-or-nothing, and external source
    files are never deleted.
```

---

## 25. Open Technical Follow-Ups

The following are explicit technical follow-ups. They are **not** unresolved
design decisions and do not reopen the approved UX direction.

```text
A. Verify whether the SpaceNet official dataset contract guarantees that
   complex-IQ sampling rate equals the observation bandwidth. Until verified,
   derived Fs/Fc remain labelled as platform-derived.

B. Diagnose the current local_cpu non-runnable state in the Windows deployment
   by identifying the actual failed predicate among technical / configured /
   certified / available, and fix it at the real backend/runtime seam if it is
   defective.

C. During implementation planning, determine whether existing list APIs are
   sufficient or require server-side dataset/analysis-history projections, and
   define the deterministic inputs and exact encoding of the opaque
   dataset_projection_id, including how the normalized dataset-root identity is
   derived from existing metadata without adding a persisted dataset table.

D. Define the exact dependency graph for safe deletion across standalone
   Recordings, dataset-member Recordings, and AnalysisRuns, including which
   retained dependents (DatasetEvaluation, benchmark, experiment provenance)
   cause a conflict and how the structured conflict response is shaped.

E. Determine whether spectrogram metadata should expose source raster width /
   height or another display-aspect hint so the viewer can honor real geometry.
```

---

## 26. Decisions Locked by This Spec

The following decisions are approved and should not be re-litigated during
implementation unless a concrete blocker is discovered:

```text
Recordings → Data Library user concept
Dataset / Standalone split
no root-level flattening of dataset members
Add Data separated from Import Analysis Results
contextual workflow entry
first-class Analysis History
safe lifecycle management
App Guide
collapsible sidebar
light/dark/system theme
URL-driven business state
Algorithm Lab workspace restoration
Auto-first execution UX
Local CPU diagnosed at the actual runtime/execution-selection seam
explicit viewer zoom controls
no wheel hijacking
improved GT/prediction overlays
no forced 640x640 display
four-track implementation structure
dataset projection identity is opaque and stable; display grouping is not identity
all destructive operations share one fail-closed dependency preflight; dataset
    removal is all-or-nothing and external files are never deleted
sealed V1 baseline remains immutable
```

---

## 27. Spec Self-Review Record

Performed before commit:

1. **Placeholders** — none. No placeholder tokens or vague deferral language are
   used. Forward-looking items are recorded as explicit technical follow-ups in
   Section 25.
2. **Contradictions** — none. Navigation, workflows, lifecycle, state rules, and
   tracks are mutually consistent.
3. **Ambiguous destructive-operation semantics** — resolved: standalone sample
   deletion removes WISA-managed representation and storage with explicit
   irreversible confirmation; dataset removal unregisters platform
   representation while explicitly preserving external source files; AnalysisRun
   deletion fails closed with a conflict when a retained dependent resource
   prevents it.
4. **Accidental reopening of Remote-GPU** — none. Remote-GPU orchestration remains
   historical/experimental; `Remote GPU` is presented as not enabled for the V1
   production workflow.
5. **Accidental introduction of a mandatory Dataset table** — none. V1.1 uses a
   projection over existing recording metadata; a persisted Dataset table is
   explicitly a non-goal unless implementation analysis proves the projection
   insufficient.
6. **Source vs derived metadata confusion** — resolved: Section 7 distinguishes
   source metadata (`observation_range`) from derived metadata (Fs/Fc) and
   requires derived values to be labelled as derived pending contract
   verification.
7. **Model input vs spectrogram raster vs browser display size** — resolved:
   Section 17.3 defines all three separately and forbids forcing the viewer to
   640x640.
8. **URL / localStorage / ephemeral state boundaries** — resolved: Section 12
   provides a normative classification table and forbids indiscriminate
   `localStorage` use and transient-state-only business workspace state.
9. **Contradictions with immutable V1 baseline** — none. This document is created
   from the sealed baseline SHA `067c6c02` and preserves its contracts; the
   baseline is neither moved nor modified.
10. **Dataset identity vs display grouping** — resolved: Section 4.1 separates
    friendly display fields from a stable opaque `dataset_projection_id`;
    `(dataset_name, dataset_split)` is no longer claimed to be a globally unique
    identity, and independently registered roots cannot collapse.
11. **Parent-resource deletion bypass** — resolved: Section 8.1 unifies the
    fail-closed dependency preflight across standalone Recording deletion,
    dataset removal, and AnalysisRun deletion; dataset removal is all-or-nothing
    and external source files are never deleted.
12. **Mandatory Dataset table** — still excluded. The identity amendment adds no
    persisted dataset table; the projection remains a read model over existing
    recording metadata.
