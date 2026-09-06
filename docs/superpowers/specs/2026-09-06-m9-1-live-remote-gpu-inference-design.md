# M9.1 Live Frozen-Pipeline Remote GPU Inference — Design

Date: 2026-09-06
Status: Formal design (product/architecture owner confirmed)
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
- remote_profile
- required_platform_commit
- remote_platform_commit
- asset identities
- submission provenance
- remote job identity
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

## 6. Remote Profile

Server deployment information comes from local secure configuration / environment, not from Git.

Logical profile name: `autodl_primary`.

The profile may contain:

- host
- port
- user
- ssh key reference
- remote repo root
- remote job root
- dataset roots (e.g., `SpaceNet -> server dataset root`)
- pipeline assets (logical asset -> absolute path)

Secrets and deployment absolute paths must never enter Git-tracked pipeline definitions.

## 7. Recording Resolution

M9.1-A (first slice) supports only Recordings whose raw IQ already exists on the server. The first dataset is `SpaceNet`.

A remote request must not carry `/root/.../0.bin` or any arbitrary remote absolute path. It carries logical identity only:

```text
dataset_name
dataset_split
dataset_key
label_space
expected_recording_fingerprint
```

For the current SpaceNet case:

- dataset_name = `SpaceNet`
- dataset_split = `test` / `train`
- dataset_key = `Recording.name` / sample stem
- label_space = `spacenet_14`

Server-side resolution:

```text
dataset-specific resolver
  -> SpaceNetAdapter
  -> split + sample key
  -> resolve .bin + .json
  -> parse metadata
  -> recompute recording_fingerprint_v1
  -> must equal expected fingerprint exactly
  -> only then run inference
```

Custom / local-only Recordings return:

```text
REMOTE_RECORDING_UNAVAILABLE
```

No path guessing and no silent upload.

## 8. Remote Execution Request V1

Define a strict versioned wire contract.

`RemoteExecutionRequestV1` contains at least:

```text
schema_version
request_id
required_platform_commit

pipeline:
  id
  version

recording:
  dataset_name
  dataset_split
  dataset_key
  label_space
  expected_recording_fingerprint

parameters

asset_manifest_identity
```

Batch transport definition:

```text
RemoteExecutionBatchV1
  schema_version
  batch_id
  required_platform_commit
  pipeline identity
  N items
```

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

## 11. Result Contract

Remote results use:

```text
RemoteExecutionEnvelopeV1
+ Analysis Package v1 compatible result semantics
```

The envelope contains at least:

```text
schema_version
request_id
batch_id
item_key
local_run_id
recording_fingerprint
pipeline_id
pipeline_version
platform_commit
asset identities
hardware/runtime provenance
result sha256
remote timestamps
```

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
- pipeline id/version
- platform commit
- asset identity
- result hash
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

The server profile only maps:

```text
logical asset -> absolute path
```

At each batch start the remote worker:

- resolves asset paths
- verifies SHA256
- verifies required platform commit
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
- verify fingerprint
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

The frontend must not rely on `cpuSupported` alone. It needs capability-aware executor availability.

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
platform commit
asset identity
implementation validation state
```

M9.1 does not build a complex batch management UI. The 32-64 and 2500 parity cohorts are launched by the backend/CLI operator workflow. Results are viewed and compared in:

```text
Algorithm Lab -> Dataset Benchmarks
```

## 16. Live Implementation Validation State

Algorithm version and implementation validation are two distinct concepts. No `1.0.1-live` version is created.

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

JSON byte-identity is not required (CUDA / FP implementation may differ at machine level), but semantic parity is strict:

- class identity exact
- physical detection one-to-one matched
- tiny numeric tolerance allowed
- unexplained unmatched prediction = blocker

Gate 2 failure blocks the full 2500.

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

Compare:

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
REMOTE_STATUS_UNAVAILABLE
REMOTE_DOWNLOAD_FAILED
REMOTE_RESULT_INVALID
REMOTE_JOB_INTERRUPTED
RECORDING_FINGERPRINT_MISMATCH
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
5. asset manifest/hash tests
6. fake SSH transport tests
7. result ingest transaction tests
8. malformed/hostile result package tests
9. restart/reconciliation tests
10. batch partial failure tests
11. remote runner CPU/mock tests
12. server GPU Gate 1
13. Gate 2 cohort
14. Gate 3 full 2500
15. actual browser smoke

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

- AnalysisRun schema/migration
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

## 25. Design Alternatives / Rejected Approaches

- Arbitrary local IQ upload in M9.1 — rejected: data-transfer subsystem is too large for the first remote executor slice.
- Remote HTTP inference service — rejected for V1: daemon/auth/network/API lifecycle overhead.
- Direct legacy script execution — rejected: fragile runtime dependency and not platform-native.
- Docker — deferred: unnecessary complexity for M9.1.
- Per-recording SSH process/model load — rejected: 2500x startup/model-load overhead.
- RemoteJob/BatchRun ORM — rejected: duplicates AnalysisRun/DatasetEvaluation semantics.
- Direct server SQLite access — rejected: breaks local DB ownership.
- stdout-only result transfer — rejected: poor integrity/recovery/artifact semantics.