# M9.1 Live Frozen-Pipeline Remote GPU Inference Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the local Windows platform create a genuine `AnalysisRun(executor=remote_gpu)`, drive the frozen `zoomspec_yolo26n_aug_combined_frn_v3` pipeline on AutoDL over SSH, run real GPU inference, and safely write the standard result back to the **same** AnalysisRun. The platform capability moves from "historical result import + analysis" to "platform directly executes the real GPU pipeline + analysis".

**Architecture:** Keep the core `Recording -> AnalysisRun -> DetectionResult` domain model unchanged. Extend the executor abstraction from `local_cpu` to `remote_gpu`. Add a new `backend/app/remote_execution/` package for transport, remote job management, result ingestion, supervision, reconciliation, recording resolution, asset verification, operator orchestration, and parity. Add a platform-native frozen pipeline package `backend/app/pipelines/zoomspec_yolo26n_aug_combined_frn_v3/` that contains only the minimal inference implementation ported from legacy reference code. SSH is transport only; the remote worker is a detached process. Remote batch is transport/provenance, never a new domain entity. A remote result writes to the same AnalysisRun that requested it; completed runs and their DetectionResults are immutable.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2.x, Pydantic, OpenSSH/SCP/SFTP subprocess transport (no `shell=True`), NumPy, pytest, React + TypeScript + Ant Design + Vitest.

**Spec:** `docs/superpowers/specs/2026-09-06-m9-1-live-remote-gpu-inference-design.md`

## Global Constraints

- Base is `feature/v1-core @ 9a6f0feac0b0e6e2ac8ecd65d2e4383479e09f7c`; work on `feature/m9-1-live-remote-gpu-inference`; never implement directly on `feature/v1-core` or `main`.
- A remote result always writes back to the same `AnalysisRun` that requested it.
- `remote_gpu` never creates an `executor=imported` run; it never calls `ImportedRunService`.
- No `RemoteJob`, `BatchRun`, `RemoteAnalysisRun`, `GpuRun`, or `ExperimentRun` ORM model; a remote batch is transport/provenance only.
- Pipeline output domain coordinates remain physical seconds + absolute Hz; pixels are never a domain output.
- `recording_fingerprint_v1` semantics MUST NOT change (M8.6B/M8.6C depend on it).
- Add an independent additive `Recording.source_data_sha256` for exact raw IQ byte identity.
- SpaceNet remote execution requires BOTH `recording_fingerprint_v1` equality AND `source_data_sha256` equality.
- GroundTruth / signals / class labels are used by the resolver only for identity validation and never reach model inference.
- Remote submission is idempotent by `batch_id` + `request_sha256`; `request_sha256` is computed from a canonical request payload that excludes `request_sha256` itself.
- A terminal remote item result `(batch_id, item_key)` is immutable/write-once; reconciliation never regenerates or overwrites it. If a terminal item exists but its result artifact is missing/corrupted, the runner reports `REMOTE_RESULT_CORRUPTED` and never regenerates it; human audit or a new AnalysisRun/batch re-executes.
- A completed local `AnalysisRun` and its DetectionResults are immutable.
- `payload_sha256` hashes the exact `analysis_result.zip` bytes; the envelope is excluded from that hash (no self-reference).
- SSH host-key verification is mandatory; `StrictHostKeyChecking=no`, `shell=True`, arbitrary user shell commands, and arbitrary remote absolute paths are forbidden.
- The production executor never runs `git pull`, `git checkout`, `git reset`, or otherwise mutates the remote repository; the remote runtime only verifies `HEAD == required_remote_runtime_commit`.
- Frozen checkpoints/config stay outside Git but are SHA256-locked in the Pipeline Asset Manifest.
- No formal runtime import/execute dependency on `/root/autodl-tmp/Claude/*.py` or `/root/autodl-tmp/ZoomSpec/*.py`.
- First live pipeline is only `zoomspec_yolo26n_aug_combined_frn_v3` version `1.0.0`; no `1.0.1-live`.
- `live_validated` is derived from the immutable `LiveImplementationValidationCertificateV1` tuple, not a mutable boolean.
- No Docker / Redis / Celery / remote HTTP inference daemon.
- No arbitrary local IQ upload in M9.1.
- Existing `local_cpu` / imported / benchmark behavior remains compatible; all existing regression must stay green (`local_cpu`, imported runs, Case Analysis, Dataset Benchmarks, M8.5, M8.6, `physical_tf_detection_ap_v2`).
- `analysis_run.status` remains `pending|running|completed|failed|interrupted`; `remote_gpu` `running` is reconcilable on restart.
- `parameters_json` holds algorithm parameters only; SSH/batch/host provenance lives in additive `execution_metadata_json`.
- Use TDD for every behavior change: failing test -> verify RED -> minimal implementation -> verify GREEN -> regression -> code review -> commit -> handoff push when required.
- The remote worker initializes CUDA/models/assets once per batch; item-level status is independent; only infrastructure errors terminate the whole batch.
- Parity protocol `live_remote_parity_v1` and cohort `spacenet_test_cohort_v1.json` are frozen before Gate 2; tolerances are never loosened after seeing results.
- Historical imported detections must never be used to fake live execution.

---

## File Structure

**Create (backend/app/remote_execution/):**

```text
backend/app/remote_execution/
  __init__.py
  schema.py              # RemoteExecutionRequestV1 / BatchV1 / EnvelopeV1 / status schemas
  canonical.py           # canonical_request_payload(), compute_request_sha256(), canonical_result helpers
  source_hash.py         # exact raw-IQ SHA256 computation + Recording cache helper
  profile.py             # RemoteProfile loader (autodl_primary) from env/secure config
  transport.py           # OpenSSH/SCP/SFTP command runner (fixed argv, host-key verified)
  job_manager.py         # RemoteGpuJobManager: submit/status/download
  executor.py            # RemoteGpuExecutor: remote executability + request construction
  result_ingestor.py     # RemoteResultIngestor: envelope/ZIP validation + idempotent ingest
  supervisor.py          # RemoteRunSupervisor: background poll + ingest trigger
  runner.py              # server-side remote runner CLI (probe/submit/status/work)
  resolver.py            # SpaceNet remote resolver (dataset/split/key, sanitized input)
  assets.py              # Pipeline Asset Manifest + fail-closed verification
  operator.py            # exact-membership operator batch service
  cli.py                 # local operator batch CLI
  parity.py              # live_remote_parity_v1 + certificate helpers
  validation.py          # shared result-writer validation / AnalysisResultWriter
  parity/live_remote_parity_v1.json
  parity/spacenet_test_cohort_v1.json
```

**Create (backend/app/pipelines/zoomspec_yolo26n_aug_combined_frn_v3/):**

```text
backend/app/pipelines/zoomspec_yolo26n_aug_combined_frn_v3/
  __init__.py
  definition.py          # PipelineDefinition (id/version/label_space/etc.)
  preprocessing.py       # LS-STFT / frozen preprocessing (NumPy/CPU-safe core)
  detector.py            # enhanced YOLOv26n / CPN-equivalent forward
  ahlp.py                # AHLP
  frn.py                 # Combined FRN V3
  pipeline.py            # Pipeline.run(recording, parameters, workspace) -> PipelineOutput
  asset_manifest.json    # pipeline_id/version + asset sha256 identities (recorded after hashing)
```

**Modify (backend):**

```text
backend/app/recordings/model.py        # + source_data_sha256
backend/app/recordings/schema.py       # + source_data_sha256 read
backend/app/analysis/model.py          # + execution_metadata_json
backend/app/analysis/schema.py         # + execution_metadata_json read, executor availability read model
backend/app/analysis/service.py        # executor-aware create_run + startup reconciliation split
backend/app/analysis/worker.py         # extract AnalysisResultWriter (no unconditional delete for completed)
backend/app/analysis/router.py         # executor availability endpoint + run start path
backend/app/pipelines/base.py          # executor capability fields on PipelineDefinition
backend/app/pipelines/registry.py      # register zoomspec pipeline; executor-aware helpers
backend/app/core/config.py             # remote profile/env settings (no secrets committed)
backend/app/db/migrations.py           # additive source_data_sha256 + execution_metadata_json
backend/app/main.py                    # wire RemoteRunSupervisor/Reconciler/executor registry
```

**Tests (backend):**

