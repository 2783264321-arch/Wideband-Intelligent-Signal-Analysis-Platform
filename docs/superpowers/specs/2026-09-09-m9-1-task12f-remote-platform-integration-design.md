# M9.1 Task 12F — Remote Platform Integration Design

Date: 2026-09-09
Status: Approved design specification (product/architecture owner confirmed)
Base: `feature/m9-1-live-remote-gpu-inference @ 8ac3d1eea6082ea781dc44acf61a0e3072e7d690`
Scope: Design specification only. No production implementation.

> This file refines and supplements
> `2026-09-06-m9-1-live-remote-gpu-inference-design.md`. Where the older design
> is general or conflicts with the approved Task12F decisions below, **this
> Task12F specification is authoritative for the remaining M9.1 remote
> integration work.** The older design remains valid for the previously frozen
> M9.1-A/B protocol and runner foundations.

## 1. Purpose

Task 12F connects the already-accepted Task 12E scientific pipeline
(`zoomspec_yolo26n_aug_combined_frn_v3`, the frozen detector/AHLP/FRN/postprocess
composition) to platform `remote_gpu` execution over the existing SSH runner.

No changes to LS-STFT / CPN / AHLP / FRN / postprocess scientific semantics.

The final domain path remains:

```text
Recording -> AnalysisRun -> DetectionResult
```

A remote batch is transport / execution provenance only. **No `RemoteJob`,
`GpuRun`, `RemoteAnalysisRun`, or `BatchRun` ORM entity is created.**

The capability after Task 12F is:

```text
platform directly creates AnalysisRun(executor=remote_gpu)
  -> drives the frozen pipeline on AutoDL RTX 5090 over SSH
  -> writes the verified result back to the SAME AnalysisRun
  -> existing DetectionResult API and Algorithm Lab consume it as an ordinary completed run
```

## 2. Decomposition

Task 12F is split into three independently testable subtasks:

| Subtask | Scope |
|---|---|
| 12F-A | Local / control-plane remote lifecycle: lightweight registry registration, `create_run(remote_gpu)` freeze path, **local control-plane probe integration** (production `RemoteExecutorProbe` adapter, `RemoteProfile`/`SshRunner` probe invocation, `AnalysisService`/API availability wiring, mapping remote probe success/error into `ExecutorAvailabilityRead`), local coordinator, restart semantics, reconciliation/ingest wiring. |
| 12F-B | Remote GPU production `ItemExecutor` + result publication **and the remote probe implementation**: `runner._cli_probe` production implementation, `RemoteWorkerContext`, runtime commit verification, asset manifest/assets verification, SpaceNet root verification, `spacenet_14` verification, CUDA/device-0 readiness, trusted worker context, SpaceNet resolver, pipeline construction, `PipelineOutput` → Analysis Package serialization, atomic envelope + zip publish. |
| 12F-C | One-recording real remote E2E on an AutoDL RTX 5090 + Algorithm Lab consumption acceptance. |

Each subtask boundary is independently testable. No single subtask may claim live
remote correctness on its own.

The remote availability probe is split across 12F-A and 12F-B by ownership:

- **12F-A** owns the *local control-plane* probe integration: the
  `RemoteExecutorProbe` adapter, `RemoteProfile`/`SshRunner` probe invocation, and
  `AnalysisService`/API wiring. It **does not require** the remote server probe
  implementation to already succeed. It is independently testable with
  fake/mocked transport responses.
- **12F-B** owns the *remote* probe implementation: the `runner._cli_probe`
  production body and the readiness semantics it verifies on the server. It does
  not depend on 12F-A.

A real SSH probe success is a **post-12F-B integration gate** (see §14), not a
prerequisite for closing 12F-A.

## 3. Registry / Definition

The current `PipelineRegistry` stores concrete `Pipeline` objects, and the
`ZoomSpecFrozenPipeline` constructor loads GPU models. Registering it directly
would require torch/ultralytics and checkpoints at control-plane construction.

### 3.1 Shared definition owner (recommended)

Create **one lightweight shared ZoomSpec definition owner**:

```text
backend/app/pipelines/zoomspec_yolo26n_aug_combined_frn_v3/definition.py
```

It owns `ZOOMSPEC_FROZEN_DEFINITION` and a remote-only `Pipeline` stub
(`ZoomSpecRemoteOnlyPipeline`) that exposes the frozen `definition` and a
`run()` that raises `EXECUTOR_UNAVAILABLE` (it must never execute locally).

- `ZoomSpecFrozenPipeline.definition` returns the same shared
  `ZOOMSPEC_FROZEN_DEFINITION` object, so registry and remote construction agree
  on the frozen definition.
