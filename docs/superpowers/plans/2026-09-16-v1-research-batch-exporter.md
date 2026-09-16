# WISA V1 Research Batch Exporter Implementation Plan

- **Status:** Planned (no code executed during planning)
- **Design authority:** `docs/superpowers/specs/2026-09-16-v1-research-server-artifact-workflow-design.md`
- **Authoritative baseline:** `integration/v1-candidate` @ `6716eaef217de97c5746d0468d62abede9b1c63c`
- **Target format:** Batch Analysis Package v1 (BAPv1) — **no v2**
- **Exact control-plane interpreter:** `/root/autodl-tmp/WISA-backend-v1-plan-b-local-gpu/.venv/bin/python` (ML-free)

## Goal

Provide a small research-server/export adapter that converts independently computed detections into the **existing** BAPv1 without owning a WISA database, without CUDA, and without `remote_gpu` orchestration.

## Reuse-First Lock (no duplicate writer)

The repository already contains the exact required seams. This plan **reuses** them and adds no second package format:

| Seam | Location | Use |
|---|---|---|
| Generic deterministic writer | `research/m9_legacy_bridge/batch_exporter.py` (`BatchExportItem`, `export_batch_package`) | write BAPv1 ZIP |
| Detection adapter | `research/m9_legacy_bridge/adapter.py` (`LegacyDetectionAdapter`, `load_label_space`) | validate/convert computed detections |
| Legacy detection record | `research/m9_legacy_bridge/schema.py` (`LegacyDetection`, `PlatformDetection`, `RecordingContext`, `PipelineMetadata`) | prediction contract |
| Fingerprints | `app/imported_runs/fingerprint.py` (`build_recording_fingerprint`, `build_batch_import_fingerprint`, `CanonicalBatchItem`) | `recording_fingerprint_v1` / `batch_import_fingerprint_v1` |
| Batch wire schema | `app/imported_runs/batch_schema.py` | `BatchManifest`, `BatchItem`, `BatchItemRecording`, `RecordingFingerprintWire`, `ResultProvenance`, `TransportProvenance`, `DatasetMetadata`, `ExecutionMetadata`, `HistoricalReference` |
| Child wire schema | `app/imported_runs/schema.py` | `Manifest`, `PackageDetection`, `PipelineMetadata`, `ExecutionMetadata` |
| Ground-truth ordering | `app/benchmarks/manifest.py` (`ManifestGroundTruth`, `ManifestRecording`, `build_recording_manifest`) | canonical GT + optional `recording_manifest_hash` |
| SpaceNet source | `app/datasets/spacenet.py` (`SpaceNetAdapter.load`) | recording bounds + GT (never full-IQ hashing) |

Layer separation (mandatory):

```text
generic deterministic package writer   (existing research/m9_legacy_bridge/batch_exporter.py)
        ↑
research-result adapter                  (new research/v1_artifact_exporter/*)
        ↑
CLI / operator entrypoint                (new research/v1_artifact_exporter/cli.py)
```

The generic writer must never know ZoomSpec internals; the layer above it must never import `torch`/`ultralytics`, open a DB, or read `platform.db`.

## Proposed Files

Production (new package):
- `research/v1_artifact_exporter/__init__.py`
- `research/v1_artifact_exporter/predictions.py`
- `research/v1_artifact_exporter/sources.py`
- `research/v1_artifact_exporter/exporter.py`
- `research/v1_artifact_exporter/cli.py`

Tests (new):
- `backend/tests/test_v1_research_batch_exporter.py`

Unchanged reused files: `research/m9_legacy_bridge/{batch_exporter,adapter,schema}.py`; add only imports.

## Exact Interfaces

`research/v1_artifact_exporter/predictions.py`:

```python
DETECTION_KEYS = ("sample_id", "t0_s", "t1_s", "f0_hz", "f1_hz", "class_id", "score")

def load_predictions_jsonl(path: Path) -> dict[str, list[dict]]:
    """Group JSONL rows by sample_id; reject malformed/unexpected rows."""

def adapt_detections(
    records: Sequence[Mapping[str, Any]],
    *,
    recording: RecordingContext,
    label_space: Mapping[int, str],
) -> tuple[PackageDetection, ...]:
    """LegacyDetectionAdapter(...).adapt_many(...) -> PackageDetection tuple."""
```

`research/v1_artifact_exporter/sources.py`:

```python
class UnexpectedSampleError(ValueError): ...
class MissingDatasetSampleError(ValueError): ...

def build_research_samples(
    *,
    dataset_dir: Path,
    label_space_root: Path,
    label_space_id: str,
    label_classes: Mapping[int, str],
    sample_ids: Sequence[str],
    predictions_by_sample: Mapping[str, Sequence[Mapping[str, Any]]],
    dataset_name: str,
    dataset_split: str,
) -> tuple[ResearchSample, ...]:
    """Load SpaceNet samples, compute recording_fingerprint_v1, adapt detections.

    - unexpected prediction sample_id (not in sample_ids) -> UnexpectedSampleError
    - sample_ids not present in the dataset split -> MissingDatasetSampleError
    - never opens .bin bytes (SpaceNetAdapter.load stats only the .bin)
    """
```

`research/v1_artifact_exporter/exporter.py`:

```python
@dataclass(frozen=True)
class ResearchSample:
    manifest_recording: ManifestRecording
    detections: tuple[PackageDetection, ...]

@dataclass(frozen=True)
class ResearchBatchRequest:
    dataset_name: str
    dataset_split: str
    label_space: str
    pipeline: PipelineMetadata
    execution: ExecutionMetadata
    result_provenance: ResultProvenance
    transport_provenance: TransportProvenance
    batch_id: str
    samples: tuple[ResearchSample, ...]
    recording_manifest_hash: str | None = None
    historical_reference: HistoricalReference | None = None

@dataclass(frozen=True)
class ResearchBatchResult:
    output_path: Path
    archive_sha256: str
    import_fingerprint: str
    recording_manifest_hash: str | None
    item_count: int
    detection_count: int
    zero_detection_items: int

def build_batch_manifest(request: ResearchBatchRequest) -> tuple[BatchManifest, tuple[BatchExportItem, ...], tuple[CanonicalBatchItem, ...]]:
    """Lexically order samples; key=f'{index:06d}'; package_path=f'items/{key}'; compute fingerprints."""

def export_research_batch(request: ResearchBatchRequest) -> ResearchBatchResult:
    """build_batch_manifest -> export_batch_package -> post-build archive SHA256."""
```

`research/v1_artifact_exporter/cli.py`:

```python
def main(argv: list[str] | None = None) -> int
```

CLI flags (exact):

```text
--dataset-dir PATH            SpaceNet root (contains test/) or the test/ split dir
--label-space PATH            label-space JSON (e.g. label_spaces/spacenet_14.json)
--predictions PATH            predictions JSONL (DETECTION_KEYS contract)
--pipeline-id STR
--pipeline-name STR
--pipeline-version STR
--executor STR
[--device STR] [--environment STR]
--output PATH                 output BAPv1 .zip
[--batch-id STR]              default: <pipeline-id>-<dataset>-<split>-<predictions_sha[:12]>
[--code-commit STR]
[--config PATH]               hashed into config_sha256
[--artifact NAME=PATH ...]    hashed into artifact_sha256
[--exporter-version STR]      default: research_batch_exporter_v1
[--expected-predictions-sha256 STR]
[--expected-artifact-sha256 NAME=SHA256 ...]
[--dataset-name STR]          default: SpaceNet
[--dataset-split STR]         default: test
```

Summary JSON printed to stdout (exact keys):

