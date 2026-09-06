# M9.1 Live Frozen-Pipeline Remote GPU Inference — Design

Date: 2026-09-06
Status: Formal design (product/architecture owner confirmed; corrective review applied)
Base: `feature/v1-core @ 9a6f0feac0b0e6e2ac8ecd65d2e4383479e09f7c`

## 1. Background and Objective

The platform already supports the full dataset-level evaluation loop:

```text
Recording
  -> imported AnalysisRun
  -> DetectionResult
  -> Case Analysis
  -> Dataset Benchmark
  -> 2500 SpaceNet real benchmark
```

M8.6C is complete and integrated into `feature/v1-core @ 9a6f0feac0b0e6e2ac8ecd65d2e4383479e09f7c`.

The verified historical frozen pipeline is:

- pipeline_id: `zoomspec_yolo26n_aug_combined_frn_v3`
- pipeline_version: `1.0.0`
- historical SpaceNet/test benchmark: 2500 recordings, 33373 predictions
- class-aware mAP50: `0.49706861157413673`
- class-aware mAP50:95: `0.3732512758737991`
- localization AP50: `0.8666496180384053`
- localization AP50:95: `0.6715938311249926`
- matched classification accuracy: `0.8151265187827444`

M9.1 does not research a new model. Its single objective is to let the local Windows platform create a genuine:

```text
AnalysisRun(executor=remote_gpu)
```

then drive the frozen pipeline on AutoDL over SSH, run real inference, and safely write the standard result back to the **same AnalysisRun**.

After M9.1, the platform capability moves from:

```text
historical result import + analysis
```

to:

```text
platform directly executes real GPU pipeline + analysis.
```

## 2. Out of Scope

M9.1 does **not** include:

- new model training;
- RT-DETR / D-FINE / new detectors;
- a parameter-tuning system;
- arbitrary local IQ upload to the server;
- custom Recording remote upload;
- a resident HTTP inference service;
- a FastAPI remote inference daemon;
- Docker / container runtime;
- Redis / Celery;
- a `RemoteJob` ORM table;
- a `BatchRun` ORM table;
- a new top-level UI page;
- a complex batch experiment UI;
- ZoomSpec intermediate-stage visualization;
- M9.2 content;
- M10 content.

## 3. Core Domain Model Is Unchanged

M9.1 keeps the existing chain:

```text
Recording
  -> AnalysisRun
  -> DetectionResult
```

`DatasetEvaluation` continues to aggregate ordinary `AnalysisRun` rows.

The following duplicate domain entities are explicitly forbidden:

```text
RemoteJob
BatchRun
RemoteAnalysisRun
GpuRun
ExperimentRun
```

A remote batch is **transport / execution provenance only**, never a new domain model.

## 4. Executor Architecture

Today there is `local_cpu`. M9.1 extends it with `remote_gpu`.

The architecture must clearly separate:

- `AnalysisService` — creates the `AnalysisRun`, checks pipeline / recording / executor compatibility, and does not perform SSH itself.
- Executor abstraction — one implementation per executor kind.

Recommended component boundaries:

```text
AnalysisService
  - creates AnalysisRun
  - checks pipeline / recording / executor compatibility
  - no SSH responsibility

LocalCpuExecutor / LocalJobManager
  - preserves existing local subprocess behavior

RemoteGpuExecutor
  - validates remote executability
  - builds the remote execution request
  - calls RemoteGpuJobManager

RemoteGpuJobManager
  - SSH submit
  - SSH status
  - SCP/SFTP result download
  - contains no pipeline algorithm logic

RemoteRunSupervisor
  - background supervision of pending/running remote runs
  - polls remote item status
  - triggers download/ingest on completion

RemoteRunReconciler
  - restores remote_gpu running runs after platform restart

RemoteResultIngestor
  - validates the remote result
  - atomically writes back to the SAME AnalysisRun

AnalysisResultWriter
  - shared by local worker and remote ingest:
    - DetectionResult validation / persistence
    - artifact indexing
    - run metadata persistence
    - completion transition
```