- `create_pipeline_registry()` registers only the lightweight remote-only stub.
- The local control plane must **not** import torch/ultralytics, and must not
  construct CPN/FRN model objects. The real `ZoomSpecFrozenPipeline` is
  constructed only inside the remote `ItemExecutor`.

### 3.2 Frozen definition

```text
id                     = zoomspec_yolo26n_aug_combined_frn_v3
version                = 1.0.0
label_space            = spacenet_14
cpu_supported          = False
executors_supported    = ("remote_gpu",)
recommended_executor   = remote_gpu
task_capability        = detection_classification
```

## 4. CreateRun / Request Freeze

`AnalysisService.create_run` gains an explicit `remote_gpu` path. It must **not**
use the local scientific worker (`LocalJobManager` / `app.analysis.worker`).

Before coordinator launch, the control plane freezes and persists, into
`execution_metadata_json`, the immutable request/provenance:

```text
request_id
batch_id
item_key
request_sha256
orchestrator_commit
required_remote_runtime_commit
asset_manifest_sha256
pipeline id
pipeline version
remote_profile name
recording_fingerprint
source_data_sha256
SpaceNet logical dataset identity   (dataset_name / dataset_split / dataset_key / label_space)
parameters                          (= {}, frozen)
```

Rules:

- Reuse the existing canonical request identity
  (`app.remote_execution.canonical.compute_request_sha256` /
  `canonical_request_payload`). No new hash scheme.
- `request_id`, `batch_id`, `item_key` are system-generated and
  format-validated; never client-supplied free text.
- Sufficient immutable provenance must be persisted so the coordinator can be
  safely restarted from `execution_metadata_json` alone.
- `parameters_json` continues to represent algorithm parameters only; SSH / batch
  / host provenance must not be mixed into `parameters_json`. For frozen ZoomSpec
  this is `{}`.

## 5. Local Coordinator — Chosen Design (V1)

**Use ONE local coordinator subprocess per remote `AnalysisRun`.**

- Do **not** reconcile via GET/list side effects.
- Do **not** add a FastAPI-global async polling framework for V1.

Coordinator sequence (single deterministic loop):

```text
submit / create-or-attach
  -> poll remote status
  -> on completed: download envelope + zip
  -> ingest_remote_result (verify + persist into the SAME run)
  -> terminal state
```

