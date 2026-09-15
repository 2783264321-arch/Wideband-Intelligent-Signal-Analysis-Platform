# Backend V1 Plan C — Remote-GPU / True Two-Host Qualification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Qualify the accepted Backend V1 `remote_gpu` execution path end-to-end on the real AutoDL GPU target while it is still rented — profile configuration, loopback transport mechanics, a bounded true two-host single `AnalysisRun`, a small true two-host ZoomSpec `DatasetExperiment`, deterministic recovery/reconciliation evidence, and a frozen Plan-C acceptance record — without changing remote-execution behavior, without weakening the exact per-installation certificate model, and without starting H5.5-S or turning the GPU off.

**Architecture:** Plan C is primarily a **qualification/campaign** plan. The M9.1 remote-execution infrastructure is already implemented and unit/integration tested at the integration baseline. Plan C consumes it unchanged. C-pre-0 has already proved the existing certified remote runtime **incompatible** (P1–P4, hard, no adapter) and the operator ruling is **Ruling B** (`PLAN_C_REMOTE_RUNTIME_REPIN_REQUIRED`); therefore a `remote_gpu` runtime-qualification type + install-eligibility support is now a **required C-pre-1 production-seam change**, not a conditional one. Remote execution behavior itself remains unchanged. Two distinct control-plane installations (loopback and true two-host) each qualify/certify separately.

**Tech Stack:** Python 3.12, FastAPI + Pydantic v2, SQLAlchemy 2 / SQLite, pytest (control plane ML-free), OpenSSH `ssh`/`scp`, AutoDL ML interpreter `/root/miniconda3/bin/python`, Plan-B control-plane venv `/root/autodl-tmp/WISA-backend-v1-plan-b-local-gpu/.venv/bin/python`.

**Spec:** `docs/superpowers/specs/2026-09-14-backend-v1-final-qualification-design.md`

**Authoritative baseline:** integration branch `integration/v1-candidate` @ `6716eaef217de97c5746d0468d62abede9b1c63c` (parents `2a76219388bf76b791c91260d4af5a57f8677c02`, `291861d36e2045396996e5d2b7beb5d0f2ac09e1`; merge base `d4b22ee4f01914974aaf47c1a88e4f8afa461913`). Backend regression on that SHA: focused 188 passed; full 2138 passed / 32 skipped / 0 failed / 0 errors; control plane `torch: None`, `ultralytics: None`. Plan B is `COMPLETE WITH OPERATOR-APPROVED DEVIATIONS` (final actual 152).

**This document revision:** supersedes the first Plan-C draft (`b4fcb533…`) per independent review, and records the **executed C-pre-0 audit and its Ruling B** (`PLAN_C_REMOTE_RUNTIME_REPIN_REQUIRED`; Ruling A rejected). Deltas: I-1 implementation surface; C-pre-0 result + ruling; strict per-installation certificate/no-reuse semantics; normative Pre-Live Freeze Rule and reordered C-pre phases; I-4 strict zero-inference C4; I-5 Plan-B read-only semantics; remote identity label `remote_commit_v1`. The Plan-C source branch is `feature/backend-v1-plan-c-remote-gpu`, created from the exact integration SHA.

## Global Constraints

1. **C-pre-0 is complete and immutable for Plan C.** The ruling is fixed: `PLAN_C_REMOTE_RUNTIME_REPIN_REQUIRED` / Ruling B. No `remote_gpu` qualification code is written until C-pre-1, and C-pre-1 may implement **only** the approved `remote_gpu` qualification/install seam as named below. No compatibility shim for `5bb5be4` is permitted. Ruling A must not be reopened without a new operator architecture ruling.
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
| **Certified-runtime compatibility ruling** | **RESOLVED** | C-pre-0 executed: Ruling B; `PLAN_C_REMOTE_RUNTIME_REPIN_REQUIRED` |
| **`remote_gpu` qualification type / install eligibility** | **D — REQUIRED by Ruling B** | `INSTALL_ELIGIBLE_TYPES["remote_gpu"] == ()`; `cli._cmd_qualify` still deferred for remote; C-pre-1 required |
| **Per-installation remote certificate provisioning** | **C** | no operator remote certificate; existing cert binds only `5bb5be4` |
| **Server-side foreign-GPU admission tooling** | **C** | not present |
| **Operator bootstrap (profile/known_hosts/pin/assets)** | **E** | operator config only |
| **Plan-C acceptance artifact + ledger** | **C/E** | not present |

