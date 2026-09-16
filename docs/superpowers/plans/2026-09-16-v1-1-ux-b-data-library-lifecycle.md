# WISA V1.1 UX-B Data Library and Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Data Library a task-oriented surface (Datasets and Standalone
Samples) backed by narrow read APIs; make the opaque `dataset_projection_id` the
authoritative identity through executor selection, experiment creation,
evaluation, and imported-batch resolution; surface imported BAPv1 batches in
Analysis History; and add fail-safe lifecycle deletion with a shared dependency
preflight, managed-file cleanup, and no dataset persistence table.

**Architecture:** A deterministic dataset projection over existing
`RecordingModel` metadata (no new table) plus one shared
`DatasetProjectionResolver`. Two additive nullable `dataset_projection_id`
columns (experiments, evaluations) via the existing additive migration
mechanism. A shared `app/lifecycle` preflight + managed-file quarantine service.
Frontend: `features/data-library` components and a `DataLibraryPage` replacing
`RecordingsPage`.

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
NO persisted dataset table. NO platform.db replication. One additive migration
    adding nullable dataset_projection_id columns to existing tables is allowed.
The raw filesystem path MUST NOT be the URL/API identity.
Two independently registered roots with identical display fields MUST remain
    two distinct projections and MUST never merge members at ANY layer:
    Data Library, /api/executor-selection, DatasetExperiment, DatasetEvaluation,
    or imported-batch resolution.
Every destructive operation runs the shared dependency preflight and FAILS
    CLOSED with a structured 409 conflict when a retained dependent exists,
    including semantic imported-batch state.
Dataset removal is ALL-OR-NOTHING.
Externally owned SpaceNet files are NEVER deleted.
Managed WISA files (standalone custom IQ dirs, single-import package dirs,
    quarantined dirs) have an explicit safe lifecycle.
AnalysisRun deletion is fail-closed when referenced.
Preserve AnalysisRun and BAPv1 internal contracts; introduce no BAPv2 and no
    second artifact schema. No Remote-GPU workflow. No GPU.
Bilingual zh-CN / en-US. Business state in URL. No sealed-baseline changes.
```

---

## File Map

Create (backend):

```text
backend/app/data_library/__init__.py
backend/app/data_library/schema.py
backend/app/data_library/service.py
backend/app/data_library/router.py
backend/app/lifecycle/__init__.py
backend/app/lifecycle/schema.py
backend/app/lifecycle/preflight.py
backend/app/lifecycle/service.py

backend/tests/test_dataset_projection.py
backend/tests/test_dataset_projection_authority.py
backend/tests/test_data_library_api.py
backend/tests/test_lifecycle_preflight.py
backend/tests/test_lifecycle_deletion_api.py
```

Modify (backend):

```text
backend/app/datasets/projection.py           (resolver)
backend/app/datasets/service.py              (no change expected; referenced for root derivation)
backend/app/benchmarks/model.py              (+ dataset_projection_id nullable)
backend/app/benchmarks/schema.py             (+ projection-aware create/read fields)
backend/app/benchmarks/service.py            (+ prepare_projection_manifest; projection-scoped
                                              evaluation; projection-scoped imported-batch resolution)
backend/app/dataset_experiments/model.py     (+ dataset_projection_id nullable)
backend/app/dataset_experiments/schema.py    (+ optional dataset_projection_id; read exposure)
backend/app/dataset_experiments/service.py   (+ projection-scoped create + revalidation)
backend/app/execution_selection/router.py    (+ dataset_projection_id scope)
backend/app/storage/service.py               (+ import_package_dir, quarantine helpers)
backend/app/db/migrations.py                 (+ upgrade_v1_1_dataset_projection)
backend/app/main.py                          (+ data_library router)
backend/app/recordings/router.py             (+ DELETE)
backend/app/analysis/router.py               (+ DELETE)
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

Modify (frontend):

```text
frontend/src/api/client.ts           (+ read/lifecycle functions, + dataset_projection scope)
frontend/src/api/types.ts            (+ read/lifecycle types, + projection fields)
frontend/src/api/client.test.ts      (+ mapping tests)
frontend/src/app/App.tsx             (Data Library routes; default -> /data-library)
frontend/src/app/App.test.tsx        (default route now /data-library)
frontend/src/localization/messages.en-US.ts
frontend/src/localization/messages.zh-CN.ts
```

Retire (frontend):

```text
frontend/src/pages/RecordingsPage.tsx        (replaced by DataLibraryPage)
frontend/src/pages/RecordingsPage.test.tsx   (replaced by DataLibraryPage.test.tsx)
```

Note: UX-A owns `MainLayout.tsx` and already creates the Data Library nav item.
UX-B MUST NOT add a second nav item.

---

## Interfaces

HTTP (all new or extended):

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
GET    /api/executor-selection?pipeline_id&model_release_id&dataset_projection_id   (new scope)
```

Backend Python contracts:

```python
# app/datasets/projection.py
@dataclass(frozen=True)
class DatasetProjection:
    dataset_projection_id: str
    source: str
    dataset_name: str
    dataset_split: str
    label_space: str | None
    normalized_root: str
    source_location: str | None

class DatasetProjectionResolver:
    def __init__(self, session: Session) -> None
    def list(self, limit: int, offset: int) -> tuple[list[DatasetProjection], int]
    def get(self, dataset_projection_id: str) -> DatasetProjection        # 404
    def members(self, dataset_projection_id: str, *, require_ground_truth: bool = False) -> list[RecordingModel]
    def find_for_recording(self, recording: RecordingModel) -> DatasetProjection | None

# app/benchmarks/service.py
def prepare_projection_manifest(self, dataset_projection_id: str) -> ManifestPreview

# app/lifecycle/preflight.py
@dataclass(frozen=True)
class DeleteBlocker:
    kind: str            # "dataset_evaluation" | "dataset_experiment" |
                         # "dataset_experiment_attempt" | "imported_batch"
    resource_id: str
    reference: str       # "recording" | "analysis_run"