SSH is transport only. SSH must never become the pipeline API.

## 5. AnalysisRun Metadata Boundary

The existing `AnalysisRun` already has:

```text
executor
status
parameters_json
hardware_info_json
worker_pid
started_at
finished_at
error_type
error_message
```

M9.1 recommends an additive migration:

```text
execution_metadata_json  JSON nullable
```

`execution_metadata_json` stores:

- request_id
- batch_id
- item_key
- request_sha256
- payload_sha256
- remote_profile
- orchestrator_commit
- remote_runtime_commit
- asset identities
- submission provenance
- remote job identity
- remote server pid / job identity
- remote start/end timestamps
- result package identity

`parameters_json` continues to represent algorithm parameters only. SSH / batch / host provenance must not be mixed into `parameters_json`.

`hardware_info_json` continues to hold:

- GPU model
- CUDA version
- PyTorch version
- Python version
- key runtime versions
- remote environment identity

`hardware_info_json` must never store SSH private keys, passwords, tokens, or secrets.

### 5.1 Local worker PID vs remote job identity

`worker_pid` remains local-worker-specific and must never pretend to be the server PID. The remote server PID / remote job identity is recorded in `execution_metadata_json`.

`started_at` for a remote `AnalysisRun` is set when the corresponding remote item actually enters `running`. `finished_at` is set when the local terminal-state transaction commits. Remote start/end timestamps are preserved separately inside the envelope / execution provenance.

## 6. Remote Profile

Server deployment information comes from local secure configuration / environment, not from Git.

Logical profile name: `autodl_primary`.

The profile may contain:

- host
- port
- user
- ssh key reference
- known_hosts / pinned trusted host key
- remote repo root
- remote job root
- dataset roots (e.g., `SpaceNet -> server dataset root`)
- pipeline assets (logical asset -> absolute path)

Secrets and deployment absolute paths must never enter Git-tracked pipeline definitions.

## 7. Recording Resolution and Raw-IQ Identity

M9.1-A (first slice) supports only Recordings whose raw IQ already exists on the server. The first dataset is `SpaceNet`.

A remote request must not carry `/root/.../0.bin` or any arbitrary remote absolute path. It carries logical identity only:

```text
dataset_name
dataset_split
dataset_key
label_space
expected_recording_fingerprint
expected_source_data_sha256
```

For the current SpaceNet case:

- dataset_name = `SpaceNet`
- dataset_split = `test` / `train`
- dataset_key = `Recording.name` / sample stem
- label_space = `spacenet_14`

### 7.1 Two independent identities

Remote recording identity requires **both**:

```text
recording_fingerprint_v1   = dataset / metadata / GroundTruth semantic identity
source_data_sha256         = exact IQ-byte identity
```

`recording_fingerprint_v1` does **not** hash raw IQ bytes. It hashes canonical Recording metadata + GroundTruth semantics. It cannot by itself prove that the local `SpaceNet/test/0.bin` and the server `SpaceNet/test/0.bin` contain identical IQ samples. M8.6B/M8.6C depend on the existing `recording_fingerprint_v1` semantics; they are unchanged.

A separate, independent raw-source identity is added:

```text
Recording.source_data_sha256   (nullable SHA256 string)
```

- represents exact raw IQ data bytes;
- is separate from `recording_fingerprint_v1`;
- is an additive nullable field (no change to existing identity semantics).

Local side:

```text
Recording
  -> resolve local .bin
  -> if source_data_sha256 absent, compute SHA256 once
  -> persist/cache it
  -> remote request carries expected_source_data_sha256
```

Server side:

```text
dataset resolver
  -> resolve exact .bin
  -> hash exact raw .bin bytes
  -> require equality with expected_source_data_sha256
  -> only then permit inference
```

Explicit error:

```text
SOURCE_DATA_HASH_MISMATCH
```

Remote recording verification fails closed. The server never silently recomputes/accepts a new expected hash. For the 2500 batch, local hashes may be precomputed/backfilled once; the same local file is not re-hashed for every later run when its cached identity is already trusted for that Recording snapshot.

