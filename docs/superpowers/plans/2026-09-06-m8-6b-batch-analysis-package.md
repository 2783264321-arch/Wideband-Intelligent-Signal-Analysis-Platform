# M8.6B Batch Analysis Package Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic, all-or-nothing, idempotent Batch Analysis Package v1 transport/import path and use it to import the frozen 2,500-sample historical SpaceNet result set into ordinary platform `AnalysisRun` and `DetectionResult` rows without rerunning inference.

**Architecture:** Preserve Analysis Package v1 and the existing `AnalysisRun` runtime model. Add strict outer batch schema/archive handling, mandatory metadata+GroundTruth recording fingerprints, reusable single-child validation, semantic batch idempotency, and a one-transaction importer under `app/imported_runs`. Reuse the existing M9 legacy detection adapter to build a deterministic historical batch archive on the server; M8.6B stops after returning the exact `Recording -> AnalysisRun` mapping and never creates or runs a `DatasetEvaluation`.

**Tech Stack:** Python 3, FastAPI, Pydantic, SQLAlchemy, SQLite, `zipfile`, `hashlib`, existing SpaceNetAdapter, existing M9 legacy bridge, pytest.

**Spec:** `docs/superpowers/specs/2026-09-06-m8-6b-batch-analysis-package-design.md`

## Global Constraints

- Start from shared baseline `feature/v1-core @ d93913c98c3cb528de4cda831bdf583f25f4ee29`.
- Work on `feature/m8-6b-batch-package`; never implement directly on `feature/v1-core` or `main`.
- Batch Analysis Package is transport only; do not add `BatchRun`, `BatchImportModel`, `Dataset`, a generic Job entity, or a `batch_id` database column.
- Existing Analysis Package v1 wire semantics remain unchanged.
- Existing M6 single-package import behavior must remain backward compatible after validation refactoring.
- Existing M8.6A `recording_manifest_hash` must remain byte-for-byte stable for the same semantic inputs.
- `recording_fingerprint_v1` must include dataset name/split, label space, Recording name/physical metadata, and canonical GroundTruth; it must not hash raw IQ payload bytes.
- Candidate Recording resolution is exactly `dataset_name + dataset_split + Recording.name`, followed by fingerprint verification; zero or multiple candidates reject the batch.
- Outer `recording_manifest_hash`, when present, must equal the local M8.6A dataset manifest hash before import proceeds.
- Validate every batch item and every detection before the first ORM write.
- A new import must commit all new `AnalysisRun` and `DetectionResult` rows in one transaction; one invalid child creates zero rows.
- Re-import of the same semantic batch must be idempotent: return the existing exact mapping, create zero runs, and create zero detections.
- Partial prior semantic import state must raise `BATCH_IMPORT_STATE_INCONSISTENT`; do not repair it automatically.
- `batch_import_fingerprint_v1` is semantic identity. ZIP SHA256 is transport integrity only.
- Semantic fingerprint excludes `batch_id`, ZIP bytes/order/timestamps, `export_timestamp`, transport commit metadata, local DB IDs/paths, and generated run/detection IDs.
- Semantic fingerprint includes stable result provenance, canonical child parameters, canonical recording fingerprints, and canonical final detections.
- Batch archive V1 limits are exactly: 10,000 items, 1,000,000 total detections, 256 MiB upload, 1 GiB expanded data, 25,000 ZIP members, 32 MiB per JSON document.
- Do not weaken or increase the current M6 single-package archive limits.
- Historical batch export must use the frozen predictions JSONL as source of truth and reuse `LegacyDetectionAdapter`; do not load models for inference, run STFT/LS-STFT, AHLP, NMS, GPU inference, training, or confidence recomputation.
- Export one child for every frozen split Recording, including zero-detection children.
- Historical mAP values are reference-only metadata and must never be written into `DatasetEvaluation` results.
- M8.6B adds no frontend production code.
- M8.6B does not create/start a benchmark; M8.6C owns real DatasetEvaluation and parity analysis.
- Do not modify M8.5 `matching.py`, M8.6A AP semantics, Pipeline contract, Recording public contract, DetectionResult public contract, or AnalysisRun public schema.
- No dataset binaries, checkpoints, generated ZIPs, runtime SQLite DBs, model artifacts, `dist`, or `node_modules` may enter Git.
- Use TDD for every behavior change: failing test -> verify RED -> minimal implementation -> verify GREEN -> review diff -> commit.
- If a real-asset acceptance step exposes a product-code defect, stop that acceptance step and return the defect to the local implementation branch for a tested fix; do not hot-patch platform code only on the server.

---

## File Structure

Create focused units under the existing imported-run domain:

```text
backend/app/imported_runs/
  batch_schema.py        # strict Batch Analysis Package v1 Pydantic wire + response schemas
  batch_archive.py       # batch-scale bounded safe extraction; keeps M6 limits unchanged
  fingerprint.py         # recording_fingerprint_v1 + batch_import_fingerprint_v1 canonical hashes
  validation.py          # reusable extracted Analysis Package v1 validation without commit
  factory.py             # pure construction of standard AnalysisRun/DetectionResult ORM objects
  batch_validation.py    # outer/child consistency, local Recording resolution, full preflight
  batch_service.py       # archive lifecycle, idempotency lookup, one-transaction import, summary
```

Modify existing imported-run files only where integration requires it:

```text
backend/app/imported_runs/service.py       # M6 importer delegates to reusable validation/factory
backend/app/imported_runs/router.py        # add POST /api/imported-runs/batch
backend/app/benchmarks/manifest.py         # expose canonical payload helpers without changing hash
```

Add research-side historical export tooling while preserving the current M9 single-sample bridge:

```text
research/m9_legacy_bridge/
  batch_exporter.py      # generic deterministic Batch Analysis Package writer
  batch_cli.py           # historical SpaceNet JSONL -> 2,500-child batch orchestration
```

Tests:

```text
backend/tests/
  batch_import_fixture.py
  test_recording_fingerprint.py
  test_imported_run_validation.py
  test_batch_archive.py
  test_batch_schema.py
  test_batch_fingerprint.py
  test_batch_validation.py
  test_batch_import_service.py
  test_batch_import_api.py
  test_m9_legacy_bridge_batch_exporter.py
  test_m9_legacy_bridge_batch_cli.py
```

Regression files that must remain green:

```text
backend/tests/test_imported_runs.py
backend/tests/test_benchmark_manifest.py
backend/tests/test_benchmark_membership.py
backend/tests/test_benchmark_api.py
backend/tests/test_m9_legacy_bridge_adapter.py
backend/tests/test_m9_legacy_bridge_exporter.py
```

Research/acceptance documentation added only after real acceptance values are known:

```text
docs/research/m8_6b_batch_analysis_package.md
```

---

### Task 1: Expose stable M8.6A canonical Recording payloads and add `recording_fingerprint_v1`

**Files:**
- Modify: `backend/app/benchmarks/manifest.py`
- Create: `backend/app/imported_runs/fingerprint.py`
- Create: `backend/tests/test_recording_fingerprint.py`
- Modify: `backend/tests/test_benchmark_manifest.py`

**Interfaces:**
- `app.benchmarks.manifest.canonical_number(value: float) -> str`
- `app.benchmarks.manifest.canonical_ground_truth_payload(ground_truth: tuple[ManifestGroundTruth, ...]) -> list[dict[str, object]]`
- `app.benchmarks.manifest.canonical_recording_payload(recording: ManifestRecording) -> dict[str, object]`
- `app.benchmarks.manifest.canonical_json_bytes(payload: object) -> bytes`
- `app.imported_runs.fingerprint.RecordingFingerprintValue`
- `app.imported_runs.fingerprint.build_recording_fingerprint(dataset_name: str, dataset_split: str, label_space: str, recording: ManifestRecording) -> RecordingFingerprintValue`
- Later tasks use the same functions to verify server/local identity and to compute semantic batch identity.

- [ ] **Step 1: Freeze an existing M8.6A manifest-hash fixture before refactoring**

In `backend/tests/test_benchmark_manifest.py`, add this exact two-Recording regression fixture. Its expected hash is the `d93913c` canonical payload hash for these explicit values:

```python
def test_manifest_hash_regression_is_frozen():
    a = ManifestRecording(
        recording_id="rec_a",
        name="a",
        data_format="float16_interleaved_le",
        sample_rate_hz=50_000_000.0,
        center_frequency_hz=2_455_000_000.0,
        frequency_low_hz=2_430_000_000.0,
        frequency_high_hz=2_480_000_000.0,
        num_samples=7_500_000,
        duration_s=0.15,
        ground_truth=(
            ManifestGroundTruth(
                t_start_s=0.032,
                t_end_s=0.08,
                f_low_hz=2_447_973_850.0,
                f_high_hz=2_448_026_150.0,
                class_id=9,
                class_name="LoRa 250kHz",
            ),
        ),
    )
    b = ManifestRecording(
        recording_id="rec_b",
        name="b",
        data_format="float16_interleaved_le",
        sample_rate_hz=50_000_000.0,
        center_frequency_hz=2_455_000_000.0,
        frequency_low_hz=2_430_000_000.0,
        frequency_high_hz=2_480_000_000.0,
        num_samples=7_500_000,
        duration_s=0.15,
        ground_truth=(
            ManifestGroundTruth(
                t_start_s=0.01,
                t_end_s=0.02,
                f_low_hz=2_440_600_000.0,
                f_high_hz=2_440_700_000.0,
                class_id=8,
                class_name="Zigbee",
            ),
        ),
    )
    frozen = build_recording_manifest("SpaceNet", "test", "spacenet_14", [b, a])
    assert frozen.sha256 == "e7fdb9b4f05656679881b72b335a004b9dbb1e8d7dd8ee7795d8dddfd82d375f"
```

