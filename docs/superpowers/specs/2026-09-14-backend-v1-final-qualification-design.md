# Backend V1 Final Qualification — Design Specification

Date: 2026-09-14
Status: DESIGN ONLY (no implementation, no model execution, no production change)
Base: `feature/bhq-local-gpu @ 173d4413321fb62b70290467552549e824120a41` (BHQ-3 final seal)
Sealed Phase-G reference: `feature/m9-2-implementation @ c809eb02a5286737819136f9391d13949e8a117b` (do not modify)

---

## Executive Goal

Finish and seal the backend for V1 while the RTX 5090 server is still rented, by
(a) concentrating every genuinely CUDA-dependent validation into one GPU phase,
(b) proving the three principal product workflows end-to-end through production
seams, (c) closing the known `local_gpu` recovery gap, and (d) sealing the
remaining CPU-only/API behavior after the server is switched to no-GPU mode.
The backend is not V1-sealed merely because pytest is green: V1 requires real
evidence for the application workflows plus explicit fail-closed behavior.

Economic constraint: GPU server ≈ 2.88 RMB/hour vs no-GPU ≈ 0.10 RMB/hour. This
spec minimizes GPU wall time by reserving it only for real CUDA behavior; all
other qualification is CPU-only. A brute-force "2500 recordings × multiple models"
plan is explicitly rejected as ~50× more GPU cost for no additional acceptance
value (see Cost-Aware GPU Budget).

## Current Accepted Baseline

```text
BHQ-3 seal SHA:              173d4413321fb62b70290467552549e824120a41
BHQ-3 established:
  local_gpu normal production path   QUALIFIED
  CPN / ZoomSpec real CUDA           QUALIFIED
  temp-cert acceptance               QUALIFIED
  direct-science parity              EXACT (13/13, 17/17, 11/11, 13/13)
  production AnalysisRun local_gpu   QUALIFIED (CPN 13, ZoomSpec 11)
  execution certificates             QUALIFIED (6 entries)
  full backend regression            1759 passed / 29 skipped / 0 failed / 0 errors
  control-plane .venv ML-free        QUALIFIED
Known limitation:
  local_gpu crash/startup recovery   NOT qualified (deferred from BHQ-3)
```

Verification performed for this spec (read-only): branch/HEAD/worktree clean at
the base SHA; sealed Phase-G branch unchanged; 147 backend test files; the
executor/certificate/plugin/API surface audited at source level.

## Product Workflows

**Scenario A — Single Recording Inference.**
`Recording → choose Plugin/ModelRelease/Executor → AnalysisRun → real inference →
detections/classifications/provenance`. Production seams: `POST /api/analysis-runs`
→ `AnalysisService.create_run` → `ExecutorRegistry` → `LocalInferenceWorkerProvider`
→ `app.analysis.local_inference_worker` → `PluginHandle.load_runtime` →
`AnalysisResultWriter`; readback via `GET /api/analysis-runs/{id}` and
`GET /api/analysis-runs/{id}/detections`.

**Scenario B — Dataset → One Model.**
`frozen Dataset → one Plugin/ModelRelease → DatasetExperiment → N AnalysisRuns →
DatasetEvaluation → metrics/coverage/provenance`. Production seams:
`POST /api/dataset-experiments`, `POST /api/dataset-experiments/{id}/run`,
`app.dataset_experiments.coordinator` (subprocess), `start_item_attempt` /
`launch_item_attempt`, `reconcile_items`, `start_linked_evaluation` →
`app.benchmarks.worker`; readback via `GET /api/dataset-experiments/{id}`,
`.../items`, `.../items/{item}/attempts`, `GET /api/dataset-benchmarks/{eval}`.

**Scenario C — Same Dataset → Different Models.**
One frozen dataset membership; Experiment A = CPN golden, Experiment B = ZoomSpec
golden; separate runs/evaluations; comparison via
`POST /api/dataset-benchmarks/compare` and, per recording,
`GET /api/dataset-benchmarks/{eval}/items` +
`POST /api/algorithm-lab/compare`.

## Existing Coverage Matrix

Classification: `ALREADY_UNIT_TESTED` (U), `ALREADY_INTEGRATION_TESTED` (I),
`ALREADY_REAL_GPU_QUALIFIED` (G), `NEEDS_NEW_CPU_TEST` (NC),
`NEEDS_NEW_GPU_TEST` (NG), `NEEDS_PRODUCTION_FIX` (F), `OUT_OF_V1_SCOPE` (O).

