# Backend V1 Plan C — Remote-GPU / True Two-Host Qualification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Qualify the accepted Backend V1 `remote_gpu` execution path end-to-end on the real AutoDL GPU target while it is still rented — profile configuration, loopback transport mechanics, a bounded true two-host single `AnalysisRun`, a small true two-host ZoomSpec `DatasetExperiment`, deterministic recovery/reconciliation evidence, and a frozen Plan-C acceptance record — without changing remote-execution behavior, without weakening the exact per-installation certificate model, and without starting H5.5-S or turning the GPU off.

**Architecture:** Plan C is primarily a **qualification/campaign** plan. The M9.1 remote-execution infrastructure is already implemented and unit/integration tested at the integration baseline. Plan C consumes it unchanged. The only possible production-seam change is a `remote_gpu` runtime-qualification type + install-eligibility (deferred by A3 to Plan C) — and whether that change is needed at all is decided by a new **C-pre-0** no-inference compatibility audit, not assumed. Two distinct control-plane installations (loopback and true two-host) each qualify/certify separately.

**Tech Stack:** Python 3.12, FastAPI + Pydantic v2, SQLAlchemy 2 / SQLite, pytest (control plane ML-free), OpenSSH `ssh`/`scp`, AutoDL ML interpreter `/root/miniconda3/bin/python`, Plan-B control-plane venv `/root/autodl-tmp/WISA-backend-v1-plan-b-local-gpu/.venv/bin/python`.

**Spec:** `docs/superpowers/specs/2026-09-14-backend-v1-final-qualification-design.md`

**Authoritative baseline:** integration branch `integration/v1-candidate` @ `6716eaef217de97c5746d0468d62abede9b1c63c` (parents `2a76219388bf76b791c91260d4af5a57f8677c02`, `291861d36e2045396996e5d2b7beb5d0f2ac09e1`; merge base `d4b22ee4f01914974aaf47c1a88e4f8afa461913`). Backend regression on that SHA: focused 188 passed; full 2138 passed / 32 skipped / 0 failed / 0 errors; control plane `torch: None`, `ultralytics: None`. Plan B is `COMPLETE WITH OPERATOR-APPROVED DEVIATIONS` (final actual 152).

**This document revision:** supersedes the first Plan-C draft (`b4fcb533…`) per independent review. Deltas: I-1 implementation surface; new C-pre-0 compatibility ruling; I-3 per-installation certificates; I-4 strict zero-inference C4; I-5 Plan-B read-only semantics; remote identity-scheme decision; revised task order. The Plan-C source branch is `feature/backend-v1-plan-c-remote-gpu`, created from the exact integration SHA.

## Global Constraints

1. **C-pre-0 precedes any production change.** No `remote_gpu` qualification code is written until the certified-remote-runtime compatibility audit produces an operator-approved ruling. Do not pre-decide the ruling in this document.
2. **No production-behavior change without explicit justification.** The remote-execution runtime, transport, coordinator, request/result identity, and Auto policy are consumed unchanged. Any change is confined to the qualification seam and named exactly in C-pre-1.
3. **Plan-B evidence is immutable (read-only).** Read-only `stat`/`SHA256`/comparison is allowed for immutability checks. Forbidden: production application lifecycle open, migration, recovery, mutation, artifact rewrite, copying/reusing Plan-B qualification state as Plan-C state, deletion/normalization.
4. **No Plan-C reuse of Plan-B state.** All Plan-C roots are fresh. Plan-B final actual 152 is never restated, reused, or mutated.
5. **Per-installation exact certificates only.** Certificate key is the exact 7-field tuple `(plugin_id, plugin_version, model_release_id, executor, device_type, precision, runtime_ref)`. No generic certificate; no `local_gpu` reuse; no reinterpretation of `remote:<profile>:<commit>`.
6. **Fixed-argv, host-key-verified transport only.** `shell=False`; `BatchMode=yes`; `StrictHostKeyChecking=yes` + explicit known-hosts. `StrictHostKeyChecking=no` forbidden. No arbitrary command/path; no secret/private key committed.
7. **One authoritative run.** A remote result is ingested into the SAME local `AnalysisRun` created on Host A. No imported substitute; no authoritative platform DB on Host B.
8. **No auto-retry; reconcile first.** Any ambiguous post-submit state STOPs after reconciliation only; never a second submit/inference.
9. **Bounded real-inference budget.** Frozen at 6 real `remote_gpu` ZoomSpec-golden model executions (C1=1, C2=1, C3=4). Above the ceiling STOPs and requires a new operator ruling. CPN+`remote_gpu` has no certificate → fail closed (`EXECUTION_NOT_CERTIFIED`).
10. **GPU stays ON.** Plan C ends with GPU on, all Plan-C processes terminal, zero Plan-C remote worker PIDs. H5.5-S is a separate later phase.
11. **Acceptance-only tooling.** Server resource/foreign-process tooling lives in `scripts/`; production `app/` never depends on `nvidia-smi`/`/proc`/cgroup for `remote_gpu`.
12. **No secrets in evidence.** References/identifiers/fingerprints only.

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
existing certified remote runtime  remote:autodl_primary:5bb5be4b04d04a071bc9d8f4f61172595ecee037
                                   (ZoomSpec golden, cuda, float16, evidence m9.1-live-gate)
