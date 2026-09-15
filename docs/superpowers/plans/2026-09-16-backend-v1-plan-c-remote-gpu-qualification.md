# Backend V1 Plan C — Remote-GPU / True Two-Host Qualification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Qualify the accepted Backend V1 `remote_gpu` execution path end-to-end on the real AutoDL GPU target while it is still rented — profile configuration, loopback transport mechanics, a bounded true two-host single `AnalysisRun`, a small true two-host ZoomSpec `DatasetExperiment`, deterministic recovery/reconciliation evidence, and a frozen Plan-C acceptance record — without changing production behavior, without weakening the exact per-installation certificate model, and without starting H5.5-S or turning the GPU off.

**Architecture:** Plan C is primarily a **qualification/campaign** plan. The M9.1 remote-execution infrastructure is already implemented and unit/integration tested at the integration baseline: `RemoteProfile` (validated config only, never secrets), `SshRunner` (fixed argv, `shell=False`, `StrictHostKeyChecking=yes` + explicit known-hosts), `SshRemoteExecutorProbe`, `RemoteGpuJobManager`, the remote `runner` (`probe`/`submit`/`status`/`work`), `Coordinator`, `request_builder` (frozen `request_sha256`), `result_ingestor` (same-`AnalysisRun`, write-once, exact-ZIP identity), and `RemoteGpuExecutorProvider` (`runtime_ref = remote:<profile>:<required_remote_runtime_commit>`). Plan C consumes these unchanged. The only genuinely missing capability is the **`remote_gpu` runtime-qualification type / install-eligibility** that A3 deliberately deferred to Plan C (and the associated remote runtime-identity binding), plus acceptance/operator tooling and evidence. Where a production-adjacent change is required it is confined to the qualification seam and must be explicitly reviewed.

**Tech Stack:** Python 3.12, FastAPI + Pydantic v2, SQLAlchemy 2 / SQLite, pytest (control plane is ML-free), OpenSSH `ssh`/`scp`, the AutoDL ML interpreter `/root/miniconda3/bin/python`, the Plan-B control-plane venv `/root/autodl-tmp/WISA-backend-v1-plan-b-local-gpu/.venv/bin/python`.

**Spec:** `docs/superpowers/specs/2026-09-14-backend-v1-final-qualification-design.md`

**Authoritative baseline:** integration branch `integration/v1-candidate` @ `6716eaef217de97c5746d0468d62abede9b1c63c` (parents: frontend freeze `2a76219388bf76b791c91260d4af5a57f8677c02`, backend Plan-B freeze `291861d36e2045396996e5d2b7beb5d0f2ac09e1`; merge base `d4b22ee4f01914974aaf47c1a88e4f8afa461913`). Backend regression on that exact SHA: focused 188 passed; full 2138 passed / 32 skipped / 0 failed / 0 errors; control plane `torch: None`, `ultralytics: None`. Plan B is `COMPLETE WITH OPERATOR-APPROVED DEVIATIONS` and its evidence is frozen (final actual 152).

**Plan-C source branch:** `feature/backend-v1-plan-c-remote-gpu`, created from the exact integration SHA `6716eae…`. Do not modify `integration/v1-candidate`, `feature/frontend-v1`, `feature/backend-v1-plan-b-local-gpu`, `main`, `feature/v1-core`.

## Global Constraints

1. **No production-behavior change without explicit justification.** The remote-execution runtime, transport, coordinator, request/result identity, and Auto policy are consumed unchanged. The one expected change is confined to `backend/app/runtime_qualification/qualification.py` (add a `remote_gpu` qualification type + probe/runner + install eligibility, parity with the Plan-B `local_gpu_cuda_v1` addition). Any other production change must be separately justified and reviewed; if a remote-execution production defect is found, STOP and report it rather than widening scope.
2. **No Plan-B reuse.** Plan C uses fresh isolated roots. Never read, write, or reuse `/root/autodl-tmp/plan_b_qual/`, Plan-B DBs, Plan-B certificates, or Plan-B evidence. Plan-B final actual is 152 and is never restated or mutated.
3. **Per-installation exact certificates only.** The certificate key is the exact 7-field tuple `(plugin_id, plugin_version, model_release_id, executor, device_type, precision, runtime_ref)`. No generic “any GPU” certificate; no reuse of a `local_gpu` certificate; no weakened runtime identity.
4. **Fixed-argv, host-key-verified transport only.** `shell=False`; `BatchMode=yes`; `StrictHostKeyChecking=yes` with an explicit known-hosts file. `StrictHostKeyChecking=no` is forbidden. No arbitrary remote command, no user-supplied remote absolute path, no secret or private key committed.
5. **One authoritative run.** A remote result is ingested into the SAME local `AnalysisRun` created on Host A. No imported substitute run; no second authoritative experiment DB on Host B.
6. **No auto-retry after ambiguity.** Any ambiguous post-submit state (transport uncertainty, unknown remote state, unexpected extra run/attempt) STOPs for operator review; it never triggers a second real inference.
7. **Bounded real-inference budget.** Plan C’s frozen ceiling is 6 real `remote_gpu` model executions (C1 = 1, C2 = 1, C3 = 4). Any execution above the ceiling STOPs and requires a new operator ruling. All remote real runs are `zoomspec_yolo26n_aug_combined_frn_v3 / 1.0.0 / golden`; CPN `remote_gpu` has no certificate and must fail closed (`EXECUTION_NOT_CERTIFIED`).
8. **GPU stays ON.** Plan C ends with the GPU on, all Plan-C local/remote processes terminal, zero qualification-owned remote worker PIDs, and evidence ready for independent review. H5.5-S is a separate later phase.
9. **Acceptance-only tooling.** Server-side resource/foreign-process/agent tooling lives in `scripts/`; production `app/` never depends on `nvidia-smi`, `/proc`, or cgroup telemetry for `remote_gpu`.
10. **No secrets in evidence.** Record only references/identifiers/fingerprints, never key contents, passphrases, or tokens.

