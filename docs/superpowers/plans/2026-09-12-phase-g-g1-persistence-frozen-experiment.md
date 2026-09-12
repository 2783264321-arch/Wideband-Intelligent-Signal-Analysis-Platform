# Phase G G1 Persistence and Frozen Experiment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:test-driven-development while implementing each task and
> superpowers:verification-before-completion before claiming a gate complete.

**Goal:** Add the G1 durable foundation for Phase G — three new tables, an
additive migration, internal read models, and a `DatasetExperiment` creation
service that freezes dataset membership, scientific identity, and execution
identity while launching nothing — plus a reusable frozen-identity
revalidation seam that later gates will call before scheduling. No
AnalysisRun, no Attempt, no worker, no coordinator, no DatasetEvaluation, no
REST, and no scientific change are created in G1.

**Architecture:** An additive `app.dataset_experiments` package that owns only
orchestration persistence and identity freezing. It reuses the existing
benchmark frozen-manifest authority (`DatasetBenchmarkService.prepare_manifest`),
the declarative/control-plane plugin catalog (`PipelineRegistry` +
`validate_plugin_parameters`), the single ModelRelease authority
(`ModelReleaseStore.resolve`), the frozen protocol set, and the exact
certificate + provider `RuntimeDescriptor` seam
(`ExecutorRegistry.certified_capability` / `.provider` /
`provider.runtime_descriptor()`). G1 never calls `availability_for`, never
probes a Recording, and never touches `AnalysisService`.

**Tech Stack:** Python 3.12 (`/root/autodl-tmp/WISA-m9-2-implementation/.venv`),
FastAPI, SQLAlchemy 2.x, Pydantic v2, pytest 9. No GPU, no SSH, no torch.

**Spec:** `docs/superpowers/specs/2026-09-12-m9-2-dataset-experiment-orchestration-design.md`

**Base:** `feature/m9-2-implementation @ 2beefeeeda71ee6c1c8a6b983bf0e679581c85fd`

---

## Global Constraints

These hold for every task in this plan. A step that violates any of them is a
stop-and-escalate condition.

1. **G1 launches nothing.** No `AnalysisRun` row, no
   `DatasetExperimentAttempt` row, no worker subprocess, no coordinator, no
   `DatasetEvaluation` row, no `AnalysisService.prepare_run`/`launch` change,
   no REST router, no frontend. Creation is pure persistence.
2. **Three tables only.** `dataset_experiments`,
   `dataset_experiment_items`, `dataset_experiment_attempts`. No new
   `analysis_runs` columns, no DatasetExperiment FK on `analysis_runs`, no
   persisted aggregate counters, no `current_analysis_run_id`, no
   `attempt_count`.
3. **One manifest authority.** All dataset membership, ordering, and hashing
   comes from `DatasetBenchmarkService.prepare_manifest(dataset_name,
   dataset_split, label_space)` (`backend/app/benchmarks/service.py:424`). G1
   does not reimplement canonical number formatting, GroundTruth sorting,
   Recording ordering, or SHA256 payload construction.
4. **One release authority.** Release-bound plugins use exactly
   `ModelReleaseStore.resolve(plugin_id, plugin_version, requested)`
   (`backend/app/remote_execution/model_release.py:146`). G1 never calls
   `resolve_by_manifest_sha`.
5. **One certificate authority.** Certificate/descriptor checks use
   `ExecutorRegistry.certified_capability(...)` and
   `ExecutorRegistry.provider(...)` (`backend/app/remote_execution/runtime.py:265`
   and `:222`). G1 never calls `availability_for` and never probes a Recording.
6. **One plugin catalog.** The exact `(plugin_id, plugin_version)` is resolved
   through `PipelineRegistry.get(plugin_id)` (`backend/app/pipelines/registry.py:72`)
   and the discovered `definition.version`; parameters are validated with
   `validate_plugin_parameters` (`backend/app/pipelines/plugin.py:71`). No
   plugin-specific branching.
7. **Frozen protocol set.** `evaluation_protocol` is validated against the
   existing supported set exposed by `app.benchmarks.service`; G1 does not
   create a `DatasetEvaluation`.
8. **Atomic creation.** Experiment + exactly one Item per frozen manifest
   Recording commit in a single transaction; failure before commit leaves no
   partial rows.
9. **Fail-closed revalidation.** The revalidation seam reads and compares only;
   it must never mutate frozen fields to "repair" drift.
10. **Additive migration only.** `Model.__table__.create(engine,
    checkfirst=True)` wired into `run_additive_migrations()`; no Alembic.
11. **Torch-free control plane.** The new package and all imports reachable
    from it must not import `torch`/`ultralytics`.
12. **Untouched frozen contracts.** Canonical request hashing, AssetManifest V1
    hashing, `ExecutionCertificate` semantics, `ModelRelease` authority, and
    the `analysis_runs` schema stay byte-for-byte/semantically unchanged.

---

## Repository Mapping (verified against HEAD 2beefee)

### Existing manifest authority

- `backend/app/benchmarks/service.py:424` — `DatasetBenchmarkService.prepare_manifest(dataset_name, dataset_split, label_space) -> ManifestPreview`.
  - `ManifestPreview.recording_manifest_hash` / `.expected_recordings` / `.entries` (`:34`).
  - `ManifestEntry.manifest_order` / `.recording_id` / `.recording_name` / `.gt_count` (`:72`).
  - Internals: `_build_frozen_manifest` (`:210`) → `build_recording_manifest` (`backend/app/benchmarks/manifest.py:84`). The hash payload is frozen and its SHA256 is authoritative.
  - Empty GT-bearing selection raises `DATASET_SNAPSHOT_EMPTY` (`:177`).
- Existing tests: `backend/tests/test_benchmark_manifest.py`,
  `backend/tests/test_benchmark_membership.py`.

### Existing release authority

- `backend/app/remote_execution/model_release.py:146` — `ModelReleaseStore.resolve(plugin_id, plugin_version, requested) -> ResolvedModelRelease`.
  - `ResolvedModelRelease.release.model_release_id`, `.manifest.asset_manifest_sha256`.
  - Release-less plugin semantics are owned by the caller: `AnalysisService._resolve_release` (`backend/app/analysis/service.py:91`) rejects a requested release when `not definition.model_release_required`.
- Existing tests: `backend/tests/test_model_release.py`,
  `backend/tests/test_release_wiring.py`.

### Existing certificate / executor authority

- `backend/app/remote_execution/runtime.py:265` — `ExecutorRegistry.certified_capability(definition, model_release_id, executor) -> ExecutionCapability | None`. Binds the plugin's declared technical capability to the provider's ACTUAL `runtime_descriptor()` and an exact `ExecutionCertificate` for `(plugin_id, plugin_version, model_release_id, executor, device_type, precision, provider.runtime_ref)`. No I/O.
- `:222` — `ExecutorRegistry.provider(executor) -> ExecutorProvider` (raises `EXECUTION_CAPABILITY_UNAVAILABLE`).
- `:300` — `deployment_qualified_executors(...)` (config/certification only).
- `:315` — `availability_for(...)`; **G1 must not call this** because it reaches `provider.availability(...)` which probes (see `LocalInferenceWorkerProvider.probe` at `backend/app/analysis/local_executor.py:73` and `SshRemoteExecutorProbe.availability` at `backend/app/remote_execution/executor.py:73`).
- `RuntimeDescriptor.to_metadata()` (`:61`) / `from_metadata` (`:72`).
- `ExecutionCertificate` / `ExecutionCertificateStore` (`:115` / `:137`).
- Existing tests: `backend/tests/test_execution_certificate.py`,
  `backend/tests/test_executor_registry.py`, and the shared fakes
  `backend/tests/executor_fixtures.py` (`FakeProvider`, `FakeRegistry`).

### Existing plugin authority

- `backend/app/pipelines/registry.py:49` — control-plane `PipelineRegistry` keyed by plugin id, derived from declarative declarations (`:81` `create_pipeline_registry`). `get(pipeline_id)` returns a `_RegisteredPipeline`; `.definition` is a `PipelineDefinition` with `.plugin_id`/`.plugin_version`.
- `backend/app/pipelines/plugin.py:71` — `validate_plugin_parameters(definition, parameters)` raising `PLUGIN_PARAMETERS_INVALID`.
- `PipelineDefinition` (`backend/app/pipelines/base.py:24`) carries `model_release_required`, `technical_execution_capabilities`, `parameter_schema`.
- Existing tests: `backend/tests/test_plugin_registry.py`,
  `backend/tests/test_plugin_parameters_freeze.py`,
  `backend/tests/test_pipeline_read_model.py`.

### Migration / model patterns

- `backend/app/db/base.py:8` — `load_domain_models()` imports every model module for metadata registration.
- `backend/app/db/migrations.py:14` — `upgrade_dataset_benchmarks(engine)` uses `Model.__table__.create(engine, checkfirst=True)`; `run_additive_migrations()` (`:31`) is the single wiring point.
- `backend/app/benchmarks/model.py` — reference for `UniqueConstraint`, JSON columns, timezone-aware timestamps, and cascade relationships.
- Existing tests: `backend/tests/test_benchmark_models.py`,
  `backend/tests/test_m9_1_provenance_migrations.py`.

### Evaluation protocol

- `backend/app/benchmarks/service.py:57` — `_protocol_config_for(protocol)` is the frozen supported set (`PHYSICAL_TF_PROTOCOL_V1`, `PHYSICAL_TF_PROTOCOL_V2`); raises `UNSUPPORTED_EVALUATION_PROTOCOL`.
- `backend/app/benchmarks/schema.py:5` — `DEFAULT_PHYSICAL_TF_PROTOCOL = PHYSICAL_TF_PROTOCOL_V2`.

### Relevant test owners / fixtures

- `backend/tests/conftest.py` — `settings`, `client`, `session`.
- `backend/tests/benchmark_fixture.py` — `add_recording`, `add_ground_truth`, `add_run`.
- `backend/tests/executor_fixtures.py` — `FakeProvider`, `FakeRegistry`.
- `backend/tests/test_benchmark_models.py`, `test_benchmark_membership.py`,
  `test_benchmark_manifest.py` — manifest/membership regressions.
