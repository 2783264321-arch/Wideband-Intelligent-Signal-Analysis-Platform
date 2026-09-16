# WISA V1.1 UX-B Data Library and Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Data Library a task-oriented surface (Datasets and
Standalone Samples) backed by narrow read APIs, and add fail-safe lifecycle
(delete/unregister) with a shared dependency preflight — without adding a
dataset persistence table and without ever deleting externally owned source
files.

**Architecture:** Add a deterministic dataset projection over existing
`RecordingModel` metadata (no new table), a new read-only
`/api/data-library` FastAPI module, and a shared
`app/lifecycle` preflight service consumed by three DELETE endpoints. Frontend:
new `features/data-library` components and a `DataLibraryPage` that replaces the
old `RecordingsPage`.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 + Pydantic v2 + pytest; React +
TypeScript + Vite + Ant Design + Vitest.

**Spec:**
`docs/superpowers/specs/2026-09-16-v1-1-ux-productization-design.md`

## Global Constraints

```text
dataset_name + dataset_split are display components, NOT stable identity.
The dataset projection MUST expose a stable opaque dataset_projection_id whose
    deterministic inputs include source, dataset_name, dataset_split, label_space
    and a normalized dataset-root identity.
NO persisted dataset table. NO platform.db replication. NO new column required.
The raw filesystem path MUST NOT be the URL/API identity.
Two independently registered roots with identical display fields MUST remain
    two distinct projections and MUST never merge members.
Every destructive operation runs the shared dependency preflight and FAILS
    CLOSED with a structured 409 conflict when a retained dependent exists.
Dataset removal is ALL-OR-NOTHING: one blocked member prevents all removal.
Externally owned SpaceNet files are NEVER deleted.
WISA-managed standalone IQ may be deleted only after preflight succeeds.
AnalysisRun deletion is fail-closed when referenced.
Preserve AnalysisRun and BAPv1 internal contracts; introduce no BAPv2 and no
    second artifact schema. No Remote-GPU workflow. No GPU.
Bilingual zh-CN / en-US. Business state in URL. No sealed-baseline changes.
```

---

## File Map

Create (backend):

```text
backend/app/datasets/projection.py
backend/app/data_library/__init__.py
backend/app/data_library/schema.py
backend/app/data_library/service.py
backend/app/data_library/router.py
backend/app/lifecycle/__init__.py
backend/app/lifecycle/schema.py
backend/app/lifecycle/preflight.py
backend/app/lifecycle/service.py

backend/tests/test_dataset_projection.py
backend/tests/test_data_library_api.py
backend/tests/test_lifecycle_preflight.py
backend/tests/test_lifecycle_deletion_api.py
```

Create (frontend):

```text
frontend/src/features/data-library/types.ts
frontend/src/features/data-library/DatasetList.tsx
frontend/src/features/data-library/DatasetSamplesTable.tsx
frontend/src/features/data-library/DatasetAnalysisHistory.tsx
frontend/src/features/data-library/StandaloneSampleList.tsx
frontend/src/features/data-library/DeleteConfirmModal.tsx
frontend/src/features/data-library/DeleteConflictAlert.tsx
frontend/src/features/data-library/AddDataMenu.tsx
frontend/src/features/data-library/ImportResultsMenu.tsx
frontend/src/features/data-library/ImportStandaloneIqModal.tsx
frontend/src/pages/DataLibraryPage.tsx
frontend/src/pages/DatasetDetailPage.tsx
frontend/src/pages/StandaloneSampleDetailPage.tsx

frontend/src/features/data-library/dataLibrary.test.tsx
frontend/src/features/data-library/DatasetList.test.tsx
frontend/src/features/data-library/DatasetSamplesTable.test.tsx
frontend/src/features/data-library/StandaloneSampleList.test.tsx
frontend/src/features/data-library/DeleteConfirmModal.test.tsx
frontend/src/pages/DataLibraryPage.test.tsx
frontend/src/pages/DatasetDetailPage.test.tsx
```

Modify (backend):

```text
backend/app/recordings/router.py     (DELETE /api/recordings/{id})
backend/app/analysis/router.py       (DELETE /api/analysis-runs/{id})
backend/app/main.py                  (register data_library router)
```

Modify (frontend):

```text
frontend/src/api/client.ts           (new functions + apiDelete)
frontend/src/api/types.ts            (new read/lifecycle types)
frontend/src/api/client.test.ts      (new mapping tests)
frontend/src/app/App.tsx             (Data Library routes; default -> /data-library)
frontend/src/app/MainLayout.tsx      (one Data Library menu item added after UX-A)
frontend/src/app/App.test.tsx        (default route now /data-library)
frontend/src/localization/messages.en-US.ts
frontend/src/localization/messages.zh-CN.ts
```

Retire (frontend):

```text
frontend/src/pages/RecordingsPage.tsx        (replaced by DataLibraryPage)
frontend/src/pages/RecordingsPage.test.tsx   (replaced by DataLibraryPage.test.tsx)
```

---

## Interfaces

Consumes:

```text
RecordingModel(dataset_name, dataset_split, label_space, source, external_path, ...)
AnalysisRunModel(recording_id)
DatasetEvaluationModel + DatasetEvaluationItemModel(recording_id, analysis_run_id)
DatasetExperimentModel + DatasetExperimentItemModel(recording_id)
DatasetExperimentAttemptModel(analysis_run_id)
app.benchmarks.manifest.canonical_json_bytes
PlatformError(code, message, status_code, details)
```

Produces (consumed by UX-C and frontend):