---

## Authoritative Baseline

```text
integration/v1-candidate            6716eaef217de97c5746d0468d62abede9b1c63c
  parent 1 (frontend freeze)        2a76219388bf76b791c91260d4af5a57f8677c02
  parent 2 (backend Plan-B freeze)  291861d36e2045396996e5d2b7beb5d0f2ac09e1
  merge base of frozen branches     d4b22ee4f01914974aaf47c1a88e4f8afa461913

Plan B final state                 COMPLETE WITH OPERATOR-APPROVED DEVIATIONS
Plan B final actual executions     152
control plane                      torch=None ultralytics=None
```

---

## Current-Source Reconciliation (at `6716eae`)

The following modules exist and are load-bearing. Verify again at plan-execution time; do not trust stale line numbers.

| Area | Current paths | What it provides |
|---|---|---|
| Remote config | `backend/app/remote_execution/profile.py` | `RemoteProfile.from_env`; validated `WSP_REMOTE_*`; `is_safe_remote_posix_path_text`, `is_safe_remote_asset_name`; `runtime_descriptor()` → `RuntimeDescriptor(remote_gpu, cuda, device_index, precision, environment_ref=name)` |
| Transport | `backend/app/remote_execution/transport.py` | `SshRunner` fixed-argv `ssh`/`scp`, `StrictHostKeyChecking=yes`, `BatchMode=yes`, `UserKnownHostsFile`; `run_runner("probe"/"submit"/"status"/"work", args)`; env bridge `_runner_env_prefix` |
| Probe | `backend/app/remote_execution/probe.py` | `run_probe`: runtime-commit check, asset hash verify, dataset root, label spaces, CUDA device (lazy torch) |
| Control-plane probe adapter | `backend/app/remote_execution/executor.py` | `SshRemoteExecutorProbe.availability` → `ExecutorAvailabilityRead`; maps runner error codes |
| Provider | `backend/app/remote_execution/runtime.py` | `RemoteGpuExecutorProvider` with `runtime_ref = f"remote:{profile.name}:{required_runtime_commit}"`; `ExecutorRegistry` certificate gating; `validate_frozen_execution_authority` |
| Job manager | `backend/app/remote_execution/job_manager.py` | `submit`/`status`/`download`; frozen-runtime guard before SCP/SSH |
| Remote runner | `backend/app/remote_execution/runner.py` | `create_or_attach`, `submit_job`, `reconcile_status`, `run_work`, `_verify_terminal_result` (write-once), CLI subcommands `probe/submit/status/work` |
| Request freeze | `backend/app/remote_execution/request_builder.py`, `canonical.py` | `freeze_request_provenance`, `build_batch`, `compute_request_sha256` |
| Identity | `backend/app/remote_execution/identity.py` | `resolve_remote_recording_identity` (fingerprint + `source_data_sha256`), `resolve_local_orchestrator_commit`, `resolve_asset_manifest_sha256` |
| Assets | `backend/app/remote_execution/assets.py` | `verify_remote_runtime_commit`, `verify_assets`, `load_pipeline_asset_manifest` |
| Worker context | `backend/app/remote_execution/worker_context.py` | `RemoteWorkerContext.from_env` (server-side), namespaced `WSP_REMOTE_ASSET_PATHS_JSON`, `runtime_descriptor()`, `resolve_assets` |
| Coordinator | `backend/app/remote_execution/coordinator.py`, `coordinator_job_manager.py` | `Coordinator.run`, `make_production_writer_factory`, fencing, terminalize-on-failure |
| Result ingest | `backend/app/remote_execution/result_ingestor.py`, `result_publisher.py`, `validation.py` | strict envelope parse, identity checks, exact-ZIP `payload_sha256`, `AnalysisResultWriter` into the SAME run, write-once/conflict |
| Recovery | `backend/app/remote_execution/recovery.py` | remote pending/running never locally interrupted; re-coordinated under rotated token when config valid |
| Dataset experiment | `backend/app/dataset_experiments/service.py`, `recovery.py`, `worker.py` | remote item launch (`launch_item_attempt`), `_validate_frozen_execution_authority`, coordinator |
| Auto policy | `backend/app/execution_selection/{policy,resolver,router}.py` | deterministic ranking (includes `remote_gpu`), `resolve_auto_execution`, `GET /api/executor-selection` |
| Qualification seam | `backend/app/runtime_qualification/{identity,doctor,qualification,evidence,install}.py` | A3 doctor/qualify/install; **`INSTALL_ELIGIBLE_TYPES["remote_gpu"] == ()`** (deferred to Plan C) |
| Certificates | `backend/app/pipelines/execution_certificates.json` | 6 repo-default certificates; the only `remote_gpu` one is ZoomSpec golden @ `remote:autodl_primary:5bb5be4b04d04a071bc9d8f4f61172595ecee037`, `evidence_ref=m9.1-live-gate` |