- `backend/tests/test_model_release.py`, `test_execution_certificate.py`,
  `test_executor_registry.py`, `test_plugin_registry.py`,
  `test_analysis_runs.py` — frozen-contract regressions.

### Test command conventions

- Single: `PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/<file> -v`
- Full: `PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests -q`

---

## File / Interface Map (before tasks)

### New files

| File | Owner task | Purpose |
|---|---|---|
| `backend/app/dataset_experiments/__init__.py` | 1 | package marker |
| `backend/app/dataset_experiments/model.py` | 1 | the three ORM models |
| `backend/app/dataset_experiments/schema.py` | 2 | create request + internal read models |
| `backend/app/dataset_experiments/service.py` | 2/3/4 | read models, creation, revalidation |
| `backend/tests/test_dataset_experiment_models.py` | 1 | model + migration tests |
| `backend/tests/test_dataset_experiment_read_model.py` | 2 | schema + derived-count read tests |
| `backend/tests/test_dataset_experiment_creation.py` | 3 | creation + atomicity + freeze tests |
| `backend/tests/test_dataset_experiment_revalidation.py` | 4 | frozen-identity drift tests |
| `backend/tests/test_dataset_experiment_regression.py` | 5 | import isolation + frozen-contract regression |

### Modified files

| File | Owner task | Change |
|---|---|---|
| `backend/app/db/base.py` | 1 | import `app.dataset_experiments.model` in `load_domain_models()` |
| `backend/app/db/migrations.py` | 1 | `upgrade_dataset_experiments(engine)` + wiring |
| `backend/app/benchmarks/service.py` | 3 | additive `resolve_protocol_config(evaluation_protocol) -> dict` public seam |

### Explicitly NOT touched in G1

`backend/app/analysis/service.py`, `backend/app/analysis/model.py`,
`backend/app/analysis/router.py`, `backend/app/remote_execution/*`,
`backend/app/pipelines/*`, `backend/app/main.py`, `backend/app/benchmarks/model.py`,
`backend/app/benchmarks/manifest.py`, any frontend file, any existing test.

---

# TASK 1 — Persistence models + additive migration

**Files:**
- Create: `backend/app/dataset_experiments/__init__.py`
- Create: `backend/app/dataset_experiments/model.py`
- Modify: `backend/app/db/base.py`
- Modify: `backend/app/db/migrations.py`
- Test: `backend/tests/test_dataset_experiment_models.py`

**Interfaces:**
- Consumes: `app.db.base.Base`, existing `recordings.id`, `analysis_runs.id`, `dataset_evaluations.id` tables, `app.db.migrations.run_additive_migrations`.
- Produces: `DatasetExperimentModel`, `DatasetExperimentItemModel`, `DatasetExperimentAttemptModel`; `upgrade_dataset_experiments(engine)`.

### Exact model definitions

```python
# backend/app/dataset_experiments/model.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DatasetExperimentModel(Base):
    __tablename__ = "dataset_experiments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Frozen dataset membership
    dataset_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    dataset_split: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    dataset_label_space: Mapped[str] = mapped_column(String(128), nullable=False)
    recording_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # Frozen scientific identity
    plugin_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    plugin_version: Mapped[str] = mapped_column(String(64), nullable=False)
    model_release_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    asset_manifest_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parameters_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    # Frozen execution identity
    executor: Mapped[str] = mapped_column(String(64), nullable=False)
    runtime_descriptor_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    evaluation_protocol: Mapped[str] = mapped_column(String(128), nullable=False)
    max_concurrency: Mapped[int] = mapped_column(Integer, nullable=False)

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)

    dataset_evaluation_id: Mapped[str | None] = mapped_column(
        ForeignKey("dataset_evaluations.id"), nullable=True, index=True
    )

    # Coordinator ownership (unused in G1; owned by G3+)
    coordinator_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    worker_pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    error_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    items: Mapped[list["DatasetExperimentItemModel"]] = relationship(
        back_populates="experiment",
        cascade="all, delete-orphan",
        order_by="DatasetExperimentItemModel.manifest_order",
    )


class DatasetExperimentItemModel(Base):
    __tablename__ = "dataset_experiment_items"
    __table_args__ = (
        UniqueConstraint("experiment_id", "recording_id", name="uq_dataset_experiment_recording"),
        UniqueConstraint("experiment_id", "manifest_order", name="uq_dataset_experiment_manifest_order"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    experiment_id: Mapped[str] = mapped_column(
        ForeignKey("dataset_experiments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    manifest_order: Mapped[int] = mapped_column(Integer, nullable=False)
    recording_id: Mapped[str] = mapped_column(
        ForeignKey("recordings.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)

    last_error_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    experiment: Mapped["DatasetExperimentModel"] = relationship(back_populates="items")
    attempts: Mapped[list["DatasetExperimentAttemptModel"]] = relationship(
        back_populates="item",
        cascade="all, delete-orphan",
        order_by="DatasetExperimentAttemptModel.attempt_number",
    )


class DatasetExperimentAttemptModel(Base):
    __tablename__ = "dataset_experiment_attempts"
    __table_args__ = (
        UniqueConstraint("experiment_item_id", "attempt_number", name="uq_dataset_experiment_attempt_number"),
        UniqueConstraint("analysis_run_id", name="uq_dataset_experiment_attempt_run"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    experiment_item_id: Mapped[str] = mapped_column(
        ForeignKey("dataset_experiment_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    analysis_run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.id"), nullable=False, index=True
    )
    launch_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    item: Mapped["DatasetExperimentItemModel"] = relationship(back_populates="attempts")
```

### Exact migration wiring

```python
# backend/app/db/migrations.py (add)
def upgrade_dataset_experiments(engine) -> None:
    from app.dataset_experiments.model import (
        DatasetExperimentAttemptModel,
        DatasetExperimentItemModel,
        DatasetExperimentModel,
    )

    DatasetExperimentModel.__table__.create(engine, checkfirst=True)
    DatasetExperimentItemModel.__table__.create(engine, checkfirst=True)
    DatasetExperimentAttemptModel.__table__.create(engine, checkfirst=True)


def run_additive_migrations(engine) -> None:
    upgrade_recording_external(engine)
    upgrade_dataset_benchmarks(engine)   # creates dataset_evaluations first (FK target)
    upgrade_m9_1_provenance(engine)
    upgrade_dataset_experiments(engine)
```

`backend/app/db/base.py` gains one line inside `load_domain_models()`:

```python
from app.dataset_experiments import model as _dataset_experiments  # noqa: F401
```

### Steps

- [ ] **Write failing test** `backend/tests/test_dataset_experiment_models.py`:

```python
from sqlalchemy import inspect

from app.analysis.model import AnalysisRunModel
from app.db.base import Base, load_domain_models
from app.db.migrations import run_additive_migrations
from app.db.session import Database

_NEW_TABLES = {"dataset_experiments", "dataset_experiment_items", "dataset_experiment_attempts"}
_ANALYSIS_RUN_COLUMNS = {
    "id", "recording_id", "pipeline_id", "pipeline_version", "executor", "status",
    "parameters_json", "execution_metadata_json", "hardware_info_json", "started_at",
    "finished_at", "error_type", "error_message", "worker_pid", "created_at",
}


def _fresh_database(settings) -> Database:
    database = Database(settings.database_url)
    load_domain_models()
    Base.metadata.create_all(database.engine)
    run_additive_migrations(database.engine)
    return database


def test_three_tables_are_registered_and_created(settings):
    database = _fresh_database(settings)
    names = set(inspect(database.engine).get_table_names())
    assert _NEW_TABLES <= names


def test_additive_migration_creates_tables_for_existing_v1_database(settings):
    database = Database(settings.database_url)
    load_domain_models()
    Base.metadata.tables["recordings"].create(database.engine, checkfirst=True)
    Base.metadata.tables["analysis_runs"].create(database.engine, checkfirst=True)
    run_additive_migrations(database.engine)
    names = set(inspect(database.engine).get_table_names())
    assert _NEW_TABLES <= names


def test_additive_migration_is_idempotent(settings):
    database = Database(settings.database_url)
    load_domain_models()
    Base.metadata.tables["recordings"].create(database.engine, checkfirst=True)
    Base.metadata.tables["analysis_runs"].create(database.engine, checkfirst=True)
    run_additive_migrations(database.engine)
    run_additive_migrations(database.engine)  # must not raise
    names = set(inspect(database.engine).get_table_names())
    assert _NEW_TABLES <= names


def test_foreign_keys_exist(settings):
    database = _fresh_database(settings)
    inspector = inspect(database.engine)

    def targets(table):
        return {
            (fk["constrained_columns"][0], fk["referred_table"])
            for fk in inspector.get_foreign_keys(table)
        }

    assert ("experiment_id", "dataset_experiments") in targets("dataset_experiment_items")
    assert ("recording_id", "recordings") in targets("dataset_experiment_items")
    assert ("experiment_item_id", "dataset_experiment_items") in targets("dataset_experiment_attempts")
    assert ("analysis_run_id", "analysis_runs") in targets("dataset_experiment_attempts")
    assert ("dataset_evaluation_id", "dataset_evaluations") in targets("dataset_experiments")


def test_item_and_attempt_unique_constraints_exist(settings):
    database = _fresh_database(settings)
    inspector = inspect(database.engine)

    item_columns = {
        tuple(c["column_names"]) for c in inspector.get_unique_constraints("dataset_experiment_items")
    }
    attempt_columns = {
        tuple(c["column_names"]) for c in inspector.get_unique_constraints("dataset_experiment_attempts")
    }
    assert ("experiment_id", "recording_id") in item_columns
    assert ("experiment_id", "manifest_order") in item_columns
    assert ("experiment_item_id", "attempt_number") in attempt_columns
    assert ("analysis_run_id",) in attempt_columns


def test_analysis_run_schema_unchanged(settings):
    database = _fresh_database(settings)
    assert set(AnalysisRunModel.__table__.columns.keys()) == _ANALYSIS_RUN_COLUMNS
    assert not any("experiment" in column for column in AnalysisRunModel.__table__.columns.keys())


def test_no_persisted_aggregate_or_derived_authority_columns(settings):
    database = _fresh_database(settings)
    inspector = inspect(database.engine)
    experiment_columns = {c["name"] for c in inspector.get_columns("dataset_experiments")}
    item_columns = {c["name"] for c in inspector.get_columns("dataset_experiment_items")}
    assert experiment_columns.isdisjoint(
        {"queued_items", "running_items", "completed_items", "failed_items", "attempt_count"}
    )
    assert item_columns.isdisjoint({"current_analysis_run_id", "attempt_count"})
```