| Area | Coverage | Evidence |
|---|---|---|
| AnalysisRun prepare/launch/lifecycle | U/I | `test_analysis_prepare_local.py`, `test_analysis_launch_prepared_run.py`, `test_analysis_create_run_compat.py`, `test_analysis_result_writer.py` |
| AnalysisRun local worker (CPU/GPU generic) | U/I | `test_local_inference_worker.py` |
| local_gpu provider probe/descriptor | U/I | `test_local_worker_provider.py` |
| local_gpu real CUDA science (CPN/ZoomSpec) | G | BHQ-3 C6/C8 evidence (`m9_2_bhq3_*`) |
| local_gpu positive production AnalysisRun | G | BHQ-3 C8 runs `run_45f5…`, `run_0bee…` |
| Executor certificate store + projection | U/I | `test_execution_certificate.py`, `test_executor_registry.py`, `test_executor_cutover.py`, `test_pipeline_read_model.py`, `test_local_gpu_certificates_exact.py` |
| Plugin registry / ModelRelease / assets | U/I | `test_plugin_registry.py`, `test_model_release.py`, `test_release_wiring.py`, `test_asset_manifest_v1_frozen.py` |
| Recording/SpaceNet ingestion + readback | U/I | `test_recordings.py`, `test_spacenet_*`, `test_dataset_adapter.py`, `test_input_output_label_space.py` |
| DatasetExperiment creation/attempts/launch | U/I | `test_dataset_experiment_creation.py`, `..._attempt.py`, `..._launch.py`, `..._models.py` |
| DatasetExperiment coordinator/fencing | U/I | `..._coordinator_*.py`, `..._generation_fence.py` |
| DatasetExperiment local_cpu recovery | U/I | `test_dataset_experiment_local_launch_recovery.py`, `test_remote_startup_recovery.py` |
| DatasetBenchmark/DatasetEvaluation science | U/I | `test_benchmark_*.py`, `test_evaluation_*.py`, `test_dataset_metrics.py` |
| Algorithm Lab compare | U/I | `test_algorithm_lab_compare.py` |
| Imported Runs (single + batch) | U/I | `test_imported_runs.py`, `test_batch_*.py` |
| **local_gpu recovery (single + experiment)** | **F + NG/NC** | **no test exists; code is local_cpu-scoped** |
| **Real DatasetExperiment end-to-end (any executor)** | **NG/NC** | no test runs a real multi-item experiment through a real worker |
| **Real concurrency=2 with two worker processes** | **NG** | `test_dataset_experiment_coordinator_scheduling.py` uses an in-process fake provider |
| **Real multi-model dataset comparison** | **NG** | aggregate `compare` unit-tested; no real two-model dataset run |
| **No-GPU startup seal (execution unavailable, read APIs live)** | **NC** | not asserted as a product behavior |
| remote_gpu / two-host | O | see Remote-GPU / Two-Host Scope Decision |

## Known Gaps

**G1 (P1, production fix required) — single-run local_gpu startup recovery.**
`app/remote_execution/recovery.py:22-37` `mark_stale_local_cpu_runs_interrupted`
interrupts only `status="running" AND executor="local_cpu"`; `remote_gpu`
orphans are handled by `find_orphaned_remote_runs` (line 40-46). A `local_gpu`
run whose worker died is in neither set and remains `running` forever.
`app/main.py:143` calls this helper at startup.

**G2 (P1, production fix required) — DatasetExperiment local_gpu recovery.**
`app/dataset_experiments/recovery.py:155-214` `repair_local_pending_runs`
skips any run with `run.executor != "local_cpu"` (line 176); the coordinator
(`app/dataset_experiments/coordinator.py`) has no executor branch, so a
`local_gpu` experiment left with a `running` item + `pending` run after a crash
is never relaunched and never fail-closed; the experiment can stall `running`
forever. (A `local_gpu` item whose run is `running` is first terminalized by
the G1 fix at startup, then projected to `failed` by `reconcile_items`
`service.py:853-862`.)

**G3 — no real multi-item / multi-model / concurrency=2 / endurance evidence.**
All coordinator scheduling tests use an in-process fake provider
(`backend/tests/executor_fixtures.py`); no test launches two real worker
processes.

**G4 — multi-model comparability semantics.** `POST /api/dataset-benchmarks/compare`
(`app/benchmarks/service.py:800-852`) does not require matching `pipeline_id`
and compares five aggregate metrics; with CPN (`cpn_bandwidth_tier_v1` output)
vs ZoomSpec (`spacenet_14` output) the class-aware/matched-accuracy deltas are
`null` (classification N/A for CPN per `app/evaluation/capability.py:22`),
while localization AP50/AP50-95 are valid. This is correct behavior, not a
defect, but must be asserted explicitly in H3.

**G5 — no explicit no-GPU startup product seal.** Absence of GPU is handled
(`build_local_providers` registers `local_gpu` only when both env vars are set;
`availability_for` fails closed), but there is no acceptance test asserting the
no-GPU product contract.

**Non-gaps (verified generic):** executor projection, certificate gating,
asset/release/label fail-closed, remote orphan coordination, DatasetEvaluation
coverage/comparability, imported-run atomicity.

## Backend V1 Scope / Non-Scope

**In scope (Backend V1 Core Seal):**
1. Scenario A single-recording inference (local_cpu and local_gpu), including
   negative/fail-closed cases.
2. Scenario B DatasetExperiment → one model, real, full lifecycle + evaluation.
3. Scenario C same dataset → CPN + ZoomSpec, real, with explicit comparability.
4. Failure/recovery/retry/fencing/idempotency, including the `local_gpu` fixes
   G1/G2.
5. Concurrency (max_concurrency 1 and 2) and moderate endurance with real workers.
6. No-GPU startup/read-back seal.
7. Full backend regression, API contract freeze, frozen-science unchanged.
8. Final artifacts: Backend V1 Acceptance Evidence, Backend V1 API Contract,
   `BACKEND_V1_SEAL_SHA`.