Do not change this expected hash during the refactor. A mismatch means the M8.6A manifest identity changed and the task must stop until the cause is understood.

- [ ] **Step 2: Run the regression fixture on the untouched implementation**

Run:

```bash
pytest backend/tests/test_benchmark_manifest.py::test_manifest_hash_regression_is_frozen -v
```

Expected: PASS on baseline. This is the behavior lock before refactor.

- [ ] **Step 3: Write failing `recording_fingerprint_v1` tests**

Create `backend/tests/test_recording_fingerprint.py` with at least these tests:

```python
def test_recording_fingerprint_ignores_local_recording_id():
    left = build_recording_fingerprint("SpaceNet", "test", "spacenet_14", make_recording("rec_local_a"))
    right = build_recording_fingerprint("SpaceNet", "test", "spacenet_14", make_recording("rec_local_b"))
    assert left.sha256 == right.sha256


def test_recording_fingerprint_changes_when_ground_truth_changes():
    original = build_recording_fingerprint("SpaceNet", "test", "spacenet_14", make_recording("rec_a"))
    changed = build_recording_fingerprint("SpaceNet", "test", "spacenet_14", make_recording("rec_a", class_id=8))
    assert original.ground_truth_sha256 != changed.ground_truth_sha256
    assert original.sha256 != changed.sha256


def test_recording_fingerprint_changes_when_dataset_split_changes():
    recording = make_recording("rec_a")
    test_fp = build_recording_fingerprint("SpaceNet", "test", "spacenet_14", recording)
    train_fp = build_recording_fingerprint("SpaceNet", "train", "spacenet_14", recording)
    assert test_fp.sha256 != train_fp.sha256
```

Also assert that GroundTruth input order does not change either fingerprint and that `-0.0`/`0.0` follow the existing M8.6A normalization.

- [ ] **Step 4: Run fingerprint tests and verify RED**

Run:

```bash
pytest backend/tests/test_recording_fingerprint.py -v
```

Expected: collection/import failure because `app.imported_runs.fingerprint` and public canonical helpers do not exist.

- [ ] **Step 5: Expose canonical helpers without changing serialized M8.6A payload**

Refactor `backend/app/benchmarks/manifest.py` so `build_recording_manifest()` uses public helpers while preserving the exact existing payload shape:

```python
def canonical_number(value: float) -> str:
    value = float(value)
    if value == 0.0:
        return "0"
    return format(value, ".17g")


def canonical_ground_truth_payload(ground_truth: tuple[ManifestGroundTruth, ...]) -> list[dict[str, object]]:
    return [
        {
            "t_start_s": canonical_number(gt.t_start_s),
            "t_end_s": canonical_number(gt.t_end_s),
            "f_low_hz": canonical_number(gt.f_low_hz),
            "f_high_hz": canonical_number(gt.f_high_hz),
            "class_id": gt.class_id,
            "class_name": gt.class_name,
        }
        for gt in sorted(ground_truth, key=_gt_sort_key)
    ]


def canonical_recording_payload(recording: ManifestRecording) -> dict[str, object]:
    return {
        "name": recording.name,
        "data_format": recording.data_format,
        "sample_rate_hz": canonical_number(recording.sample_rate_hz),
        "center_frequency_hz": canonical_number(recording.center_frequency_hz),
        "frequency_low_hz": canonical_number(recording.frequency_low_hz),
        "frequency_high_hz": canonical_number(recording.frequency_high_hz),
        "num_samples": recording.num_samples,
        "duration_s": canonical_number(recording.duration_s),
        "ground_truth": canonical_ground_truth_payload(recording.ground_truth),
    }


def canonical_json_bytes(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
```

`build_recording_manifest()` must use these helpers and must produce the same hash as before.

- [ ] **Step 6: Implement the recording fingerprint value**

Create `backend/app/imported_runs/fingerprint.py`:

```python
from dataclasses import dataclass
from hashlib import sha256

from app.benchmarks.manifest import (
    ManifestRecording,
    canonical_ground_truth_payload,
    canonical_json_bytes,
    canonical_recording_payload,
)

RECORDING_FINGERPRINT_SCHEMA = "recording_fingerprint_v1"
BATCH_IMPORT_FINGERPRINT_SCHEMA = "batch_import_fingerprint_v1"


@dataclass(frozen=True)
class RecordingFingerprintValue:
    schema: str
    metadata: dict[str, object]
    ground_truth_sha256: str
    sha256: str


def build_recording_fingerprint(
    dataset_name: str,
    dataset_split: str,
    label_space: str,
    recording: ManifestRecording,
) -> RecordingFingerprintValue:
    recording_payload = canonical_recording_payload(recording)
    gt_payload = canonical_ground_truth_payload(recording.ground_truth)
    payload = {
        "schema": RECORDING_FINGERPRINT_SCHEMA,
        "dataset_name": dataset_name,
        "dataset_split": dataset_split,
        "label_space": label_space,
        "recording": recording_payload,
    }
    metadata = {
        "data_format": recording.data_format,
        "sample_rate_hz": recording.sample_rate_hz,
        "center_frequency_hz": recording.center_frequency_hz,
        "frequency_low_hz": recording.frequency_low_hz,
        "frequency_high_hz": recording.frequency_high_hz,
        "num_samples": recording.num_samples,
        "duration_s": recording.duration_s,
    }
    return RecordingFingerprintValue(
        schema=RECORDING_FINGERPRINT_SCHEMA,
        metadata=metadata,
        ground_truth_sha256=sha256(canonical_json_bytes(gt_payload)).hexdigest(),
        sha256=sha256(canonical_json_bytes(payload)).hexdigest(),
    )
```

- [ ] **Step 7: Run M8.6A hash regression + new fingerprint tests**

Run:

```bash
pytest backend/tests/test_benchmark_manifest.py backend/tests/test_recording_fingerprint.py -v
```

Expected: all PASS; the frozen M8.6A hash remains unchanged.

- [ ] **Step 8: Commit**

```bash
git add backend/app/benchmarks/manifest.py backend/app/imported_runs/fingerprint.py backend/tests/test_benchmark_manifest.py backend/tests/test_recording_fingerprint.py
git commit -m "feat: add stable recording fingerprints"
```

---

### Task 2: Extract reusable Analysis Package v1 validation and model construction without changing M6 behavior

**Files:**
- Create: `backend/app/imported_runs/validation.py`
- Create: `backend/app/imported_runs/factory.py`
- Modify: `backend/app/imported_runs/service.py`
- Create: `backend/tests/test_imported_run_validation.py`
- Regression: `backend/tests/test_imported_runs.py`

**Interfaces:**
- `ValidatedAnalysisPackage(manifest: Manifest, detections: tuple[PackageDetection, ...])`
- `validate_extracted_package(root: Path, recording: RecordingModel, labels: LabelSpaceService) -> ValidatedAnalysisPackage`
- `BuiltImportedRun(run: AnalysisRunModel, detections: tuple[DetectionResultModel, ...], source_detection_ids: dict[str, str])`
- `build_imported_run_models(recording: RecordingModel, validated: ValidatedAnalysisPackage, *, run_id: str, batch_import: dict[str, object] | None = None) -> BuiltImportedRun`
- M6 `PackageImportService.import_run()` continues to own package-file promotion and its one-run commit; it delegates validation/model construction only.

- [ ] **Step 1: Write failing pure-validation tests**

Create `backend/tests/test_imported_run_validation.py` proving the reusable validator does not write to the Session. Use an extracted temporary `manifest.json` + `detections.json`, a real local Recording fixture, and assert:

```python
validated = validate_extracted_package(root, recording, labels)
assert validated.manifest.pipeline.id == "dummy"
assert len(validated.detections) == 1
assert session.query(AnalysisRunModel).count() == 0
assert session.query(DetectionResultModel).count() == 0
```

Add focused failures for duplicate source detection IDs, invalid bbox, invalid label, and invalid confidence. Reuse the exact existing M6 error code `INVALID_IMPORT_PACKAGE` for child validation failures.

- [ ] **Step 2: Run validation tests and verify RED**

```bash
pytest backend/tests/test_imported_run_validation.py -v
```

Expected: collection/import failure for missing `validation.py`.

- [ ] **Step 3: Implement `validate_extracted_package()` by moving, not rewriting, M6 validation semantics**

The function must preserve the current sequence and rules from `PackageImportService.import_run()`:

```python
@dataclass(frozen=True)
class ValidatedAnalysisPackage:
    manifest: Manifest
    detections: tuple[PackageDetection, ...]


def validate_extracted_package(root: Path, recording: RecordingModel, labels: LabelSpaceService) -> ValidatedAnalysisPackage:
    try:
        manifest = Manifest.model_validate(read_json(root / "manifest.json"))
        if manifest.label_space != recording.label_space:
            raise invalid("Package label_space does not match the selected Recording.")
        labels.get(manifest.label_space)
        if Path(manifest.results.detections).name != "detections.json":
            raise invalid("The detections result must point to detections.json.")
        detections_doc = read_json(safe_path(root, manifest.results.detections))
        if not isinstance(detections_doc, dict) or not isinstance(detections_doc.get("detections"), list):
            raise invalid("detections.json must contain a 'detections' array.")
        detections = TypeAdapter(list[PackageDetection]).validate_python(detections_doc["detections"])
        if len(detections) > 100000:
            raise invalid("A package may contain at most 100000 detections.")
        source_ids = [item.id for item in detections if item.id is not None]
        if len(source_ids) != len(set(source_ids)):
            raise invalid("Detection source ids must be unique within the package.")
        for item in detections:
            validate_physical_box(recording, **item.model_dump(include={
                "t_start_s", "t_end_s", "f_low_hz", "f_high_hz"
            }), error_code="INVALID_IMPORT_PACKAGE")
            validate_label(
                labels,
                label_space_id=manifest.label_space,
                class_id=item.class_id,
                class_name=item.class_name,
                error_code="INVALID_IMPORT_PACKAGE",
            )
    except ValidationError as exc:
        raise invalid("Package schema or execution metadata is invalid.") from exc
    return ValidatedAnalysisPackage(manifest=manifest, detections=tuple(detections))
```