- [ ] **Run exact RED command:**

```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_dataset_experiment_models.py -v
```

Expected RED: collection failure (`ModuleNotFoundError: app.dataset_experiments`) / missing tables.

- [ ] **Implement minimal code:** create the package, `model.py`, `base.py` import, `migrations.py` function + wiring exactly as specified above.

- [ ] **Run exact GREEN command:** same pytest invocation. Expected: all pass.

- [ ] **Regression:**

```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_benchmark_models.py \
  backend/tests/test_m9_1_provenance_migrations.py \
  backend/tests/test_m9_1_provenance_migrations.py \
  backend/tests/test_benchmark_manifest.py -q
```

- [ ] **Commit checkpoint:** `feat: add dataset experiment persistence models and migration`

---

# TASK 2 — G1 schemas / internal read models

**Files:**
- Create: `backend/app/dataset_experiments/schema.py`
- Create: `backend/app/dataset_experiments/service.py` (read-only skeleton: constructor + `get_experiment` / `list_items` / `list_attempts` + derived counts)
- Test: `backend/tests/test_dataset_experiment_read_model.py`

**Interfaces:**
- Consumes: Task 1 models; `RecordingModel`; `sqlalchemy.select/func`; `PlatformError`.
- Produces:
  - `DatasetExperimentCreate`
  - `DatasetExperimentRead`
  - `DatasetExperimentItemRead`
  - `DatasetExperimentAttemptRead`
  - `DatasetExperimentService.__init__(session, registry, model_release_store, executor_registry)`
  - `DatasetExperimentService.get_experiment(experiment_id) -> DatasetExperimentRead`
  - `DatasetExperimentService.list_items(experiment_id) -> list[DatasetExperimentItemRead]`
  - `DatasetExperimentService.list_attempts(item_id) -> list[DatasetExperimentAttemptRead]`
  - `DatasetExperimentService._to_read(experiment) -> DatasetExperimentRead`

### Exact schema

```python
# backend/app/dataset_experiments/schema.py
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from app.analysis.schema import MODEL_RELEASE_ID_PATTERN
from app.benchmarks.schema import DEFAULT_PHYSICAL_TF_PROTOCOL

class DatasetExperimentCreate(BaseModel):
    """G1 creation request.

    Frozen identity fields (``recording_manifest_hash``,
    ``asset_manifest_sha256``, ``runtime_descriptor_json``) are deliberately
    absent and additionally rejected via ``extra="forbid"``: the platform
    resolves/freezes them.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    dataset_name: str = Field(min_length=1)
    dataset_split: str = Field(min_length=1)
    dataset_label_space: str = Field(min_length=1)

    plugin_id: str = Field(min_length=1)
    plugin_version: str = Field(min_length=1)
    model_release_id: Annotated[str, Field(pattern=MODEL_RELEASE_ID_PATTERN)] | None = None

    executor: str = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)

    evaluation_protocol: str = Field(default=DEFAULT_PHYSICAL_TF_PROTOCOL, min_length=1)
    max_concurrency: int = Field(ge=1)

class DatasetExperimentItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    experiment_id: str
    manifest_order: int
    recording_id: str
    recording_name: str
    status: str
    last_error_type: str | None
    last_error_message: str | None
    created_at: datetime
    updated_at: datetime

class DatasetExperimentAttemptRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    experiment_item_id: str
    attempt_number: int
    analysis_run_id: str
    launch_requested_at: datetime | None
    created_at: datetime

class DatasetExperimentRead(BaseModel):
    """Internal read model. Derived counts are computed, never persisted."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str

    dataset_name: str
    dataset_split: str
    dataset_label_space: str
    recording_manifest_hash: str

    plugin_id: str
    plugin_version: str
    model_release_id: str | None
    asset_manifest_sha256: str | None
    parameters_json: dict[str, Any]

    executor: str
    runtime_descriptor_json: dict[str, Any]

    evaluation_protocol: str
    max_concurrency: int

    status: str
    dataset_evaluation_id: str | None

    error_type: str | None
    error_message: str | None

    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None

    # Derived from Item rows (never persisted)
    expected_items: int
    queued_items: int
    running_items: int
    completed_items: int
    failed_items: int
    attempt_count: int
```

### Exact service read skeleton

```python
# backend/app/dataset_experiments/service.py
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import PlatformError
from app.dataset_experiments.model import (
    DatasetExperimentAttemptModel,
    DatasetExperimentItemModel,
    DatasetExperimentModel,
)
from app.dataset_experiments.schema import (
    DatasetExperimentAttemptRead,
    DatasetExperimentItemRead,
    DatasetExperimentRead,
)
from app.recordings.model import RecordingModel

_ITEM_STATUSES = ("queued", "running", "completed", "failed")


class DatasetExperimentService:
    def __init__(self, session: Session, registry, model_release_store, executor_registry) -> None:
        self.session = session
        self.registry = registry
        self.model_release_store = model_release_store
        self.executor_registry = executor_registry

    # ---------- read models ----------

    def _get(self, experiment_id: str) -> DatasetExperimentModel:
        experiment = self.session.get(DatasetExperimentModel, experiment_id)
        if experiment is None:
            raise PlatformError("DATASET_EXPERIMENT_NOT_FOUND", "Dataset experiment was not found.", 404)
        return experiment

    def get_experiment(self, experiment_id: str) -> DatasetExperimentRead:
        return self._to_read(self._get(experiment_id))

    def list_items(self, experiment_id: str) -> list[DatasetExperimentItemRead]:
        self._get(experiment_id)
        rows = self.session.execute(
            select(DatasetExperimentItemModel, RecordingModel.name)
            .join(RecordingModel, DatasetExperimentItemModel.recording_id == RecordingModel.id)
            .where(DatasetExperimentItemModel.experiment_id == experiment_id)
            .order_by(DatasetExperimentItemModel.manifest_order)
        ).all()
        return [
            DatasetExperimentItemRead(
                id=item.id,
                experiment_id=item.experiment_id,
                manifest_order=item.manifest_order,
                recording_id=item.recording_id,
                recording_name=recording_name,
                status=item.status,
                last_error_type=item.last_error_type,
                last_error_message=item.last_error_message,
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
            for item, recording_name in rows
        ]

    def list_attempts(self, item_id: str) -> list[DatasetExperimentAttemptRead]:
        item = self.session.get(DatasetExperimentItemModel, item_id)
        if item is None:
            raise PlatformError("DATASET_EXPERIMENT_ITEM_NOT_FOUND", "Dataset experiment item was not found.", 404)
        rows = self.session.scalars(
            select(DatasetExperimentAttemptModel)
            .where(DatasetExperimentAttemptModel.experiment_item_id == item_id)
            .order_by(DatasetExperimentAttemptModel.attempt_number)
        ).all()
        return [DatasetExperimentAttemptRead.model_validate(row) for row in rows]

    def _to_read(self, experiment: DatasetExperimentModel) -> DatasetExperimentRead:
        status_counts = dict(
            self.session.execute(
                select(DatasetExperimentItemModel.status, func.count(DatasetExperimentItemModel.id))
                .where(DatasetExperimentItemModel.experiment_id == experiment.id)
                .group_by(DatasetExperimentItemModel.status)
            ).all()
        )
        attempt_count = int(
            self.session.scalar(
                select(func.count(DatasetExperimentAttemptModel.id))
                .join(
                    DatasetExperimentItemModel,
                    DatasetExperimentAttemptModel.experiment_item_id == DatasetExperimentItemModel.id,
                )
                .where(DatasetExperimentItemModel.experiment_id == experiment.id)
            )
            or 0
        )
        return DatasetExperimentRead(
            id=experiment.id,
            name=experiment.name,
            dataset_name=experiment.dataset_name,
            dataset_split=experiment.dataset_split,
            dataset_label_space=experiment.dataset_label_space,
            recording_manifest_hash=experiment.recording_manifest_hash,
            plugin_id=experiment.plugin_id,
            plugin_version=experiment.plugin_version,
            model_release_id=experiment.model_release_id,
            asset_manifest_sha256=experiment.asset_manifest_sha256,
            parameters_json=dict(experiment.parameters_json or {}),
            executor=experiment.executor,
            runtime_descriptor_json=dict(experiment.runtime_descriptor_json or {}),
            evaluation_protocol=experiment.evaluation_protocol,
            max_concurrency=experiment.max_concurrency,
            status=experiment.status,
            dataset_evaluation_id=experiment.dataset_evaluation_id,
            error_type=experiment.error_type,
            error_message=experiment.error_message,
            created_at=experiment.created_at,
            started_at=experiment.started_at,
            completed_at=experiment.completed_at,
            expected_items=sum(status_counts.values()),
            queued_items=status_counts.get("queued", 0),
            running_items=status_counts.get("running", 0),
            completed_items=status_counts.get("completed", 0),
            failed_items=status_counts.get("failed", 0),
            attempt_count=attempt_count,
        )
```

### Steps

- [ ] **Write failing test** `backend/tests/test_dataset_experiment_read_model.py`:

```python
import pytest
from pydantic import ValidationError

from benchmark_fixture import add_ground_truth, add_recording

from app.analysis.model import AnalysisRunModel
from app.core.errors import PlatformError
from app.dataset_experiments.model import (
    DatasetExperimentAttemptModel,
    DatasetExperimentItemModel,
    DatasetExperimentModel,
)
from app.dataset_experiments.schema import DatasetExperimentCreate
from app.dataset_experiments.service import DatasetExperimentService


def test_create_request_rejects_platform_frozen_fields():
    base = dict(
        name="e", dataset_name="SpaceNet", dataset_split="test",
        dataset_label_space="spacenet_14", plugin_id="p", plugin_version="1.0",
        executor="local_cpu", max_concurrency=1,
    )
    DatasetExperimentCreate(**base)  # accepts
    for forbidden in ("recording_manifest_hash", "asset_manifest_sha256", "runtime_descriptor_json"):
        with pytest.raises(ValidationError):
            DatasetExperimentCreate(**base, **{forbidden: "x"})


def test_create_request_requires_max_concurrency_at_least_one():
    with pytest.raises(ValidationError):
        DatasetExperimentCreate(
            name="e", dataset_name="SpaceNet", dataset_split="test",
            dataset_label_space="spacenet_14", plugin_id="p", plugin_version="1.0",
            executor="local_cpu", max_concurrency=0,
        )


def test_create_request_defaults_protocol_and_parameters():
    payload = DatasetExperimentCreate(
        name="e", dataset_name="SpaceNet", dataset_split="test",
        dataset_label_space="spacenet_14", plugin_id="p", plugin_version="1.0",
        executor="local_cpu", max_concurrency=2,
    )
    assert payload.parameters == {}
    assert payload.evaluation_protocol == "physical_tf_detection_ap_v2"
    assert payload.model_release_id is None


def _seed_experiment(client):
    database = client.app.state.database
    with database.session_factory() as session:
        add_recording(session, recording_id="rec_a", name="a")
        add_recording(session, recording_id="rec_b", name="b")
        add_recording(session, recording_id="rec_c", name="c")
        add_ground_truth(session, gt_id="gt_a", recording_id="rec_a", class_id=9,
                         class_name="LoRa 250kHz", t0=0.01, t1=0.02,
                         f0=2_440_600_000.0, f1=2_440_700_000.0)
        add_ground_truth(session, gt_id="gt_b", recording_id="rec_b", class_id=6,
                         class_name="BLE LE1M", t0=0.03, t1=0.04,
                         f0=2_440_800_000.0, f1=2_440_900_000.0)
        add_ground_truth(session, gt_id="gt_c", recording_id="rec_c", class_id=9,
                         class_name="LoRa 250kHz", t0=0.05, t1=0.06,
                         f0=2_441_000_000.0, f1=2_441_100_000.0)
        session.add(DatasetExperimentModel(
            id="exp_1", name="e", dataset_name="SpaceNet", dataset_split="test",
            dataset_label_space="spacenet_14", recording_manifest_hash="a" * 64,
            plugin_id="p", plugin_version="1.0", model_release_id=None,
            asset_manifest_sha256=None, executor="local_cpu",
            runtime_descriptor_json={"executor": "local_cpu", "device_type": "cpu",
                                     "device_index": None, "precision": "float32",
                                     "environment_ref": "x", "environment_label": "x"},
            parameters_json={}, evaluation_protocol="physical_tf_detection_ap_v2",
            max_concurrency=2, status="pending",
        ))
        session.add_all([
            DatasetExperimentItemModel(id="ei_1", experiment_id="exp_1", manifest_order=0,
                                       recording_id="rec_a", status="completed"),
            DatasetExperimentItemModel(id="ei_2", experiment_id="exp_1", manifest_order=1,
                                       recording_id="rec_b", status="failed"),
            DatasetExperimentItemModel(id="ei_3", experiment_id="exp_1", manifest_order=2,
                                       recording_id="rec_c", status="running"),
        ])
        session.commit()
    return database


def test_counts_are_derived_from_item_rows(client):
    database = _seed_experiment(client)
    with database.session_factory() as session:
        read = DatasetExperimentService(session, None, None, None).get_experiment("exp_1")
    assert (read.expected_items, read.queued_items, read.running_items,
            read.completed_items, read.failed_items) == (3, 0, 1, 1, 1)
    assert read.attempt_count == 0


def test_attempt_count_is_derived(client):
    database = _seed_experiment(client)
    with database.session_factory() as session:
        session.add(AnalysisRunModel(
            id="run_x", recording_id="rec_a", pipeline_id="p", pipeline_version="1.0",
            executor="local_cpu", status="completed", parameters_json={},
        ))
        session.add(DatasetExperimentAttemptModel(
            id="ea_1", experiment_item_id="ei_1", attempt_number=1,
            analysis_run_id="run_x", launch_requested_at=None,
        ))
        session.commit()
    with database.session_factory() as session:
        read = DatasetExperimentService(session, None, None, None).get_experiment("exp_1")
    assert read.attempt_count == 1


def test_read_does_not_persist_counts():
    columns = set(DatasetExperimentModel.__table__.columns.keys())
    item_columns = set(DatasetExperimentItemModel.__table__.columns.keys())
    assert columns.isdisjoint(
        {"queued_items", "running_items", "completed_items", "failed_items", "attempt_count"}
    )
    assert item_columns.isdisjoint({"current_analysis_run_id", "attempt_count"})


def test_get_missing_experiment_raises(client):
    with client.app.state.database.session_factory() as session:
        with pytest.raises(PlatformError) as exc:
            DatasetExperimentService(session, None, None, None).get_experiment("missing")
    assert exc.value.code == "DATASET_EXPERIMENT_NOT_FOUND"
```

- [ ] **Run exact RED command:**

```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_dataset_experiment_read_model.py -v
```

Expected RED: collection failure (`ModuleNotFoundError: app.dataset_experiments.service`).

- [ ] **Implement minimal code:** `schema.py` and the read-only `service.py` skeleton exactly as specified.

- [ ] **Run exact GREEN command:** same pytest invocation. Expected: all pass.

- [ ] **Regression:**

```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_analysis_runs.py \
  backend/tests/test_benchmark_models.py -q
```

- [ ] **Commit checkpoint:** `feat: add dataset experiment schemas and derived read models`

---

# TASK 3 — Frozen Experiment creation + identity freezing

**Files:**
- Modify: `backend/app/dataset_experiments/service.py` (add creation)
- Modify: `backend/app/benchmarks/service.py` (add `resolve_protocol_config`)
- Test: `backend/tests/test_dataset_experiment_creation.py`

**Interfaces:**
- Consumes: `DatasetBenchmarkService.prepare_manifest`; `PipelineRegistry.get`; `validate_plugin_parameters`; `ModelReleaseStore.resolve`; `ExecutorRegistry.provider` / `.certified_capability`; `provider.runtime_descriptor().to_metadata()`; `app.benchmarks.service.resolve_protocol_config`.
- Produces:
  - `resolve_protocol_config(evaluation_protocol: str) -> dict` (public seam over the frozen protocol set)
  - `DatasetExperimentService.create_experiment(*, name, dataset_name, dataset_split, dataset_label_space, plugin_id, plugin_version, executor, parameters, evaluation_protocol, max_concurrency, model_release_id=None) -> DatasetExperimentModel`
  - `DatasetExperimentService._manifest_preview(dataset_name, dataset_split, dataset_label_space) -> ManifestPreview`
  - `DatasetExperimentService._resolve_definition(plugin_id, plugin_version) -> PipelineDefinition`
  - `DatasetExperimentService._resolve_release(definition, requested) -> ResolvedModelRelease | None`
  - `DatasetExperimentService._validate_execution(definition, model_release_id, executor) -> tuple[ExecutorProvider, RuntimeDescriptor]`
  - `DatasetExperimentService._new_item_rows(experiment_id, entries) -> list[DatasetExperimentItemModel]`

### Exact benchmark protocol seam (additive)

```python
# backend/app/benchmarks/service.py (add near _protocol_config_for)

def resolve_protocol_config(evaluation_protocol: str) -> dict:
    """Public read seam over the frozen supported evaluation-protocol set.

    Reuses ``_protocol_config_for`` so the supported-protocol authority stays
    singular; raises ``UNSUPPORTED_EVALUATION_PROTOCOL`` for unknown values.
    """
    return _protocol_config_for(evaluation_protocol)
```

### Exact creation implementation additions

```python
# backend/app/dataset_experiments/service.py (imports added)
from uuid import uuid4

from app.benchmarks.service import DatasetBenchmarkService, resolve_protocol_config
from app.pipelines.plugin import validate_plugin_parameters

# methods added to DatasetExperimentService

    # ---------- reversible authority seams ----------

    def _manifest_preview(self, dataset_name, dataset_split, dataset_label_space):
        return DatasetBenchmarkService(self.session).prepare_manifest(
            dataset_name, dataset_split, dataset_label_space
        )

    def _resolve_definition(self, plugin_id, plugin_version):
        try:
            pipeline = self.registry.get(plugin_id)
        except PlatformError as exc:
            raise PlatformError(
                "PLUGIN_NOT_FOUND",
                f"Plugin '{plugin_id}' is not registered.",
            ) from exc
        definition = pipeline.definition
        if definition.plugin_version != plugin_version:
            raise PlatformError(
                "PLUGIN_NOT_FOUND",
                f"Plugin '{plugin_id}' version '{plugin_version}' is not registered; "
                f"registered version is '{definition.plugin_version}'.",
            )
        return definition

    def _resolve_release(self, definition, requested):
        """Mirrors AnalysisService._resolve_release; single authority is ModelReleaseStore.resolve."""
        if not definition.model_release_required:
            if requested is not None:
                raise PlatformError(
                    "MODEL_RELEASE_MISMATCH",
                    "This plugin does not accept a model release identity.",
                )
            return None
        if self.model_release_store is None:
            raise PlatformError(
                "EXECUTION_CAPABILITY_UNAVAILABLE", "Model release store is not configured."
            )
        return self.model_release_store.resolve(
            definition.plugin_id, definition.plugin_version, requested
        )

    def _validate_execution(self, definition, model_release_id, executor):
        """Deployment identity only. Never probes a Recording, never calls availability_for."""
        provider = self.executor_registry.provider(executor)
        if self.executor_registry.certified_capability(
            definition, model_release_id, executor
        ) is None:
            raise PlatformError(
                "EXECUTION_NOT_CERTIFIED",
                "The requested executor has no exact platform certificate for this "
                "release and runtime.",
            )
        return provider, provider.runtime_descriptor()

    # ---------- creation ----------

    def create_experiment(
        self,
        *,
        name,
        dataset_name,
        dataset_split,
        dataset_label_space,
        plugin_id,
        plugin_version,
        executor,
        parameters,
        evaluation_protocol,
        max_concurrency,
        model_release_id=None,
    ):
        if max_concurrency < 1:
            raise PlatformError(
                "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                "max_concurrency must be >= 1.",
                422,
            )

        manifest = self._manifest_preview(dataset_name, dataset_split, dataset_label_space)
        definition = self._resolve_definition(plugin_id, plugin_version)
        validate_plugin_parameters(definition, parameters)
        resolved_release = self._resolve_release(definition, model_release_id)
        resolve_protocol_config(evaluation_protocol)

        frozen_release_id = (
            None if resolved_release is None else resolved_release.release.model_release_id
        )
        frozen_asset_sha = (
            None if resolved_release is None else resolved_release.manifest.asset_manifest_sha256
        )
        provider, descriptor = self._validate_execution(definition, frozen_release_id, executor)

        experiment = DatasetExperimentModel(
            id=f"exp_{uuid4().hex}",
            name=name,
            dataset_name=dataset_name,
            dataset_split=dataset_split,
            dataset_label_space=dataset_label_space,
            recording_manifest_hash=manifest.recording_manifest_hash,
            plugin_id=definition.plugin_id,
            plugin_version=definition.plugin_version,
            model_release_id=frozen_release_id,
            asset_manifest_sha256=frozen_asset_sha,
            parameters_json=dict(parameters),
            executor=provider.name,
            runtime_descriptor_json=descriptor.to_metadata(),
            evaluation_protocol=evaluation_protocol,
            max_concurrency=max_concurrency,
            status="pending",
        )
        try:
            self.session.add(experiment)
            item_rows = self._new_item_rows(experiment.id, manifest.entries)
            self.session.add_all(item_rows)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        self.session.refresh(experiment)
        return experiment

    def _new_item_rows(self, experiment_id, entries):
        return [
            DatasetExperimentItemModel(
                id=f"expitem_{uuid4().hex}",
                experiment_id=experiment_id,
                manifest_order=entry.manifest_order,
                recording_id=entry.recording_id,
                status="queued",
            )
            for entry in entries
        ]
```