```text
backend/tests/test_remote_execution_schema.py
backend/tests/test_remote_execution_canonical.py
backend/tests/test_remote_source_hash.py
backend/tests/test_remote_executor_availability.py
backend/tests/test_analysis_result_writer.py
backend/tests/test_remote_result_ingestor.py
backend/tests/test_remote_transport.py
backend/tests/test_remote_runner.py
backend/tests/test_remote_resolver.py
backend/tests/test_remote_assets.py
backend/tests/test_remote_supervisor.py
backend/tests/test_remote_operator.py
backend/tests/test_remote_parity.py
backend/tests/test_remote_validation.py
backend/tests/test_zoomspec_pipeline_cpu.py
backend/tests/test_restart_reconciliation.py
```

**Modify/Test (frontend):**

```text
frontend/src/api/types.ts
frontend/src/api/client.ts
frontend/src/pages/SpectrumAnalysisPage.tsx
frontend/src/pages/SpectrumAnalysisPage.test.tsx
```

---

## M9.1-A — Protocol and Executor Foundation

### Task 1: Add additive DB provenance fields

**Owner:** 本地电脑opencode

**Files:**
- Modify: `backend/app/recordings/model.py`
- Modify: `backend/app/recordings/schema.py`
- Modify: `backend/app/analysis/model.py`
- Modify: `backend/app/analysis/schema.py`
- Modify: `backend/app/db/migrations.py`
- Create: `backend/tests/test_m9_1_provenance_migrations.py`

**Interfaces:**
- `RecordingModel.source_data_sha256: Mapped[str | None]` (String(64), nullable)
- `AnalysisRunModel.execution_metadata_json: Mapped[dict | None]` (JSON, nullable)
- `run_additive_migrations()` adds `upgrade_m9_1_provenance(engine)` that ALTERs both tables additively.

- [ ] **Step 1: Write failing migration/read tests**

Create `backend/tests/test_m9_1_provenance_migrations.py`:

```python
from sqlalchemy import inspect, select

from app.analysis.model import AnalysisRunModel
from app.db.migrations import run_additive_migrations
from app.recordings.model import RecordingModel


def test_columns_exist_on_fresh_db(session):
    engine = session.get_bind()
    recordings = {c["name"] for c in inspect(engine).get_columns("recordings")}
    analysis_runs = {c["name"] for c in inspect(engine).get_columns("analysis_runs")}
    assert "source_data_sha256" in recordings
    assert "execution_metadata_json" in analysis_runs


def test_old_db_gets_columns_after_additive_migration(tmp_path):
    from app.core.config import Settings
    from app.db.base import Base, load_domain_models
    from app.db.session import Database

    settings = Settings(project_root=tmp_path, data_root=tmp_path / "data",
                        label_space_root=tmp_path / "label_spaces",
                        database_url=f"sqlite:///{tmp_path / 'old.db'}")
    load_domain_models()
    db = Database(settings.database_url)
    # Simulate an old DB that only had the pre-M9.1 schema.
    Base.metadata.create_all(db.engine)
    with db.engine.begin() as connection:
        connection.execute("DROP TABLE recordings")
        connection.execute("DROP TABLE analysis_runs")
    # Recreate minimal old tables without the new columns.
    with db.engine.begin() as connection:
        connection.execute("CREATE TABLE recordings (id VARCHAR(64) PRIMARY KEY, name VARCHAR(255) NOT NULL)")
        connection.execute("CREATE TABLE analysis_runs (id VARCHAR(64) PRIMARY KEY, status VARCHAR(32) NOT NULL DEFAULT 'pending')")
    run_additive_migrations(db.engine)
    with db.engine.begin() as connection:
        recordings = {c["name"] for c in inspect(connection).get_columns("recordings")}
        analysis_runs = {c["name"] for c in inspect(connection).get_columns("analysis_runs")}
    assert "source_data_sha256" in recordings
    assert "execution_metadata_json" in analysis_runs
```

- [ ] **Step 2: Run and confirm RED**

```bash
pytest backend/tests/test_m9_1_provenance_migrations.py -v
```

Expected RED: import/column assertion failures because the columns do not exist.

- [ ] **Step 3: Minimal implementation**

In `backend/app/recordings/model.py`, add the field:

```python
source_data_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
```

In `backend/app/analysis/model.py`, add:

```python
execution_metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
```

In `backend/app/recordings/schema.py` and `backend/app/analysis/schema.py` add the corresponding read fields to `RecordingRead` / `AnalysisRunRead`.

In `backend/app/db/migrations.py`:

```python
def upgrade_m9_1_provenance(engine) -> None:
    with engine.begin() as connection:
        recordings = {c["name"] for c in inspect(connection).get_columns("recordings")}
        if "source_data_sha256" not in recordings:
            connection.execute(text("ALTER TABLE recordings ADD COLUMN source_data_sha256 VARCHAR(64)"))
        analysis_runs = {c["name"] for c in inspect(connection).get_columns("analysis_runs")}
        if "execution_metadata_json" not in analysis_runs:
            connection.execute(text("ALTER TABLE analysis_runs ADD COLUMN execution_metadata_json JSON"))
```

and add `upgrade_m9_1_provenance(engine)` to `run_additive_migrations`.

- [ ] **Step 4: Run GREEN**

```bash
pytest backend/tests/test_m9_1_provenance_migrations.py backend/tests/test_analysis_runs.py backend/tests/test_recordings.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/recordings/model.py backend/app/recordings/schema.py backend/app/analysis/model.py backend/app/analysis/schema.py backend/app/db/migrations.py backend/tests/test_m9_1_provenance_migrations.py
git commit -m "feat: add remote execution provenance fields"
```

### Task 2: Strict remote execution V1 schemas and canonical hashing

**Owner:** 本地电脑opencode

**Files:**
- Create: `backend/app/remote_execution/schema.py`
- Create: `backend/app/remote_execution/canonical.py`
- Create: `backend/tests/test_remote_execution_schema.py`
- Create: `backend/tests/test_remote_execution_canonical.py`

**Interfaces:**
- `RemoteRecordingRefV1` — `dataset_name`, `dataset_split`, `dataset_key`, `label_space`, `expected_recording_fingerprint`, `expected_source_data_sha256`
- `RemoteExecutionItemV1` — `item_key`, `local_run_id`, `recording: RemoteRecordingRefV1`, `parameters: dict`
- `RemoteExecutionBatchV1` — `schema_version`, `batch_id`, `required_remote_runtime_commit`, `pipeline`, `items`, `request_sha256`
- `RemoteExecutionRequestV1` — per-item request carrying `orchestrator_commit`; `execution_metadata_json` records both `orchestrator_commit` (local control plane) and `remote_runtime_commit` (executed platform runtime)
- `RemoteItemStatusV1` — `item_key`, `status` (`queued|running|completed|failed|interrupted`), `error_code`, `error_message`, `result_relative_path`
- `RemoteBatchStatusV1` — `batch_id`, `status`, `items: list[RemoteItemStatusV1]`
- `RemoteExecutionEnvelopeV1` — `schema_version`, `request_id`, `batch_id`, `item_key`, `local_run_id`, `recording_fingerprint`, `source_data_sha256`, `pipeline_id`, `pipeline_version`, `orchestrator_commit`, `remote_runtime_commit`, `asset_manifest_sha256`, `hardware`, `payload_sha256`, `remote_started_at`, `remote_finished_at`
- `canonical_request_payload(batch: RemoteExecutionBatchV1) -> dict` — all semantic fields excluding `request_sha256`
- `canonical_request_bytes(payload: dict) -> bytes`
- `compute_request_sha256(batch: RemoteExecutionBatchV1) -> str`

Reuse `app.benchmarks.manifest.canonical_number` and `app.benchmarks.manifest.canonical_json_bytes` where practical; `canonical_json_bytes` already sorts keys, uses compact separators, UTF-8, and `ensure_ascii=False`.

- [ ] **Step 1: Write failing canonical tests**

Create `backend/tests/test_remote_execution_canonical.py`:

```python
from app.remote_execution.canonical import canonical_request_payload, compute_request_sha256
from app.remote_execution.schema import RemoteExecutionBatchV1, RemoteExecutionItemV1, RemoteRecordingRefV1


def _batch(request_sha256="a" * 64):
    return RemoteExecutionBatchV1(
        schema_version=1,
        batch_id="batch_x",
        required_remote_runtime_commit="9a6f0feac0b0e6e2ac8ecd65d2e4383479e09f7c",
        pipeline={"id": "zoomspec_yolo26n_aug_combined_frn_v3", "version": "1.0.0"},
        items=[
            RemoteExecutionItemV1(
                item_key="000000",
                local_run_id="run_a",
                recording=RemoteRecordingRefV1(
                    dataset_name="SpaceNet", dataset_split="test", dataset_key="0",
                    label_space="spacenet_14",
                    expected_recording_fingerprint="b" * 64,
                    expected_source_data_sha256="c" * 64,
                ),
                parameters={},
            )
        ],
        request_sha256=request_sha256,
    )


def test_request_sha256_excludes_itself():
    canonical = canonical_request_payload(_batch())
    assert "request_sha256" not in canonical


def test_request_sha256_is_deterministic_and_field_order_independent():
    first = compute_request_sha256(_batch())
    second = compute_request_sha256(_batch())
    assert first == second
    assert len(first) == 64


def test_request_sha256_changes_when_semantic_field_changes():
    base = compute_request_sha256(_batch())
    changed = _batch()
    changed.batch_id = "batch_y"
    assert compute_request_sha256(changed) != base
```

- [ ] **Step 2: Run and confirm RED**

```bash
pytest backend/tests/test_remote_execution_canonical.py backend/tests/test_remote_execution_schema.py -q
```

Expected RED: missing module `app.remote_execution`.

- [ ] **Step 3: Minimal implementation**

Define the Pydantic models in `schema.py` using `model_config = ConfigDict(extra="forbid")` and strict `Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]`. In `canonical.py`:

```python
from app.benchmarks.manifest import canonical_json_bytes
from app.remote_execution.schema import RemoteExecutionBatchV1


def canonical_request_payload(batch: RemoteExecutionBatchV1) -> dict:
    payload = batch.model_dump(exclude={"request_sha256"})
    # Numeric canonicalization is applied by canonical_json_bytes via canonical_number
    # where the schema declares float fields; the rest stays strict JSON.
    return payload


def canonical_request_bytes(payload: dict) -> bytes:
    return canonical_json_bytes(payload)


def compute_request_sha256(batch: RemoteExecutionBatchV1) -> str:
    from hashlib import sha256
    return sha256(canonical_request_bytes(canonical_request_payload(batch))).hexdigest()
```

- [ ] **Step 4: Run GREEN**

```bash
pytest backend/tests/test_remote_execution_schema.py backend/tests/test_remote_execution_canonical.py backend/tests/test_benchmark_manifest.py -q
```

Expected: PASS (M8.6A manifest hash untouched).

- [ ] **Step 5: Commit**

```bash
git add backend/app/remote_execution/schema.py backend/app/remote_execution/canonical.py backend/tests/test_remote_execution_schema.py backend/tests/test_remote_execution_canonical.py
git commit -m "feat: define remote execution v1 protocol and canonical identity"
```

### Task 3: Exact raw-IQ SHA256 service + Recording cache

**Owner:** 本地电脑opencode

**Files:**
- Create: `backend/app/remote_execution/source_hash.py`
- Create: `backend/tests/test_remote_source_hash.py`

**Interfaces:**
- `compute_file_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str`
- `resolve_source_data_sha256(session: Session, recording: RecordingModel, data_root: Path) -> str` — returns cached value if present, otherwise hashes the exact raw file bytes and persists it.

The `data_root` is an explicit constructor/argument; it is never an implicit global. `RecordingInput`/data path resolution follows `app.analysis.worker._recording_input` semantics (external_path when set, otherwise `data_root / recording.data_path`).

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_remote_source_hash.py`:

```python
import hashlib

from benchmark_fixture import add_recording

from app.remote_execution.source_hash import compute_file_sha256, resolve_source_data_sha256


def test_compute_file_sha256_matches_manual_hash(tmp_path):
    blob = b"0123456789" * 100000
    path = tmp_path / "raw.iq"
    path.write_bytes(blob)
    assert compute_file_sha256(path) == hashlib.sha256(blob).hexdigest()


def test_resolve_source_data_sha256_caches_on_recording(client, tmp_path):
    blob = b"\x00\x01" * 5000
    raw_path = tmp_path / "raw.iq"
    raw_path.write_bytes(blob)
    with client.app.state.database.session_factory() as session:
        add_recording(session, recording_id="rec_x", name="x")
        session.commit()
        from app.recordings.model import RecordingModel
        recording = session.get(RecordingModel, "rec_x")
        # point the recording's data_path at the raw file inside data_root
        data_root = tmp_path / "data"
        recording.data_path = raw_path.name
        session.commit()
        first = resolve_source_data_sha256(session, recording, data_root)
        second = resolve_source_data_sha256(session, recording, data_root)
        assert first == second == hashlib.sha256(blob).hexdigest()
```

- [ ] **Step 2: Run and confirm RED**

```bash
pytest backend/tests/test_remote_source_hash.py -q
```

Expected RED: missing `app.remote_execution.source_hash`.

- [ ] **Step 3: Minimal implementation**

`source_hash.py` implements bounded chunk reads and writes the computed hash back to `recording.source_data_sha256` in a single `session.commit()`. It uses the same path-resolution rule as `_recording_input`: `external_path` if set, else `data_root / data_path`, resolved and checked `is_file()`.

- [ ] **Step 4: Run GREEN**

```bash
pytest backend/tests/test_remote_source_hash.py backend/tests/test_imported_runs.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/remote_execution/source_hash.py backend/tests/test_remote_source_hash.py
git commit -m "feat: hash and cache raw iq source identity"
```

### Task 4: Executor-aware pipeline definition + backend-owned executor availability

**Owner:** 本地电脑opencode

**Files:**
- Modify: `backend/app/pipelines/base.py`
- Modify: `backend/app/pipelines/registry.py`
- Modify: `backend/app/analysis/schema.py`
- Modify: `backend/app/analysis/service.py`
- Create: `backend/tests/test_remote_executor_availability.py`

**Interfaces:**
- `PipelineDefinition` gains `executors_supported: tuple[str, ...] = ("local_cpu",)` and `recommended_executor: str = "local_cpu"`.
- `ExecutorAvailabilityRead` — `executor`, `available`, `reason_code`, `reason_message`, `remote_profile`, `recommended`.
- `AnalysisService.executor_availability(recording_id, pipeline_id) -> ExecutorAvailabilityRead` — the backend is the only authority; it checks static pipeline capability and dynamic deployment availability (AutoDL profile configured/reachable, and for SpaceNet `source_data_sha256`/fingerprint establishable).

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_remote_executor_availability.py`:

```python
from benchmark_fixture import add_recording

from app.analysis.schema import ExecutorAvailabilityRead
from app.analysis.service import AnalysisService
from app.pipelines.registry import create_pipeline_registry


def _service(client):
    with client.app.state.database.session_factory() as session:
        return AnalysisService(session, create_pipeline_registry(), client.app.state.job_manager)


def test_remote_gpu_unavailable_for_non_spacenet_recording(client):
    with client.app.state.database.session_factory() as session:
        add_recording(session, recording_id="rec_local", name="local", dataset_name=None, dataset_split=None, label_space=None)
        session.commit()
    svc = _service(client)
    with client.app.state.database.session_factory() as session:
        availability = svc.executor_availability("rec_local", "stft_energy_detector")
    assert availability.executor == "remote_gpu"
    assert availability.available is False
    assert availability.reason_code in {"PIPELINE_NOT_REMOTE_CAPABLE", "REMOTE_PROFILE_UNAVAILABLE", "REMOTE_RECORDING_UNAVAILABLE"}


def test_remote_gpu_available_for_frozen_pipeline_and_spacenet_when_profile_configured(client):
    with client.app.state.database.session_factory() as session:
        add_recording(session, recording_id="rec_sn", name="0", dataset_name="SpaceNet", dataset_split="test", label_space="spacenet_14")
        session.commit()
    svc = _service(client)
    with client.app.state.database.session_factory() as session:
        availability = svc.executor_availability("rec_sn", "zoomspec_yolo26n_aug_combined_frn_v3")
    assert availability.available is True
    assert availability.recommended is True
```

- [ ] **Step 2: Run and confirm RED**

```bash
pytest backend/tests/test_remote_executor_availability.py -q
```

Expected RED: `ExecutorAvailabilityRead` missing and `executor_availability` missing.

- [ ] **Step 3: Minimal implementation**

Add the fields to `PipelineDefinition`, update `registry.list()` serialization, add `ExecutorAvailabilityRead` to `analysis/schema.py`, and implement `AnalysisService.executor_availability(...)`. The availability logic consults `RemoteProfile.from_env()` (Task 6 profile) and `source_hash.resolve_source_data_sha256` for SpaceNet. The UI later renders only this backend decision.

- [ ] **Step 4: Run GREEN**