**Out of scope:** frontend; new model abstractions; new MultiModelExperiment
entity (YAGNI — Scenario C is composed from two DatasetExperiments, verified in
H3); remote_gpu/two-host qualification (deferred, see decision); DatasetExperiment
executor changes beyond the G1/G2 local-generic fix; schema/migration changes;
training; concurrency > 2.

## H1–H7 Architecture (logical milestones)

| Milestone | Purpose | Production change | Real GPU | CPU/No-GPU |
|---|---|---|---|---|
| H0 | Current-state audit (this document) | none | no | yes |
| H1 | Single-recording residual qualification + fail-closed negatives | none | optional smoke | yes (primary) |
| H2 | Dataset → one model real E2E (16-item) | none (CPU) / none (GPU) | yes (local_gpu) | yes (local_cpu) |
| H3 | Same dataset → CPN + ZoomSpec real E2E + comparison | none | yes | no |
| H4 | local_gpu recovery/retry/fencing/idempotency | **G1 + G2** | partial (live crash) | yes (DB-level) |
| H5 | Concurrency=1/2 + endurance with real workers | none | yes | partial |
| H6 | No-GPU backend seal | none | no | yes |
| H7 | Backend V1 final evidence + API freeze | none | no | yes |

## Single-Recording Acceptance (H1)

BHQ-3 already provides real positive evidence for Scenario A on `local_gpu`
(CPN, ZoomSpec) and local_cpu (dummy/stft/CPN). H1 adds only the missing
negative/fail-closed contract, all of which are CPU-testable except one optional
GPU availability probe:

| Case | Expected | GPU? |
|---|---|---|
| unsupported/unknown executor requested | `EXECUTION_CAPABILITY_UNAVAILABLE` (registry `provider`, `runtime.py:222-229`) or availability `EXECUTION_CAPABILITY_UNAVAILABLE` | no |
| executor with no exact certificate (e.g. `local_gpu` with a non-matching runtime_ref) | `EXECUTION_NOT_CERTIFIED` (`runtime.py:336-347`) | no |
| wrong/unknown ModelRelease | `MODEL_RELEASE_NOT_FOUND` / `MODEL_RELEASE_MISMATCH` (`model_release.py:134-156`) | no |
| asset identity mismatch (unconfigured/missing/relative path) | `PIPELINE_ASSET_MISMATCH` (`local_inference_worker.py:91-119,172-177`) | no |
| recording label space incompatible with plugin input | `INPUT_INCOMPATIBLE` (`local_executor.py:175-183`, `local_inference_worker.py:231-235`) | no |
| descriptor generation mismatch (`WSP_LOCAL_INFERENCE_RUNTIME_REF`) | `RUNTIME_DESCRIPTOR_INVALID` (`local_inference_worker.py:136-146`) | no |
| `local_gpu` provider not configured (no env) | `local_gpu` absent from providers; `EXECUTION_CAPABILITY_UNAVAILABLE` | no |
| `local_gpu` configured but CUDA probe fails | availability `EXECUTION_CAPABILITY_UNAVAILABLE` with probe reason (`local_executor.py:184-193`) | yes (one case) |
| terminal-run immutability | no rewrite (`local_inference_worker.py:214-216`, `validation.py:47-52`) | no |

Acceptance evidence: an H1 CPU test module (new) plus one optional GPU probe
assertion folded into H5. No re-run of BHQ-3 scientific acceptance.

## Dataset Single-Model Acceptance (H2)

**Deterministic qualification subset (16 recordings).** The dataset membership is
frozen by registering exactly the selected stems via the existing external-path
semantics (`app/datasets/service.py:33-99`: `external_path = absolute`, idempotent
by `external_path`, `source="spacenet"`, GT rows inserted). Only the subset is
registered, so `prepare_manifest` (`app/benchmarks/service.py:433-449`, filtering
`has_ground_truth=True`) yields exactly 16 items.

Selection algorithm (deterministic, metadata-only — no IQ read/hash):
1. Candidate pool = advanced `test` stems with at least one signal in the `.json`
   metadata.
2. Metrics per candidate (from `.json` + file `stat` only): `num_signals`,
   `duration_s = max(end_time)/1000`, `bandwidth_hz = (high-low)*1e6`,
   `sample_rate_hz = bandwidth_hz`.
3. Quartile edges per metric over the candidate pool; each candidate gets a
   shape key `(dur_bucket, bw_bucket, nsignals_bucket)`.
4. Include anchors `{0,1,2}`; then include one representative (lowest numeric
   stem) for each remaining distinct shape key; then evenly-spaced numeric fill,
   to 16 total. Exact stems computed by this algorithm on the frozen repository
   data (verified once for this spec):

```text
0, 1, 2, 3, 9, 11, 12, 15, 32, 42, 79, 80, 83, 99, 109, 280
(16 stems, 16 distinct shape keys; duration 0.020–0.150 s,
 bandwidth 20–80 MHz, signal count 6–10)
```

**Primary one-model acceptance model: CPN `cpn_bandwidth_tier 1.0.0 / golden`.**
Justification: it is the fastest certified model (BHQ-3: ~6–7 s worker wall for
stem 2; BHQ-2: 0.76 s stem 0 / 1.0 s stem 2 inference), has both `local_cpu` and
`local_gpu` certificates, exercises LS-STFT + CPN detection end-to-end, and keeps
GPU/CPU cost minimal. ZoomSpec is reserved for H3 (multi-model) where its
`spacenet_14` classification adds distinct value.