```python
# app/datasets/projection.py
def normalize_dataset_root(external_path: str, dataset_split: str) -> str
def compute_dataset_projection_id(
    *, source: str, dataset_name: str, dataset_split: str,
    label_space: str | None, normalized_root: str) -> str

# app/lifecycle/preflight.py
@dataclass(frozen=True)
class DeleteBlocker:
    kind: str            # "dataset_evaluation" | "dataset_experiment" | "dataset_experiment_attempt"
    resource_id: str
    reference: str       # "recording" | "analysis_run"
def find_run_blockers(session, run_ids: Sequence[str]) -> list[DeleteBlocker]
def find_recording_blockers(session, recording_ids: Sequence[str]) -> list[DeleteBlocker]
```

HTTP (all new):

```text
GET    /api/data-library/datasets?limit&offset                     -> DatasetProjectionListRead
GET    /api/data-library/datasets/{dataset_projection_id}          -> DatasetProjectionSummaryRead
GET    /api/data-library/datasets/{dataset_projection_id}/samples  -> DatasetSampleListRead
         ?limit&offset&search
GET    /api/data-library/datasets/{dataset_projection_id}/analysis-history
                                                                   -> DatasetAnalysisHistoryListRead
GET    /api/data-library/standalone-samples?limit&offset&search    -> StandaloneSampleListRead
DELETE /api/data-library/datasets/{dataset_projection_id}          -> 204 | 409
DELETE /api/recordings/{recording_id}                              -> 204 | 409
DELETE /api/analysis-runs/{run_id}                                 -> 204 | 409
```

Conflict contract (identical for all three DELETE endpoints):

```json
{
  "error": {
    "code": "RECORDING_DELETE_BLOCKED | DATASET_REMOVE_BLOCKED | ANALYSIS_RUN_DELETE_BLOCKED",
    "message": "human readable, no secrets",
    "details": {
      "blockers": [
        { "kind": "dataset_evaluation", "resource_id": "eval_...", "reference": "recording" }
      ]
    }
  }
}
```

---

## Task B0: SpaceNet Fs derivation contract investigation (bounded, non-blocking)

**Files:**
- Modify: `backend/app/datasets/spacenet.py` (module docstring only)
- Test: `backend/tests/test_spacenet_adapter.py` (one added assertion)

**Interfaces:**
- Consumes: `observation_range`, current derivation.
- Produces: an explicit "derived" declaration in the adapter docstring.

- [ ] Confirm the current derivation in `spacenet.py`:
      `sample_rate_hz = (frequency_high_mhz - frequency_low_mhz) * 1e6` and
      `center_frequency_hz = ((frequency_low_mhz + frequency_high_mhz) / 2) * 1e6`.
- [ ] Investigate the official SpaceNet dataset contract using the dataset
      files already registered locally (`observation_range` JSON) — do not
      download or run inference. Record whether Fs == observation bandwidth is
      guaranteed.
- [ ] Add to the `spacenet.py` module docstring:

```text
Observation-range semantics: `observation_range` is SOURCE metadata. The
sample_rate_hz and center_frequency_hz values produced here are PLATFORM-DERIVED
values (bandwidth and midpoint of observation_range). They are exposed as
derived until the official dataset contract is verified to guarantee that the
complex-IQ sampling rate equals the observation bandwidth. See V1.1 design
specification section 7.
```

- [ ] Add a regression assertion that the derived flags are emitted by the Data
      Library read model (covered by Task B3).
- [ ] Outcome record: the Data Library sample read model exposes
      `sample_rate_derived` / `center_frequency_derived` = `True` for
      `source == "spacenet"` until the contract is verified; the flags are the
      single switch point if verification changes the conclusion.
- [ ] This task does not block any other task.
- [ ] Commit: `docs(ux-b): record SpaceNet derived-metadata assumption`

---

## Task B1: Deterministic dataset projection module

**Files:**
- Create: `backend/app/datasets/projection.py`
- Test: `backend/tests/test_dataset_projection.py`

**Interfaces:**
- Produces: `normalize_dataset_root`, `compute_dataset_projection_id` (above).

- [ ] Write the failing test:

```python
from app.datasets.projection import compute_dataset_projection_id, normalize_dataset_root

def test_root_is_parent_of_split_directory():
    assert normalize_dataset_root(r"D:\SpaceNet-A\test\a.bin", "test") == \
        os.path.normcase(os.path.normpath(r"D:\SpaceNet-A"))

def test_two_roots_with_same_display_fields_get_distinct_ids():
    a = compute_dataset_projection_id(
        source="spacenet", dataset_name="SpaceNet", dataset_split="test",
        label_space="spacenet_14", normalized_root=normalize_dataset_root(r"D:\SpaceNet-A\test\a.bin", "test"))
    b = compute_dataset_projection_id(
        source="spacenet", dataset_name="SpaceNet", dataset_split="test",
        label_space="spacenet_14", normalized_root=normalize_dataset_root(r"E:\SpaceNet-B\test\a.bin", "test"))
    assert a != b
    assert a.startswith("dsproj_")

def test_same_root_is_stable_across_calls():
    args = dict(source="spacenet", dataset_name="SpaceNet", dataset_split="test",
                label_space="spacenet_14", normalized_root="/data/spacenet")
    assert compute_dataset_projection_id(**args) == compute_dataset_projection_id(**args)

def test_projection_id_is_url_safe():
    pid = compute_dataset_projection_id(source="s", dataset_name="n", dataset_split="t",
                                        label_space=None, normalized_root="/r")
    assert re.fullmatch(r"dsproj_[0-9a-f]{32}", pid)
```