```bash
pytest backend/tests/test_remote_executor_availability.py backend/tests/test_analysis_runs.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/pipelines/base.py backend/app/pipelines/registry.py backend/app/analysis/schema.py backend/app/analysis/service.py backend/tests/test_remote_executor_availability.py
git commit -m "feat: backend owned executor availability"
```

### Task 5: Extract shared AnalysisResultWriter

**Owner:** 本地电脑opencode

**Files:**
- Create: `backend/app/remote_execution/validation.py`
- Modify: `backend/app/analysis/worker.py`
- Create: `backend/tests/test_analysis_result_writer.py`

**Interfaces:**
- `AnalysisResultWriter(session, label_service, pipeline_definition, workspace)` with `persist(run, recording, output: PipelineOutput) -> None`
- The writer validates each detection (physical box + label) and inserts `DetectionResultModel` rows, writes `artifacts.json` and `run_metadata.json`, and sets `run.status = "completed"`.
- The writer must NOT perform an unconditional `delete(DetectionResultModel)` for an already-`completed` run. The local worker keeps its existing delete-then-rewrite only when the run is not yet `completed` (preserving current `local_cpu` behavior); completed remote runs are immutable (handled by the ingestor in Task 6).

- [ ] **Step 1: Write failing test that writer does not delete completed-run detections**

Create `backend/tests/test_analysis_result_writer.py`:

```python
from benchmark_fixture import add_detection, add_recording

from app.analysis.model import AnalysisRunModel
from app.detections.model import DetectionResultModel
from app.remote_execution.validation import AnalysisResultWriter


def test_writer_inserts_new_detections_without_touching_existing_completed(client, tmp_path):
    with client.app.state.database.session_factory() as session:
        add_recording(session, recording_id="rec_w", name="w")
        session.commit()
        run = AnalysisRunModel(id="run_w", recording_id="rec_w", pipeline_id="pipeline_x",
                               pipeline_version="1.0", executor="imported", status="completed")
        session.add(run)
        add_detection(session, detection_id="det_old", run_id="run_w", class_id=9, class_name="LoRa 250kHz",
                      confidence=0.9, t0=0.01, t1=0.02, f0=2440600000.0, f1=2440700000.0)
        session.commit()
        from app.labels.service import LabelSpaceService
        from app.pipelines.base import DetectionPayload, PipelineOutput
        from app.pipelines.base import PipelineDefinition
        writer = AnalysisResultWriter(
            session, LabelSpaceService(""), PipelineDefinition(id="pipeline_x", name="P", version="1.0",
                                                              label_space="spacenet_14", recommended_device="CPU",
                                                              cpu_supported=True, stages=(), inspectable_stages=()),
            tmp_path / "ws")
        run = session.get(AnalysisRunModel, "run_w")
        recording = session.get(__import__("app.recordings.model", fromlist=["RecordingModel"]).RecordingModel, "rec_w")
        writer.persist(run, recording, PipelineOutput(detections=[DetectionPayload(
            t_start_s=0.01, t_end_s=0.02, f_low_hz=2440600000.0, f_high_hz=2440700000.0,
            class_id=9, class_name="LoRa 250kHz", confidence=0.9)]))
        session.commit()
        assert session.query(DetectionResultModel).filter(DetectionResultModel.run_id == "run_w").count() == 2
```

- [ ] **Step 2: Run and confirm RED**

```bash
pytest backend/tests/test_analysis_result_writer.py -q
```

Expected RED: missing `AnalysisResultWriter`.

- [ ] **Step 3: Minimal implementation**

Implement `AnalysisResultWriter` with the validation and insert logic extracted from `worker.execute_run` (validation + row creation + artifact/metadata writes + completion transition). Refactor `execute_run` to call it, preserving the current `local_cpu` delete-then-rewrite only for non-completed runs.

- [ ] **Step 4: Run GREEN**

```bash
pytest backend/tests/test_analysis_result_writer.py backend/tests/test_analysis_runs.py -q
```

Expected: PASS (local_cpu behavior unchanged).

- [ ] **Step 5: Commit**

```bash
git add backend/app/remote_execution/validation.py backend/app/analysis/worker.py backend/tests/test_analysis_result_writer.py
git commit -m "refactor: extract shared analysis result writer"
```

### Task 6: RemoteResultIngestor

**Owner:** 本地电脑opencode

**Files:**
- Create: `backend/app/remote_execution/result_ingestor.py`
- Create: `backend/tests/test_remote_result_ingestor.py`

**Interfaces:**
- `ingest_remote_result(session, run_id, envelope: RemoteExecutionEnvelopeV1, zip_path: Path, label_service, writer: AnalysisResultWriter) -> str`
- Verifies: envelope identity fields match the local run; `payload_sha256` equals SHA256 of exact `zip_path` bytes; strict envelope JSON; bbox bounds; label validity; confidence validity; safe ZIP extraction (reuse `app.imported_runs.archive.safe_path` and bounded extraction semantics).
- Transaction rules:
  - `pending`/`running` run + valid result -> ingest once -> `completed`;
  - already `completed` run + same `payload_sha256` -> idempotent no-op (return existing payload hash);
  - already `completed` run + different `payload_sha256` -> `REMOTE_RESULT_CONFLICT`;
  - `failed`/`interrupted` run -> never resurrected by an unrelated result.
- Never calls `ImportedRunService`; writes to the same `AnalysisRun`.

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_remote_result_ingestor.py`:

```python
import hashlib
import zipfile

import pytest

from benchmark_fixture import add_detection, add_recording

from app.analysis.model import AnalysisRunModel
from app.core.errors import PlatformError
from app.detections.model import DetectionResultModel
from app.remote_execution.result_ingestor import ingest_remote_result
from app.remote_execution.schema import RemoteExecutionEnvelopeV1


def _envelope(payload_sha256):
    return RemoteExecutionEnvelopeV1(
        schema_version=1, request_id="req_1", batch_id="batch_x", item_key="000000",
        local_run_id="run_r", recording_fingerprint="a" * 64, source_data_sha256="b" * 64,
        pipeline_id="pipeline_x", pipeline_version="1.0", orchestrator_commit="9a6f0feac0b0e6e2ac8ecd65d2e4383479e09f7c",
        remote_runtime_commit="9a6f0feac0b0e6e2ac8ecd65d2e4383479e09f7c", asset_manifest_sha256="c" * 64,
        hardware={}, payload_sha256=payload_sha256, remote_started_at=None, remote_finished_at=None,
    )


def _zip_bytes():
    buffer = __import__("io").BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("detections.json", '{"detections": []}')
    return buffer.getvalue()


def test_ingest_once_then_idempotent_noop(client, tmp_path, settings):
    from app.labels.service import LabelSpaceService
    with client.app.state.database.session_factory() as session:
        add_recording(session, recording_id="rec_r", name="r")
        session.commit()
    zip_bytes = _zip_bytes()
    payload = hashlib.sha256(zip_bytes).hexdigest()
    zip_path = tmp_path / "analysis_result.zip"
    zip_path.write_bytes(zip_bytes)
    with client.app.state.database.session_factory() as session:
        session.add(AnalysisRunModel(id="run_r", recording_id="rec_r", pipeline_id="pipeline_x",
                                     pipeline_version="1.0", executor="remote_gpu", status="running"))
        session.commit()
        labels = LabelSpaceService(settings.label_space_root)
        writer = None  # replaced by AnalysisResultWriter instance in implementation
        first = ingest_remote_result(session, "run_r", _envelope(payload), zip_path, labels, writer)
        second = ingest_remote_result(session, "run_r", _envelope(payload), zip_path, labels, writer)
        assert first == second == payload
        assert session.get(AnalysisRunModel, "run_r").status == "completed"