Do not add child Recording-name/dataset restrictions here; M6 did not enforce them. Batch-specific outer/child identity checks belong to Task 5.

- [ ] **Step 4: Write model-factory tests**

Test that `build_imported_run_models()`:

- creates `executor="imported"`, `status="completed"`;
- preserves current `package`, `source_detection_ids`, `detection_count`, and hardware JSON shape;
- adds no `batch_import` key when `batch_import=None`;
- adds exactly the supplied `batch_import` dict when present;
- does not add the models to a Session and does not commit.

- [ ] **Step 5: Implement the pure model factory**

Create `backend/app/imported_runs/factory.py` with deterministic structure but caller-supplied generated IDs:

```python
@dataclass(frozen=True)
class BuiltImportedRun:
    run: AnalysisRunModel
    detections: tuple[DetectionResultModel, ...]
    source_detection_ids: dict[str, str]


def build_imported_run_models(
    recording: RecordingModel,
    validated: ValidatedAnalysisPackage,
    *,
    run_id: str,
    detection_ids: list[str],
    batch_import: dict[str, object] | None = None,
) -> BuiltImportedRun:
    if len(detection_ids) != len(validated.detections):
        raise ValueError("detection_ids length must match validated detections")
    models = tuple(
        DetectionResultModel(
            id=detection_id,
            run_id=run_id,
            **item.model_dump(exclude={"id", "scores"}),
            scores_json=item.scores,
        )
        for detection_id, item in zip(detection_ids, validated.detections)
    )
    source_ids = {
        item.id: model.id
        for item, model in zip(validated.detections, models)
        if item.id is not None
    }
    parameters: dict[str, object] = {
        "package": {
            "pipeline_id": validated.manifest.pipeline.id,
            "pipeline_name": validated.manifest.pipeline.name,
            "pipeline_version": validated.manifest.pipeline.version,
            "recording_name": validated.manifest.recording.name,
            "dataset": validated.manifest.recording.dataset,
        },
        "source_detection_ids": source_ids,
        "detection_count": len(models),
    }
    if batch_import is not None:
        parameters["batch_import"] = batch_import
    run = AnalysisRunModel(
        id=run_id,
        recording_id=recording.id,
        pipeline_id=validated.manifest.pipeline.id,
        pipeline_version=validated.manifest.pipeline.version,
        executor="imported",
        status="completed",
        parameters_json=parameters,
        hardware_info_json={
            "executor": validated.manifest.execution.executor,
            "device": validated.manifest.execution.device,
            "environment": validated.manifest.execution.environment,
        },
        created_at=datetime.now(timezone.utc),
    )
    return BuiltImportedRun(run=run, detections=models, source_detection_ids=source_ids)
```

- [ ] **Step 6: Refactor M6 `PackageImportService` to use validator + factory**

Keep these M6 behaviors unchanged:

- selected `recording_id` is still supplied by the user;
- safe extraction is still `extract_package()` with current M6 limits;
- package directory is still promoted to `imports/<run_id>`;
- one run still commits per request;
- rollback still removes the promoted package directory on DB failure;
- response remains the standard AnalysisRun.

- [ ] **Step 7: Run pure tests and full M6 regression**

```bash
pytest backend/tests/test_imported_run_validation.py backend/tests/test_imported_runs.py -v
```

Expected: all PASS with no changed M6 public behavior.

- [ ] **Step 8: Commit**

```bash
git add backend/app/imported_runs/validation.py backend/app/imported_runs/factory.py backend/app/imported_runs/service.py backend/tests/test_imported_run_validation.py
git commit -m "refactor: share analysis package validation"
```

---

### Task 3: Define strict Batch Analysis Package v1 schema and batch-scale safe extraction

**Files:**
- Create: `backend/app/imported_runs/batch_schema.py`
- Create: `backend/app/imported_runs/batch_archive.py`
- Create: `backend/tests/test_batch_schema.py`
- Create: `backend/tests/test_batch_archive.py`

**Interfaces:**
- Strict Pydantic types `BatchManifest`, `BatchItem`, `RecordingFingerprintWire`, `BatchImportSummary`, `BatchRunMapping`.
- `extract_batch_package(source: BinaryIO, destination: Path) -> Path`
- `read_batch_json(path: Path) -> object`
- `invalid_batch(message: str, *, details: dict[str, object] | None = None) -> PlatformError`
- Safety constants are exported exactly as specified in the design.

- [ ] **Step 1: Write failing schema tests**

Create `backend/tests/test_batch_schema.py` with one fully valid two-item manifest plus strict rejection tests. At minimum assert:

```python
def test_batch_manifest_accepts_v1_fixture():
    manifest = BatchManifest.model_validate(valid_manifest())
    assert manifest.schema_version == 1
    assert manifest.expected_items == 2
    assert manifest.items[1].recording.fingerprint.schema == "recording_fingerprint_v1"


def test_batch_manifest_rejects_unknown_fields():
    payload = valid_manifest()
    payload["surprise"] = True
    with pytest.raises(ValidationError):
        BatchManifest.model_validate(payload)


def test_batch_manifest_rejects_more_than_10000_items():
    payload = valid_manifest()
    payload["expected_items"] = 10001
    with pytest.raises(ValidationError):
        BatchManifest.model_validate(payload)
```

Also cover invalid SHA256 syntax, non-finite numbers, schema_version != 1, and `historical_reference.reference_only` not true.

- [ ] **Step 2: Run schema tests and verify RED**

```bash
pytest backend/tests/test_batch_schema.py -v
```

Expected: missing-module import failure.

- [ ] **Step 3: Implement strict schema objects**

Use the existing `PackageObject`, `PipelineMetadata`, `ExecutionMetadata`, `Name`, and `Number` from `app.imported_runs.schema` so strict/extra-forbid behavior stays consistent. Define exact public shapes:

```python
Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

class DatasetMetadata(PackageObject):
    name: Name
    split: Name

class ResultProvenance(PackageObject):
    code_commit: str | None = None
    config_sha256: Sha256Hex | None = None
    split_manifest_sha256: Sha256Hex | None = None
    source_predictions_sha256: Sha256Hex | None = None
    artifact_sha256: dict[str, Sha256Hex] = Field(default_factory=dict)

class TransportProvenance(PackageObject):
    exporter_version: Name
    platform_repo_commit: str | None = None
    export_timestamp: str | None = None

class RecordingFingerprintMetadata(PackageObject):
    data_format: Name
    sample_rate_hz: Number
    center_frequency_hz: Number
    frequency_low_hz: Number
    frequency_high_hz: Number
    num_samples: Annotated[int, Field(ge=1)]
    duration_s: Annotated[Number, Field(gt=0)]

class RecordingFingerprintWire(PackageObject):
    schema: Literal["recording_fingerprint_v1"]
    metadata: RecordingFingerprintMetadata
    ground_truth_sha256: Sha256Hex
    sha256: Sha256Hex

class BatchItemRecording(PackageObject):
    name: Name
    fingerprint: RecordingFingerprintWire

class BatchItem(PackageObject):
    key: Name
    package_path: Name
    recording: BatchItemRecording

class HistoricalReference(PackageObject):
    reference_only: Literal[True]
    report_sha256: Sha256Hex
    images: Annotated[int, Field(ge=0)] | None = None
    canonical_ground_truth: Annotated[int, Field(ge=0)] | None = None
    predictions: Annotated[int, Field(ge=0)] | None = None
    recorded_map50: Annotated[Number, Field(ge=0, le=1)] | None = None
    recorded_map50_95: Annotated[Number, Field(ge=0, le=1)] | None = None

class BatchManifest(PackageObject):
    schema_version: Literal[1]
    batch_id: Name
    pipeline: PipelineMetadata
    label_space: Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]+$", max_length=128)]
    dataset: DatasetMetadata
    expected_items: Annotated[int, Field(ge=1, le=10_000)]
    execution: ExecutionMetadata
    result_provenance: ResultProvenance
    transport_provenance: TransportProvenance
    recording_manifest_hash: Sha256Hex | None = None
    historical_reference: HistoricalReference | None = None
    items: Annotated[list[BatchItem], Field(min_length=1, max_length=10_000)]
```

`expected_items == len(items)` is intentionally a semantic validation rule in Task 5 so the error can use the stable batch business-error contract rather than a generic Pydantic message.

Define the response models exactly as:

```python
class BatchRunMapping(PackageObject):
    recording_id: Name
    recording_name: Name
    analysis_run_id: Name


class BatchImportSummary(PackageObject):
    batch_id: Name
    import_fingerprint: Sha256Hex
    archive_sha256: Sha256Hex
    dataset_name: Name
    dataset_split: Name
    pipeline_id: Name
    pipeline_version: Name
    label_space: Name
    item_count: int
    detection_count: int
    already_imported: bool
    created_runs: int
    existing_runs: int
    created_detections: int
    matched_recordings: int
    missing_recordings: int
    ambiguous_recordings: int
    fingerprint_mismatches: int
    recording_run_mapping: list[BatchRunMapping]
```

All count fields must be non-negative via Pydantic constraints in the actual implementation.

- [ ] **Step 4: Write failing archive-security tests**

Create ZIPs in memory and cover:

- root `batch_manifest.json` accepted;
- traversal `../escape` rejected;
- backslash/ADS/Windows reserved names rejected through the existing `safe_path()` semantics;
- duplicate or case-colliding paths rejected;
- symlink/special-file member rejected;
- encrypted flag rejected;
- corrupt ZIP rejected;
- 25,001 members rejected;
- exact exported constants equal the approved limits;
- expanded-size bound rejected by monkeypatching `MAX_BATCH_EXPANDED_BYTES` to a few bytes and creating a small archive that exceeds that test-local bound;
- member-count bound rejected by monkeypatching `MAX_BATCH_MEMBERS` to `2` and creating three members;
- upload-size bound rejected using a seekable fake file object that reports a length above a monkeypatched small `MAX_BATCH_UPLOAD_BYTES`;
- missing root `batch_manifest.json` rejected.

- [ ] **Step 5: Implement batch extraction without changing M6 constants**

`batch_archive.py` defines:

```python
MAX_BATCH_ITEMS = 10_000
MAX_TOTAL_DETECTIONS = 1_000_000
MAX_BATCH_UPLOAD_BYTES = 256 * 1024 * 1024
MAX_BATCH_EXPANDED_BYTES = 1024 * 1024 * 1024
MAX_BATCH_MEMBERS = 25_000
MAX_JSON_BYTES = 32 * 1024 * 1024
```

Reuse `safe_path()` by wrapping its `PlatformError` into `INVALID_BATCH_IMPORT_PACKAGE`. Reuse the same duplicate-path, member-kind, encrypted-member, and bounded streaming extraction logic as M6, but with the batch constants. Require `batch_manifest.json` at the extracted root; do not guess nested wrapper directories in V1.

`invalid_batch()` is exactly:

```python
def invalid_batch(message: str, *, details: dict[str, object] | None = None) -> PlatformError:
    return PlatformError(
        "INVALID_BATCH_IMPORT_PACKAGE",
        message,
        400,
        details={} if details is None else details,
    )
```

`read_batch_json()` must apply the same duplicate-key and non-finite JSON rejection as M6 and the same 32 MiB per-document limit, but raise `INVALID_BATCH_IMPORT_PACKAGE`.

- [ ] **Step 6: Run schema + archive tests and M6 archive regression**

```bash
pytest backend/tests/test_batch_schema.py backend/tests/test_batch_archive.py backend/tests/test_imported_runs.py -v
```

Expected: all PASS; M6 size/member behavior remains unchanged.

- [ ] **Step 7: Commit**

```bash
git add backend/app/imported_runs/batch_schema.py backend/app/imported_runs/batch_archive.py backend/tests/test_batch_schema.py backend/tests/test_batch_archive.py
git commit -m "feat: define batch analysis package v1"
```

---

### Task 4: Add canonical `batch_import_fingerprint_v1`

**Files:**
- Modify: `backend/app/imported_runs/fingerprint.py`
- Create: `backend/tests/test_batch_fingerprint.py`

**Interfaces:**
- `CanonicalBatchItem` is exactly:

```python
@dataclass(frozen=True)
class CanonicalBatchItem:
    key: str
    recording_fingerprint: str
    parameters: dict[str, object]
    detections: tuple[PackageDetection, ...]
```

- `build_batch_import_fingerprint(manifest: BatchManifest, items: tuple[CanonicalBatchItem, ...]) -> str`
- Fingerprint is independent of `batch_id`, transport provenance, archive SHA, item JSON-array order, and ZIP representation.

- [ ] **Step 1: Write failing semantic-idempotency hash tests**

Create two semantically identical manifests differing only in `batch_id`, `transport_provenance.export_timestamp`, `transport_provenance.platform_repo_commit`, and outer item array order. Assert:

```python
assert build_batch_import_fingerprint(first_manifest, first_items) == build_batch_import_fingerprint(second_manifest, second_items)
```

Then prove fingerprint changes when any of these change:

- pipeline version;
- result provenance checkpoint SHA;
- recording fingerprint;
- child parameters;
- physical bbox;
- class ID/name;
- confidence;
- optional score component.

- [ ] **Step 2: Run and verify RED**

```bash
pytest backend/tests/test_batch_fingerprint.py -v
```

Expected: missing `build_batch_import_fingerprint`.

- [ ] **Step 3: Implement canonical detection payload**

Use `canonical_number()` for all float values. Canonical scores are sorted by score key. Define a deterministic detection sort key so reordering `detections.json` does not change semantic identity:

```python
def canonical_detection_payload(item: PackageDetection) -> dict[str, object]:
    return {
        "id": item.id,
        "t_start_s": canonical_number(item.t_start_s),
        "t_end_s": canonical_number(item.t_end_s),
        "f_low_hz": canonical_number(item.f_low_hz),
        "f_high_hz": canonical_number(item.f_high_hz),
        "class_id": item.class_id,
        "class_name": item.class_name,
        "confidence": canonical_number(item.confidence),
        "scores": None if item.scores is None else {
            key: canonical_number(item.scores[key]) for key in sorted(item.scores)
        },
    }
```

Sort canonical detections by `canonical_json_bytes(canonical_detection_payload(item))`; this produces one total deterministic ordering even when `scores` is a nested mapping. Keep duplicate identical detections as duplicate list entries.

- [ ] **Step 4: Implement the batch semantic payload**

Canonicalize items by `item.key` so outer JSON-array order is transport-insensitive. Include only stable result provenance:

```python
payload = {
    "schema": BATCH_IMPORT_FINGERPRINT_SCHEMA,
    "batch_schema_version": manifest.schema_version,
    "pipeline": manifest.pipeline.model_dump(),
    "label_space": manifest.label_space,
    "dataset": manifest.dataset.model_dump(),
    "result_provenance": manifest.result_provenance.model_dump(),
    "items": canonical_items,
}
```

Each canonical item contains exactly:

- item key;
- recording fingerprint SHA256;
- canonical child manifest parameters;
- canonical final detections.

Do not include `batch_id`, `transport_provenance`, archive SHA, local IDs/paths, or generated IDs.

- [ ] **Step 5: Run fingerprint tests**

```bash
pytest backend/tests/test_recording_fingerprint.py backend/tests/test_batch_fingerprint.py -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/imported_runs/fingerprint.py backend/tests/test_batch_fingerprint.py
git commit -m "feat: add semantic batch import fingerprint"
```

---

### Task 5: Implement full batch preflight validation with bulk Recording/GT resolution

**Files:**
- Create: `backend/app/imported_runs/batch_validation.py`
- Create: `backend/tests/batch_import_fixture.py`
- Create: `backend/tests/test_batch_validation.py`

**Interfaces:**
- `ResolvedBatchItem` and `ValidatedBatch` are exactly:

```python
@dataclass(frozen=True)
class ResolvedBatchItem:
    item: BatchItem
    recording: RecordingModel
    manifest_recording: ManifestRecording
    recording_fingerprint: RecordingFingerprintValue
    child_root: Path
    validated_package: ValidatedAnalysisPackage


@dataclass(frozen=True)
class ValidatedBatch:
    manifest: BatchManifest
    items: tuple[ResolvedBatchItem, ...]
    total_detections: int
    import_fingerprint: str
    local_recording_manifest_hash: str | None
```

- `validate_batch(root: Path, session: Session, labels: LabelSpaceService) -> ValidatedBatch`
- Validation performs no ORM writes and no commits.

- [ ] **Step 1: Build a reusable two-Recording batch fixture**

Create `backend/tests/batch_import_fixture.py` helpers that:

- insert two local `RecordingModel` rows with the same `(dataset_name="SpaceNet", dataset_split="test", label_space="spacenet_14")` and distinct names;
- insert canonical GroundTruth rows;
- write matching child `manifest.json` and `detections.json` directories;
- build outer batch manifest fingerprints using production fingerprint helpers;
- allow one child to have zero detections.

The fixture must avoid private production methods and must make it easy to mutate one field for negative tests.

- [ ] **Step 2: Write failing successful-preflight and no-write tests**

```python
def test_validate_batch_resolves_every_recording_without_writing(session, batch_root, labels):
    before_runs = session.query(AnalysisRunModel).count()
    validated = validate_batch(batch_root, session, labels)
    assert len(validated.items) == 2
    assert validated.total_detections == 1
    assert session.query(AnalysisRunModel).count() == before_runs
```

- [ ] **Step 3: Write failing Recording resolution/fingerprint tests**

Cover exact stable errors and details:

- zero local candidate -> `BATCH_RECORDING_NOT_FOUND` with `item_key` and `recording_name`;
- two candidates for same dataset/split/name -> `BATCH_RECORDING_AMBIGUOUS`;
- metadata-only mismatch -> `RECORDING_FINGERPRINT_MISMATCH` with a `metadata_mismatches` mapping;
- GroundTruth-only mismatch -> `RECORDING_FINGERPRINT_MISMATCH` with `ground_truth_mismatch=true`;
- outer `recording_manifest_hash` mismatch -> `DATASET_MANIFEST_MISMATCH`.

- [ ] **Step 4: Write failing cross-item invariant tests**

Cover:

- `expected_items != len(items)`;
- duplicate item key;
- duplicate package path;
- duplicate Recording name;
- two outer items resolving to the same local Recording;
- child Recording name != outer item name;
- child `recording.dataset` != outer `dataset.name`;
- child pipeline ID mismatch;
- child pipeline version mismatch;
- child label space mismatch;
- missing child manifest;
- missing detections;
- malformed child package;
- total detections >1,000,000.

Use these stable business errors:

```text
INVALID_BATCH_IMPORT_PACKAGE     HTTP 400   structural/child/cross-item invalidity
BATCH_RECORDING_NOT_FOUND        HTTP 422   zero dataset+split+name candidates
BATCH_RECORDING_AMBIGUOUS        HTTP 409   more than one dataset+split+name candidate
RECORDING_FINGERPRINT_MISMATCH   HTTP 409   semantic Recording/GT mismatch
DATASET_MANIFEST_MISMATCH        HTTP 409   outer/local M8.6A dataset hash mismatch
BATCH_IMPORT_STATE_INCONSISTENT  HTTP 409   partial/duplicate prior semantic state
```