def find_run_blockers(session, run_ids: Sequence[str]) -> list[DeleteBlocker]
def find_recording_blockers(session, recording_ids: Sequence[str]) -> list[DeleteBlocker]
```

Conflict contract (identical for all three DELETE endpoints):

```json
{
  "error": {
    "code": "RECORDING_DELETE_BLOCKED | DATASET_REMOVE_BLOCKED | ANALYSIS_RUN_DELETE_BLOCKED",
    "message": "human readable, no secrets",
    "details": {
      "blockers": [
        { "kind": "dataset_evaluation", "resource_id": "eval_...", "reference": "recording" },
        { "kind": "imported_batch", "resource_id": "<import_fingerprint>", "reference": "analysis_run" }
      ]
    }
  }
}
```

---

## Task B0: SpaceNet Fs derivation evidence rule

**Files:**
- Modify: `backend/app/datasets/spacenet.py` (module docstring only)
- Test: `backend/tests/test_spacenet_adapter.py` (one added assertion)

- [ ] Confirm the current derivation:
      `sample_rate_hz = (frequency_high_mhz - frequency_low_mhz) * 1e6`,
      `center_frequency_hz = ((frequency_low_mhz + frequency_high_mhz) / 2) * 1e6`.
- [ ] Do NOT claim the official Fs contract is verified from sample JSON.
      `observation_range` alone cannot establish the sampling-rate contract.
      Follow-up A is marked VERIFIED only if an authoritative dataset
      specification/source explicitly defines the relationship.
- [ ] Record in the `spacenet.py` module docstring:

```text
official Fs contract   = unverified (unless an authoritative source is found)
observation_range      = source metadata
Fs / Fc                = platform-derived
```

- [ ] Outcome record for V1.1: `sample_rate_derived = true` and
      `center_frequency_derived = true` for `source == "spacenet"`; this is the
      single switch point if verification later changes the conclusion.
- [ ] This task does not block any other task.
- [ ] Commit: `docs(ux-b): record SpaceNet derived-metadata evidence rule`

---

## Task B1: Deterministic projection module + shared resolver (3A)

**Files:**
- Create/Modify: `backend/app/datasets/projection.py`
- Test: `backend/tests/test_dataset_projection.py`

**Interfaces:**
- Produces: `normalize_dataset_root`, `compute_dataset_projection_id`,
  `DatasetProjection`, `DatasetProjectionResolver` (signatures above).

- [ ] Write the host-portable failing tests using `tmp_path` and host-native
      paths (never Windows literals that assume a Windows host):

```python
import os
import re
from pathlib import Path

def test_root_is_parent_of_split_directory(tmp_path):
    root = tmp_path / "SpaceNet-A"
    sample = root / "test" / "a.bin"
    sample.parent.mkdir(parents=True)
    sample.write_bytes(b"\x00" * 8)
    assert normalize_dataset_root(str(sample.resolve()), "test") == \
        os.path.normcase(os.path.normpath(str(root.resolve())))

def test_two_roots_with_same_display_fields_get_distinct_ids(tmp_path):
    root_a = tmp_path / "SpaceNet-A"
    root_b = tmp_path / "SpaceNet-B"
    id_a = compute_dataset_projection_id(source="spacenet", dataset_name="SpaceNet",
        dataset_split="test", label_space="spacenet_14",
        normalized_root=normalize_dataset_root(str(root_a / "test" / "a.bin"), "test"))
    id_b = compute_dataset_projection_id(source="spacenet", dataset_name="SpaceNet",
        dataset_split="test", label_space="spacenet_14",
        normalized_root=normalize_dataset_root(str(root_b / "test" / "a.bin"), "test"))
    assert id_a != id_b

def test_id_is_stable_and_url_safe(tmp_path):
    args = dict(source="spacenet", dataset_name="SpaceNet", dataset_split="test",
                label_space="spacenet_14", normalized_root=str(tmp_path))
    first = compute_dataset_projection_id(**args)
    assert first == compute_dataset_projection_id(**args)   # stable across calls
    assert re.fullmatch(r"dsproj_[0-9a-f]{32}", first)      # opaque + URL-safe
```

- [ ] Run `pytest backend/tests/test_dataset_projection.py -v`; observe failure.
- [ ] Implement `projection.py`:

```python
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from app.benchmarks.manifest import canonical_json_bytes
from app.core.errors import PlatformError
from app.recordings.model import RecordingModel

PROJECTION_ID_PREFIX = "dsproj_"


def normalize_dataset_root(external_path: str, dataset_split: str) -> str:
    parent = Path(external_path).parent
    root = parent.parent if parent.name == dataset_split else parent
    return os.path.normcase(os.path.normpath(str(root)))


def compute_dataset_projection_id(
    *, source: str, dataset_name: str, dataset_split: str,
    label_space: str | None, normalized_root: str,
) -> str:
    payload = {"source": source, "dataset_name": dataset_name,
               "dataset_split": dataset_split, "label_space": label_space,
               "root": normalized_root}
    digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return f"{PROJECTION_ID_PREFIX}{digest[:32]}"


@dataclass(frozen=True)
class DatasetProjection:
    dataset_projection_id: str
    source: str
    dataset_name: str
    dataset_split: str
    label_space: str | None
    normalized_root: str
    source_location: str | None


