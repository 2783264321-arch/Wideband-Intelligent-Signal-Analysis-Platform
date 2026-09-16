"""Operator CLI: already-computed detections -> Batch Analysis Package v1.

No inference: this reads frozen predictions, validates them against the local
SpaceNet dataset identity, and writes a deterministic BAPv1 ZIP. It never loads
a model, imports torch/ultralytics, uses CUDA, contacts a remote host, or writes
a WISA database.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys

from app.imported_runs.batch_schema import ResultProvenance, TransportProvenance
from app.imported_runs.schema import ExecutionMetadata, PipelineMetadata
from research.m9_legacy_bridge.adapter import load_label_space
from research.v1_artifact_exporter.exporter import ResearchBatchRequest, export_research_batch
from research.v1_artifact_exporter.predictions import load_predictions_jsonl
from research.v1_artifact_exporter.sources import (
    build_research_samples,
    list_split_sample_ids,
    resolve_split_dir,
)

DEFAULT_EXPORTER_VERSION = "research_batch_exporter_v1"
DEFAULT_DATASET_NAME = "SpaceNet"
DEFAULT_DATASET_SPLIT = "test"


class CliError(Exception):
    """A CLI-level validation failure."""


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with Path(path).open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _parse_name_path(value: str) -> tuple[str, Path]:
    name, separator, raw_path = value.partition("=")
    if not separator or not name or not raw_path:
        raise CliError(f"--artifact must be NAME=PATH, got {value!r}")
    return name, Path(raw_path)


def _parse_name_sha(value: str) -> tuple[str, str]:
    name, separator, raw_sha = value.partition("=")
    if not separator or not name or not raw_sha:
        raise CliError(f"--expected-artifact-sha256 must be NAME=SHA256, got {value!r}")
    return name, raw_sha.lower()


def _gate(name: str, actual: str, expected: str | None) -> None:
    if expected is not None and expected.lower() != actual:
        raise CliError(f"{name} SHA256 mismatch: expected {expected}, got {actual}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m research.v1_artifact_exporter.cli")
    parser.add_argument("--dataset-dir", required=True)
    parser.add_argument("--label-space", required=True)
    parser.add_argument("--predictions", required=True)
    parser.add_argument(
        "--sample-id",
        action="append",
        default=None,
        dest="sample_ids",
        help="Export exactly this sample id (repeatable). Omit to export the full split.",
    )
    parser.add_argument("--pipeline-id", required=True)
    parser.add_argument("--pipeline-name", required=True)
    parser.add_argument("--pipeline-version", required=True)
    parser.add_argument("--executor", required=True)
    parser.add_argument("--device", default=None)
    parser.add_argument("--environment", default=None)
    parser.add_argument("--output", required=True)
    parser.add_argument("--batch-id", default=None)
    parser.add_argument("--code-commit", default=None)
    parser.add_argument("--config", default=None)
    parser.add_argument("--artifact", action="append", default=[])
    parser.add_argument("--exporter-version", default=DEFAULT_EXPORTER_VERSION)
    parser.add_argument("--expected-predictions-sha256", default=None)
    parser.add_argument("--expected-artifact-sha256", action="append", default=[])
    parser.add_argument("--dataset-name", default=DEFAULT_DATASET_NAME)
    parser.add_argument("--dataset-split", default=DEFAULT_DATASET_SPLIT)
    args = parser.parse_args(argv)

    try:
        dataset_dir = Path(args.dataset_dir)
        label_space_path = Path(args.label_space)
        predictions_path = Path(args.predictions)
        output_path = Path(args.output)

        predictions_sha = _sha256_file(predictions_path)
        _gate("predictions", predictions_sha, args.expected_predictions_sha256)

        expected_artifacts = dict(_parse_name_sha(value) for value in args.expected_artifact_sha256)
        artifact_sha256: dict[str, str] = {}
        for value in args.artifact:
            name, path = _parse_name_path(value)
            artifact_sha256[name] = _sha256_file(path)

        config_sha = None
        if args.config is not None:
            config_sha = _sha256_file(Path(args.config))

        for name, expected in expected_artifacts.items():
            if name not in artifact_sha256:
                raise CliError(f"expected artifact {name!r} was not supplied via --artifact")
            _gate(name, artifact_sha256[name], expected)

        _, split, _ = resolve_split_dir(dataset_dir, args.dataset_split)
        discovered = list_split_sample_ids(dataset_dir, args.dataset_split)
        requested = args.sample_ids
        if requested:
            if len(requested) != len(set(requested)):
                raise CliError("--sample-id values must be unique")
            unknown = sorted({value for value in requested if value not in set(discovered)})
            if unknown:
                raise CliError(f"--sample-id values not present in dataset split: {unknown}")
            sample_ids = list(requested)
        else:
            sample_ids = discovered
        predictions_by_sample = load_predictions_jsonl(predictions_path)
        label_classes = load_label_space(label_space_path)
        samples = build_research_samples(
            dataset_dir=dataset_dir,
            label_space_root=label_space_path.parent,
            label_space_id=label_space_path.stem,
            label_classes=label_classes,
            sample_ids=sample_ids,
            predictions_by_sample=predictions_by_sample,
            dataset_name=args.dataset_name,
            dataset_split=split,
        )

        batch_id = args.batch_id or (
            f"{args.pipeline_id}-{args.dataset_name}-{split}-{predictions_sha[:12]}"
        )
        request = ResearchBatchRequest(
            dataset_name=args.dataset_name,
            dataset_split=split,
            label_space=label_space_path.stem,
            pipeline=PipelineMetadata(
                id=args.pipeline_id, name=args.pipeline_name, version=args.pipeline_version
            ),
            execution=ExecutionMetadata(
                executor=args.executor, device=args.device, environment=args.environment
            ),
            result_provenance=ResultProvenance(
                code_commit=args.code_commit,
                config_sha256=config_sha,
                split_manifest_sha256=None,
                source_predictions_sha256=predictions_sha,
                artifact_sha256=artifact_sha256,
            ),
            transport_provenance=TransportProvenance(
                exporter_version=args.exporter_version,
                platform_repo_commit=None,
                export_timestamp=datetime.now(timezone.utc).isoformat(),
            ),
            batch_id=batch_id,
            samples=samples,
            output_path=output_path,
        )
        result = export_research_batch(request)
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    summary = {
        "dataset": args.dataset_name,
        "split": request.dataset_split,
        "label_space": request.label_space,
        "pipeline_id": args.pipeline_id,
        "pipeline_version": args.pipeline_version,
        "expected_samples": len(sample_ids),
        "exported_items": result.item_count,
        "source_prediction_rows": sum(len(rows) for rows in predictions_by_sample.values()),
        "zero_detection_items": result.zero_detection_items,
        "unexpected_sample_ids": 0,
        "missing_dataset_samples": 0,
        "fingerprint_failures": 0,
        "recording_manifest_hash": result.recording_manifest_hash,
        "batch_import_fingerprint": result.import_fingerprint,
        "archive_sha256": result.archive_sha256,
        "output_path": str(result.output_path),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
