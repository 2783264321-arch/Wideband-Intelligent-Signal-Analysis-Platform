# WISA V1.1 UX Productization — Orchestration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Coordinate four independently reviewable implementation tracks
(UX-A App Shell, UX-B Data Library & Lifecycle, UX-C Analysis Workflows, UX-D
Spectrogram Viewer) into one accepted `integration/v1-1-ux-candidate` branch
without mutating the sealed V1 baseline.

**Architecture:** Tracks branch from the accepted planning baseline, are
implemented in isolated worktrees, reviewed per task, and integrated in a fixed
order that resolves only documented shared files. The four track plans contain
the implementation detail; this document contains only cross-track
coordination.

**Tech Stack:** React + TypeScript + Vite + Ant Design + Vitest (verified in
`frontend/package.json`); FastAPI + SQLAlchemy 2.0 + Pydantic v2 + pytest
(verified in `backend/pyproject.toml`).

**Spec:**
`docs/superpowers/specs/2026-09-16-v1-1-ux-productization-design.md`

---

## A. Planning Baseline

```text
planning baseline branch : docs/v1-1-ux-implementation-plans
planning baseline SHA    : (this branch's first commit parent)
                          = 0119349f51ed99c55b1d362ba04acec42658f0de
spec                     : docs/superpowers/specs/2026-09-16-v1-1-ux-productization-design.md
sealed V1 code baseline  : integration/v1-candidate
                           067c6c020db241076718cc85d9716ce764ba09cb
```

The planning baseline `0119349` contains the sealed V1 source tree **plus** the
approved V1.1 design documentation. All four implementation branches derive
from `0119349` unless a documented dependency requires an accepted upstream
track SHA (only UX-C does; see Dependency Graph).

The spec branch `docs/v1-1-ux-productization` and the sealed baseline
`integration/v1-candidate` are immutable. No plan task may commit to either.

---

## B. Implementation Branches

```text
feature/v1-1-ux-a-app-shell
feature/v1-1-ux-b-data-library-lifecycle
feature/v1-1-ux-c-analysis-workflows
feature/v1-1-ux-d-spectrogram-viewer
```

Target integration branch:

```text
integration/v1-1-ux-candidate
```

Rules:

- Each feature branch is created from `0119349` in its own worktree.
- UX-C may create a branch from `0119349` for its independent diagnostics
  (C0), then rebase onto the accepted UX-B SHA before dependent wiring (C1).
- Cherry-picks are not the normal integration mechanism. Integration uses
  ordered merges of accepted track branches into the integration branch.
- One documentation commit per plan is created on
  `docs/v1-1-ux-implementation-plans`; production code is not committed there.

---

## C. Dependency Graph

```text
                      0119349  (accepted planning baseline)
                         │
        ┌────────────────┼────────────────┬────────────────┐
        ▼                ▼                ▼                ▼
   UX-A shell       UX-D viewer      UX-B library      UX-C C0
   (independent)    (independent)    (independent)     (independent:
                                                       local_cpu diagnosis,
                                                       candidate-state UX)
        │                │                │                 │
        │                │                │                 │
        │                │                └───────┬─────────┘
        │                │                        ▼
        │                │                 UX-C C1 (dependent):
        │                │                 consumes A navigation/state contract
        │                │                 + B dataset projection/history APIs
        │                │                        │
        └────────────────┴────────────────────────┴──────────────┐
                                                                  ▼
                                       integration/v1-1-ux-candidate
```

Dependency edges:

```text
UX-A  → none
UX-D  → none
UX-B  → none (its own narrowly scoped backend APIs)
UX-C0 → none
UX-C1 → UX-A (workspace route memory + nav) and UX-B (dataset projection,
        analysis-history, prefilled experiment identity)
```

Hard rules:

- UX-C must not duplicate UX-B APIs. It consumes the UX-B client functions and
  types.
- UX-C must not implement a second dataset representation.
- UX-D and UX-A must remain implementable without waiting on backend work.
- UX-B's backend work must not wait on frontend tracks.

---

## D. Cross-Track File / Interface Ownership

This matrix is the authoritative conflict-resolution contract. "Owner" is the
single track permitted to make structural changes during parallel development.
"Integration rule" is applied when merging.