```

---

## Current-Source Reconciliation (at `6716eae`)

| Area | Current paths | What it provides |
|---|---|---|
| Remote config | `backend/app/remote_execution/profile.py` | `RemoteProfile.from_env`; `WSP_REMOTE_*`; `is_safe_remote_posix_path_text`, `is_safe_remote_asset_name`; `runtime_descriptor()` |
| Transport | `backend/app/remote_execution/transport.py` | `SshRunner` fixed-argv `ssh`/`scp`, strict host-key/BatchMode/known-hosts; `run_runner("probe"/"submit"/"status"/"work", args)`; `_runner_env_prefix` |
| Probe | `backend/app/remote_execution/probe.py` | `run_probe`: runtime-commit, assets, dataset root, label spaces, CUDA |
| Control-plane probe adapter | `backend/app/remote_execution/executor.py` | `SshRemoteExecutorProbe.availability`; maps runner codes |
| Provider | `backend/app/remote_execution/runtime.py` | `RemoteGpuExecutorProvider.runtime_ref = remote:<profile>:<required_runtime_commit>`; registry certificate gating |
| Job manager | `backend/app/remote_execution/job_manager.py` | `submit`/`status`/`download`; frozen-runtime guard |
| Remote runner | `backend/app/remote_execution/runner.py` | `create_or_attach`, `submit_job`, `reconcile_status`, `run_work`, `_verify_terminal_result`; CLI `probe/submit/status/work` |
| Request freeze | `backend/app/remote_execution/{request_builder,canonical}.py` | frozen `request_sha256` |
| Identity | `backend/app/remote_execution/identity.py` | `resolve_remote_recording_identity`, `resolve_local_orchestrator_commit`, `resolve_asset_manifest_sha256` |
| Assets | `backend/app/remote_execution/assets.py` | `verify_remote_runtime_commit`, `verify_assets`, `load_pipeline_asset_manifest` |
| Worker context | `backend/app/remote_execution/worker_context.py` | `RemoteWorkerContext.from_env`; generic namespaced `WSP_REMOTE_ASSET_PATHS_JSON`; `runtime_descriptor()`; `resolve_assets` |
| Coordinator | `backend/app/remote_execution/{coordinator,coordinator_job_manager}.py` | `Coordinator.run`, writer factory, fencing |
| Result ingest | `backend/app/remote_execution/{result_ingestor,result_publisher,validation}.py` | strict envelope, identity checks, exact-ZIP `payload_sha256`, same-run `AnalysisResultWriter`, write-once/conflict |
| Recovery | `backend/app/remote_execution/recovery.py` | remote pending/running never locally interrupted; re-coordinate |
| Dataset experiment | `backend/app/dataset_experiments/{service,recovery,worker}.py` | remote item launch (`launch_item_attempt`), frozen-authority validation |
| Auto policy | `backend/app/execution_selection/{policy,resolver,router}.py` | deterministic ranking incl. `remote_gpu`; `GET /api/executor-selection` |
| Qualification seam | `backend/app/runtime_qualification/{identity,doctor,qualification,evidence,install}.py`; `backend/app/cli.py::_cmd_qualify` | A3 doctor/qualify/install; `INSTALL_ELIGIBLE_TYPES["remote_gpu"] == ()`; **`_cmd_qualify` builds a real probe only for `local_cpu`/`local_gpu`** and calls `select_runner(executor="remote_gpu", target_probe=None)` → `DeferredGpuQualificationRunner` |
| Certificates | `backend/app/pipelines/execution_certificates.json` | 6 repo-default; only `remote_gpu` = ZoomSpec golden @ `remote:autodl_primary:5bb5be4…`, `evidence_ref=m9.1-live-gate` |

Remote tests present (selection): `test_remote_bootstrap`, `test_remote_generic_bootstrap`, `test_remote_transport`, `test_remote_probe`, `test_remote_probe_error_channel`, `test_remote_executor_availability`, `test_remote_executor_negative`, `test_remote_runner`, `test_remote_runner_cutover`, `test_remote_runner_work_wiring`, `test_remote_job_manager_submit`, `test_remote_package_publisher`, `test_remote_result_publisher`, `test_remote_result_ingestor`, `test_remote_create_run`, `test_remote_request_freeze`, `test_remote_source_hash`, `test_remote_worker_context`, `test_remote_runtime_commit_config`, `test_remote_startup_recovery`, `test_remote_coordinator*`, `test_remote_live_loop`, `test_remote_live_negative`, `test_dataset_experiment_launch_remote`, `test_dataset_experiment_attempt_remote`, `test_analysis_prepare_remote`, `test_zoomspec_generic_remote_execution`, `test_execution_selection_*`, `test_executor_selection_api`. Qualification tests: `test_cli`, `test_runtime_qualification`, `test_runtime_qualification_integration`, `test_certificate_install`, `test_runtime_identity`, `test_runtime_doctor`.

---

## Existing Capability Matrix

Classification: **A** implemented + unit/integration tested; **B** implemented but not true-two-host qualified; **C** qualification tooling missing; **D** production behavior genuinely missing; **E** documentation/operator config missing.

| Requirement | Class | Evidence |
|---|---|---|
| `RemoteProfile` validation | A | `test_remote_bootstrap`, `test_remote_generic_bootstrap`, `test_remote_runtime_commit_config` |
| Fixed-argv strict SSH | A | `test_remote_transport` |
| Remote probe (commit/assets/dataset/label/CUDA) | A | `test_remote_probe`, `test_remote_probe_error_channel` |
| `remote_gpu` provider `runtime_ref = remote:<profile>:<commit>` | A | `test_remote_runtime_commit_config`, `test_remote_executor_availability` |
| Certificate-gated availability + negatives | A | `test_remote_executor_negative`, `test_execution_certificate`, `test_executor_registry` |
| Frozen request provenance | A | `test_remote_request_freeze`, `test_remote_execution_canonical` |
| Runner + write-once terminal result | A | `test_remote_runner`, `test_remote_runner_work_wiring` |
| Same-`AnalysisRun` ingestion + conflict | A | `test_remote_result_ingestor`, `test_remote_live_loop` |
| Coordinator lifecycle/fencing | A | `test_remote_coordinator*` |
| Remote startup recovery | A | `test_remote_startup_recovery` |
| DatasetExperiment remote launch | A | `test_dataset_experiment_launch_remote`, `test_dataset_experiment_attempt_remote` |
| Auto ranking incl. `remote_gpu` | A | `test_execution_selection_*`, `test_executor_selection_api` |
| **Real remote_gpu run cross-host, same AnalysisRun** | **B** | never executed cross-host |
| **Small real remote ZoomSpec DatasetExperiment + evaluation** | **B** | never executed |
| **Certified-runtime compatibility ruling** | **C (C-pre-0)** | not yet performed |
| **`remote_gpu` qualification type / install eligibility** | **D (conditional; Plan C)** | `INSTALL_ELIGIBLE_TYPES["remote_gpu"] == ()`; `cli._cmd_qualify` deferred for remote |
| **Per-installation remote certificate provisioning** | **C** | no operator remote certificate; existing cert binds only `5bb5be4` |
| **Server-side foreign-GPU admission tooling** | **C** | not present |
| **Operator bootstrap (profile/known_hosts/pin/assets)** | **E** | operator config only |
| **Plan-C acceptance artifact + ledger** | **C/E** | not present |

---

## Remaining Plan-C Gaps

1. **Gap C-1 (conditional D, gated by C-pre-0): `remote_gpu` qualification type.** Only if Ruling B. `INSTALL_ELIGIBLE_TYPES["remote_gpu"] == ()` and `cli._cmd_qualify` defers `remote_gpu`; a new remote commit yields a new `runtime_ref` requiring a new exact certificate provisioned through the platform CLI. **Actual minimum implementation surface (if required):** `backend/app/runtime_qualification/qualification.py` **and** `backend/app/cli.py` **and** the relevant qualification/CLI/certificate tests. `identity.py` is required only if a derived remote hash scheme is chosen (see "Remote Qualification Identity Model"); `doctor.py` is required only if remote doctor reporting is part of the ruling. Do not assume a single-file change.
2. **Gap C-2 (C): certified-runtime compatibility is unproven.** Must be decided by C-pre-0 with concrete field-level evidence and an operator ruling; not by "files changed".
3. **Gap C-3 (C): server-side foreign-GPU admission tooling.**
4. **Gap C-4 (E): operator bootstrap** (profile env, known-hosts + fingerprint, remote repo pin, asset map, dataset root, Host-A DB/work root).
5. **Gap C-5 (C/E): Plan-C per-installation evidence + ledger.**

---

## C-pre-0 — Certified Remote Runtime Compatibility Audit (NO INFERENCE)

**Mandatory, before any production implementation.** Compare the current Host-A control plane against the existing certified Host-B remote runtime.

```text
CURRENT CONTROL PLANE   = 6716eae (or the later Plan-C candidate derived from it)
CERTIFIED REMOTE RUNTIME = 5bb5be4b04d04a071bc9d8f4f61172595ecee037
```

### Comparison checklist (deterministic, read-only)

```text
runner CLI subcommands + argv/options
probe request fields + probe response schema (RemoteProbeResponseV1)
request batch/envelope schema + request_sha256 canonicalization
worker env bridge (WSP_REMOTE_*)
asset mapping format (legacy flat vs generic namespaced)
model-release identity + asset-manifest identity
recording fingerprint + source_data_sha256
terminal envelope schema + analysis_result.zip contract + payload_sha256
result ingestion expectations + write-once/conflict
```

### Preliminary read-only findings (planning time; MUST be formally confirmed and operator-approved in C-pre-0)

Read-only `git diff 5bb5be4..6716eae` on `backend/app/remote_execution/` already shows concrete, field-level incompatibilities (not merely renamed files):

| # | Contract | Certified runtime `5bb5be4` | Current control plane `6716eae` | Consequence |
|---|---|---|---|---|
| P1 | `probe` CLI args | `probe` takes no `--plugin-id`/`--plugin-version`/`--model-release-id`/`--asset-manifest-sha256` flags | `SshRemoteExecutorProbe.availability` sends all four | old `argparse` rejects unknown args → probe fails |
| P2 | Worker env | requires legacy flat scalars `WSP_REMOTE_DETECTOR_CHECKPOINT`, `WSP_REMOTE_FRN_CHECKPOINT`, `WSP_REMOTE_FROZEN_CONFIG`, `WSP_REMOTE_LS_STFT_NORMALIZATION` | `_runner_env_prefix` sends `WSP_REMOTE_MANIFEST_ROOT` + namespaced `WSP_REMOTE_ASSET_PATHS_JSON` only; profile rejects the legacy flat shape | old `RemoteWorkerContext.from_env` fails closed on missing scalars |
| P3 | Wire schema | `RemotePipelineRefV1`/`RemoteExecutionEnvelopeV1` have no `model_release_id`; `RemoteWireModel` is `strict=True, extra="forbid"` | adds `model_release_id` (set to `golden` for release-required plugins) and `canonical_request_payload(..., exclude_none=True)` | current request rejected by old schema; hashes differ |
| P4 | Result envelope | envelope has no `model_release_id` | `result_ingestor._verify_envelope_identity` compares against the local run’s `golden` | old-worker envelope rejected by current ingestor |

These findings **strongly indicate Ruling B**, but the formal ruling is produced by C-pre-0 execution and an explicit operator decision. They are recorded here so the audit is concrete, not speculative.

### Mandatory outcomes (exactly one)

**Ruling A — compatible.** If (and only if) C-pre-0 proves the current control plane can safely operate against `5bb5be4`:
```text
required_remote_runtime_commit = 5bb5be4b04d04a071bc9d8f4f61172595ecee037
existing exact repo certificate remains valid
NO new remote qualification production code
Host A and Host B may run distinct commits because the frozen remote
wire/runtime contract is explicitly proven compatible
```
This is the preferred minimal-change outcome if technically valid. Do not repin for commit symmetry.

**Ruling B — incompatible.** If C-pre-0 proves incompatibility with concrete field/schema/contract mismatches:
```text
required_remote_runtime_commit = Plan-C candidate SHA
PLAN_C_REMOTE_RUNTIME_REPIN_REQUIRED   (operator architecture ruling)
→ new exact remote certificate required
→ C-pre-1 remote qualification flow required
```
Proof must name exact fields/contracts (as in P1–P4), never "many files changed".

---

## Remote Qualification Identity Model

Runtime identity for `remote_gpu` is the existing, unchanged contract:

```text
runtime_ref = remote:<profile_name>:<required_remote_runtime_commit>
```

Under Ruling B, Plan C uses **option B** (justified use of the existing evidence model; no derived local hash scheme):

- **Scheme label:** `remote_commit_v1` — a label recorded in evidence meaning “identity is the exact required remote runtime commit under this profile”.
- **Authoritative material:** profile name + required remote runtime commit; NOT a hash.
- **Validation:** exact string equality `runtime_ref == remote:<profile>:<commit>` plus the live probe (Host B HEAD == required commit, assets match, CUDA device available).
- **Explicit non-claims:** `remote:<profile>:<commit>` does NOT encode GPU model, driver version, or CUDA version. Those remain live-health evidence only. Do not reuse `local_cpu_v1`/`bhq3_gpu_v1` for remote. Do not widen the `runtime_ref` string contract.
- **Distinct concepts kept separate:** remote runtime identity (commit+profile), live CUDA availability, asset integrity, host-key/transport identity.

No new hash scheme is introduced unless C-pre-0’s ruling explicitly requires one (which would then require `identity.py` changes and be named in C-pre-1).

---

## Host-A / Host-B Responsibility Matrix

```text
HOST A — LOCAL WINDOWS MACHINE (local OpenCode + operator)
  WISA control plane (ML-free); SQLite/domain authority
  Recording / DatasetExperiment authority; AnalysisRun authority; frontend
  local_cpu; remote_gpu provider (SshRemoteExecutorProbe + RemoteGpuExecutorProvider)
  remote profile config (WSP_REMOTE_*), SSH key + known_hosts presence
  local Plan-C qualification DB/work root; membership freeze
  AnalysisRun/DatasetExperiment creation; result ingestion; evaluation; provenance
                       │
                       │ SSH (fixed argv, StrictHostKeyChecking=yes) / SCP
                       ▼