Generic structural/cross-item failures use `INVALID_BATCH_IMPORT_PACKAGE` and include the failing `item_key` when one exists.

- [ ] **Step 5: Run and verify RED**

```bash
pytest backend/tests/test_batch_validation.py -v
```

Expected: missing `batch_validation.py`.

- [ ] **Step 6: Implement one bulk Recording query and one bulk GroundTruth query**

Use outer dataset/label values as the authoritative local selector:

```python
recording_names = [item.recording.name for item in manifest.items]
recordings = list(session.scalars(
    select(RecordingModel).where(
        RecordingModel.dataset_name == manifest.dataset.name,
        RecordingModel.dataset_split == manifest.dataset.split,
        RecordingModel.name.in_(recording_names),
    )
).all())
```

Do not filter candidates by label space: the approved identity rule is dataset + split + name first, then semantic fingerprint verification. Build `recordings_by_name: dict[str, list[RecordingModel]]` in memory. Query all GroundTruth rows for the candidate recording IDs in one statement and group in memory. Do not query one Recording or GT list per item.

- [ ] **Step 7: Build local `ManifestRecording` objects and verify fingerprints**

Use the same fields as M8.6A. Build the local fingerprint using the candidate Recording's own `recording.label_space`; if it is null, use an empty sentinel that cannot equal a valid outer label space. Compare the canonical local fingerprint to the wire fingerprint generated with the outer label space. This makes a local label-space mismatch fail fingerprint validation instead of being hidden by candidate selection. Diagnostic metadata comparison uses canonical-number equality, not fuzzy tolerance. A fingerprint mismatch must not be ignored even if the human-readable metadata looks close.

- [ ] **Step 8: Verify outer dataset manifest hash when present**

Use the existing M8.6A service path as the authority rather than rebuilding a second dataset-selection rule:

```python
local_manifest_hash = DatasetBenchmarkService(session).prepare_manifest(
    manifest.dataset.name,
    manifest.dataset.split,
    manifest.label_space,
).recording_manifest_hash
```

When the outer `recording_manifest_hash` is present, compare it to `local_manifest_hash` exactly and raise `DATASET_MANIFEST_MISMATCH` on any difference. Do not trust the outer value as the local benchmark identity.

- [ ] **Step 9: Validate every child using Task 2 reusable validator**

For each item, resolve `safe_path(root, item.package_path)` through a batch-safe relative-path wrapper, require a directory, call `validate_extracted_package()`, then enforce outer/child consistency. Catch child `INVALID_IMPORT_PACKAGE` and rethrow `INVALID_BATCH_IMPORT_PACKAGE` with `item_key`, `recording_name`, and the child error message in details.

- [ ] **Step 10: Compute semantic fingerprint only after every child is valid**

Build `CanonicalBatchItem` values from the validated children and call `build_batch_import_fingerprint()`.

- [ ] **Step 11: Run validation tests plus M8.6A/M6 regression**

```bash
pytest backend/tests/test_batch_validation.py backend/tests/test_imported_runs.py backend/tests/test_benchmark_manifest.py backend/tests/test_benchmark_membership.py -v
```

Expected: all PASS.

- [ ] **Step 12: Commit**

```bash
git add backend/app/imported_runs/batch_validation.py backend/tests/batch_import_fixture.py backend/tests/test_batch_validation.py
git commit -m "feat: validate batch imports before writes"
```

---

### Task 6: Implement one-transaction batch import and semantic idempotency

**Files:**
- Create: `backend/app/imported_runs/batch_service.py`
- Create: `backend/tests/test_batch_import_service.py`

**Interfaces:**
- `BatchPackageImportService(session: Session, storage: StorageService, labels: LabelSpaceService)`
- `import_batch(source: BinaryIO) -> BatchImportSummary`
- The service owns temporary extraction, archive SHA256, preflight validation, idempotency lookup, ORM construction, one commit, rollback, and response mapping.

- [ ] **Step 1: Write failing happy-path transaction test**

Use the two-Recording fixture with one normal child and one zero-detection child. Assert after `import_batch()`:

```python
assert result.already_imported is False
assert result.item_count == 2
assert result.created_runs == 2
assert result.created_detections == 1
assert len(result.recording_run_mapping) == 2
assert session.query(AnalysisRunModel).count() == 2
assert session.query(DetectionResultModel).count() == 1
```

Assert both runs are standard `executor="imported"`, `status="completed"`, and contain `parameters_json["batch_import"]` with schema version, batch ID, item key/path, semantic fingerprint, recording fingerprint, and archive SHA256.

- [ ] **Step 2: Write failing atomicity test**

Build a batch where item 0 is valid and item 1 has invalid confidence. Call `import_batch()` and assert:

```python
assert session.query(AnalysisRunModel).count() == 0
assert session.query(DetectionResultModel).count() == 0
```

This proves no item is inserted before full preflight succeeds.

- [ ] **Step 2A: Add the required 2,499-valid + 1-invalid scale atomicity test**

Construct a 2,500-item synthetic batch with tiny ORM Recording/GT rows and tiny child JSON documents; items 0..2498 are valid and item 2499 contains an invalid confidence. Run the real preflight/import path and assert zero new `AnalysisRun` and zero new `DetectionResult` rows. The fixture may use zero detections for most valid items so the test stresses item-count/transaction semantics without manufacturing a large prediction corpus.

- [ ] **Step 3: Write failing DB-rollback test after preflight**

Monkeypatch the final commit to raise a SQLAlchemy exception after models are added. Assert rollback leaves zero new runs/detections. Do not simulate failure by invalidating an earlier child; this test specifically covers Phase 4 transaction failure.

- [ ] **Step 4: Write failing full-idempotency test**

Import the same semantic batch twice. Assert the second response:

```python
assert second.already_imported is True
assert second.created_runs == 0
assert second.created_detections == 0
assert second.existing_runs == 2
assert second.recording_run_mapping == first.recording_run_mapping
```

Database counts must not increase.

- [ ] **Step 5: Write failing repackaging-idempotency test**

Create two ZIP archives with the same semantic batch but different `batch_id`, transport timestamp, member order, and ZIP timestamp. Assert archive SHA256 may differ while the second import is still `already_imported=True` with zero new rows.

- [ ] **Step 6: Write failing partial-state test**

After a successful import, delete exactly one imported run in a controlled test transaction while leaving the other run with the same semantic fingerprint. Re-import the batch and assert:

```python
with pytest.raises(PlatformError) as exc:
    service.import_batch(source)
assert exc.value.code == "BATCH_IMPORT_STATE_INCONSISTENT"
```

The service must not fill the missing run and must not create a second copy of the existing run.

- [ ] **Step 7: Implement streaming archive SHA256 helper**

Hash the uploaded file without holding it all in memory, then seek back to position zero:

```python
def sha256_fileobj(source: BinaryIO) -> str:
    source.seek(0)
    digest = hashlib.sha256()
    while True:
        block = source.read(1024 * 1024)
        if not block:
            break
        digest.update(block)
    source.seek(0)
    return digest.hexdigest()
```

- [ ] **Step 8: Implement idempotency lookup in one bulk run query**

After `ValidatedBatch` exists, query completed imported runs for the resolved Recording IDs and matching pipeline ID/version. Filter `parameters_json["batch_import"]["import_fingerprint"]` in Python to avoid SQLite JSON-function dependency.

Require exact item-key/Recording mapping for a complete prior set:

```text
0 matching item keys      -> create new rows
N exact matching item keys -> already imported
1..N-1 matching item keys -> BATCH_IMPORT_STATE_INCONSISTENT
N rows but wrong/duplicate item-key mapping -> BATCH_IMPORT_STATE_INCONSISTENT
```

- [ ] **Step 9: Build all ORM objects only after preflight + idempotency decision**

Generate `run_<uuid>` and `det_<uuid>` IDs, call `build_imported_run_models()` for every item, keep all models in memory, then add all models once.

- [ ] **Step 10: Commit once and return deterministic mapping order**

Call `session.add_all()` for all runs/detections, then exactly one `session.commit()`. On exception, `session.rollback()` and re-raise. Return mapping in outer batch item order.

Batch uploads/extracted files remain only inside `TemporaryDirectory`; do not rename them into permanent `imports/<run_id>` directories.

- [ ] **Step 11: Run service tests**

```bash
pytest backend/tests/test_batch_import_service.py -v
```

Expected: all PASS.

- [ ] **Step 12: Run M6 regression again**

```bash
pytest backend/tests/test_imported_runs.py -v
```

Expected: all PASS; M6 still stores its promoted single-package directory exactly as before.

- [ ] **Step 13: Commit**

```bash
git add backend/app/imported_runs/batch_service.py backend/tests/test_batch_import_service.py
git commit -m "feat: import analysis batches atomically"
```

---

### Task 7: Expose `POST /api/imported-runs/batch`

**Files:**
- Modify: `backend/app/imported_runs/router.py`
- Create: `backend/tests/test_batch_import_api.py`

**Interfaces:**
- Endpoint: `POST /api/imported-runs/batch`
- Multipart field: `file`
- Response model: `BatchImportSummary`
- New import returns HTTP 201.
- Complete idempotent re-import also returns HTTP 201 with `already_imported=true`; it is a successful idempotent operation, not 409.

- [ ] **Step 1: Write failing API happy-path test**

```python
response = client.post(
    "/api/imported-runs/batch",
    files={"file": ("tiny.analysis-batch.zip", zip_bytes, "application/zip")},
)
assert response.status_code == 201
body = response.json()
assert body["already_imported"] is False
assert body["created_runs"] == 2
assert len(body["recording_run_mapping"]) == 2
```