```

- [ ] **Step 2: Run and confirm RED**

```bash
pytest backend/tests/test_remote_result_ingestor.py -q
```

Expected RED: missing `ingest_remote_result`.

- [ ] **Step 3: Minimal implementation**

Implement the ingestor per the transaction rules above, reusing `AnalysisResultWriter.persist` for a fresh ingest and returning early for an already-ingested identical payload. Use `safe_path` + strict ZIP parsing for extraction.

- [ ] **Step 4: Run GREEN**

```bash
pytest backend/tests/test_remote_result_ingestor.py backend/tests/test_analysis_result_writer.py backend/tests/test_imported_runs.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/remote_execution/result_ingestor.py backend/tests/test_remote_result_ingestor.py
git commit -m "feat: idempotent remote result ingestion"
```

---

## M9.1-B — Runner / Resolver / Assets

### Task 7: Secure OpenSSH/SCP transport + RemoteGpuJobManager

**Owner:** 本地电脑opencode

**Files:**
- Create: `backend/app/remote_execution/profile.py`
- Create: `backend/app/remote_execution/transport.py`
- Create: `backend/app/remote_execution/job_manager.py`
- Create: `backend/tests/test_remote_transport.py`

**Interfaces:**
- `RemoteProfile` — dataclass with `name`, `host`, `port`, `user`, `ssh_key_path`, `known_hosts_path`, `remote_repo_root`, `remote_job_root`, `dataset_roots`, `asset_paths`.
- `RemoteProfile.from_env(settings: Settings) -> RemoteProfile` — reads `WSP_REMOTE_*` environment variables; raises `REMOTE_EXECUTOR_UNAVAILABLE` when missing.
- `SshRunner` — runs fixed-argv commands via `subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", f"UserKnownHostsFile={known_hosts_path}", "-i", key_path, "-p", str(port), f"{user}@{host}", *argv], ...)` with `shell=False`; never `StrictHostKeyChecking=no`.
- `RemoteGpuJobManager` — `submit(batch: RemoteExecutionBatchV1, request_json_path: Path)`, `status(batch_id) -> RemoteBatchStatusV1`, `download(batch_id, item_key, dest_dir) -> Path`.
- Transport tests use a fake `subprocess` / recorded argv; no real SSH.

- [ ] **Step 1: Write failing transport tests**

Create `backend/tests/test_remote_transport.py` (representative):

```python
from app.remote_execution.profile import RemoteProfile
from app.remote_execution.transport import SshRunner


def test_ssh_argv_uses_fixed_command_and_host_key(tmp_path, monkeypatch):
    profile = RemoteProfile(name="autodl_primary", host="auto.example.com", port=22, user="root",
                            ssh_key_path=str(tmp_path / "id_ed25519"), known_hosts_path=str(tmp_path / "known_hosts"),
                            remote_repo_root="/root/repo", remote_job_root="/root/jobs",
                            dataset_roots={}, asset_paths={})
    calls = []
    monkeypatch.setattr("subprocess.run", lambda argv, **kwargs: calls.append((argv, kwargs)) or _ok())
    runner = SshRunner(profile)
    runner.run(["status"])
    argv = calls[0][0]
    assert "ssh" in argv[0]
    assert "-o" in argv and "StrictHostKeyChecking=no" not in argv
    assert f"UserKnownHostsFile={tmp_path / 'known_hosts'}" in argv
    assert calls[0][1]["shell"] is False
```

- [ ] **Step 2: Run and confirm RED**

```bash
pytest backend/tests/test_remote_transport.py -q
```

Expected RED: missing modules.

- [ ] **Step 3: Minimal implementation**

Implement the profile loader, fixed-argv `SshRunner` (with a `run`/`scp`/`sftp` set of fixed commands and strict identifier validation), and `RemoteGpuJobManager`. No `shell=True`, no arbitrary user command strings, no `StrictHostKeyChecking=no`.

- [ ] **Step 4: Run GREEN**

```bash
pytest backend/tests/test_remote_transport.py backend/tests/test_remote_execution_canonical.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/remote_execution/profile.py backend/app/remote_execution/transport.py backend/app/remote_execution/job_manager.py backend/tests/test_remote_transport.py
git commit -m "feat: secure remote gpu job transport"
```

### Task 8: Detached server remote runner

**Owner:** 服务器opencode

**Files:**
- Create: `backend/app/remote_execution/runner.py`
- Create: `backend/tests/test_remote_runner.py`

**Interfaces:**
- CLI: `python -m app.remote_execution.runner probe|submit|status|work` with strict flags.
  - `probe` — verify deployed repo HEAD equals `required_remote_runtime_commit` and asset hashes match the manifest; exit nonzero on mismatch.
  - `submit` — strict-parse the request, exclude `request_sha256`, rebuild canonical payload, recompute `request_sha256`, require equality (else `REMOTE_REQUEST_INVALID`); create-or-attach by `batch_id`; on duplicate identical hash return existing job; on duplicate different hash return `REMOTE_REQUEST_CONFLICT`.
  - `status` — write `status.json` atomically (temp + fsync + rename).
  - `work` — detached process that resolves assets once, initializes CUDA once, loads models once, then iterates items; each item writes per-item result + status; terminal item results are write-once. If a terminal item exists but its result artifact is missing/corrupted, the runner reports `REMOTE_RESULT_CORRUPTED` and never regenerates/overwrites it.

- [ ] **Step 1: Write failing runner tests**

Create `backend/tests/test_remote_runner.py` with `submit` create-or-attach semantics (same `batch_id` + same hash -> attach; different hash -> `REMOTE_REQUEST_CONFLICT`) and write-once terminal result (re-`work` on a completed item does not overwrite the result).

- [ ] **Step 2: Run and confirm RED**

```bash
pytest backend/tests/test_remote_runner.py -q
```

Expected RED: missing `app.remote_execution.runner`.

- [ ] **Step 3: Minimal implementation**

Implement the four subcommands with atomic file publication and strict identifier validation. The remote worker does not run pipeline algorithm logic here; it delegates to `app.pipelines.zoomspec_...` (Task 12) via a resolver (Task 9).

- [ ] **Step 4: Run GREEN**

```bash
pytest backend/tests/test_remote_runner.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/remote_execution/runner.py backend/tests/test_remote_runner.py
git commit -m "feat: detached remote runner with idempotent submit"
```

### Task 9: SpaceNet remote resolver

**Owner:** 服务器opencode

**Files:**
- Create: `backend/app/remote_execution/resolver.py`
- Create: `backend/tests/test_remote_resolver.py`

**Interfaces:**
- `ResolvedSpaceNetInput` — `recording_fingerprint`, `source_data_sha256`, and a sanitized `RecordingInput`-like object carrying raw IQ path + non-label metadata (sample rate, center frequency, frequency bounds, duration) with NO signals/labels.
- `resolve_space_net(dataset_root: Path, split: str, key: str, label_space: str, expected_fingerprint: str, expected_source_hash: str, label_space_root: Path) -> ResolvedSpaceNetInput`
  - accepts only `dataset/split/key` logical identity;
  - uses `SpaceNetAdapter` to resolve `.bin` + `.json`;
  - recomputes `recording_fingerprint_v1` and `source_data_sha256`;
  - requires both to equal the expected values, else `RECORDING_FINGERPRINT_MISMATCH` / `SOURCE_DATA_HASH_MISMATCH`;
  - returns a sanitized input that excludes signals/labels.

- [ ] **Step 1: Write failing resolver tests**

Create `backend/tests/test_remote_resolver.py` (representative):

```python
import hashlib

import pytest

from app.core.errors import PlatformError
from app.remote_execution.resolver import resolve_space_net
from app.benchmarks.manifest import ManifestGroundTruth, ManifestRecording
from app.imported_runs.fingerprint import build_recording_fingerprint


def test_resolve_requires_source_hash_equality(tmp_path):
    # build a tiny SpaceNet dataset dir with sample a
    ...
    resolved = resolve_space_net(tmp_path, "test", "a", "spacenet_14", fp, source_sha, ...)
    assert resolved.source_data_sha256 == source_sha
    with pytest.raises(PlatformError) as exc:
        resolve_space_net(tmp_path, "test", "a", "spacenet_14", fp, "0" * 64, ...)
    assert exc.value.code == "SOURCE_DATA_HASH_MISMATCH"


def test_sanitized_input_excludes_signals(resolved):
    assert getattr(resolved, "signals", None) is None
```

- [ ] **Step 2: Run and confirm RED**

```bash
pytest backend/tests/test_remote_resolver.py -q
```

Expected RED: missing module.

- [ ] **Step 3: Minimal implementation**

Implement the resolver using `SpaceNetAdapter.load`, `build_recording_fingerprint`, and `compute_file_sha256`; return the sanitized input with no signals/labels.

- [ ] **Step 4: Run GREEN**

```bash
pytest backend/tests/test_remote_resolver.py backend/tests/test_remote_source_hash.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/remote_execution/resolver.py backend/tests/test_remote_resolver.py
git commit -m "feat: exact spacenet remote recording resolution"
```

### Task 10: Frozen pipeline asset manifest + fail-closed verification

**Owner:** 服务器opencode

**Files:**
- Create: `backend/app/remote_execution/assets.py`
- Create: `backend/tests/test_remote_assets.py`