HOST B — AUTODL GPU SERVER (server OpenCode + operator)
  remote execution runtime @ required commit; ZoomSpec golden assets
  CUDA / PyTorch ML interpreter (/root/miniconda3/bin/python)
  remote worker (per batch); result artifact production
  remote job/work roots under /root/autodl-tmp/plan_c_qual/
  NO authoritative platform DB; NO local AnalysisRun duplication
```

Invariant: **Host B never owns the authoritative `AnalysisRun`.**

---

## Security / SSH Model

- `SshRunner` fixed argv: `ssh -p <port> -i <key> -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=<known_hosts> <user>@<host> env <validated assignments> <remote_python> -m app.remote_execution.runner <subcommand> <args>`; `shell=False`; only platform-owned tokens / validated identifiers / trusted absolute POSIX paths.
- `scp` mirrors the same options. Remote paths derive from the trusted job root + validated identifiers.
- `StrictHostKeyChecking=no` forbidden. Known-hosts is deployment config; never committed.
- Remote assets use the generic namespaced `WSP_REMOTE_ASSET_PATHS_JSON` only.
- Fail-closed negatives (prefer deterministic / no inference): unreachable host → `REMOTE_TRANSPORT_UNAVAILABLE`; host-key mismatch → handshake failure; bad profile → `REMOTE_EXECUTOR_UNAVAILABLE`; wrong runtime commit → `REMOTE_IMPLEMENTATION_MISMATCH`; missing/mismatched asset → `PIPELINE_ASSET_MISMATCH`; missing certificate → `EXECUTION_NOT_CERTIFIED`; probe failure → `REMOTE_PROBE_UNAVAILABLE`.

---

## Per-Installation Certificate Model

Two **separate** control-plane installations must be qualified/certified independently. A certificate binds the 7-field key plus `runtime_ref`; it does **not** encode host/IP/host-key, so a loopback certificate must never be projected onto true two-host deployment.

| Installation | Control plane | Target | Recommended profile | Runtime ref | Operator cert data_root |
|---|---|---|---|---|---|
| C1 loopback | Host B (AutoDL), ML-free control-plane venv | Host B itself (SSH to `127.0.0.1`/own address) | `plan_c_loopback` | `remote:plan_c_loopback:<commit>` | C1 control-plane data root |
| C2/C3 true two-host | Host A (Windows) | Host B (AutoDL) | `autodl_primary` | `remote:autodl_primary:<commit>` | Host-A Plan-C data root |

Per-installation flow (0 model inference):

```text
runtime doctor            (provider present, remote identity status)
wisa qualify --plugin zoomspec_yolo26n_aug_combined_frn_v3 \
     --plugin-version 1.0.0 --executor remote_gpu --model-release golden
