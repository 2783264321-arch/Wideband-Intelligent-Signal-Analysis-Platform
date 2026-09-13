# Backend V1 Final Qualification — Design Specification (Revision A1: Portable Manual + Auto Execution)

Date: 2026-09-14
Status: DESIGN ONLY (no implementation, no plans, no model execution, no production/test/certificate change)
Base: `feature/bhq-local-gpu @ 173d4413321fb62b70290467552549e824120a41` (BHQ-3 final seal)
Design branch: `feature/backend-v1-final-qualification @ 185e947…`
Sealed Phase-G reference: `feature/m9-2-implementation @ c809eb02a5286737819136f9391d13949e8a117b` (do not modify)

This revision supersedes the earlier `OUT_OF_V1` remote decision and adds
portable execution environments, a manual + Auto execution policy, certificate
portability, the AutoDL cold mode switch, and a two-seal recommendation.

---

## Executive Goal

Seal the backend for V1 across three portable deployment profiles (LCPU, LGPU,
RGPU), proving the three principal product workflows end-to-end, closing the
`local_gpu` recovery gap, designing an explainable manual + Auto executor
selection policy, and handling the GPU→no-GPU cold handoff — while concentrating
genuinely CUDA-dependent validation into the rented-GPU window and running
everything else on the cheap no-GPU profile.

Economic constraint: GPU server ≈ 2.88 RMB/hour, no-GPU ≈ 0.10 RMB/hour. GPU
wall time is reserved for real CUDA behavior; brute force (2500 recordings ×
multiple models) is rejected as ~20–50× the cost for no added acceptance value.

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

Verification for this revision (read-only): design branch/HEAD clean at
`185e947…`; remote matches; sealed Phase-G and BHQ-3 worktrees unchanged; 147
backend test files; executor architecture, recovery, durable launch ordering,
remote profile, and certificate semantics audited at source level.

## Portable Execution Environments (Profiles)

**Profile LCPU — local CPU-only machine.**
```text
User computer
├─ WISA control plane
├─ local_cpu
└─ no local CUDA GPU
```
Platform fully usable; any plugin with a valid `local_cpu` path may run locally.

**Profile LGPU — local machine/server with GPU.**
```text
User computer / server
├─ WISA control plane
├─ local_cpu
└─ local_gpu
```
Both coexist; executor chosen manually or by Auto.

**Profile RGPU — CPU-only local machine + rented remote GPU.**
```text
Local computer
├─ WISA control plane
├─ local_cpu
│
└──── remote execution (SSH) ────> rented GPU server
                                   └─ remote_gpu
```
This is an explicit real user scenario; the earlier remote deferral is reopened
(see Remote-GPU V1 Scope and Recommended Final Seal Model).

## Executor Architecture (Concept Separation)

The platform must keep six distinct concepts separate (do not conflate
deployment qualification with live health):

```text
A. technical capability     PipelineDefinition.technical_execution_capabilities
                            (declared by the plugin; not a grant)
B. configured provider      build_local_providers(settings) + optional
                            RemoteGpuExecutorProvider (app/main.py:47-52, 67-116)
C. exact certificate        ExecutionCertificate: (plugin_id, plugin_version,
                            model_release_id, executor, device_type, precision,
                            runtime_ref) (remote_execution/runtime.py:114-191)
D. live availability        provider probe for the current input/runtime
                            (ExecutorRegistry.availability_for; local_executor
                            probe; remote SSH probe)
E. selection policy         Manual (exact executor) or Auto (pre-run resolver)
F. frozen execution identity   AnalysisRun.executor / DatasetExperiment.executor
                            + runtime_descriptor_json (authoritative, persisted)
```

Deployment-qualified candidate:
```text
technical capability  ∩  registered provider  ∩  exact certificate  =  candidate
candidate  +  live availability (current input/runtime)  =  runnable now
```
Source: `ExecutorRegistry.certified_capability` (`runtime.py:265-298`),
`deployment_qualified_executors` (`runtime.py:300-313`), `availability_for`
(`runtime.py:315-348`); `AnalysisService` dispatch (`service.py:144-160`);
`AnalysisRunCreate.executor` default `"local_cpu"` (`analysis/schema.py:32-37`).

## Product Workflows

**A — Single Recording Inference.** `Recording → choose Plugin/ModelRelease/
Executor (manual or Auto) → AnalysisRun → real inference → detections/provenance`.
Seams: `POST /api/analysis-runs` → `AnalysisService.create_run` →
`ExecutorRegistry` → provider → worker/coordinator → `AnalysisResultWriter`;
readback `GET /api/analysis-runs/{id}`, `.../detections`.

**B — Dataset → One Model.** `frozen Dataset → one Plugin/ModelRelease/Executor →
DatasetExperiment → N AnalysisRuns → DatasetEvaluation → metrics/coverage/provenance`.
Seams: `POST /api/dataset-experiments`, `…/run`, coordinator subprocess,
`start_item_attempt`/`launch_item_attempt`, `reconcile_items`,
`start_linked_evaluation` → benchmark worker; readback `GET /api/dataset-experiments/{id}`,
`…/items`, `…/items/{item}/attempts`, `GET /api/dataset-benchmarks/{eval}`.

**C — Same Dataset → Different Models.** One frozen membership; Experiment A =
CPN golden, Experiment B = ZoomSpec golden; separate runs/evaluations;
comparison via `POST /api/dataset-benchmarks/compare` and per recording via
`GET /api/dataset-benchmarks/{eval}/items` + `POST /api/algorithm-lab/compare`.

## Existing Coverage Matrix

Classification: `U` unit, `I` integration, `G` real-GPU qualified, `NC` needs new
CPU test, `NG` needs new GPU test, `F` needs production fix, `O` out of V1.