- [ ] Run `pytest backend/tests/test_dataset_projection.py -v`; observe failure.
- [ ] Implement `projection.py`:

```python
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from app.benchmarks.manifest import canonical_json_bytes

PROJECTION_ID_PREFIX = "dsproj_"


def normalize_dataset_root(external_path: str, dataset_split: str) -> str:
    parent = Path(external_path).parent
    root = parent.parent if parent.name == dataset_split else parent
    return os.path.normcase(os.path.normpath(str(root)))


def compute_dataset_projection_id(
    *, source: str, dataset_name: str, dataset_split: str,
    label_space: str | None, normalized_root: str,
) -> str:
    payload = {
        "source": source,
        "dataset_name": dataset_name,
        "dataset_split": dataset_split,
        "label_space": label_space,
        "root": normalized_root,
    }
    digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return f"{PROJECTION_ID_PREFIX}{digest[:32]}"
```

- [ ] Rerun focused test; observe pass.
- [ ] Commit: `feat(ux-b): deterministic dataset projection identity`

---

## Task B2: Data Library read schemas + service (projection list/detail)

**Files:**
- Create: `backend/app/data_library/__init__.py`, `schema.py`, `service.py`
- Test: `backend/tests/test_data_library_api.py`

**Interfaces:**
- Produces: `DataLibraryService.list_dataset_projections`,
  `DataLibraryService.get_dataset_projection`,
  `DataLibraryService.projection_members`.

- [ ] Define `data_library/schema.py`:

```python
from datetime import datetime
from typing import Literal
from pydantic import BaseModel


class DatasetProjectionSummaryRead(BaseModel):
    dataset_projection_id: str
    source: str
    dataset_name: str
    dataset_split: str
    label_space: str | None
    sample_count: int
    ground_truth_sample_count: int
    external: bool
    source_location: str | None

class DatasetProjectionListRead(BaseModel):
    items: list[DatasetProjectionSummaryRead]
    total: int
```

- [ ] Implement `service.py` grouping. Key logic:

```python
def _projection_key(self, rec: RecordingModel) -> tuple[str, str, str, str | None, str]:
    root = normalize_dataset_root(rec.external_path, rec.dataset_split or "") if rec.external_path else ""
    return (rec.source or "custom", rec.dataset_name or "", rec.dataset_split or "", rec.label_space, root)

def _projection_id(self, key) -> str:
    source, name, split, label_space, root = key
    return compute_dataset_projection_id(source=source, dataset_name=name,
        dataset_split=split, label_space=label_space, normalized_root=root)
```

- [ ] `list_dataset_projections(limit, offset)` loads
      `select(RecordingModel).where(RecordingModel.dataset_name.is_not(None))`,
      groups in Python by key → summaries, sorts by `(dataset_name, dataset_split, source)`,
      slices `[offset:offset+limit]`, returns `(items, total)`.
- [ ] `get_dataset_projection(projection_id)` raises
      `PlatformError("DATASET_PROJECTION_NOT_FOUND", ..., 404)` when unknown.
- [ ] `projection_members(projection_id)` returns the ordered member
      `RecordingModel` list.
- [ ] Write API tests (using the `client` fixture and the dataset registration
      fixture pattern from `backend/tests/test_spacenet_registration.py`):

```python
def test_two_physical_roots_remain_distinct(client, tmp_path):
    register_spaceNet_root(client, tmp_path / "SpaceNet-A", "test", ["a", "b"])
    register_spaceNet_root(client, tmp_path / "SpaceNet-B", "test", ["c"])
    body = client.get("/api/data-library/datasets").json()
    assert body["total"] == 2
    assert {item["sample_count"] for item in body["items"]} == {2, 1}

def test_unknown_projection_is_404(client):
    assert client.get("/api/data-library/datasets/dsproj_missing").status_code == 404
```

- [ ] Run `pytest backend/tests/test_data_library_api.py -v`; observe pass.
- [ ] Commit: `feat(ux-b): dataset projection read API`

---

## Task B3: Data Library samples + standalone samples (pagination/search)

**Files:**
- Modify: `backend/app/data_library/schema.py`, `service.py`
- Modify: `backend/app/data_library/router.py`
- Test: `backend/tests/test_data_library_api.py`

**Interfaces:**
- Produces: `DatasetSampleRead`, `DatasetSampleListRead`, `StandaloneSampleRead`,
  `StandaloneSampleListRead`, and the corresponding service methods.

- [ ] Add schemas:

```python
class DatasetSampleRead(BaseModel):
    id: str
    name: str
    sample_rate_hz: float
    center_frequency_hz: float
    frequency_low_hz: float
    frequency_high_hz: float
    duration_s: float
    has_ground_truth: bool
    analysis_count: int
    sample_rate_derived: bool
    center_frequency_derived: bool

class DatasetSampleListRead(BaseModel):
    dataset_projection_id: str
    items: list[DatasetSampleRead]
    total: int

class StandaloneSampleRead(BaseModel):
    id: str
    name: str
    source: str
    sample_rate_hz: float
    center_frequency_hz: float
    frequency_low_hz: float
    frequency_high_hz: float
    duration_s: float
    data_format: str
    has_ground_truth: bool
    analysis_count: int

class StandaloneSampleListRead(BaseModel):
    items: list[StandaloneSampleRead]
    total: int
```

  `sample_rate_derived` / `center_frequency_derived` are `source == "spacenet"`
  (the single switch point from Task B0).

