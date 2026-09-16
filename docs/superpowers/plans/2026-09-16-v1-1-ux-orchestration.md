# WISA V1.1 UX Productization — Orchestration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Coordinate four independently reviewable implementation tracks
(UX-A App Shell, UX-B Data Library & Lifecycle, UX-C Analysis Workflows, UX-D
Spectrogram Viewer) into one accepted `integration/v1-1-ux-candidate` branch
without mutating the sealed V1 baseline and without rewriting any reviewed SHA.

**Architecture:** All implementation branches start from the final accepted
planning SHA (which carries the sealed V1 source, the frozen UX spec, and these
final plans). Accepted tracks are combined by ordinary merge ancestry, never by
rebase. Dataset flows are projection-authoritative end to end.

**Tech Stack:** React + TypeScript + Vite + Ant Design + Vitest; FastAPI +
SQLAlchemy 2.0 + Pydantic v2 + pytest; the `wisa` CLI operator workflow.

**Spec:**
`docs/superpowers/specs/2026-09-16-v1-1-ux-productization-design.md`

---

## A. Planning Baseline

```text
sealed V1 code baseline : integration/v1-candidate
                          067c6c020db241076718cc85d9716ce764ba09cb
frozen UX spec          : docs/v1-1-ux-productization
                          0119349f51ed99c55b1d362ba04acec42658f0de
planning documents      : docs/v1-1-ux-implementation-plans
final planning baseline : FINAL_ACCEPTED_PLANNING_SHA
                          (the final head of docs/v1-1-ux-implementation-plans
                           after this amendment is accepted; supplied by review)
```

`FINAL_ACCEPTED_PLANNING_SHA` is symbolic until review publishes it. Every
initial implementation worktree MUST be created from that exact SHA so that a
single commit tree contains the sealed V1 source, the frozen spec, and the final
implementation plans.

The spec branch and `integration/v1-candidate` are immutable and receive no
commits.

---

## B. Implementation Branches and the No-Rebase Rule

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

- Each branch is created from `FINAL_ACCEPTED_PLANNING_SHA` in its own worktree.
- **Once a track SHA has passed review, it is never rewritten.** No rebase of
  accepted commits. Dependencies are incorporated with ordinary merge commits,
  so accepted provenance remains immutable.
- UX-C0 (independent diagnosis/UX) may be reviewed and frozen; UX-C1 dependent
  wiring merges the accepted UX-A and UX-B into the UX-C branch with a merge
  commit, then adds its own commits on top.
- UX-B's route wiring (Task B17) requires the accepted UX-A; UX-B merges the
  accepted UX-A SHA into its branch with a normal merge commit before B17.
- Cherry-picks are not the normal integration mechanism.
- No track modifies the sealed baseline or the spec.

---

## C. Dependency Graph

```text
            FINAL_ACCEPTED_PLANNING_SHA
                       │
   ┌───────────────────┼────────────────────┬──────────────────┐
   ▼                   ▼                    ▼                  ▼
UX-A shell         UX-D viewer         UX-B data/backend    UX-C0
(independent)      (independent)       (independent core)   (independent:
                                                             local_cpu
                                                             qualification +
                                                             candidate UX)
   │                   │                    │                  │
   │                   │                    │  merge A         │
   │                   │                    ▼                  │
   │                   │            UX-B frontend wiring       │
   │                   │            (routes; not a rebase)     │
   │                   │                    │                  │
   │                   │                    └────────┬─────────┘
   │                   │                             │ merge A + B
   │                   │                             ▼
   │                   │                      UX-C1 workflow wiring
   └───────────────────┴─────────────────────────────┘
                         │
                         ▼
              integration/v1-1-ux-candidate
```

Dependency edges:

```text
UX-A  → none
UX-D  → none
UX-B  → none for backend core; UX-A only for B17 route wiring (merge)
UX-C0 → none
UX-C1 → UX-A (shell/nav) + UX-B (projection/history contracts), via merge
```

Hard rules:

- UX-C must not duplicate UX-B APIs and must not create a second dataset
  representation.
- No track may depend on a rewritten SHA.
- UX-D and UX-A must remain implementable without waiting on backend work.

---

## D. Cross-Track File / Interface Ownership