Required assertions (Scenario B contract):
```text
experiment status: pending → running → evaluating → completed
expected_items == manifest size == 16
completed items == 16; failed items == 0
one successful Attempt per item (attempt_number == 1)
exactly one AnalysisRun per Attempt; analysis_run_id unique
experiment.executor == "local_gpu" (H2-GPU) or "local_cpu" (H2-CPU)
runtime descriptor frozen and equal to provider descriptor
plugin_id/version == cpn_bandwidth_tier/1.0.0; model_release_id == "golden";
asset_manifest_sha256 == 7ab8a6a4…fc55bb
DatasetEvaluation created; evaluated_recordings == 16; missing == 0; coverage == 1.0;
comparable == true; classification_applicable == false; classification_reason ==
  "label_space_mismatch"; localization.ap50/ap50_95 present
recording_manifest_hash == frozen experiment hash and stable across a re-prepare
no duplicate launches; no orphan worker processes after terminal
```

H2 runs twice (H2-GPU in the GPU phase; H2-CPU in the no-GPU phase) on the same
16-stem subset but in separate dedicated databases.

## Multi-Model Dataset Acceptance (H3)

Architecture: **two independent DatasetExperiments** (no new abstraction):
```text
frozen 16-stem dataset (one registration / one manifest)
  Experiment A = cpn_bandwidth_tier 1.0.0 / golden / local_gpu
  Experiment B = zoomspec_yolo26n_aug_combined_frn_v3 1.0.0 / golden / local_gpu
```

Required assertions:
```text
A.recording_manifest_hash == B.recording_manifest_hash
manifest membership + manifest_order identical (build same manifest twice; compare)
experiment A provenance: model_release_id golden; asset_manifest_sha256 7ab8a6a4…
experiment B provenance: model_release_id golden; asset_manifest_sha256 16cc0534…
A runs owned only by A items; B runs owned only by B items (no shared run_id)
DatasetEvaluation A and B distinct; each coverage == 1.0; each comparable == true
localization metrics computed independently for each
POST /api/dataset-benchmarks/compare(A,B) → comparable == true,
  reasons == [],
  aggregate_a/aggregate_b localization_ap50 and localization_ap50_95 numeric,
  deltas computed for localization,
  class_aware_map50/class_aware_map50_95/matched_accuracy deltas are null
  (CPN classification N/A — label_space_mismatch), and this is asserted as expected
per-recording: GET .../A/items and .../B/items share recording_id + manifest_order;
  per-recording comparison available via POST /api/algorithm-lab/compare with the
  two analysis_run_ids (localization + cases; class correctness suppressed for CPN)
```

Comparability contract (explicit, no apples-to-oranges):
```text
Legitimately comparable: localization AP50, AP50-95, operating tp/fp/fn,
  per-recording TF boxes/detection membership.
Not comparable (report N/A): class-aware AP, matched accuracy, confusion,
  per-class SpaceNet-14 metrics — CPN emits cpn_bandwidth_tier_v1 tiers, not
  SpaceNet-14 classes; the platform already suppresses these via
  classification_applicability label_space_mismatch.
```

Backend gap determination: the existing APIs are **sufficient** for a future
multi-model frontend comparison (aggregate via `compare`, per-recording by
joining `.../items` and calling Algorithm Lab compare). No new endpoint is
required for V1; a bundled per-recording comparison endpoint is explicitly
deferred (YAGNI).

## Recovery / Retry / Fencing Design (H4)

### H4 production fixes (minimal, fail-closed)

**P1 — generic local stale-run startup recovery (fixes G1).**
Change `app/remote_execution/recovery.py::mark_stale_local_cpu_runs_interrupted`
to a generic local helper, e.g. `mark_stale_local_runs_interrupted(session)`
interrupting `status="running" AND executor IN ("local_cpu","local_gpu")` with the
same `ANALYSIS_INTERRUPTED` projection. Keep a thin compatibility alias
`mark_stale_local_cpu_runs_interrupted` delegating to it (preserves
`AnalysisService.mark_stale_running_runs_interrupted` and existing imports).
`remote_gpu` behavior is unchanged. Update call site `app/main.py:143` and update
the existing tests that assert local_cpu-only scope
(`test_remote_stale_helper_regression.py`, `test_remote_startup_recovery.py`,
`test_dataset_experiment_startup_order.py`) to assert generic local scope while
still preserving `remote_gpu`.

**P2 — generic DatasetExperiment pending-run repair (fixes G2).**
Change `app/dataset_experiments/recovery.py:176` from
`if run.executor != "local_cpu" ...` to accept local executors
(`run.executor not in ("local_cpu","local_gpu")`), so `repair_local_pending_runs`
relaunches the safe first-launch path and fail-closes the ambiguous marker path
for `local_gpu` exactly as for `local_cpu`. `remote_gpu` continues to be handled
by the remote coordinator/recovery path. Update
`test_dataset_experiment_local_launch_recovery.py` to parameterize executor over
`local_cpu`/`local_gpu`.