- [ ] `list_dataset_samples(projection_id, limit, offset, search)`:
      load members, filter by case-insensitive `search` on `name`, sort by
      `name`, then slice; never load members into the browser.
- [ ] `list_standalone_samples(limit, offset, search)`:
      `select(RecordingModel).where(RecordingModel.dataset_name.is_(None))`,
      optional `search` on name, order by `created_at, id`, slice.
- [ ] Add `analysis_count` via a grouped count of `AnalysisRunModel` per
      recording id.
- [ ] `router.py`:

```python
router = APIRouter(prefix="/api/data-library", tags=["data-library"])

@router.get("/datasets", response_model=DatasetProjectionListRead)
def list_datasets(request: Request, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)): ...

@router.get("/datasets/{dataset_projection_id}", response_model=DatasetProjectionSummaryRead)
def get_dataset(dataset_projection_id: str, request: Request): ...

@router.get("/datasets/{dataset_projection_id}/samples", response_model=DatasetSampleListRead)
def list_samples(dataset_projection_id: str, request: Request,
                 limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                 search: str | None = Query(None, max_length=200)): ...

@router.get("/standalone-samples", response_model=StandaloneSampleListRead)
def list_standalone(request: Request, limit: int = Query(50, ge=1, le=200),
                    offset: int = Query(0, ge=0), search: str | None = Query(None, max_length=200)): ...
```

- [ ] Tests:

```python
def test_dataset_samples_paginate_and_search(client, ...):
    ...  # register 5 samples; ?limit=2&offset=0 -> 2 items total 5; ?search=... -> filtered
def test_standalone_samples_exclude_dataset_members(client, ...):
    ...  # imported custom recording appears; spacenet member does not
```

- [ ] Run focused tests; observe pass.
- [ ] Commit: `feat(ux-b): data library sample pagination and search`

---

## Task B4: Dataset analysis-history projection

**Files:**
- Modify: `backend/app/data_library/schema.py`, `service.py`, `router.py`
- Test: `backend/tests/test_data_library_api.py`

**Interfaces:**
- Produces: `DatasetAnalysisHistoryItemRead`, `DatasetAnalysisHistoryListRead`.

- [ ] Add schemas:

```python
class DatasetAnalysisHistoryItemRead(BaseModel):
    kind: Literal["experiment", "evaluation"]
    resource_id: str
    name: str
    pipeline_id: str
    pipeline_version: str
    status: str
    executor: str | None
    expected_items: int
    completed_items: int
    failed_items: int
    coverage: float | None
    created_at: datetime | None
    dataset_evaluation_id: str | None

class DatasetAnalysisHistoryListRead(BaseModel):
    dataset_projection_id: str
    items: list[DatasetAnalysisHistoryItemRead]
    total: int
```

- [ ] `list_dataset_analysis_history(projection_id)` scopes dataset-level
      results to the projection so two roots with identical display fields do
      not share history:

```python
member_ids = {rec.id for rec in self.projection_members(projection_id)}
# experiments: same (dataset_name, dataset_split, dataset_label_space) AND
#   set(item.recording_id for item in experiment.items) is non-empty and a
#   subset of member_ids
# evaluations: same display fields AND set(item.recording_id for item in
#   evaluation.items) is non-empty and a subset of member_ids
```

- [ ] Map each match to `DatasetAnalysisHistoryItemRead`; experiments use
      `attempt_count`-based `completed_items`/`failed_items` from the existing
      read model logic, evaluations use `evaluated_recordings`/`missing_recordings`.
- [ ] Add `GET /datasets/{dataset_projection_id}/analysis-history`.
- [ ] Tests:

```python
def test_history_is_scoped_to_the_projection_root(client, ...):
    ...  # an evaluation built from root A does not appear under root B
def test_history_reports_experiment_and_evaluation_kinds(client, ...):
    ...  # both kinds present with bounded fields
```

- [ ] Run focused tests; observe pass.
- [ ] Commit: `feat(ux-b): dataset analysis-history projection`

---

## Task B5: Shared dependency preflight

**Files:**
- Create: `backend/app/lifecycle/__init__.py`, `schema.py`, `preflight.py`
- Test: `backend/tests/test_lifecycle_preflight.py`

**Interfaces:**
- Produces: `DeleteBlocker`, `find_run_blockers`, `find_recording_blockers`,
  `DeleteBlockerRead`.

- [ ] Define `lifecycle/preflight.py`:

```python
from dataclasses import dataclass
from sqlalchemy import select
from app.benchmarks.model import DatasetEvaluationItemModel
from app.dataset_experiments.model import DatasetExperimentAttemptModel, DatasetExperimentItemModel

@dataclass(frozen=True)
class DeleteBlocker:
    kind: str
    resource_id: str
    reference: str

def find_run_blockers(session, run_ids: Sequence[str]) -> list[DeleteBlocker]:
    blockers: list[DeleteBlocker] = []
    if not run_ids:
        return blockers
    eval_rows = session.execute(
        select(DatasetEvaluationItemModel.evaluation_id, DatasetEvaluationItemModel.analysis_run_id)
        .where(DatasetEvaluationItemModel.analysis_run_id.in_(run_ids))
    ).all()
    for evaluation_id, _ in eval_rows:
        blockers.append(DeleteBlocker("dataset_evaluation", evaluation_id, "analysis_run"))
    attempt_rows = session.execute(
        select(DatasetExperimentItemModel.experiment_id, DatasetExperimentAttemptModel.analysis_run_id)
        .join(DatasetExperimentItemModel, DatasetExperimentAttemptModel.experiment_item_id == DatasetExperimentItemModel.id)
        .where(DatasetExperimentAttemptModel.analysis_run_id.in_(run_ids))
    ).all()
    for experiment_id, _ in attempt_rows:
        blockers.append(DeleteBlocker("dataset_experiment", experiment_id, "analysis_run"))
    return blockers

def find_recording_blockers(session, recording_ids: Sequence[str]) -> list[DeleteBlocker]:
    blockers: list[DeleteBlocker] = []
    if not recording_ids:
        return blockers
    eval_rows = session.execute(select(DatasetEvaluationItemModel.evaluation_id)
        .where(DatasetEvaluationItemModel.recording_id.in_(recording_ids))).all()
    for (evaluation_id,) in eval_rows:
        blockers.append(DeleteBlocker("dataset_evaluation", evaluation_id, "recording"))
    exp_rows = session.execute(select(DatasetExperimentItemModel.experiment_id)
        .where(DatasetExperimentItemModel.recording_id.in_(recording_ids))).all()
    for (experiment_id,) in exp_rows:
        blockers.append(DeleteBlocker("dataset_experiment", experiment_id, "recording"))
    owned = session.scalars(select(AnalysisRunModel.id).where(AnalysisRunModel.recording_id.in_(recording_ids))).all()
    blockers.extend(find_run_blockers(session, list(owned)))
    return _dedupe(blockers)
```

- [ ] `lifecycle/schema.py`:

```python
class DeleteBlockerRead(BaseModel):
    kind: Literal["dataset_evaluation", "dataset_experiment", "dataset_experiment_attempt"]
    resource_id: str
    reference: Literal["recording", "analysis_run"]

def blocker_details(blockers: Sequence[DeleteBlocker]) -> dict:
    return {"blockers": [DeleteBlockerRead(kind=b.kind, resource_id=b.resource_id,
                                            reference=b.reference).model_dump() for b in blockers]}
```

- [ ] Tests: create an evaluation item referencing a recording and its run;
      assert `find_recording_blockers` returns both a `recording` reference and
      the `analysis_run` reference; assert dedupe; assert empty inputs return
      `[]`.
- [ ] Run `pytest backend/tests/test_lifecycle_preflight.py -v`; observe pass.
- [ ] Commit: `feat(ux-b): shared deletion dependency preflight`

---

## Task B6: Deletion service + DELETE endpoints (fail-closed, atomic)

**Files:**
- Create: `backend/app/lifecycle/service.py`
- Modify: `backend/app/recordings/router.py`, `backend/app/analysis/router.py`,
  `backend/app/data_library/router.py`, `backend/app/main.py`
- Test: `backend/tests/test_lifecycle_deletion_api.py`

**Interfaces:**
- Produces: `delete_standalone_recording`, `delete_analysis_run`,
  `remove_dataset_projection`.

- [ ] Implement `lifecycle/service.py`:

```python
def delete_standalone_recording(session, storage, data_root, recording_id: str) -> None:
    rec = session.get(RecordingModel, recording_id)
    if rec is None:
        raise PlatformError("RECORDING_NOT_FOUND", "Recording not found.", 404)
    if rec.dataset_name is not None:
        raise PlatformError("RECORDING_IS_DATASET_MEMBER",
            "Dataset members are removed through the dataset; use Remove Dataset.", 409,
            {"dataset_name": rec.dataset_name, "dataset_split": rec.dataset_split})
    blockers = find_recording_blockers(session, [recording_id])
    if blockers:
        raise PlatformError("RECORDING_DELETE_BLOCKED",
            "Recording deletion is blocked by retained dependents.", 409, blocker_details(blockers))
    managed = rec.external_path is None and rec.source == "custom"
    session.delete(rec)          # ORM cascade removes owned runs/detections/GT
    session.commit()
    if managed:
        shutil.rmtree(storage.recording_dir(recording_id), ignore_errors=True)

def delete_analysis_run(session, run_id: str) -> None:
    run = session.get(AnalysisRunModel, run_id)
    if run is None:
        raise PlatformError("ANALYSIS_RUN_NOT_FOUND", "Analysis run not found.", 404)
    blockers = find_run_blockers(session, [run_id])
    if blockers:
        raise PlatformError("ANALYSIS_RUN_DELETE_BLOCKED",
            "Analysis run deletion is blocked by retained dependents.", 409, blocker_details(blockers))
    session.delete(run)          # ORM cascade removes detections
    session.commit()

def remove_dataset_projection(session, storage, projection_id: str) -> None:
    members = DataLibraryService(session).projection_members(projection_id)  # 404 inside
    member_ids = [rec.id for rec in members]
    blockers = find_recording_blockers(session, member_ids)
    if blockers:
        raise PlatformError("DATASET_REMOVE_BLOCKED",
            "Dataset removal is blocked by retained dependents; no members were removed.", 409,
            blocker_details(blockers))
    managed_ids = [rec.id for rec in members if rec.external_path is None and rec.source == "custom"]
    for rec in members:
        session.delete(rec)
    session.commit()             # all-or-nothing: preflight ran before any mutation
    for recording_id in managed_ids:
        shutil.rmtree(storage.recording_dir(recording_id), ignore_errors=True)
    # external SpaceNet files are never touched
```

- [ ] Add endpoints:

```python
# recordings/router.py
@router.delete("/{recording_id}", status_code=204)
def delete_recording(recording_id: str, request: Request):
    with request.app.state.database.session_factory() as session:
        delete_standalone_recording(session, request.app.state.storage,
            request.app.state.settings.data_root, recording_id)

# analysis/router.py
@router.delete("/api/analysis-runs/{run_id}", status_code=204)
def delete_run(run_id: str, request: Request):
    with request.app.state.database.session_factory() as session:
        delete_analysis_run(session, run_id)

# data_library/router.py
@router.delete("/datasets/{dataset_projection_id}", status_code=204)
def remove_dataset(dataset_projection_id: str, request: Request):
    with request.app.state.database.session_factory() as session:
        remove_dataset_projection(session, request.app.state.storage, dataset_projection_id)
```

- [ ] Register the `data_library` router in `main.py` alongside the others.
- [ ] Tests:

```python
def test_standalone_delete_succeeds_and_removes_owned_runs(client, session, ...):
    ...
def test_standalone_delete_blocked_by_evaluation_returns_409_blockers(client, ...):
    ...  # assert code == "RECORDING_DELETE_BLOCKED"; blockers[0].kind == "dataset_evaluation"
def test_dataset_member_cannot_be_deleted_as_standalone(client, ...):
    ...  # code == "RECORDING_IS_DATASET_MEMBER"
def test_dataset_removal_is_atomic_when_one_member_is_blocked(client, session, ...):
    ...  # create 3 members; reference one; DELETE -> 409; assert all 3 rows still present
def test_dataset_removal_succeeds_and_leaves_external_files(client, tmp_path, ...):
    ...  # .bin/.json files still exist on disk; DB rows gone
def test_analysis_run_delete_blocked_then_allowed_after_dependent_removed(client, ...):
    ...
def test_delete_endpoints_return_structured_error_shape(client, ...):
    ...  # body has {"error": {"code","message","details"}}
```

- [ ] Run `pytest backend/tests/test_lifecycle_deletion_api.py -v`; observe pass.
- [ ] Commit: `feat(ux-b): fail-safe deletion endpoints`

---

## Task B7: Frontend API client types + functions

**Files:**
- Modify: `frontend/src/api/types.ts`, `frontend/src/api/client.ts`,
  `frontend/src/api/client.test.ts`

**Interfaces:**
- Produces: the TypeScript types and functions listed in the UX-B plan's
  interface contract.

- [ ] Write failing client tests for `listDatasetProjections`,
      `listDatasetSamples`, `deleteRecording` (204), and
      `deleteBlockersFromError`:

```ts
test("maps dataset projections", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({
    items: [{ dataset_projection_id: "dsproj_1", source: "spacenet", dataset_name: "SpaceNet",
      dataset_split: "test", label_space: "spacenet_14", sample_count: 2500,
      ground_truth_sample_count: 2500, external: true, source_location: "D:\\SpaceNet\\test" }],
    total: 1,
  })));
  const page = await listDatasetProjections();
  expect(page.items[0].datasetProjectionId).toBe("dsproj_1");
});

test("deleteRecording resolves on 204 and surfaces blockers on 409", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 204 })));
  await expect(deleteRecording("rec_1")).resolves.toBeUndefined();

  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
    error: { code: "RECORDING_DELETE_BLOCKED", message: "blocked",
             details: { blockers: [{ kind: "dataset_evaluation", resource_id: "eval_1", reference: "recording" }] } },
  }), { status: 409 })));
  const error = await deleteRecording("rec_1").catch((e) => e);
  expect(deleteBlockersFromError(error)).toEqual([
    { kind: "dataset_evaluation", resourceId: "eval_1", reference: "recording" },
  ]);
});
```

- [ ] Add types (exact shapes from the interface contract) and `apiDelete`:

```ts
async function apiDelete(path: string): Promise<void> {
  const response = await fetch(apiUrl(path), { method: "DELETE" });
  if (!response.ok) throw await structuredErrorFromResponse(response);
}
```

- [ ] Implement the read/delete functions with `*Wire` mapping (snake_case →
      camelCase) consistent with the existing client.
- [ ] Implement `deleteBlockersFromError(error: unknown): DeleteBlocker[]`
      reading `PlatformApiError.details.blockers` defensively (empty array when
      absent or malformed).
- [ ] Run `npx vitest run src/api/client.test.ts`; observe pass.
- [ ] Commit: `feat(ux-b): data library client contract`

---

## Task B8: Data Library root page (Datasets / Standalone tabs)

**Files:**
- Create: `frontend/src/features/data-library/types.ts`,
  `DatasetList.tsx`, `StandaloneSampleList.tsx`,
  `DeleteConfirmModal.tsx`, `DeleteConflictAlert.tsx`,
  `frontend/src/pages/DataLibraryPage.tsx`
- Modify: `frontend/src/app/App.tsx` (route `/data-library`)
- Test: `frontend/src/pages/DataLibraryPage.test.tsx`

**Interfaces:**
- Consumes: `listDatasetProjections`, `listStandaloneSamples`,
  `deleteRecording`, `deleteDatasetProjection`.
- Produces: `DataLibraryPage()`; a dataset row navigates to
  `/data-library/datasets/:datasetProjectionId`.

- [ ] Write failing tests:

```tsx
test("renders dataset cards, not member recordings", async () => {
  mockFetch.datasets([{ datasetName: "SpaceNet", datasetSplit: "test", sampleCount: 2500, ... }]);
  render(<DataLibraryPage />, { route: "/data-library" });
  expect(await screen.findByText("SpaceNet")).toBeInTheDocument();
  expect(screen.queryAllByTestId("sample-card")).toHaveLength(0);
});

test("blocked standalone delete shows the conflict reason", async () => {
  mockFetch.deleteRecordingRejects({ code: "RECORDING_DELETE_BLOCKED",
    details: { blockers: [{ kind: "dataset_evaluation", resource_id: "eval_1", reference: "recording" }] } });
  render(<StandaloneSampleList />);
  await user.click(await screen.findByRole("button", { name: "Delete" }));
  await user.click(screen.getByRole("button", { name: "Confirm delete" }));
  expect(await screen.findByTestId("delete-conflict-alert")).toHaveTextContent("eval_1");
});
```

- [ ] Implement `DataLibraryPage` with antd `Tabs` labelled
      `dataLibrary.tabDatasets` / `dataLibrary.tabStandalone`.
- [ ] Add the page-copy keys and render a clear title, one-sentence purpose, and
      the primary action at the top of the root page:

```ts
"dataLibrary.title": "Data Library",       // zh: "数据管理"
"dataLibrary.subtitle": "Manage standalone IQ samples and registered datasets, and start analysis.",
                                           // zh: "管理独立 IQ 样本与已注册数据集，并启动分析。"
"dataLibrary.tabDatasets": "Datasets",     // zh: "数据集"
"dataLibrary.tabStandalone": "Standalone Samples", // zh: "独立样本"
```
- [ ] Implement `DatasetList` (cards showing `datasetName`, `datasetSplit`,
      `sampleCount`, `labelSpace`, ground-truth count, source, external/local,
      display `sourceLocation`) with actions: Browse Samples, Create Dataset
      Experiment (link to `/experiments?datasetProjectionId=...`), Import
      Analysis Results, More (Remove Dataset).
- [ ] Implement `StandaloneSampleList` (bounded paginated list, search box,
      actions: Open Analysis Workspace → `/spectrum/:id`, Analysis History,
      Delete).
- [ ] Implement `DeleteConfirmModal` (explicit irreversible confirmation, names
      the affected resources) and `DeleteConflictAlert` (renders blockers).
- [ ] Add the `/data-library` route to `App.tsx` (this is a UX-A-owned file;
      apply the additive change after UX-A is merged).
- [ ] Run focused tests; observe pass.
- [ ] Commit: `feat(ux-b): data library root page`

---

## Task B9: Dataset detail (Overview / Samples / Analysis History)

**Files:**
- Create: `frontend/src/features/data-library/DatasetSamplesTable.tsx`,
  `DatasetAnalysisHistory.tsx`, `frontend/src/pages/DatasetDetailPage.tsx`
- Test: `frontend/src/pages/DatasetDetailPage.test.tsx`

**Interfaces:**
- Consumes: `getDatasetProjection`, `listDatasetSamples`,
  `listDatasetAnalysisHistory`, `deleteDatasetProjection`.
- Produces: `DatasetDetailPage()`; "Create Dataset Experiment" navigates to
  `/experiments?datasetProjectionId=<id>`; "Open Evaluation" navigates to
  `/experiments?tab=benchmarks`.

- [ ] Write failing tests: overview renders aggregate metadata; samples table
      paginates and searches; derived Fs/Fc are labelled; analysis history rows
      render pipeline/status/coverage; Remove Dataset confirms and calls
      `deleteDatasetProjection`.
- [ ] Implement `DatasetSamplesTable` with server-side `limit`/`offset`/`search`
      (antd `Table` `pagination` + `Input.Search`), columns: name/id, physical
      observation range (`frequencyLowHz`–`frequencyHighHz`), duration, GT
      presence, analysis count, derived badge for SpaceNet Fs/Fc, actions
      view/analyze.
- [ ] Implement `DatasetAnalysisHistory` with contextual actions: Create
      Experiment, Import Batch Analysis Results, Open Evaluation.
- [ ] Implement `DatasetDetailPage` with antd `Tabs` Overview/Samples/Analysis
      History and a Remove Dataset action using `DeleteConfirmModal`.
- [ ] Run focused tests; observe pass.
- [ ] Commit: `feat(ux-b): dataset detail with samples and analysis history`

---

## Task B10: Standalone sample detail + deletion

**Files:**
- Create: `frontend/src/pages/StandaloneSampleDetailPage.tsx`
- Test: `frontend/src/pages/StandaloneSampleDetailPage.test.tsx`

**Interfaces:**
- Consumes: `getRecording`, `listAnalysisRuns`, `deleteRecording`.
- Produces: `StandaloneSampleDetailPage()`; "Open Analysis Workspace"
  navigates to `/spectrum/:id`.

- [ ] Write failing tests: renders recording metadata; shows analysis history
      from `listAnalysisRuns`; Delete calls `deleteRecording` and navigates back
      to `/data-library`; blocked deletion surfaces blockers.
- [ ] Implement the page with `PageHeader` and a delete confirmation.
- [ ] Run focused tests; observe pass.
- [ ] Commit: `feat(ux-b): standalone sample detail and safe deletion`

---

## Task B11: Add Data vs Import Analysis Results

**Files:**
- Create: `frontend/src/features/data-library/AddDataMenu.tsx`,
  `ImportResultsMenu.tsx`, `ImportStandaloneIqModal.tsx`
- Modify: `frontend/src/features/imports/ImportRunModal.tsx` (reuse as-is),
  `frontend/src/features/imports/BatchImportModal.tsx` (reuse as-is),
  `frontend/src/pages/DataLibraryPage.tsx`,
  `frontend/src/localization/messages.en-US.ts`,
  `frontend/src/localization/messages.zh-CN.ts`