| File / path | UX-A | UX-B | UX-C | UX-D | Owner | Integration rule |
|---|---|---|---|---|---|---|
| `frontend/src/main.tsx` | edit | — | — | — | UX-A | A only |
| `frontend/src/app/App.tsx` | edit | edit | — | — | UX-A, then UX-B | Merge A first; B rebases and adds Data Library routes (additive) |
| `frontend/src/app/MainLayout.tsx` | edit | edit | — | — | UX-A, then UX-B | A owns shell; B adds one Data Library menu item after A (additive) |
| `frontend/src/app/App.test.tsx` | edit | edit | — | — | UX-A, then UX-B | A keeps `/recordings` default; B switches the default route to `/data-library` |
| `frontend/src/pages/AlgorithmLabPage.tsx` | edit | — | — | — | UX-A | A only (adds workspace-route memory write) |
| `frontend/src/app/ThemeProvider*` (new) | create | — | — | — | UX-A | new file |
| `frontend/src/pages/SettingsPage.tsx` (new) | create | — | — | — | UX-A | new file |
| `frontend/src/pages/UserGuidePage.tsx` (new) | create | — | — | — | UX-A | new file |
| `frontend/src/features/guide/*` (new) | create | — | — | — | UX-A | new file |
| `frontend/src/api/client.ts` | — | edit | — | edit | UX-B, then UX-D | D rebases onto B; D's addition is the single spectrogram-meta mapping |
| `frontend/src/api/types.ts` | — | edit | — | edit | UX-B, then UX-D | same rule |
| `frontend/src/api/client.test.ts` | — | edit | — | edit | UX-B, then UX-D | union of tests |
| `frontend/src/localization/messages.en-US.ts` | edit | edit | edit | edit | sequenced | Each track only ADDS keys; conflicts resolved by union. Order: A, D, B, C |
| `frontend/src/localization/messages.zh-CN.ts` | edit | edit | edit | edit | sequenced | same rule |
| `frontend/src/localization/glossary.ts` | edit | edit | — | — | sequenced | Order: A, then B |
| `frontend/src/localization/localization.acceptance.test.tsx` | edit | edit | — | — | sequenced | Order: A, then B |
| `frontend/src/app/navigation.test.tsx` | edit | — | — | — | UX-A | A only |
| `frontend/src/pages/*` (existing pages) | — | replace RecordingsPage | — | — | UX-B | B only |
| `frontend/src/features/spectrum/SpectrogramViewer.tsx` | — | — | — | edit | UX-D | D only |
| `frontend/src/features/spectrum/SpectrogramViewer.test.tsx` | — | — | — | edit | UX-D | D only |
| `frontend/src/features/execution-environment/*` | — | — | edit | — | UX-C | C only |
| `frontend/src/features/dataset-experiment/ExperimentCreateForm.tsx` | — | — | edit | — | UX-C | C only (adds optional prefill prop) |
| `frontend/src/features/data-library/*` (new) | — | create | edit (consume only) | — | UX-B | B creates; C imports |
| `frontend/src/pages/StandaloneSampleDetailPage.tsx` (new) | — | create | edit (one additive compare shortcut) | — | UX-B, then UX-C | B creates; C inserts the compare shortcut after B |
| `frontend/src/pages/ExperimentsPage.tsx` | — | — | edit | — | UX-C | C only (dataset prefill query param) |
| `frontend/src/features/evaluation/ExperimentComparePanel.tsx` | — | — | edit | — | UX-C | C only (URL preselection) |
| `backend/app/datasets/projection.py` (new) | — | create | — | — | UX-B | new file |
| `backend/app/data_library/*` (new) | — | create | — | — | UX-B | new file |
| `backend/app/lifecycle/*` (new) | — | create | — | — | UX-B | new file |
| `backend/app/main.py` | — | edit | — | — | UX-B | B registers new routers/lifecycle service |
| `backend/app/db/migrations.py` | — | — | — | — | (no plan edits) | no new tables/columns required |
| `backend/app/execution_selection/*` | — | — | edit (only if C1 diagnosis proves defect) | — | UX-C | C only, gated by diagnosis outcome |

Rules of engagement:

- A track never edits a file owned by another track during parallel development.
- Shared central files (`client.ts`, `types.ts`, localization resources) are
  additive-only; the integration lead applies the ordered rule above and
  verifies a union merge (no deleted baseline key, no reordered contract).
- No track introduces a large architectural refactor to avoid conflicts. The
  existing central API client and central localization files are preserved.

---

## E. Parallel Execution Rules

Phase 1 (fully parallel, four worktrees):

```text
UX-A  start immediately from 0119349
UX-B  start immediately from 0119349
UX-D  start immediately from 0119349
UX-C0 start immediately from 0119349 (diagnosis + candidate-state UX)
```

Phase 2 (after Phase 1 merges exist on the integration branch):

```text
UX-C1 branch/rebase onto accepted UX-A + UX-B SHAs; implement dependent wiring
```