### 7.2 No GroundTruth leakage

GroundTruth may be read by the dataset resolver **only** for recording identity / fingerprint validation. GroundTruth / SpaceNet signals / class labels must never be passed into:

```text
LS-STFT
detector
AHLP
FRN
pipeline inference input
pipeline preprocessing
pipeline postprocessing decisions
```

The frozen inference path receives only:

```text
raw IQ
non-label signal metadata required for physical coordinate interpretation
```

The server-side SpaceNet resolver may parse the `.json` for validation, but must construct a sanitized inference input that excludes signals/labels.

Future Testing Strategy must include:

- resolver identity may inspect GT;
- frozen inference adapter cannot access/use GT;
- a test proving predictions do not depend on GroundTruth rows.

## 8. Remote Execution Request V1

Define a strict versioned wire contract.

`RemoteExecutionRequestV1` contains at least:

```text
schema_version
request_id
orchestrator_commit

pipeline:
  id
  version

recording:
  dataset_name
  dataset_split
  dataset_key
  label_space
  expected_recording_fingerprint
  expected_source_data_sha256

parameters

asset_manifest_identity
```

### 8.1 RemoteExecutionBatchV1 and canonical request identity

Batch transport definition:

```text
RemoteExecutionBatchV1
  schema_version
  batch_id
  required_remote_runtime_commit
  request_sha256
  pipeline identity
  N items
```

`request_sha256` is the canonical deterministic payload hash of the batch request (the exact serialized request bytes). It is the stable identity used for idempotent submission.

Submission semantics:

- same `batch_id` + same `request_sha256` -> idempotent create-or-attach; return/status the existing remote job; **must not** start a duplicate worker;
- same `batch_id` + different `request_sha256` -> `REMOTE_REQUEST_CONFLICT`, fatal.

This handles the case where the server accepted a job but the SSH connection died before the local side received the ACK. The local side reconciles the same `batch_id` first and must not immediately create a second remote job.

If submit transport fails:

1. query/reconcile the same `batch_id`;
2. if a job exists with a matching request hash: attach/resume;
3. if the server definitively proves the job is absent: `REMOTE_SUBMIT_FAILED`;
4. if server status itself is temporarily unavailable: keep the run recoverable and record transient transport diagnostics; do not falsely mark a potentially-running remote job `failed`.

Each item maps to an existing local `AnalysisRun`. The server never creates an `AnalysisRun`.

## 9. Remote Job Lifecycle

The remote job and the SSH session are decoupled. SSH handles only submit / status / download. The remote worker runs as a detached process.

A remote job directory, e.g. `<remote_job_root>/<batch_id>/`, contains:

```text
request.json
status.json
stdout.log
stderr.log
results/
```

All status/result files are written temp -> close/fsync -> atomic rename, to avoid downloading half-written files.

- Closing the local browser does not affect the remote job.
- Local FastAPI restart does not affect the remote job.

## 10. State Machine

User-visible `AnalysisRun` status stays:

```text
pending
running
completed
failed
interrupted
```

No new external domain statuses are introduced (`reconciling`, `downloading`, `remote_completed`).

A `remote_gpu` `running` status is reconcilable.

Rules:

- `local_cpu` running + platform restart -> `interrupted`
- `remote_gpu` running + platform restart -> query remote state
  - remote running -> resume supervision
  - remote completed -> download + validate + ingest
  - remote failed -> `AnalysisRun` `failed`
  - remote job missing / unrecoverable -> `interrupted`

Critical rule:

```text
remote inference completed != AnalysisRun completed
```

`AnalysisRun.status = completed` only after:

```text
download
  -> integrity validation
  -> identity validation
  -> schema validation
  -> physical bbox / label validation
  -> local DB transaction commit
```

all succeed.

### 10.1 Idempotent / immutable local result ingest

`RemoteRunSupervisor` and `RemoteRunReconciler` may race or repeat the same completion event. `RemoteResultIngestor` must therefore be idempotent.

A stable remote result identity is used, for example `payload_sha256` (see Section 11).