class DatasetProjectionResolver:
    def __init__(self, session) -> None:
        self.session = session

    def _rows(self):
        from sqlalchemy import select
        return list(self.session.scalars(
            select(RecordingModel).where(RecordingModel.dataset_name.is_not(None))
        ).all())

    def _projection_for_row(self, recording: RecordingModel) -> DatasetProjection:
        root = (normalize_dataset_root(recording.external_path, recording.dataset_split or "")
                if recording.external_path else "")
        source = recording.source or "custom"
        pid = compute_dataset_projection_id(
            source=source, dataset_name=recording.dataset_name or "",
            dataset_split=recording.dataset_split or "",
            label_space=recording.label_space, normalized_root=root)
        return DatasetProjection(pid, source, recording.dataset_name or "",
            recording.dataset_split or "", recording.label_space, root,
            str(Path(recording.external_path).parent.parent)
                if recording.external_path else None)

    def _grouped(self) -> dict[str, list[RecordingModel]]:
        grouped: dict[str, list[RecordingModel]] = {}
        for recording in self._rows():
            grouped.setdefault(self._projection_for_row(recording).dataset_projection_id, []).append(recording)
        return grouped

    def list(self, limit: int, offset: int):
        grouped = self._grouped()
        projections = sorted((self._projection_for_row(members[0]) for members in grouped.values()),
                             key=lambda p: (p.dataset_name, p.dataset_split, p.source, p.dataset_projection_id))
        return projections[offset:offset + limit], len(projections)

    def get(self, dataset_projection_id: str) -> DatasetProjection:
        grouped = self._grouped()
        members = grouped.get(dataset_projection_id)
        if not members:
            raise PlatformError("DATASET_PROJECTION_NOT_FOUND", "Dataset projection was not found.", 404)
        return self._projection_for_row(members[0])

    def members(self, dataset_projection_id: str, *, require_ground_truth: bool = False):
        grouped = self._grouped()
        members = grouped.get(dataset_projection_id)
        if members is None:
            raise PlatformError("DATASET_PROJECTION_NOT_FOUND", "Dataset projection was not found.", 404)
        ordered = sorted(members, key=lambda r: (r.name, r.id))
        if require_ground_truth:
            ordered = [r for r in ordered if r.has_ground_truth]
        return ordered
```

- [ ] Rerun focused test; observe pass.
- [ ] Commit: `feat(ux-b): shared dataset projection resolver`

---

## Task B2: Projection-scoped frozen manifest (3B)

**Files:**
- Modify: `backend/app/benchmarks/service.py`
- Test: `backend/tests/test_dataset_projection_authority.py`

**Interfaces:**
- Produces: `DatasetBenchmarkService.prepare_projection_manifest(dataset_projection_id)`.

- [ ] Refactor `_load_recording_manifests` so the loading of `ManifestRecording`
      rows accepts an explicit ordered `list[RecordingModel]` (DRY), keeping the
      legacy triple query as a thin wrapper.
- [ ] Add:

```python
def prepare_projection_manifest(self, dataset_projection_id: str) -> ManifestPreview:
    resolver = DatasetProjectionResolver(self.session)
    projection = resolver.get(dataset_projection_id)
    members = resolver.members(dataset_projection_id, require_ground_truth=True)
    if not members:
        raise PlatformError("DATASET_SNAPSHOT_EMPTY",
            "No Ground-Truth-bearing Recordings belong to this dataset projection.", 422)
    manifests = self._load_manifest_recordings(members)
    frozen = build_recording_manifest(projection.dataset_name, projection.dataset_split,
                                      projection.label_space or "", manifests)
    return self._manifest_preview_from_frozen(frozen)
```

- [ ] Write the authority test: two roots share `SpaceNet/test/spacenet_14`; a
      projection manifest for root A contains exactly root A's GT-bearing
      recordings.

```python
def test_projection_manifest_is_scoped_to_one_root(client, session):
    # register root A (2 gt samples) and root B (3 gt samples) with identical display fields
    resolver = DatasetProjectionResolver(session)
    ids = {p.dataset_projection_id for p, _ in zip(*[resolver.list(50, 0)]*2)}
    ...
    # for each projection id, manifest expected_recordings matches that root only
```

- [ ] Do not let V1.1 contextual flows call the legacy
      `prepare_manifest(dataset_name, dataset_split, label_space)`; it remains for
      historical callers only.
- [ ] Run focused test; observe pass.
- [ ] Commit: `feat(ux-b): projection-scoped frozen manifest`

---

## Task B3: Data Library read APIs (projection list/detail)

**Files:**
- Create: `backend/app/data_library/__init__.py`, `schema.py`, `service.py`, `router.py`
- Modify: `backend/app/main.py` (register router)
- Test: `backend/tests/test_data_library_api.py`

- [ ] Define `schema.py`:

```python
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

- [ ] `DataLibraryService(session)` wraps `DatasetProjectionResolver` and
      computes `sample_count`, `ground_truth_sample_count`, `external`
      (`source != "custom"`) and `source_location` (display-only normalized
      root).
- [ ] `router.py` prefix `/api/data-library`; add `GET /datasets` and
      `GET /datasets/{dataset_projection_id}`.
- [ ] Tests:

```python
def test_two_physical_roots_remain_distinct(client, register_spaceNet_root, tmp_path):
    register_spaceNet_root(client, tmp_path / "SpaceNet-A", "test", ["a", "b"])
    register_spaceNet_root(client, tmp_path / "SpaceNet-B", "test", ["c"])
    body = client.get("/api/data-library/datasets").json()
    assert body["total"] == 2
    assert {item["sample_count"] for item in body["items"]} == {2, 1}

def test_unknown_projection_is_404(client):
    assert client.get("/api/data-library/datasets/dsproj_missing").status_code == 404
```

- [ ] Run focused test; observe pass.
- [ ] Commit: `feat(ux-b): dataset projection read API`

---

## Task B4: Samples + standalone pagination/search (with derived flags)

**Files:**
- Modify: `backend/app/data_library/schema.py`, `service.py`, `router.py`
- Test: `backend/tests/test_data_library_api.py`

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

- [ ] `list_dataset_samples(projection_id, limit, offset, search)`: load
      projection members, filter by case-insensitive `name` match, slice; never
      send all members to the browser.
- [ ] `list_standalone_samples(limit, offset, search)`:
      `RecordingModel.dataset_name.is_(None)` with optional name search.