Rules:

- Each track keeps its branch rebased only at its documented dependency points.
- Each task ends with a focused test run and a commit using the message in the
  track plan.
- Independent review happens after each meaningful task, before the next task in
  the same track.
- No track runs GPU inference; no track modifies tests merely to satisfy
  POSIX-only historical failures.
- No track modifies the sealed baseline or the spec.

---

## F. Track Acceptance Gates

```text
UX-A gate
  npm test -- --run          (all frontend tests, from frontend/)
  npm run build              (tsc -b && vite build, from frontend/)
  manual: sidebar collapse, theme system/light/dark, guide locale switch,
          Algorithm Lab leave-and-return restoration

UX-D gate
  npx vitest run src/features/spectrum   (focused viewer tests)
  npm test -- --run
  npm run build

UX-B gate
  focused backend:  pytest backend/tests/test_data_library_projection.py -v
                    pytest backend/tests/test_data_library_api.py -v
                    pytest backend/tests/test_lifecycle_deletion.py -v
  focused frontend: npx vitest run src/features/data-library
  track boundary:   npm test -- --run ; npm run build
  (Linux full backend regression is an integration-boundary gate, not a
   per-task gate)

UX-C gate
  focused backend (only if backend changed):
                    pytest backend/tests/test_execution_selection_resolver.py -v
                    pytest backend/tests/test_executor_selection_api.py -v
  focused frontend: npx vitest run src/features/execution-environment
                    npx vitest run src/features/data-library
                    npx vitest run src/features/dataset-experiment
  track boundary:   npm test -- --run ; npm run build
```

---

## G. Integration Order

```text
1.  accepted planning baseline (0119349)
2.  create integration/v1-1-ux-candidate from 0119349
3.  merge UX-A  → integration/v1-1-ux-candidate
4.  merge UX-D  → integration/v1-1-ux-candidate   (rebased onto A; resolve
                                                    client.ts/types.ts additions)
5.  merge UX-B  → integration/v1-1-ux-candidate   (rebased onto A+D; union
                                                    the localization resources and
                                                    client/types additions, then
                                                    add Data Library routes)
6.  rebase UX-C onto the post-B integration SHA; merge UX-C
7.  resolve only documented shared-file integration points (section D)
8.  full frontend regression:  npm test -- --run
9.  frontend production build: npm run build
10. full Linux backend regression: pytest backend/tests -v
11. ML-free control-plane verification: start the backend control plane with no
    GPU available and confirm import/startup succeeds and health responds
    (no inference execution)
12. targeted end-to-end UX acceptance (section H)
```

No GPU is used in any integration step. The sealed `integration/v1-candidate`
is never merged into and never receives a commit.

Shared-file integration points (only these require manual conflict resolution):

```text
frontend/src/app/App.tsx
frontend/src/api/client.ts
frontend/src/api/types.ts
frontend/src/api/client.test.ts
frontend/src/localization/messages.en-US.ts
frontend/src/localization/messages.zh-CN.ts
frontend/src/localization/glossary.ts
frontend/src/localization/localization.acceptance.test.tsx
```

Resolution rule for the localization resources and client/types: keep the union
of both sides' added keys/functions/fields; never delete a baseline symbol;
never change an existing baseline value.

---

## H. Final Integration Acceptance

The integration branch is accepted when all of the following end-to-end
scenarios pass (manual or automated), with no GPU:

```text
1.  Data Library shows one 2500-sample SpaceNet dataset, not 2500 root cards.
2.  Two independently registered roots with identical display fields appear as
    two distinct dataset projections.
3.  Dataset sample browsing paginates and searches server-side.
4.  A standalone sample remains separately manageable and browsable.
5.  Add Data is visually and semantically separate from Import Analysis Results.
6.  Dataset experiment creation is prefilled from the dataset page with no
    manual dataset identity entry.
7.  A single sample can be analyzed end-to-end (Analyze → pipeline → Auto →
    run → spectrogram + detections).
8.  Two completed sample runs can be compared via a shortcut without manual ID
    entry, routing to Algorithm Lab with recording/runA/runB.
9.  Algorithm Lab state survives sidebar navigation (leave and return restores
    the previous recording/run comparison and rehydrates it).
10. No-runnable-executor states show candidate-level safe explanations, not only
    a generic disabled button.
11. Local CPU behavior matches the recorded diagnosis outcome (section C of the
    UX-C plan).
12. Theme system/light/dark works coherently and persists.
13. Collapsible sidebar state persists across reload.
14. The User Guide renders in zh-CN and en-US following the app locale.
15. The spectrogram viewer does not hijack page wheel scrolling.
16. The viewer provides explicit zoom out / percentage / zoom in / fit / reset.
17. GT and prediction overlays are visually distinct and a legend is visible.
18. A standalone sample deletes safely after confirmation; retained dependents
    block deletion with an explicit conflict; external files are unchanged.
19. Dataset removal is atomic: a blocked member prevents all removal; otherwise
    removal succeeds and external SpaceNet files are untouched.
20. AnalysisRun deletion fails closed when referenced and succeeds otherwise.
```

