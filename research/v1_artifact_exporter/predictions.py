"""Prediction loading + validation for the V1 research batch exporter.

Predictions are *already computed* detection rows in the frozen JSONL contract
(``sample_id, t0_s, t1_s, f0_hz, f1_hz, class_id, score``). No inference occurs
here; the existing ``LegacyDetectionAdapter`` performs identity/coordinate/class
validation and conversion to the platform ``PackageDetection`` shape.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.imported_runs.schema import PackageDetection
from research.m9_legacy_bridge.adapter import LegacyDetectionAdapter
from research.m9_legacy_bridge.schema import RecordingContext

DETECTION_KEYS = ("sample_id", "t0_s", "t1_s", "f0_hz", "f1_hz", "class_id", "score")


class PredictionError(ValueError):
    """A predictions JSONL row is missing or malformed."""


def load_predictions_jsonl(path: Path) -> dict[str, list[dict]]:
    """Group JSONL prediction rows by ``sample_id``.

    Blank lines are skipped; every non-blank line must be a JSON object with a
    non-empty string ``sample_id``. Malformed input fails closed.
    """
    grouped: dict[str, list[dict]] = {}
    source = Path(path)
    try:
        handle = source.open("r", encoding="utf-8")
    except OSError as exc:
        raise PredictionError(f"predictions file could not be read: {source}") from exc
    with handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PredictionError(f"predictions line {line_number} is not valid JSON") from exc
            if not isinstance(row, dict):
                raise PredictionError(f"predictions line {line_number} must be a JSON object")
            sample_id = row.get("sample_id")
            if not isinstance(sample_id, str) or not sample_id.strip():
                raise PredictionError(f"predictions line {line_number} has no usable sample_id")
            for key in DETECTION_KEYS:
                if key not in row:
                    raise PredictionError(
                        f"predictions line {line_number} is missing required key {key!r}"
                    )
            grouped.setdefault(sample_id, []).append(row)
    return grouped


def adapt_detections(
    records: Sequence[Mapping[str, Any]],
    *,
    recording: RecordingContext,
    label_space: Mapping[int, str],
) -> tuple[PackageDetection, ...]:
    """Validate/convert already-computed detection rows into ``PackageDetection``."""
    adapter = LegacyDetectionAdapter(recording=recording, label_space=label_space)
    return tuple(
        PackageDetection.model_validate(detection.to_package_dict())
        for detection in adapter.adapt_many([dict(record) for record in records])
    )