- [ ] `analysis_count` from a grouped count of `AnalysisRunModel` per recording.
- [ ] Derived flags: `source == "spacenet"` (from Task B0).
- [ ] Router: `GET /datasets/{dataset_projection_id}/samples` and
      `GET /standalone-samples`, each `limit` (1..200), `offset >= 0`,
      `search` max 200.
- [ ] Tests: pagination, search, standalone excludes dataset members, derived
      flags true for SpaceNet.
- [ ] Run focused test; observe pass.
- [ ] Commit: `feat(ux-b): data library sample pagination and search`

---

## Task B5: Dataset analysis history incl. imported BAPv1 (CORRECTION 4)

**Files:**
- Modify: `backend/app/data_library/schema.py`, `service.py`, `router.py`
- Test: `backend/tests/test_data_library_api.py`

- [ ] Add schemas:

```python
class DatasetAnalysisHistoryItemRead(BaseModel):
    kind: Literal["experiment", "evaluation", "imported_batch"]
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
    dataset_evaluation_id: str | None = None
    batch_id: str | None = None
    archive_sha256: str | None = None

class DatasetAnalysisHistoryListRead(BaseModel):
    dataset_projection_id: str
    items: list[DatasetAnalysisHistoryItemRead]
    total: int
```

- [ ] `list_dataset_analysis_history(projection_id)` scopes everything to the
      projection:
  - experiments/evaluations: same display fields AND non-empty item
    recording-id set that is a subset of projection member ids.
  - **imported batches**: select completed runs with `executor == "imported"`
    carrying `parameters_json["batch_import"]["import_fingerprint"]`; keep runs
    whose `recording_id` is a projection member; require the group's recordings
    to be a subset of projection members; group by `import_fingerprint`.
- [ ] Emit `kind = "imported_batch"` items with:

```text
resource_id      = import_fingerprint
name             = f"{pipeline_id} {pipeline_version}"
executor         = "imported"
status           = "completed"
expected_items   = len(projection GT-bearing members)  (the projection manifest size)
completed_items  = number of mapped completed runs inside the projection
failed_items     = 0  (no real represented failure is invented)
coverage         = completed_items / expected_items (0.0 when expected_items is 0)
created_at       = max run created_at
batch_id         = batch_import payload batch_id
archive_sha256   = batch_import payload archive_sha256
```

- [ ] Do NOT create a batch persistence table. Do NOT change BAPv1.
- [ ] Router: `GET /datasets/{dataset_projection_id}/analysis-history`.
- [ ] Tests: an imported batch (2 of 2 runs inside projection) appears with
      `kind == "imported_batch"` and `completed_items == 2` **before** any
      evaluation exists; two same-display roots never share imported-batch
      history.
- [ ] Run focused test; observe pass.
- [ ] Commit: `feat(ux-b): imported batch analysis history`

---

## Task B6: DatasetExperiment projection identity (3C)

**Files:**
- Modify: `backend/app/dataset_experiments/model.py`, `schema.py`, `service.py`,
  `backend/app/db/migrations.py`, `backend/app/main.py` (migration call)
- Test: `backend/tests/test_dataset_projection_authority.py`

- [ ] Add the model field:

```python
dataset_projection_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
```

- [ ] Add the additive migration and wire it into `run_additive_migrations`:

```python
def upgrade_v1_1_dataset_projection(engine) -> None:
    with engine.begin() as connection:
        experiments = {c["name"] for c in inspect(connection).get_columns("dataset_experiments")}
        if "dataset_projection_id" not in experiments:
            connection.execute(text(
                "ALTER TABLE dataset_experiments ADD COLUMN dataset_projection_id VARCHAR(64)"))
        evaluations = {c["name"] for c in inspect(connection).get_columns("dataset_evaluations")}
        if "dataset_projection_id" not in evaluations:
            connection.execute(text(
                "ALTER TABLE dataset_evaluations ADD COLUMN dataset_projection_id VARCHAR(64)"))
        connection.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_dataset_experiments_dataset_projection_id "
            "ON dataset_experiments (dataset_projection_id)"))
        connection.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_dataset_evaluations_dataset_projection_id "
            "ON dataset_evaluations (dataset_projection_id)"))
```

- [ ] Extend `DatasetExperimentCreate`:

```python
dataset_projection_id: str | None = Field(default=None, min_length=1, max_length=64)
dataset_name: str | None = Field(default=None, min_length=1, max_length=255)
dataset_split: str | None = Field(default=None, min_length=1, max_length=64)
dataset_label_space: str | None = Field(default=None, min_length=1, max_length=128)

@model_validator(mode="after")
def _require_identity_scope(self):
    has_projection = self.dataset_projection_id is not None
    has_triple = all(v is not None for v in
                     (self.dataset_name, self.dataset_split, self.dataset_label_space))
    if not has_projection and not has_triple:
        raise ValueError("Dataset identity requires dataset_projection_id or the "
                         "dataset_name/split/label_space triple.")
    return self
```

- [ ] Extend `DatasetExperimentService.create_experiment(..., dataset_projection_id=None)`:
  - if `dataset_projection_id` is present: resolve the projection; when friendly
    fields are supplied, require them to equal the projection's
    `dataset_name/split/label_space` else raise
    `EXECUTION_REQUEST_INVALID`; manifest =
    `DatasetBenchmarkService(self.session).prepare_projection_manifest(id)`.
  - else: legacy triple path (unchanged).
  - persist `dataset_projection_id` on the new experiment.
- [ ] `revalidate_frozen_identity`: non-null projection id → projection-scoped
      manifest; null → legacy triple path.
- [ ] `DatasetExperimentRead`: expose `dataset_projection_id: str | None`.
- [ ] Test (blocking scenario from the review):