Transaction rules:

- `pending`/`running` run + valid matching result -> ingest once -> `completed`;
- already `completed` run + exact same `payload_sha256` -> idempotent no-op / already ingested; never duplicate detections;
- already `completed` run + different `payload_sha256` -> `REMOTE_RESULT_CONFLICT`, fatal audit error; never overwrite completed detections;
- `failed` / `interrupted` / `completed` AnalysisRuns must not be silently resurrected or mutated by an unrelated result.

A completed `AnalysisRun` is immutable.

When `AnalysisResultWriter` is extracted from the current local worker, it must **not** preserve an unconditional "delete all detections then rewrite" behavior for completed remote runs.

### 10.2 Batch infrastructure failure semantics

Partial batch failure is explicit:

```text
2500 items
items 1..1733 completed
remote worker crashes before the remaining items finish
```

Required behavior:

- already locally ingested completed items -> remain completed forever;
- item with an explicit pipeline/data error -> `failed`;
- items that were still `pending`/`running` when the batch infrastructure died and have no valid terminal result -> `interrupted`.

Completed items are never rolled back. Completed items are never marked `failed` because the batch later died.

Retry of terminal `failed`/`interrupted` scientific runs normally creates **new** `AnalysisRun` rows. Exception: an uncertain submit/status event must first reconcile the same `AnalysisRun` rows / `batch_id` before declaring them terminal.

## 11. Result Contract

Remote results use:

```text
RemoteExecutionEnvelopeV1
+ Analysis Package v1 compatible result semantics
```

### 11.1 Result layout and hash coverage

Per-item remote result:

```text
result/
  envelope.json
  analysis_result.zip
```

`envelope.json` includes:

```text
schema_version
request_id
batch_id
item_key
local_run_id
recording_fingerprint
source_data_sha256
pipeline_id
pipeline_version
orchestrator_commit
remote_runtime_commit
asset identities
hardware/runtime provenance
payload_sha256
remote start/end timestamps
```

`payload_sha256` = SHA256 of the **exact bytes of `analysis_result.zip`**.

`analysis_result.zip` contains the Analysis Package v1-compatible payload. The envelope is **not** included in its own payload hash (this avoids a self-referential hash).

Local flow:

1. download `envelope.json`;
2. download `analysis_result.zip`;
3. verify the exact ZIP bytes against `payload_sha256`;
4. then safe-extract and validate the internal schema.

If another exact layout is chosen, it must preserve the same non-self-referential integrity rule.

The detection wire schema continues to be the existing standard:

```text
t_start_s
t_end_s
f_low_hz
f_high_hz
class_id
class_name
confidence
scores
```

Units:

- time = seconds
- frequency = absolute Hz

Pixels are never a domain output.

The local `RemoteResultIngestor` must verify:

- request_id
- batch_id
- item identity
- local run id
- recording fingerprint
- source data hash
- pipeline id/version
- remote runtime commit
- asset identity
- payload hash
- strict schema
- bbox bounds
- label validity
- confidence validity

A remote result writes back only to the original `AnalysisRun`. It must never call `ImportedRunService` to create a second `executor=imported` run.

## 12. Frozen Pipeline Implementation

The only M9.1 live pipeline is:

```text
zoomspec_yolo26n_aug_combined_frn_v3  /  1.0.0
```

Target algorithm chain:

```text
Wideband IQ
  -> LS-STFT / frozen preprocessing
  -> enhanced YOLOv26n detector / CPN equivalent
  -> AHLP
  -> Combined FRN V3
  -> canonical physical DetectionResult
```

Principles:

- The legacy implementation may serve as behavior reference, checkpoint source, config source, and parity reference.
- The formal M9.1 runtime code must live in the platform repo.

Forbidden:

```text
sys.path.append("/root/autodl-tmp/Claude")
import legacy Claude package
```

Forbidden at runtime:

```text
/root/autodl-tmp/Claude/*.py
/root/autodl-tmp/ZoomSpec/*.py
```

