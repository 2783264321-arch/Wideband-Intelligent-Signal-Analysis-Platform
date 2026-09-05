"""M9.0 Analysis Package v1 exporter.

Builds a ZIP Analysis Package (``manifest.json`` + ``detections.json`` +
``metrics.json``) that conforms to the platform M6 import wire contract
(``backend/app/imported_runs/schema.py``) without modifying it.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any, Iterable

from research.m9_legacy_bridge.schema import (
    HistoricalEvaluation,
    PipelineMetadata,
    PlatformDetection,
    Provenance,
    RecordingContext,
)

DEFAULT_PIPELINE = PipelineMetadata(
    id="zoomspec_yolo26n_aug_combined_frn_v3",
    name="ZoomSpec YOLOv26n Aug + Combined FRN V3",
    version="1.0.0",
)
DEFAULT_LABEL_SPACE = "spacenet_14"
DEFAULT_EXECUTOR = "historical_import"
DEFAULT_DEVICE = "historical_gpu_run"
DEFAULT_ENVIRONMENT = "AutoDL legacy frozen result; no inference rerun in M9.0"

DETECTIONS_FILENAME = "detections.json"
METRICS_FILENAME = "metrics.json"


class ExportError(Exception):
    """A package export failed validation."""


def build_manifest(
    *,
    recording: RecordingContext,
    pipeline: PipelineMetadata = DEFAULT_PIPELINE,
    label_space: str = DEFAULT_LABEL_SPACE,
    executor: str = DEFAULT_EXECUTOR,
    device: str | None = DEFAULT_DEVICE,
    environment: str | None = DEFAULT_ENVIRONMENT,
) -> dict[str, Any]:
    """Build the Analysis Package v1 manifest document."""
    return {
        "schema_version": 1,
        "pipeline": {
            "id": pipeline.id,
            "name": pipeline.name,
            "version": pipeline.version,
        },
        "label_space": label_space,
        "recording": {
            "name": recording.name,
            "dataset": recording.dataset,
        },
        "execution": {
            "executor": executor,
            "device": device,
            "environment": environment,
        },
        "results": {
            "detections": DETECTIONS_FILENAME,
            "metrics": METRICS_FILENAME,
        },
    }


def build_detections(detections: Iterable[PlatformDetection]) -> dict[str, Any]:
    """Build the detections.json document."""
    items = [detection.to_package_dict() for detection in detections]
    return {"detections": items}


def build_metrics(
    *,
    historical: HistoricalEvaluation,
    provenance: Provenance,
) -> dict[str, Any]:
    """Build metrics.json, separating full-corpus historical metrics from provenance.

    Single-sample metrics are intentionally NOT placed here; the historical
    mAP values describe the full 2,500-sample test evaluation.
    """
    return {
        "historical_evaluation": historical.to_dict(),
        "provenance": provenance.to_dict(),
    }


def export_package(
    *,
    output_dir: str | Path,
    recording: RecordingContext,
    detections: list[PlatformDetection],
    historical: HistoricalEvaluation,
    provenance: Provenance,
    pipeline: PipelineMetadata = DEFAULT_PIPELINE,
) -> Path:
    """Write ``<recording.name>.analysis.zip`` into ``output_dir``.

    Returns the ZIP path. The ZIP is written outside the git repo by design.
    """
    if not detections:
        raise ExportError("refusing to export an empty detection package")

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    zip_path = output / f"{recording.name}.analysis.zip"

    manifest = build_manifest(recording=recording, pipeline=pipeline)
    detections_doc = build_detections(detections)
    metrics_doc = build_metrics(historical=historical, provenance=provenance)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        )
        archive.writestr(
            DETECTIONS_FILENAME,
            json.dumps(detections_doc, indent=2, sort_keys=True) + "\n",
        )
        archive.writestr(
            METRICS_FILENAME,
            json.dumps(metrics_doc, indent=2, sort_keys=True) + "\n",
        )

    return zip_path