Remote tests already present (selection): `test_remote_bootstrap`, `test_remote_generic_bootstrap`, `test_remote_transport`, `test_remote_probe`, `test_remote_probe_error_channel`, `test_remote_executor_availability`, `test_remote_executor_negative`, `test_remote_runner`, `test_remote_runner_cutover`, `test_remote_runner_work_wiring`, `test_remote_job_manager_submit`, `test_remote_package_publisher`, `test_remote_result_publisher`, `test_remote_result_ingestor`, `test_remote_create_run`, `test_remote_request_freeze`, `test_remote_source_hash`, `test_remote_worker_context`, `test_remote_runtime_commit_config`, `test_remote_startup_recovery`, `test_remote_coordinator*`, `test_remote_live_loop`, `test_remote_live_negative`, `test_dataset_experiment_launch_remote`, `test_dataset_experiment_attempt_remote`, `test_analysis_prepare_remote`, `test_zoomspec_generic_remote_execution`, `test_execution_selection_*`, `test_executor_selection_api`.

---

## Existing Capability Matrix

Classification: **A** implemented + unit/integration tested; **B** implemented but not true-two-host qualified; **C** qualification tooling missing; **D** production behavior genuinely missing; **E** documentation/operator configuration missing.

| Requirement | Class | Evidence |
|---|---|---|
| `RemoteProfile` validation (host/port/user/paths/commit/device/precision) | A | `test_remote_bootstrap`, `test_remote_generic_bootstrap`, `test_remote_runtime_commit_config` |
| Fixed-argv SSH with strict host-key + known-hosts + `shell=False` | A | `test_remote_transport` |
| Deterministic remote probe (runtime commit, asset hash, dataset root, label space, CUDA) | A | `test_remote_probe`, `test_remote_probe_error_channel` |
| `remote_gpu` provider `runtime_ref = remote:<profile>:<commit>` | A | `test_remote_runtime_commit_config`, `test_remote_executor_availability` |
| Certificate-gated `remote_gpu` availability + fail-closed negatives | A | `test_remote_executor_negative`, `test_execution_certificate`, `test_executor_registry` |
| Frozen request provenance + `request_sha256` | A | `test_remote_request_freeze`, `test_remote_execution_canonical` |
| Remote runner `probe/submit/status/work`, write-once terminal result | A | `test_remote_runner`, `test_remote_runner_work_wiring` |
| Same-`AnalysisRun` result ingestion + conflict detection | A | `test_remote_result_ingestor`, `test_remote_live_loop` |
| Coordinator lifecycle + fencing + completion failure | A | `test_remote_coordinator*` |
| Remote startup recovery (never blind-interrupt; re-coordinate) | A | `test_remote_startup_recovery` |
| DatasetExperiment remote item launch | A | `test_dataset_experiment_launch_remote`, `test_dataset_experiment_attempt_remote` |
| Auto deterministic ranking includes `remote_gpu` | A | `test_execution_selection_policy`, `_resolver`, `_failclosed_matrix`, `test_executor_selection_api` |
| **Real remote_gpu run cross-host, same AnalysisRun** | **B** | never executed cross-host |
| **Small real remote ZoomSpec DatasetExperiment + evaluation** | **B** | never executed |
| **`remote_gpu` runtime qualification type / install eligibility** | **D (deferred, Plan C)** | `INSTALL_ELIGIBLE_TYPES["remote_gpu"] == ()` |
| **Remote runtime identity binding for a new remote commit** | **D (deferred, Plan C)** | no scheme for `remote:*`; existing cert binds only `5bb5be4` |
| **Remote profile/known-hosts/asset/dataset bootstrap for the candidate SHA** | **E** | operator config only; partially documented M9.1 |
| **Server-side foreign-GPU admission gate (acceptance tooling)** | **C** | not present; observed D-FINE contamination risk |
| **Plan-C acceptance artifact + per-host evidence** | **C/E** | not present |