- [ ] **Step 2: Write failing API idempotency test**

Post the same ZIP twice and assert the second response is 201, `already_imported=true`, `created_runs=0`, `created_detections=0`, and has the identical mapping.

- [ ] **Step 3: Write failing stable-error tests**

At minimum assert response JSON error codes for:

- malformed ZIP -> `INVALID_BATCH_IMPORT_PACKAGE`;
- missing Recording -> `BATCH_RECORDING_NOT_FOUND`;
- ambiguous Recording -> `BATCH_RECORDING_AMBIGUOUS`;
- fingerprint mismatch -> `RECORDING_FINGERPRINT_MISMATCH`;
- dataset hash mismatch -> `DATASET_MANIFEST_MISMATCH`;
- partial prior state -> `BATCH_IMPORT_STATE_INCONSISTENT`.

- [ ] **Step 4: Run and verify RED**

```bash
pytest backend/tests/test_batch_import_api.py -v
```

Expected: 404 for missing route.

- [ ] **Step 5: Add the route to the existing imported-runs router**

```python
@router.post("/api/imported-runs/batch", response_model=BatchImportSummary, status_code=201)
def import_analysis_batch(request: Request, file: UploadFile = File(...)):
    with request.app.state.database.session_factory() as session:
        return BatchPackageImportService(
            session,
            request.app.state.storage,
            LabelSpaceService(request.app.state.settings.label_space_root),
        ).import_batch(file.file)
```

Do not add a new top-level router or Job model.

- [ ] **Step 6: Run API + existing imported-run API regression**

```bash
pytest backend/tests/test_batch_import_api.py backend/tests/test_imported_runs.py -v
```

Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/imported_runs/router.py backend/tests/test_batch_import_api.py
git commit -m "feat: expose batch analysis package import api"
```

---

### Task 8: Add deterministic generic batch writer and tiny historical exporter tests

**Files:**
- Create: `research/m9_legacy_bridge/batch_exporter.py`
- Create: `backend/tests/test_m9_legacy_bridge_batch_exporter.py`
- Regression: `backend/tests/test_m9_legacy_bridge_exporter.py`

**Interfaces:**
- `BatchExportItem` is exactly:

```python
@dataclass(frozen=True)
class BatchExportItem:
    item: BatchItem
    child_manifest: Manifest
    detections: tuple[PackageDetection, ...]
```

- `export_batch_package(output_path: Path, manifest: BatchManifest, items: tuple[BatchExportItem, ...]) -> Path`
- Writer emits only `batch_manifest.json` plus each child `manifest.json` and `detections.json`.
- ZIP members are emitted in deterministic order with fixed timestamps.

- [ ] **Step 1: Write failing two-Recording writer test**

Use Recording A with two detections and Recording B with zero detections. Assert the ZIP contains exactly:

```text
batch_manifest.json
items/000000/manifest.json
items/000000/detections.json
items/000001/manifest.json
items/000001/detections.json
```

Assert B's `detections.json` is exactly a JSON object with an empty `detections` array.

- [ ] **Step 2: Write failing child-schema compatibility test**

Open both child manifests and validate each with the existing `Manifest` Pydantic class; validate both detection arrays with `TypeAdapter(list[PackageDetection])`. Assert child:

- pipeline ID/name/version match outer;
- label space matches outer;
- `recording.name` matches outer item;
- `recording.dataset == outer.dataset.name`;
- `results.detections == "detections.json"`;
- `results.metrics is None`.

- [ ] **Step 3: Write failing deterministic transport test**

Export the exact same manifest/items twice with the same transport provenance and assert byte-identical archives and equal archive SHA256. Separately, change transport timestamp and assert the semantic `batch_import_fingerprint_v1` remains equal even if archive SHA differs.

- [ ] **Step 4: Run and verify RED**

```bash
pytest backend/tests/test_m9_legacy_bridge_batch_exporter.py -v
```

Expected: missing module.

- [ ] **Step 5: Implement deterministic ZIP writer**

Use stable compact/sorted JSON serialization and explicit `ZipInfo` timestamps:

```python
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


