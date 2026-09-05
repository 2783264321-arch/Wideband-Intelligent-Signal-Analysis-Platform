# M8.6A Dataset Benchmark Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the CPU-only backend core for reproducible dataset-level benchmarking: frozen dataset/run membership, deterministic physical time-frequency AP/mAP, dataset diagnostics, persistent DatasetEvaluation records, and recoverable subprocess execution.

**Architecture:** Keep Pipeline execution unchanged as ordinary `AnalysisRun`. Add `DatasetEvaluation` and `DatasetEvaluationItem` as immutable benchmark records over a frozen `Recording -> AnalysisRun` mapping. Pure evaluation math lives under `app/evaluation`; persistence, manifest freezing, APIs, and subprocess lifecycle live under `app/benchmarks`. M8.6A does not implement Batch Analysis Package transport or frontend UI.

**Tech Stack:** Python 3, FastAPI, SQLAlchemy, SQLite, Pydantic, NumPy/SciPy already present in the repository, pytest, local Python subprocess workers.

**Spec:** `docs/superpowers/specs/2026-09-06-m8-6-dataset-benchmark-design.md`

## Global Constraints

- Start from shared baseline `feature/v1-core @ f2c28b3b13e3ecfdaf5f366d23ca90d758cd1c4d`.
- Work on `feature/m8-6a-benchmark-core`; never implement directly on `feature/v1-core` or `main`.
- Evaluation protocol name is exactly `physical_tf_detection_ap_v1`.
- Coordinates remain physical seconds and absolute Hz; never convert to pixels or normalized coordinates.
- M8.5 `match_predictions()` semantics remain unchanged: class-agnostic Hungarian one-to-one matching at IoU 0.5.
- AP/mAP must use confidence-ranked greedy matching, not Hungarian matching.
- AP uses 101-point interpolated precision and IoU thresholds `0.50, 0.55, ..., 0.95`.
- AP consumes every stored final `DetectionResult.confidence`; add no benchmark-level confidence threshold.
- Detection-only pipelines have localization metrics but classification/class-aware metrics are N/A, never numeric zero.
- Completed DatasetEvaluations are immutable; changed run membership means a new evaluation.
- Retry for failed/interrupted evaluations reuses the exact frozen membership and protocol.
- Ambiguous run candidates are never auto-selected by latest/oldest/UUID.
- Database changes are additive only; do not change AnalysisRun, DetectionResult, Recording, GroundTruth, Analysis Package v1, M6 importer, or Pipeline contracts.
- Do not add Celery, Redis, Postgres, a generic Job table, `Dataset`, `BatchRun`, `Experiment`, or frontend code in M8.6A.
- Avoid N+1 queries in benchmark execution; dataset GT and detections must be loaded with bulk/join queries.
- Use TDD for every behavior change: write a failing test, run and record the expected RED, implement minimally, run GREEN, then refactor.
- Do not weaken existing tests or increase unrelated timeouts to make the suite pass.
- No model checkpoints, datasets, analysis ZIPs, runtime SQLite DBs, or build artifacts may enter Git.

---

## File Structure

Create or modify the following focused units. Do not collapse them into one large service file.

```text
backend/app/benchmarks/
  __init__.py            # package marker only
  model.py               # DatasetEvaluation / DatasetEvaluationItem ORM models
  manifest.py            # pure canonical manifest/hash logic
  schema.py              # API/request/read schemas and result JSON shapes
  service.py             # manifest preparation, membership resolution/freezing, CRUD/start/retry/compare
  job_manager.py         # local subprocess launcher for benchmark worker
  loader.py              # bulk DB load of frozen items, GT, detections into evaluation inputs
  worker.py              # status/progress lifecycle, compute, atomic result persistence

backend/app/evaluation/
  capability.py          # shared classification applicability helper used by M8.5 and M8.6
  ap.py                  # pure confidence-ranked AP/mAP protocol
  dataset_metrics.py     # pure dataset-level operating-point diagnostics

backend/tests/
  benchmark_fixture.py
  test_benchmark_models.py
  test_benchmark_manifest.py
  test_benchmark_membership.py
  test_evaluation_ap.py
  test_dataset_metrics.py
  test_benchmark_worker.py
  test_benchmark_api.py
```

Modify existing files only where integration requires it:

```text
backend/app/db/base.py
backend/app/db/migrations.py
backend/app/evaluation/service.py
backend/app/main.py
backend/tests/test_algorithm_lab_compare.py
backend/tests/test_analysis_runs.py        # only if startup recovery integration is most naturally asserted here
```

---

### Task 1: Persist DatasetEvaluation and DatasetEvaluationItem additively

**Files:**
- Create: `backend/app/benchmarks/__init__.py`
- Create: `backend/app/benchmarks/model.py`
- Modify: `backend/app/db/base.py`
- Modify: `backend/app/db/migrations.py`
- Create: `backend/tests/test_benchmark_models.py`

**Interfaces:**
- Produces ORM models `DatasetEvaluationModel` and `DatasetEvaluationItemModel`.
- Produces additive migration function `upgrade_dataset_benchmarks(engine) -> None`.
- Later tasks rely on table names `dataset_evaluations` and `dataset_evaluation_items`.

- [ ] **Step 1: Write the failing model/migration tests**

Create `backend/tests/test_benchmark_models.py` with tests that first fail because benchmark models/tables do not exist:

```python
from sqlalchemy import inspect, select

from app.db.base import Base, load_domain_models
from app.db.migrations import run_additive_migrations
from app.db.session import Database


def test_benchmark_tables_are_registered_and_created(settings):
    database = Database(settings.database_url)
    load_domain_models()
    Base.metadata.create_all(database.engine)
    names = set(inspect(database.engine).get_table_names())
    assert "dataset_evaluations" in names
    assert "dataset_evaluation_items" in names


def test_additive_migration_creates_benchmark_tables_for_existing_database(settings):
    database = Database(settings.database_url)
    load_domain_models()
    # Simulate an existing DB where regular domain tables already exist, then run
    # the additive migration path expected on application startup.
    Base.metadata.tables["recordings"].create(database.engine, checkfirst=True)
    Base.metadata.tables["analysis_runs"].create(database.engine, checkfirst=True)
    run_additive_migrations(database.engine)
    names = set(inspect(database.engine).get_table_names())
    assert "dataset_evaluations" in names
    assert "dataset_evaluation_items" in names
```