`executor` is frozen from `provider.name` (matching `AnalysisService._create_local_run` at `backend/app/analysis/service.py:163`, which stores `provider.name`). `_validate_execution` returns the provider and the descriptor whose `executor` equals the provider name (asserted in tests).

### Steps

- [ ] **Write failing test** `backend/tests/test_dataset_experiment_creation.py`.

Test scaffolding (reused across creation tests):

```python
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmark_fixture import add_ground_truth, add_recording

from app.analysis.model import AnalysisRunModel
from app.benchmarks.model import DatasetEvaluationModel
from app.benchmarks.service import DatasetBenchmarkService
from app.core.errors import PlatformError
from app.dataset_experiments.model import (
    DatasetExperimentAttemptModel,
    DatasetExperimentItemModel,
    DatasetExperimentModel,
)
from app.dataset_experiments.service import DatasetExperimentService
from app.pipelines.base import ExecutionCapability, Pipeline, PipelineDefinition, PipelineOutput, RecordingInput
from app.pipelines.plugin import PluginDeclaration
from app.pipelines.registry import PipelineRegistry
from app.remote_execution.model_release import ResolvedModelRelease
from app.remote_execution.runtime import ExecutionCertificate, ExecutionCertificateStore, ExecutorRegistry
from app.remote_execution.runtime import RuntimeDescriptor

from executor_fixtures import FakeProvider

PLUGIN_ID = "exp_test_plugin"
PLUGIN_VERSION = "1.0"
LOCAL_REF = "local:test:cpu:1"
REMOTE_REF = "remote:test:cuda:1"
MANIFEST_SHA = "b" * 64

_PARAM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"threshold": {"type": "number"}},
}


class ExpTestPipeline(Pipeline):
    def __init__(self, *, release_required=False, version=PLUGIN_VERSION):
        self._release_required = release_required
        self._version = version

    @property
    def definition(self) -> PipelineDefinition:
        return PipelineDefinition(
            id=PLUGIN_ID,
            name="Experiment Test",
            version=self._version,
            label_space="spacenet_14",
            recommended_device="CPU",
            cpu_supported=True,
            stages=(),
            inspectable_stages=(),
            task_capability="classification",
            executors_supported=("local_cpu",),
            recommended_executor="local_cpu",
            model_release_required=self._release_required,
            parameter_schema=_PARAM_SCHEMA,
            technical_execution_capabilities=(
                ExecutionCapability("local_cpu", "cpu", "float32"),
            ),
        )

    def run(self, recording, parameters, workspace) -> PipelineOutput:
        raise AssertionError("must not execute")


class FakeReleaseStore:
    def __init__(self, *, release_id="golden", manifest_sha=MANIFEST_SHA):
        self.release_id = release_id
        self.manifest_sha = manifest_sha
        self.resolve_calls = []
        self.reverse_calls = 0

    def resolve(self, plugin_id, plugin_version, requested):
        self.resolve_calls.append((plugin_id, plugin_version, requested))
        rid = requested or self.release_id
        release = SimpleNamespace(
            plugin_id=plugin_id, plugin_version=plugin_version, model_release_id=rid,
            asset_manifest_sha256=self.manifest_sha,
        )
        manifest = SimpleNamespace(asset_manifest_sha256=self.manifest_sha)
        return ResolvedModelRelease(release=release, manifest=manifest)

    def resolve_by_manifest_sha(self, *args, **kwargs):
        self.reverse_calls += 1
        raise AssertionError("G1 must use resolve() only")


class ExplodingProbe:
    def __init__(self):
        self.calls = []

    def availability(self, recording, pipeline, source_data_sha256, model_release=None):
        self.calls.append(recording.id)
        raise AssertionError("G1 must not probe a Recording")


def _certificate(*, release_required, model_release_id, executor, runtime_ref):
    return ExecutionCertificate(
        plugin_id=PLUGIN_ID, plugin_version=PLUGIN_VERSION,
        model_release_id=model_release_id if release_required else None,
        executor=executor, device_type="cpu", precision="float32",
        runtime_ref=runtime_ref, evidence_ref="g1-test",
    )


def _registry(*, release_required=False, certs=None, with_probe=False):
    probe = ExplodingProbe() if with_probe else None
    provider = FakeProvider("local_cpu", runtime_ref=LOCAL_REF, probe=probe)
    if certs is None:
        certs = [_certificate(
            release_required=release_required,
            model_release_id="golden",
            executor="local_cpu",
            runtime_ref=LOCAL_REF,
        )]
    return ExecutorRegistry({"local_cpu": provider}, ExecutionCertificateStore(list(certs)))


def _service(client, *, pipeline=None, store=None, registry=None):
    session = client.app.state.database.session_factory()
    return DatasetExperimentService(
        session,
        PipelineRegistry([(pipeline or ExpTestPipeline())]),
        store or FakeReleaseStore(),
        registry or _registry(),
    )


def _seed_dataset(client, *, count=3):
    database = client.app.state.database
    with database.session_factory() as session:
        for index in range(count):
            rid = f"rec_{index}"
            add_recording(session, recording_id=rid, name=f"name_{index}")
            add_ground_truth(session, gt_id=f"gt_{index}", recording_id=rid, class_id=9,
                             class_name="LoRa 250kHz", t0=0.01, t1=0.02,
                             f0=2_440_600_000.0, f1=2_440_700_000.0)
        session.commit()
    return database


def _create(service, **overrides):
    kwargs = dict(
        name="experiment", dataset_name="SpaceNet", dataset_split="test",
        dataset_label_space="spacenet_14", plugin_id=PLUGIN_ID,
        plugin_version=PLUGIN_VERSION, executor="local_cpu",
        parameters={}, evaluation_protocol="physical_tf_detection_ap_v2",
        max_concurrency=1,
    )
    kwargs.update(overrides)
    return service.create_experiment(**kwargs)
```

Test bodies:

```python
def test_creation_freezes_exact_items_in_manifest_order(client):
    _seed_dataset(client)
    service = _service(client)
    experiment = _create(service)
    with client.app.state.database.session_factory() as session:
        items = session.query(DatasetExperimentItemModel).order_by(
            DatasetExperimentItemModel.manifest_order
        ).all()
        expected = DatasetBenchmarkService(session).prepare_manifest(
            "SpaceNet", "test", "spacenet_14"
        )
    assert experiment.status == "pending"
    assert len(items) == expected.expected_recordings == 3
    assert [i.manifest_order for i in items] == [e.manifest_order for e in expected.entries]
    assert [i.recording_id for i in items] == [e.recording_id for e in expected.entries]
    assert all(i.status == "queued" for i in items)
    assert experiment.recording_manifest_hash == expected.recording_manifest_hash


def test_creation_creates_zero_attempts_and_zero_analysis_runs_and_no_evaluation(client):
    _seed_dataset(client)
    service = _service(client)
    _create(service)
    with client.app.state.database.session_factory() as session:
        assert session.query(DatasetExperimentAttemptModel).count() == 0
        assert session.query(AnalysisRunModel).count() == 0
        assert session.query(DatasetEvaluationModel).count() == 0


def test_creation_ignores_historical_completed_runs(client):
    _seed_dataset(client)
    with client.app.state.database.session_factory() as session:
        session.add(AnalysisRunModel(
            id="run_old", recording_id="rec_0", pipeline_id=PLUGIN_ID,
            pipeline_version=PLUGIN_VERSION, executor="local_cpu", status="completed",
            parameters_json={},
        ))
        session.commit()
    service = _service(client)
    experiment = _create(service)
    with client.app.state.database.session_factory() as session:
        assert session.query(AnalysisRunModel).count() == 1
        assert session.query(DatasetExperimentAttemptModel).count() == 0
    assert experiment.dataset_evaluation_id is None


def test_creation_freezes_parameters_and_runtime_descriptor(client):
    _seed_dataset(client)
    service = _service(client, registry=_registry(with_probe=True))
    experiment = _create(service, parameters={"threshold": 0.5})
    assert experiment.parameters_json == {"threshold": 0.5}
    assert experiment.executor == "local_cpu"
    assert experiment.runtime_descriptor_json == FakeProvider("local_cpu", runtime_ref=LOCAL_REF).runtime_descriptor().to_metadata()


def test_creation_does_not_probe_any_recording(client):
    _seed_dataset(client)
    registry = _registry(with_probe=True)
    probe = registry.provider("local_cpu")._probe
    _service(client, registry=registry).create_experiment(
        name="experiment", dataset_name="SpaceNet", dataset_split="test",
        dataset_label_space="spacenet_14", plugin_id=PLUGIN_ID,
        plugin_version=PLUGIN_VERSION, executor="local_cpu", parameters={},
        evaluation_protocol="physical_tf_detection_ap_v2", max_concurrency=1,
    )
    assert probe.calls == []


def test_creation_freezes_release_id_and_manifest_sha(client):
    _seed_dataset(client)
    store = FakeReleaseStore()
    service = _service(client, pipeline=ExpTestPipeline(release_required=True),
                      store=store, registry=_registry(release_required=True))
    experiment = _create(service, model_release_id="golden")
    assert experiment.model_release_id == "golden"
    assert experiment.asset_manifest_sha256 == MANIFEST_SHA
    assert store.resolve_calls == [(PLUGIN_ID, PLUGIN_VERSION, "golden")]
    assert store.reverse_calls == 0


def test_creation_release_less_keeps_release_fields_null(client):
    _seed_dataset(client)
    service = _service(client)
    experiment = _create(service)
    assert experiment.model_release_id is None
    assert experiment.asset_manifest_sha256 is None


def test_creation_rejects_release_for_release_less_plugin(client):
    _seed_dataset(client)
    service = _service(client)
    with pytest.raises(PlatformError) as exc:
        _create(service, model_release_id="golden")
    assert exc.value.code == "MODEL_RELEASE_MISMATCH"


def test_creation_rejects_max_concurrency_below_one(client):
    _seed_dataset(client)
    service = _service(client)
    with pytest.raises(PlatformError) as exc:
        _create(service, max_concurrency=0)
    assert exc.value.code == "DATASET_EXPERIMENT_INVARIANT_VIOLATION"


def test_creation_rejects_empty_dataset(client):
    service = _service(client)
    with pytest.raises(PlatformError) as exc:
        _create(service)
    assert exc.value.code == "DATASET_SNAPSHOT_EMPTY"


def test_creation_rejects_wrong_plugin_version(client):
    _seed_dataset(client)
    service = _service(client)
    with pytest.raises(PlatformError) as exc:
        _create(service, plugin_version="9.9")
    assert exc.value.code == "PLUGIN_NOT_FOUND"


def test_creation_rejects_invalid_parameters(client):
    _seed_dataset(client)
    service = _service(client)
    with pytest.raises(PlatformError) as exc:
        _create(service, parameters={"unknown": 1})
    assert exc.value.code == "PLUGIN_PARAMETERS_INVALID"


def test_creation_rejects_unsupported_executor(client):
    _seed_dataset(client)
    service = _service(client)
    with pytest.raises(PlatformError) as exc:
        _create(service, executor="remote_gpu")
    assert exc.value.code == "EXECUTION_CAPABILITY_UNAVAILABLE"


def test_creation_rejects_missing_exact_certificate(client):
    _seed_dataset(client)
    service = _service(client, registry=_registry(certs=[]))
    with pytest.raises(PlatformError) as exc:
        _create(service)
    assert exc.value.code == "EXECUTION_NOT_CERTIFIED"


def test_creation_rejects_unknown_evaluation_protocol(client):
    _seed_dataset(client)
    service = _service(client)
    with pytest.raises(PlatformError) as exc:
        _create(service, evaluation_protocol="not_a_protocol")
    assert exc.value.code == "UNSUPPORTED_EVALUATION_PROTOCOL"


def test_creation_is_atomic_when_item_staging_fails(client, monkeypatch):
    _seed_dataset(client)
    service = _service(client)

    def explode(*args, **kwargs):
        raise RuntimeError("injected failure before commit")

    monkeypatch.setattr(DatasetExperimentService, "_new_item_rows", explode)
    with pytest.raises(RuntimeError):
        _create(service)
    with client.app.state.database.session_factory() as session:
        assert session.query(DatasetExperimentModel).count() == 0
        assert session.query(DatasetExperimentItemModel).count() == 0
```

- [ ] **Run exact RED command:**

```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_dataset_experiment_creation.py -v
```

Expected RED: `AttributeError: 'DatasetExperimentService' object has no attribute 'create_experiment'`.

- [ ] **Implement minimal code:** add `resolve_protocol_config` to `backend/app/benchmarks/service.py` and the creation methods above to `service.py`.

- [ ] **Run exact GREEN command:** same pytest invocation. Expected: all pass.

- [ ] **Regression:**

```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_benchmark_membership.py \
  backend/tests/test_model_release.py \
  backend/tests/test_execution_certificate.py \
  backend/tests/test_executor_registry.py \
  backend/tests/test_release_wiring.py -q
```

- [ ] **Commit checkpoint:** `feat: add frozen dataset experiment creation service`

---

# TASK 4 — Frozen identity revalidation

**Files:**
- Modify: `backend/app/dataset_experiments/service.py` (add revalidation)
- Test: `backend/tests/test_dataset_experiment_revalidation.py`

**Interfaces:**
- Consumes: Task 3 creation; `_manifest_preview`; `_resolve_definition`; `validate_plugin_parameters`; `_resolve_release`; `ExecutorRegistry.provider` / `.certified_capability`; `provider.runtime_descriptor().to_metadata()`.
- Produces:
  - `DatasetExperimentService.revalidate_frozen_identity(experiment_id: str) -> DatasetExperimentModel`
  - `DatasetExperimentService._assert_item_membership(experiment, manifest) -> None`

### Exact revalidation implementation

```python
# backend/app/dataset_experiments/service.py (add)

    # ---------- frozen identity revalidation (G3+ seam) ----------

    def revalidate_frozen_identity(self, experiment_id):
        try:
            experiment = self._get(experiment_id)
        except PlatformError as exc:
            if exc.code != "DATASET_EXPERIMENT_NOT_FOUND":
                raise
            raise PlatformError(
                "DATASET_EXPERIMENT_ORCHESTRATION_FAILED",
                "Frozen experiment no longer exists; cannot revalidate.",
                409,
            ) from exc

        # 1. Frozen dataset membership hash.
        manifest = self._manifest_preview(
            experiment.dataset_name, experiment.dataset_split, experiment.dataset_label_space
        )
        if manifest.recording_manifest_hash != experiment.recording_manifest_hash:
            raise PlatformError(
                "DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED",
                "Frozen dataset manifest hash no longer matches the current manifest.",
                409,
            )

        # 2. Item membership / order must equal the frozen manifest exactly.
        self._assert_item_membership(experiment, manifest)

        # 3. Exact plugin version identity.
        try:
            definition = self._resolve_definition(experiment.plugin_id, experiment.plugin_version)
        except PlatformError as exc:
            raise PlatformError(
                "DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED",
                "Frozen plugin id/version no longer resolves exactly.",
                409,
            ) from exc

        # 4. Frozen parameters must still validate against the exact plugin.
        try:
            validate_plugin_parameters(definition, experiment.parameters_json or {})
        except PlatformError as exc:
            raise PlatformError(
                "DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED",
                "Frozen parameters are no longer valid for the frozen plugin identity.",
                409,
            ) from exc

        # 5. Release identity (id + AssetManifest SHA) via resolve() only.
        frozen_release_id = experiment.model_release_id
        frozen_asset_sha = experiment.asset_manifest_sha256
        if definition.model_release_required:
            if frozen_release_id is None or frozen_asset_sha is None:
                raise PlatformError(
                    "DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED",
                    "Frozen release identity is incomplete for a release-bound plugin.",
                    409,
                )
            try:
                resolved = self.model_release_store.resolve(
                    definition.plugin_id, definition.plugin_version, frozen_release_id
                )
            except PlatformError as exc:
                raise PlatformError(
                    "DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED",
                    "Frozen model release no longer resolves.",
                    409,
                ) from exc
            if (
                resolved.release.model_release_id != frozen_release_id
                or resolved.manifest.asset_manifest_sha256 != frozen_asset_sha
            ):
                raise PlatformError(
                    "DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED",
                    "Frozen model release or asset manifest hash has changed.",
                    409,
                )
        elif frozen_release_id is not None or frozen_asset_sha is not None:
            raise PlatformError(
                "DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED",
                "Plugin is no longer release-less but a frozen release identity exists.",
                409,
            )

        # 6. Executor capability + exact certificate + provider RuntimeDescriptor.
        try:
            provider = self.executor_registry.provider(experiment.executor)
        except PlatformError as exc:
            raise PlatformError(
                "DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED",
                "Frozen executor is no longer technically supported.",
                409,
            ) from exc
        if self.executor_registry.certified_capability(
            definition, frozen_release_id, experiment.executor
        ) is None:
            raise PlatformError(
                "DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED",
                "Exact execution certificate no longer exists for the frozen identity.",
                409,
            )
        if provider.runtime_descriptor().to_metadata() != (experiment.runtime_descriptor_json or {}):
            raise PlatformError(
                "DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED",
                "Provider RuntimeDescriptor no longer matches the frozen descriptor.",
                409,
            )

        return experiment

    def _assert_item_membership(self, experiment, manifest):
        items = list(
            self.session.scalars(
                select(DatasetExperimentItemModel)
                .where(DatasetExperimentItemModel.experiment_id == experiment.id)
                .order_by(DatasetExperimentItemModel.manifest_order)
            ).all()
        )
        expected = [(entry.manifest_order, entry.recording_id) for entry in manifest.entries]
        actual = [(item.manifest_order, item.recording_id) for item in items]
        if actual != expected:
            raise PlatformError(
                "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                "Frozen Item membership/order is inconsistent with the frozen manifest.",
                409,
            )
```

**Deliberate error boundaries:**

- Missing experiment row during revalidation → `DATASET_EXPERIMENT_ORCHESTRATION_FAILED` (fail closed). This is the only "not found" path inside revalidation; `_get` is shared with read models and raises `DATASET_EXPERIMENT_NOT_FOUND` there. `revalidate_frozen_identity` translates only the not-found case to `DATASET_EXPERIMENT_ORCHESTRATION_FAILED` (shown in the implementation above).

- Any frozen drift (manifest hash, plugin identity, parameters, release id/SHA, executor, certificate, descriptor) → `DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED`.
- Structural row corruption (Item membership/order) → `DATASET_EXPERIMENT_INVARIANT_VIOLATION`.

### Steps

