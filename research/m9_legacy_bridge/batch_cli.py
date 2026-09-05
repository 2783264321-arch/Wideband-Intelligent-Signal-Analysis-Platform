"""Historical SpaceNet JSONL -> deterministic Batch Analysis Package v1 CLI.

This CLI never runs inference, STFT/LS-STFT, AHLP, NMS, or training. It reads
the frozen prediction JSONL, reuses LegacyDetectionAdapter for conversion, and
writes a deterministic batch archive.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

from app.benchmarks.manifest import (
    ManifestGroundTruth,
    ManifestRecording,
    build_recording_manifest,
)
from app.datasets.spacenet import SpaceNetAdapter
from app.imported_runs.batch_archive import MAX_BATCH_ITEMS
from app.imported_runs.batch_schema import (
    BatchImportSummary,
    BatchItem,
    BatchItemRecording,
    BatchManifest,
    DatasetMetadata,
    ExecutionMetadata,
    HistoricalReference,
    PipelineMetadata,
    RecordingFingerprintWire,
    ResultProvenance,
    TransportProvenance,
)
from app.imported_runs.fingerprint import (
    CanonicalBatchItem,
    build_batch_import_fingerprint,
    build_recording_fingerprint,
)
from app.imported_runs.schema import Manifest, PackageDetection
from research.m9_legacy_bridge.adapter import LegacyDetectionAdapter, load_label_space
from research.m9_legacy_bridge.batch_exporter import BatchExportItem, export_batch_package
from research.m9_legacy_bridge.schema import RecordingContext

PIPELINE_ID = "zoomspec_yolo26n_aug_combined_frn_v3"
PIPELINE_NAME = "ZoomSpec YOLOv26n Aug + Combined FRN V3"
PIPELINE_VERSION = "1.0.0"
LABEL_SPACE = "spacenet_14"
DATASET_NAME = "SpaceNet"
DATASET_SPLIT = "test"


class BatchExportError(Exception):
    pass


def _sha256(path: Path) -> str:
    digest = sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _gate(name: str, actual: str, expected: str | None) -> None:
    if expected is not None and expected.lower() != actual:
        raise BatchExportError(f"{name} SHA256 mismatch: expected {expected}, got {actual}")


def _parse_test_ids(test_manifest_path: Path) -> list[str]:
    raw = json.loads(test_manifest_path.read_text(encoding="utf-8"))
    test_ids = raw.get("test_ids")
    if not isinstance(test_ids, list):
        raise BatchExportError("test_manifest.test_ids must be a list")
    ids = []
    for value in test_ids:
        sample_id = str(value)
        if not sample_id or Path(sample_id).name != sample_id:
            raise BatchExportError(f"invalid sample id: {value!r}")
        ids.append(sample_id)
    if len(ids) != len(set(ids)):
        raise BatchExportError("test_ids must be unique")
    return ids


def _load_metrics(metrics_path: Path) -> HistoricalReference:
    raw = json.loads(metrics_path.read_text(encoding="utf-8"))
    required = ("images", "canonical_ground_truth", "predictions", "map_50", "map_50_95")
    if any(key not in raw for key in required):
        raise BatchExportError("historical metrics report is missing required keys")
    return HistoricalReference(
        reference_only=True,
        report_sha256=_sha256(metrics_path),
        images=int(raw["images"]),
        canonical_ground_truth=int(raw["canonical_ground_truth"]),
        predictions=int(raw["predictions"]),
        recorded_map50=float(raw["map_50"]),
        recorded_map50_95=float(raw["map_50_95"]),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="batch_cli")
    parser.add_argument("--predictions", required=True)
    parser.add_argument("--test-manifest", required=True)
    parser.add_argument("--dataset-dir", required=True)
    parser.add_argument("--metrics", required=True)
    parser.add_argument("--detector-checkpoint", required=True)
    parser.add_argument("--frn-checkpoint", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--label-space", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--expected-predictions-sha256")
    parser.add_argument("--expected-test-manifest-sha256")
    parser.add_argument("--expected-detector-sha256")
    parser.add_argument("--expected-frn-sha256")
    parser.add_argument("--expected-config-sha256")
    args = parser.parse_args(argv)

    try:
        predictions_path = Path(args.predictions)
        test_manifest_path = Path(args.test_manifest)
        dataset_dir = Path(args.dataset_dir)
        metrics_path = Path(args.metrics)
        detector_path = Path(args.detector_checkpoint)
        frn_path = Path(args.frn_checkpoint)
        config_path = Path(args.config)
        label_space_path = Path(args.label_space)
        output_path = Path(args.output)

        pred_sha = _sha256(predictions_path)
        manifest_sha = _sha256(test_manifest_path)
        detector_sha = _sha256(detector_path)
        frn_sha = _sha256(frn_path)
        config_sha = _sha256(config_path)
        _gate("predictions", pred_sha, args.expected_predictions_sha256)
        _gate("test-manifest", manifest_sha, args.expected_test_manifest_sha256)
        _gate("detector", detector_sha, args.expected_detector_sha256)
        _gate("frn", frn_sha, args.expected_frn_sha256)
        _gate("config", config_sha, args.expected_config_sha256)

        split = dataset_dir.name if dataset_dir.name in ("train", "test") else DATASET_SPLIT
        root = dataset_dir.parent if dataset_dir.name in ("train", "test") else dataset_dir
        adapter = SpaceNetAdapter(root, label_space_path.parent, LABEL_SPACE)
        label_classes = load_label_space(label_space_path)

        test_ids = _parse_test_ids(test_manifest_path)
        if len(test_ids) > MAX_BATCH_ITEMS:
            raise BatchExportError(f"test_ids exceed {MAX_BATCH_ITEMS}")

        # Group frozen predictions by sample_id.
        grouped: dict[str, list[dict]] = {}
        with predictions_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                sample_id = str(row.get("sample_id"))
                if sample_id not in set(test_ids):
                    raise BatchExportError(f"unexpected sample_id {sample_id!r} outside frozen split")
                grouped.setdefault(sample_id, []).append(row)
        source_prediction_rows = sum(len(v) for v in grouped.values())

        # Load every split sample from the dataset directory.
        samples = []
        manifest_recordings = []
        for sample_id in test_ids:
            sample = adapter.load(split, sample_id)
            samples.append(sample)
            ground_truth = tuple(
                ManifestGroundTruth(
                    t_start_s=sig.t_start_s, t_end_s=sig.t_end_s,
                    f_low_hz=sig.f_low_hz, f_high_hz=sig.f_high_hz,
                    class_id=sig.class_id, class_name=sig.class_name,
                )
                for sig in sample.signals
            )
            manifest_recordings.append(ManifestRecording(
                recording_id=sample.id, name=sample.id,
                data_format=sample.data_format,
                sample_rate_hz=sample.sample_rate_hz,
                center_frequency_hz=sample.center_frequency_hz,
                frequency_low_hz=sample.frequency_low_hz,
                frequency_high_hz=sample.frequency_high_hz,
                num_samples=sample.num_samples,
                duration_s=sample.duration_s,
                ground_truth=ground_truth,
            ))

        frozen_manifest = build_recording_manifest(DATASET_NAME, DATASET_SPLIT, LABEL_SPACE, manifest_recordings)
        dataset_manifest_hash = frozen_manifest.sha256

        # Build ordered items (lexical sample-id order, matching M8.6A).
        ordered_samples = sorted(zip(test_ids, samples, manifest_recordings), key=lambda t: t[0])
        items = []
        export_items = []
        canonical_items = []
        zero_detection_items = 0
        for index, (sample_id, sample, manifest_recording) in enumerate(ordered_samples):
            fingerprint = build_recording_fingerprint(
                DATASET_NAME, DATASET_SPLIT, LABEL_SPACE, manifest_recording)
            context = RecordingContext(
                name=sample_id, duration_s=sample.duration_s,
                frequency_low_hz=sample.frequency_low_hz,
                frequency_high_hz=sample.frequency_high_hz,
                dataset="SpaceNet advanced/test",
            )
            legacy_adapter = LegacyDetectionAdapter(recording=context, label_space=label_classes)
            platform_detections = legacy_adapter.adapt_many(grouped.get(sample_id, []))
            package_detections = tuple(
                PackageDetection.model_validate(det.to_package_dict()) for det in platform_detections
            )
            if not package_detections:
                zero_detection_items += 1

            item_key = f"{index:06d}"
            package_path = f"items/{item_key}"
            item = BatchItem(
                key=item_key,
                package_path=package_path,
                recording=BatchItemRecording(
                    name=sample_id,
                    fingerprint=RecordingFingerprintWire(
                        schema="recording_fingerprint_v1",
                        metadata={
                            "data_format": manifest_recording.data_format,
                            "sample_rate_hz": manifest_recording.sample_rate_hz,
                            "center_frequency_hz": manifest_recording.center_frequency_hz,
                            "frequency_low_hz": manifest_recording.frequency_low_hz,
                            "frequency_high_hz": manifest_recording.frequency_high_hz,
                            "num_samples": manifest_recording.num_samples,
                            "duration_s": manifest_recording.duration_s,
                        },
                        ground_truth_sha256=fingerprint.ground_truth_sha256,
                        sha256=fingerprint.sha256,
                    ),
                ),
            )
            child_manifest = Manifest.model_validate({
                "schema_version": 1,
                "pipeline": {"id": PIPELINE_ID, "name": PIPELINE_NAME, "version": PIPELINE_VERSION},
                "label_space": LABEL_SPACE,
                "recording": {"name": sample_id, "dataset": DATASET_NAME},
                "execution": {"executor": "historical_import", "device": "historical_gpu_run",
                              "environment": "frozen external result; no inference rerun"},
                "results": {"detections": "detections.json"},
            })
            items.append(item)
            export_items.append(BatchExportItem(item=item, child_manifest=child_manifest, detections=package_detections))
            canonical_items.append(CanonicalBatchItem(
                key=item_key,
                recording_fingerprint=fingerprint.sha256,
                parameters={},
                detections=package_detections,
            ))

        batch_id = (
            f"{PIPELINE_ID}-{DATASET_NAME}-{DATASET_SPLIT}-{pred_sha[:12]}"
        )
        outer = BatchManifest(
            schema_version=1,
            batch_id=batch_id,
            pipeline=PipelineMetadata(id=PIPELINE_ID, name=PIPELINE_NAME, version=PIPELINE_VERSION),
            label_space=LABEL_SPACE,
            dataset=DatasetMetadata(name=DATASET_NAME, split=DATASET_SPLIT),
            expected_items=len(items),
            execution=ExecutionMetadata(
                executor="historical_import", device="historical_gpu_run",
                environment="frozen external result; no inference rerun"),
            result_provenance=ResultProvenance(
                code_commit=None,
                config_sha256=config_sha,
                split_manifest_sha256=manifest_sha,
                source_predictions_sha256=pred_sha,
                artifact_sha256={"detector_checkpoint": detector_sha, "frn_checkpoint": frn_sha},
            ),
            transport_provenance=TransportProvenance(
                exporter_version="batch_analysis_package_v1",
                platform_repo_commit=None,
                export_timestamp=None,
            ),
            recording_manifest_hash=dataset_manifest_hash,
            historical_reference=_load_metrics(metrics_path),
            items=items,
        )
        import_fingerprint = build_batch_import_fingerprint(outer, tuple(canonical_items))
        export_batch_package(output_path, outer, tuple(export_items))
        archive_sha = _sha256(output_path)

        summary = {
            "dataset": DATASET_NAME,
            "split": DATASET_SPLIT,
            "label_space": LABEL_SPACE,
            "pipeline_id": PIPELINE_ID,
            "pipeline_version": PIPELINE_VERSION,
            "expected_samples": len(test_ids),
            "exported_items": len(items),
            "source_prediction_rows": source_prediction_rows,
            "zero_detection_items": zero_detection_items,
            "unexpected_sample_ids": 0,
            "missing_dataset_samples": 0,
            "fingerprint_failures": 0,
            "recording_manifest_hash": dataset_manifest_hash,
            "batch_import_fingerprint": import_fingerprint,
            "archive_sha256": archive_sha,
            "output_path": str(output_path),
        }
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0
    except BatchExportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())