**Interfaces:**
- `PipelineAssetManifest` — `pipeline_id`, `pipeline_version`, `assets: dict[str, str]` (logical asset -> sha256), `asset_manifest_sha256`.
- `verify_assets(manifest, asset_paths: dict[str, Path]) -> None` — hashes each file and requires exact equality; raises `PIPELINE_ASSET_MISMATCH` on any mismatch.
- `verify_remote_runtime_commit(repo_root: Path, required: str) -> None` — reads `git rev-parse HEAD` and requires equality; raises `REMOTE_IMPLEMENTATION_MISMATCH` otherwise.

The implementation must obtain and record the real hashes BEFORE committing the manifest:

```bash
sha256sum /root/autodl-tmp/Claude/runs/cpn/ls_stft_yolo26n_aug_warm/weights/best.pt
sha256sum /root/autodl-tmp/Claude/artifacts/frn_combined_v3_training/best.pt
sha256sum /root/autodl-tmp/Claude/configs/frozen_full_pipeline_v26_aug_combined.yaml
```

Do not invent the hash values in this plan; the task records the observed values into `asset_manifest.json`.

- [ ] **Step 1: Write failing asset tests**

Create `backend/tests/test_remote_assets.py`: verify a matching manifest passes, a single byte-different file raises `PIPELINE_ASSET_MISMATCH`, and a mismatched commit raises `REMOTE_IMPLEMENTATION_MISMATCH`.

- [ ] **Step 2: Run and confirm RED**

```bash
pytest backend/tests/test_remote_assets.py -q
```

Expected RED: missing module.

- [ ] **Step 3: Minimal implementation**

Implement `verify_assets` (bounded chunk hashing) and `verify_remote_runtime_commit` (fixed `git rev-parse HEAD` argv, `shell=False`).

- [ ] **Step 4: Run GREEN + record real hashes**

```bash
pytest backend/tests/test_remote_assets.py -q
```

Then run the three `sha256sum` commands above on the server, and write the observed values into `backend/app/pipelines/zoomspec_yolo26n_aug_combined_frn_v3/asset_manifest.json` plus the logical `asset_paths` mapping in the `autodl_primary` profile (secrets/paths stay out of Git).

- [ ] **Step 5: Commit**

```bash
git add backend/app/remote_execution/assets.py backend/tests/test_remote_assets.py backend/app/pipelines/zoomspec_yolo26n_aug_combined_frn_v3/asset_manifest.json
git commit -m "feat: frozen pipeline asset manifest verification"
```

---

## M9.1-C — Native Frozen Pipeline + First Live Smoke

### Task 11: Legacy source characterization

**Owner:** 服务器opencode

**Files:**
- Create: `docs/research/m9_1_legacy_source_map.md` (evidence record)

**Interfaces:**
- Produces a source-map evidence document detailing the exact legacy code/functions, tensor shapes, preprocessing, thresholds, class ordering, and coordinate transforms used by the frozen historical pipeline.

- [ ] **Step 1: Verify the historical oracle**

```bash
sha256sum /root/autodl-tmp/Claude/reports_claude/test_val_detections_augv3.jsonl
```

Expected SHA256: `950ad87ec355169b1904da4364f296ade5854926d4e02dc83049d9585859efcd`. If it differs, STOP and report.

- [ ] **Step 2: Document the legacy source map**

Inspect the legacy tree under `/root/autodl-tmp/Claude` and `/root/autodl-tmp/ZoomSpec` read-only. Record: entrypoint (`run_test_new_pipeline.py`), `evaluate_detections.py`, LS-STFT parameters, detector (CPN/YOLOv26n) input/output shapes and class ordering, AHLP behavior, Combined FRN V3 inputs/thresholds, confidence/NMS handling, and the physical coordinate transform (time seconds / absolute Hz).

- [ ] **Step 3: Record and commit evidence only**

```bash
git add docs/research/m9_1_legacy_source_map.md
git commit -m "docs: record m9.1 legacy source map"
```

No runtime code depends on the legacy tree; it is reference only.

### Task 12: Port minimal platform-native frozen inference

**Owner:** 服务器opencode

**Files:**
- Create: `backend/app/pipelines/zoomspec_yolo26n_aug_combined_frn_v3/{__init__,definition,preprocessing,detector,ahlp,frn,pipeline}.py`
- Modify: `backend/app/pipelines/registry.py`
- Create: `backend/tests/test_zoomspec_pipeline_cpu.py`

**Interfaces:**
- `ZoomSpecYolo26nAugCombinedFrnV3Pipeline` implements `Pipeline.run(...) -> PipelineOutput` with `definition.id = "zoomspec_yolo26n_aug_combined_frn_v3"`, `version = "1.0.0"`, `label_space = "spacenet_14"`, `executors_supported = ("local_cpu", "remote_gpu")`, `recommended_executor = "remote_gpu"`.
- GPU-heavy imports (PyTorch, CUDA) are lazy and only executed on the server-side runner path; the CPU/mock path runs a deterministic stub that returns empty detections or a fixed fixture for tests.
- `preprocessing.py` implements LS-STFT / frozen preprocessing; `detector.py` the YOLOv26n / CPN-equivalent forward; `ahlp.py`; `frn.py`; `pipeline.py` orchestrates them into physical `DetectionPayload`s.

- [ ] **Step 1: Write failing CPU/mock pipeline test**

Create `backend/tests/test_zoomspec_pipeline_cpu.py`: assert the registered pipeline has the exact id/version/label_space and that `pipeline.run` accepts a `RecordingInput` and returns a `PipelineOutput` with physical-coordinate detections (seconds / absolute Hz) or an empty list on the CPU stub.

- [ ] **Step 2: Run and confirm RED**

```bash
pytest backend/tests/test_zoomspec_pipeline_cpu.py -q
```

Expected RED: pipeline not registered.

- [ ] **Step 3: Minimal implementation**

Port the minimal inference chain. Add a module scan test proving the platform runtime never imports `/root/autodl-tmp/Claude` or `/root/autodl-tmp/ZoomSpec` (e.g., a test that asserts `sys.path` / module import hooks never reference those roots).

- [ ] **Step 4: Run GREEN**

```bash
pytest backend/tests/test_zoomspec_pipeline_cpu.py backend/tests/test_analysis_runs.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/pipelines/zoomspec_yolo26n_aug_combined_frn_v3/ backend/app/pipelines/registry.py backend/tests/test_zoomspec_pipeline_cpu.py
git commit -m "feat: platform native frozen zoomspec pipeline"
```

### Task 13: Wire remote_gpu into AnalysisService + supervisor + API + UI

**Owner:** 本地电脑opencode