---

## Remaining Plan-C Gaps

1. **Gap C-1 (D, Ruling B selected): `remote_gpu` qualification type.** `INSTALL_ELIGIBLE_TYPES["remote_gpu"] == ()` and `cli._cmd_qualify` defers `remote_gpu`; a new remote commit yields a new `runtime_ref` requiring a new exact certificate provisioned through the platform CLI. **Actual minimum implementation surface:** `backend/app/runtime_qualification/qualification.py` **and** `backend/app/cli.py` (`_cmd_qualify` remote wiring **and** `runtime doctor`/identity resolver for `remote_gpu`) **and** the applicable qualification/CLI/certificate tests. `identity.py`/`doctor.py` only if the implementation proves they are required. Do not assume a single-file change.
2. **Gap C-2 (resolved by C-pre-0): certified-runtime incompatibility proven.** Four hard field-level incompatibilities (P1–P4) with no compatibility adapter → `PLAN_C_REMOTE_RUNTIME_REPIN_REQUIRED` (Ruling B); Ruling A rejected.
3. **Gap C-3 (C): server-side foreign-GPU admission tooling** (acceptance-only, `scripts/`, no production dependency).
4. **Gap C-4 (E): operator bootstrap** (profile env, known-hosts + fingerprint, remote repo pin, asset map, dataset root, Host-A DB/work root).
5. **Gap C-5 (C/E): Plan-C per-installation evidence + ledger.**

---

## C-pre-0 — Certified Remote Runtime Compatibility Audit (NO INFERENCE)

**STATUS: COMPLETE (executed 2026-09-16) — this gate precedes C-pre-1 and its result is frozen.** Compare the current Host-A control plane against the existing certified Host-B remote runtime (recorded below).

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

### C-pre-0 RESULT — EXECUTED (2026-09-16, read-only source audit)

C-pre-0 was executed against the exact sources (`git show`/`git diff`, no inference). All four findings are **independently confirmed** as `HARD_INCOMPATIBILITY`, and no compatibility adapter exists in the accepted current source (no `WSP_REMOTE_DETECTOR_CHECKPOINT`/`_FRN_CHECKPOINT`/`_FROZEN_CONFIG`/`_LS_STFT_NORMALIZATION` anywhere in `backend/app/`; current `runner._cli_probe` hard-requires `args.plugin_id`/`args.plugin_version`/`args.asset_manifest_sha256`).

| # | Contract | Certified runtime `5bb5be4` | Current control plane `6716eae` | Confirmed evidence | Verdict |
|---|---|---|---|---|---|
| P1 | `probe` CLI args | `probe` subparser declares **no** arguments | `runner._build_parser` declares `--plugin-id`, `--plugin-version`, `--model-release-id`, `--asset-manifest-sha256`; `SshRemoteExecutorProbe.availability` sends all four | old `argparse` rejects unknown args | HARD_INCOMPATIBILITY |
| P2 | Worker env | `RemoteWorkerContext.from_env` calls `_require_posix` on the four legacy flat scalars `WSP_REMOTE_DETECTOR_CHECKPOINT`, `WSP_REMOTE_FRN_CHECKPOINT`, `WSP_REMOTE_FROZEN_CONFIG`, `WSP_REMOTE_LS_STFT_NORMALIZATION` | `_runner_env_prefix` sends generic namespaced `WSP_REMOTE_ASSET_PATHS_JSON` (+ `WSP_REMOTE_MANIFEST_ROOT`), never the four scalars; `_parse_generic_asset_paths` explicitly rejects the legacy flat shape | old worker fails closed on missing scalars | HARD_INCOMPATIBILITY |
| P3 | Request wire schema | `RemoteWireModel` = `strict=True, extra="forbid"`; `RemotePipelineRefV1` has only `{id, version}` | `request_builder._build_batch_content` sets `pipeline.model_release_id = metadata["model_release_id"]` (= `golden`); `canonical_request_payload(..., exclude_none=True)` | current request rejected by old strict schema | HARD_INCOMPATIBILITY |
| P4 | Result envelope | `RemoteExecutionEnvelopeV1` has no `model_release_id` | current envelope adds `model_release_id`; `result_ingestor._verify_envelope_identity` compares `envelope.model_release_id` to the local frozen `golden` when `local_release_id is not None` | old-worker envelope rejected by current ingestor | HARD_INCOMPATIBILITY |