| Area | Class | Evidence |
|---|---|---|
| AnalysisRun prepare/launch/lifecycle | U/I | `test_analysis_prepare_local.py`, `test_analysis_launch_prepared_run.py`, `test_analysis_create_run_compat.py`, `test_analysis_result_writer.py` |
| Local worker (CPU/GPU generic) | U/I | `test_local_inference_worker.py` |
| local_gpu provider probe/descriptor | U/I | `test_local_worker_provider.py` |
| local_gpu real CUDA science | G | BHQ-3 C6/C8 (`m9_2_bhq3_*`) |
| local_gpu positive production AnalysisRun | G | BHQ-3 runs `run_45f5…`, `run_0bee…` |
| Certificate store + projection | U/I | `test_execution_certificate.py`, `test_executor_registry.py`, `test_executor_cutover.py`, `test_pipeline_read_model.py`, `test_local_gpu_certificates_exact.py` |
| Plugin registry / ModelRelease / assets | U/I | `test_plugin_registry.py`, `test_model_release.py`, `test_release_wiring.py`, `test_asset_manifest_v1_frozen.py` |
| Recording/SpaceNet ingestion/readback | U/I | `test_recordings.py`, `test_spacenet_*`, `test_dataset_adapter.py`, `test_input_output_label_space.py` |
| DatasetExperiment create/attempts/launch | U/I | `test_dataset_experiment_creation.py`, `…_attempt.py`, `…_launch.py`, `…_models.py` |
| DatasetExperiment coordinator/fencing | U/I | `…_coordinator_*.py`, `…_generation_fence.py` |
| DatasetExperiment local_cpu recovery | U/I | `test_dataset_experiment_local_launch_recovery.py`, `test_remote_startup_recovery.py` |
| Benchmark/Evaluation science | U/I | `test_benchmark_*.py`, `test_evaluation_*.py`, `test_dataset_metrics.py` |
| Algorithm Lab compare | U/I | `test_algorithm_lab_compare.py` |
| Imported Runs (single + batch) | U/I | `test_imported_runs.py`, `test_batch_*.py` |
| remote_gpu transport/probe/coordinator | U/I (+1 cold start) | `test_remote_*.py`, `test_remote_coordinator_cold_start.py` |
| **local_gpu recovery (single + experiment)** | **F + NC/NG** | no test exists; code is local_cpu-scoped |
| **Executor selection policy (manual/Auto)** | **NC/F** | no Auto concept exists today |
| **Real DatasetExperiment E2E (any executor)** | **NG/NC** | no real multi-item experiment test |
| **Real concurrency=2 with two worker processes** | **NG** | scheduling test uses an in-process fake provider |
| **Real multi-model dataset comparison** | **NG** | aggregate `compare` unit-tested only |
| **No-GPU startup product seal** | **NC** | not asserted as product behavior |
| **Remote two-host end-to-end (Profile RGPU)** | **NG + operator-assisted** | never executed cross-host |

## Known Gaps

**G1 (P1, production fix) — single-run local_gpu startup recovery.**
`remote_execution/recovery.py:22-37` interrupts only `status="running" AND
executor="local_cpu"`; `remote_gpu` handled separately (`:40-46`). `local_gpu`
stays `running` forever. Called at `app/main.py:143`.

**G2 (P1, production fix) — DatasetExperiment local_gpu recovery.**
`dataset_experiments/recovery.py:176` `if run.executor != "local_cpu" … continue`;
a `local_gpu` item with a pending run is never relaunched nor fail-closed.

**G3 — no real multi-item / multi-model / concurrency=2 / endurance evidence**
(in-process fake provider only).

**G4 — multi-model comparability:** `dataset-benchmarks/compare` does not require
matching `pipeline_id`; CPN vs ZoomSpec class-aware/matched-accuracy deltas are
`null` (correct; CPN classification N/A per `evaluation/capability.py:22`).

**G5 — no explicit no-GPU startup product seal; no local_gpu config/disappearance
semantics.**

**G6 — no Auto executor-selection policy exists.** Today `AnalysisRunCreate.executor`
defaults to `"local_cpu"`; `DatasetExperimentModel.executor` is required at
creation (`create_experiment._validate_execution`, `service.py:193-204`). There is
no pre-run resolver, no `executor="auto"` concept, and no reason-code provenance.

**G7 — durable launch ordering can create a false ambiguous state on provider
disappearance.** `launch_item_attempt` (`service.py:572-695`) commits the launch
intent **before** provider lookup: `_claim_launch_intent` CAS + commit
(`service.py:673-691`), then `analysis_service.launch_prepared_run(run.id)`
(`service.py:693`), where `ExecutorRegistry.provider(run.executor)` is resolved
(`analysis/service.py:281-285`) before the launch try/except. If the frozen
executor's provider is no longer registered, the run is left `pending` with
`launch_requested_at` set and `worker_pid` NULL — i.e. an ambiguous state that is
correctly fail-closed on the *next* restart but can stall the running experiment
in-process and could be misread as "another actor launched it". (See Recovery.)

**Non-gaps:** executor projection, certificate gating, asset/release/label
fail-closed, remote orphan coordination, DatasetEvaluation coverage/comparability,
imported-run atomicity.

## Backend V1 Scope / Non-Scope

**In scope:**
1. Scenario A (local_cpu/local_gpu/remote_gpu), including fail-closed negatives.
2. Manual + Auto execution selection for single AnalysisRun and DatasetExperiment.
3. Scenario B DatasetExperiment → one model, real, full lifecycle + evaluation.
4. Scenario C same dataset → CPN + ZoomSpec, real, with explicit comparability.
5. Recovery/retry/fencing/idempotency incl. G1/G2 and provider-disappearance (G7).
6. Concurrency (1, 2) and moderate endurance with real workers.
7. **Profile RGPU portable deployment qualification** (remote_gpu real path;
   loopback mechanics + operator-assisted two-host). Reopened per new requirement.
8. GPU→no-GPU cold handoff seal; no-GPU startup product contract.
9. Full regression, API/frontend executor contract freeze, frozen-science intact.
10. Artifacts: Backend V1 Acceptance Evidence, Backend V1 API Contract,
    Backend V1 Portable Deployment Evidence, `BACKEND_V1_SEAL_SHA`.

**Out of scope:** frontend implementation; new MultiModelExperiment entity;
concurrency > 2; training; DB/migration changes beyond the G1/G2/G7 fixes;
weakened/generic certificates.

## V1 Execution Mode — Manual + Auto

Both user mental models are preserved.

**Manual mode (default, exact executor).** User selects `local_cpu`,
`local_gpu`, or `remote_gpu`. The platform validates that exact executor via
`ExecutorRegistry.availability_for` and fails closed with the exact reason code
(`EXECUTION_CAPABILITY_UNAVAILABLE`, `EXECUTION_NOT_CERTIFIED`,
`INPUT_INCOMPATIBLE`, remote probe reason). The platform never substitutes
another executor (`analysis/service.py:144-160`).

**Auto mode (pre-run selection policy, not a fourth executor).**
```text
Auto
 ↓ collect eligible executors (technical ∩ registered provider ∩ exact certificate)
 ↓ filter by live availability for this input/runtime
 ↓ evaluate deterministic policy factors
 ↓ select ONE exact executor
 ↓ freeze exact executor identity
 ↓ create AnalysisRun / DatasetExperiment
```
After resolution the persisted `executor` is exactly one of `local_cpu`,
`local_gpu`, `remote_gpu`. A persisted `executor="auto"` is forbidden.