**Files:**
- Modify: `backend/app/analysis/service.py`
- Modify: `backend/app/analysis/router.py`
- Modify: `backend/app/analysis/schema.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/remote_execution/executor.py` (create)
- Modify: `backend/app/remote_execution/supervisor.py` (create)
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/pages/SpectrumAnalysisPage.tsx`
- Modify: `frontend/src/pages/SpectrumAnalysisPage.test.tsx`

**Interfaces:**
- `AnalysisService.create_run(..., executor="local_cpu")` stops hardcoding the local_cpu-only rejection; for `executor="remote_gpu"` it constructs a `RemoteExecutionBatchV1` (single item) and calls `RemoteGpuJobManager.submit`, persisting `execution_metadata_json`.
- `AnalysisService.startup_reconcile()` — local_cpu running -> interrupted; remote_gpu running -> reconcile remote state (Task 15).
- `frontend createAnalysisRun(recordingId, pipelineId, executor)` no longer hardcodes `local_cpu`; the page passes the backend-selected executor.

- [ ] **Step 1: Write failing create_run(remote_gpu) test**

Create/extend `backend/tests/test_remote_executor_availability.py` or a new `backend/tests/test_analysis_remote_create.py` asserting `create_run(executor="remote_gpu")` with a configured profile produces a `pending` run with `execution_metadata_json` and does not throw `EXECUTOR_UNAVAILABLE`.

- [ ] **Step 2: Run and confirm RED**

```bash
pytest backend/tests/test_analysis_remote_create.py -q
```

Expected RED: create_run still rejects remote_gpu.

- [ ] **Step 3: Minimal implementation**

Refactor `create_run` to dispatch by executor (local via `LocalJobManager`, remote via `RemoteGpuExecutor`/`RemoteGpuJobManager`). Wire `RemoteRunSupervisor` and `startup_reconcile` into `create_app`. Update the frontend client and `SpectrumAnalysisPage` to call executor-aware `createAnalysisRun` and render backend-provided availability; the Run button is enabled only when backend availability says so.

- [ ] **Step 4: Run GREEN (backend + frontend)**

```bash
pytest backend/tests/test_analysis_remote_create.py backend/tests/test_analysis_runs.py backend/tests/test_remote_executor_availability.py -q
npm test -- --run src/pages/SpectrumAnalysisPage.test.tsx
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/analysis/service.py backend/app/analysis/router.py backend/app/analysis/schema.py backend/app/main.py backend/app/remote_execution/executor.py backend/app/remote_execution/supervisor.py backend/tests/test_analysis_remote_create.py frontend/src/api/client.ts frontend/src/api/types.ts frontend/src/pages/SpectrumAnalysisPage.tsx frontend/src/pages/SpectrumAnalysisPage.test.tsx
git commit -m "feat: wire remote gpu analysis workflow"
```

### Task 14: Provisional Gate 1 — single recording true live smoke

**Owner:** 本地电脑opencode（orchestration） + 服务器opencode（GPU execution side）

**Files:** No product code changes expected. Evidence collected into `docs/research/m9_1_gate1_provisional.md` (documentation commit only).

- [ ] **Step 1: Deploy frozen candidate to AutoDL**

The server deploys the `feature/m9-1-live-remote-gpu-inference` worktree at a validated `remote_runtime_commit` and verifies `probe` (repo HEAD + asset hashes) passes.

- [ ] **Step 2: Run sample 0 true live smoke**

From the local side, create one `AnalysisRun(executor=remote_gpu)` for `SpaceNet/test` sample `0`, submit as a one-item batch, let the server run `work`, download, validate, and ingest into the same run. Confirm Spectrum Analysis shows real detections.

- [ ] **Step 3: Record evidence**

```bash
git add docs/research/m9_1_gate1_provisional.md
git commit -m "docs: record provisional m9.1 gate 1"
```

This is an early integration gate only; it is re-run on the final candidate in Task 19.

---

## M9.1-D — Recovery / Batch / Final Runtime Freeze

### Task 15: Executor-aware restart reconciliation + N-item batch

**Owner:** 本地电脑opencode

**Files:**
- Create: `backend/app/remote_execution/supervisor.py` (extend), `backend/app/remote_execution/reconciler.py`
- Modify: `backend/app/analysis/service.py` (`mark_stale_running_runs_interrupted` split)
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_restart_reconciliation.py`
- Create: `backend/tests/test_remote_supervisor.py`

**Interfaces:**
- `mark_stale_running_runs_interrupted(session)` -> only `local_cpu` running runs become `interrupted`.
- `reconcile_remote_running_runs(session, profile)` -> remote_gpu running runs query remote state: remote running -> resume supervision; remote completed -> download+ingest; remote failed -> run failed; remote missing -> interrupted.
- `RemoteRunSupervisor` polls and triggers idempotent ingest; tests cover duplicate supervisor/reconciler completion (same `payload_sha256` no-op) and partial batch failure (completed items stay completed; no terminal result -> interrupted).

- [ ] **Step 1: Write failing reconciliation tests**

Create `backend/tests/test_restart_reconciliation.py`: `local_cpu` running -> interrupted; `remote_gpu` running with a fake remote `completed` status -> ingested to `completed`; `remote_gpu` running with remote `missing` -> `interrupted`.

- [ ] **Step 2: Run and confirm RED**

```bash
pytest backend/tests/test_restart_reconciliation.py backend/tests/test_remote_supervisor.py -q
```

Expected RED: missing reconciler/supervisor behavior.

- [ ] **Step 3: Minimal implementation**

Implement the reconciler + supervisor; wire into `create_app` startup and a background poller.

- [ ] **Step 4: Run GREEN**

```bash
pytest backend/tests/test_restart_reconciliation.py backend/tests/test_remote_supervisor.py backend/tests/test_analysis_runs.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/remote_execution/reconciler.py backend/app/remote_execution/supervisor.py backend/app/analysis/service.py backend/app/main.py backend/tests/test_restart_reconciliation.py backend/tests/test_remote_supervisor.py
git commit -m "feat: executor aware restart reconciliation"
```

### Task 16: Exact-membership operator batch service/CLI

**Owner:** 本地电脑opencode

**Files:**
- Create: `backend/app/remote_execution/operator.py`
- Create: `backend/app/remote_execution/cli.py`
- Create: `backend/tests/test_remote_operator.py`

**Interfaces:**
- `OperatorBatchService` — `plan_membership(dataset_name, split, label_space, selection) -> list[AnalysisRun]`, `submit_batch(run_ids) -> batch_id`, `monitor(batch_id)`.
- The CLI supports `1` / `cohort` / `2500`. It never resolves runs ambiguously by "latest"; it freezes exact run membership. It creates ordinary `AnalysisRun` rows and reuses `DatasetBenchmarkService` exact membership for the final `DatasetEvaluation`.

- [ ] **Step 1: Write failing operator tests**

Create `backend/tests/test_remote_operator.py`: planning a fixed cohort returns exactly the requested run set, and `submit_batch` creates exactly one remote batch for N runs without creating a `BatchRun` table row.

- [ ] **Step 2: Run and confirm RED**

```bash
pytest backend/tests/test_remote_operator.py -q
```

Expected RED: missing modules.

- [ ] **Step 3: Minimal implementation**

Implement `operator.py` + `cli.py`.

- [ ] **Step 4: Run GREEN**

```bash
pytest backend/tests/test_remote_operator.py backend/tests/test_benchmark_membership.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/remote_execution/operator.py backend/app/remote_execution/cli.py backend/tests/test_remote_operator.py
git commit -m "feat: exact membership remote batch operator"
```

### Task 17: Implement + freeze parity protocol and fixed cohort

**Owner:** 本地电脑opencode

**Files:**
- Create: `backend/app/remote_execution/parity.py`
- Create: `backend/app/remote_execution/parity/live_remote_parity_v1.json`
- Create: `backend/app/remote_execution/parity/spacenet_test_cohort_v1.json`
- Create: `backend/tests/test_remote_parity.py`

**Interfaces:**
- `live_remote_parity_v1.json` encodes the frozen automatic criteria:
  - prediction count exact;
  - same `class_id`;
  - deterministic one-to-one matching;
  - physical TF IoU >= `0.9999`;
  - confidence absolute delta <= `1e-5`;
  - zero unmatched predictions.
  - Gate 3 scalar tolerance: `abs_delta <= 1e-6`; prediction count and coverage exact unless an explicit reviewed explanation exists.
- `spacenet_test_cohort_v1.json` — 32-64 fixed `SpaceNet/test` samples covering all 14 classes, multiple bandwidths, different signal counts, overlap, and boundary-clipped/difficult cases; no random resampling.
- `run_gate2_pair(historical_run, live_run, protocol_config) -> bool` and `parity_conclusion(...)` returning `PARITY_CONFIRMED` / `PARITY_REVIEW_REQUIRED` / `PARITY_DIFFERENCE_UNEXPLAINED`.

Do NOT run Gate 2 in this task.

- [ ] **Step 1: Write failing parity tests**

Create `backend/tests/test_remote_parity.py`: a fully matching pair returns parity true with zero unmatched; a single unmatched prediction makes `PARITY_REVIEW_REQUIRED`; the frozen config tolerances are read from `live_remote_parity_v1.json`.

- [ ] **Step 2: Run and confirm RED**

```bash
pytest backend/tests/test_remote_parity.py -q
```

Expected RED: missing modules/files.

- [ ] **Step 3: Minimal implementation**

Write the frozen JSON configs (fixed values, no placeholders) and the parity comparator.

- [ ] **Step 4: Run GREEN**

```bash
pytest backend/tests/test_remote_parity.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/remote_execution/parity.py backend/app/remote_execution/parity/live_remote_parity_v1.json backend/app/remote_execution/parity/spacenet_test_cohort_v1.json backend/tests/test_remote_parity.py
git commit -m "feat: freeze live remote parity protocol and cohort"
```

### Task 18: Immutable validation-certificate infrastructure

**Owner:** 本地电脑opencode

**Files:**
- Create: `backend/app/remote_execution/validation.py` (extend with certificate)
- Modify: `backend/app/remote_execution/parity.py`
- Create: `backend/tests/test_remote_validation.py`