---

## I. Follow-Up Resolution Map

```text
Spec follow-up A (SpaceNet Fs derivation contract)
  → UX-B bounded investigation task B0; must not block any other task.

Spec follow-up B (local_cpu non-runnable diagnosis)
  → UX-C task C1; evidence-driven, outcome recorded.

Spec follow-up C (dataset projection/API design)
  → UX-B tasks B1–B5; concrete schemas defined in the UX-B plan.

Spec follow-up D (safe deletion dependency graph)
  → UX-B task B6; concrete blockers and 409 schema defined in the UX-B plan.

Spec follow-up E (spectrogram geometry metadata)
  → UX-D task D5; resolved via intrinsic image geometry, no backend contract
    change.
```

---

## J. Scope Boundaries (all tracks)

```text
no Remote-GPU production workflow
no mandatory dataset persistence table
no platform.db replication
no BAPv2 or second artifact schema
no GPU requirement on Windows
no frontend force-enable of unavailable executors
no modification of the sealed V1 baseline or the approved spec
```

---

## K. Spec Coverage Matrix

Every normative section of the approved spec maps to a plan or to final
integration acceptance. No requirement is orphaned.

| Spec section | Covered by |
|---|---|
| 1 Context and problem | Orchestration A–C; all track goals |
| 2 Design principles | Global constraints in every track plan |
| 3 Top-level IA | UX-A tasks A4, A6 |
| 4.1 Dataset projection | UX-B B1, B2 |
| 4.2 Dataset list | UX-B B2, B8 |
| 4.3 Dataset detail | UX-B B3, B4, B9 |
| 4.4 Standalone sample list | UX-B B3, B8, B10 |
| 5 Add Data vs Import Results | UX-B B11 |
| 6 Standalone IQ import | UX-B B11 |
| 7 SpaceNet metadata semantics | UX-B B0, B3, B9 |
| 8.1 Dependency guard | UX-B B5, B6 |
| 8.2 Standalone deletion | UX-B B6, B10 |
| 8.3 Dataset removal | UX-B B6, B9 |
| 8.4 AnalysisRun deletion | UX-B B6 |
| 9.1 Sample → one pipeline | UX-C C3 |
| 9.2 Sample → comparison | UX-C C4 |
| 9.3 Dataset → one pipeline | UX-C C5 |
| 9.4 Dataset → evaluation | UX-C C6 |
| 10 Execution environment UX | UX-C C1, C2; integration H10, H11 |
| 11 Algorithm Lab persistence | UX-A A5 |
| 12 Global state rule | UX-A A2, A3, A5; URL usage in UX-B/UX-C |
| 13 App shell | UX-A A3, A4, A6 |
| 14 Theme | UX-A A2, A6, A8; UX-D D5 |
| 15 User Guide | UX-A A7 |
| 16 Page copy | UX-A A6/A8; UX-B B8/B10 (title, purpose, primary action) |
| 17 Spectrogram viewer | UX-D D1–D5 |
| 18 Analysis history | UX-B B4, B9, B10; UX-C C4, C6 |
| 19 Backend API changes | UX-B B2–B7 |
| 20 Non-goals | Orchestration J; track global constraints |
| 21 Development structure | Orchestration B, C, E |
| 22 Branching / integration | Orchestration A, B, G |
| 23 Testing and acceptance philosophy | Orchestration F, G; track gates |
| 24 Success criteria | Orchestration H |
| 25 Follow-ups A–E | Orchestration I; UX-B B0/B1–B6; UX-C C1; UX-D D4 |
| 26 Decisions locked | Track global constraints |

Technical follow-up coverage:

```text
A SpaceNet Fs derivation contract → UX-B B0 (bounded, non-blocking)
B Local CPU non-runnable diagnosis → UX-C C1 (evidence-driven, outcome recorded)
C dataset projection/API design      → UX-B B1–B5
D safe deletion dependency graph     → UX-B B5, B6
E spectrogram geometry metadata      → UX-D D4 (intrinsic image strategy; resolved
                                       without a backend contract change)
```