---

## Remaining Plan-C Gaps

1. **Gap C-1 (D, deferred, justified): `remote_gpu` qualification type.** `INSTALL_ELIGIBLE_TYPES["remote_gpu"]` is `()`; A3 explicitly deferred real remote types to Plan C. A new remote commit produces a new `runtime_ref` (`remote:<profile>:<commit>`) and therefore needs a new exact certificate provisioned through the platform-owned CLI. **Justified change:** add a `remote_gpu` qualification type + `RemoteGpuTargetProbe` + `RemoteGpuQualificationRunner` (parity with `local_gpu_cuda_v1`), reusing the existing probe and commit/asset verification. This is a qualification-seam change, not a change to remote execution behavior.
2. **Gap C-2 (D/E): remote runtime commit pinning.** The existing `remote_gpu` certificate binds `5bb5be4…`, which predates the generic-asset/model-release wire changes (`schema.py`, `worker_context.py`, `runner.py`, `probe.py` all changed since; `5bb5be4` is an ancestor of `6716eae`). The remote repo must be pinned to a commit whose runner/worker schema matches the control plane. **Recommended decision:** pin the remote runtime to the Plan-C candidate SHA (same reviewed source both hosts), which forces a new exact remote certificate (Gap C-1). **Alternative (fallback, requires operator approval):** pin remote to a commit already certified via a reviewed repo-default certificate commit. Record the ruling.
3. **Gap C-3 (C): server-side foreign-GPU admission gate.** Acceptance-only tooling must refuse to start a live run when an unknown compute application occupies the GPU, without adding a production `nvidia-smi` dependency.
4. **Gap C-4 (E): operator bootstrap.** Remote profile env, known-hosts + host-key fingerprint, remote repo checkout at the pinned commit, remote asset map, remote dataset root, and the local Host-A control-plane/DB/work-root setup are operator-supplied. Documented, never committed with secrets.
5. **Gap C-5 (C/E): Plan-C evidence + ledger.** No Plan-C acceptance artifact exists; the per-host evidence template and execution ledger must be defined.

---

## Host-A / Host-B Responsibility Matrix

```text
HOST A — LOCAL WINDOWS MACHINE (owner: local OpenCode + operator)
  WISA control plane (ML-free)         SQLite/domain state authority
  Recording / DatasetExperiment authority
  AnalysisRun authority
  frontend
  local_cpu provider
  remote_gpu provider (SshRemoteExecutorProbe + RemoteGpuExecutorProvider)
  remote profile config (WSP_REMOTE_*), SSH key + known_hosts presence
  local Plan-C qualification DB/work root
  dataset membership freeze; AnalysisRun/DatasetExperiment creation
  result ingestion into the SAME AnalysisRun; evaluation; provenance capture
                       │
                       │  SSH (fixed argv, StrictHostKeyChecking=yes) / SCP
                       ▼
HOST B — AUTODL GPU SERVER (owner: server OpenCode + operator)
  remote execution runtime @ pinned commit (app.remote_execution.runner)
  ZoomSpec golden assets
  CUDA / PyTorch ML interpreter (/root/miniconda3/bin/python)
  remote worker (work subcommand), detached per batch
  result artifact production (envelope.json + analysis_result.zip)
  remote job root / work roots under /root/autodl-tmp/plan_c_qual/
  NO authoritative platform DB; NO local AnalysisRun duplication
```

Invariant: **Host B never creates or owns the authoritative `AnalysisRun`.** The remote worker returns an analysis package + envelope; Host A ingests it into the same run.

---

## Security / SSH Model

- `SshRunner` argv is fixed: `ssh -p <port> -i <key> -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=<known_hosts> <user>@<host> env <validated assignments> <remote_python> -m app.remote_execution.runner <subcommand> <args>`. `shell=False`; only platform-owned tokens/validated identifiers/trusted absolute POSIX paths may appear.
- `scp` mirrors the same options for `upload_file`/`download_file`; remote paths derive from the trusted configured job root plus validated identifiers.
- `StrictHostKeyChecking=no` is forbidden anywhere. The known-hosts file is deployment config; its path is configured, its contents are never committed.
- Remote asset mapping is the generic namespaced `WSP_REMOTE_ASSET_PATHS_JSON` only; the retired flat legacy shape is rejected.
- Fail-closed negatives (prefer deterministic/no-inference): host unreachable → `REMOTE_TRANSPORT_UNAVAILABLE`; host-key mismatch → SSH handshake failure (runner never reached); bad profile → `REMOTE_EXECUTOR_UNAVAILABLE`; wrong remote runtime commit → `REMOTE_IMPLEMENTATION_MISMATCH`; missing remote asset → `PIPELINE_ASSET_MISMATCH`; asset hash mismatch → `PIPELINE_ASSET_MISMATCH`; missing certificate → `EXECUTION_NOT_CERTIFIED`; remote probe failure → `REMOTE_PROBE_UNAVAILABLE`.