```python
def test_experiment_from_projection_never_absorbs_sibling_root(client, session, ...):
    # root A: 2 gt samples, root B: 3 gt samples, identical display fields
    projection_id = projection_id_for_root(client, "A")
    created = client.post("/api/dataset-experiments", json={
        "name": "A experiment", "dataset_projection_id": projection_id,
        "dataset_name": "SpaceNet", "dataset_split": "test",
        "dataset_label_space": "spacenet_14",
        "plugin_id": "stft_energy_detector", "plugin_version": "1.0",
        "execution_mode": "manual", "executor": "local_cpu",
        "parameters": {}, "max_concurrency": 1,
    }).json()
    assert created["dataset_projection_id"] == projection_id
    items = client.get(f"/api/dataset-experiments/{created['id']}/items").json()
    member_ids = {rec.id for rec in DatasetProjectionResolver(session).members(projection_id)}
    assert {item["recording_id"] for item in items} <= member_ids
    # revalidation remains scoped to A
    assert client.get(f"/api/dataset-experiments/{created['id']}").status_code == 200
```

- [ ] Run focused test; observe pass.
- [ ] Commit: `feat(ux-b): projection-scoped dataset experiments`

---

## Task B7: Executor selection projection scope (3D)

**Files:**
- Modify: `backend/app/execution_selection/router.py`
- Test: `backend/tests/test_executor_selection_api.py`

- [ ] Add `dataset_projection_id: str | None = Query(None)` to the endpoint and
      the scope validation: exactly one of `recording_id`,
      `dataset_projection_id`, or the legacy triple.
- [ ] If `dataset_projection_id` is supplied:

```python
manifest = DatasetBenchmarkService(session).prepare_projection_manifest(dataset_projection_id)
probe_recording = session.get(RecordingModel, manifest.entries[0].recording_id)
selection = resolve_auto_execution(..., dataset_item_count=manifest.expected_recordings)
```

- [ ] Never mix projection scope and legacy triple scope.
- [ ] Test: root A has 2 samples, root B has 20, identical display identity;
      `?dataset_projection_id=<A>&pipeline_id=...` classifies with
      `dataset_item_count == 2`, not 22.
- [ ] Run focused test; observe pass.
- [ ] Commit: `feat(ux-b): projection-scoped executor selection`

---

## Task B8: Dataset evaluation projection scope (3E)

**Files:**
- Modify: `backend/app/benchmarks/model.py`, `schema.py`, `service.py`
- Test: `backend/tests/test_dataset_projection_authority.py`

- [ ] Add `DatasetEvaluationModel.dataset_projection_id` nullable indexed (same
      migration as B6).
- [ ] `DatasetEvaluationCreate`: add `dataset_projection_id: str | None = None`.
- [ ] `prepare_evaluation(..., dataset_projection_id=None)`: when present, use
      `prepare_projection_manifest` and validate the supplied items against only
      that projection; persist the projection id; when absent keep legacy
      behavior.
- [ ] `DatasetEvaluationRead`: expose `dataset_projection_id: str | None`.
- [ ] Test: evaluation created from `dsproj_A` has `dataset_projection_id ==
      dsproj_A`, includes only A members, and exposes the field on read.
- [ ] Run focused test; observe pass.
- [ ] Commit: `feat(ux-b): projection-scoped dataset evaluation`

---

## Task B9: Imported-batch resolution projection scope (3F)

**Files:**
- Modify: `backend/app/benchmarks/schema.py`, `service.py`
- Test: `backend/tests/test_dataset_projection_authority.py`

- [ ] In `resolve_imported_batch`, derive each referenced Recording's projection
      via `DatasetProjectionResolver.find_for_recording`; require all mappings to
      belong to exactly one projection:

```python
projection_ids = {resolver.find_for_recording(rec).dataset_projection_id for rec in recordings.values()}
if len(projection_ids) != 1:
    raise PlatformError("IMPORTED_BATCH_STATE_INCONSISTENT",
        "Imported batch Recordings do not share one dataset projection identity.", 409)
dataset_projection_id = next(iter(projection_ids))
frozen = self.prepare_projection_manifest(dataset_projection_id)
```

- [ ] Return `dataset_projection_id` in `ImportedBatchResolutionPreviewRead` and
      the service preview.
- [ ] Test: two same-display roots exist; an imported batch mapping only to root
      A resolves against A only and never requires/absorbs B.
- [ ] Run focused test; observe pass.
- [ ] Commit: `feat(ux-b): projection-scoped imported batch resolution`

---

## Task B10: Lifecycle preflight incl. imported-batch semantics (CORRECTION 5)

**Files:**
- Create: `backend/app/lifecycle/__init__.py`, `schema.py`, `preflight.py`
- Test: `backend/tests/test_lifecycle_preflight.py`

**Interfaces:**
- Produces: `DeleteBlocker` (with `"imported_batch"`), `find_run_blockers`,
  `find_recording_blockers`, `DeleteBlockerRead`, `blocker_details`.

- [ ] Implement `lifecycle/preflight.py` with the FK blockers from the previous
      revision plus imported-batch completeness:

```python
def _batch_fingerprint(run) -> str | None:
    payload = (run.parameters_json or {}).get("batch_import")
    return payload.get("import_fingerprint") if isinstance(payload, dict) else None

def _all_runs_for_fingerprint(session, fingerprint: str) -> set[str]:
    runs = session.scalars(select(AnalysisRunModel).where(
        AnalysisRunModel.executor == "imported",
        AnalysisRunModel.status == "completed",
    )).all()
    return {run.id for run in runs if _batch_fingerprint(run) == fingerprint}

def _imported_batch_blockers(session, proposed_run_ids: set[str], reference: str) -> list[DeleteBlocker]:
    blockers: list[DeleteBlocker] = []
    for fingerprint in {fp for run_id in proposed_run_ids
                        if (fp := _batch_fingerprint(session.get(AnalysisRunModel, run_id)))}:
        complete = _all_runs_for_fingerprint(session, fingerprint)
        if not complete <= proposed_run_ids:
            blockers.append(DeleteBlocker("imported_batch", fingerprint, reference))
    return blockers

def find_run_blockers(session, run_ids: Sequence[str]) -> list[DeleteBlocker]:
    proposed = set(run_ids)
    return _dedupe(_fk_run_blockers(session, run_ids)
                   + _imported_batch_blockers(session, proposed, "analysis_run"))

def find_recording_blockers(session, recording_ids: Sequence[str]) -> list[DeleteBlocker]:
    owned = set(session.scalars(select(AnalysisRunModel.id).where(
        AnalysisRunModel.recording_id.in_(recording_ids))).all())
    return _dedupe(_fk_recording_blockers(session, recording_ids)
                   + _fk_run_blockers(session, owned)
                   + _imported_batch_blockers(session, owned, "recording"))
```