certificate install --from <evidence_dir>     # writes <data_root>/runtime_certificates.json
readback: certificate list / store membership
restart/registry rebuild requirement acknowledged (no hot reload)
```

Rules:
- Each installation runs its own qualification + install. **Default (strict final-design): two independent 0-inference qualifications.** Evidence reuse between installations is permitted **only if** `validate_evidence_for_install` re-resolves the CURRENT live authority (provider registered, `runtime_ref`, descriptor, technical capability) and the loopback vs true-two-host profiles differ (different `runtime_ref`), which the live-authority revalidation enforces. When in doubt, qualify separately.
- Qualification probes do NOT consume the six model executions.
- Do NOT silently reuse the loopback certificate as proof of true-two-host qualification.
- **Repo-default certificate commit is a secondary, operator-approved fallback only.** Default Plan-C flow prefers the operator certificate store; do not convert an installation-specific qualification into a portable repo-default certificate silently.

---

## Dataset / Recording Identity

- Double identity already implemented: `recording_fingerprint` (Recording metadata + GroundTruth) and `source_data_sha256` (exact raw-IQ bytes).
- Host B must hold identical IQ bytes; the envelope returns `source_data_sha256`; Host A rejects mismatches (`REMOTE_RESULT_INVALID`).
- C3 membership is frozen before execution.

---

## Execution Budget (frozen before live run)

| Gate | Real remote model executions | Executor | Model | Purpose | Max |
|---|---:|---|---|---|---:|
| C-pre-0 | 0 | — | — | compatibility ruling | 0 |
| C-pre-1/2/3 | 0 | — | — | implementation (if needed), qualification/cert, admission gate | 0 |
| C0 | 0 | — | — | static preflight | 0 |
| C1 loopback | 1 | `remote_gpu` | ZoomSpec golden | transport + same-run ingestion | 1 |
| C2 true two-host single | 1 | `remote_gpu` | ZoomSpec golden | one cross-host run → SAME run | 1 |
| C3 true two-host DatasetExperiment | 4 | `remote_gpu` | ZoomSpec golden | 4-item experiment + evaluation | 4 |
| C4 | 0 | — | — | deterministic reconciliation | 0 |
| C5 | 0 | — | — | evidence + regression | 0 |
| **TOTAL** | **6** | | | | **6** |

- C3 membership (frozen now): SpaceNet `test` stems **`0,1,2,3`**; `SpaceNet / test / spacenet_14`; `zoomspec_yolo26n_aug_combined_frn_v3 / 1.0.0 / golden`; `remote_gpu`; `max_concurrency=1`; exactly one `DatasetExperiment`, 4 items, 4 attempts, 4 runs; no retry.
- Any 7th real execution STOPs. Qualification/probe/certificate operations are inference-free and uncounted.

---

## C0 — Static / No-Inference Preflight

Prove, fail-closed (Host A and Host B as noted):

```text
source identity            Host A orchestrator_commit == Plan-C candidate SHA;
                           Host B HEAD == required_remote_runtime_commit; branch clean