---

## Runtime / Certificate Identity Model

Keep these distinct (never conflate):

```text
orchestrator_commit              Host A WISA repo git HEAD (resolve_local_orchestrator_commit)
required_remote_runtime_commit   Host B WISA repo git HEAD (WSP_REMOTE_REQUIRED_RUNTIME_COMMIT)
remote runtime_ref               remote:<profile_name>:<required_remote_runtime_commit>
execution certificate key        (plugin_id, plugin_version, model_release_id,
                                  executor=remote_gpu, device_type=cuda,
                                  precision=float16, runtime_ref)
pipeline asset identity          <plugin_id>/<plugin_version>/<asset_manifest_sha256>
```

Pinning rule: both hosts are pinned to a frozen commit (recommended: the Plan-C candidate SHA) and the remote HEAD must exactly equal `required_remote_runtime_commit` **before any inference** (`verify_remote_runtime_commit`). The remote runtime never runs `git pull`/`checkout`/`reset` as part of execution; repository preparation is an operator action.

Certificate rule: `remote_gpu` qualification must establish, for the current installation, that (a) Host B is reachable over the strict transport, (b) Host B HEAD equals the required commit, (c) the golden asset bytes match the manifest, (d) CUDA device 0 is usable, and (e) the resulting `runtime_ref` is exactly `remote:<profile>:<commit>`. Only then may the operator install the exact 7-field `remote_gpu` certificate. `local_gpu` certificates are never reused for `remote_gpu`.

---

## Dataset / Recording Identity

- Recording identity is the double identity already implemented: `recording_fingerprint` (semantic, from Recording metadata + GroundTruth via `build_recording_fingerprint`) and `source_data_sha256` (exact raw-IQ bytes via `resolve_source_data_sha256`).
- Host B must hold the identical IQ bytes; the envelope returns `source_data_sha256` and Host A rejects any mismatch (`REMOTE_RESULT_INVALID` / `_verify_envelope_identity`).
- Dataset membership for the C3 experiment is frozen before execution (see Execution Budget).

---

## Execution Budget (frozen before live run)

| Gate | Real remote model executions | Executor | Model | Purpose | Maximum authorized |
|---|---:|---|---|---|---:|
| C0 Static preflight | 0 | — | — | source/identity/assets/CUDA/connectivity/foreign gate | 0 |
| C1 Loopback mechanics | 1 | `remote_gpu` | ZoomSpec golden | transport + same-run ingestion on one host | 1 |
| C2 True two-host single run | 1 | `remote_gpu` | ZoomSpec golden | one cross-host `AnalysisRun` → SAME run | 1 |
| C3 True two-host DatasetExperiment | 4 | `remote_gpu` | ZoomSpec golden | 4-item experiment + evaluation | 4 |
| C4 Recovery/reconciliation | 0 | — | — | deterministic/CPU + constrained reuse | 0 |
| C5 Final acceptance | 0 | — | — | evidence + regression | 0 |
| **TOTAL** | **6** | | | | **6** |

- C3 membership (frozen now, before any result): SpaceNet `test` stems **`0, 1, 2, 3`** (the first four of the Plan-B verified `H2_STEMS`), dataset `SpaceNet / test / spacenet_14`, plugin `zoomspec_yolo26n_aug_combined_frn_v3 / 1.0.0 / golden`, executor `remote_gpu`, `max_concurrency=1` (SQLite single-writer; the smallest useful batch).
- Exactly **one** C3 `DatasetExperiment`; exactly 4 `AnalysisRun`s; no retry experiment.
- Any execution above 6 STOPs and requires a new operator ruling. No silent retry.

---

## C0 — Static / No-Inference Preflight

**No model inference. Runs on Host B (server OpenCode) and, where noted, Host A (local).**

Prove, fail-closed:

```text
source identity
  Host A orchestrator_commit == the frozen Plan-C candidate SHA
  Host B remote repo HEAD     == required_remote_runtime_commit (verify_remote_runtime_commit)
  integration clean; branch = feature/backend-v1-plan-c-remote-gpu

control plane ML-free
  torch = None; ultralytics = None (control-plane interpreter)

remote profile load
  RemoteProfile.from_env builds from WSP_REMOTE_* (no secrets printed)

security
  known_hosts present; host-key fingerprint recorded (fingerprint only)
  StrictHostKeyChecking=yes confirmed in the transport argv

transport connectivity
  runner "probe" reachable; a non-live connectivity check only

runtime identity
  Host B HEAD == required commit; probe returns remote_runtime_commit match

CUDA health / probe
  probe run_probe: runtime commit + asset hashes + dataset root + label spaces + CUDA device 0

asset hashes
  ZoomSpec golden manifest 16cc0534…; detector eba4fa4b…; frn da6087da…;
  frozen_config 030dbfa7…; normalization 9b994655… (exact bytes)

certificate state
  the exact remote_gpu certificate for (ZoomSpec golden, cuda, float16,
  remote:<profile>:<commit>) exists (after the C-pre qualification task)

dataset resolver compatibility
  SpaceNet test stems 0,1,2,3 resolvable; spacenet_14 label space loadable

recording fingerprint / source_data_sha256 contract
  resolve_remote_recording_identity returns stable fingerprint + source hash for each stem

foreign-GPU admission gate (acceptance tooling)
  no unknown NVIDIA compute application
```