The M9.1 implementation must extract the minimal necessary inference implementation from legacy code into the platform repo. It must not copy the entire legacy project.

The frozen inference adapter receives only raw IQ and non-label signal metadata; it cannot access or use GroundTruth.

## 13. Pipeline Asset Manifest

Checkpoints and config do not enter Git, but an immutable Pipeline Asset Manifest is required.

Git records:

```text
pipeline_id
pipeline_version

assets:
  detector_checkpoint: sha256
  frn_checkpoint: sha256
  frozen_config: sha256
```

Any additional static files that algorithm behavior depends on must also be part of the manifest identity.

`asset_manifest_sha256` is the hash of the manifest record itself and is part of the validation certificate identity.

The server profile only maps:

```text
logical asset -> absolute path
```

At each batch start the remote worker:

- resolves asset paths
- verifies SHA256
- verifies required remote runtime commit
- loads models

Any asset hash mismatch:

```text
PIPELINE_ASSET_MISMATCH
```

Immediate failure. No warn-and-continue.

Platform implementation commit mismatch:

```text
REMOTE_IMPLEMENTATION_MISMATCH
```

Execution is forbidden.

Models are hashed/loaded exactly once per batch worker start.

## 14. Batch Transport

Remote scheduling supports N-item batches. The same code path handles:

- Smoke: N = 1
- Parity cohort: N = 32-64
- Full test: N = 2500

The remote worker:

- verifies assets once
- initializes CUDA once
- loads detector once
- loads FRN once

Then for each item:

- resolve recording
- verify fingerprint and source data hash
- inference
- write per-item result
- update per-item status

Item-level status is independent. A single Recording failure marks that `AnalysisRun` `failed`; other items continue by default.

Infrastructure-level errors terminate the whole batch, e.g.:

- checkpoint load failure
- asset mismatch
- CUDA runtime unrecoverable
- process crash
- global config invalid

A batch is transport / provenance only. The database never gains a `BatchRun`.

## 15. UI Scope

No new top-level page.

The existing Spectrum Analysis page is reused:

```text
Pipeline selector
Run Analysis
AnalysisRun polling
Spectrogram
Detection overlays
```

M9.1 adds:

- executor selector / executor availability, e.g.:
  - Pipeline: `ZoomSpec YOLO26n + AHLP + Combined FRN V3`
  - Executor: `AutoDL GPU`
  - `[Run Analysis]`

### 15.1 Executor availability contract

The frontend does **not** infer remote executability itself. The backend owns executor availability.

For the selected Recording + Pipeline, the backend provides an availability read model such as:

```text
executor
available
reason_code
reason_message
remote_profile
recommended
```

Static pipeline capability and dynamic deployment availability are distinct:

- pipeline does not support `remote_gpu` -> unavailable;
- AutoDL profile missing/unreachable -> unavailable;
- SpaceNet `source_data_sha256` / fingerprint cannot be established -> unavailable;
- valid frozen pipeline + valid SpaceNet Recording + configured AutoDL -> `remote_gpu` available.

The UI only renders the backend decision. It must not simply replace `cpuSupported` with another frontend boolean guess.

Single-Recording user workflow:

```text
Recording
  -> Spectrum Analysis
  -> choose Pipeline
  -> choose AutoDL GPU
  -> Run Analysis
  -> pending/running
  -> completed
  -> detections overlay
```

Displayed provenance:

```text
executor
GPU
CUDA
PyTorch
remote runtime commit
asset identity
implementation validation state
```

M9.1 does not build a complex batch management UI. The 32-64 and 2500 parity cohorts are launched by the backend/CLI operator workflow. Results are viewed and compared in:

```text
Algorithm Lab -> Dataset Benchmarks
```

## 16. Live Implementation Validation State

Algorithm version and implementation validation are two distinct concepts. No `1.0.1-live` version is created.

### 16.1 LiveImplementationValidationCertificateV1

`live_candidate` / `live_validated` are not an arbitrary mutable boolean. They are derived from an immutable validation certificate.

`LiveImplementationValidationCertificateV1` is created only after Gate 3 acceptance. Its identity includes at least:

```text
pipeline_id
pipeline_version
remote_runtime_commit
asset_manifest_sha256
parity_protocol_id
parity_protocol_config_sha256
historical DatasetEvaluation id
live DatasetEvaluation id
dataset manifest hash
coverage
reference metrics
live metrics
parity conclusion
accepted_at
```

UI/backend reports `live_validated` **only** when a run's exact tuple:

```text
pipeline_id
pipeline_version
remote_runtime_commit
asset_manifest_sha256
```

matches an accepted validation certificate. Otherwise the run is `live_candidate`.

A later different runtime commit or changed asset manifest automatically returns the run to `live_candidate` until revalidated.

Pipeline version still remains `1.0.0`. No `1.0.1-live` version is created.

The certificate is an immutable research/validation artifact. It does not require a new RemoteJob/BatchRun domain table.

Before Gate 3:

```text
pipeline:                 zoomspec_yolo26n_aug_combined_frn_v3 / 1.0.0
implementation_validation: live_candidate
```

After Gate 3 passes:

```text
implementation_validation: live_validated
```

Validation state never changes `pipeline_version`.

## 17. M9.1 Live Parity Gates

### 17.1 Parity protocol is frozen before Gate 2

A versioned parity protocol `live_remote_parity_v1` and its config must be frozen **before** Gate 2 is first executed. The config defines deterministic comparison fields and fixed tolerances.

Recommended automatic Gate 2 criteria:

- prediction count exact;
- same class_id;
- deterministic one-to-one matching;
- physical TF IoU >= `0.9999`;
- confidence absolute delta <= `1e-5`;
- zero unmatched predictions.

If automatic criteria fail, status is `PARITY_REVIEW_REQUIRED` / not automatically PASS.

Thresholds must not be loosened after seeing results. Any accepted explained difference must be documented explicitly in the parity audit.

For Gate 3, the comparison configuration is frozen before execution. At minimum compare:

```text
prediction count
coverage
localization AP50
localization AP50:95
class-aware mAP50
class-aware mAP50:95
matched accuracy
per-class AP
confusions
```

Recommended automatic aggregate scalar tolerance:

```text
abs_delta <= 1e-6
```

Prediction count and coverage remain exact unless an explicit reviewed explanation exists. `PARITY DIFFERENCE UNEXPLAINED` cannot pass.

The parity protocol config hash is recorded in the validation certificate.

### Gate 1 — Single Recording True Live Smoke

Use `SpaceNet/test` sample 0.

Must genuinely execute:

```text
local AnalysisRun(remote_gpu)
  -> SSH submit
  -> AutoDL detached worker
  -> GPU inference
  -> result package
  -> download
  -> validate
  -> ingest
  -> SAME AnalysisRun completed
  -> Spectrum Analysis shows detections
```

Historical imported detections must never be used to fake live inference.

### Gate 2 — Fixed Representative Cohort

Fix 32-64 `SpaceNet/test` Recordings covering:

- all 14 classes
- different bandwidths
- different signal counts / overlap / scene difficulty

The cohort is fixed and versioned, never randomly sampled per run.

For each Recording compare:

```text
historical frozen run  vs  live remote_gpu run
```

Audit:

```text
prediction count
class_id
physical bbox
confidence
scores (if applicable)
```

Semantic parity is strict, per the frozen `live_remote_parity_v1` criteria. Gate 2 failure blocks the full 2500.

### Gate 3 — Full SpaceNet Test 2500

Re-run live inference over 2500 recordings, producing 2500 ordinary `remote_gpu` `AnalysisRun` rows.

Then use the existing:

```text
DatasetEvaluation
physical_tf_detection_ap_v2
```

compared against the historical imported benchmark.

Historical reference:

```text
predictions: 33373
class-aware mAP50: 0.49706861157413673
class-aware mAP50:95: 0.3732512758737991
localization AP50: 0.8666496180384053
localization AP50:95: 0.6715938311249926
matched classification accuracy: 0.8151265187827444
```

Compare (per the frozen config):