control plane ML-free      torch=None; ultralytics=None
remote profile load        RemoteProfile.from_env from WSP_REMOTE_* (no secrets printed)
security                   known_hosts present; host-key fingerprint recorded; StrictHostKeyChecking=yes
transport connectivity     runner "probe" reachable (connectivity only)
runtime identity           Host B HEAD == required commit; probe runtime commit match
CUDA health / probe        run_probe: commit + assets + dataset root + label spaces + CUDA device 0
asset hashes               ZoomSpec golden manifest 16cc0534…; detector eba4fa4b…; frn da6087da…;
                           frozen_config 030dbfa7…; normalization 9b994655… (exact bytes)
certificate state          exact remote_gpu certificate for (ZoomSpec golden, cuda, float16,
                           remote:<installation profile>:<commit>) exists for THIS installation
dataset resolver           SpaceNet test stems 0,1,2,3 resolvable; spacenet_14 loadable
recording identity         resolve_remote_recording_identity stable fingerprint + source hash per stem
foreign-GPU gate           no unknown NVIDIA compute application (acceptance tooling)
```

Any failure STOPs (no inference).

---

## C1 — Loopback Mechanics

**≤1 real remote model execution (probe is inference-free). Server-side.**

Same AutoDL host acts as control plane (ML-free venv) and SSH target; the remote worker runs in the ML interpreter. Profile: `plan_c_loopback`; runtime_ref `remote:plan_c_loopback:<commit>`; own operator certificate under the C1 data root.

Proves: profile load; strict host-key; fixed argv/env; runtime-commit + asset verification; CUDA availability; `submit`/`status`/`work`; result download; same-`AnalysisRun` ingestion; write-once; conflict fails closed.

Does NOT prove: cross-machine portability, independent host keys, cross-machine filesystem/dataset equivalence, real network failure behavior. It is a bounded mechanics gate, not a substitute for true two-host. Uses a **fresh Plan-C loopback DB/work root**.

---

## C2 — True Two-Host Single Run

**Exactly 1 real remote model execution. Operator/local-driven (Host A).** Profile `autodl_primary`; runtime_ref `remote:autodl_primary:<commit>`.

```text
Host A: Recording registered (external-path); AnalysisRun executor=remote_gpu (manual), golden
      → request frozen (request_sha256), submitted over SSH