**Gate:** C0 must be fully green before C1. Any failure STOPs (no inference consumed).

---

## C1 — Loopback Mechanics

**Consumes at most 1 real remote model execution (plus a 0-inference probe). Server-side automatable.**

Definition (current implementation): Host A and Host B are the **same AutoDL host**; the control plane runs in the ML-free control-plane venv, and the remote target is the same host addressed over SSH (`127.0.0.1` or the host’s own address) with its own known-hosts entry and key. The remote worker executes in the ML interpreter on the same host.

What it proves: `RemoteProfile` load, strict host-key mechanics, fixed-argv/env bridge, remote runtime-commit verification, asset-hash verification, CUDA availability, `submit`/`status`/`work`, result download, and same-`AnalysisRun` ingestion with write-once semantics.

What it does **not** prove: physical portability across independent machines, independent host keys, cross-machine filesystem/dataset equivalence, or real network latency/failure behavior. Therefore it is **not** a substitute for true two-host qualification; it is a bounded mechanics gate.

Workload: exactly one ZoomSpec golden `remote_gpu` `AnalysisRun` (probe is inference-free). Acceptance: run reaches `completed`; `executor=remote_gpu`; envelope/runtime/asset identity verified; detections persisted into the SAME run; a second identical ingest is idempotent; a conflicting payload fails closed (`REMOTE_RESULT_CONFLICT`).

Hosts: control plane + target = Host B. Uses a **fresh Plan-C loopback DB/work root** (never Plan-B).

---

## C2 — True Two-Host Single Run

**Consumes exactly 1 real remote model execution. Operator/local-driven (Host A).**

Host A = the local Windows machine; Host B = AutoDL. Proves one genuine cross-host path:

```text
Host A: Recording registered (SpaceNet test stem, external-path semantics)
      → AnalysisRun created with executor=remote_gpu (manual), model_release=golden
      → request frozen (request_sha256), submitted over SSH
Host B: runner work executes ZoomSpec golden on CUDA device 0
      → envelope.json + analysis_result.zip produced
Host A: result downloaded, envelope identity verified, package validated,
        ingested into the SAME AnalysisRun
```

Acceptance: `status=completed`; `executor=remote_gpu`; `runtime_descriptor` == provider descriptor; `model_release_id=golden`; asset manifest `16cc0534…`; envelope `source_data_sha256` == Recording; detections persisted; `GET /api/analysis-runs/{id}` and detections readback correct; write-once/conflict negatives hold.

Auto (bounded, zero-inference): `GET /api/executor-selection` with `remote_gpu` certified+available and `local_gpu` absent must resolve `resolved_executor=remote_gpu` (or, if the workload is `SMALL`, report the deterministic ranking outcome). Full Auto acceptance remains Plan D; the endpoint observation is evidence only.

---

## C3 — True Two-Host Small DatasetExperiment

**Consumes exactly 4 real remote model executions. Operator/local-driven (Host A).**

Frozen membership (before results): SpaceNet `test` stems **`0,1,2,3`**; `spacenet_14`; ZoomSpec golden; `remote_gpu`; `max_concurrency=1`; exactly one `DatasetExperiment`.

Acceptance:

```text
DatasetExperiment lives on Host A (authoritative)
executor frozen to remote_gpu; runtime descriptor matches provider
exactly 4 items; 4 attempts; 4 unique AnalysisRuns (one per item)
remote workers execute on Host B; results return to Host A
no imported substitute run; no hidden local_cpu/local_gpu execution
linked DatasetEvaluation: status=completed; evaluated=4; missing=0; coverage=1.0
provenance: exact plugin/release/runtime_ref/asset manifest per run
no orphan remote workers after terminal
```

---

## C4 — Recovery / Reconciliation Evidence

**Consumes 0 real model executions (deterministic/CPU; reuse where possible).**

Reuse existing behavior/tests; add only what is missing for the remote path:

```text
control-plane restart: remote pending/running never blindly interrupted
  (mark_stale_local_runs_interrupted excludes remote; treat as CPU test)
remote status reconciliation: reconcile_status idempotency (run twice)
idempotent submission: create_or_attach returns "attached" for identical request_sha256;
  conflicting semantic request -> REMOTE_REQUEST_CONFLICT
terminal result immutability: completed item verified write-once; no re-execution
orphan safety: coordinator fencing + terminalize-on-completion-failure
corrupt/missing terminal result: _verify_terminal_result -> REMOTE_RESULT_CORRUPTED (fail closed)
provider disappearance: remote config absent -> runs left untouched, no launch
```

Ambiguous post-submit state (transport timeout after a possible submit, unknown remote status) STOPs for operator review; it never triggers a second inference. Where a real remote re-drive is unavoidable it must reuse the SAME batch/`request_sha256` and consume no new model execution beyond the C2/C3 budget.

---

## C5 — Final Acceptance

**Consumes 0 real model executions.**

```text
Plan-C acceptance artifact written (see Evidence Artifacts)
runtime/provenance record per gate
execution ledger (C1..C3) == frozen budget (1 + 1 + 4 = 6)
full backend regression on the Plan-C candidate SHA: 0 failed / 0 errors
frontend/API contract preserved (no frontend change; API reads unchanged)
control plane ML-free
remote workers drained (zero Plan-C remote worker PIDs)
local workers/coordinators drained
GPU quiescent (no Plan-C compute process)
Plan-B evidence + DBs unchanged (hashes)
Plan-C roots under a fresh isolated root
GPU remains ON
```

---

## Evidence Artifacts

Proposed acceptance document (created only at C5, never in this planning task):

```text
docs/superpowers/acceptance/2026-09-16-backend-v1-plan-c-remote-gpu-qualification.md
```

Must record (never secrets):

```text
source SHAs (integration baseline, Plan-C candidate, orchestrator_commit, required_remote_runtime_commit)
host roles (Host A / Host B)
remote profile identity (name + host key fingerprint reference; never key material)
runtime_ref
certificate 7-field key
remote runtime commit
asset logical names + SHA256
dataset membership (0,1,2,3)
AnalysisRun IDs; DatasetExperiment ID; DatasetEvaluation ID
execution ledger (C1=1, C2=1, C3=4, total=6)
status chronology
result artifact hashes (envelope.json, analysis_result.zip, payload_sha256)
provenance projections
remote/local DB state (counts only)
worker/process drain
GPU state
full regression counts
```

Server-side Plan-C roots (fresh; never Plan-B):

```text
/root/autodl-tmp/plan_c_qual/            (server acceptance artifacts, job root, evidence)
/root/autodl-tmp/plan_c_remote_runtime/  (Host B checkout at required_remote_runtime_commit)
```

Host A local: a dedicated Plan-C qualification DB/work root (operator-supplied).

---

## Failure / No-Retry Rules

Fail closed before live execution; never auto-retry a real inference:

```text
SSH uncertainty / host-key mismatch / unreachable host        -> STOP (no inference)
launch ambiguity or unknown remote state                       -> STOP for operator review
transport timeout after a possible submit                      -> treat as ambiguous; STOP; reconcile manually
foreign/unknown GPU compute process                            -> BLOCKED; never kill it
wrong remote runtime commit                                    -> STOP
asset mismatch (missing/hash)                                  -> STOP
certificate mismatch / missing                                 -> STOP
result corruption (envelope/zip/payload hash)                  -> STOP
unexpected extra AnalysisRun / attempt / second experiment     -> STOP
budget would exceed 6                                          -> STOP; require new operator ruling
```

Distinguish for every live gate:

```text
safe pre-launch retry          (no request submitted; no state changed)  -> allowed
ambiguous post-submit state    (submit may/may not have happened)         -> STOP; no auto second submit
```

---

## Regression Matrix

| Check | Command / scope (reference) | Gate |
|---|---|---|
| Remote unit/integration | `pytest backend/tests -k "remote" -q` | 0 failed / 0 errors |
| Dataset experiment remote | `test_dataset_experiment_launch_remote.py`, `test_dataset_experiment_attempt_remote.py` | green |
| Execution selection / Auto | `test_execution_selection_*.py`, `test_executor_selection_api.py` | green |
| Runtime qualification (after C-pre) | `test_runtime_qualification*.py`, `test_runtime_identity.py`, `test_runtime_doctor.py`, `test_certificate_install.py` | green |
| Full backend regression | `PYTHONPATH="$PWD/backend:$PWD/scripts" <control-plane venv> -m pytest backend/tests -q` | 0 failed / 0 errors |
| Control-plane ML-free | `find_spec("torch")`/`find_spec("ultralytics")` | both None |
| Plan-B evidence immutability | SHA256 of Plan-B NC-02 artifacts + `qual.db` | unchanged |
| Frontend | not re-run on server (already accepted) | no change |

---

## H5.5-S Handoff Boundary

Plan C ends with the GPU still ON. It does **not** execute H5.5-S, the operator cold switch, H5.5-C, or Plan D, and does not create `BACKEND_V1_SEAL_SHA`. H5.5-S is the next phase after independent Plan-C review and requires all Plan-C local/remote processes terminal and zero qualification-owned remote worker PIDs.