**Interfaces:**
- `LiveImplementationValidationCertificateV1` dataclass with identity fields: `pipeline_id`, `pipeline_version`, `remote_runtime_commit`, `asset_manifest_sha256`, `parity_protocol_id`, `parity_protocol_config_sha256`, `historical_dataset_evaluation_id`, `live_dataset_evaluation_id`, `dataset_manifest_hash`, `coverage`, `reference_metrics`, `live_metrics`, `parity_conclusion`, `accepted_at`.
- `certificate_tuple(run) -> tuple` and `implementation_validation_state(run, accepted_certificates) -> "live_validated" | "live_candidate"` — derived from the exact tuple match, never a mutable bool.

- [ ] **Step 1: Write failing certificate tests**

Create `backend/tests/test_remote_validation.py`: a run whose tuple matches an accepted certificate -> `live_validated`; any different `remote_runtime_commit` or `asset_manifest_sha256` -> `live_candidate`.

- [ ] **Step 2: Run and confirm RED**

```bash
pytest backend/tests/test_remote_validation.py -q
```

Expected RED: missing certificate helpers.

- [ ] **Step 3: Minimal implementation**

Implement the certificate dataclass and tuple-derived validation.

- [ ] **Step 4: Run GREEN + full test/build**

```bash
pytest backend/tests -q
npm test -- --run
npm run build
```

Expected: all PASS. This task MUST complete before the final runtime candidate is frozen.

- [ ] **Step 5: Commit**

```bash
git add backend/app/remote_execution/validation.py backend/app/remote_execution/parity.py backend/tests/test_remote_validation.py
git commit -m "feat: immutable live validation certificate"
```

**ONLY AFTER THIS TASK:** freeze the final `remote_runtime_commit` candidate. No runtime-changing code may be scheduled after a successful Gate 2.

### Task 19: Final-candidate Gate 1 rerun + Gate 2

**Owner:** 本地电脑opencode（orchestration）+ 服务器opencode（GPU execution）

**Files:** No product code changes expected. Evidence committed to `docs/research/m9_1_gate2.md` (documentation only).

- [ ] **Step 1: Deploy exact frozen candidate**

Deploy the frozen candidate to AutoDL; verify `probe` (repo HEAD == frozen `remote_runtime_commit`; asset hashes match).

- [ ] **Step 2: Rerun sample 0 true-live Gate 1 on the exact candidate**

If any code/config changes, the candidate is invalid; restart final Gate 1 and Gate 2.

- [ ] **Step 3: Run fixed cohort as one remote batch**

Use only the committed `live_remote_parity_v1`; compare historical vs live per the frozen criteria. Record per-sample parity and any `PARITY_REVIEW_REQUIRED` items.

- [ ] **Step 4: Commit documentation-only evidence**

```bash
git add docs/research/m9_1_gate2.md
git commit -m "docs: record m9.1 gate 2 parity evidence"
```

The evidence commit does not change the `remote_runtime_commit` identity.

---

## M9.1-E — Full 2500 + Acceptance

### Task 20: Gate 3 — full SpaceNet test 2500

**Owner:** 本地电脑opencode（orchestration/DB/UI）+ 服务器opencode（GPU execution）

**Files:** No product code changes expected. Evidence committed to `docs/research/m9_1_gate3.md`.

- [ ] **Step 1: Create fresh 2500 remote_gpu AnalysisRuns**

Exact membership from the frozen dataset manifest; submit as one remote batch; require 2500/2500 completion.

- [ ] **Step 2: Create one DatasetEvaluation with physical_tf_detection_ap_v2**

Using `DatasetBenchmarkService.create_evaluation` with the exact live run membership. Verify GT provenance: raw `20018`, canonical `19962`, removed duplicates `56`.

- [ ] **Step 3: Compare against the historical reference**

```text
predictions = 33373
Localization AP50: 0.8666496180384053
Localization AP50:95: 0.6715938311249926
matched classification accuracy: 0.8151265187827444
class-aware mAP50: 0.49706861157413673
class-aware mAP50:95: 0.3732512758737991
```

Compare prediction count, coverage, Localization AP50/AP50:95, class-aware mAP50/mAP50:95, matched accuracy, per-class AP, confusions, and item-level mismatch per the frozen config. `PARITY DIFFERENCE UNEXPLAINED` cannot pass.

- [ ] **Step 4: Create the LiveImplementationValidationCertificateV1**

Only after accepted Gate 3, with `remote_runtime_commit`, `asset_manifest_sha256`, both DatasetEvaluation ids, dataset manifest hash, coverage, reference/live metrics, parity conclusion, and `accepted_at`.

- [ ] **Step 5: Commit documentation-only evidence**

```bash
git add docs/research/m9_1_gate3.md
git commit -m "docs: record m9.1 gate 3 live parity"
```

### Task 21: Final acceptance

**Owner:** 本地电脑opencode（orchestration + UI/browser）

**Files:** No behavior changes during acceptance. Evidence committed to `docs/research/m9_1_acceptance.md`.

- [ ] **Step 1: Fresh complete backend suite**

```bash
pytest backend/tests -q
```

Expected: 0 failed.

- [ ] **Step 2: Fresh complete frontend suite**

```bash
npm test -- --run
npm run build
```

Expected: 0 failed; build PASS.

- [ ] **Step 3: Actual browser smoke**

Run backend against the real `platform.db`; open Spectrum Analysis for a live run and verify detections overlay, executor/GPU/CUDA/platform-commit provenance, and implementation validation state. Also run a restart-reconciliation smoke (restart the local FastAPI while a remote run is running; confirm the run is reconciled and not auto-interrupted).

- [ ] **Step 4: Security scan**

Verify no secret/SSH key/password/token in Git, DB, AnalysisRun JSON, request packages, or logs; verify no `shell=True` / `StrictHostKeyChecking=no` in the production transport; verify no legacy source import.

- [ ] **Step 5: Final acceptance evidence + commit**

```bash
git add docs/research/m9_1_acceptance.md
git commit -m "docs: record m9.1 acceptance"
```

---

## Cross-Machine Handoff Protocol

- Never push the same feature branch from both machines concurrently.
- Before editing after a handoff, each agent runs:

```bash
git fetch origin
git checkout feature/m9-1-live-remote-gpu-inference
git merge --ff-only origin/feature/m9-1-live-remote-gpu-inference
git rev-parse HEAD
```

- After a task: tests -> review -> fix Critical/Important findings -> fresh verification -> commit -> normal push -> report the exact new SHA to the other machine.
- `本地电脑opencode` owns Tasks 1-7, 13, 15-18, and orchestration/DB/UI of 19-21. `服务器opencode` owns Tasks 8-12, the server side of 14 and 15, and the GPU execution side of 19-20.

## Final M9.1 Verification Checklist

Before claiming M9.1 complete, fresh evidence must prove:

```text
existing local_cpu / imported / benchmark regression: PASS
remote_gpu creates and completes ordinary AnalysisRuns: YES
remote result always writes to the SAME AnalysisRun: YES
no executor=imported run from remote_gpu: YES
no RemoteJob/BatchRun/RemoteAnalysisRun/GpuRun table: YES
source_data_sha256 required with recording_fingerprint_v1: YES
GroundTruth never enters inference: YES
request_sha256 excludes itself (canonical): YES
terminal remote item result write-once: YES
completed AnalysisRun + DetectionResults immutable: YES
payload_sha256 hashes exact analysis_result.zip bytes: YES
SSH host-key verified; no shell=True / StrictHostKeyChecking=no: YES
remote runtime only verifies required_remote_runtime_commit: YES
Pipeline Asset Manifest hash-locked: YES
no legacy Claude/ZoomSpec runtime import: YES
live_validated derived from certificate tuple: YES
parity protocol/cohort frozen before Gate 2: YES
Gate 1 sample 0 true live smoke: PASS
Gate 2 fixed cohort parity: PASS
Gate 3 full 2500 parity: PASS
final candidate Gate 1 rerun before Gate 2: YES
no runtime-changing task after successful Gate 2: YES
full backend/frontend/build/browser/restart/security acceptance: PASS
```

---

## Required Final Report

The executor ends with a single report titled:

`M9.1 Live Remote GPU Inference Report`

covering: branch + final HEAD, base + ancestry, complete commit list, files created/modified, provenance-field migration evidence, protocol/canonical-hash evidence, source-hash + resolver evidence, executor availability evidence, result writer/ingestor evidence, reconciliation/batch evidence, parity protocol/cohort evidence, certificate evidence, Gate 1/2/3 evidence, security scan, full backend/frontend/build evidence, real browser smoke, and final verdict `PASS` or `FAIL`. The report then stops; it must not begin unrelated work.