- [ ] `lifecycle/schema.py`:

```python
class DeleteBlockerRead(BaseModel):
    kind: Literal["dataset_evaluation", "dataset_experiment",
                  "dataset_experiment_attempt", "imported_batch"]
    resource_id: str
    reference: Literal["recording", "analysis_run"]
def blocker_details(blockers) -> dict: ...
```

- [ ] Tests:

```python
def test_individual_batch_run_delete_is_blocked(session, ...):
    ...  # 3 runs share a fingerprint; blockers include imported_batch
def test_complete_batch_set_is_not_blocked(session, ...):
    ...  # proposed set contains all runs of the fingerprint -> no imported_batch blocker
def test_partial_parent_deletion_is_blocked(session, ...):
    ...  # deleting a recording that owns a subset of a fingerprint -> blocker
def test_complete_dataset_removal_covers_full_fingerprint(session, ...):
    ...  # all runs in the dataset -> no imported_batch blocker
```

- [ ] Run focused test; observe pass.
- [ ] Commit: `feat(ux-b): imported batch deletion dependency guard`

---

## Task B11: Deletion service + managed-file cleanup (CORRECTION 6)

**Files:**
- Create: `backend/app/lifecycle/service.py`
- Modify: `backend/app/storage/service.py`, `backend/app/recordings/router.py`,
  `backend/app/analysis/router.py`, `backend/app/data_library/router.py`
- Test: `backend/tests/test_lifecycle_deletion_api.py`

- [ ] Add storage helpers (reuse `_safe_child`):

```python
def import_package_dir(self, run_id: str) -> Path:      # data_root/imports/<run_id>
    return self._safe_child("imports", run_id)

def quarantine_root(self) -> Path:                       # data_root/quarantine
    path = self.data_root / "quarantine"
    path.mkdir(parents=True, exist_ok=True)
    return path
```

- [ ] Implement the safe choreography:

```python
@contextmanager
def _quarantine_managed_dirs(storage, managed_dirs: Iterable[Path]):
    token = uuid4().hex
    moved: list[tuple[Path, Path]] = []
    try:
        for index, source in enumerate(managed_dirs):
            if not source.exists():
                continue
            target = storage.quarantine_root() / f"{token}_{index}"
            os.replace(source, target)          # same filesystem rename
            moved.append((source, target))
        yield
    except Exception:
        for source, target in reversed(moved):
            if target.exists():
                os.replace(target, source)       # restore on failure
        raise
    else:
        for _source, target in moved:
            shutil.rmtree(target, ignore_errors=True)
```

- [ ] `delete_standalone_recording`:
  - reject dataset members with `RECORDING_IS_DATASET_MEMBER` (409);
  - preflight `find_recording_blockers`;
  - managed dirs = `storage.recording_dir(recording_id)` when
    `external_path is None and source == "custom"`;
  - quarantine → `session.delete(rec)` + commit → on success quarantine removed.
- [ ] `delete_analysis_run`:
  - preflight `find_run_blockers` (includes `imported_batch`);
  - managed dirs = `storage.import_package_dir(run_id)` when the run carries a
    single-package `parameters_json["package"]` and no batch fingerprint;
  - quarantine → `session.delete(run)` + commit.
- [ ] `remove_dataset_projection`:
  - preflight `find_recording_blockers` across all members; if any blocker →
    `DATASET_REMOVE_BLOCKED` (409) with **zero** mutation;
  - managed dirs = managed member recording dirs + single-import package dirs of
    all cascaded runs;
  - quarantine → delete all member rows + commit (all-or-nothing);
  - external_path dirs are never computed or touched.
- [ ] Endpoints: `DELETE /api/recordings/{recording_id}`,
      `DELETE /api/analysis-runs/{run_id}`,
      `DELETE /api/data-library/datasets/{dataset_projection_id}` (all 204).
- [ ] Tests:

```python
def test_dataset_removal_is_atomic_when_one_member_is_blocked(client, ...):
    ...  # 3 members, one referenced -> 409, all 3 rows remain
def test_dataset_removal_removes_all_managed_but_not_external(client, tmp_path, ...):
    ...  # external .bin/.json still present; managed dirs gone
def test_single_import_package_dir_removed_on_run_delete(client, ...):
    ...  # imports/<run_id> gone
def test_db_failure_restores_quarantined_files(client, monkeypatch, ...):
    ...  # force commit failure; managed dir restored
def test_imported_batch_run_delete_blocked(client, ...):
    ...  # 409 with imported_batch blocker
```

- [ ] Run `pytest backend/tests/test_lifecycle_deletion_api.py -v`; observe pass.
- [ ] Commit: `feat(ux-b): fail-safe deletion with managed-file quarantine`

---

## Task B12: Frontend client types + functions

**Files:**
- Modify: `frontend/src/api/types.ts`, `frontend/src/api/client.ts`,
  `frontend/src/api/client.test.ts`

- [ ] Add types (camelCase domain mapping of the backend reads):

