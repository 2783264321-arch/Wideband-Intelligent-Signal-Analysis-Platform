"""Analysis Package v1 publisher tests (Task 12F-B Task 3).

``manifest_for`` / ``build_analysis_package_zip`` serialize a ``PipelineOutput``
into the existing Analysis Package v1 ZIP (root ``manifest.json`` + referenced
``detections.json``) so the existing ``validate_extracted_package`` /
``result_ingestor`` path accepts it. Exact execution metadata:
executor=remote_gpu, device=cuda:0, environment=None. No GPU.
"""
from __future__ import annotations

import json
from pathlib import Path
import zipfile

import pytest

from benchmark_fixture import add_recording

from app.imported_runs.archive import extract_package
from app.imported_runs.schema import ExecutionMetadata, Manifest, PackageDetection
from app.imported_runs.validation import validate_extracted_package
from app.labels.service import LabelSpaceService
from app.pipelines.base import DetectionPayload, PipelineOutput
from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.definition import ZOOMSPEC_FROZEN_DEFINITION
from app.recordings.model import RecordingModel
from app.remote_execution.package_publisher import (
    build_analysis_package_zip,
    manifest_for,
)

RECORDING_NAME = "0"
DATASET_NAME = "SpaceNet"


def _detection(*, class_id=9, class_name="LoRa 250kHz", confidence=0.94):
    return DetectionPayload(
        t_start_s=0.01,
        t_end_s=0.02,
        f_low_hz=2440600000.0,
        f_high_hz=2440700000.0,
        class_id=class_id,
        class_name=class_name,
        confidence=confidence,
        scores={
            "cpn": 0.9,
            "frn_signal": 0.92,
            "frn_class": 0.95,
            "fused": confidence,
        },
    )


def test_manifest_for_matches_frozen_definition():
    manifest = manifest_for(
        pipeline_definition=ZOOMSPEC_FROZEN_DEFINITION,
        label_space="spacenet_14",
        recording_name=RECORDING_NAME,
        dataset_name=DATASET_NAME,
        device=0,
    )
    assert isinstance(manifest, Manifest)
    assert manifest.schema_version == 1
    assert manifest.pipeline.id == ZOOMSPEC_FROZEN_DEFINITION.id
    assert manifest.pipeline.name == ZOOMSPEC_FROZEN_DEFINITION.name
    assert manifest.pipeline.version == ZOOMSPEC_FROZEN_DEFINITION.version
    assert manifest.label_space == "spacenet_14"
    assert manifest.recording.name == RECORDING_NAME
    assert manifest.recording.dataset == DATASET_NAME
    assert manifest.execution.executor == "remote_gpu"
    assert manifest.results.detections == "detections.json"
    assert manifest.parameters == {}


def test_manifest_execution_metadata_exact():
    manifest = manifest_for(
        pipeline_definition=ZOOMSPEC_FROZEN_DEFINITION,
        label_space="spacenet_14",
        recording_name=RECORDING_NAME,
        dataset_name=DATASET_NAME,
        device=0,
    )
    assert manifest.execution == ExecutionMetadata(
        executor="remote_gpu", device="cuda:0", environment=None
    )
    assert manifest.execution.executor == "remote_gpu"
    assert manifest.execution.device == "cuda:0"
    assert manifest.execution.environment is None


def _seed_recording(session):
    add_recording(session, recording_id="rec_pkg", name=RECORDING_NAME,
                  dataset_name="SpaceNet", dataset_split="test", label_space="spacenet_14")
    session.commit()
    return session.get(RecordingModel, "rec_pkg")


def _roundtrip(tmp_path, session, settings, output: PipelineOutput):
    workspace = tmp_path / "ws"
    zip_path = build_analysis_package_zip(
        output, RECORDING_NAME, DATASET_NAME, workspace
    )
    assert zip_path.is_file()
    recording = _seed_recording(session)
    labels = LabelSpaceService(settings.label_space_root)
    with zip_path.open("rb") as source:
        root = extract_package(source, tmp_path / "extracted")
    validated = validate_extracted_package(root, recording, labels)
    return validated, zip_path


def test_package_zip_roundtrips_through_validate_extracted_package(session, tmp_path, settings):
    output = PipelineOutput(detections=[_detection()])
    validated, _ = _roundtrip(tmp_path, session, settings, output)
    assert len(validated.detections) == 1
    det = validated.detections[0]
    assert det.t_start_s == 0.01
    assert det.t_end_s == 0.02
    assert det.f_low_hz == 2440600000.0
    assert det.f_high_hz == 2440700000.0
    assert det.class_id == 9
    assert det.class_name == "LoRa 250kHz"
    assert det.confidence == 0.94
    assert det.scores == {
        "cpn": 0.9,
        "frn_signal": 0.92,
        "frn_class": 0.95,
        "fused": 0.94,
    }
    assert validated.manifest.pipeline.id == ZOOMSPEC_FROZEN_DEFINITION.id


def test_package_detection_mapping_preserves_all_fields(session, tmp_path, settings):
    first = _detection()
    second = DetectionPayload(
        t_start_s=0.03,
        t_end_s=0.04,
        f_low_hz=2440800000.0,
        f_high_hz=2440900000.0,
        class_id=6,
        class_name="BLE LE1M",
        confidence=0.61,
        scores=None,
    )
    output = PipelineOutput(detections=[first, second])
    validated, _ = _roundtrip(tmp_path, session, settings, output)
    assert len(validated.detections) == 2
    for source, parsed in zip((first, second), validated.detections):
        assert parsed.t_start_s == source.t_start_s
        assert parsed.t_end_s == source.t_end_s
        assert parsed.f_low_hz == source.f_low_hz
        assert parsed.f_high_hz == source.f_high_hz
        assert parsed.class_id == source.class_id
        assert parsed.class_name == source.class_name
        assert parsed.confidence == source.confidence
        assert parsed.scores == source.scores


def test_empty_output_produces_empty_detections_array(session, tmp_path, settings):
    output = PipelineOutput(detections=[])
    validated, zip_path = _roundtrip(tmp_path, session, settings, output)
    assert validated.detections == ()
    with zipfile.ZipFile(zip_path) as archive:
        detections_doc = json.loads(archive.read("detections.json"))
    assert detections_doc["detections"] == []