Provenance: persist the requested intent alongside the resolved identity:
```text
requested_execution_mode = "auto" | "manual"     (request/audit concept)
resolved_executor        = "local_gpu"           (authoritative, frozen)
auto_reason_code         = "AUTO_LOCAL_GPU_PREFERRED"
auto_reason              = "Local GPU certified and available; workload exceeds CPU preference threshold."
```
Rationale for preserving `requested_execution_mode` + reason: auditability of
Auto decisions and future UI explanation; the authoritative execution identity is
always the resolved executor. (Minimal persistence: extend AnalysisRun/
DatasetExperiment execution metadata JSON; no schema migration required for
metadata-only fields, but adding explicit columns is deferred unless contract
freeze requires them.)

## Auto Policy

V1 Auto is deterministic, explainable, and cheap — never an opaque ML scheduler.

**Candidate inputs actually available today:** plugin technical capabilities
(`definition.technical_execution_capabilities`), registered providers
(`build_local_providers`, remote provider), exact certificates
(`execution_certificates.json`), live availability (`availability_for`),
recording size (`RecordingModel.num_samples`/`duration_s`), dataset item count
(`prepare_manifest().expected_recordings`), `recommended_execution`, and remote
probe/config presence. GPU memory/utilization is read only for the optional
"GPU currently saturated" guard; CPU/resource pressure uses the existing
Amendment-A1 cgroup reader.

**Policy hierarchy (evaluated in order, first match wins):**
```text
1. Build the runnable set = candidates that are also live-available.
2. If the runnable set is empty → fail closed with the union of per-executor
   reasons (AUTO_NO_RUNNABLE_EXECUTOR); never invent an executor.
3. If exactly one executor is runnable → select it (AUTO_ONLY_RUNNABLE_EXECUTOR).
4. Compute workload class from task size:
     GPU_BENEFICIAL if duration_s > GPU_PREFER_DURATION_S (default 0.05 s)
       OR num_samples > GPU_PREFER_SAMPLES (default 3,000,000)
       OR dataset_item_count >= GPU_BATCH_ITEMS (default 8);
     else SMALL_WORKLOAD.
5. If GPU_BENEFICIAL and local_gpu is runnable → local_gpu
     (AUTO_LOCAL_GPU_PREFERRED). Prefer local over remote to avoid SSH/cost.
6. Else if SMALL_WORKLOAD and local_cpu is runnable (and certified for the
   release) → local_cpu (AUTO_LOCAL_CPU_SMALL).
7. Else if remote_gpu is runnable and no local candidate is runnable-or-beneficial
   → remote_gpu (AUTO_REMOTE_GPU_ONLY_VIABLE).
8. Else (two or more runnable, none matched above) → deterministic ranking:
     local_gpu > local_cpu > remote_gpu, filtered by runnable set
     (AUTO_DETERMINISTIC_RANK; also the fallback when task size is unknown).
9. The resolver MUST NOT choose an executor the plugin does not technically
   support or that is not certified for the resolved release.
```
`recommended_execution` is used as a **tie-break hint only** within the runnable
set, never as an override of availability/certification.

**Reason codes (closed set):** `AUTO_ONLY_RUNNABLE_EXECUTOR`,
`AUTO_LOCAL_GPU_PREFERRED`, `AUTO_LOCAL_CPU_SMALL`,
`AUTO_REMOTE_GPU_ONLY_VIABLE`, `AUTO_DETERMINISTIC_RANK`,
`AUTO_NO_RUNNABLE_EXECUTOR`.

**Minimal metadata gap (do not over-engineer).** The V1 policy above is
expressible from technical capabilities (cuda vs cpu), certificates, live
availability, `recommended_execution`, and task size — **no new plugin metadata
is required**. A future optional metadata seam (e.g. `execution_preference`,
`resource_class`, `gpu_acceleration_benefit`) is *not* introduced in V1; the
policy is generic and contains no plugin-id branches.

**Policy factors deliberately excluded in V1:** learned/ML scheduling, market
cost optimization, cross-host GPU telemetry beyond a simple saturation guard, and
per-item Auto re-resolution (see DatasetAuto freeze).

## Auto vs Fallback Boundary

Auto is selection, not fallback. Once resolved:
```text
Auto → selected local_gpu → AnalysisRun.executor = local_gpu
```
If local_gpu later becomes unavailable or its worker fails:
```text
DO NOT silently substitute local_cpu or remote_gpu.
The run stays bound to local_gpu and follows normal lifecycle
(running → failed/interrupted per existing rules).
```
A retry MAY re-invoke Auto only by creating a NEW attempt/run and persisting a new
explicit resolved executor under a documented retry policy. For V1:
```text
Single AnalysisRun Auto:   resolve once before creation.
DatasetExperiment Auto:    resolve once at experiment creation and freeze one
                           exact executor for the whole experiment.
```
Rationale: reproducibility, evaluation comparability (all items under one
executor), resource predictability, provenance, and recovery simplicity.

## Dataset Auto Freeze Semantics

`create_experiment` currently requires an explicit `executor` and validates it
(`service.py:193-204, 242-261`) and freezes `executor` +
`runtime_descriptor_json` on the experiment (`model.py:50-51`). For Auto:
```text
User requests DatasetExperiment with execution_mode = auto
  ↓ resolver runs ONCE (same policy) against the manifest/task size
  ↓ resolved executor + runtime descriptor + certificate/release identity frozen
  ↓ all Items use exactly that executor for their entire lifetime
  ↓ recovery/retry reuse the frozen executor; retry does NOT re-resolve Auto
```
Smallest future seam (designed, not implemented): accept
`execution_mode: "manual"|"auto"` (default `"manual"`) in the create request;
when `auto`, call the Auto resolver before persistence and store the resolved
executor exactly as today; persist `requested_execution_mode` + `auto_reason_code`
in experiment execution metadata. No per-item Auto selection.

## Plugin × Executor Support Matrix

From declarations (`definition.py`), certificates (`execution_certificates.json`),
and real qualification. Legend: `T` technically supported, `C` certified,
`R` real-qualified, `CD` config-dependent, `NS` not supported, `NC` not certified.

| Plugin | local_cpu | local_gpu | remote_gpu |
|---|---|---|---|
| CPN `1.0.0` | T + C + R | T + C + R | T, **NC** (no CPN remote certificate) |
| ZoomSpec `1.0.0` | T, **NC** | T + C + R | T + C + R (runtime `remote:autodl_primary:5bb5be4…`, needs remote profile/config) |
| STFT Energy `1.0` | T + C + R | NS (declares local_cpu only) | NS |
| Dummy `1.0` | T + C + R | NS | NS |