| File / path | UX-A | UX-B | UX-C | UX-D | Ownership | Rule |
|---|---|---|---|---|---|---|
| `frontend/src/main.tsx` | edit | — | — | — | UX-A | A only |
| `frontend/src/app/App.tsx` | edit | edit | — | — | UX-A, then UX-B | A adds `/guide`,`/settings`; B merges A then adds Data Library routes |
| `frontend/src/app/MainLayout.tsx` | edit | — | — | — | UX-A | A only; A creates the Data Library nav item. B never edits it |
| `frontend/src/app/App.test.tsx` | edit | edit | — | — | UX-A, then UX-B | A keeps `/recordings` default; B switches default to `/data-library` |
| `frontend/src/pages/AlgorithmLabPage.tsx` | edit | — | — | — | UX-A | A only (workspace-route memory) |
| `frontend/src/api/client.ts` | — | edit | — | — | UX-B | B only; D adds nothing |
| `frontend/src/api/types.ts` | — | edit | — | — | UX-B | B only; D adds nothing |
| `frontend/src/api/client.test.ts` | — | edit | — | — | UX-B | B only |
| `frontend/src/features/spectrum/SpectrogramViewer.tsx` | — | — | — | edit | UX-D | D only |
| `frontend/src/features/spectrum/SpectrogramViewer.test.tsx` | — | — | — | edit | UX-D | D only |
| `frontend/src/features/execution-environment/*` | — | — | edit | — | UX-C | C only |
| `frontend/src/features/dataset-experiment/ExperimentCreateForm.tsx` | — | — | edit | — | UX-C | C only |
| `frontend/src/pages/ExperimentsPage.tsx` | — | — | edit | — | UX-C | C only |
| `frontend/src/features/evaluation/ExperimentComparePanel.tsx` | — | — | edit | — | UX-C | C only |
| `frontend/src/features/data-library/*` (new) | — | create | edit (additive) | — | UX-B, then UX-C | B creates; C adds compare entry/actions after merge |
| `frontend/src/pages/DataLibraryPage.tsx` | — | create | edit (additive Analyze) | — | UX-B, then UX-C | B creates; C adds the Analyze primary action |
| `frontend/src/pages/DatasetDetailPage.tsx` | — | create | — | — | UX-B | B only |
| `frontend/src/pages/StandaloneSampleDetailPage.tsx` | — | create | edit (additive compare) | — | UX-B, then UX-C | B creates; C adds the GT-gated compare shortcut |
| `frontend/src/localization/messages.en-US.ts` | edit | edit | edit | edit | sequenced | Additive ordered union; keep `nav.recordings`; order A→D→B→C |
| `frontend/src/localization/messages.zh-CN.ts` | edit | edit | edit | edit | sequenced | same rule |
| `frontend/src/localization/glossary.ts` | edit | edit | — | — | sequenced | A then B |
| `frontend/src/localization/localization.acceptance.test.tsx` | edit | edit | — | — | sequenced | A then B |
| `frontend/src/app/navigation.test.tsx` | edit | — | — | — | UX-A | A only |
| `backend/app/datasets/projection.py` (new) | — | create | — | — | UX-B | new file |
| `backend/app/data_library/*` (new) | — | create | — | — | UX-B | new file |
| `backend/app/lifecycle/*` (new) | — | create | — | — | UX-B | new file |
| `backend/app/benchmarks/*`, `dataset_experiments/*`, `execution_selection/router.py`, `storage/service.py`, `db/migrations.py`, `main.py`, `recordings/router.py`, `analysis/router.py` | — | edit | edit (only if C1 proves a defect) | — | UX-B; UX-C gated | C may touch `execution_selection/resolver.py` / `analysis/local_executor.py` only for a diagnosed defect |

Central-file merge rule (localization resources):

```text
Every track only ADDS keys. Never delete or change a baseline localization key.
`nav.recordings` is retained as an unused compatibility key for V1.1.
Conflicts in the central message files are resolved by union of additions.
Merge order for those files is A → D → B → C.
```

---

## E. Parallel Execution Rules

Phase 1 (fully parallel, four worktrees from `FINAL_ACCEPTED_PLANNING_SHA`):

```text
UX-A   start immediately
UX-D   start immediately
UX-B   start immediately (backend core + Data Library components)
UX-C0  start immediately (Local CPU qualification + candidate-state UX)
```

Phase 2:

```text
UX-B frontend route wiring (B17): merge accepted UX-A, then wire routes.
UX-C1 dependent wiring: merge accepted UX-A + accepted UX-B, then implement.
```

Rules:

- Each task ends with a focused test run and a commit using the message in the
  track plan.