No other production change is required. Standalone `pending` local runs (no
attempt marker) are intentionally left unchanged in V1 (same semantics as today's
`local_cpu`); this is documented as a known, non-P0 behavior, not silently fixed.

### H4 acceptance cases

| Case | Setup | Expected | GPU? |
|---|---|---|---|
| H4-A local_gpu running run interrupted at startup | seed `running` local_gpu run; `create_app` recovery | `interrupted` / `ANALYSIS_INTERRUPTED`; no permanent `running` | no (DB-level); one live variant in GPU phase |
| H4-A2 local_cpu parity | same for local_cpu | unchanged `ANALYSIS_INTERRUPTED` | no |
| H4-A3 remote_gpu untouched | seed `remote_gpu` pending/running, no remote config | left untouched; no coordinator | no |
| H4-B experiment crash mid-item | item1/item2 completed, item3 running, item4..queued; restart | item3 → failed (from interrupted run); queued items resume; completed items never rerun; attempt history preserved (attempt_number increments only on retry) | yes (live) + no (DB-level) |
| H4-C launch boundary: intent absent | latest attempt `launch_requested_at IS NULL`, no worker | relaunch same attempt/run (CAS single-use); concurrent actor cannot double-launch | no |
| H4-C launch boundary: intent present, pending, no worker | `launch_requested_at` set; run `pending`; no PID | fail-closed `pending→interrupted` `ANALYSIS_LAUNCH_AMBIGUOUS`; item → failed | yes (live) + no (DB-level) |
| H4-D repeated recovery | `recover_dataset_experiments` twice | no duplicate attempts/runs/tokens; heartbeat cutoff prevents re-claim; idempotent | no |
| H4-E stale coordinator fencing | old token after rotation | every CAS zero-row; `DATASET_EXPERIMENT_FENCE_LOST`; no writes | no |
| H4-F retry_failed semantics | `completed_with_failures` with failed items | `completed_with_failures→running` CAS; failed→queued; new attempt_number; spawn compensation on failure restores | no |
| H4-G evaluation recovery | `evaluating` experiment, orphaned `pending`+PID evaluation | `normalize_orphaned_evaluation` → `interrupted`; coordinator `reset_interrupted_evaluation`/restart; no AnalysisRuns | no |

Live GPU variants: H4-B and H4-C(live) with a real `local_gpu` worker terminated
mid-run (SIGKILL to the exact worker PID after it is verified via
`/proc/<pid>/cmdline` = `app.analysis.local_inference_worker <run_id>`), then
`create_app` recovery on a fresh process against the same DB.

## Concurrency Design (H5)

Use the 16-stem subset (or a deterministic 8-stem slice for H5 to save GPU time;
the spec fixes H5 at 8 stems: `0,1,2,3,9,11,12,15` for concurrency and endurance
uses 40 executions reusing the 16-stem subset cycled deterministically — never
more than 16 distinct).

- H5-C1: `max_concurrency=1`, CPN local_gpu, 8 items → asserts scheduler never
  runs >1 worker; all terminal.
- H5-C2: `max_concurrency=2`, CPN local_gpu, 8 items → asserts maximum
  simultaneously-active model worker processes ≤ 2, measured by sampling
  `app.analysis.local_inference_worker` PIDs (and/or `nvidia-smi` compute apps)
  while the coordinator runs; all items terminal; exactly one run per attempt;
  no duplicate attempt ownership; no orphan workers after terminal; DB
  consistency (`completed==8`, one attempt each).
- GPU-safety: the Amendment-A1 `MemoryMonitor`/gate remains active around the
  experiment; VRAM is sampled via `nvidia-smi` and must return to baseline after
  the run (no monotonic growth).
- No `max_concurrency>2` is proposed for V1 (SQLite single-writer + single-GPU;
  current architecture justifies at most 2 for V1).

## Endurance Design (H5 continuation)

- Workload: 40 real executions of CPN `golden` on `local_gpu`, `max_concurrency=2`,
  cycling the 16-stem subset deterministically (2 experiments of 16 + 1 of 8, or
  one 40-item experiment if the manifest supports 40 distinct recordings — use
  three sequential experiments to stay within the 16 registered stems).
- Observability per sample (every ~5 s + before/after each experiment): worker
  PID set and lifetimes, worker RSS (`/proc/<pid>/status` `VmHWM`), GPU
  process memory (`nvidia-smi --query-compute-apps`), GPU total used/free,
  cgroup `memory.current`/`committed_floor`/`effective_headroom`, PVS `memory.pressure`,
  `memory.events` (`high`/`max`/`oom`/`oom_kill`), DB counts (experiment/item/
  attempt/run statuses), failed/duplicate attempt counts.
- Acceptance criteria (auditable):
```text
0 failed runs; 0 failed items; 0 orphan worker PIDs after each experiment
attempts per successful item == 1 (retry only if an explicit injected failure occurs)
no monotonically increasing trend in worker RSS or GPU process memory across the
  40 executions (each post-run observation returns to the pre-run baseline within
  tolerance; no one-way growth over the sequence)
0 `memory.events.max`/oom/oom_kill deltas; PSI full avg10 stays under the A1 abort
  threshold; no SQLite "database is locked" errors in logs
all experiments reach `completed` with coverage 1.0
```