Also add a persistence test that creates one evaluation + two items and verifies deterministic `manifest_order` retrieval and nullable `analysis_run_id`.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
pytest backend/tests/test_benchmark_models.py -v
```

Expected: collection/import failure for missing `app.benchmarks.model` or assertions that the benchmark tables are absent. Record the RED evidence.

- [ ] **Step 3: Implement the ORM models**

Create `backend/app/benchmarks/model.py` with these exact semantic fields:

```python
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class DatasetEvaluationModel(Base):
    __tablename__ = "dataset_evaluations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    dataset_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    dataset_split: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    label_space: Mapped[str] = mapped_column(String(128), nullable=False)
    pipeline_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    pipeline_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)

    expected_recordings: Mapped[int] = mapped_column(Integer, nullable=False)
    evaluated_recordings: Mapped[int] = mapped_column(Integer, nullable=False)
    missing_recordings: Mapped[int] = mapped_column(Integer, nullable=False)
    coverage: Mapped[float] = mapped_column(Float, nullable=False)
    comparable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    recording_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    evaluation_protocol: Mapped[str] = mapped_column(String(128), nullable=False)
    protocol_config_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    aggregate_metrics_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    per_class_metrics_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    confusion_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)

    progress_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    progress_current: Mapped[int | None] = mapped_column(Integer, nullable=True)
    progress_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    worker_pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    items: Mapped[list["DatasetEvaluationItemModel"]] = relationship(
        back_populates="evaluation", cascade="all, delete-orphan", order_by="DatasetEvaluationItemModel.manifest_order"
    )