- Independent review happens after each meaningful task and before the next.
- No track runs GPU inference; no track modifies tests merely to satisfy
  POSIX-only historical failures; no accepted SHA is rewritten.

---

## F. Track Acceptance Gates

```text
UX-A gate
  npm test -- --run ; npm run build
  manual: sidebar collapse, theme system/light/dark (no fixed light shell),
          guide locale switch, Algorithm Lab leave-and-return restoration

UX-D gate
  npx vitest run src/features/spectrum ; npm test -- --run ; npm run build

UX-B gate
  focused backend:  pytest backend/tests/test_dataset_projection.py
                            backend/tests/test_dataset_projection_authority.py
                            backend/tests/test_data_library_api.py
                            backend/tests/test_lifecycle_preflight.py
                            backend/tests/test_lifecycle_deletion_api.py -v
  focused frontend: npx vitest run src/features/data-library
  track boundary:   npm test -- --run ; npm run build
  (Linux full backend regression is an integration-boundary gate only)

UX-C gate
  focused backend (only if C1 changed backend code):
                    pytest backend/tests/test_local_cpu_diagnosis.py
                            backend/tests/test_execution_selection_resolver.py
                            backend/tests/test_executor_selection_api.py -v
  plus the Local CPU acceptance checklist (real qualified CPU executor + one
  CPU-only AnalysisRun smoke, or an explicit STOP blocker)
  focused frontend: npx vitest run src/features/execution-environment
                    npx vitest run src/features/algorithm-lab
                    npx vitest run src/features/dataset-experiment
                    npx vitest run src/pages
  track boundary:   npm test -- --run ; npm run build
```

---

## G. Integration Order

```text
1.  FINAL_ACCEPTED_PLANNING_SHA
2.  create integration/v1-1-ux-candidate from FINAL_ACCEPTED_PLANNING_SHA
3.  merge accepted UX-A  -> integration/v1-1-ux-candidate   (ordinary merge)
4.  merge accepted UX-D  -> integration/v1-1-ux-candidate   (ordinary merge;
                                                             no client/type change)
5.  UX-B merges accepted UX-A into its branch, wires routes (B17), and is
    reviewed; then merge accepted UX-B -> integration/v1-1-ux-candidate
6.  UX-C merges accepted UX-A + UX-B into its branch; implements C1 wiring; is
    reviewed; then merge accepted UX-C -> integration/v1-1-ux-candidate
7.  resolve only the documented central-file integration points (union merge)
8.  full frontend regression:  npm test -- --run
9.  frontend production build: npm run build
10. full Linux backend regression: pytest backend/tests -v
11. ML-free control-plane verification: start the backend control plane with no
    GPU, confirm startup/health succeed (no inference execution)
12. targeted end-to-end UX acceptance (section H)
```

No GPU is used. No accepted SHA is rewritten. The sealed
`integration/v1-candidate` is never merged into and never receives a commit.

Central-file integration points (only these need manual resolution):

```text
frontend/src/app/App.tsx
frontend/src/app/App.test.tsx
frontend/src/localization/messages.en-US.ts
frontend/src/localization/messages.zh-CN.ts
frontend/src/localization/glossary.ts
frontend/src/localization/localization.acceptance.test.tsx
```

Resolution rule: keep the union of both sides' additive changes; never delete a
baseline symbol or change a baseline value.

---

## H. Final Integration Acceptance

The integration branch is accepted when all of the following pass, with no GPU:

```text
1.  Data Library shows one 2500-sample SpaceNet dataset, not 2500 root cards.
2.  Two independently registered roots with identical display fields appear as
    two distinct dataset projections.
3.  Executor selection for root A classifies using only root A members.
4.  An experiment created from root A contains zero root-B recordings and
    revalidates against root A.
5.  A dataset evaluation from root A includes only root A members.
6.  An imported batch mapping to root A resolves against root A, never root B.
7.  Dataset sample browsing paginates and searches server-side.
8.  Dataset Analysis History shows an imported BAPv1 batch before any evaluation.
9.  A standalone sample remains separately manageable.
10. Add Data is semantically separate from Import Analysis Results.
11. Dataset experiment creation is prefilled from the projection; no retyping.
12. Single sample analysis works end to end with Auto default.
13. Two completed sample runs compare without manual ID entry.
14. Algorithm Lab state survives sidebar navigation.
15. No-runnable-executor states show candidate-level explanations.
16. Local CPU matches the accepted qualification outcome (certified + available
    + one CPU-only smoke) or an explicit STOP blocker is reported.
17. Theme system/light/dark applies (no fixed light shell) and persists.
18. Collapsible sidebar state persists; guide renders bilingual.
19. Viewer does not hijack page scroll; explicit zoom out/level/in/fit/reset.
20. GT and prediction overlays are distinguishable with a legend.
21. Standalone deletion is safe; retained dependents block it explicitly;
    external files unchanged; managed files cleaned.
22. Dataset removal is atomic; external SpaceNet files untouched.
23. Individual imported-batch run deletion is blocked; complete-set removal works.
```