## GPU Qualification Matrix

| Qualification | GPU required? | Why | Estimated real runs |
|---|---|---:|---:|
| H2-GPU DatasetExperiment CPN `local_gpu` (16 items) | yes | real CUDA LS-STFT+CPN through the batch worker | 16 |
| H3-GPU Experiment A CPN `local_gpu` (16 items) | yes | same frozen manifest as B; independent provenance | 16 |
| H3-GPU Experiment B ZoomSpec `local_gpu` (16 items) | yes | real CUDA ZoomSpec (LS-STFT→CPN→AHLP→FRN→NMS) | 16 |
| H4-GPU live worker-kill recovery (`local_gpu`) | yes | kill a real GPU worker mid-run; prove startup recovery | ~4 |
| H4-GPU ambiguous-launch fail-closed (live) | yes | real pending run with durable launch intent, no worker | ~2 |
| H5-GPU concurrency=1 (8 items) | yes | real single worker scheduling | 8 |
| H5-GPU concurrency=2 (8 items) | yes | prove ≤2 real simultaneous workers | 8 |
| H5-GPU endurance (40 CPN executions, concurrency 2) | yes | lifecycle/resource trend over many real runs | 40 |
| H1 optional GPU availability probe | yes | `local_gpu` configured, CUDA probe fails/succeeds | ~2 |
| **GPU total** | | | **~112** |

Everything else (single-recording fail-closed negatives, H2-CPU, H4 DB-level
recovery/retry/fencing/idempotency, concurrency DB invariants on CPU, no-GPU
startup, full regression, API contract, scope audit) is CPU-only.

## No-GPU Seal Matrix

Performed after the GPU phase, with the server switched to no-GPU mode (no ML
interpreter/CUDA configured for `local_gpu`):

| Capability | Expected in no-GPU mode | Test |
|---|---|---|
| Platform starts | YES | `create_app` boots with no GPU/ML env; `/api/health` 200 |
| Read APIs | YES | recordings/datasets/detections/analysis/benchmarks/dataset-experiments/imported/list endpoints return historical data |
| Historical results readback | YES | completed `local_gpu` runs and evaluations from the GPU phase are readable (read-only) |
| CPU capabilities | YES | `local_cpu` (dummy/stft/CPN) providers registered and certified |
| Algorithm Lab | YES | `POST /api/algorithm-lab/compare` on two completed runs |
| Imported Runs | YES | single + batch import/list |
| Dataset → one model (CPU) | YES | H2-CPU CPN `local_cpu` 16-item lifecycle + evaluation coverage 1.0 |
| `local_gpu` execution available | **NO (fail closed)** | `local_gpu` provider absent/unavailable; `availability_for` → `EXECUTION_CAPABILITY_UNAVAILABLE`; `prepare_run(executor="local_gpu")` raises, never crashes |
| `remote_gpu` execution available | NO (no profile) | untouched/fail-closed; existing runs preserved |
| Control-plane boundary | torch/ultralytics absent | `find_spec` None; ML-free import test |
| Absence of GPU does not crash control plane | YES | boot + all read paths succeed |

## Deterministic Dataset Qualification Subset Strategy

- Selection is metadata-only (JSON + `stat` size), deterministic, and re-runnable;
  the 16 stems are `0,1,2,3,9,11,12,15,32,42,79,80,83,99,109,280` (16 distinct
  duration/bandwidth/signal-count shape keys).
- Qualification registers **only** these stems (not the 2500-stem split) via the
  existing external-path registration semantics, in a dedicated qualification DB;
  the production `platform.db` and any G6/BHQ-3 databases are never touched.
- The H5/H6 concurrency/endurance slices are deterministic prefixes/cycles of the
  same frozen set, so manifest hashes are stable and reproducible.
- A committed acceptance tool (not production) performs selection + registration
  (planned for the implementation phase; mirroring the G6 registration pattern).
- No full IQ scan/hash is performed for selection; only `verify_assets`-style
  hashing of the four golden model assets remains (already qualified).

## Resource / Memory Observability

Reuse Amendment-A1 acceptance tooling (`scripts/bhq3_memory_gate.py`:
`read_snapshot`/`evaluate`/`MemoryMonitor`/`run_guarded_subprocess`) and the
BHQ-3 resource approach:
- GPU: `nvidia-smi --query-gpu=memory.used,memory.free,utilization.gpu` and
  `--query-compute-apps=pid,used_memory` before/during/after each experiment.
- Workers: PID identity via `/proc/<pid>/cmdline`; RSS `VmHWM`; lifetime.
- Cgroup: `memory.current`, `committed_floor`, `effective_headroom`, `memory.stat`,
  `memory.pressure`, `memory.events`.
- DB: status counts and attempt/run invariants via SQLAlchemy.
`/usr/bin/time` is unavailable; peak RSS is captured via `/proc` (as in BHQ-3).

## Failure Injection Strategy

- **Process kill:** SIGKILL the exact verified local worker PID (never unrelated
  PIDs) to simulate worker death mid-inference; then start a fresh `create_app`
  process against the same DB for recovery.
- **Coordinator death:** SIGKILL the exact `app.dataset_experiments.worker` PID
  (verified by argv/token) to simulate coordinator crash mid-schedule.