Transient submit/status uncertainty must **reconcile the same batch_id first**
before marking terminal (see the parent design's submit-failure rules). The
coordinator may be restarted safely from the persisted provenance and must re-query
the same remote batch rather than re-submitting a duplicate or blindly failing.

## 6. Restart Semantics — Mandatory

The current `mark_stale_running_runs_interrupted` implementation marks **every**
`running` AnalysisRun as `interrupted` on startup. That is **invalid for
`remote_gpu`** and must be corrected.

Startup rules:

```text
local_cpu   running          -> interrupted          (unchanged)
remote_gpu  pending/running  -> NEVER blindly interrupted
```

For `remote_gpu` pending/running runs at startup:

```text
respawn / re-attach the local coordinator
  -> query the same remote batch
  -> remote running      -> resume supervision / polling
  -> remote completed    -> download + validate + ingest
  -> remote failed       -> AnalysisRun failed
  -> remote job missing/unrecoverable -> AnalysisRun interrupted (audited)
```

- A remote worker may survive a local API restart; startup must not kill or
  invalidate it.
- `AnalysisRun.status = completed` only after verified local ingest and DB commit
  (download → integrity → identity → schema → physical box/label validation →
  transaction commit).
- The ingest rule that `failed` / `interrupted` / `completed` runs are immutable
  is **unchanged**.

## 7. RemoteProfile vs RemoteWorkerContext

These two concepts must **not** be conflated.

`RemoteProfile` (local control-plane configuration, `app.remote_execution.profile`)
contains:

```text
host / port / user
ssh key path reference
known_hosts / pinned trusted host key path
remote_repo_root / remote_job_root / remote_python_path
dataset_roots mapping
asset_paths mapping (logical asset -> remote absolute path)
```

The remote GPU worker must **never** construct or receive SSH credentials or
profile control-plane fields.

Define a **remote worker deployment context** containing only trusted server-side
paths:

```text
repo root
job root
SpaceNet root
detector checkpoint
FRN checkpoint
LS-STFT normalization
frozen_config
label-space root
asset-manifest path
```

Rules:

- Trusted paths are propagated by **fixed, validated runner environment
  assignments** (the existing `SshRunner` env-var construction /
  `RemoteProfile-from-env` assignments), never by client input.
- Do **not** put absolute deployment paths into `RemoteExecutionBatchV1`. The wire
  request remains logical identity only.
- The worker context is populated on the server from its own validated
  environment; it carries no `host` / `user` / SSH credential reference.

## 8. Production Remote Probe

The remote probe splits into two independently testable halves, one per subtask.

### 8.1 12F-A — Local control-plane probe adapter

12F-A implements the `RemoteExecutorProbe` protocol in
`app.remote_execution.executor.py` with a production adapter that:

- reads `RemoteProfile` / `SshRunner` configuration;
- invokes the runner `probe` subcommand over the existing SSH transport;
- maps a probe success/error/return-code into an `ExecutorAvailabilityRead`
  (`executor`, `available`, `reason_code`, `reason_message`, `remote_profile`,
  `recommended`);
- wires availability into `AnalysisService.executor_availability` and the API
  (a `remote_gpu` availability endpoint may be added).

It is **independently testable with fake/mocked transport responses** (a stub
`SshRunner`/subprocess return), so it does **not require** the remote server
probe implementation to already succeed. Its only contract is that a
deterministic probe transport response is mapped correctly (success →
`available=true`; structured error → explicit `available=false` + reason); it
never crashes the control plane on an unavailable remote.

### 8.2 12F-B — Remote probe implementation

12F-B implements the `runner._cli_probe` production body. `available = true`
means all of, on the server:

```text
SSH / runner reachable
required runtime / repo identity acceptable (remote_runtime_commit matches)
asset manifest + asset bytes valid
SpaceNet root available
spacenet_14 available
CUDA / device 0 usable
```

The probe must **not** load YOLO/FRN models nor run inference.

A failure returns an explicit unavailable reason code / message (e.g.
`REMOTE_EXECUTOR_UNAVAILABLE`, `REMOTE_IMPLEMENTATION_MISMATCH`,
`PIPELINE_ASSET_MISMATCH`, `REMOTE_PROBE_UNAVAILABLE`), never a control-plane
crash. This half owns the real server readiness semantics and defines the
`RemoteWorkerContext` the probe reads.

### 8.3 Post-12F-B integration gate

A **real** SSH probe success (`12F-A` adapter + real remote `runner` probe) is an
integration acceptance performed **after** 12F-B. It is not a prerequisite for
closing 12F-A.

## 9. Remote ItemExecutor

The runner (`app.remote_execution.runner`) remains the filesystem/protocol
lifecycle owner and keeps GPU-library imports lazy. Add a production
`ZoomSpecRemoteItemExecutor` implementing the existing `ItemExecutor` protocol.

Sequence inside `execute(item, job_root)`:

```text
1.  load RemoteWorkerContext (trusted server-side paths only)
2.  verify runtime commit + full asset manifest (fail closed)
3.  resolve SpaceNet logical identity (dataset_name / split / key / label_space)
4.  verify recording_fingerprint + source_data_sha256 (fail closed)
5.  load spacenet_14 label space
6.  load LS-STFT normalization from ls_stft_normalization.json
7.  construct ZoomSpecFrozenPipeline(..., device=0)
8.  output = pipeline.run(recording_input, {}, workspace)
9.  serialize PipelineOutput -> Analysis Package compatible ZIP
10. publish analysis_result.zip + RemoteExecutionEnvelopeV1 atomically
11. runner performs the existing write-once terminal verification
```

- `runner` continues to own status transitions, verification, write-once behavior
  and crash-window handling; the executor owns actual result creation.
- V1 model lifetime = load detector/FRN **once per item (per run)**. No cross-item
  reuse requirement.

## 10. Device

- Canonical V1 remote device = integer `0`.
- CPN / FRN / LS-STFT must resolve the same physical GPU.
- Task 12F **may** centralize device normalization (one canonical
  resolution helper) without changing single-GPU Task 12E numerical semantics.
- Multi-GPU scheduling is **out of scope**.

## 11. Result / Persistence

Reuse the existing analysis package schema (`imported_runs` `Manifest` /
`PackageDetection`), `ingest_remote_result`, and `AnalysisResultWriter`.

- **No second `DetectionResult` persistence path.**
- `PipelineOutput.detections` maps 1:1 to package detections.
- Completed + same payload SHA = idempotent no-op (no duplicate rows).
- Completed + different payload SHA = `REMOTE_RESULT_CONFLICT` (never overwrite).
- `failed` / `interrupted` = immutable.
- Hardware comes from the envelope into `hardware_info_json`.
- Execution provenance stays in `execution_metadata_json`.
- Existing `DetectionResult` API and Algorithm Lab must consume remote results as
  ordinary completed runs.

## 12. API / Frontend

- `GET /api/pipelines` exposes ZoomSpec automatically once registered (the read
  model already returns `executors_supported` / `recommended_executor`).
- An executor-availability endpoint **may** be added in 12F-A (backend-owned
  availability; frontend never guesses).
- No new top-level page.
- No scientific parameter editor (frozen ZoomSpec accepts `{}` only).
- Frontend changes **only** if 12F-C evidence proves the existing UI cannot
  select / start / display a remote run.

## 13. Security

- Keep `shell=False` fixed-argv (existing `SshRunner`).
- Strict host-key verification (existing `StrictHostKeyChecking=yes` +
  known-hosts file). No blind host-key acceptance.
- No request-controlled remote paths.
- No arbitrary remote shell.
- No deployment paths in the wire request.
- No SSH secrets in the worker context.
- Runtime / asset / fingerprint / source mismatch fail closed.
- Control-plane import must remain torch-free.
- `runner` module import must remain GPU-library-free until the lazy `work` /
  `probe` path.

## 14. Acceptance

### 12F-A

- registry exposes ZoomSpec with **zero model load** and no torch/ultralytics import in the control plane;
- the control-plane `RemoteExecutorProbe` adapter maps deterministic (fake/mocked transport) probe responses correctly — success → `available=true`; structured failure → explicit `available=false` + reason code, no control-plane crash;
- probe transport failures return explicit unavailable reasons, not silent successful-availability;
- `create_run(remote_gpu)` freezes a valid request + provenance;
- a coordinator subprocess is used instead of the local scientific worker;
- same request attach is idempotent (`batch_id` + `request_sha256`);
- startup interrupts `local_cpu` only; remote `pending/running` is re-coordinated;
- remote completion ingests once; failed/interrupted mapping correct.

> 12F-A may close without any real remote probe success. It only proves the local
> adapter/wiring and its deterministic mapping.

### 12F-B

- trusted deployment context reaches the remote probe/work path **without SSH credentials**;
- `runner._cli_probe` verifies real server readiness **without loading models** (runtime commit, asset manifest + asset bytes, SpaceNet root, `spacenet_14`, CUDA/device-0);
- runtime / asset / source / fingerprint mismatches fail closed;
- production `ItemExecutor` creates a valid envelope + zip;
- existing runner terminal verification accepts it;
- control-plane and module import boundaries remain clean.

### Post-12F-B integration gate (real SSH probe)

- 12F-A `RemoteExecutorProbe` adapter + **real** remote `runner` probe succeed
  end-to-end over SSH (probe returns `available=true` with the real
  `RemoteWorkerContext`). This is a post-12F-B acceptance, not a 12F-A gate.

### 12F-C

- real SpaceNet sample 0: `POST AnalysisRun(remote_gpu)` → local coordinator →
  SSH submit → detached AutoDL RTX 5090 worker → `ZoomSpecFrozenPipeline` →
  verified download → **SAME AnalysisRun completed** → `DetectionResult` rows
  persisted → Algorithm Lab reads/compares the run.
- Use live CPN batch-1 semantics accepted in Task 12E. Historical batch-16 bitwise
  equality is **not** required.

## 15. Out of Scope

```text
No Redis / Celery
No resident remote HTTP inference server
No generic model / plugin registry
No RemoteJob ORM
No realtime SDR
No training / open-set
No multi-GPU scheduler
No batch experiment UI
No changes to Task 12A-E scientific semantics
```

## 16. Spec Relationship

This file **refines and supplements**
`2026-09-06-m9-1-live-remote-gpu-inference-design.md`. The older design is
general (and, in a few places, recommends batch-first / supervisor-polling and a
blanket per-executor restart policy). Where the older design conflicts with these
approved Task 12F decisions, **this Task 12F specification is authoritative** for
the remaining M9.1 integration work:

- The parent's `RemoteRunSupervisor` / `RemoteRunReconciler` are realized in
  12F-A as a **per-run local coordinator subprocess** (not a FastAPI-global async
  poller).
- The parent's batch-first scheduling is **not** the V1 scope: Task 12F V1 is
  one-recording-per-run, per the Task 12F decomposition and device/model-lifetime
  decisions.
- The parent's asset manifest lists `frozen_config`; Task 12F keeps it as a
  tracked manifest identity but the native runtime consumes
  `detector_checkpoint`, `frn_checkpoint`, and `ls_stft_normalization`.