Host B: runner work executes ZoomSpec golden on CUDA device 0 → envelope + analysis_result.zip
Host A: download → envelope identity verify → package validate → ingest into SAME AnalysisRun
```

Acceptance: `status=completed`; `executor=remote_gpu`; descriptor == provider descriptor; `model_release_id=golden`; manifest `16cc0534…`; envelope `source_data_sha256` == Recording; detections persisted; API readback correct; write-once/conflict negatives hold.

Auto (bounded, zero-inference): `GET /api/executor-selection` resolves `resolved_executor=remote_gpu` when `remote_gpu` certified+available and `local_gpu` absent. Full Auto acceptance remains Plan D.

---

## C3 — True Two-Host Small DatasetExperiment

**Exactly 4 real remote model executions. Operator/local-driven (Host A).** Membership `0,1,2,3`; ZoomSpec golden; `remote_gpu`; `max_concurrency=1`; one `DatasetExperiment`.

Acceptance: experiment on Host A; executor frozen `remote_gpu`; 4 items/attempts/unique runs; remote workers on Host B; results return to Host A; no imported substitute; no hidden local execution; linked evaluation `completed`/`evaluated=4`/`missing=0`/`coverage=1.0`; exact provenance; no orphan remote workers.

---

## C4 — Recovery / Reconciliation (strictly zero inference)

**Strictly NO inference.** C4 MAY: `status`; read/reconcile; download existing terminal artifacts; re-ingest an identical terminal artifact; idempotent deterministic calls; CPU/unit/integration tests; DB/read-model verification.

C4 MUST NOT: submit a new inference request; create a new `AnalysisRun`; create a new attempt; invoke remote `work` if model execution can occur; restart/retry inference.

Reuse existing behavior/tests for: control-plane restart (remote never blindly interrupted); remote status reconciliation idempotency (`reconcile_status` twice); idempotent submission (`create_or_attach` → "attached" for identical `request_sha256`; conflicting semantic request → `REMOTE_REQUEST_CONFLICT`); terminal write-once/no re-execution; coordinator fencing; corrupt/missing terminal result → `REMOTE_RESULT_CORRUPTED`; provider disappearance → untouched.

If C4 discovers that proving recovery requires a new real execution:
```text
STOP
PLAN_C_EXECUTION_BUDGET_REVIEW_REQUIRED
```
Do not hide a new execution under the same `request_sha256`. `request_sha256` identity is **not** an execution-budget exemption.

---

## C5 — Final Acceptance

**Zero inference.** Acceptance artifact; runtime/provenance per gate; ledger C1..C3 == 1+1+4 = 6; full backend regression on the Plan-C candidate SHA (0 failed / 0 errors); frontend/API contract preserved (no frontend change); control plane ML-free; Plan-C remote/local workers + coordinators drained; GPU quiescent (no Plan-C compute process); Plan-B evidence read-only-verified unchanged; GPU ON.

---

## Evidence Artifacts

Proposed acceptance document (created only at C5):

```text
docs/superpowers/acceptance/2026-09-16-backend-v1-plan-c-remote-gpu-qualification.md
```

Records (never secrets): source SHAs; host roles; remote profile identity + host-key fingerprint reference; per-installation runtime_ref + certificate key + operator cert data_root; remote runtime commit; asset logical names + SHA256; dataset membership; AnalysisRun/DatasetExperiment/DatasetEvaluation IDs; execution ledger; status chronology; artifact hashes; provenance; remote/local DB counts; worker/process drain; GPU state; regression counts.

Server-side Plan-C roots (fresh; never Plan-B): `/root/autodl-tmp/plan_c_qual/` (jobs, evidence, loopback DB), `/root/autodl-tmp/plan_c_remote_runtime/` (Host B checkout at required commit). Host A: dedicated Plan-C qualification DB/work root.

---

## Failure / No-Retry Rules

Fail closed before live execution; never auto-retry a real inference:

```text
SSH uncertainty / host-key mismatch / unreachable host   -> STOP (no inference)
launch ambiguity or unknown remote state                 -> reconcile first; STOP if unresolved
transport timeout after a possible submit                -> first action is reconciliation only; never resubmit
foreign/unknown GPU compute process                      -> BLOCKED; never kill it
wrong remote runtime commit / asset / certificate        -> STOP
result corruption (envelope/zip/payload hash)            -> STOP
unexpected extra AnalysisRun/attempt/second experiment   -> STOP
budget would exceed 6                                    -> STOP; new operator ruling
```

Distinguish: safe pre-launch retry (no request submitted; no state changed) vs ambiguous post-submit state (reconcile only; never a second submit until the existing remote job identity is resolved).

---

## Regression Matrix

| Check | Scope | Gate |
|---|---|---|
| Remote unit/integration | `pytest backend/tests -k "remote" -q` | 0 failed / 0 errors |
| Dataset experiment remote | `test_dataset_experiment_launch_remote.py`, `test_dataset_experiment_attempt_remote.py` | green |
| Execution selection / Auto | `test_execution_selection_*.py`, `test_executor_selection_api.py` | green |
| Qualification (after C-pre-1, if any) | `test_cli.py`, `test_runtime_qualification*.py`, `test_certificate_install.py`, `test_runtime_identity.py`, `test_runtime_doctor.py` | green |
| Full backend regression | `PYTHONPATH="$PWD/backend:$PWD/scripts" <control-plane venv> -m pytest backend/tests -q` | 0 failed / 0 errors |
| Control-plane ML-free | `find_spec("torch")`/`find_spec("ultralytics")` | both None |
| Plan-B evidence immutability | read-only SHA256 of Plan-B NC-02 artifacts + `qual.db` | unchanged |
| Frontend | not re-run on server (accepted) | no change |

---

## H5.5-S Handoff Boundary

Plan C ends with the GPU still ON. No H5.5-S, cold switch, H5.5-C, Plan D, or `BACKEND_V1_SEAL_SHA`. H5.5-S requires all Plan-C local/remote processes terminal and zero Plan-C remote worker PIDs, and follows independent Plan-C review.

---

## Exact Task Ownership

**Server OpenCode (Host B):** C-pre-0 read-only evidence; pin `plan_c_remote_runtime` to the required commit; C0 remote-side checks (commit, CUDA, interpreter, assets, dataset root, remote identity, runner state, work roots, GPU quiescence, foreign-GPU gate); C1 loopback (control plane + target on same host); remote-side observation during C2/C3 (worker drain, quiescence, no orphans, artifact integrity); never create the authoritative local run.

**Local OpenCode (Host A, Windows):** checkout exact Plan-C candidate SHA; CPU-only control plane; remote profile config + known_hosts/key presence; dedicated Plan-C DB/work root; membership freeze; create C2 run and C3 experiment (manual `remote_gpu` + zero-inference Auto endpoint observation); poll/read; verify DB authority + evaluation; capture local provenance/evidence.

**Operator-only:** SSH key + known_hosts + host-key fingerprint; C-pre-0 architecture ruling (including any repin); approve the C-pre-1 surface; run the true two-host session; report evidence.

---

## Stop Conditions

```text
source/branch not at the exact approved SHA
control plane not ML-free
C-pre-0 unresolved (no explicit Ruling A/B)
remote HEAD != required_remote_runtime_commit
host-key mismatch or `StrictHostKeyChecking=no` anywhere
foreign GPU compute process present before a live gate
asset/certificate mismatch
ambiguous post-submit state not reconciled
worker/GPU quiescence failure
any execution above 6, or any second experiment/retry
Plan-B evidence mutation
GPU powered off
```

---

## Revised Task Order

```text
C-pre-0  Remote protocol/runtime compatibility audit            (0 inference)
         │
         ├── Ruling A: keep certified remote runtime
         │             minimize/no production changes
         │
         └── Ruling B: operator-approved repin
                       PLAN_C_REMOTE_RUNTIME_REPIN_REQUIRED
                       → C-pre-1 required
                             │