Notes: `remote_gpu` is `CD` for ZoomSpec (requires configured `RemoteProfile`,
SSH key, known_hosts, repo/job roots, remote python, required runtime commit,
dataset/asset mappings). Auto may only select valid cells (never `T` without `C`).

## Certificate Portability

Runtime identity is per-installation: `runtime_ref` (e.g.
`local:autodl_primary:gpu:7b958347b5af`) is an operator-owned generation identity
derived from that runtime's material (`scripts/bhq3_local_gpu_runtime_identity.py`
and the BHQ-3 evidence), and the certificate binds it exactly (7-field key,
`remote_execution/runtime.py:114-134`). Consequences:

- **The current AutoDL certificate does NOT certify another machine.** Student A
  (LCPU), Student B (LGPU), this AutoDL server, and another rented GPU server each
  produce a different `runtime_ref`; a certificate for one runtime does not match
  another (`is_certified` exact-match, `runtime.py:148-168`).
- **Do not weaken exact certification for convenience.**

Approaches:
- **Approach A (recommended, V1): per-installation qualification.** Each runtime
  runs a `runtime doctor` → computes its runtime identity → probes capabilities →
  produces qualification evidence → an operator provisions exact certificates for
  that generated `runtime_ref`. This is the only approach consistent with the
  current model.
- **Approach B (future, only if the runtime identity model supports it):
  reproducible reference runtime family.** Valid only if multiple installations
  can be proven to share the exact frozen scientific runtime material; not
  assumed for V1.
- **Approach C (strongly disfavored): weakened/generic certificates.**
  Rejected: it would certify combinations never observed and break the exact
  provenance guarantee.

A runtime/plugin MUST NOT self-certify because execution succeeded once:
certificates remain platform-owned evidence records bound to a release + runtime
+ executor. Installation/bootstrap flow (design, not implemented):
```text
runtime doctor
  → runtime identity (python/torch/ultralytics/numpy/scipy/GPU/driver)
  → capability probe (interpreter, CUDA, assets, label spaces)
  → qualification evidence (real acceptance on that runtime)
  → operator-owned certificate provisioning (exact runtime_ref)
```

## Local-GPU Recovery Design (G1, G2, G7)

**P1 — generic local stale-run startup recovery (G1).** Generalize
`remote_execution/recovery.py::mark_stale_local_cpu_runs_interrupted` to
`executor IN ("local_cpu","local_gpu")` (`ANALYSIS_INTERRUPTED`); keep a
compatibility alias; `remote_gpu` unchanged; update call site `app/main.py:143`
and the three impacted recovery tests.

**P2 — generic DatasetExperiment pending-run repair (G2).** Change
`dataset_experiments/recovery.py:176` to accept local executors, so
`repair_local_pending_runs` relaunches the safe first-launch path and fail-closes
the ambiguous marker path for `local_gpu` exactly as for `local_cpu`.

**P3 — provider-disappearance safety at the durable launch boundary (G7).**
Required ordering change: verify the frozen executor's provider is still
registered (and, for local, that the certified capability still exists) **before**
committing the launch intent in `launch_item_attempt` (`service.py:572-695`), or
fail-close immediately on a provider miss. Intended semantics:
```text
frozen executor provider absent/unavailable at launch:
  → do NOT claim the launch intent;
  → fail the item with EXECUTION_CAPABILITY_UNAVAILABLE (or
    DATASET_EXPERIMENT_EVALUATION_FAILED at evaluation time);
  → experiment proceeds deterministically (completed_with_failures);
  → retry_failed / retry_evaluation available once the executor is restored;
  → NEVER a false "another actor launched it" claim;
  → NEVER a stall until the next restart.
```
This preserves the existing CAS/launch-intent semantics (no new state machine) and
closes the ambiguity window. A run already `pending` with a durable intent and no
worker remains fail-closed to `interrupted` (`ANALYSIS_LAUNCH_AMBIGUOUS`) by the
existing recovery path.

**Recovery acceptance cases (H4):**

| Case | Setup | Expected | GPU |
|---|---|---|---|
| H4-A local_gpu running interrupted at startup | seed `running` local_gpu run; `create_app` recovery | `interrupted`/`ANALYSIS_INTERRUPTED`; no permanent running | DB-level no; one live in GPU phase |
| H4-A2 local_cpu parity | same | unchanged `ANALYSIS_INTERRUPTED` | no |
| H4-A3 remote_gpu untouched | remote pending/running, no config | untouched; no coordinator | no |
| H4-B experiment crash mid-item | item1/2 completed, item3 running, item4.. queued; restart | item3 → failed (from interrupted run); queued resume; completed never rerun; attempts preserved | live + DB-level |
| H4-C launch boundary (no intent) | attempt `launch_requested_at IS NULL`, no worker | relaunch same attempt/run via CAS; no double launch | no |
| H4-C launch boundary (intent present, pending, no worker) | marker set; run pending; no PID | fail-closed `pending→interrupted` `ANALYSIS_LAUNCH_AMBIGUOUS`; item failed | live + DB-level |
| H4-C2 provider disappeared | provider removed before launch | deterministic fail-close (P3); no false launch claim; no stall | no (simulated) |
| H4-D repeated recovery | `recover_dataset_experiments` twice | idempotent; no duplicate attempts/runs/tokens | no |
| H4-E stale coordinator fencing | old token after rotation | zero-row CAS; `DATASET_EXPERIMENT_FENCE_LOST`; no writes | no |
| H4-F retry_failed | `completed_with_failures` | `→running` CAS; failed→queued; new attempt_number; spawn-compensation restores | no |
| H4-G evaluation recovery | evaluating + orphaned pending+PID eval | normalize→interrupted; restart; no AnalysisRuns | no |

## Remote-GPU Qualification Design (Profile RGPU)

Reopened: remote_gpu is a real V1 deployment profile. Audit findings:

- **Support:** ZoomSpec is technically supported, certified (`remote_gpu/cuda/
  float16`, runtime `remote:autodl_primary:5bb5be4b04d04a071bc9d8f4f61172595ecee037`,
  evidence `m9.1-live-gate`), and real-qualified at M9.1. CPN is technically
  supported but **not certified** for remote_gpu. STFT/Dummy are not supported.
- **Runtime binding:** the remote certificate binds the remote runtime commit
  `5bb5be4…`; the deployed remote repo must be at that exact commit
  (`verify_remote_runtime_commit`, `remote_execution/assets.py:168-188`).
- **Config required:** `RemoteProfile.from_env` (`remote_execution/profile.py:
  220-293`) requires `WSP_REMOTE_PROFILE_NAME/HOST/PORT/USER/SSH_KEY_PATH/
  KNOWN_HOSTS_PATH/REPO_ROOT/JOB_ROOT/PYTHON_PATH/REQUIRED_RUNTIME_COMMIT`, plus
  dataset roots and generic namespaced asset paths; SSH uses `BatchMode=yes`,
  `StrictHostKeyChecking=yes`, explicit known-hosts (`transport.py:115-123`).