- [ ] **Write failing test** `backend/tests/test_dataset_experiment_revalidation.py`. Tests must stay self-contained; do not import one test module from another, so re-declare the shared scaffolding locally:

```python
from types import SimpleNamespace

import pytest

from benchmark_fixture import add_ground_truth, add_recording

from app.core.errors import PlatformError
from app.dataset_experiments.model import (
    DatasetExperimentAttemptModel,
    DatasetExperimentItemModel,
    DatasetExperimentModel,
)
from app.dataset_experiments.service import DatasetExperimentService
from app.pipelines.base import ExecutionCapability, Pipeline, PipelineDefinition, PipelineOutput
from app.pipelines.registry import PipelineRegistry
from app.remote_execution.model_release import ResolvedModelRelease
from app.remote_execution.runtime import (
    ExecutionCertificate,
    ExecutionCertificateStore,
    ExecutorRegistry,
    RuntimeDescriptor,
)

from executor_fixtures import FakeProvider

PLUGIN_ID = "exp_test_plugin"
PLUGIN_VERSION = "1.0"
LOCAL_REF = "local:test:cpu:1"
MANIFEST_SHA = "b" * 64

_PARAM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"threshold": {"type": "number"}},
}


class ExpTestPipeline(Pipeline):
    def __init__(self, *, release_required=False, version=PLUGIN_VERSION, plugin_id=PLUGIN_ID):
        self._release_required = release_required
        self._version = version
        self._plugin_id = plugin_id

    @property
    def definition(self) -> PipelineDefinition:
        return PipelineDefinition(
            id=self._plugin_id, name="Experiment Test", version=self._version,
            label_space="spacenet_14", recommended_device="CPU", cpu_supported=True,
            stages=(), inspectable_stages=(), task_capability="classification",
            executors_supported=("local_cpu",), recommended_executor="local_cpu",
            model_release_required=self._release_required, parameter_schema=_PARAM_SCHEMA,
            technical_execution_capabilities=(ExecutionCapability("local_cpu", "cpu", "float32"),),
        )

    def run(self, recording, parameters, workspace) -> PipelineOutput:
        raise AssertionError("must not execute")


class FakeReleaseStore:
    def __init__(self, *, release_id="golden", manifest_sha=MANIFEST_SHA):
        self.release_id = release_id
        self.manifest_sha = manifest_sha
        self.resolve_calls = []

    def resolve(self, plugin_id, plugin_version, requested):
        self.resolve_calls.append((plugin_id, plugin_version, requested))
        rid = requested or self.release_id
        release = SimpleNamespace(
            plugin_id=plugin_id, plugin_version=plugin_version, model_release_id=rid,
            asset_manifest_sha256=self.manifest_sha,
        )
        manifest = SimpleNamespace(asset_manifest_sha256=self.manifest_sha)
        return ResolvedModelRelease(release=release, manifest=manifest)


class MissingReleaseStore:
    def resolve(self, plugin_id, plugin_version, requested):
        raise PlatformError("MODEL_RELEASE_NOT_FOUND", "gone")


class DriftingProvider(FakeProvider):
    """Same runtime_ref/cert tuple, different frozen descriptor metadata."""

    def runtime_descriptor(self) -> RuntimeDescriptor:
        base = super().runtime_descriptor()
        return RuntimeDescriptor(
            executor=base.executor, device_type=base.device_type,
            device_index=base.device_index, precision=base.precision,
            environment_ref=base.environment_ref, environment_label="drifted",
        )


def _certificate(*, release_required, model_release_id, executor, runtime_ref):
    return ExecutionCertificate(
        plugin_id=PLUGIN_ID, plugin_version=PLUGIN_VERSION,
        model_release_id=model_release_id if release_required else None,
        executor=executor, device_type="cpu", precision="float32",
        runtime_ref=runtime_ref, evidence_ref="g1-test",
    )


def _registry(*, release_required=False, certs=None, provider=None):
    provider = provider or FakeProvider("local_cpu", runtime_ref=LOCAL_REF)
    if certs is None:
        certs = [_certificate(
            release_required=release_required, model_release_id="golden",
            executor="local_cpu", runtime_ref=LOCAL_REF,
        )]
    return ExecutorRegistry({"local_cpu": provider}, ExecutionCertificateStore(list(certs)))


def _service(client, *, pipeline=None, store=None, registry=None):
    session = client.app.state.database.session_factory()
    return DatasetExperimentService(
        session,
        PipelineRegistry([(pipeline or ExpTestPipeline())]),
        store if store is not None else FakeReleaseStore(),
        registry or _registry(),
    )


def _seed_dataset(client, *, count=3):
    database = client.app.state.database
    with database.session_factory() as session:
        for index in range(count):
            rid = f"rec_{index}"
            add_recording(session, recording_id=rid, name=f"name_{index}")
            add_ground_truth(session, gt_id=f"gt_{index}", recording_id=rid, class_id=9,
                             class_name="LoRa 250kHz", t0=0.01, t1=0.02,
                             f0=2_440_600_000.0, f1=2_440_700_000.0)
        session.commit()
    return database


def _default_kwargs():
    return dict(
        name="experiment", dataset_name="SpaceNet", dataset_split="test",
        dataset_label_space="spacenet_14", plugin_id=PLUGIN_ID,
        plugin_version=PLUGIN_VERSION, executor="local_cpu",
        parameters={}, evaluation_protocol="physical_tf_detection_ap_v2",
        max_concurrency=1,
    )


def _create(service, **overrides):
    return service.create_experiment(**{**_default_kwargs(), **overrides})


def _update_experiment(client, experiment_id, **values):
    with client.app.state.database.session_factory() as session:
        experiment = session.get(DatasetExperimentModel, experiment_id)
        for key, value in values.items():
            setattr(experiment, key, value)
        session.commit()


def test_unchanged_identity_passes(client):
    _seed_dataset(client)
    service = _service(client)
    experiment = _create(service)
    revalidated = service.revalidate_frozen_identity(experiment.id)
    assert revalidated.id == experiment.id
    assert revalidated.status == "pending"


def test_manifest_hash_drift_fails(client):
    database = _seed_dataset(client)
    service = _service(client)
    experiment = _create(service)
    with database.session_factory() as session:
        add_ground_truth(session, gt_id="gt_extra", recording_id="rec_0", class_id=6,
                         class_name="BLE LE1M", t0=0.07, t1=0.08,
                         f0=2_441_200_000.0, f1=2_441_300_000.0)
        session.commit()
    with pytest.raises(PlatformError) as exc:
        service.revalidate_frozen_identity(experiment.id)
    assert exc.value.code == "DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED"


def test_item_membership_corruption_fails(client):
    database = _seed_dataset(client)
    service = _service(client)
    experiment = _create(service)
    with database.session_factory() as session:
        victim = session.query(DatasetExperimentItemModel).filter_by(
            experiment_id=experiment.id, manifest_order=2
        ).one()
        session.delete(victim)
        session.commit()
    with pytest.raises(PlatformError) as exc:
        service.revalidate_frozen_identity(experiment.id)
    assert exc.value.code == "DATASET_EXPERIMENT_INVARIANT_VIOLATION"


def test_item_order_corruption_fails(client):
    database = _seed_dataset(client)
    service = _service(client)
    experiment = _create(service)
    with database.session_factory() as session:
        first = session.query(DatasetExperimentItemModel).filter_by(
            experiment_id=experiment.id, manifest_order=0
        ).one()
        first.manifest_order = 5  # unique constraint stays satisfied
        session.commit()
    with pytest.raises(PlatformError) as exc:
        service.revalidate_frozen_identity(experiment.id)
    assert exc.value.code == "DATASET_EXPERIMENT_INVARIANT_VIOLATION"


def test_plugin_version_drift_fails(client):
    _seed_dataset(client)
    experiment = _create(_service(client))
    drifting = _service(client, pipeline=ExpTestPipeline(version="2.0"))
    with pytest.raises(PlatformError) as exc:
        drifting.revalidate_frozen_identity(experiment.id)
    assert exc.value.code == "DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED"


def test_plugin_removed_fails(client):
    _seed_dataset(client)
    experiment = _create(_service(client))
    missing = _service(client, pipeline=ExpTestPipeline(plugin_id="other_plugin"))
    with pytest.raises(PlatformError) as exc:
        missing.revalidate_frozen_identity(experiment.id)
    assert exc.value.code == "DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED"


def test_parameters_drift_fails(client):
    _seed_dataset(client)
    experiment = _create(_service(client))
    _update_experiment(client, experiment.id, parameters_json={"unknown": 1})
    with pytest.raises(PlatformError) as exc:
        _service(client).revalidate_frozen_identity(experiment.id)
    assert exc.value.code == "DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED"


def test_release_drift_fails(client):
    _seed_dataset(client)
    create_service = _service(
        client, pipeline=ExpTestPipeline(release_required=True),
        store=FakeReleaseStore(), registry=_registry(release_required=True),
    )
    experiment = _create(create_service, model_release_id="golden")
    drifting = _service(
        client, pipeline=ExpTestPipeline(release_required=True),
        store=FakeReleaseStore(manifest_sha="c" * 64), registry=_registry(release_required=True),
    )
    with pytest.raises(PlatformError) as exc:
        drifting.revalidate_frozen_identity(experiment.id)
    assert exc.value.code == "DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED"


def test_release_no_longer_resolves_fails(client):
    _seed_dataset(client)
    create_service = _service(
        client, pipeline=ExpTestPipeline(release_required=True),
        store=FakeReleaseStore(), registry=_registry(release_required=True),
    )
    experiment = _create(create_service, model_release_id="golden")
    drifting = _service(
        client, pipeline=ExpTestPipeline(release_required=True),
        store=MissingReleaseStore(), registry=_registry(release_required=True),
    )
    with pytest.raises(PlatformError) as exc:
        drifting.revalidate_frozen_identity(experiment.id)
    assert exc.value.code == "DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED"


def test_certificate_drift_fails(client):
    _seed_dataset(client)
    experiment = _create(_service(client))
    unmatched = _service(client, registry=_registry(certs=[]))
    with pytest.raises(PlatformError) as exc:
        unmatched.revalidate_frozen_identity(experiment.id)
    assert exc.value.code == "DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED"


def test_runtime_descriptor_drift_fails(client):
    _seed_dataset(client)
    experiment = _create(_service(client))
    drifting = _service(client, registry=_registry(provider=DriftingProvider("local_cpu", runtime_ref=LOCAL_REF)))
    with pytest.raises(PlatformError) as exc:
        drifting.revalidate_frozen_identity(experiment.id)
    assert exc.value.code == "DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED"


def test_missing_experiment_fails_closed(client):
    with pytest.raises(PlatformError) as exc:
        _service(client).revalidate_frozen_identity("does-not-exist")
    assert exc.value.code == "DATASET_EXPERIMENT_ORCHESTRATION_FAILED"


def test_validation_never_mutates_frozen_identity(client):
    _seed_dataset(client)
    experiment = _create(_service(client))
    with client.app.state.database.session_factory() as session:
        stored = session.get(DatasetExperimentModel, experiment.id)
        snapshot = {
            "dataset_name": stored.dataset_name,
            "dataset_split": stored.dataset_split,
            "dataset_label_space": stored.dataset_label_space,
            "recording_manifest_hash": stored.recording_manifest_hash,
            "plugin_id": stored.plugin_id,
            "plugin_version": stored.plugin_version,
            "model_release_id": stored.model_release_id,
            "asset_manifest_sha256": stored.asset_manifest_sha256,
            "parameters_json": dict(stored.parameters_json or {}),
            "executor": stored.executor,
            "runtime_descriptor_json": dict(stored.runtime_descriptor_json or {}),
            "evaluation_protocol": stored.evaluation_protocol,
            "max_concurrency": stored.max_concurrency,
            "status": stored.status,
        }
    _update_experiment(client, experiment.id, parameters_json={"unknown": 1})
    with pytest.raises(PlatformError):
        _service(client).revalidate_frozen_identity(experiment.id)
    with client.app.state.database.session_factory() as session:
        stored = session.get(DatasetExperimentModel, experiment.id)
        for key, value in snapshot.items():
            assert getattr(stored, key) == value, key
```