def _write_json_member(archive: zipfile.ZipFile, name: str, payload: object) -> None:
    info = zipfile.ZipInfo(name, date_time=FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n"
    archive.writestr(info, data)
```

Before writing, require the `BatchExportItem.item.key` set to equal the outer `BatchManifest.items` key set exactly, and for every key require the same `package_path` and Recording name. Then emit `batch_manifest.json` first, followed by items in outer manifest order, each child manifest before detections. The writer must not add `metrics.json` or artifacts.

- [ ] **Step 6: Run new writer tests + old M9 exporter regression**

```bash
pytest backend/tests/test_m9_legacy_bridge_batch_exporter.py backend/tests/test_m9_legacy_bridge_exporter.py -v
```

Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add research/m9_legacy_bridge/batch_exporter.py backend/tests/test_m9_legacy_bridge_batch_exporter.py
git commit -m "feat: add deterministic historical batch writer"
```

---

### Task 9: Add historical SpaceNet batch CLI using frozen JSONL and existing LegacyDetectionAdapter

**Files:**
- Create: `research/m9_legacy_bridge/batch_cli.py`
- Create: `backend/tests/test_m9_legacy_bridge_batch_cli.py`
- Regression: `backend/tests/test_m9_legacy_bridge_adapter.py`

**Interfaces:**
- CLI: `python -m research.m9_legacy_bridge.batch_cli`
- Inputs: predictions JSONL, test manifest, SpaceNet `advanced/test` directory, historical metric report, detector checkpoint, FRN checkpoint, frozen config, label-space path, output path, optional expected SHA256 values.
- Output: one JSON summary on stdout containing counts, hashes, semantic fingerprint, archive SHA, and output path.
- Exit code 0 on success; nonzero with a clear error on invalid frozen assets/split/predictions.

- [ ] **Step 1: Write a tiny real-file SpaceNet CLI fixture**

Create a temporary `advanced/test` directory with two valid tiny `.bin + .json` samples. Sample `a` has two historical predictions; sample `b` has none. Create a split manifest with both IDs, tiny dummy checkpoint/config files used only for SHA256, and a historical metrics JSON.

- [ ] **Step 2: Write failing complete-split export test**

Invoke `batch_cli.main(argv)` or a subprocess. Assert summary:

```python
assert summary["expected_samples"] == 2
assert summary["exported_items"] == 2
assert summary["source_prediction_rows"] == 2
assert summary["zero_detection_items"] == 1
assert summary["unexpected_sample_ids"] == 0
assert summary["missing_dataset_samples"] == 0
assert len(summary["recording_manifest_hash"]) == 64
assert len(summary["batch_import_fingerprint"]) == 64
assert len(summary["archive_sha256"]) == 64
```

- [ ] **Step 3: Write failing unexpected-sample and missing-sample tests**

- A JSONL row whose `sample_id` is outside the split must fail; it is not silently ignored.
- A split ID missing from the SpaceNet directory must fail.
- A zero-prediction split member must succeed and remain in the archive.

- [ ] **Step 4: Write failing expected-hash mismatch test**

Supply one optional expected hash that does not match the actual file. Assert nonzero exit/exception before archive creation.

- [ ] **Step 5: Run and verify RED**

```bash
pytest backend/tests/test_m9_legacy_bridge_batch_cli.py -v
```

Expected: missing batch CLI.

- [ ] **Step 6: Implement CLI arguments and self-hashing**

Arguments are exactly:

```text
--predictions PATH
--test-manifest PATH
--dataset-dir PATH
--metrics PATH
--detector-checkpoint PATH
--frn-checkpoint PATH
--config PATH
--label-space PATH
--output PATH
--expected-predictions-sha256 HEX       optional
--expected-test-manifest-sha256 HEX     optional
--expected-detector-sha256 HEX          optional
--expected-frn-sha256 HEX               optional
--expected-config-sha256 HEX            optional
```

Compute actual SHA256 from each supplied frozen asset. Optional expected hashes are hard gates when supplied.

- [ ] **Step 7: Parse the frozen split as the item universe**

Require `test_ids` to be a list. Normalize each entry with `str(value)`, reject null/empty values and values where `Path(stem).name != stem`, then require uniqueness. Compare the normalized set to prediction sample IDs; any prediction ID outside the split is an error. Resolve every normalized split ID through `SpaceNetAdapter`.

For package order, sort loaded samples by `sample.id` using the same lexical Recording-name order as M8.6A, not JSONL line order and not ZIP order.

- [ ] **Step 8: Group JSONL once and reuse `LegacyDetectionAdapter` per Recording**

Load/group the 33k-scale JSONL in memory by `str(row["sample_id"])`. For each sample:

```python
legacy_adapter = LegacyDetectionAdapter(recording=recording_context, label_space=label_space)
detections = legacy_adapter.adapt_many(grouped_rows.get(sample.id, []))
```

Do not reject empty `detections`; batch export requires zero-detection children.

- [ ] **Step 9: Build Recording fingerprints and full dataset manifest hash from SpaceNetAdapter metadata/GT**

Convert each SpaceNetSample to the exact `ManifestRecording` fields used by M8.6A, using `recording_id=sample.id` only as a required local placeholder field; that field is excluded from all canonical hashes. No call to `read_iq()` is permitted. Using `.bin` file size through `SpaceNetAdapter.load()` is allowed.

- [ ] **Step 10: Build batch ID, provenance, and historical reference**

Generate the first-case transport batch ID deterministically from stable source identity:

```python
batch_id = (
    "zoomspec_yolo26n_aug_combined_frn_v3-"
    "SpaceNet-test-"
    + source_predictions_sha256[:12]
)
```

Result provenance contains actual computed hashes. For the historical pipeline, `artifact_sha256` uses the generic mapping keys `detector_checkpoint` and `frn_checkpoint`. Set `result_provenance.code_commit=None` unless a trustworthy commit tied to the frozen legacy result is supplied by the recorded provenance; do not substitute the current exporter commit. Transport provenance contains `exporter_version="batch_analysis_package_v1"`, the current platform repository commit when `git rev-parse HEAD` succeeds, and an ISO-8601 UTC export timestamp.

Parse the historical metrics report strictly into:

```python
historical_reference = {
    "reference_only": True,
    "report_sha256": metrics_sha256,
    "images": int(report["images"]),
    "canonical_ground_truth": int(report["canonical_ground_truth"]),
    "predictions": int(report["predictions"]),
    "recorded_map50": float(report["map_50"]),
    "recorded_map50_95": float(report["map_50_95"]),
}
```

Missing required first-case report keys are an export error; do not silently write null historical reference fields. The semantic fingerprint must be computed from result provenance + canonical child semantics before writing the archive.

- [ ] **Step 11: Export and print machine-readable summary**

The success JSON contains at least:

```text
dataset
split
label_space
pipeline_id
pipeline_version
expected_samples
exported_items
source_prediction_rows
zero_detection_items
unexpected_sample_ids
missing_dataset_samples
fingerprint_failures
recording_manifest_hash
batch_import_fingerprint
archive_sha256
output_path
```

- [ ] **Step 12: Run CLI + existing legacy-adapter regression**

```bash
pytest backend/tests/test_m9_legacy_bridge_batch_cli.py backend/tests/test_m9_legacy_bridge_batch_exporter.py backend/tests/test_m9_legacy_bridge_adapter.py -v
```

Expected: all PASS.

- [ ] **Step 13: Commit**

```bash
git add research/m9_legacy_bridge/batch_cli.py backend/tests/test_m9_legacy_bridge_batch_cli.py
git commit -m "feat: export historical spacenet analysis batches"
```

---

### Task 10: Local implementation verification and cross-machine feature-branch handoff

**Owner:** `本地电脑opencode`

**Files:** no production changes unless a failing test reveals a defect that is fixed under TDD.

- [ ] **Step 1: Run the focused M8.6B suite**

```bash
pytest \
  backend/tests/test_recording_fingerprint.py \
  backend/tests/test_imported_run_validation.py \
  backend/tests/test_batch_schema.py \
  backend/tests/test_batch_archive.py \
  backend/tests/test_batch_fingerprint.py \
  backend/tests/test_batch_validation.py \
  backend/tests/test_batch_import_service.py \
  backend/tests/test_batch_import_api.py \
  backend/tests/test_m9_legacy_bridge_batch_exporter.py \
  backend/tests/test_m9_legacy_bridge_batch_cli.py -v
```

Expected: 0 failed.

- [ ] **Step 2: Run required compatibility regression**

```bash
pytest \
  backend/tests/test_imported_runs.py \
  backend/tests/test_benchmark_manifest.py \
  backend/tests/test_benchmark_membership.py \
  backend/tests/test_benchmark_api.py \
  backend/tests/test_m9_legacy_bridge_adapter.py \
  backend/tests/test_m9_legacy_bridge_exporter.py \
  backend/tests/test_evaluation_matching.py \
  backend/tests/test_evaluation_ap.py -v
```

Expected: 0 failed.

- [ ] **Step 3: Run fresh full backend suite**

```bash
pytest backend/tests -v
```

Expected: exit 0, 0 failed. Record the actual pass count; do not predict it.

- [ ] **Step 4: Verify scope before server handoff**

```bash
git diff d93913c98c3cb528de4cda831bdf583f25f4ee29..HEAD --name-status
git diff d93913c98c3cb528de4cda831bdf583f25f4ee29..HEAD -- backend/app/evaluation/matching.py
git status --short --branch
```

Requirements:

- no frontend changes;
- `matching.py` diff empty;
- no DatasetEvaluation protocol changes;
- no BatchRun/BatchImportModel/database migration;
- no model/data/archive artifacts;
- working tree clean.

- [ ] **Step 5: Push only the feature branch for server acceptance**

```bash
git push -u origin feature/m8-6b-batch-package
```

Do not merge `feature/v1-core` yet. Do not touch `main`.

---

### Task 11: Export the real 2,500-sample historical batch on the server

**Owner:** `服务器opencode`

**Code source:** checkout/pull `feature/m8-6b-batch-package` from GitHub. Do not modify platform source during this acceptance task.

**Frozen inputs:**

```text
predictions:
/root/autodl-tmp/Claude/reports_claude/test_val_detections_augv3.jsonl
expected SHA256:
950ad87ec355169b1904da4364f296ade5854926d4e02dc83049d9585859efcd

test manifest:
/root/autodl-tmp/Claude/reports_claude/test_manifest.json
expected SHA256:
ea5b41d0cd6b3393be75ece3f3bbc8aee38e782ef421e8cd0d1b3e580839f5b6

dataset:
/root/autodl-tmp/SpaceNet_Dataset/advanced/test

detector checkpoint:
/root/autodl-tmp/Claude/runs/cpn/ls_stft_yolo26n_aug_warm/weights/best.pt
expected SHA256:
eba4fa4b112a0e61cc1013e96f99d1ae82b845f4be1e8b1f80bd2089d1f82311

FRN checkpoint:
/root/autodl-tmp/Claude/artifacts/frn_combined_v3_training/best.pt
expected SHA256:
da6087da2fbfbaa5ba0e2cb210d08c24ee8b2af8418329d32216f7c77253be67

frozen config:
/root/autodl-tmp/Claude/configs/frozen_full_pipeline_v26_aug_combined.yaml
expected SHA256:
030dbfa77353f876728252c2f247b47816baf8921a7641bb8873ae9035d9d7ec

historical metrics:
/root/autodl-tmp/Claude/reports_claude/test_full_val_map_augv3.json

output directory:
/root/autodl-tmp/m8_6_exports
```

- [ ] **Step 1: Verify server branch and frozen assets before export**

Record:

```bash
git status --short --branch
git rev-parse HEAD
sha256sum /root/autodl-tmp/Claude/reports_claude/test_val_detections_augv3.jsonl
sha256sum /root/autodl-tmp/Claude/reports_claude/test_manifest.json
sha256sum /root/autodl-tmp/Claude/runs/cpn/ls_stft_yolo26n_aug_warm/weights/best.pt
sha256sum /root/autodl-tmp/Claude/artifacts/frn_combined_v3_training/best.pt
sha256sum /root/autodl-tmp/Claude/configs/frozen_full_pipeline_v26_aug_combined.yaml
```

All expected hashes must match. If any differ, STOP; do not export under pipeline version 1.0.0.

- [ ] **Step 2: Run the real batch exporter without GPU/model inference**

From repository root, run:

```bash
python -m research.m9_legacy_bridge.batch_cli \
  --predictions /root/autodl-tmp/Claude/reports_claude/test_val_detections_augv3.jsonl \
  --test-manifest /root/autodl-tmp/Claude/reports_claude/test_manifest.json \
  --dataset-dir /root/autodl-tmp/SpaceNet_Dataset/advanced/test \
  --metrics /root/autodl-tmp/Claude/reports_claude/test_full_val_map_augv3.json \
  --detector-checkpoint /root/autodl-tmp/Claude/runs/cpn/ls_stft_yolo26n_aug_warm/weights/best.pt \
  --frn-checkpoint /root/autodl-tmp/Claude/artifacts/frn_combined_v3_training/best.pt \
  --config /root/autodl-tmp/Claude/configs/frozen_full_pipeline_v26_aug_combined.yaml \
  --label-space label_spaces/spacenet_14.json \
  --output /root/autodl-tmp/m8_6_exports/zoomspec_yolo26n_aug_combined_frn_v3-spacenet-test.analysis-batch.zip \
  --expected-predictions-sha256 950ad87ec355169b1904da4364f296ade5854926d4e02dc83049d9585859efcd \
  --expected-test-manifest-sha256 ea5b41d0cd6b3393be75ece3f3bbc8aee38e782ef421e8cd0d1b3e580839f5b6 \
  --expected-detector-sha256 eba4fa4b112a0e61cc1013e96f99d1ae82b845f4be1e8b1f80bd2089d1f82311 \
  --expected-frn-sha256 da6087da2fbfbaa5ba0e2cb210d08c24ee8b2af8418329d32216f7c77253be67 \
  --expected-config-sha256 030dbfa77353f876728252c2f247b47816baf8921a7641bb8873ae9035d9d7ec
```

- [ ] **Step 3: Require exact real-export gates**

The JSON summary must report:

```text
dataset = SpaceNet
split = test
label_space = spacenet_14
pipeline_id = zoomspec_yolo26n_aug_combined_frn_v3
pipeline_version = 1.0.0
expected_samples = 2500
exported_items = 2500
source_prediction_rows = 33373
unexpected_sample_ids = 0
missing_dataset_samples = 0
fingerprint_failures = 0
```

Record the actual values for:

```text
zero_detection_items
recording_manifest_hash
batch_import_fingerprint
archive_sha256
output_path
archive size
```

Do not assume `zero_detection_items` in advance.

- [ ] **Step 4: Inspect archive structure without extracting all IQ data**

Use `unzip -l` or Python `zipfile` to confirm `1 + 2*2500 = 5001` core JSON members and no `metrics.json`, `.bin`, checkpoint, or artifact members.

- [ ] **Step 5: STOP on any acceptance mismatch**

If item/prediction counts, hashes, split membership, fingerprints, or archive schema fail, do not modify source on the server. Report exact failure and return to the local implementation branch.

---

### Task 12: Transfer-integrity gate and first real local batch import

**Owner:** `本地电脑opencode`

**Prerequisite:** the operator has copied the server-generated `.analysis-batch.zip` to a local path outside Git.

- [ ] **Step 1: Verify archive SHA256 after transfer**

Compute local SHA256 and compare to the exact server-reported `archive_sha256`. If it differs, STOP before opening/importing the archive.

- [ ] **Step 2: Confirm local SpaceNet test registration**

Using the local API/DB, verify exactly 2,500 matching Recordings for:

```text
dataset_name = SpaceNet
dataset_split = test
label_space = spacenet_14
```

Do not re-register if the existing dataset is already the intended snapshot merely to make counts match.

- [ ] **Step 3: Compute local M8.6A manifest hash before import**

Call the existing prepare-manifest API/service for `SpaceNet/test/spacenet_14`. Compare its `recording_manifest_hash` to the server batch outer hash. They must be exactly equal.

If not equal, STOP and report both hashes; do not import and do not edit local GT.

- [ ] **Step 4: Record pre-import row counts**

Record counts for:

```text
AnalysisRun rows for pipeline zoomspec_yolo26n_aug_combined_frn_v3 v1.0.0
DetectionResult rows belonging to those runs
```

If prior runs from unrelated single-sample M9.0 acceptance exist, record them separately; the batch import's semantic fingerprint will distinguish the new batch set.

- [ ] **Step 5: Import the real batch through the public endpoint**

Use the running local FastAPI application or TestClient-equivalent public route:

```text
POST /api/imported-runs/batch
multipart file = transferred archive
```

The first successful response must report:

```text
already_imported = false
item_count = 2500
created_runs = 2500
created_detections = 33373
matched_recordings = 2500
missing_recordings = 0
ambiguous_recordings = 0
fingerprint_mismatches = 0
recording_run_mapping length = 2500
```

- [ ] **Step 6: Verify persisted run semantics**

Bulk-query the returned run IDs and assert all 2,500 are:

```text
executor = imported
status = completed
pipeline_id = zoomspec_yolo26n_aug_combined_frn_v3
pipeline_version = 1.0.0
```

Verify total detections across those exact returned run IDs is 33,373. Do not infer this count from all historical runs in the database.

- [ ] **Step 7: Verify exact mapping uniqueness**

Assert the response has 2,500 unique `recording_id`, 2,500 unique `recording_name`, and 2,500 unique `analysis_run_id` values.

- [ ] **Step 8: Immediately perform the required real idempotency re-import**

Upload the exact same archive again. Require:

```text
already_imported = true
created_runs = 0
created_detections = 0
existing_runs = 2500
same import_fingerprint
same 2500 Recording -> AnalysisRun mappings
```

Re-query DB counts and prove they did not increase.

- [ ] **Step 9: Confirm M8.6B boundary**

After successful import, confirm no `DatasetEvaluation` was automatically created and no benchmark worker started.

---

### Task 13: Record real M8.6B provenance/acceptance, rerun full regression, and push final feature branch

**Owner:** `本地电脑opencode`

**Files:**
- Create: `docs/research/m8_6b_batch_analysis_package.md`

- [ ] **Step 1: Write the acceptance record using only actual observed values**

The research note must contain:

- feature branch + implementation HEAD;
- server source asset paths and verified SHA256 values;
- actual archive path, size, and archive SHA256;
- actual `recording_manifest_hash`;
- actual `batch_import_fingerprint_v1`;
- actual zero-detection item count;
- exact exported item/prediction counts;
- local pre-import manifest hash and equality result;
- first import summary/counts;
- second import idempotency summary/counts;
- confirmation no DatasetEvaluation was created;
- historical `.4970686/.3732513` metrics labeled reference-only and not platform-computed in M8.6B;
- note that GT duplicate-policy parity remains deferred to M8.6C.

Do not state M8.6C metrics, UI, or parity conclusions.

- [ ] **Step 2: Commit the acceptance note**

```bash
git add docs/research/m8_6b_batch_analysis_package.md
git commit -m "docs: record m8.6b batch package acceptance"
```

- [ ] **Step 3: Run fresh focused M8.6B suite after real acceptance**

```bash
pytest \
  backend/tests/test_recording_fingerprint.py \
  backend/tests/test_imported_run_validation.py \
  backend/tests/test_batch_schema.py \
  backend/tests/test_batch_archive.py \
  backend/tests/test_batch_fingerprint.py \
  backend/tests/test_batch_validation.py \
  backend/tests/test_batch_import_service.py \
  backend/tests/test_batch_import_api.py \
  backend/tests/test_m9_legacy_bridge_batch_exporter.py \
  backend/tests/test_m9_legacy_bridge_batch_cli.py -v
```

Expected: 0 failed.

- [ ] **Step 4: Run fresh full backend suite**

```bash
pytest backend/tests -v
```

Expected: exit 0, 0 failed. Record actual passed count and any warnings.

- [ ] **Step 5: Verify immutable compatibility boundaries**

```bash
git diff d93913c98c3cb528de4cda831bdf583f25f4ee29..HEAD --name-status
git diff d93913c98c3cb528de4cda831bdf583f25f4ee29..HEAD -- backend/app/evaluation/matching.py
git diff d93913c98c3cb528de4cda831bdf583f25f4ee29..HEAD -- backend/app/evaluation/ap.py
git diff d93913c98c3cb528de4cda831bdf583f25f4ee29..HEAD -- frontend
```

Requirements:

- `matching.py` empty diff;
- M8.6A AP evaluator empty diff unless a purely import/path change was explicitly required by the approved plan; no metric semantics may change;
- frontend empty diff;
- no ORM/database migration files added for BatchRun/BatchImport;
- no Analysis Package v1 schema semantic change;
- no generated ZIP/checkpoint/data/DB artifacts tracked.

- [ ] **Step 6: Verify Git history and cleanliness**

```bash
git status --short --branch
git log --oneline --decorate -20
```

Working tree must be clean.

- [ ] **Step 7: Push final feature branch normally**

```bash
git push origin feature/m8-6b-batch-package
```

Do not merge `feature/v1-core` in this task. Shared-baseline integration is a separate post-report decision after independent review.

---

## Expected Commit Sequence

The implementation should normally produce these reviewable commits in order:

```text
feat: add stable recording fingerprints
refactor: share analysis package validation
feat: define batch analysis package v1
feat: add semantic batch import fingerprint
feat: validate batch imports before writes
feat: import analysis batches atomically
feat: expose batch analysis package import api
feat: add deterministic historical batch writer
feat: export historical spacenet analysis batches
docs: record m8.6b batch package acceptance
```

Small corrective commits are acceptable when a failing test or real-asset acceptance exposes a real defect. Do not squash the history merely to match this list.

---

## Final M8.6B Verification Checklist

Before claiming M8.6B complete, fresh evidence must prove all of the following:

```text
Shared starting baseline:
feature/v1-core @ d93913c98c3cb528de4cda831bdf583f25f4ee29

Feature branch:
feature/m8-6b-batch-package

M8.6A recording manifest hash unchanged:
YES

M6 single-package import regression:
PASS

Batch schema strict:
PASS

Batch archive limits:
items <= 10000
members <= 25000
upload <= 256 MiB
expanded <= 1 GiB
total detections <= 1000000
JSON <= 32 MiB each

Recording matching:
dataset + split + name unique candidate
then mandatory metadata + canonical-GT fingerprint

Raw IQ SHA/read for identity:
NO

All children validated before ORM writes:
YES

Invalid child leaves new rows:
0 AnalysisRuns
0 DetectionResults

Successful import transaction commits:
once

Same semantic batch re-import:
already_imported = true
new runs = 0
new detections = 0
same mapping

Partial prior fingerprint state:
BATCH_IMPORT_STATE_INCONSISTENT

Historical exporter uses LegacyDetectionAdapter:
YES

Historical inference/model rerun/GPU:
NO

Real server export:
2500 items
33373 source prediction rows
unexpected IDs = 0
missing dataset samples = 0

Server/local recording_manifest_hash:
exact match

Real first local import:
2500 created AnalysisRuns
33373 created DetectionResults
2500 exact mappings

Real second local import:
0 created rows
same mappings

Automatic DatasetEvaluation creation:
NO

Frontend changes:
NO

M8.5 matching semantics changed:
NO

M8.6A AP semantics changed:
NO

Generated ZIP/checkpoint/dataset committed:
NO

Full backend suite:
0 failed
```

---

## Required Final Report

The executor must end with a single report titled:

`M8.6B Batch Analysis Package Report`

It must include:

1. Branch + HEAD.
2. Base SHA + ancestry.
3. Commit list.
4. Files created/modified.
5. M8.6A manifest-hash regression evidence.
6. M6 compatibility regression evidence.
7. Batch schema and exact safety bounds.
8. Recording candidate/fingerprint semantics and tests.
9. Batch semantic fingerprint inclusions/exclusions.
10. All-or-nothing validation/transaction evidence.
11. Idempotency and partial-state evidence.
12. API route/result/error behavior.
13. Tiny zero-detection batch exporter evidence.
14. Historical exporter no-inference evidence.
15. Real server frozen-asset hashes.
16. Real server export counts, zero-detection count, dataset hash, semantic fingerprint, archive hash/size/path.
17. Local transfer SHA gate.
18. Local pre-import dataset hash equality.
19. First real import exact counts/mapping.
20. Second real import exact idempotency counts/mapping.
21. Confirmation no DatasetEvaluation was created/run.
22. Focused test result.
23. Full backend result with actual pass/fail count.
24. Scope checks: `matching.py`, AP evaluator, frontend, BatchRun/BatchImportModel, artifacts.
25. Remote feature branch SHA + working tree.
26. Problems/warnings.
27. Final verdict `PASS` or `FAIL`.

The report must then STOP. It must not begin M8.6C or M9.1.