- **Cross-host identity:** recording identity is verified by
  `recording_fingerprint` + `source_data_sha256` (`remote_execution/identity.py`),
  so the remote host must hold identical IQ bytes; assets are verified by
  manifest SHA on the remote host.
- **Required V1 end-to-end (small):**
```text
CPU-only local control plane
  → remote_gpu provider configured (profile)
  → deployment-qualified projection includes remote_gpu for ZoomSpec
  → live availability (SSH probe)
  → explicit Manual selection (and Auto consideration)
  → AnalysisRun executor=remote_gpu
  → remote execution on GPU host
  → result ingestion
  → normal readback (GET /api/analysis-runs/{id}, detections, provenance)
```
- **Small remote DatasetExperiment:** one 2–4 item ZoomSpec experiment over the
  frozen subset via remote_gpu, to qualify transport + batch lifecycle (NOT
  endurance). Keep it tiny.
- **Exclusions:** CPN remote is not certified → Auto/Manual must fail closed for
  CPN+remote_gpu with `EXECUTION_NOT_CERTIFIED`; no weakened certificate.

## Loopback vs Two-Host

- **Loopback** (`server → itself over SSH`) validates transport mechanics, host-key
  strictness, runner argv/env, asset/dataset identity, and result ingestion. It
  does NOT prove true deployment portability across independent machines.
- **True two-host** (`student laptop / local control plane → rented GPU server`)
  is the real Profile RGPU acceptance. Because the control plane must run on the
  user's machine, and server-side automation cannot execute a laptop-side test,
  this is an **operator-assisted acceptance gate**: the operator runs the
  documented local control-plane startup + remote profile config on their machine
  and reports evidence (SSH reachability with strict host-key verification,
  availability, one ZoomSpec remote AnalysisRun completing, readback).
- Server-side OpenCode must not claim it executed a laptop-side test. Evidence
  tiers: (1) server-side loopback mechanics (automatable), (2) operator-assisted
  two-host (human-in-the-loop).

## AutoDL Cold Mode Switch and GPU→No-GPU Handoff Seal

The AutoDL `GPU ↔ no-GPU` mode change is a **cold hardware switch**
(shutdown → change rented mode → cold restart), not a runtime toggle. Model it as
milestone H5.5.

**Pre-shutdown seal (H5.5-S):**
```text
all qualification DatasetExperiments terminal (completed/completed_with_failures/failed)
all DatasetEvaluations terminal
all AnalysisRuns terminal
zero qualification coordinator PIDs (app.dataset_experiments.worker)
zero qualification local worker PIDs (app.analysis.local_inference_worker)
zero qualification GPU compute processes (nvidia-smi --query-compute-apps empty)
no active qualification DB writer
```
**Persistence requirement:** no required state lives only in `/tmp`. Persist under
`/root/autodl-tmp/...` (qualification DB, dataset registration rows, evidence JSON,
manifests, acceptance artifacts). Record DB path/size, relevant row counts,
evidence inventory, artifact inventory, and hashes. Verify no `memory.events`
`oom`/`oom_kill`/`max` deltas and no orphan PIDs at seal time.

**Post-cold-boot continuity (H5.5-C):** first boot in the new mode verifies the
DB opens, migrations are additive/no-op, historical rows are readable, and no
qualification process resumed. No automatic execution.

## No-GPU Mode Semantics

Two distinct cases, both must keep the platform healthy:

**Case A — no local_gpu configuration exists.**
```text
local_gpu provider NOT registered (build_local_providers requires both
  WSP_LOCAL_GPU_PYTHON_PATH and WSP_LOCAL_GPU_RUNTIME_REF; local_executor.py:226-241)
```
`/api/pipelines`: `executors_supported` excludes `local_gpu` (it never appears).
`/api/executor-availability?executor=local_gpu`: `available=false`,
`reason_code=EXECUTION_CAPABILITY_UNAVAILABLE`. Auto removes local_gpu from the
runnable set. Manual `local_gpu` fails closed.

**Case B — local_gpu configuration remains but CUDA is unavailable.**
```text
provider registered; live GPU probe fails (local_executor._probe_gpu)
```
`/api/pipelines`: the deployment-qualified projection still lists `local_gpu`
(certificate + provider exist; projection is config/cert only). `/api/
executor-availability?executor=local_gpu`: `available=false`,
`reason_code=EXECUTION_CAPABILITY_UNAVAILABLE` with the probe reason. Auto removes
it from the runnable set. Manual `local_gpu` fails closed. No crash, no hidden
fallback, no recorded failed run.

In both cases the control plane boots and all read/CPU paths work.

## H1–H7 Architecture (updated)

| Milestone | Purpose | Production change | Real GPU | CPU/No-GPU |
|---|---|---|---|---|
| H0 | Audit (this spec) | none | no | yes |
| H1 | Single-recording residual + fail-closed negatives | none | no (probe folded into reuse) | yes |
| H2 | Dataset → one model (CPN ×16, concurrency 1) | none | yes | yes (CPU variant) |
| H3 | Multi-model: Experiment A reuses H2; Experiment B ZoomSpec | none | yes | no |
| H4 | Recovery/retry/fencing/idempotency + provider disappearance | **P1+P2+P3** | partial (live crash) | yes (DB-level) |
| H5 | Auto policy + concurrency=2 + endurance | Auto resolver (new, non-science) | yes (concurrency/endurance) | partial |
| H5.5 | GPU→no-GPU cold handoff seal | none | no | yes |
| H6 | No-GPU backend seal / Auto no-GPU semantics | none | no | yes |
| H7 | Backend V1 evidence + API/frontend executor contract freeze | none | no | yes |

## Single-Recording Acceptance (H1)

BHQ-3 already provides positive real evidence. H1 adds only fail-closed negatives
(all CPU-testable; the GPU availability probe is removed as redundant — BHQ-3 owns
real local_gpu health):

| Case | Expected | GPU |
|---|---|---|
| unknown/unsupported executor (manual) | `EXECUTION_CAPABILITY_UNAVAILABLE` (`runtime.py:222-229`) | no |
| executor with no exact certificate | `EXECUTION_NOT_CERTIFIED` (`runtime.py:336-347`) | no |
| wrong/unknown ModelRelease | `MODEL_RELEASE_NOT_FOUND`/`MODEL_RELEASE_MISMATCH` | no |
| asset identity mismatch | `PIPELINE_ASSET_MISMATCH` | no |
| input label-space incompatible | `INPUT_INCOMPATIBLE` | no |
| descriptor generation mismatch | `RUNTIME_DESCRIPTOR_INVALID` | no |
| terminal-run immutability | no rewrite | no |
| Auto with zero runnable executors | `AUTO_NO_RUNNABLE_EXECUTOR` (fail closed, explainable) | no |