- **Restart:** open a fresh `create_app` on the same DB (real startup recovery
  path), not an in-process helper.
- **Ambiguous launch:** kill between the `pending` commit and the worker's
  `running` transition (seeded by construction when needed).
- **Duplicate recovery:** call `recover_dataset_experiments` twice in the same
  epoch and across a heartbeat cutoff.
- **Fencing:** rotate the coordinator token and attempt writes with the stale
  token.
- **Fail-closed input:** wrong release/asset/label/executor/certificate states.
- No signal is ever sent to a PID whose identity cannot be proven; no `kill -9`
  of unrelated processes; no `drop_caches`.

## API Freeze Strategy

- Freeze the 38 router endpoints + `/api/health` enumerated in the audit
  (recordings, dsp, ground_truth, datasets, detections, analysis, imported_runs,
  evaluation/algorithm-lab, benchmarks, dataset_experiments).
- Freeze request/response schemas (`app/**/schema.py`) and error mapping
  (`PlatformError` → HTTP status; `app/main.py:169-174`), including the public
  `AnalysisRunRead.ExecutionMetadataRead` allowlist (no internal keys leaked).
- Add contract regression tests that assert path+method+status+top-level response
  keys for every endpoint (mostly ALREADY covered; H7 adds a single consolidated
  contract test to freeze the set).
- Produce `docs/superpowers/acceptance/…-backend-v1-api-contract.md` enumerating
  each endpoint, method, request model, response model, and error codes.
- No breaking schema change without a documented version bump; V1 freezes the
  current contract.

## Remote-GPU / Two-Host Scope Decision

**Decision: `remote_gpu`/two-host (BHQ-4 SSH loopback, BHQ-6 Windows→AutoDL) are
OUT_OF_V1_CORE_SCOPE.**

Rationale (from source):
- The three V1 workflows are satisfied by local executors: Scenario A/B/C use
  `local_cpu`/`local_gpu`; `remote_gpu` is an alternative transport for the same
  `PluginHandle`/`AnalysisResultWriter` contract (`RemoteGpuExecutorProvider`,
  `plugin_executor.py`), already unit/integration tested via fakes and one
  cold-start subprocess test.
- The deployment assumption is Browser → AutoDL backend → `local_gpu`; no V1
  workflow requires SSH to a second host.
- The only real `remote_gpu` certificate is for ZoomSpec on a specific remote
  runtime commit (`remote:autodl_primary:5bb5be4…`), which is not part of the V1
  product topology.
- Deferring BHQ-4/BHQ-6 leaves **no hole** in Scenarios A/B/C or the no-GPU seal.

Future qualification path (documented, not V1): BHQ-4 server-to-self SSH loopback
(requires known_hosts enrollment + own-key authorization; no strict-check
weakening) then BHQ-6 two-host Windows→AutoDL. `remote_gpu` remains a supported
code path, unit/integration tested, but not V1-certified for production topology.

## Cost-Aware GPU Budget

Baseline BHQ-3 measured worker wall times (include model load + inference):
CPN stem 2 ≈ 6.9 s; CPN stem 0 ≈ 6.6 s; ZoomSpec stem 2 ≈ 8.2 s; ZoomSpec stem 0
≈ 18.2 s. Direct-science (post-load) inference: CPN ≈ 0.8–1.0 s; ZoomSpec ≈ 2.3 s
(stem 2) / 11.6 s (stem 0).

Approximate GPU-phase real execution counts:

| Item | Runs | Approx wall |
|---|---|---|
| H2-GPU DatasetExperiment (CPN local_gpu, 16 items) | 16 | ~2–3 min |
| H3-GPU Experiment A (CPN local_gpu, 16 items) | 16 | ~2–3 min |
| H3-GPU Experiment B (ZoomSpec local_gpu, 16 items) | 16 | ~4–6 min |
| H4-GPU live crash/recovery cases | ~6 | ~2 min |
| H5-GPU concurrency (1 and 2, 8 items each) | 16 | ~2–4 min |
| H5-GPU endurance (40 CPN local_gpu executions, concurrency 2) | 40 | ~5–7 min |
| H1 optional GPU probe | ~2 | <1 min |
| **GPU phase total** | **~112 real executions** | **~20–30 min wall** |

The GPU phase therefore costs on the order of one server-hour, not hundreds.
The rejected brute-force alternative (2500 recordings × 2 models = 5000 runs at
~7 s ≈ 9.7 h of GPU wall, excluding overhead and evaluation) is ~20–50× the cost
for no additional acceptance value. No-GPU qualification (pytest, H1 CPU,
H2-CPU 16 runs, H4 DB-level, no-GPU seal, API freeze) runs at the cheaper rate.

## Final Seal Criteria

