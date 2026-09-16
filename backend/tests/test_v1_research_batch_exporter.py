"""Plan 1: V1 research batch exporter (no-GPU, BAPv1 reuse).

GPU REQUIRED: NO. No model, no CUDA, no torch/ultralytics, no SSH, no DB sync.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import zipfile

import numpy as np
import pytest

from app.benchmarks.manifest import ManifestGroundTruth, ManifestRecording
from app.imported_runs.batch_schema import BatchManifest
from app.imported_runs.fingerprint import build_recording_fingerprint
from app.imported_runs.schema import Manifest, PackageDetection
from app.labels.service import LabelSpaceService

REPO_ROOT = Path(__file__).resolve().parents[2]
LABEL_SPACE_ROOT = REPO_ROOT / "label_spaces"
LABEL_SPACE_PATH = LABEL_SPACE_ROOT / "spacenet_14.json"


# ---------------------------------------------------------------------------
# Tiny deterministic SpaceNet fixture
# ---------------------------------------------------------------------------
def _write_sample(split_dir: Path, name: str, *, num_samples: int = 6000) -> None:
    interleaved = np.zeros(num_samples * 2, dtype="<f2")
    interleaved.tofile(split_dir / f"{name}.bin")
    (split_dir / f"{name}.json").write_text(
        json.dumps({
            "observation_range": [2401.0, 2431.0],
            "signals": [{
                "signal_id": 0, "start_frequency": 2417.0, "end_frequency": 2417.1,
                "start_time": 0.02, "end_time": 0.1, "class": 9,
            }],
        }),
        encoding="utf-8",
    )


def _dataset(tmp_path: Path, names=("0", "1")) -> Path:
    split = tmp_path / "advanced" / "test"
    split.mkdir(parents=True, exist_ok=True)
    for name in names:
        _write_sample(split, name)
    return split


def _predictions(tmp_path: Path, rows) -> Path:
    path = tmp_path / "preds.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + ("\n" if rows else ""), encoding="utf-8")
    return path


def _det_row(sample_id, *, t0=2e-5, t1=1e-4, f0=2417.0e6, f1=2417.1e6, cls=9, score=0.9):
    return {"sample_id": sample_id, "t0_s": t0, "t1_s": t1,
            "f0_hz": f0, "f1_hz": f1, "class_id": cls, "score": score}


def _samples(dataset_dir: Path, predictions_path: Path, *, sample_ids=("0", "1")):
    from research.v1_artifact_exporter.predictions import load_predictions_jsonl
    from research.v1_artifact_exporter.sources import build_research_samples
    from research.m9_legacy_bridge.adapter import load_label_space

    return build_research_samples(
        dataset_dir=dataset_dir,
        label_space_root=LABEL_SPACE_ROOT,
        label_space_id="spacenet_14",
        label_classes=load_label_space(LABEL_SPACE_PATH),
        sample_ids=list(sample_ids),
        predictions_by_sample=load_predictions_jsonl(predictions_path),
        dataset_name="SpaceNet",
        dataset_split="test",
    )


def _request(tmp_path: Path, *, samples, output: Path, transport_timestamp="2026-09-16T00:00:00Z"):
    from app.imported_runs.batch_schema import (
        DatasetMetadata, ExecutionMetadata, ResultProvenance, TransportProvenance,
    )
    from app.imported_runs.schema import PipelineMetadata
    from research.v1_artifact_exporter.exporter import ResearchBatchRequest

    return ResearchBatchRequest(
        dataset_name="SpaceNet",
        dataset_split="test",
        label_space="spacenet_14",
        pipeline=PipelineMetadata(id="pipeline_x", name="Pipeline X", version="1.0"),
        execution=ExecutionMetadata(executor="research_gpu", device="cuda:0", environment="test"),
        result_provenance=ResultProvenance(),
        transport_provenance=TransportProvenance(
            exporter_version="research_batch_exporter_v1",
            platform_repo_commit=None,
            export_timestamp=transport_timestamp,
        ),
        batch_id="pipeline_x-SpaceNet-test-000000000000",
        samples=samples,
        output_path=output,
    )


# ---------------------------------------------------------------------------
# 1-2. Valid package + zero-detection child
# ---------------------------------------------------------------------------
def test_two_recording_batch_exports_valid_package(tmp_path: Path) -> None:
    from research.v1_artifact_exporter.exporter import export_research_batch

    split = _dataset(tmp_path)
    preds = _predictions(tmp_path, [_det_row("0"), _det_row("0", t0=3e-5, t1=9e-5, f0=2417.2e6, f1=2417.3e6)])
    samples = _samples(split, preds)
    result = export_research_batch(_request(tmp_path, samples=samples, output=tmp_path / "batch.zip"))

    assert result.output_path.is_file()
    with zipfile.ZipFile(result.output_path) as archive:
        assert archive.namelist() == [
            "batch_manifest.json",
            "items/000000/manifest.json",
            "items/000000/detections.json",
            "items/000001/manifest.json",
            "items/000001/detections.json",
        ]
        BatchManifest.model_validate(json.loads(archive.read("batch_manifest.json").decode("utf-8")))


def test_zero_detection_sample_exports_empty_child(tmp_path: Path) -> None:
    from research.v1_artifact_exporter.exporter import export_research_batch

    split = _dataset(tmp_path)
    preds = _predictions(tmp_path, [_det_row("0")])
    samples = _samples(split, preds)
    result = export_research_batch(_request(tmp_path, samples=samples, output=tmp_path / "batch.zip"))

    assert result.item_count == 2
    assert result.detection_count == 1
    assert result.zero_detection_items == 1
    with zipfile.ZipFile(result.output_path) as archive:
        empty = json.loads(archive.read("items/000001/detections.json").decode("utf-8"))
        assert empty == {"detections": []}


# ---------------------------------------------------------------------------
# 3. Deterministic bytes + transport-independent semantic fingerprint
# ---------------------------------------------------------------------------
def test_deterministic_semantic_content(tmp_path: Path) -> None:
    from research.v1_artifact_exporter.exporter import build_batch_manifest, export_research_batch
    from app.imported_runs.fingerprint import build_batch_import_fingerprint

    split = _dataset(tmp_path)
    preds = _predictions(tmp_path, [_det_row("0")])
    samples = _samples(split, preds)

    a = export_research_batch(_request(tmp_path, samples=samples, output=tmp_path / "a.zip"))
    b = export_research_batch(_request(tmp_path, samples=samples, output=tmp_path / "b.zip"))
    assert a.output_path.read_bytes() == b.output_path.read_bytes()

    m1, e1, c1 = build_batch_manifest(_request(tmp_path, samples=samples, output=tmp_path / "x.zip", transport_timestamp="2026-01-01T00:00:00Z"))
    m2, e2, c2 = build_batch_manifest(_request(tmp_path, samples=samples, output=tmp_path / "y.zip", transport_timestamp="2026-02-02T00:00:00Z"))
    assert build_batch_import_fingerprint(m1, c1) == build_batch_import_fingerprint(m2, c2)


# ---------------------------------------------------------------------------
# 4. Exact recording fingerprints
# ---------------------------------------------------------------------------
def test_exact_recording_fingerprints(tmp_path: Path) -> None:
    from research.v1_artifact_exporter.exporter import build_batch_manifest

    split = _dataset(tmp_path)
    preds = _predictions(tmp_path, [_det_row("0")])
    samples = _samples(split, preds)
    manifest, _, _ = build_batch_manifest(_request(tmp_path, samples=samples, output=tmp_path / "x.zip"))

    for item, sample in zip(manifest.items, samples):
        expected = build_recording_fingerprint(
            "SpaceNet", "test", "spacenet_14", sample.manifest_recording
        )
        assert item.recording.fingerprint.sha256 == expected.sha256
        assert item.recording.fingerprint.ground_truth_sha256 == expected.ground_truth_sha256


# ---------------------------------------------------------------------------
# 5. Validates through the EXISTING importer
# ---------------------------------------------------------------------------
def test_package_validates_through_existing_importer(tmp_path: Path, session) -> None:
    from research.v1_artifact_exporter.exporter import export_research_batch
    from app.imported_runs.batch_validation import validate_batch
    from app.recordings.model import RecordingModel
    from app.ground_truth.model import GroundTruthModel

    split = _dataset(tmp_path)
    preds = _predictions(tmp_path, [_det_row("0"), _det_row("1")])
    samples = _samples(split, preds)
    result = export_research_batch(_request(tmp_path, samples=samples, output=tmp_path / "batch.zip"))

    for sample in samples:
        mr = sample.manifest_recording
        session.add(RecordingModel(
            id=f"rec_{mr.name}", name=mr.name, data_path=f"x/{mr.name}.bin",
            data_format=mr.data_format, sample_rate_hz=mr.sample_rate_hz,
            center_frequency_hz=mr.center_frequency_hz, frequency_low_hz=mr.frequency_low_hz,
            frequency_high_hz=mr.frequency_high_hz, num_samples=mr.num_samples,
            duration_s=mr.duration_s, dataset_name="SpaceNet", dataset_split="test",
            label_space="spacenet_14", has_ground_truth=bool(mr.ground_truth),
        ))
        for index, gt in enumerate(mr.ground_truth):
            session.add(GroundTruthModel(
                id=f"gt_{mr.name}_{index}", recording_id=f"rec_{mr.name}",
                t_start_s=gt.t_start_s, t_end_s=gt.t_end_s, f_low_hz=gt.f_low_hz,
                f_high_hz=gt.f_high_hz, class_id=gt.class_id, class_name=gt.class_name,
            ))
    session.commit()

    root = tmp_path / "extracted"
    with zipfile.ZipFile(result.output_path) as archive:
        archive.extractall(root)
    labels = LabelSpaceService(LABEL_SPACE_ROOT)
    validated = validate_batch(root, session, labels)
    assert len(validated.items) == 2
    assert validated.import_fingerprint == result.import_fingerprint
    assert validated.total_detections == 2


# ---------------------------------------------------------------------------
# 6-7. Negative export blocks
# ---------------------------------------------------------------------------
def test_unexpected_sample_id_blocks_export(tmp_path: Path) -> None:
    from research.v1_artifact_exporter.sources import UnexpectedSampleError

    split = _dataset(tmp_path)
    preds = _predictions(tmp_path, [_det_row("9")])
    with pytest.raises(UnexpectedSampleError):
        _samples(split, preds)


def test_missing_dataset_sample_blocks_export(tmp_path: Path) -> None:
    from research.v1_artifact_exporter.sources import MissingDatasetSampleError

    split = _dataset(tmp_path)
    preds = _predictions(tmp_path, [_det_row("0")])
    with pytest.raises(MissingDatasetSampleError):
        _samples(split, preds, sample_ids=("0", "77"))


# ---------------------------------------------------------------------------
# 8. CLI hash gate
# ---------------------------------------------------------------------------
def test_expected_hash_mismatch_blocks_export(tmp_path: Path, capsys) -> None:
    from research.v1_artifact_exporter.cli import main

    split = _dataset(tmp_path)
    preds = _predictions(tmp_path, [_det_row("0")])
    output = tmp_path / "out.zip"
    code = main([
        "--dataset-dir", str(split), "--label-space", str(LABEL_SPACE_PATH),
        "--predictions", str(preds), "--pipeline-id", "pipeline_x",
        "--pipeline-name", "Pipeline X", "--pipeline-version", "1.0",
        "--executor", "research_gpu", "--output", str(output),
        "--expected-predictions-sha256", "0" * 64,
    ])
    assert code == 1
    assert not output.exists()
    assert "SHA256 mismatch" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# 8b. CLI bounded subset (--sample-id)
# ---------------------------------------------------------------------------
def _cli_base(split: Path, preds: Path, output: Path) -> list[str]:
    return [
        "--dataset-dir", str(split), "--label-space", str(LABEL_SPACE_PATH),
        "--predictions", str(preds), "--pipeline-id", "pipeline_x",
        "--pipeline-name", "Pipeline X", "--pipeline-version", "1.0",
        "--executor", "research_gpu", "--output", str(output),
    ]


def _batch_names(output: Path) -> list[str]:
    with zipfile.ZipFile(output) as archive:
        manifest = json.loads(archive.read("batch_manifest.json").decode("utf-8"))
    assert manifest["expected_items"] == len(manifest["items"])
    return sorted(item["recording"]["name"] for item in manifest["items"])


def test_cli_subset_exports_exactly_selected_items(tmp_path: Path) -> None:
    from research.v1_artifact_exporter.cli import main

    split = _dataset(tmp_path, names=("0", "1", "2"))
    preds = _predictions(tmp_path, [_det_row("0"), _det_row("1")])
    output = tmp_path / "subset.zip"
    code = main(_cli_base(split, preds, output) + ["--sample-id", "0", "--sample-id", "1"])
    assert code == 0
    assert _batch_names(output) == ["0", "1"]
    assert "2" not in _batch_names(output)


def test_cli_subset_no_sample_id_exports_full_split(tmp_path: Path) -> None:
    from research.v1_artifact_exporter.cli import main

    split = _dataset(tmp_path, names=("0", "1", "2"))
    preds = _predictions(tmp_path, [_det_row("0")])
    output = tmp_path / "full.zip"
    assert main(_cli_base(split, preds, output)) == 0
    assert _batch_names(output) == ["0", "1", "2"]


def test_cli_subset_prediction_outside_selection_fails(tmp_path: Path, capsys) -> None:
    from research.v1_artifact_exporter.cli import main

    split = _dataset(tmp_path, names=("0", "1", "2"))
    preds = _predictions(tmp_path, [_det_row("2")])
    output = tmp_path / "subset.zip"
    code = main(_cli_base(split, preds, output) + ["--sample-id", "0", "--sample-id", "1"])
    assert code == 1
    assert not output.exists()
    assert "UnexpectedSampleError" in capsys.readouterr().err


def test_cli_subset_nonexistent_stem_fails(tmp_path: Path, capsys) -> None:
    from research.v1_artifact_exporter.cli import main

    split = _dataset(tmp_path, names=("0", "1", "2"))
    preds = _predictions(tmp_path, [_det_row("0")])
    output = tmp_path / "subset.zip"
    code = main(_cli_base(split, preds, output) + ["--sample-id", "0", "--sample-id", "99"])
    assert code == 1
    assert not output.exists()
    assert "not present in dataset split" in capsys.readouterr().err


def test_cli_subset_duplicate_sample_id_fails(tmp_path: Path, capsys) -> None:
    from research.v1_artifact_exporter.cli import main

    split = _dataset(tmp_path, names=("0", "1", "2"))
    preds = _predictions(tmp_path, [_det_row("0")])
    output = tmp_path / "subset.zip"
    code = main(_cli_base(split, preds, output) + ["--sample-id", "0", "--sample-id", "0"])
    assert code == 1
    assert not output.exists()
    assert "must be unique" in capsys.readouterr().err


def test_cli_subset_selected_sample_without_predictions_is_zero_detection(tmp_path: Path) -> None:
    from research.v1_artifact_exporter.cli import main

    split = _dataset(tmp_path, names=("0", "1", "2"))
    preds = _predictions(tmp_path, [_det_row("0")])
    output = tmp_path / "subset.zip"
    assert main(_cli_base(split, preds, output) + ["--sample-id", "0", "--sample-id", "1"]) == 0
    with zipfile.ZipFile(output) as archive:
        empty = json.loads(archive.read("items/000001/detections.json").decode("utf-8"))
    assert empty == {"detections": []}


# ---------------------------------------------------------------------------
# 9. No full raw-IQ content hashing
# ---------------------------------------------------------------------------
def test_no_full_iq_content_hashing(tmp_path: Path, monkeypatch) -> None:
    from research.v1_artifact_exporter.exporter import export_research_batch

    split = _dataset(tmp_path)
    preds = _predictions(tmp_path, [_det_row("0")])
    samples = _samples(split, preds)

    real_read_bytes = Path.read_bytes

    def guarded(self: Path):
        if self.suffix == ".bin":
            raise AssertionError("exporter must not read raw IQ bytes")
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", guarded)
    export_research_batch(_request(tmp_path, samples=samples, output=tmp_path / "batch.zip"))


# ---------------------------------------------------------------------------
# 10. No torch / ultralytics
# ---------------------------------------------------------------------------
def test_no_torch_or_ultralytics_import() -> None:
    import research.v1_artifact_exporter.exporter  # noqa: F401
    import research.v1_artifact_exporter.predictions  # noqa: F401
    import research.v1_artifact_exporter.sources  # noqa: F401

    assert importlib.util.find_spec("torch") is None
    assert importlib.util.find_spec("ultralytics") is None
