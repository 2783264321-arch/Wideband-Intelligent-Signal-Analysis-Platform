"""M9.0 bridge CLI: export one real historical test sample as an Analysis Package.

Example:
    python -m research.m9_legacy_bridge.cli \\
        --sample 0 \\
        --predictions /root/autodl-tmp/Claude/reports_claude/test_val_detections_augv3.jsonl \\
        --test-manifest /root/autodl-tmp/Claude/reports_claude/test_manifest.json \\
        --dataset-dir /root/autodl-tmp/SpaceNet_Dataset/advanced/test \\
        --metrics /root/autodl-tmp/Claude/reports_claude/test_full_val_map_augv3.json \\
        --output-dir /root/autodl-tmp/m9_exports

All frozen-asset hashes are taken from the recorded provenance document
(``docs/research/m9_legacy_pipeline_provenance.md``) and passed through here
so the package's metrics.json matches the frozen record exactly.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.datasets.spacenet import SpaceNetAdapter  # noqa: E402

from research.m9_legacy_bridge.adapter import (  # noqa: E402
    LegacyDetectionAdapter,
    load_label_space,
)
from research.m9_legacy_bridge.exporter import export_package  # noqa: E402
from research.m9_legacy_bridge.schema import (  # noqa: E402
    HistoricalEvaluation,
    Provenance,
    RecordingContext,
)

LABEL_SPACE_PATH = REPO_ROOT / "label_spaces" / "spacenet_14.json"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m research.m9_legacy_bridge.cli",
        description="Export one real legacy historical test sample as an Analysis Package v1.",
    )
    parser.add_argument("--sample", required=True, help="SpaceNet advanced/test sample stem, e.g. 0")
    parser.add_argument("--predictions", required=True, type=Path,
                        help="Historical test detections JSONL (frozen file)")
    parser.add_argument("--test-manifest", required=True, type=Path,
                        help="Historical test split manifest JSON")
    parser.add_argument("--dataset-dir", required=True, type=Path,
                        help="SpaceNet advanced/test dataset directory")
    parser.add_argument("--metrics", required=True, type=Path,
                        help="Historical full-test metrics JSON")
    parser.add_argument("--output-dir", required=True, type=Path,
                        help="Output directory for the .analysis.zip (outside the git repo)")
    parser.add_argument("--label-space", type=Path, default=LABEL_SPACE_PATH,
                        help="Platform label-space JSON (default: repo label_spaces/spacenet_14.json)")
    parser.add_argument("--legacy-prediction-sha256", required=True,
                        help="SHA256 of the frozen historical prediction file")
    parser.add_argument("--detector-checkpoint-sha256", required=True)
    parser.add_argument("--frn-checkpoint-sha256", required=True)
    parser.add_argument("--config-sha256", required=True)
    return parser.parse_args(argv)


def _load_recording_context(args: argparse.Namespace, sample: object) -> RecordingContext:
    # sample is a platform SpaceNetSample (backend/app/datasets/spacenet.py)
    return RecordingContext(
        name=sample.id,
        duration_s=float(sample.duration_s),
        frequency_low_hz=float(sample.frequency_low_hz),
        frequency_high_hz=float(sample.frequency_high_hz),
        dataset="SpaceNet advanced/test",
    )


def _load_historical_evaluation(args: argparse.Namespace) -> HistoricalEvaluation:
    report = json.loads(args.metrics.read_text(encoding="utf-8"))
    return HistoricalEvaluation(
        scope="full historical SpaceNet advanced/test evaluation",
        mAP50=float(report.get("map_50")),
        mAP50_95=float(report.get("map_50_95")),
        source_report=str(args.metrics.resolve()),
    )


def _load_provenance(args: argparse.Namespace) -> Provenance:
    return Provenance(
        legacy_prediction_sha256=args.legacy_prediction_sha256,
        detector_checkpoint_sha256=args.detector_checkpoint_sha256,
        frn_checkpoint_sha256=args.frn_checkpoint_sha256,
        config_sha256=args.config_sha256,
        extra={"test_manifest_sha256": args.test_manifest_sha256},
    )


def main(argv: list[str] | None = None) -> int:
    import hashlib

    args = _parse_args(argv)
    args.test_manifest_sha256 = hashlib.sha256(
        args.test_manifest.read_bytes()
    ).hexdigest()

    label_space = load_label_space(args.label_space)
    adapter_root = args.dataset_dir.resolve().parent
    split = args.dataset_dir.resolve().name
    adapter = SpaceNetAdapter(adapter_root, REPO_ROOT / "label_spaces", "spacenet_14")
    sample = adapter.load(split, args.sample)
    recording = _load_recording_context(args, sample)

    rows = [
        json.loads(line)
        for line in args.predictions.open(encoding="utf-8")
        if line.strip()
    ]
    sample_rows = [row for row in rows if str(row.get("sample_id")) == args.sample]
    if not sample_rows:
        print(f"ERROR: no historical predictions for sample '{args.sample}'", file=sys.stderr)
        return 2

    legacy_adapter = LegacyDetectionAdapter(recording=recording, label_space=label_space)
    detections = legacy_adapter.adapt_many(sample_rows)

    zip_path = export_package(
        output_dir=args.output_dir,
        recording=recording,
        detections=detections,
        historical=_load_historical_evaluation(args),
        provenance=_load_provenance(args),
    )

    print(json.dumps({
        "sample": args.sample,
        "gt_signals": len(sample.signals),
        "predictions_used": len(sample_rows),
        "detections_adapted": len(detections),
        "package": str(zip_path),
        "sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())