---

## I. Follow-Up Resolution Map

```text
A SpaceNet Fs derivation contract → UX-B B0 (bounded; not falsely marked
    verified; derived flags stay true unless an authoritative source is found)
B Local CPU non-runnable diagnosis → UX-C C1 (full qualification + smoke)
C dataset projection/API design      → UX-B B1–B4, B6–B9, B12
D safe deletion dependency graph     → UX-B B10, B11 (incl. imported_batch and
                                       managed-file lifecycle)
E spectrogram geometry metadata      → UX-D D4 (intrinsic image; no backend change)
```

---

## J. Scope Boundaries (all tracks)

```text
no Remote-GPU production workflow
no dataset persistence table (two additive nullable columns only)
no platform.db replication
no BAPv2 or second artifact schema
no GPU requirement on Windows
no frontend force-enable of unavailable executors
no rebase of accepted track SHAs
no modification of the sealed V1 baseline or the approved spec
```

---

## K. Projection Authority Propagation

The opaque `dataset_projection_id` is the single dataset identity through every
V1.1 contextual flow:

```text
Data Library
  DatasetProjectionResolver -> dataset_projection_id
        │
        ├── /api/executor-selection (dataset_projection_id scope) -> probe + count
        ├── DatasetExperiment (dataset_projection_id persisted + revalidated)
        ├── DatasetEvaluation (dataset_projection_id persisted + scoped)
        └── imported-batch resolution (derived projection; single-projection rule)
```

Legacy `dataset_name / dataset_split / label_space` contracts remain only for
historical rows and API callers; V1.1 contextual flows never use the triple to
resolve membership.

---

## L. Spec Coverage Matrix

| Spec section | Covered by |
|---|---|
| 1 Context and problem | Orchestration A–C; all track goals |
| 2 Design principles | Global constraints in every track plan |
| 3 Top-level IA | UX-A A4, A6 |
| 4.1 Dataset projection | UX-B B1, B2 |
| 4.2 Dataset list | UX-B B2, B13 |
| 4.3 Dataset detail | UX-B B4, B5, B14 |
| 4.4 Standalone sample list | UX-B B4, B13, B15 |
| 5 Add Data vs Import Results | UX-B B16 |
| 6 Standalone IQ import | UX-B B16 |
| 7 SpaceNet metadata semantics | UX-B B0, B4, B14 |
| 8.1 Dependency guard | UX-B B10, B11 |
| 8.2 Standalone deletion | UX-B B11, B15 |
| 8.3 Dataset removal | UX-B B11, B14 |
| 8.4 AnalysisRun deletion | UX-B B11 |
| 9.1 Sample → one pipeline | UX-C C3 |
| 9.2 Sample → comparison | UX-C C4 |
| 9.3 Dataset → one pipeline | UX-C C5 |
| 9.4 Dataset → evaluation | UX-C C6 |
| 10 Execution environment UX | UX-C C1, C2; integration H15, H16 |
| 11 Algorithm Lab persistence | UX-A A5 |
| 12 Global state rule | UX-A A2, A3, A5; URL usage throughout |
| 13 App shell | UX-A A3, A4, A6 |
| 14 Theme | UX-A A2, A6, A8; UX-D D5 |
| 15 User Guide | UX-A A7 |
| 16 Page copy | UX-A A6/A8; UX-B B13/B15 |
| 17 Spectrogram viewer | UX-D D1–D5 |
| 18 Analysis history | UX-B B5, B14, B15; UX-C C4, C6 |
| 19 Backend API changes | UX-B B3–B12 |
| 20 Non-goals | Orchestration J; track global constraints |
| 21 Development structure | Orchestration B, C, E |
| 22 Branching / integration | Orchestration A, B, G |
| 23 Testing and acceptance philosophy | Orchestration F, G; track gates |
| 24 Success criteria | Orchestration H |
| 25 Follow-ups A–E | Orchestration I |
| 26 Decisions locked | Track global constraints |