- prediction count
- coverage
- Localization AP50
- Localization AP50:95
- class-aware mAP50
- class-aware mAP50:95
- matched classification accuracy
- per-class AP
- confusions
- item-level parity / mismatches

`PARITY DIFFERENCE UNEXPLAINED` cannot pass. Only after Gate 3 may `implementation_validation = live_validated`.

## 18. Error Contract

Define at least these explicit error codes:

```text
REMOTE_EXECUTOR_UNAVAILABLE
REMOTE_RECORDING_UNAVAILABLE
REMOTE_SUBMIT_FAILED
REMOTE_REQUEST_CONFLICT
REMOTE_STATUS_UNAVAILABLE
REMOTE_DOWNLOAD_FAILED
REMOTE_RESULT_INVALID
REMOTE_RESULT_CONFLICT
REMOTE_JOB_INTERRUPTED
RECORDING_FINGERPRINT_MISMATCH
SOURCE_DATA_HASH_MISMATCH
REMOTE_IMPLEMENTATION_MISMATCH
PIPELINE_ASSET_MISMATCH
PIPELINE_EXECUTION_FAILED
```

Errors are structured and preserve backend code/message/details. Vague messages such as `Analysis failed` are forbidden.

## 19. Security

SSH keys, passwords, tokens, and secrets must never enter:

- Git
- SQLite
- AnalysisRun JSON
- request packages
- logs

### 19.1 SSH trust / command safety

SSH must use server host-key verification. Required:

```text
known_hosts / pinned trusted host key
```

Forbidden:

```text
StrictHostKeyChecking=no
blind host-key acceptance
```

The remote executor may invoke only a fixed platform-owned remote runner entrypoint. Client/request data must never become an arbitrary shell command. Identifiers passed into remote commands must be strict validated IDs. The required remote commit must be validated as an exact commit identifier.

The production `RemoteGpuJobManager` must **not**:

```text
git pull
git checkout arbitrary client-supplied refs
git reset
mutate the remote repository automatically
```

The AutoDL runtime/worktree is deployed separately. Remote execution only verifies that the deployed remote runtime is the required commit.

Clients cannot submit arbitrary shell commands. Clients cannot submit arbitrary remote absolute paths. `dataset_key` must go through the strict resolver. `request_id` / `batch_id` are system-generated and format-validated. Remote job paths are constructed from a safe fixed root plus validated id.

The result package must be:

- size bounded
- safely extracted
- no path traversal
- no symlink / special files
- strict JSON
- hash verified

Reuse existing imported-package safety primitives; do not copy-paste a second inconsistent implementation.

## 20. Operator Batch Workflow

M9.1 provides a controlled backend/CLI operator workflow for:

- 1 recording
- fixed cohort
- full 2500

It:

- selects exact dataset manifest membership
- creates per-recording `remote_gpu` AnalysisRuns
- submits them as one remote batch
- monitors / reconciles
- can generate/select the exact AnalysisRuns for a `DatasetEvaluation`

It must avoid pipeline id/version ambiguous resolution. The 2500 gate freezes exact run membership so historical imported runs and new live runs are never confused.

## 21. Testing Strategy

The future implementation plan must test in layers:

1. contract/unit tests
2. executor compatibility tests
3. recording resolver tests
4. fingerprint mismatch tests
5. source data hash tests
6. GroundTruth isolation tests (predictions independent of GT)
7. asset manifest/hash tests
8. fake SSH transport tests
9. result ingest transaction tests (idempotency, immutability)
10. malformed/hostile result package tests
11. restart/reconciliation tests
12. batch partial failure tests
13. remote runner CPU/mock tests
14. server GPU Gate 1
15. Gate 2 cohort
16. Gate 3 full 2500
17. actual browser smoke

Existing regression must continue to pass:

```text
local_cpu
imported runs
Case Analysis
Dataset Benchmarks
M8.5
M8.6
physical_tf_detection_ap_v2
```

## 22. Local / Server Responsibilities

Local 电脑opencode:

- AnalysisRun schema/migration (additive `execution_metadata_json`, `Recording.source_data_sha256`)
- executor abstraction
- RemoteGpuJobManager
- SSH/SCP transport
- supervisor/reconciler
- RemoteResultIngestor
- common result writer
- backend API
- Spectrum Analysis executor UI
- operator batch orchestration
- local/fake integration tests

Server opencode:

- remote runner
- SpaceNet remote resolver
- raw-IQ source data hashing and verification
- asset verification
- minimal platform-native frozen pipeline implementation
- GPU runtime
- pipeline behavioral parity
- Gate 1 / 2 / 3 server evidence

Shared protocol lives in the platform repo:

```text
RemoteExecutionRequestV1
RemoteExecutionBatchV1
RemoteExecutionEnvelopeV1
Pipeline Asset Manifest
LiveImplementationValidationCertificateV1
live_remote_parity_v1
```

## 23. Implementation Sequencing Constraints

This design recommends the future implementation plan split into independently gated stages:

- M9.1-A: Protocol + executor foundation
- M9.1-B: Remote runner + resolver + asset verification
- M9.1-C: Platform-native frozen pipeline adapter + Gate 1
- M9.1-D: Recovery + batch transport + Gate 2
- M9.1-E: Full 2500 Gate 3 + live validation + final UI/browser acceptance

This is a DESIGN SPEC only. No implementation plan file is created in this task.

## 24. Non-Negotiable Invariants

1. A remote result always writes back to the same AnalysisRun that requested it.
2. `remote_gpu` never creates an `executor=imported` run.
3. Pipeline output domain coordinates remain physical seconds + absolute Hz.
4. No arbitrary remote paths are accepted from clients.
5. SpaceNet remote execution requires fingerprint equality.
6. Pipeline 1.0.0 assets are hash-locked.
7. Asset mismatch or platform implementation mismatch is fatal.
8. Pipeline version is algorithm behavior identity, not executor identity.
9. A remote batch is transport/provenance, not a new domain entity.
10. `AnalysisRun` completed means the result is already validated and committed locally.
11. Platform restart must not automatically kill or invalidate a still-running remote job.
12. Gate 3 full-2500 parity is required before `live_validated`.
13. No implementation may depend on importing/running legacy Claude/ZoomSpec source directories at runtime.
14. Secrets never enter Git, DB, request contracts, or logs.
15. M9.1 does not introduce new training/tuning behavior.
16. `recording_fingerprint_v1` is semantic identity, not raw-IQ identity; its M8.6B/M8.6C semantics are unchanged.
17. Remote SpaceNet execution requires `source_data_sha256` equality in addition to `recording_fingerprint_v1`.
18. GroundTruth never enters model inference; it is used by the resolver only for identity/fingerprint validation.
19. Remote submission is idempotent by `batch_id` + `request_sha256`.
20. Remote result ingestion is idempotent by `payload_sha256`.
21. A completed AnalysisRun and its DetectionResults are immutable.
22. SSH host identity is verified (known_hosts / pinned host key); blind host-key acceptance is forbidden.
23. The remote executor never mutates/checks out the remote repository state automatically.
24. `live_validated` is derived from an exact validation certificate tuple; otherwise the run is `live_candidate`.
25. The parity protocol is frozen before Gate 2; tolerances are never loosened after seeing results.

## 25. Design Alternatives / Rejected Approaches

- Arbitrary local IQ upload in M9.1 — rejected: data-transfer subsystem is too large for the first remote executor slice.
- Remote HTTP inference service — rejected for V1: daemon/auth/network/API lifecycle overhead.
- Direct legacy script execution — rejected: fragile runtime dependency and not platform-native.
- Docker — deferred: unnecessary complexity for M9.1.
- Per-recording SSH process/model load — rejected: 2500x startup/model-load overhead.
- RemoteJob/BatchRun ORM — rejected: duplicates AnalysisRun/DatasetEvaluation semantics.
- Direct server SQLite access — rejected: breaks local DB ownership.
- stdout-only result transfer — rejected: poor integrity/recovery/artifact semantics.