```ts
export interface DatasetProjectionSummary {
  datasetProjectionId: string; source: string; datasetName: string;
  datasetSplit: string; labelSpace: string | null; sampleCount: number;
  groundTruthSampleCount: number; external: boolean; sourceLocation: string | null;
}
export interface DatasetProjectionListPage { items: DatasetProjectionSummary[]; total: number; }
export interface DatasetSample { id: string; name: string; sampleRateHz: number; centerFrequencyHz: number;
  frequencyLowHz: number; frequencyHighHz: number; durationS: number;
  hasGroundTruth: boolean; analysisCount: number;
  sampleRateDerived: boolean; centerFrequencyDerived: boolean; }
export interface DatasetSamplePage { datasetProjectionId: string; items: DatasetSample[]; total: number; }
export interface StandaloneSample { id: string; name: string; source: string; sampleRateHz: number;
  centerFrequencyHz: number; frequencyLowHz: number; frequencyHighHz: number; durationS: number;
  dataFormat: string; hasGroundTruth: boolean; analysisCount: number; }
export interface StandaloneSamplePage { items: StandaloneSample[]; total: number; }
export interface DatasetAnalysisHistoryItem {
  kind: "experiment" | "evaluation" | "imported_batch"; resourceId: string; name: string;
  pipelineId: string; pipelineVersion: string; status: string; executor: string | null;
  expectedItems: number; completedItems: number; failedItems: number; coverage: number | null;
  createdAt: string | null; datasetEvaluationId: string | null;
  batchId: string | null; archiveSha256: string | null; }
export interface DatasetAnalysisHistoryPage { datasetProjectionId: string; items: DatasetAnalysisHistoryItem[]; total: number; }
export interface DeleteBlocker { kind: "dataset_evaluation" | "dataset_experiment" |
  "dataset_experiment_attempt" | "imported_batch"; resourceId: string;
  reference: "recording" | "analysis_run"; }
```

- [ ] Extend `ExecutionSelectionScope` and the existing domain types:

```ts
export type ExecutionSelectionScope =
  | { kind: "recording"; recordingId: string }
  | { kind: "dataset"; datasetName: string; datasetSplit: string; datasetLabelSpace: string }
  | { kind: "dataset_projection"; datasetProjectionId: string };

// DatasetExperiment / DatasetExperimentCreateRequest gain:
datasetProjectionId?: string | null;
```

- [ ] Add functions:

```ts
export async function listDatasetProjections(limit = 50, offset = 0): Promise<DatasetProjectionListPage>;
export async function getDatasetProjection(datasetProjectionId: string): Promise<DatasetProjectionSummary>;
export async function listDatasetSamples(datasetProjectionId: string,
  params?: { limit?: number; offset?: number; search?: string }): Promise<DatasetSamplePage>;
export async function listStandaloneSamples(params?: { limit?: number; offset?: number; search?: string }): Promise<StandaloneSamplePage>;
export async function listDatasetAnalysisHistory(datasetProjectionId: string): Promise<DatasetAnalysisHistoryPage>;
export async function deleteRecording(recordingId: string): Promise<void>;
export async function deleteDatasetProjection(datasetProjectionId: string): Promise<void>;
export async function deleteAnalysisRun(runId: string): Promise<void>;
export function deleteBlockersFromError(error: unknown): DeleteBlocker[];
```

- [ ] Add `apiDelete(path)` (204-aware) and implement `getExecutorSelection` to
      serialize `dataset_projection_id` for the new scope.
- [ ] Write client tests: mapping, DELETE 204, 409 blocker parsing, projection
      scope query serialization (`dataset_projection_id=dsproj_1`).
- [ ] Run `npx vitest run src/api/client.test.ts`; observe pass.
- [ ] Commit: `feat(ux-b): data library client contract and projection scope`

---

## Task B13: Data Library root page (Datasets / Standalone tabs)

**Files:**
- Create: `frontend/src/features/data-library/types.ts`, `DatasetList.tsx`,
  `StandaloneSampleList.tsx`, `DeleteConfirmModal.tsx`, `DeleteConflictAlert.tsx`,
  `frontend/src/pages/DataLibraryPage.tsx`
- Test: `frontend/src/pages/DataLibraryPage.test.tsx`

- [ ] Add page-copy keys:

```ts
"dataLibrary.title": "Data Library",       // zh: "数据管理"
"dataLibrary.subtitle": "Manage standalone IQ samples and registered datasets, and start analysis.",
                                           // zh: "管理独立 IQ 样本与已注册数据集，并启动分析。"
"dataLibrary.tabDatasets": "Datasets",     // zh: "数据集"
"dataLibrary.tabStandalone": "Standalone Samples", // zh: "独立样本"
```

- [ ] Write failing tests: dataset cards (not member recordings) render; a
      standalone delete blocked by a dependent shows `DeleteConflictAlert` with
      the blocker resource id.
- [ ] Implement `DataLibraryPage` (title + purpose + primary action),
      `DatasetList` (aggregate fields + Browse Samples / Create Dataset
      Experiment / Import Analysis Results / Remove Dataset),
      `StandaloneSampleList` (bounded paginated + search; Open Analysis
      Workspace / Analysis History / Delete), `DeleteConfirmModal` (explicit
      irreversible confirmation), `DeleteConflictAlert` (from
      `deleteBlockersFromError`).
- [ ] Do NOT add routes or nav here (Task B17 owns route wiring).
- [ ] Run focused tests; observe pass.
- [ ] Commit: `feat(ux-b): data library root page`

---

## Task B14: Dataset detail (Overview / Samples / Analysis History)

**Files:**
- Create: `frontend/src/features/data-library/DatasetSamplesTable.tsx`,
  `DatasetAnalysisHistory.tsx`, `frontend/src/pages/DatasetDetailPage.tsx`
- Test: `frontend/src/pages/DatasetDetailPage.test.tsx`