## Dataset Single-Model Acceptance (H2)

Deterministic metadata-only subset (16 stems, 16 distinct shape keys):
`0,1,2,3,9,11,12,15,32,42,79,80,83,99,109,280` (duration 0.020–0.150 s,
bandwidth 20–80 MHz, signal count 6–10). Register exactly these via existing
external-path semantics; `platform.db` and G6/BHQ-3 DBs untouched.

Primary model: **CPN `cpn_bandwidth_tier 1.0.0 / golden`** (fastest, dual-certified,
LS-STFT+CPN). Contract:
```text
pending → running → evaluating → completed
expected_items == completed == 16; failed == 0
one successful Attempt/item (attempt_number == 1); one AnalysisRun/Attempt
experiment.executor frozen; runtime descriptor equals provider descriptor
plugin cpn_bandwidth_tier/1.0.0; release golden; asset manifest 7ab8a6a4…fc55bb
DatasetEvaluation: evaluated == 16; missing == 0; coverage == 1.0; comparable == true;
  classification_applicable == false; reason label_space_mismatch; localization ap50/ap50_95 present
recording_manifest_hash stable across re-prepare
no duplicate launches; no orphan workers after terminal
```
H2 runs once on local_gpu (GPU phase, concurrency=1) and once on local_cpu
(no-GPU phase, CPU variant) in separate dedicated DBs.

## Multi-Model Dataset Acceptance (H3)

Two independent DatasetExperiments over the identical frozen 16-stem manifest:
Experiment A = CPN `local_gpu`; Experiment B = ZoomSpec `local_gpu`.
```text
A.manifest_hash == B.manifest_hash; identical membership + manifest_order
independent provenance (A 7ab8a6a4…, B 16cc0534…); independent runs/evaluations
both coverage == 1.0; comparable == true
compare(A,B): comparable == true; reasons == [];
  localization_ap50 / localization_ap50_95 numeric + deltas;
  class_aware_map50 / class_aware_map50_95 / matched_accuracy deltas null (CPN N/A)
per-recording via .../items + POST /api/algorithm-lab/compare (localization + cases)
```
Comparable: localization AP + per-recording TF boxes/membership. N/A: class-aware
AP, matched accuracy, confusion, per-class SpaceNet-14 (CPN emits
`cpn_bandwidth_tier_v1`). Existing APIs are sufficient; no new endpoint in V1.

## Concurrency Design (H5)

- H5-C2 only (concurrency=2); concurrency=1 baseline is provided by H2.
- 8-stem slice `0,1,2,3,9,11,12,15`, CPN `local_gpu`, `max_concurrency=2`.
- Prove: max simultaneously live `local_inference_worker` PIDs ≤ 2 (sample
  `/proc/*/cmdline` + `nvidia-smi --query-compute-apps`); all items terminal; one
  run/attempt; no duplicate ownership; no orphan workers; DB consistent.
- V1 ceiling is 2 (SQLite single-writer + single GPU); no higher concurrency.

## Endurance Design (H5 continuation)

- 40 real CPN `local_gpu` executions at concurrency=2, cycling the 16-stem set
  across three sequential experiments.
- Machine-auditable criteria:
```text
all intended runs terminal; 0 failed runs/items
0 orphan qualification worker PIDs; 0 remaining qualification GPU compute processes after quiescence
memory.events.max delta == 0; oom == 0; oom_kill == 0
0 SQLite "database is locked" failures
all evaluations coverage == 1.0
leak analysis: compare identical stems across cycles, separately for
  page cache vs committed process memory vs worker RSS vs GPU memory;
  no one-way growth trend
```
Observation cadence ~5 s + before/after each experiment: worker PID set/lifetimes,
`/proc/<pid>/status` `VmHWM`, GPU process memory, GPU used/free, cgroup
`memory.current`/`committed_floor`/`effective_headroom`, `memory.pressure`,
`memory.events`, DB counts.

## Deterministic Dataset Qualification Subset Strategy

Metadata-only (`.json` + `stat` size), deterministic, re-runnable; no IQ
scan/hash. Selection: candidate pool = test stems with ≥1 signal; metrics
(`num_signals`, `duration_s`, `bandwidth_hz`); quartile buckets; anchors `{0,1,2}`;
one representative per distinct shape key; evenly-spaced fill to 16. Qualification
registers only these stems in a dedicated DB; concurrency/endurance slices are
deterministic prefixes/cycles of the same frozen set. A committed acceptance tool
(not production) performs selection + registration in the implementation phase.

## Resource / Memory Observability

Reuse Amendment-A1 tooling (`scripts/bhq3_memory_gate.py`: `read_snapshot`,
`evaluate`, `MemoryMonitor`, `run_guarded_subprocess`) plus:
- GPU: `nvidia-smi --query-gpu=memory.used,memory.free,utilization.gpu` and
  `--query-compute-apps=pid,used_memory` (before/during/after).
- Workers: PID identity via `/proc/<pid>/cmdline`, RSS `VmHWM`, lifetime.
- Cgroup: `memory.current`, `committed_floor`, `effective_headroom`, `memory.stat`,
  `memory.pressure`, `memory.events`.
- DB: status/attempt/run invariants.
`/usr/bin/time` is unavailable; peak RSS via `/proc` (as in BHQ-3).

## Failure Injection Strategy

- **Worker kill:** SIGKILL the exact verified local worker PID (`/proc/<pid>/cmdline`
  contains `app.analysis.local_inference_worker <exact run_id>`).
- **Coordinator kill:** SIGKILL the exact `app.dataset_experiments.worker` PID
  (verified by argv + token).
- **Restart:** fresh `create_app` against the same dedicated qualification DB.
- **Ambiguous launch / provider disappearance:** seeded by construction (intent
  committed, no worker) and by removing the provider (P3).
- **Duplicate recovery / fencing:** call recovery twice in/after the heartbeat
  cutoff; rotate token and attempt stale writes.
- **Fail-closed inputs:** wrong release/asset/label/executor/certificate.
- **Crash safety (mandatory):** dedicated qualification DB, dedicated work/data
  root, exact `run_id`, exact PID verified before any signal; never signal
  OpenCode, Jupyter, autopanel, unrelated Python, the interactive backend, or
  system services; run under an isolated qualification app process. No `kill -9`
  of unrelated processes; no `drop_caches`.

## GPU Evidence Reuse