- Test: `frontend/src/pages/DataLibraryPage.test.tsx`

**Interfaces:**
- Consumes: existing `importRecording` (multipart), `registerSpaceNetDataset`,
  `importAnalysisPackage`, `importBatchRun`.
- Produces: two menus with the exact user concepts, plus a relocated standalone
  IQ import modal (the inline form currently owned by the retired
  `RecordingsPage`).

- [ ] Write failing tests: the Data Library toolbar shows exactly
      "+ Add Data" and "Import Analysis Results"; the Add Data menu contains
      "Import Standalone IQ" and "Register Dataset"; the Import Analysis Results
      menu contains "Single-sample Analysis Result" and "Dataset Batch Analysis
      Result"; selecting a batch item opens `BatchImportModal`.
- [ ] Add localization keys:

```ts
"dataLibrary.addData": "+ Add Data",                       // zh: "+ 添加数据"
"dataLibrary.importResults": "Import Analysis Results",     // zh: "导入分析结果"
"dataLibrary.importStandaloneIq": "Import Standalone IQ",  // zh: "导入独立 IQ"
"dataLibrary.registerDataset": "Register Dataset",          // zh: "注册数据集"
"dataLibrary.importSingleResult": "Single-sample Analysis Result",
                                                            // zh: "单样本分析结果"
"dataLibrary.importBatchResult": "Dataset Batch Analysis Result",
                                                            // zh: "数据集批量分析结果"
"dataLibrary.fsFcRequiredHint": "Raw IQ files do not always contain acquisition metadata, so sampling rate (Fs) and center frequency (Fc) are required.",
  // zh: "原始 IQ 文件不一定包含采集元数据，因此必须填写采样率（Fs）与中心频率（Fc）。"
```

- [ ] Move the standalone IQ import form (name, file, data format, Fs, Fc,
      optional label space) into `ImportStandaloneIqModal.tsx`, reusing
      `importRecording` unchanged. The modal must display
      `dataLibrary.fsFcRequiredHint` so the required Fs/Fc fields are
      explained, and must not imply the platform reads them from the file.
- [ ] Implement `AddDataMenu` and `ImportResultsMenu`; reuse the existing
      import modals unchanged (no BAPv2, no new package schema).
- [ ] Run focused tests; observe pass.
- [ ] Commit: `feat(ux-b): separate Add Data from Import Analysis Results`

---

## Task B12: Routes, navigation entry, and default route

**Files:**
- Modify: `frontend/src/app/App.tsx`, `frontend/src/app/MainLayout.tsx`,
  `frontend/src/app/App.test.tsx`
- Retire: `frontend/src/pages/RecordingsPage.tsx`,
  `frontend/src/pages/RecordingsPage.test.tsx`
- Test: `frontend/src/app/navigation.test.tsx`

**Interfaces:**
- Consumes: UX-A five-item navigation.
- Produces: `/data-library`, `/data-library/datasets/:datasetProjectionId`,
  `/data-library/samples/:recordingId`; `/recordings` redirects to
  `/data-library`; index redirects to `/data-library`.

- [ ] Apply this task **after** UX-A is accepted (documented sequencing in the
      orchestration plan).
- [ ] Update `App.tsx`:

```tsx
<Route index element={<Navigate to="/data-library" replace />} />
<Route path="recordings" element={<Navigate to="/data-library" replace />} />
<Route path="data-library" element={<DataLibraryPage />} />
<Route path="data-library/datasets/:datasetProjectionId" element={<DatasetDetailPage />} />
<Route path="data-library/samples/:recordingId" element={<StandaloneSampleDetailPage />} />
```

- [ ] Add the Data Library item to the UX-A `MainLayout` nav items array (one
      line) and its path mapping already present.
- [ ] Delete `RecordingsPage.tsx` and `RecordingsPage.test.tsx`.
- [ ] Update `App.test.tsx` default-route assertion to the Data Library title.
- [ ] Run `npx vitest run src/app src/pages`; observe pass.
- [ ] Commit: `feat(ux-b): wire Data Library routes and navigation`

---

## Task B13: Track boundary

- [ ] Focused backend: `pytest backend/tests/test_dataset_projection.py backend/tests/test_data_library_api.py backend/tests/test_lifecycle_preflight.py backend/tests/test_lifecycle_deletion_api.py -v`
- [ ] Full frontend: `npm test -- --run`
- [ ] Production build: `npm run build`
- [ ] Confirm no regression in existing backend suites touched by the DELETE
      router additions: `pytest backend/tests/test_recordings.py backend/tests/test_analysis_runs.py backend/tests/test_benchmark_api.py backend/tests/test_dataset_experiment_api.py -v`
- [ ] Commit: `chore(ux-b): track boundary verification`

---

## Self-Review Checklist

```text
[ ] No dataset table created; no platform.db replication.
[ ] dataset_projection_id deterministic, opaque, URL-safe, root-scoped.
[ ] Two same-named roots never merge members.
[ ] Pagination/search are server-side.
[ ] Preflight is shared and runs before any mutation.
[ ] Dataset removal is all-or-nothing.
[ ] External SpaceNet files are never deleted (asserted in tests).
[ ] Recording deletion cannot bypass run dependency guards.
[ ] AnalysisRun conflict behavior is fail-closed.
[ ] Conflict schema identical across the three DELETE endpoints.
[ ] AnalysisRun and BAPv1 contracts preserved; no BAPv2.
[ ] No Remote-GPU; no GPU.
```