class DatasetEvaluationItemModel(Base):
    __tablename__ = "dataset_evaluation_items"
    __table_args__ = (
        UniqueConstraint("evaluation_id", "recording_id", name="uq_dataset_eval_recording"),
        UniqueConstraint("evaluation_id", "manifest_order", name="uq_dataset_eval_manifest_order"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    evaluation_id: Mapped[str] = mapped_column(
        ForeignKey("dataset_evaluations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    manifest_order: Mapped[int] = mapped_column(Integer, nullable=False)
    recording_id: Mapped[str] = mapped_column(ForeignKey("recordings.id"), index=True, nullable=False)
    analysis_run_id: Mapped[str | None] = mapped_column(ForeignKey("analysis_runs.id"), index=True, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    gt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prediction_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    evaluation: Mapped[DatasetEvaluationModel] = relationship(back_populates="items")
```

Do not add `Dataset`, `BatchRun`, or generic job tables.

- [ ] **Step 4: Register models and additive table creation**

Modify `backend/app/db/base.py`:

```python
def load_domain_models() -> None:
    from app.analysis import model as _analysis  # noqa: F401
    from app.benchmarks import model as _benchmarks  # noqa: F401
    from app.detections import model as _detections  # noqa: F401
    from app.ground_truth import model as _ground_truth  # noqa: F401
    from app.recordings import model as _recordings  # noqa: F401
```

Modify `backend/app/db/migrations.py` with a new-table additive migration that reuses SQLAlchemy metadata instead of duplicating CREATE TABLE SQL:

```python
def upgrade_dataset_benchmarks(engine) -> None:
    from app.benchmarks.model import DatasetEvaluationItemModel, DatasetEvaluationModel

    DatasetEvaluationModel.__table__.create(engine, checkfirst=True)
    DatasetEvaluationItemModel.__table__.create(engine, checkfirst=True)


def run_additive_migrations(engine) -> None:
    upgrade_recording_external(engine)
    upgrade_dataset_benchmarks(engine)
```

- [ ] **Step 5: Run model/migration tests GREEN**

Run:

```bash
pytest backend/tests/test_benchmark_models.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit Task 1**

```bash
git add backend/app/benchmarks backend/app/db/base.py backend/app/db/migrations.py backend/tests/test_benchmark_models.py
git commit -m "feat: add dataset benchmark persistence"
```

---

### Task 2: Freeze a deterministic Recording manifest and hash

**Files:**
- Create: `backend/app/benchmarks/manifest.py`
- Create: `backend/tests/test_benchmark_manifest.py`
- Create: `backend/tests/benchmark_fixture.py`

**Interfaces:**
- Produces `ManifestGroundTruth`, `ManifestRecording`, `FrozenRecordingManifest`.
- Produces `build_recording_manifest(dataset_name, dataset_split, label_space, recordings) -> FrozenRecordingManifest`.
- Hash excludes local DB IDs and local paths but includes canonical Recording semantics and GT.

- [ ] **Step 1: Create test-only benchmark fixture helpers**

Create `backend/tests/benchmark_fixture.py` with small helpers that only construct ORM test data. Keep them deterministic and free of production imports beyond ORM models. At minimum expose:

```python
def add_recording(session, *, recording_id: str, name: str, dataset_name="SpaceNet", dataset_split="test", label_space="spacenet_14", path_suffix=None): ...
def add_ground_truth(session, *, gt_id: str, recording_id: str, class_id: int, class_name: str, t0: float, t1: float, f0: float, f1: float): ...
def add_run(session, *, run_id: str, recording_id: str, pipeline_id="pipeline_x", pipeline_version="1.0", executor="imported", status="completed", created_at=None): ...
def add_detection(session, *, detection_id: str, run_id: str, class_id: int, class_name: str, confidence: float, t0: float, t1: float, f0: float, f1: float): ...
```

Use physical coordinates inside a valid tiny 1 MHz Recording around 2.441 GHz so later tests can reuse the fixtures.

- [ ] **Step 2: Write manifest tests before production code**

Create `backend/tests/test_benchmark_manifest.py` covering:

```python
def test_manifest_hash_is_independent_of_local_ids_paths_and_input_order(): ...
def test_gt_annotation_change_changes_manifest_hash(): ...
def test_duplicate_recording_names_are_rejected(): ...
def test_manifest_order_is_deterministic_by_recording_name(): ...
def test_numeric_canonicalization_treats_equivalent_float_values_stably(): ...
```

Construct two logically equivalent manifests with different `recording_id`, `data_path`, and insertion order; assert equal hashes. Change one GT frequency by 1 Hz; assert a different hash.

- [ ] **Step 3: Run RED**

```bash
pytest backend/tests/test_benchmark_manifest.py -v
```

Expected: import/attribute failures because `app.benchmarks.manifest` does not exist.

- [ ] **Step 4: Implement pure manifest canonicalization**

Create `backend/app/benchmarks/manifest.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json


@dataclass(frozen=True)
class ManifestGroundTruth:
    t_start_s: float
    t_end_s: float
    f_low_hz: float
    f_high_hz: float
    class_id: int
    class_name: str


@dataclass(frozen=True)
class ManifestRecording:
    recording_id: str  # local lookup only; excluded from hash payload
    name: str
    data_format: str
    sample_rate_hz: float
    center_frequency_hz: float
    frequency_low_hz: float
    frequency_high_hz: float
    num_samples: int
    duration_s: float
    ground_truth: tuple[ManifestGroundTruth, ...]


@dataclass(frozen=True)
class FrozenRecordingManifest:
    dataset_name: str
    dataset_split: str
    label_space: str
    entries: tuple[ManifestRecording, ...]
    sha256: str


def _number(value: float) -> str:
    value = float(value)
    if value == 0.0:
        return "0"
    return format(value, ".17g")
```

Canonical GT sort key must be:

```python
(gt.t_start_s, gt.t_end_s, gt.f_low_hz, gt.f_high_hz, gt.class_id, gt.class_name)
```

Canonical Recording order is lexical `Recording.name`; duplicate names raise `ValueError("duplicate Recording.name in dataset snapshot")`.

The hash payload must include:

```python
{
    "dataset_name": dataset_name,
    "dataset_split": dataset_split,
    "label_space": label_space,
    "recordings": [
        {
            "name": r.name,
            "data_format": r.data_format,
            "sample_rate_hz": _number(r.sample_rate_hz),
            "center_frequency_hz": _number(r.center_frequency_hz),
            "frequency_low_hz": _number(r.frequency_low_hz),
            "frequency_high_hz": _number(r.frequency_high_hz),
            "num_samples": r.num_samples,
            "duration_s": _number(r.duration_s),
            "ground_truth": [... canonical GT dicts using _number ...],
        }
    ],
}
```

Serialize exactly with:

```python
json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
```

then SHA256 hex digest.

- [ ] **Step 5: Run manifest tests GREEN**

```bash
pytest backend/tests/test_benchmark_manifest.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit Task 2**

```bash
git add backend/app/benchmarks/manifest.py backend/tests/benchmark_fixture.py backend/tests/test_benchmark_manifest.py
git commit -m "feat: freeze deterministic dataset manifests"
```

---

### Task 3: Prepare dataset snapshots, resolve candidate runs, and freeze explicit membership

**Files:**
- Create: `backend/app/benchmarks/schema.py`
- Create: `backend/app/benchmarks/service.py`
- Create: `backend/tests/test_benchmark_membership.py`

**Interfaces:**
- Produces fixed protocol constants `PHYSICAL_TF_PROTOCOL = "physical_tf_detection_ap_v1"` and `PROTOCOL_CONFIG_V1`.
- Produces `DatasetBenchmarkService.prepare_manifest(...)`, `resolve_pipeline_snapshot(...)`, and `create_evaluation(...)`.
- `create_evaluation()` accepts an explicit item for every frozen Recording, with `analysis_run_id=None` representing missing coverage.

- [ ] **Step 1: Write membership/service tests first**

Create `backend/tests/test_benchmark_membership.py` with direct service tests for:

```python
def test_prepare_manifest_requires_gt_and_returns_deterministic_hash(...): ...
def test_pipeline_snapshot_reports_resolved_missing_and_ambiguous_without_auto_selection(...): ...
def test_create_evaluation_rejects_stale_manifest_hash(...): ...
def test_create_evaluation_freezes_exact_recording_to_run_mapping(...): ...
def test_newer_run_created_after_freeze_does_not_change_membership(...): ...
def test_create_evaluation_rejects_run_for_wrong_recording(...): ...
def test_create_evaluation_rejects_noncompleted_run(...): ...
def test_create_evaluation_rejects_mixed_pipeline_or_version(...): ...
def test_incomplete_mapping_requires_allow_incomplete(...): ...
def test_incomplete_mapping_sets_coverage_and_comparable_false(...): ...
```

For the ambiguous test, create two completed runs with identical pipeline ID/version for the same Recording and assert both candidate IDs are returned and no chosen run ID exists.

- [ ] **Step 2: Run RED**

```bash
pytest backend/tests/test_benchmark_membership.py -v
```

Expected: missing service/schema imports or missing methods.

- [ ] **Step 3: Define protocol and API/service data schemas**

Create `backend/app/benchmarks/schema.py` with strict Pydantic models. Use snake_case internally and the repository's existing response serialization conventions.

At minimum define:

```python
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

PHYSICAL_TF_PROTOCOL = "physical_tf_detection_ap_v1"
IOU_THRESHOLDS_V1 = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95]
PROTOCOL_CONFIG_V1 = {
    "iou_thresholds": IOU_THRESHOLDS_V1,
    "ap_interpolation": "101_point_max_precision",
    "ap_recall_points": 101,
    "confidence_field": "DetectionResult.confidence",
    "diagnostic_matching": "hungarian_class_agnostic",
    "diagnostic_iou_threshold": 0.5,
    "ranking_tie_break": [
        "confidence_desc",
        "manifest_order",
        "t_start_s",
        "f_low_hz",
        "t_end_s",
        "f_high_hz",
        "class_id",
    ],
}

class DatasetSelection(BaseModel):
    dataset_name: str = Field(min_length=1)
    dataset_split: str = Field(min_length=1)
    label_space: str = Field(min_length=1)

class FrozenRunItemInput(BaseModel):
    recording_id: str
    analysis_run_id: str | None

class DatasetEvaluationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    dataset_name: str
    dataset_split: str
    label_space: str
    recording_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    allow_incomplete: bool = False
    items: list[FrozenRunItemInput]
```

Also define read models for manifest entries and run-resolution entries so Task 8 can expose them without changing service return shapes.

- [ ] **Step 4: Implement DB-backed manifest preparation without N+1 queries**

In `DatasetBenchmarkService.prepare_manifest()`:

1. Query matching `RecordingModel` rows with `has_ground_truth == True`.
2. Reject an empty selection with `PlatformError("DATASET_SNAPSHOT_EMPTY", ...)`.
3. Query all matching `GroundTruthModel` rows in one joined/bulk query, not one query per Recording.
4. Build `ManifestRecording` values and call `build_recording_manifest()`.
5. Return the frozen manifest plus local IDs/GT counts for preview.

Use stable error codes:

```text
DATASET_SNAPSHOT_EMPTY
DATASET_SNAPSHOT_AMBIGUOUS
DATASET_MANIFEST_CHANGED
INVALID_BENCHMARK_MEMBERSHIP
```

- [ ] **Step 5: Implement pipeline snapshot resolution**

`resolve_pipeline_snapshot()` receives the same dataset selection plus `pipeline_id` and `pipeline_version`.

For each frozen Recording, return one of:

```text
resolved   -> exactly one completed run, include candidate_run_ids=[id]
missing    -> zero completed runs, candidate_run_ids=[]
ambiguous  -> >1 completed runs, return every candidate ID sorted deterministically
```

Candidate sorting must be `(created_at, id)`. Do not choose a winner for ambiguous rows.

- [ ] **Step 6: Implement explicit frozen evaluation creation**

`create_evaluation()` must:

1. Rebuild the current Recording manifest and require exact hash equality with the caller's preview hash.
2. Require exactly one supplied item per manifest Recording and no extra Recording IDs.
3. Require at least one included run.
4. For each included run, require existence, `status == "completed"`, and `run.recording_id == item.recording_id`.
5. Require all included runs to share the exact same `pipeline_id` and `pipeline_version`.
6. If any `analysis_run_id is None` and `allow_incomplete == False`, reject before any ORM write.
7. Bulk-query prediction counts by run ID and set each item `gt_count` / `prediction_count`.
8. Create all `DatasetEvaluationItemModel` rows in manifest order.
9. Compute coverage from included items only.
10. Set `comparable = (coverage == 1.0)`.
11. Store exact `PHYSICAL_TF_PROTOCOL` and a deep copy of `PROTOCOL_CONFIG_V1`.
12. Commit evaluation + items once, then refresh.

Use IDs:

```python
evaluation_id = f"eval_{uuid4().hex}"
item_id = f"evalitem_{uuid4().hex}"
```

Item status is `included` or `missing_run` in M8.6A. Reserve `invalid` for future import workflows; do not invent invalid rows during a successful freeze.

- [ ] **Step 7: Run membership tests GREEN**

```bash
pytest backend/tests/test_benchmark_membership.py -v
```

Expected: all pass.

- [ ] **Step 8: Commit Task 3**

```bash
git add backend/app/benchmarks/schema.py backend/app/benchmarks/service.py backend/tests/test_benchmark_membership.py
git commit -m "feat: freeze dataset benchmark membership"
```

---

### Task 4: Extract shared classification applicability without changing M8.5 semantics

**Files:**
- Create: `backend/app/evaluation/capability.py`
- Modify: `backend/app/evaluation/service.py`
- Modify: `backend/tests/test_algorithm_lab_compare.py`
- Create or extend: `backend/tests/test_benchmark_membership.py`

**Interfaces:**
- Produces `classification_applicability(run, recording, registry) -> ClassificationApplicability`.
- Both M8.5 and M8.6 use exactly the same detection-only/imported/unknown semantics.

- [ ] **Step 1: Add regression tests before extraction**

Add tests that assert the helper behavior expected by M8.5:

```text
registry pipeline + task_capability=detection_localization -> false, detection_only_pipeline
registry classification pipeline + matching label space -> true, null reason
registry classification pipeline + mismatched label space -> false, label_space_mismatch
unknown registry pipeline + executor=imported + recording label space present -> true
unknown non-imported run -> false, unknown_classification_semantics
```

Also keep existing M8.5 API assertions unchanged.

- [ ] **Step 2: Run targeted tests and record RED for the missing helper**

```bash
pytest backend/tests/test_algorithm_lab_compare.py backend/tests/test_benchmark_membership.py -v
```

The newly added direct helper tests should fail because `app.evaluation.capability` does not exist.

- [ ] **Step 3: Implement the helper**

Create `backend/app/evaluation/capability.py`:

```python
from dataclasses import dataclass

from app.core.errors import PlatformError


@dataclass(frozen=True)
class ClassificationApplicability:
    applicable: bool
    reason: str | None


def classification_applicability(run, recording, registry) -> ClassificationApplicability:
    if registry is not None:
        try:
            pipeline = registry.get(run.pipeline_id)
        except PlatformError:
            pipeline = None
        if pipeline is not None:
            definition = pipeline.definition
            if definition.task_capability == "detection_localization":
                return ClassificationApplicability(False, "detection_only_pipeline")
            if recording.label_space is not None and definition.label_space != recording.label_space:
                return ClassificationApplicability(False, "label_space_mismatch")
            return ClassificationApplicability(True, None)
    if run.executor == "imported":
        if recording.label_space is None:
            return ClassificationApplicability(False, "unknown_classification_semantics")
        return ClassificationApplicability(True, None)
    return ClassificationApplicability(False, "unknown_classification_semantics")
```

- [ ] **Step 4: Refactor M8.5 service to call the helper**

Replace the private duplicated decision logic in `AlgorithmLabComparisonService._classification_applicability()` with the shared helper. Do not change public response fields or matching behavior.

- [ ] **Step 5: Run M8.5 regression tests GREEN**

```bash
pytest backend/tests/test_algorithm_lab_compare.py backend/tests/test_evaluation_classification.py backend/tests/test_evaluation_matching.py -v
```

Expected: all pass with the same M8.5 semantics.

- [ ] **Step 6: Commit Task 4**

```bash
git add backend/app/evaluation/capability.py backend/app/evaluation/service.py backend/tests/test_algorithm_lab_compare.py backend/tests/test_benchmark_membership.py
git commit -m "refactor: share classification applicability"
```

---

### Task 5: Implement deterministic confidence-ranked AP/mAP math

**Files:**
- Create: `backend/app/evaluation/ap.py`
- Create: `backend/tests/test_evaluation_ap.py`

**Interfaces:**
- Produces immutable input records `EvaluationGroundTruth` and `EvaluationPrediction`.
- Produces `average_precision_at_iou(...)`, `localization_ap_summary(...)`, `class_aware_ap_summary(...)`.
- Reuses `app.evaluation.matching.bbox_iou()` for physical time-frequency IoU; does not modify `matching.py`.

- [ ] **Step 1: Write all tiny AP fixtures first**

Create `backend/tests/test_evaluation_ap.py` and cover every required mathematical edge case:

```text
perfect detections -> AP=1
all false positives -> AP=0
duplicate prediction -> one TP then duplicate FP
wrong class + perfect bbox -> localization TP but class-aware AP does not match it
cross-recording overlap -> never matches
same confidence -> deterministic tie order
class with zero GT -> AP=None
GT exists + no predictions -> AP=0
IoU exactly 0.50 -> TP at AP50
IoU 0.49 -> FP at AP50
AP50:95 -> exactly ten thresholds
101-point interpolation -> hand-computed fixture
```

For the interpolation fixture, use two GT and ranked predictions `TP, FP, TP`. Assert:

```python
expected = (51 * 1.0 + 50 * (2.0 / 3.0)) / 101.0
assert result.ap == pytest.approx(expected)
```

- [ ] **Step 2: Run RED**

```bash
pytest backend/tests/test_evaluation_ap.py -v
```

Expected: missing `app.evaluation.ap` symbols.

- [ ] **Step 3: Implement input/result dataclasses and deterministic sort**

Create `backend/app/evaluation/ap.py` with:

```python
from dataclasses import dataclass

from app.evaluation.matching import bbox_iou


@dataclass(frozen=True)
class EvaluationGroundTruth:
    recording_id: str
    manifest_order: int
    t_start_s: float
    t_end_s: float
    f_low_hz: float
    f_high_hz: float
    class_id: int
    class_name: str


@dataclass(frozen=True)
class EvaluationPrediction:
    recording_id: str
    manifest_order: int
    t_start_s: float
    t_end_s: float
    f_low_hz: float
    f_high_hz: float
    class_id: int
    class_name: str
    confidence: float


@dataclass(frozen=True)
class AveragePrecisionResult:
    ap: float | None
    gt_count: int
    prediction_count: int


def prediction_sort_key(pred: EvaluationPrediction):
    return (
        -pred.confidence,
        pred.manifest_order,
        pred.t_start_s,
        pred.f_low_hz,
        pred.t_end_s,
        pred.f_high_hz,
        pred.class_id,
    )
```

Do not use detection UUIDs in sorting.

- [ ] **Step 4: Implement ranked greedy matching and 101-point AP**

Implement:

```python
def average_precision_at_iou(
    ground_truths: list[EvaluationGroundTruth],
    predictions: list[EvaluationPrediction],
    *,
    iou_threshold: float,
    class_id: int | None,
) -> AveragePrecisionResult:
```

Semantics:

- `class_id=None` means class-agnostic localization AP.
- a numeric class ID means filter GT and predictions to that exact class before matching.
- group eligible GT by `recording_id`.
- rank predictions with `prediction_sort_key`.
- for each prediction, select the unmatched GT from the same Recording with maximum IoU.
- if max IoU `>= iou_threshold`, append TP=1/FP=0 and mark that GT matched; else TP=0/FP=1.
- if GT count is zero, return `ap=None` even when predictions exist.
- if GT exists but predictions are empty, return `ap=0.0`.
- cumulative P/R drives 101 recall points `0.00 ... 1.00`; each interpolated precision is max precision where observed recall >= target, else zero.

Use `bbox_iou()` by converting dataclasses to the existing four-key dict shape. Do not duplicate the IoU formula.

- [ ] **Step 5: Implement AP50/AP50:95 summaries**

Define exact thresholds:

```python
IOU_THRESHOLDS = tuple(round(0.50 + 0.05 * i, 2) for i in range(10))
```

Return structured dataclasses:

```python
@dataclass(frozen=True)
class APSummary:
    ap50: float | None
    ap50_95: float | None
    per_threshold: tuple[tuple[float, float | None], ...]

@dataclass(frozen=True)
class PerClassAP:
    class_id: int
    class_name: str
    gt_count: int
    prediction_count: int
    ap50: float | None
    ap50_95: float | None

@dataclass(frozen=True)
class ClassAwareAPSummary:
    map50: float | None
    map50_95: float | None
    per_class: tuple[PerClassAP, ...]
```

`localization_ap_summary()` computes class-agnostic AP at all 10 thresholds.

`class_aware_ap_summary()` enumerates the union of class IDs appearing in GT or predictions, but macro mAP averages only classes with `gt_count > 0`. A zero-GT class remains in `per_class` with AP fields `None` and visible prediction count.

- [ ] **Step 6: Run AP tests GREEN**

```bash
pytest backend/tests/test_evaluation_ap.py -v
```

Expected: all pass.

- [ ] **Step 7: Prove M8.5 matching file is untouched**

```bash
git diff feature/v1-core -- backend/app/evaluation/matching.py
```

Expected: empty diff.

- [ ] **Step 8: Commit Task 5**

```bash
git add backend/app/evaluation/ap.py backend/tests/test_evaluation_ap.py
git commit -m "feat: add physical tf dataset ap evaluator"
```

---

### Task 6: Aggregate dataset operating-point diagnostics from M8.5 matching

**Files:**
- Create: `backend/app/evaluation/dataset_metrics.py`
- Create: `backend/tests/test_dataset_metrics.py`

**Interfaces:**
- Consumes `EvaluationGroundTruth` / `EvaluationPrediction` grouped by Recording.
- Produces localization operating P/R/F1 for all pipelines.
- Produces matched-classification/confusions and overall/per-class class-aware operating metrics only when classification applies.

- [ ] **Step 1: Write diagnostic tests first**

Create `backend/tests/test_dataset_metrics.py` covering:

```text
two recordings aggregate TP/FP/FN from per-recording Hungarian matches
wrong-class localized pair contributes classification wrong + predicted-class FP + GT-class FN
unmatched prediction contributes per-class FP
unmatched GT contributes per-class FN
matched_count=0 -> matched_accuracy=None
detection-only -> classification and class-aware results are None
confusion pairs aggregate and sort by (gt_class_id,pred_class_id)
```

Include a case where two boxes from different Recordings overlap perfectly in coordinates and prove they are evaluated separately.

- [ ] **Step 2: Run RED**

```bash
pytest backend/tests/test_dataset_metrics.py -v
```

Expected: missing module/symbols.

- [ ] **Step 3: Implement a pure dataset diagnostics module**

Create `backend/app/evaluation/dataset_metrics.py`.

Use a small input container:

```python
@dataclass(frozen=True)
class EvaluationSample:
    recording_id: str
    manifest_order: int
    ground_truths: tuple[EvaluationGroundTruth, ...]
    predictions: tuple[EvaluationPrediction, ...]
```

For every sample:

1. Convert GT/predictions to existing `match_predictions()` box dicts.
2. Run `match_predictions(..., iou_threshold=0.5)` exactly once.
3. Accumulate localization TP/FP/FN from `calculate_detection_metrics()`.
4. If classification is applicable, reuse that same `MatchResult` for class correctness/confusion and class-aware counts.

Return dataclasses that serialize cleanly later:

```python
@dataclass(frozen=True)
class OperatingMetrics:
    tp: int
    fp: int
    fn: int
    precision: float
    recall: float
    f1: float

@dataclass(frozen=True)
class MatchedClassificationDiagnostics:
    matched_count: int
    class_correct: int
    class_wrong: int
    matched_accuracy: float | None
    confusions: tuple[ClassificationConfusion, ...]

@dataclass(frozen=True)
class PerClassOperatingMetrics:
    class_id: int
    class_name: str
    tp: int
    fp: int
    fn: int
    precision: float
    recall: float
    f1: float

@dataclass(frozen=True)
class DatasetDiagnostics:
    localization: OperatingMetrics
    classification: MatchedClassificationDiagnostics | None
    class_aware: OperatingMetrics | None
    per_class: tuple[PerClassOperatingMetrics, ...]
```

Do not perform any AP calculation in this module.

- [ ] **Step 4: Run diagnostic tests GREEN**

```bash
pytest backend/tests/test_dataset_metrics.py -v
```

Expected: all pass.

- [ ] **Step 5: Run M8.5 evaluation regression tests**

```bash
pytest backend/tests/test_evaluation_matching.py backend/tests/test_evaluation_classification.py backend/tests/test_algorithm_lab_compare.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit Task 6**

```bash
git add backend/app/evaluation/dataset_metrics.py backend/tests/test_dataset_metrics.py
git commit -m "feat: add dataset operating diagnostics"
```

---

### Task 7: Load frozen benchmark inputs in bulk and compute/persist results in a subprocess worker

**Files:**
- Create: `backend/app/benchmarks/loader.py`
- Create: `backend/app/benchmarks/job_manager.py`
- Create: `backend/app/benchmarks/worker.py`
- Extend: `backend/app/benchmarks/service.py`
- Create: `backend/tests/test_benchmark_worker.py`

**Interfaces:**
- Produces `BenchmarkInputLoader.load(evaluation_id) -> LoadedBenchmark` without N+1 queries.
- Produces `LocalBenchmarkJobManager.start(evaluation_id) -> int`.
- Produces `execute_benchmark(evaluation_id, settings=None) -> None`.
- Service adds `start_evaluation()`, `retry_evaluation()`, `mark_stale_running_evaluations_interrupted()`.

- [ ] **Step 1: Write worker/lifecycle tests first**

Create `backend/tests/test_benchmark_worker.py` with tests for:

```text
pending -> running -> completed
final JSON contains localization AP + operating metrics
classification-capable run stores class-aware mAP/per-class/confusion
classification detection-only run stores N/A as null and never zero
worker exception -> failed + error metadata + no formal result JSON
stale running -> interrupted on startup recovery
retry failed/interrupted keeps exact same item IDs/run IDs/protocol/hash
completed evaluation cannot rerun
```

Use a tiny two-Recording dataset with deterministic GT/predictions so expected AP and P/R/F1 can be asserted exactly or with `pytest.approx`.

For atomicity, inject/monkeypatch the calculation function to raise after loading and assert:

```python
assert evaluation.aggregate_metrics_json is None
assert evaluation.per_class_metrics_json is None
assert evaluation.confusion_json is None
assert evaluation.status == "failed"
```

- [ ] **Step 2: Run RED**

```bash
pytest backend/tests/test_benchmark_worker.py -v
```

Expected: missing loader/worker/job manager methods.

- [ ] **Step 3: Implement bulk loader**

Create `backend/app/benchmarks/loader.py`.

Load `DatasetEvaluationItemModel` rows ordered by `manifest_order` in one query. Then use joins filtered by `evaluation_id` to load:

- all included Recording rows;
- all included AnalysisRun rows;
- all GT rows joined by item.recording_id;
- all DetectionResult rows joined by item.analysis_run_id.

Do not execute one GT/detection query per item.

Return:

```python
@dataclass(frozen=True)
class LoadedBenchmark:
    samples: tuple[EvaluationSample, ...]
    ground_truths: tuple[EvaluationGroundTruth, ...]
    predictions: tuple[EvaluationPrediction, ...]
    runs_by_recording: dict[str, AnalysisRunModel]
    recordings_by_id: dict[str, RecordingModel]
```

Predictions carry the item's frozen `manifest_order`, not database detection order.

- [ ] **Step 4: Implement classification applicability consistency check**

Before class-aware computation, determine applicability for every included `(run, recording)` through `classification_applicability()`.

All included items must resolve to the same `(applicable, reason)` pair. If not, fail with:

```text
INCONSISTENT_CLASSIFICATION_SEMANTICS
```

This prevents a benchmark from silently mixing classification-capable and unknown/detection-only semantics.

- [ ] **Step 5: Implement result JSON construction**

The worker must store these exact JSON shapes.

`aggregate_metrics_json`:

```python
{
    "classification_applicable": bool,
    "classification_reason": str | None,
    "localization": {
        "ap50": float,
        "ap50_95": float,
        "operating": {"tp": int, "fp": int, "fn": int, "precision": float, "recall": float, "f1": float},
    },
    "classification_on_matched": None | {
        "matched_count": int,
        "class_correct": int,
        "class_wrong": int,
        "matched_accuracy": float | None,
    },
    "class_aware": None | {
        "map50": float | None,
        "map50_95": float | None,
        "operating": {"tp": int, "fp": int, "fn": int, "precision": float, "recall": float, "f1": float},
    },
}
```

`per_class_metrics_json` is `[]` for detection-only; otherwise one row per union class:

```python
{
    "class_id": int,
    "class_name": str,
    "gt_count": int,
    "prediction_count": int,
    "ap50": float | None,
    "ap50_95": float | None,
    "operating": {"tp": int, "fp": int, "fn": int, "precision": float, "recall": float, "f1": float},
}
```

`confusion_json` is `None` for detection-only; otherwise a sorted list of wrong-class confusion dicts.

- [ ] **Step 6: Implement worker lifecycle and progress**

Create `backend/app/benchmarks/worker.py` with the same standalone-process pattern as `app.analysis.worker`:

```python
def execute_benchmark(evaluation_id: str, settings: Settings | None = None) -> None:
    # open its own Database/session
    # load domain models, create tables, run additive migrations
    # set running + started_at + progress_stage="loading"; commit
    # load frozen inputs
    # update progress_stage="diagnostics"; commit
    # compute DatasetDiagnostics
    # progress_stage="localization_ap"; commit
    # compute localization AP
    # if applicable: progress_stage="class_aware_ap"; commit; compute class-aware AP
    # build every final result dict in memory
    # progress_stage="finalizing"; commit
    # ONE final transaction writes aggregate/per-class/confusion, item counts,
    # status="completed", completed_at, progress values
```

On any exception, open a recovery session and set:

```text
status=failed
error_type=<PlatformError.code or exception class>
error_message=<max 1000 chars>
completed_at=now
```

Do not write partial formal metrics before the final transaction.

Add CLI behavior:

```text
python -m app.benchmarks.worker <evaluation_id>
```

- [ ] **Step 7: Implement benchmark job manager and service lifecycle methods**

Create `backend/app/benchmarks/job_manager.py` analogous to the existing analysis job manager, launching:

```python
[sys.executable, "-m", "app.benchmarks.worker", evaluation_id]
```

The service methods must enforce:

```text
start: pending only
retry: failed or interrupted only
completed: never rerun
```

`mark_stale_running_evaluations_interrupted(session)` updates all `running` benchmark rows to:

```text
status=interrupted
error_type=BENCHMARK_INTERRUPTED
error_message="Previous local benchmark process ended before platform restart."
completed_at=now
```

Retry clears error/progress final fields as appropriate but never changes items, manifest hash, protocol, pipeline identity, or membership.

- [ ] **Step 8: Run worker tests GREEN**

```bash
pytest backend/tests/test_benchmark_worker.py -v
```

Expected: all pass.

- [ ] **Step 9: Commit Task 7**

```bash
git add backend/app/benchmarks/loader.py backend/app/benchmarks/job_manager.py backend/app/benchmarks/worker.py backend/app/benchmarks/service.py backend/tests/test_benchmark_worker.py
git commit -m "feat: execute dataset benchmarks in background"
```

---

### Task 8: Expose M8.6A backend APIs and comparability rules

**Files:**
- Extend: `backend/app/benchmarks/schema.py`
- Create: `backend/app/benchmarks/router.py`
- Extend: `backend/app/benchmarks/service.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_benchmark_api.py`

**Interfaces:**
- Exposes preparation, resolution, create/list/get/items/start/retry/compare operations.
- App state exposes `benchmark_job_manager` separately from the existing analysis job manager.

- [ ] **Step 1: Write API tests first**

Create `backend/tests/test_benchmark_api.py` covering these exact routes:

```text
POST /api/dataset-benchmarks/prepare
POST /api/dataset-benchmarks/resolve-runs
POST /api/dataset-benchmarks
GET  /api/dataset-benchmarks
GET  /api/dataset-benchmarks/{evaluation_id}
GET  /api/dataset-benchmarks/{evaluation_id}/items
POST /api/dataset-benchmarks/{evaluation_id}/run
POST /api/dataset-benchmarks/{evaluation_id}/retry
POST /api/dataset-benchmarks/compare
```

Tests must assert stable business-error shapes for stale manifest, ambiguous snapshot preview, illegal lifecycle transition, and incompatible comparison.

- [ ] **Step 2: Run API tests RED**

```bash
pytest backend/tests/test_benchmark_api.py -v
```

Expected: 404/missing route failures.

- [ ] **Step 3: Finish read/response schemas**

Add models:

```python
class DatasetManifestEntryRead(BaseModel):
    manifest_order: int
    recording_id: str
    recording_name: str
    gt_count: int

class DatasetManifestPreviewRead(BaseModel):
    dataset_name: str
    dataset_split: str
    label_space: str
    recording_manifest_hash: str
    expected_recordings: int
    entries: list[DatasetManifestEntryRead]

class RunResolutionEntryRead(BaseModel):
    manifest_order: int
    recording_id: str
    recording_name: str
    resolution: Literal["resolved", "missing", "ambiguous"]
    candidate_run_ids: list[str]

class DatasetEvaluationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    # expose every persistent identity/status/coverage/protocol/progress/error field,
    # plus result JSON fields when completed

class DatasetEvaluationItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    # expose id/evaluation_id/manifest_order/recording_id/analysis_run_id/status/counts/error_reason
```

For compare, define:

```python
class DatasetBenchmarkCompareRequest(BaseModel):
    evaluation_a_id: str
    evaluation_b_id: str

class DatasetBenchmarkCompareResponse(BaseModel):
    comparable: bool
    reasons: list[str]
    evaluation_a_id: str
    evaluation_b_id: str
    aggregate_a: dict | None
    aggregate_b: dict | None
    deltas: dict[str, float | None]
```

The V1 delta dict must include, when both numeric:

```text
localization_ap50
localization_ap50_95
class_aware_map50
class_aware_map50_95
matched_accuracy
```

- [ ] **Step 4: Implement list/get/items and comparability service methods**

`compare()` is directly comparable only if both are `completed`, both coverage equals 1.0, and these fields match exactly:

```text
dataset_name
dataset_split
label_space
recording_manifest_hash
evaluation_protocol
protocol_config_json
```

Return explicit reason codes for each failed condition; do not throw merely because two valid completed evaluations are non-comparable. Throw only for missing evaluation IDs.

- [ ] **Step 5: Implement routes and app integration**

Create `backend/app/benchmarks/router.py` using the repository's existing session-per-request pattern.

Modify `backend/app/main.py`:

```python
from app.benchmarks.job_manager import LocalBenchmarkJobManager
from app.benchmarks.router import router as benchmarks_router
from app.benchmarks.service import mark_stale_running_evaluations_interrupted
```

Initialize:

```python
app.state.benchmark_job_manager = LocalBenchmarkJobManager(settings)
```

After DB initialization, run both recovery functions in the same startup recovery block/session:

```python
mark_stale_running_runs_interrupted(recovery_session)
mark_stale_running_evaluations_interrupted(recovery_session)
```

Include `benchmarks_router` without altering existing route behavior.

- [ ] **Step 6: Run API tests GREEN**

```bash
pytest backend/tests/test_benchmark_api.py -v
```

Expected: all pass.

- [ ] **Step 7: Run startup recovery regression tests**

```bash
pytest backend/tests/test_analysis_runs.py backend/tests/test_benchmark_worker.py -v
```

Expected: existing AnalysisRun stale recovery still passes and new benchmark recovery passes.

- [ ] **Step 8: Commit Task 8**

```bash
git add backend/app/benchmarks backend/app/main.py backend/tests/test_benchmark_api.py backend/tests/test_analysis_runs.py
git commit -m "feat: expose dataset benchmark core api"
```

If `test_analysis_runs.py` did not require modification, omit it from `git add` rather than touching it gratuitously.

---

### Task 9: End-to-end tiny benchmark acceptance and full backend regression

**Files:**
- Extend: `backend/tests/test_benchmark_api.py`
- Create: `docs/research/m8_6a_dataset_benchmark_core.md`

**Interfaces:**
- Proves the whole M8.6A path from frozen manifest through background execution on tiny data.
- Documents protocol and M8.6A scope for later M8.6B/C.

- [ ] **Step 1: Add one tiny end-to-end acceptance test**

Add a test that creates two Ground-Truth-bearing Recordings, one completed classification-capable run per Recording, and deterministic predictions. Through HTTP:

```text
prepare manifest
-> create frozen evaluation
-> start benchmark
-> poll until completed
-> GET detail
-> GET items
```

Assert at least:

```text
status == completed
expected_recordings == 2
evaluated_recordings == 2
missing_recordings == 0
coverage == 1.0
comparable == true
evaluation_protocol == physical_tf_detection_ap_v1
localization AP fields are numeric
classification_applicable == true
class-aware mAP fields are numeric
item run IDs exactly equal the frozen input run IDs
```

Then create a newer run for one Recording and re-fetch the completed evaluation; assert the stored item still points to the original run.

- [ ] **Step 2: Run the end-to-end test RED/GREEN as needed**

First run the new test before any fixes and record any legitimate integration RED. Then make only the minimal integration fixes needed.

Run:

```bash
pytest backend/tests/test_benchmark_api.py -v
```

Expected final state: all pass.

- [ ] **Step 3: Write the M8.6A research note**

Create `docs/research/m8_6a_dataset_benchmark_core.md` recording:

```text
M8.6A scope
physical_tf_detection_ap_v1 protocol summary
M8.5 Hungarian vs M8.6 AP distinction
frozen Recording + run membership semantics
detection-only N/A semantics
worker lifecycle/retry semantics
explicit statement that Batch Package and UI are not implemented yet
```

Do not copy future M8.6B/C results into this note.

- [ ] **Step 4: Run all targeted benchmark/evaluation tests**

```bash
pytest \
  backend/tests/test_benchmark_models.py \
  backend/tests/test_benchmark_manifest.py \
  backend/tests/test_benchmark_membership.py \
  backend/tests/test_evaluation_ap.py \
  backend/tests/test_dataset_metrics.py \
  backend/tests/test_benchmark_worker.py \
  backend/tests/test_benchmark_api.py \
  backend/tests/test_evaluation_matching.py \
  backend/tests/test_evaluation_classification.py \
  backend/tests/test_algorithm_lab_compare.py \
  -v
```

Expected: zero failures.

- [ ] **Step 5: Run the full backend suite fresh**

```bash
pytest backend/tests -v
```

Expected: zero failures. Record actual passed count; do not predict the number in advance.

If a timeout occurs, follow systematic debugging. Do not label it environmental until isolated/repeated evidence supports that conclusion.

- [ ] **Step 6: Verify scope and forbidden changes**

```bash
git diff feature/v1-core..HEAD --stat
git diff feature/v1-core..HEAD --name-status
git diff feature/v1-core..HEAD -- backend/app/evaluation/matching.py
git status --short --branch
```

Requirements:

```text
matching.py diff = empty
no frontend files
no imported_runs behavior changes
no Pipeline/DetectionResult/Recording contract changes
no large data/model/archive/database artifacts
working tree clean after final commit
```

- [ ] **Step 7: Commit Task 9**

```bash
git add backend/tests/test_benchmark_api.py docs/research/m8_6a_dataset_benchmark_core.md
git commit -m "docs: record m8.6a benchmark core"
```

If minimal integration fixes were needed in Step 2, include only those exact files in this commit and describe them in the completion report.

---

### Task 10: Preserve approved spec/plan, verify branch, and push only the M8.6A feature branch

**Files:**
- Add: `docs/superpowers/specs/2026-09-06-m8-6-dataset-benchmark-design.md`
- Add: `docs/superpowers/plans/2026-09-06-m8-6a-dataset-benchmark-core.md`

**Interfaces:**
- Makes the approved architecture and implementation provenance travel with the feature branch.
- Does not merge into `feature/v1-core`.

- [ ] **Step 1: Verify branch ancestry before pushing**

```bash
git branch --show-current
git merge-base --is-ancestor f2c28b3b13e3ecfdaf5f366d23ca90d758cd1c4d HEAD
git status --short --branch
```

Expected:

```text
branch = feature/m8-6a-benchmark-core
merge-base command exit 0
no tracked/staged changes except the two approved doc files if not committed yet
```

- [ ] **Step 2: Add the approved spec and this plan verbatim**

Place the two user-approved documents at the exact paths listed above. Do not rewrite their semantics during implementation.

- [ ] **Step 3: Commit docs if they are not already committed**

```bash
git add docs/superpowers/specs/2026-09-06-m8-6-dataset-benchmark-design.md docs/superpowers/plans/2026-09-06-m8-6a-dataset-benchmark-core.md
git commit -m "docs: add m8.6 benchmark design and core plan"
```

If both files were intentionally committed earlier on the branch, skip this commit and report their existing commit SHA instead.

- [ ] **Step 4: Re-run full backend verification after the final tree is fixed**

```bash
pytest backend/tests -v
```

Expected: zero failures. This fresh run is the completion evidence for the exact commit tree to be pushed.

- [ ] **Step 5: Verify Git cleanliness and diff**

```bash
git status --short --branch
git diff feature/v1-core..HEAD --stat
git log --oneline --decorate -15
```

Require no uncommitted tracked/staged changes.

- [ ] **Step 6: Push only the feature branch**

```bash
git push -u origin feature/m8-6a-benchmark-core
```

No force. Do not merge `feature/v1-core`; do not touch `main`; do not start M8.6B or M8.6C.

- [ ] **Step 7: Produce completion report and STOP**

Report:

```text
M8.6A Dataset Benchmark Core Report

1. Branch + HEAD
2. Base SHA / ancestry
3. Files created/modified
4. DatasetEvaluation schema
5. Manifest/hash semantics
6. Membership freeze semantics
7. AP protocol implementation
   - AP interpolation
   - IoU thresholds
   - localization AP
   - class-aware mAP
   - deterministic confidence ordering
8. Dataset operating diagnostics
9. Detection-only classification N/A behavior
10. Worker lifecycle/recovery/retry
11. Atomic result persistence
12. API routes
13. TDD RED/GREEN evidence by task
14. Targeted test results
15. Full backend suite actual pass/fail count
16. Tiny end-to-end benchmark result
17. M8.5 regression status
18. Scope confirmation
   - matching.py changed? expected NO
   - Batch Package implemented? expected NO
   - frontend implemented? expected NO
   - Redis/Celery/BatchRun/Dataset added? expected NO
19. Git commits
20. Remote feature branch SHA
21. Working tree status
22. Problems/warnings
23. Final verdict PASS / FAIL
```

STOP after the report.

---

## Plan Self-Review Notes

- **Spec coverage:** M8.6A sections 5-10, 12-13, 15-16, 18-20 are covered. Batch Package and frontend requirements are intentionally deferred to M8.6B/C per the approved phase split.
- **No placeholders:** No TBD/TODO/“similar to” implementation gaps remain. Exact routes, result shapes, lifecycle rules, protocol constants, and test cases are specified.
- **Type consistency:** `EvaluationGroundTruth`, `EvaluationPrediction`, `EvaluationSample`, `DatasetEvaluationModel`, and `DatasetEvaluationItemModel` are defined before downstream use. AP and diagnostic result semantics are kept separate.
- **Compatibility:** M8.5 Hungarian logic is reused for diagnostics and explicitly excluded from AP; the plan requires `matching.py` to remain unchanged.