Because one or more hard incompatibilities are proven and no compatibility adapter exists, the operator authorization condition is met.

### Formal operator architecture ruling

```text
PLAN_C_REMOTE_RUNTIME_REPIN_REQUIRED
Ruling B — incompatible — SELECTED
required_remote_runtime_commit = the frozen PLAN_C_PRELIVE_SHA (see Pre-Live Freeze Rule)
```

**Ruling A is marked `REJECTED BY C-PRE-0`.** It is retained only as historical decision-tree documentation; no executable Ruling-A branch remains. Do not add a compatibility shim to preserve `5bb5be4`; do not modify the accepted remote-execution protocol. (Note: Ruling A was also internally inconsistent with C1 — the only existing repo certificate covers `remote:autodl_primary:5bb5be4…`, not `remote:plan_c_loopback:…`.)

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
- **No qualification-evidence reuse across the two installations.** `install` requires exact `evidence.runtime_ref == live authority.runtime_ref` and `evidence.runtime_descriptor == live authority.runtime_descriptor`; since `remote:plan_c_loopback:<commit>` ≠ `remote:autodl_primary:<commit>`, loopback evidence can never install the true-two-host certificate (and vice versa). Each installation performs its **own** `qualify → evidence → certificate install → readback → registry/control-plane rebuild`.
- Both installations are zero-model-inference qualification operations and do NOT consume the six model executions.
- The loopback certificate never authorizes the true two-host installation; the true two-host certificate never authorizes loopback.
- **Repo-default certificate commit is a secondary, operator-approved fallback only.** Default Plan-C flow prefers the operator certificate store (`<data_root>/runtime_certificates.json`); do not convert an installation-specific qualification into a portable repo-default certificate silently.

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
| C-pre-1 / C-pre-3 / C-pre-FREEZE / C-pre-2 | 0 | — | — | qualification seam, admission tooling, SHA freeze, two installs | 0 |
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
C-pre-0 compatibility ruling absent or reopened (must remain Ruling B; Ruling A is REJECTED)
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
C-pre-0  remote protocol/runtime compatibility audit             (0 inference)
         DONE — Ruling B; PLAN_C_REMOTE_RUNTIME_REPIN_REQUIRED
         Ruling A marked REJECTED BY C-PRE-0 (historical only)
         │
C-pre-1  remote_gpu qualification/install support implementation  (0 inference)
         surface = qualification.py + cli.py + tests
         (identity.py/doctor.py only if the identity ruling requires)
         TDD; no change to remote-execution behavior
         │
C-pre-3  foreign-GPU admission / acceptance tooling              (0 inference)
         acceptance-only under scripts/; no production nvidia-smi dependency
         │
PRE-LIVE REGRESSION
         focused + full backend regression: 0 failed / 0 errors
         control plane ML-free
         │
C-pre-FREEZE
         freeze exact PLAN_C_PRELIVE_SHA
         Host A orchestrator checkout   = PLAN_C_PRELIVE_SHA
         Host B remote runtime checkout = PLAN_C_PRELIVE_SHA
         required_remote_runtime_commit = PLAN_C_PRELIVE_SHA
         │
C-pre-2  per-installation qualification + certificate provisioning (0 inference)
         (a) plan_c_loopback   — its own qualify/evidence/install/readback
         (b) autodl_primary    — its own qualify/evidence/install/readback
         NO cross-installation evidence reuse
         │
C0       static preflight                                         (0)
         │
C1       loopback mechanics                                       (1)
         │
C2       true two-host single run                                 (1)
         │
C3       true two-host small DatasetExperiment                    (4)
         │