- H2 CPN ×16 at concurrency=1 serves simultaneously as: Dataset→one-model
  evidence, concurrency=1 baseline, and H3 Experiment A.
- H3 runs **only** ZoomSpec Experiment B (×16).
- H5 runs **only** concurrency=2 (8 executions).
- BHQ-3 owns real local_gpu health + single-recording CPN/ZoomSpec acceptance;
  the H1 GPU probe is removed.
- DB/state-machine ambiguity cases stay CPU-only where GPU is irrelevant.

## GPU Qualification Matrix (local_gpu)

| Qualification | GPU? | Why | Executions |
|---|---:|---|---:|
| H2 CPN local_gpu ×16 (c=1) | yes | real batch CUDA + evaluation | 16 |
| H3 ZoomSpec local_gpu ×16 | yes | real CUDA multi-model | 16 |
| H4 live worker-kill recovery | yes | real GPU worker crash | ~4 |
| H4 ambiguous-launch live | yes | real pending GPU run | ~2 |
| H5 concurrency=2 (8) | yes | ≤2 live GPU workers | 8 |
| H5 endurance (40 CPN, c=2) | yes | lifecycle/resource trend | 40 |
| **local_gpu total** | | | **~86** |

## Remote-GPU Budget

| Qualification | GPU? | Who | Executions |
|---|---:|---|---:|
| Loopback transport mechanics (server→self) | (remote GPU host) | server-side automatable | ~2 (probe + 1 ZoomSpec run) |
| Small remote DatasetExperiment (2–4 items, ZoomSpec) | (remote GPU host) | server-side + remote host | 2–4 |
| True two-host (laptop → rented GPU) | (remote GPU host) | **operator-assisted** | ≥1 ZoomSpec remote AnalysisRun |
| **remote_gpu total** | | | **~5–7 remote executions + 1 operator session** |

Remote budget is reported separately from local_gpu; it requires the rented GPU
target to still be in GPU mode (i.e., before the H5.5 cold switch).

## H5.5 Handoff

See "AutoDL Cold Mode Switch and GPU→No-GPU Handoff Seal". H5.5-S (pre-shutdown
terminal/zero-PID/persist-under-`/root/autodl-tmp` seal) runs at the end of the
GPU phase, before the mode change; H5.5-C (post-cold-boot continuity) is the first
step of the no-GPU phase. All remote_gpu acceptance must complete before H5.5-S.

## API / Frontend Executor Contract

Frontend-facing endpoints to freeze:
```text
/api/health, /api/recordings, /api/pipelines, /api/executor-availability,
/api/analysis-runs, /api/imported-runs, /api/dataset-experiments,
/api/dataset-benchmarks, /api/algorithm-lab/compare
```
Frozen executor semantics:
```text
technical_execution_capabilities ; executors_supported ; recommended_executor
live availability: available, reason_code, reason_message
manual execution selection (exact executor; fail closed)
Auto request + Auto resolved executor + auto_reason_code/auto_reason
AnalysisRun exact executor provenance (execution metadata)
DatasetExperiment frozen executor
```

**Designed (not implemented) Auto surface.** Keep the current exact-executor
contract and add an optional mode:
```text
POST /api/analysis-runs
  { recording_id, pipeline_id, executor?: "local_cpu"|"local_gpu"|"remote_gpu",
    execution_mode?: "manual"|"auto" (default "manual"), model_release_id?, parameters }
POST /api/dataset-experiments
  { ..., executor? , execution_mode?: "manual"|"auto" (default "manual"), ... }

Resolver/explanation (future): GET /api/executor-selection
  ?recording_id=&pipeline_id=[&dataset_split=&model_release_id=]
  → { resolved_executor, reason_code, reason,
      candidates: [ { executor, technical, configured, certified, available,
                      reason_code, reason_message } ] }
```
Rules: `execution_mode="auto"` resolves before persistence; a persisted run/
experiment never has `executor="auto"`; manual stays exact-by-name. The existing
`GET /api/executor-availability` remains the per-executor availability probe.

Frontend UX model (future, not implemented):
```text
Execution Environment
  ● Auto            (default for ordinary users when a valid explainable decision exists)
  ○ Local CPU
  ○ Local GPU
  ○ Remote GPU
```
Frontend needs, per model: which environments are technically supported /
configured / certified / currently available and why not, which executor Auto
selects and why, and — after launch — the exact resolved executor.

## API Freeze Strategy

- Freeze the 38 router endpoints + `/api/health` (recordings, dsp, ground_truth,
  datasets, detections, analysis, imported_runs, evaluation/algorithm-lab,
  benchmarks, dataset_experiments).
- Freeze request/response schemas and error mapping (`PlatformError` →
  `app/main.py:169-174`), including the public metadata allowlist.
- Produce a canonical deterministic OpenAPI subset snapshot plus a human-readable
  `Backend V1 API Contract`. No breaking change without a documented version bump.

## Installation / Deployment Documentation (future operator docs)

Design operator docs (no secrets committed; no claim that arbitrary machines are
certified):
```text
LCPU setup            control plane + local_cpu python path + runtime_ref
LGPU setup            + local_gpu python path + runtime_ref; local_cpu coexists
RGPU local control-plane setup   RemoteProfile env (host/user/key/known_hosts/
                                 repo/job roots/python/required commit)
RGPU rented GPU-server setup     repo at required commit, assets, dataset roots
runtime doctor        interpreter probe, CUDA probe, asset/label checks
runtime identity      generation material -> runtime_ref
certificate provisioning   operator-owned exact certificate per runtime_ref
asset-path configuration    namespaced WSP_LOCAL_ASSET_PATHS_JSON / remote maps
remote profile / SSH / known_hosts   strict host-key verification, no weakening
```

## Recommended Final Seal Model

**Recommendation: Option B — two explicit seals**, both V1-required before
frontend productization (because Profile RGPU is now an explicit user scenario):

```text
Seal 1 — Backend V1 Core Functional Seal
  local_cpu + local_gpu; Scenarios A/B/C; recovery (P1/P2/P3); Auto policy;
  concurrency=2; endurance; no-GPU startup semantics; regression; API freeze.

Seal 2 — Backend V1 Portable Deployment Seal
  remote_gpu real path (Profile RGPU): loopback transport mechanics + a small
  remote DatasetExperiment + operator-assisted true two-host acceptance;
  per-installation certificate provisioning model; installation/deployment docs.
```
Why two seals: the remote path carries a distinct cost profile (rented GPU +
operator-assisted two-host) and a distinct evidence type (transport/deployment),
and it can be validated independently without re-running local science. Splitting
avoids blocking the core seal on operator scheduling while keeping remote work
inside V1. A single combined seal (Option A) is acceptable but riskier for
schedule and evidence attribution.

