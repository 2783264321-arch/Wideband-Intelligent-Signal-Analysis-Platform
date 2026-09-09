"""Analysis Package v1 publisher for remote GPU results (Task 12F-B Task 3).

Reuses the existing Analysis Package v1 schema exactly (``Manifest`` /
``PackageDetection``); there is no second result schema. A ``PipelineOutput``
is serialized into a ZIP whose root ``manifest.json`` + referenced
``detections.json`` are accepted by the existing
``validate_extracted_package`` / ``result_ingestor`` path.

Execution metadata is frozen: executor=remote_gpu, device=cuda:0,
environment=None.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import zipfile

from app.imported_runs.schema import (
    ExecutionMetadata,
    Manifest,
    PackageDetection,
    PipelineMetadata,
    RecordingMetadata,
    ResultPaths,
)
from app.pipelines.base import DetectionPayload, PipelineOutput
from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.definition import ZOOMSPEC_FROZEN_DEFINITION

_EXECUTOR = "remote_gpu"
_LABEL_SPACE = "spacenet_14"
_DETECTIONS_FILENAME = "detections.json"
_ZIP_FILENAME = "analysis_result.zip"


def manifest_for(
    *,
    pipeline_definition,
    label_space: str,
    recording_name: str,
    dataset_name: str,
    device: int,
) -> Manifest:
    """Exact Analysis Package v1 manifest for a frozen remote_gpu run."""
    return Manifest(
        schema_version=1,
        pipeline=PipelineMetadata(
            id=pipeline_definition.id,
            name=pipeline_definition.name,
            version=pipeline_definition.version,
        ),
        label_space=label_space,
        recording=RecordingMetadata(name=recording_name, dataset=dataset_name),
        execution=ExecutionMetadata(
            executor=_EXECUTOR,
            device=f"cuda:{device}",
            environment=None,
        ),
        results=ResultPaths(detections=_DETECTIONS_FILENAME),
        parameters={},
    )


def _detection_to_package(detection: DetectionPayload) -> PackageDetection:
    """1:1 DetectionPayload -> PackageDetection preserving every field."""
    return PackageDetection(
        t_start_s=detection.t_start_s,
        t_end_s=detection.t_end_s,
        f_low_hz=detection.f_low_hz,
        f_high_hz=detection.f_high_hz,
        class_id=detection.class_id,
        class_name=detection.class_name,
        confidence=detection.confidence,
        scores=detection.scores,
    )


def build_analysis_package_zip(
    output: PipelineOutput,
    recording_name: str,
    dataset_name: str,
    workspace: Path,
) -> Path:
    """Serialize ``PipelineOutput`` -> detections.json + manifest.json inside a
    ZIP under ``workspace`` and return the zip path."""
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    zip_path = workspace / _ZIP_FILENAME
    manifest = manifest_for(
        pipeline_definition=ZOOMSPEC_FROZEN_DEFINITION,
        label_space=_LABEL_SPACE,
        recording_name=recording_name,
        dataset_name=dataset_name,
        device=0,
    )
    detections = [_detection_to_package(detection) for detection in output.detections]
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(manifest.model_dump(mode="json"), sort_keys=True),
        )
        archive.writestr(
            _DETECTIONS_FILENAME,
            json.dumps(
                {"detections": [detection.model_dump(mode="json") for detection in detections]},
                sort_keys=True,
            ),
        )
    return zip_path