```text
Single Recording CPU/GPU paths            acceptance evidence + fail-closed negatives
Dataset → one model (16-item)             completed; coverage 1.0; provenance frozen
Same Dataset → multiple models            both complete; manifest identical; compare valid
DatasetEvaluation                         coverage 1.0; comparable true; localization metrics
retry semantics                           retry_failed / retry_evaluation pass
local_gpu crash/startup recovery          P1 + P2 green; no permanent stale running/pending
DatasetExperiment recovery                H4-B/C/D/E green; idempotent; fenced
fencing/idempotency                       stale token cannot write; repeated recovery idempotent
concurrency=2                             ≤2 live workers; all terminal; no duplicate run/attempt
moderate endurance (40 executions)        quantitative criteria in Endurance Design met
certificate/release fail-closed           negative matrix green
no-GPU startup                            starts; read APIs + CPU capabilities live;
                                          local_gpu execution fail-closed (no crash)
full backend regression                    0 failed / 0 errors
API contract freeze                       endpoint+schema contract test green; contract doc
frozen science unchanged                  NO_FROZEN_SCIENCE_CHANGED
known P0/P1 backend defects               0
```

Engineering standard for the seal: **all defined V1 critical paths have
acceptance evidence, all known blockers are resolved, and there are no
unresolved known P0/P1 backend risks.** This is not a claim of literal zero bugs.

Artifacts:
```text
Backend V1 Acceptance Evidence : docs/superpowers/acceptance/…-backend-v1-acceptance.md
Backend V1 API Contract        : docs/superpowers/acceptance/…-backend-v1-api-contract.md
BACKEND_V1_SEAL_SHA            : SHA of the commit that adds the final evidence
                                 (determined externally after commit; never embedded)
```

## Risks / Limitations

- **SQLite single-writer concurrency:** `max_concurrency=2` is the V1 ceiling;
  higher concurrency is deferred until a shared DB is adopted. Risk: intermittent
  write contention at concurrency 2 — mitigated by bounded concurrency and
  observing for `database is locked`.
- **GPU/time flakiness:** transient PSI at `memory.high` can block A1 admission;
  the gate is fail-closed and never weakened; buffer time in the GPU phase.
- **`local_gpu` recovery fixes change existing local_cpu-scoped tests:** the
  implementation plan must update `test_remote_stale_helper_regression.py`,
  `test_remote_startup_recovery.py`, `test_dataset_experiment_startup_order.py`,
  and `test_dataset_experiment_local_launch_recovery.py` to assert generic local
  scope. This is expected, not a weakening.
- **Cross-model metrics:** only localization is comparable between CPN and
  ZoomSpec; class-level metrics are N/A by design.
- **Remote/two-host deferred:** documented; no V1 hole.
- **No-GPU mode:** `local_gpu`/`remote_gpu` become unavailable/fail-closed; any
  historical `local_gpu` runs are read-only.

## Proposed Implementation Sequence

Milestones are implemented first (production fixes P1/P2 + targeted CPU tests),
then the GPU phase is executed in one concentrated window, then the server is
switched to no-GPU for the seal.

**Pre-GPU (CPU; can run in GPU mode cheaply):**
1. C-V1-0: implement P1 + P2 production fixes with focused TDD tests (update the
   four impacted test files); commit `fix: generalize local executor startup
   recovery` and `fix: generalize dataset experiment local pending repair`.
2. C-V1-1: add the H1 negative/fail-closed CPU test module; commit.
3. C-V1-2: add the deterministic 16-stem selector + registration acceptance tool
   and the H2/H3/H4/H5 qualification drivers (acceptance tooling only); commit.

**GPU phase (while the 5090 is rented; one window):**
4. H2-GPU: CPN `local_gpu` 16-item DatasetExperiment → evidence.
5. H3-GPU: CPN + ZoomSpec `local_gpu` over the identical 16-stem manifest;
   `compare` + per-recording checks → evidence.
6. H4-GPU: live worker-kill crash/recovery for `local_gpu` (H4-B, H4-C live).
7. H5-GPU: concurrency=1 and 2 (8 items) with live worker-count sampling.
8. H5-GPU: 40-execution endurance with resource monitoring.
9. Capture consolidated GPU evidence JSON; then switch server to no-GPU mode.

**No-GPU phase (cheap):**
10. H1 residual CPU cases; H2-CPU CPN `local_cpu` 16-item; H4 CPU-level
    recovery/retry/fencing/idempotency; no-GPU startup seal (starts, read APIs,
    CPU capabilities, `local_gpu` fail-closed).
11. Full backend regression (`pytest backend/tests -q`), control-plane ML-free
    check.
12. H7: Backend V1 Acceptance Evidence + API Contract freeze; scope audit
    (`c809eb0..HEAD` and `173d441..HEAD`); commit final evidence (do not embed its
    own SHA); determine `BACKEND_V1_SEAL_SHA` externally; push only
    `feature/backend-v1-final-qualification`.

Checkpoint/commit granularity: one commit per numbered step; no giant
multi-purpose commits; evidence documents committed separately.

---

### Spec self-review record

- No TODO/TBD placeholders remain; the one previously-open item (qualification
  subset) is resolved to exact stems `0,1,2,3,9,11,12,15,32,42,79,80,83,99,109,280`.
- API references were checked against source (`app/**/router.py`); error codes
  against `app/core/errors.py` + call sites; recovery claims against
  `remote_execution/recovery.py` and `dataset_experiments/recovery.py`.
- GPU/no-GPU classification minimizes GPU usage; no redundant scientific
  acceptance is proposed (BHQ-3 positives are reused).
- All three product workflows are covered end-to-end (H1/H2/H3).
- Recovery is explicitly designed (H4) with the production fixes it requires.
- No frontend work is included.