## Cost-Aware GPU Budget

Local_gpu phase: ~86 real executions (H2 16, H3 16, H4 ~6, H5 8, endurance 40).
At BHQ-3 measured worker walls (CPN ~6.9 s, ZoomSpec ~8.2–18.2 s), the local GPU
phase is on the order of **~20–30 minutes** of GPU wall (≈ one server-hour).
Remote_gpu: ~5–7 remote executions + one operator-assisted two-host session,
reported separately, and must occur **before** the H5.5 cold switch. The rejected
brute force (2500 × 2 ≈ 5000 runs at ~7 s ≈ 9.7 h GPU) is ~20–50× the cost for no
added acceptance value; no-GPU work runs at ~0.10 RMB/h.

## Final Seal Criteria

```text
Manual execution (exact executor, fail closed)             evidence + negatives
Auto execution (deterministic, explainable, no fallback)   evidence + reason codes
Auto resolves before persistence; resolved executor exact  no executor="auto"
DatasetExperiment Auto freezes ONE executor                evidence
Single Recording CPU/GPU/remote paths                      evidence + negatives
Dataset → one model (16-item, c1)                          completed; coverage 1.0
Same Dataset → multiple models                             manifest equal; compare valid
DatasetEvaluation                                          coverage 1.0; localization; N/A classes
retry semantics                                            retry_failed / retry_evaluation
local_gpu recovery (P1/P2) + provider disappearance (P3)   no permanent stale; no false claim
DatasetExperiment recovery / fencing / idempotency         green
concurrency=2 (≤2 live workers)                            green; no orphan/duplicate
endurance (40 executions)                                  auditable criteria met
certificate/release fail-closed                            negatives green
remote_gpu Profile RGPU (loopback + small experiment)      green
true two-host (operator-assisted)                          evidence recorded
no-GPU startup (Case A and Case B)                         healthy; local_gpu fail closed
GPU→no-GPU cold handoff seal + continuity                  green
full backend regression                                    0 failed / 0 errors
API + frontend executor contract freeze                    green; contract doc
frozen science unchanged                                   NO_FROZEN_SCIENCE_CHANGED
known P0/P1 backend defects                                0
```
Engineering standard: **all defined V1 critical paths have acceptance evidence,
all known blockers are resolved, and there are no unresolved known P0/P1 backend
risks** (not a literal "zero bugs" claim).

Artifacts:
```text
Backend V1 Acceptance Evidence          docs/superpowers/acceptance/…-backend-v1-acceptance.md
Backend V1 Portable Deployment Evidence  docs/superpowers/acceptance/…-backend-v1-portable-deployment.md
Backend V1 API Contract                 docs/superpowers/acceptance/…-backend-v1-api-contract.md
OpenAPI subset snapshot                 docs/superpowers/acceptance/…-backend-v1-openapi.json
BACKEND_V1_SEAL_SHA / PORTABLE_SEAL_SHA  determined externally after commit (never embedded)
```

## Risks / Limitations

- **SQLite single-writer:** V1 concurrency ceiling is 2; watch for `database is
  locked`.
- **Transient cgroup PSI** at `memory.high` can fail A1 admission mid-GPU-phase;
  gate stays fail-closed; budget buffer.
- **P1/P2/P3 change existing tests:** four recovery test files must be updated to
  generic-local scope; expected, not a weakening.
- **Auto policy calibration:** thresholds (`GPU_PREFER_DURATION_S` 0.05 s,
  `GPU_PREFER_SAMPLES` 3.0 M, `GPU_BATCH_ITEMS` 8) are initial deterministic
  defaults derived from the BHQ-2/BHQ-3 workload sizes; they are documented and
  adjustable, not learned. Mis-calibration only affects preference, never
  correctness (availability/certification always gate).
- **Certificate portability:** each machine needs its own runtime identity +
  certificates; there is no cross-machine certificate reuse in V1.
- **Cold hardware switch:** a failed AutoDL mode change is an infrastructure
  event; H5.5-S ensures no required state is lost.
- **Two-host is operator-assisted:** evidence depends on operator participation;
  server-side automation cannot substitute for it.
- **remote_gpu config variance:** SSH/known_hosts/asset/dataset identity must be
  correct on both hosts; failures are fail-closed with explicit reason codes.

## Proposed Implementation Sequence (plan decomposition, not created here)

Do not create implementation plans in this pass. Expected decomposition (each
plan independently seals before the next begins):

```text
Plan A  Recovery + Auto-policy + runtime/deployment hardening + qualification tooling
        (P1/P2/P3; Auto resolver design+impl; runtime doctor/identity; selector +
        registration tooling; no-GPU startup semantics; unit/integration tests)
Plan B  Concentrated local-GPU qualification
        (H2 CPN ×16 c=1; H3 ZoomSpec ×16; H4 live recovery; H5 concurrency=2;
        endurance ×40; GPU evidence; then H5.5-S pre-shutdown seal)
Plan C  Remote-GPU / two-host portability qualification
        (profile config; loopback mechanics; small remote DatasetExperiment;
        operator-assisted true two-host; per-installation certificate model;
        installation/deployment docs) — before H5.5-S
Plan D  GPU→no-GPU cold handoff + no-GPU Backend Seal + API/frontend contract freeze
        (H5.5-C continuity; no-GPU Case A/B; H2-CPU; H1 CPU; full regression;
        API contract; final evidence; BACKEND_V1_SEAL_SHA)
```
Note ordering tension: Plan C requires the GPU target to still be in GPU mode, so
Plan C must complete (or at least its live portions) before Plan B's final
H5.5-S shutdown. Plan A has no GPU dependency and may run first.

---

### Spec self-review record

- Manual + Auto both preserved; Auto is deterministic/explainable with closed
  reason codes; Auto resolves before run persistence; resolved identity is always
  an exact executor; no `executor="auto"` persisted.
- No silent runtime fallback; DatasetExperiment Auto freezes one executor.
- Plugin × executor matrix explicit; certificate portability stated honestly
  (per-installation identity; no weakened certificates).
- remote_gpu treated as a real deployment profile (RGPU), with loopback ≠ two-host.
- AutoDL mode switch modeled as a cold reboot with a pre-shutdown seal and
  post-boot continuity check; no required state in `/tmp`.
- Recovery handles provider disappearance (P3) and the durable launch ordering.
- GPU evidence reused (H2 used for c=1 baseline + H3-A); H1 GPU probe removed;
  local/remote GPU budgets separated.
- API contract includes manual/Auto semantics; frontend executor UX defined.
- No implementation occurred; no TODO/TBD placeholders remain.