```json
{
  "dataset": "...", "split": "...", "label_space": "...",
  "pipeline_id": "...", "pipeline_version": "...",
  "expected_samples": 0, "exported_items": 0,
  "source_prediction_rows": 0, "zero_detection_items": 0,
  "unexpected_sample_ids": 0, "missing_dataset_samples": 0, "fingerprint_failures": 0,
  "recording_manifest_hash": "...", "batch_import_fingerprint": "...",
  "archive_sha256": "...", "output_path": "..."
}
```

The exporter MUST NOT: load a model, import `torch`/`ultralytics`, initialize CUDA, call WISA/Windows APIs, use `RemoteProfile`, use execution certificates, or write `platform.db`.

---

## Task 1 — Recon lock (read-only)

- [ ] Confirm `research/m9_legacy_bridge/batch_exporter.py::export_batch_package(output_path, manifest, items)` exists and writes `batch_manifest.json` + `items/<key>/{manifest,detections}.json`.
- [ ] Confirm `app.imported_runs.batch_schema.BatchManifest` has `schema_version Literal[1]` and **no** `archive_sha256`.
- [ ] Confirm `app.imported_runs.fingerprint.build_batch_import_fingerprint` excludes `transport_provenance` and `archive_sha256`.
- [ ] Confirm `SpaceNetAdapter.load` does not read `.bin` bytes.
- Command: `PYTHONPATH="$PWD/backend" $WISA_PY -m pytest backend/tests/test_m9_legacy_bridge_batch_exporter.py -q` (expect existing pass).
- Done when the four confirmations are recorded in the plan PR description.

## Task 2 — RED (adapter tests fail first)

- [ ] Create `backend/tests/test_v1_research_batch_exporter.py` with the tests listed in Task 2.1.
- [ ] Run RED (expect import/attribute failures for `research.v1_artifact_exporter`):

```bash
WISA_PY=/root/autodl-tmp/WISA-backend-v1-plan-b-local-gpu/.venv/bin/python
PYTHONPATH="$PWD/backend:$PWD/scripts" "$WISA_PY" -m pytest backend/tests/test_v1_research_batch_exporter.py -q
```

- [ ] Record exact failing test names and the intended reason (missing `research.v1_artifact_exporter`).

### Task 2.1 — Tests to add (exact)

1. `test_two_recording_batch_exports_valid_package` — build samples for `("0","1")` where one has detections and the other has zero; `export_research_batch`; assert ZIP member list equals `["batch_manifest.json","items/000000/manifest.json","items/000000/detections.json","items/000001/manifest.json","items/000001/detections.json"]`.
2. `test_one_sample_with_detections_and_one_zero_detection` — assert `{"detections": []}` for the empty child and `detection_count` equals the non-empty count.
3. `test_deterministic_semantic_content` — exporting twice with identical `ResearchBatchRequest` yields identical ZIP bytes; changing only `transport_provenance.export_timestamp` keeps `import_fingerprint` identical.
4. `test_exact_recording_fingerprints` — assert each item's `recording.fingerprint.sha256` equals `build_recording_fingerprint(...)` computed independently from the same `ManifestRecording`.
5. `test_package_validates_through_existing_importer` — seed a local DB with the two Recordings + GT (`benchmark_fixture.add_recording`/`add_ground_truth`), then call `app.imported_runs.batch_validation.validate_batch(extracted_root, session, LabelSpaceService(...))` on the extracted ZIP; assert it resolves both items and computes the same `import_fingerprint`.
6. `test_unexpected_sample_id_blocks_export` — predictions contain `"9"` not in `sample_ids`; expect `UnexpectedSampleError`.
7. `test_missing_dataset_sample_blocks_export` — `sample_ids` includes a stem absent from the dataset dir; expect `MissingDatasetSampleError`.
8. `test_expected_hash_mismatch_blocks_export` — wrong `--expected-predictions-sha256` (CLI test) returns exit 1 and writes no ZIP.
9. `test_no_full_iq_content_hashing` — monkeypatch `pathlib.Path.read_bytes` to raise for paths ending `.bin`; export still succeeds.
10. `test_no_torch_or_ultralytics_import` — after importing `research.v1_artifact_exporter.exporter`, assert `importlib.util.find_spec("torch") is None` and `...("ultralytics") is None` in the ML-free interpreter.