- [ ] Write failing tests: overview aggregates; samples table paginates/searches;
      derived Fs/Fc labelled; imported-batch history rows render
      (`ZoomSpec 1.0.0 / imported / completed`); Remove Dataset confirms and
      calls `deleteDatasetProjection`.
- [ ] Implement `DatasetSamplesTable` with server-side `limit/offset/search` and
      a derived badge driven by `sampleRateDerived`/`centerFrequencyDerived`.
- [ ] Implement `DatasetAnalysisHistory` rendering experiment, evaluation, and
      imported_batch kinds with pipeline/status/coverage and contextual actions
      (Create Experiment, Import Batch Analysis Results, Open Evaluation).
- [ ] Implement `DatasetDetailPage` with Overview/Samples/Analysis History tabs.
- [ ] Run focused tests; observe pass.
- [ ] Commit: `feat(ux-b): dataset detail with samples and imported-batch history`

---

## Task B15: Standalone sample detail + safe deletion

**Files:**
- Create: `frontend/src/pages/StandaloneSampleDetailPage.tsx`
- Test: `frontend/src/pages/StandaloneSampleDetailPage.test.tsx`

- [ ] Write failing tests: metadata renders; analysis history from
      `listAnalysisRuns`; Delete calls `deleteRecording` and navigates back;
      blocked deletion surfaces blockers.
- [ ] Implement the page with `PageHeader` and delete confirmation.
- [ ] Run focused tests; observe pass.
- [ ] Commit: `feat(ux-b): standalone sample detail and safe deletion`

---

## Task B16: Add Data vs Import Analysis Results

**Files:**
- Create: `frontend/src/features/data-library/AddDataMenu.tsx`,
  `ImportResultsMenu.tsx`, `ImportStandaloneIqModal.tsx`
- Modify: `frontend/src/features/imports/ImportRunModal.tsx` (reuse as-is),
  `frontend/src/features/imports/BatchImportModal.tsx` (reuse as-is),
  `frontend/src/pages/DataLibraryPage.tsx`,
  `frontend/src/localization/messages.en-US.ts`,
  `frontend/src/localization/messages.zh-CN.ts`
- Test: `frontend/src/pages/DataLibraryPage.test.tsx`

- [ ] Add keys:

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

- [ ] Write failing tests for the two menus and their exact items; selecting a
      batch item opens `BatchImportModal`.
- [ ] Move the standalone IQ import form (name, file, data format, Fs, Fc,
      optional label space) into `ImportStandaloneIqModal.tsx`, reusing
      `importRecording` unchanged and showing `dataLibrary.fsFcRequiredHint`.
- [ ] Reuse the existing single/batch import modals unchanged (no BAPv2).
- [ ] Run focused tests; observe pass.
- [ ] Commit: `feat(ux-b): separate Add Data from Import Analysis Results`

---

## Task B17: Data Library routes and default route (CORRECTION 2)

**Files:**
- Modify: `frontend/src/app/App.tsx`, `frontend/src/app/App.test.tsx`
- Retire: `frontend/src/pages/RecordingsPage.tsx`,
  `frontend/src/pages/RecordingsPage.test.tsx`
- Test: `frontend/src/app/navigation.test.tsx`

- [ ] Apply only after UX-A is incorporated (merge ancestry; never rebase UX-A).
- [ ] Update `App.tsx`:

```tsx
<Route index element={<Navigate to="/data-library" replace />} />
<Route path="recordings" element={<Navigate to="/data-library" replace />} />
<Route path="data-library" element={<DataLibraryPage />} />
<Route path="data-library/datasets/:datasetProjectionId" element={<DatasetDetailPage />} />
<Route path="data-library/samples/:recordingId" element={<StandaloneSampleDetailPage />} />
```

- [ ] Do NOT edit `MainLayout.tsx`; UX-A already provides the Data Library nav
      item.
- [ ] Delete `RecordingsPage.tsx` and `RecordingsPage.test.tsx`.
- [ ] Update `App.test.tsx` default-route assertion to the Data Library title.
- [ ] Run `npx vitest run src/app src/pages`; observe pass.
- [ ] Commit: `feat(ux-b): wire Data Library routes and default`

---

## Task B18: Track boundary

- [ ] Focused backend:
      `pytest backend/tests/test_dataset_projection.py backend/tests/test_dataset_projection_authority.py backend/tests/test_data_library_api.py backend/tests/test_lifecycle_preflight.py backend/tests/test_lifecycle_deletion_api.py backend/tests/test_executor_selection_api.py -v`
- [ ] Full frontend: `npm test -- --run`
- [ ] Production build: `npm run build`
- [ ] Existing regression touched by router changes:
      `pytest backend/tests/test_recordings.py backend/tests/test_analysis_runs.py backend/tests/test_benchmark_api.py backend/tests/test_dataset_experiment_api.py -v`
- [ ] Commit: `chore(ux-b): track boundary verification`

---

## Self-Review Checklist

```text
[ ] No dataset table created; no platform.db replication.
[ ] dataset_projection_id deterministic, opaque, URL-safe, root-scoped, and
    propagated through executor selection, experiment, revalidation,
    evaluation, and imported-batch resolution.
[ ] Two same-display roots never merge at any layer.
[ ] Dataset Analysis History includes imported BAPv1 batches.
[ ] Individual imported-batch run deletion fails closed; complete-set deletion
    is allowed.
[ ] Managed single-import and custom IQ directories have a quarantine-backed
    lifecycle; DB failure restores them.
[ ] External SpaceNet files are never deleted (asserted in tests).
[ ] Dataset removal is all-or-nothing.
[ ] Recording deletion cannot bypass run imported-batch guards.
[ ] Conflict schema identical across the three DELETE endpoints.
[ ] Projection tests are host-portable and use tmp_path.
[ ] SpaceNet Fs is not falsely claimed verified.
[ ] No BAPv2; no Remote-GPU; no GPU; no sealed-baseline change.
```