C4       zero-inference reconciliation                            (0)
         │
C5       acceptance + regression                                  (0)
```

Do not amend `b4fcb533…` or `9ddf640…`.

---

## Pre-Live Freeze Rule (normative)

Certificate installation binds the exact `runtime_ref`, which embeds `required_remote_runtime_commit`. Therefore **certificate provisioning must occur only after all source-changing prerequisite work is complete.**

```text
C-pre-1 (source) → C-pre-3 (source) → PRE-LIVE REGRESSION
  → C-pre-FREEZE: freeze PLAN_C_PRELIVE_SHA
  → C-pre-2: provision certificates at PLAN_C_PRELIVE_SHA
  → C0..C4 pinned to PLAN_C_PRELIVE_SHA
```

Once `PLAN_C_PRELIVE_SHA` is frozen and C-pre-2 certificates are provisioned, **no production/backend/script/config source change is allowed before C0–C4 complete.** If a source change becomes necessary after freeze:

```text
STOP
invalidate the Plan-C pre-live freeze
invalidate any not-yet-used Plan-C certificate assumptions
new review required
```

Documentation/evidence recording may be committed later, but the Host-A orchestrator and Host-B remote runtime actually used for C1–C4 must remain exactly pinned to the recorded `PLAN_C_PRELIVE_SHA`. Do not mint a certificate and then create another source-changing commit that changes the required remote runtime SHA.

---

## Production Change Policy

- C-pre-0 selected **Ruling B**; the bounded C-pre-1 change is therefore required. Surface: **`backend/app/runtime_qualification/qualification.py` + `backend/app/cli.py` + the applicable qualification/CLI/certificate tests** (`test_cli.py`, `test_runtime_qualification.py`, `test_runtime_qualification_integration.py`, `test_certificate_install.py`). `cli.py` review must cover **both** `_cmd_qualify` remote_gpu probe/runner wiring **and** `runtime doctor` / identity-resolver behavior for `remote_gpu`. Modify `doctor.py`/`identity.py` only if the implementation proves they are required. No remote-execution behavior change.
- C-pre-3 is acceptance tooling only, under `scripts/`; `backend/app/**` must not depend on `nvidia-smi`/`/proc`/cgroup.
- Repo-default certificate edits remain a secondary, operator-approved fallback.
- Any other production change must be separately justified and reviewed; if a remote-execution production defect is found, STOP and report.

---

## Plan Self-Review

1. **C-pre-0 executed; ruling recorded, Ruling A rejected.** P1–P4 confirmed as hard incompatibilities with no compatibility adapter; `PLAN_C_REMOTE_RUNTIME_REPIN_REQUIRED`. ✅
2. **Implementation surface corrected (I-1).** Minimum = `qualification.py` + `cli.py` + tests; `cli` covers `_cmd_qualify` and doctor/identity for `remote_gpu`; `identity.py`/`doctor.py` conditional; no single-file claim. ✅
3. **Compatibility proven by contract, not filenames.** P1–P4 are exact field/schema evidence. ✅
4. **Per-installation certificates (I-3).** Strict no cross-installation evidence reuse; each of `plan_c_loopback` / `autodl_primary` qualifies, installs, reads back, and rebuilds separately. ✅
5. **Remote identity model explicit.** `runtime_ref = remote:<profile>:<commit>`; scheme label `remote_commit_v1`; no GPU/driver encoded; local schemes not reused. ✅
6. **Strict C4 zero-inference (I-4).** No submit/work/run/attempt; budget review STOP if inference would be required; `request_sha256` is not an exemption. ✅
7. **Plan-B read-only semantics (I-5).** Read-only hash/stat allowed; lifecycle/mutation/reuse forbidden. ✅
8. **Budget frozen at 6** (1+1+4); membership `0,1,2,3`; 7th execution STOPs. ✅
9. **No secrets; fixed-argv strict SSH; same-run authority; GPU stays ON; H5.5-S outside Plan C; no seal.** ✅
10. **Budget/Plan-B arithmetic untouched** (Plan-B final actual 152). ✅
11. **All referenced paths/tests/APIs verified against `6716eae`.** ✅