## Task 3 — Implement predictions + sources

- [ ] Add `research/v1_artifact_exporter/__init__.py` exporting `ResearchSample`, `ResearchBatchRequest`, `ResearchBatchResult`, `export_research_batch`, `build_research_samples`, `load_predictions_jsonl`, `adapt_detections`.
- [ ] Implement `predictions.py` using `LegacyDetection` + `LegacyDetectionAdapter`.
- [ ] Implement `sources.py` using `SpaceNetAdapter`, `ManifestGroundTruth`, `ManifestRecording`, `build_recording_fingerprint`.
- [ ] Run Task 2 tests 1–4, 9, 10 → GREEN.

## Task 4 — Implement exporter + manifest builder

- [ ] Implement `export_research_batch` delegating to the existing `export_batch_package`; compute `archive_sha256 = sha256(zip_bytes)` **after** the ZIP exists.
- [ ] Reuse `build_recording_manifest` when `recording_manifest_hash` is requested; otherwise leave `None`.
- [ ] Run Task 2 tests 1–5, 9, 10 → GREEN.

## Task 5 — Implement CLI + hash gates

- [ ] Implement `cli.py` with the exact flags above; reuse `_sha256` hashing (full file bytes of predictions/config/artifacts only — never dataset `.bin`).
- [ ] `--expected-*` mismatches print `error: <name> SHA256 mismatch` to stderr and return `1`; no partial ZIP is written.
- [ ] Run test 8 → GREEN.

## Task 6 — Focused GREEN + regression

```bash
WISA_PY=/root/autodl-tmp/WISA-backend-v1-plan-b-local-gpu/.venv/bin/python
PYTHONPATH="$PWD/backend:$PWD/scripts" "$WISA_PY" -m pytest \
  backend/tests/test_v1_research_batch_exporter.py \
  backend/tests/test_m9_legacy_bridge_batch_exporter.py \
  backend/tests/test_m9_legacy_bridge_batch_cli.py \
  backend/tests/test_batch_schema.py \
  backend/tests/test_batch_validation.py \
  backend/tests/test_batch_import_service.py \
  backend/tests/test_batch_import_api.py \
  -q
```

- [ ] Require `0 failed, 0 errors`.
- [ ] Confirm `torch`/`ultralytics` remain `None` before and after.

## Task 7 — Full backend regression

```bash
WISA_PY=/root/autodl-tmp/WISA-backend-v1-plan-b-local-gpu/.venv/bin/python
PYTHONPATH="$PWD/backend:$PWD/scripts" "$WISA_PY" -m pytest backend/tests -q
```

- [ ] Require `0 failed, 0 errors`; record pass/skip counts.

## Task 8 — Commit

- [ ] Commit A: `feat(research): add V1 research batch exporter` (production adapter + CLI).
- [ ] Commit B: `test(research): cover V1 research batch exporter` (tests) — or combine if the repo prefers; keep each commit reviewable.
- [ ] No changes outside `research/v1_artifact_exporter/` and `backend/tests/test_v1_research_batch_exporter.py`.
- [ ] `git diff --check` clean.

## Cross-Plan Contract (must match Plans 2 and 3)

- Format: BAPv1 only; members `batch_manifest.json`, `items/<key>/manifest.json`, `items/<key>/detections.json`.
- External `archive_sha256` computed post-ZIP; not embedded; excluded from `batch_import_fingerprint_v1`.
- Recording identity: `recording_fingerprint_v1` from dataset+split+label_space+Recording metadata+GT; no raw-IQ full-content hashing.
- Import seam consumed by Windows: `POST /api/imported-runs/batch`; inconsistent prior state → `BATCH_IMPORT_STATE_INCONSISTENT`.
- No `platform.db`, no `remote_gpu`, no `RemoteProfile`, no certificates, no CUDA.