C-pre-1  remote_gpu qualification implementation                 (0 inference)
         ONLY if Ruling B; surface = qualification.py + cli.py + tests
         (+ identity.py/doctor.py only if the identity ruling requires)
         TDD; no change to remote-execution behavior
                             │
C-pre-2  per-installation qualification + certificate provisioning (0 inference)
         loopback installation AND true two-host installation, separately
                             │
C-pre-3  foreign-GPU admission gate tooling                      (0 inference)
                             │
C0       static preflight                                        (0)
         │
C1       loopback mechanics                                      (1)
         │
C2       true two-host single run                                (1)
         │
C3       true two-host small DatasetExperiment                   (4)
         │
C4       zero-inference reconciliation                           (0)
         │
C5       acceptance + regression                                 (0)
```

Do not pre-decide C-pre-1 before C-pre-0. Do not amend `b4fcb533…`.

---

## Production Change Policy

- Default: **no production change** beyond acceptance tooling.
- Only under Ruling B: a bounded remote qualification flow spanning **`backend/app/runtime_qualification/qualification.py` + `backend/app/cli.py` + tests** (and `identity.py`/`doctor.py` only if the identity ruling requires). Justified as parity with the Plan-B `local_gpu_cuda_v1` addition; changes only how remote identity is *qualified/certified*, never how remote execution *runs*.
- Repo-default certificate edits remain a secondary, operator-approved fallback.
- Any other production change must be separately justified and reviewed; if a remote-execution production defect is found, STOP and report.

---

## Plan Self-Review

1. **C-pre-0 precedes production change; ruling not pre-decided in the plan.** Preliminary read-only evidence (P1–P4) recorded for the audit but the ruling is C-pre-0 + operator. ✅
2. **Implementation surface corrected (I-1).** Minimum = `qualification.py` + `cli.py` + tests; `identity.py`/`doctor.py` conditional on the identity ruling; no single-file claim. ✅
3. **Compatibility proven by contract, not filenames.** P1–P4 are concrete field/schema mismatches. ✅
4. **Per-installation certificates (I-3).** Loopback (`plan_c_loopback`) and true two-host (`autodl_primary`) are distinct installations with distinct `runtime_ref`, data roots, and (default) separate 0-inference qualifications; no cross-projection. ✅
5. **Remote identity model explicit.** `runtime_ref = remote:<profile>:<commit>`; scheme label `remote_commit_v1`; no GPU/driver encoded; local schemes not reused. ✅
6. **Strict C4 zero-inference (I-4).** No submit/work/run/attempt; budget review STOP if inference would be required; `request_sha256` is not an exemption. ✅
7. **Plan-B read-only semantics (I-5).** Read-only hash/stat allowed; lifecycle/mutation/reuse forbidden. ✅
8. **Budget frozen at 6** (1+1+4); membership `0,1,2,3`; 7th execution STOPs. ✅
9. **No secrets; fixed-argv strict SSH; same-run authority; GPU stays ON; H5.5-S outside Plan C; no seal.** ✅
10. **Budget/Plan-B arithmetic untouched** (Plan-B final actual 152). ✅
11. **All referenced paths/tests/APIs verified against `6716eae`.** ✅