---

## Exact Task Ownership

**Server OpenCode (Host B):**
- Pin `plan_c_remote_runtime` to the required commit; verify HEAD.
- C0 remote-side checks: runtime commit, CUDA health, ML interpreter, asset identity, dataset root, remote runtime identity, runner state, remote work roots, GPU quiescence, foreign-GPU admission gate.
- C1 loopback mechanics (control plane + target on the same host).
- Remote-side observation during C2/C3 (worker drain, GPU quiescence, no orphan remote workers, remote artifact integrity).
- Never create the authoritative local `AnalysisRun`/`DatasetExperiment` for the true two-host gate.

**Local OpenCode (Host A, Windows):**
- Checkout the exact Plan-C candidate SHA; configure the CPU-only control plane.
- Configure the remote profile (`WSP_REMOTE_*`), verify known_hosts/key presence (no secret content).
- Create the dedicated Plan-C qualification DB/work root.
- Register/freeze the dataset membership (`0,1,2,3`).
- Create the C2 `AnalysisRun` and C3 `DatasetExperiment`; manual `remote_gpu` (and the bounded Auto endpoint observation).
- Poll/read results; verify local DB authority; verify evaluation; capture local provenance/evidence.

**Operator-only:**
- Provide SSH key + known_hosts; record host-key fingerprint.
- Approve the remote runtime commit pin (Gap C-2 ruling) and the Gap C-1 qualification-seam change.
- Run the true two-host session; report evidence.

---

## Stop Conditions

```text
source/branch not at the exact approved SHA
control plane not ML-free
remote HEAD != required_remote_runtime_commit
host-key mismatch or `StrictHostKeyChecking=no` anywhere
foreign GPU compute process present before a live gate
asset/certificate mismatch
any ambiguous post-submit state
monitor/worker/GPU quiescence failure
any execution that would push Plan-C above 6, or any second experiment/retry
Plan-B evidence mutation
GPU powered off
```

---

## Plan-C Prerequisite Tasks (to be implemented after independent review of this plan)

Order matters; each is its own reviewed commit on `feature/backend-v1-plan-c-remote-gpu`.

```text
C-pre-1 (Gap C-1, qualification seam)
  Provide a bounded `remote_gpu` qualification type + probe/runner + install eligibility,
  parity with Plan-B `local_gpu_cuda_v1`, reusing SshRemoteExecutorProbe/run_probe +
  commit/asset verification. TDD; no change to remote execution behavior.
  Commit: feat: add remote_gpu runtime qualification

C-pre-2 (Gap C-2, operator decision)
  Record the remote runtime commit pin ruling (recommended: Plan-C candidate SHA) and
  the resulting certificate provisioning path (C-pre-1 install, or reviewed repo-default
  certificate commit). Operator-approved.

C-pre-3 (Gap C-3, acceptance tooling)
  scripts/plan_c_remote_gpu_gate.py — server-side foreign-GPU admission + quiescence
  observation (nvidia-smi; acceptance-only). No production dependency.
  Commit: test: add plan c remote gpu admission gate

C0..C5 then execute per this plan; C5 writes the acceptance artifact.
```

---

## Plan Self-Review

1. **No Plan-B reuse.** All Plan-C roots are fresh; Plan-B final actual 152 and its evidence are never restated or mutated. ✅
2. **Exact certificate model.** 7-field key; `remote_gpu` only; no generic/local reuse. ✅
3. **Runtime identity separated.** `orchestrator_commit` vs `required_remote_runtime_commit` vs `runtime_ref` vs asset manifest, explicitly distinct. ✅
4. **Secret safety.** Only references/fingerprints recorded; keys/known-hosts supplied externally and never committed. ✅
5. **Fixed-argv SSH.** `shell=False`, strict host-key, no arbitrary command/path; `StrictHostKeyChecking=no` forbidden. ✅
6. **Bounded budget.** Frozen at 6 (1+1+4), fixed membership `0,1,2,3`; above ceiling requires a new ruling. ✅
7. **No silent retry.** Ambiguous post-submit STOPs for operator review. ✅
8. **Same-run authority.** No imported substitute; Host B has no authoritative platform DB. ✅
9. **Deterministic negatives preferred.** C0/C4 no-inference; recovery via existing tests. ✅
10. **GPU stays ON; H5.5-S outside Plan C; no seal.** ✅
11. **Production change justified and minimal.** Only the `remote_gpu` qualification seam (Gap C-1); everything else is config/tooling/evidence. ✅
12. **Plan-B/`main`/`integration` refs untouched.** Planning branch is isolated. ✅
13. **Auto scope resolved.** Manual `remote_gpu` is qualified in Plan C; a zero-inference `GET /api/executor-selection` observation is included; full Auto acceptance is Plan D. ✅