- [ ] **Run exact RED command:**

```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_dataset_experiment_revalidation.py -v
```

Expected RED: `AttributeError: 'DatasetExperimentService' object has no attribute 'revalidate_frozen_identity'`.

- [ ] **Implement minimal code:** add `revalidate_frozen_identity` and `_assert_item_membership` exactly as specified. No other method may write/commit.

- [ ] **Run exact GREEN command:** same pytest invocation. Expected: all pass.

- [ ] **Regression:**

```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_dataset_experiment_creation.py \
  backend/tests/test_dataset_experiment_read_model.py \
  backend/tests/test_executor_registry.py -q
```

- [ ] **Commit checkpoint:** `feat: add frozen experiment identity revalidation`

---

# TASK 5 — G1 regression matrix, import isolation, full verification

**Files:**
- Create: `backend/tests/test_dataset_experiment_regression.py`
- Run/verify only: all G1 and frozen-contract suites
- Docs: no production doc change is required by G1 because G1 adds no public
  API and `ARCHITECTURE.md` (16 lines) contains no table inventory; this task
  instead verifies the spec ↔ plan coverage checklist below.

**Interfaces:**
- Consumes: everything produced by Tasks 1–4.
- Produces: a single regression file pinning import isolation and frozen
  contracts.

### Steps

- [ ] **Write failing test** `backend/tests/test_dataset_experiment_regression.py`:

```python
import subprocess
import sys
from pathlib import Path

from app.analysis.model import AnalysisRunModel


def test_control_plane_import_does_not_load_torch_or_ultralytics():
    code = (
        "import sys; "
        "import app.dataset_experiments.service; "
        "assert 'torch' not in sys.modules, 'torch leaked into G1 control plane'; "
        "assert 'ultralytics' not in sys.modules, 'ultralytics leaked into G1 control plane'; "
        "print('OK')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_analysis_run_schema_has_no_dataset_experiment_columns():
    columns = set(AnalysisRunModel.__table__.columns.keys())
    assert not any("experiment" in name for name in columns)
    assert "launch_requested_at" not in columns
```

- [ ] **Run exact RED command:**

```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests/test_dataset_experiment_regression.py -v
```

Expected RED: only if a prior task leaked a heavy import or touched
`AnalysisRunModel`. Otherwise this file is expected to pass immediately after
Task 4; if it passes on first run, record that as evidence that G1 did not
leak (the TDD "RED" for this task is the frozen-contract assertion, which must
be verified against the unchanged `AnalysisRunModel`).

- [ ] **Run focused G1 suite:**

```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_dataset_experiment_models.py \
  backend/tests/test_dataset_experiment_read_model.py \
  backend/tests/test_dataset_experiment_creation.py \
  backend/tests/test_dataset_experiment_revalidation.py \
  backend/tests/test_dataset_experiment_regression.py -v
```

- [ ] **Run frozen-contract regression (must stay green, unchanged):**

```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest \
  backend/tests/test_benchmark_manifest.py \
  backend/tests/test_benchmark_models.py \
  backend/tests/test_benchmark_membership.py \
  backend/tests/test_plugin_registry.py \
  backend/tests/test_plugin_parameters_freeze.py \
  backend/tests/test_model_release.py \
  backend/tests/test_release_wiring.py \
  backend/tests/test_asset_manifest_v1_frozen.py \
  backend/tests/test_execution_certificate.py \
  backend/tests/test_executor_registry.py \
  backend/tests/test_analysis_runs.py \
  backend/tests/test_remote_execution_canonical.py \
  backend/tests/test_request_release_provenance.py -q
```

- [ ] **Run full backend suite:**

```bash
PYTHONPATH="$PWD/backend" "$PWD/.venv/bin/python" -m pytest backend/tests -q
```

- [ ] **Spec coverage self-check** (must all be true):
  - Spec §20 G1 "models" → Task 1.
  - Spec §20 G1 "additive migration" → Task 1.
  - Spec §20 G1 "schemas/read models" → Task 2.
  - Spec §20 G1 "Experiment creation" → Task 3.
  - Spec §20 G1 "frozen manifest Items" → Task 3.
  - Spec §20 G1 "frozen identity validation" → Task 4.
  - Spec §17 "Additive Migration" → Task 1.
  - Spec §18.1 "Model / migration" → Task 1.
  - Spec §15 invariants 1–3 (frozen membership/scientific/execution identity)
    → Tasks 1, 3.
  - Spec §15 invariant 5 (No Historical Run Reuse) → Task 3 test.

- [ ] **Commit checkpoint:** `test: add phase G G1 regression matrix`

---

## G1 Boundary (what this plan does / does not do)

**G1 implements (this plan):**
- `DatasetExperimentModel`, `DatasetExperimentItemModel`,
  `DatasetExperimentAttemptModel` and their constraints/FKs/indexes.
- One additive migration wired into `run_additive_migrations()` and model
  registration in `load_domain_models()`.
- `DatasetExperimentCreate` + internal `DatasetExperimentRead` /
  `DatasetExperimentItemRead` / `DatasetExperimentAttemptRead` with derived
  counts.
- `DatasetExperimentService.get_experiment` / `list_items` / `list_attempts`.
- `DatasetExperimentService.create_experiment` (atomic Experiment + N queued
  Items; freezes dataset hash, plugin identity, parameters, release id +
  AssetManifest SHA, executor, frozen `RuntimeDescriptor`, protocol, and
  `max_concurrency`).
- `DatasetExperimentService.revalidate_frozen_identity` (fail-closed drift
  detection; read-only).
- Focused G1 tests: models/migration, schema/read models, creation/atomicity,
  revalidation, regression/import isolation.

**G1 explicitly does NOT implement (belongs to G2+):**
- `prepare_run` / `launch_prepared_run` and any `AnalysisService` change (G2).
- Attempt creation, coordinator worker, job manager, bounded scheduling,
  reconciliation, best-effort execution (G3).
- Retry Failed, launch fencing / `launch_requested_at` writes, coordinator
  tokens, restart recovery, duplicate active Attempt protection (G4).
- DatasetEvaluation creation, evaluation lifecycle, REST endpoints, read
  routers, Retry Evaluation (G5).
- Real 3-Recording SpaceNet/CPN acceptance and seal (G6).
- Any change to canonical request hashing, AssetManifest V1 hashing,
  `ExecutionCertificate` semantics, `ModelRelease` authority, terminal
  `AnalysisRun` immutability, or the `analysis_runs` schema.

---

## Self-Review Checklist (run before G1 implementation is sealed)

1. **Spec coverage:** every §20 G1 bullet maps to a task in the coverage
   self-check above; no G1 bullet is unimplemented.
2. **Boundary scan:** no G2–G6 behavior (Attempt creation, workers, Retry,
   fencing, evaluation, REST, frontend) appears in any task; no
   `AnalysisService` edit.
3. **Source-of-truth scan:**
   - Manifest: only `DatasetBenchmarkService.prepare_manifest` (no second hash algorithm).
   - Release: only `ModelReleaseStore.resolve` (no `resolve_by_manifest_sha`).
   - Certificate: only `ExecutorRegistry.certified_capability` / `.provider` (no parallel checker, no `availability_for`).
   - Plugin: only `PipelineRegistry.get` + `validate_plugin_parameters`.
   - Protocol: only `resolve_protocol_config` → `_protocol_config_for`.
4. **Transaction scan:** Experiment + Items commit once; `_new_item_rows` failure rolls back; no Attempt/Run/launch in G1.
5. **Placeholder scan:** no TBD/TODO/XXX/ellipsis-instead-of-logic; every planned symbol is defined in this plan with a concrete signature.
6. **Type/signature consistency:** `create_experiment` keyword signature, `revalidate_frozen_identity(experiment_id)`, and all read/schema types match across Tasks 2–5 and the test scaffolding.

---

## Out-of-Scope Guardrails During Implementation

- If implementation appears to need a new `analysis_runs` column, STOP.
- If implementation appears to need `availability_for` or a Recording probe in
  creation or revalidation, STOP.
- If implementation appears to need `resolve_by_manifest_sha`, STOP.
- If implementation appears to need a second dataset hashing routine, STOP.
- If implementation appears to need `prepare_run`/`launch_prepared_run`,
  coordinator, or `DatasetEvaluation` creation, STOP — that is G2+.
