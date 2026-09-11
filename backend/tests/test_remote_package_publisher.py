"""D5 generic Analysis Package v1 publisher tests.

``manifest_for`` / ``build_analysis_package_zip`` are generic: pipeline identity
comes from the ``PipelineDefinition``, label space from the resolved output label
space, and execution metadata exactly from
``RuntimeDescriptor.public_projection()``. ``publish_package`` is the production
callable matching ``PluginItemExecutor``'s package-publisher seam and delegates
final persistence to ``result_publisher.publish_result``. No GPU.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import zipfile

import pytest

from benchmark_fixture import add_recording

from app.imported_runs.archive import extract_package
from app.imported_runs.schema import ExecutionMetadata, Manifest
from app.imported_runs.validation import validate_extracted_package
from app.labels.service import LabelSpaceService
from app.pipelines.base import DetectionPayload, PipelineDefinition, PipelineOutput
from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.definition import ZOOMSPEC_FROZEN_DEFINITION
from app.recordings.model import RecordingModel
from app.remote_execution import package_publisher as package_publisher_module
from app.remote_execution.package_publisher import (
    build_analysis_package_zip,
    manifest_for,
    publish_package,
)
from app.remote_execution.request_builder import build_batch, freeze_request_provenance
from app.remote_execution.runtime import RuntimeDescriptor

RECORDING_NAME = "0"
DATASET_NAME = "SpaceNet"
RUN = "a" * 40
MANIFEST_SHA = "c" * 64

LEGACY_DESCRIPTOR = RuntimeDescriptor(
    executor="remote_gpu", device_type="cuda", device_index=0, precision="float16"
)
GENERIC_DESCRIPTOR = RuntimeDescriptor(
    executor="remote_gpu",
    device_type="cuda",
    device_index=3,
    precision="float16",
    environment_ref="private-deployment-ref",
    environment_label="autodl_primary",
)

_CPN_DEFINITION = PipelineDefinition(
    id="cpn_only",
    name="CPN Only",
    version="1.0",
    label_space="spacenet_14",
    output_label_space="signal_presence_v1",
    recommended_device="GPU",
    cpu_supported=False,
    stages=(),
    inspectable_stages=(),
    executors_supported=("remote_gpu",),
    recommended_executor="remote_gpu",
)


def _detection(*, class_id=9, class_name="LoRa 250kHz", confidence=0.94):
    return DetectionPayload(
        t_start_s=0.01,
        t_end_s=0.02,
        f_low_hz=2440600000.0,
        f_high_hz=2440700000.0,
        class_id=class_id,
        class_name=class_name,
        confidence=confidence,
        scores={"cpn": 0.9, "frn_signal": 0.92, "frn_class": 0.95, "fused": confidence},
    )


def test_manifest_for_matches_frozen_definition_with_legacy_descriptor():
    manifest = manifest_for(
        pipeline_definition=ZOOMSPEC_FROZEN_DEFINITION,
        label_space="spacenet_14",
        recording_name=RECORDING_NAME,
        dataset_name=DATASET_NAME,
        runtime_descriptor=LEGACY_DESCRIPTOR,
        parameters={},
    )
    assert isinstance(manifest, Manifest)
    assert manifest.schema_version == 1
    assert manifest.pipeline.id == ZOOMSPEC_FROZEN_DEFINITION.id
    assert manifest.pipeline.name == ZOOMSPEC_FROZEN_DEFINITION.name
    assert manifest.pipeline.version == ZOOMSPEC_FROZEN_DEFINITION.version
    assert manifest.label_space == "spacenet_14"
    assert manifest.recording.name == RECORDING_NAME
    assert manifest.recording.dataset == DATASET_NAME
    assert manifest.results.detections == "detections.json"
    assert manifest.parameters == {}
    assert manifest.execution == ExecutionMetadata(
        executor="remote_gpu", device="cuda:0", environment=None
    )


def test_manifest_for_projects_nonzero_device_and_environment_label():
    manifest = manifest_for(
        pipeline_definition=ZOOMSPEC_FROZEN_DEFINITION,
        label_space="spacenet_14",
        recording_name=RECORDING_NAME,
        dataset_name=DATASET_NAME,
        runtime_descriptor=GENERIC_DESCRIPTOR,
        parameters={"threshold": 0.5},
    )
    assert manifest.execution.executor == "remote_gpu"
    assert manifest.execution.device == "cuda:3"
    assert manifest.execution.environment == "autodl_primary"
    assert manifest.parameters == {"threshold": 0.5}


def _seed_recording(session, label_space="spacenet_14"):
    add_recording(session, recording_id="rec_pkg", name=RECORDING_NAME,
                  dataset_name="SpaceNet", dataset_split="test", label_space=label_space)
    session.commit()
    return session.get(RecordingModel, "rec_pkg")


def _roundtrip(tmp_path, session, settings, output, *, descriptor=LEGACY_DESCRIPTOR,
               definition=ZOOMSPEC_FROZEN_DEFINITION, label_space="spacenet_14",
               parameters=None, expected_label_space=None, recording_label_space="spacenet_14"):
    workspace = tmp_path / "ws"
    zip_path = build_analysis_package_zip(
        output,
        pipeline_definition=definition,
        label_space=label_space,
        runtime_descriptor=descriptor,
        parameters=parameters or {},
        recording_name=RECORDING_NAME,
        dataset_name=DATASET_NAME,
        workspace=workspace,
    )
    assert zip_path.is_file()
    recording = _seed_recording(session, recording_label_space)
    labels = LabelSpaceService(settings.label_space_root)
    with zip_path.open("rb") as source:
        root = extract_package(source, tmp_path / "extracted")
    validated = validate_extracted_package(
        root, recording, labels, expected_label_space=expected_label_space
    )
    return validated, zip_path


def test_package_zip_roundtrips_through_validate_extracted_package(session, tmp_path, settings):
    output = PipelineOutput(detections=[_detection()])
    validated, _ = _roundtrip(tmp_path, session, settings, output)
    assert len(validated.detections) == 1
    det = validated.detections[0]
    assert det.class_id == 9
    assert det.class_name == "LoRa 250kHz"
    assert validated.manifest.pipeline.id == ZOOMSPEC_FROZEN_DEFINITION.id


def test_generic_package_projects_descriptor_without_private_fields(session, tmp_path, settings):
    output = PipelineOutput(detections=[_detection()])
    _, zip_path = _roundtrip(
        tmp_path, session, settings, output, descriptor=GENERIC_DESCRIPTOR
    )
    with zipfile.ZipFile(zip_path) as archive:
        raw = archive.read("manifest.json").decode("utf-8")
    manifest = json.loads(raw)
    assert manifest["execution"] == {
        "executor": "remote_gpu",
        "device": "cuda:3",
        "environment": "autodl_primary",
    }
    # Private runtime data must never enter the public package.
    assert "environment_ref" not in raw
    assert "private-deployment-ref" not in raw
    assert "precision" not in raw
    assert "device_index" not in raw


def test_output_label_space_package_roundtrips(session, tmp_path, settings):
    signal = DetectionPayload(
        t_start_s=0.01, t_end_s=0.02, f_low_hz=2440600000.0, f_high_hz=2440700000.0,
        class_id=0, class_name="Signal", confidence=0.9, scores=None,
    )
    output = PipelineOutput(detections=[signal])
    validated, _ = _roundtrip(
        tmp_path, session, settings, output,
        definition=_CPN_DEFINITION, label_space="signal_presence_v1",
        expected_label_space="signal_presence_v1",
    )
    assert validated.manifest.label_space == "signal_presence_v1"
    assert len(validated.detections) == 1


def test_package_detection_mapping_preserves_all_fields(session, tmp_path, settings):
    first = _detection()
    second = DetectionPayload(
        t_start_s=0.03, t_end_s=0.04, f_low_hz=2440800000.0, f_high_hz=2440900000.0,
        class_id=6, class_name="BLE LE1M", confidence=0.61, scores=None,
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


# ---------------------------------------------------------------------------
# D5 production publisher callable (D3A seam)
# ---------------------------------------------------------------------------


def _d3a_seam_kwargs(tmp_path, *, output, pipeline_definition, descriptor,
                     output_label_space, parameters=None):
    metadata = freeze_request_provenance(
        local_run_id="run_x",
        recording_fingerprint="2" * 64,
        source_data_sha256="1" * 64,
        dataset_name=DATASET_NAME,
        dataset_split="test",
        dataset_key=RECORDING_NAME,
        label_space="spacenet_14",
        pipeline_id=pipeline_definition.id,
        pipeline_version=pipeline_definition.version,
        required_remote_runtime_commit=RUN,
        orchestrator_commit=RUN,
        asset_manifest_sha256=MANIFEST_SHA,
        remote_profile="autodl_primary",
        model_release_id="golden",
        parameters=parameters or {},
    )
    batch = build_batch(metadata)
    item = batch.items[0]
    return dict(
        output=output,
        pipeline_definition=pipeline_definition,
        output_label_space=output_label_space,
        runtime_descriptor=descriptor,
        item=item,
        batch=batch,
        job_root=tmp_path / "job",
        workspace=tmp_path / "job" / "work" / item.item_key,
        asset_manifest_sha256=MANIFEST_SHA,
        remote_started_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
        remote_finished_at=datetime(2026, 1, 1, 0, 1, 0, tzinfo=timezone.utc),
    )


def test_publish_package_matches_d3a_seam_and_delegates(tmp_path, monkeypatch):
    captured = {}

    def fake_publish_result(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(package_publisher_module, "publish_result", fake_publish_result)
    output = PipelineOutput(detections=[_detection()])
    kwargs = _d3a_seam_kwargs(
        tmp_path, output=output, pipeline_definition=ZOOMSPEC_FROZEN_DEFINITION,
        descriptor=GENERIC_DESCRIPTOR, output_label_space="spacenet_14",
        parameters={"threshold": 0.25},
    )
    publish_package(**kwargs)

    assert captured["item"] is kwargs["item"]
    assert captured["batch"] is kwargs["batch"]
    assert captured["remote_runtime_commit"] == RUN
    assert captured["asset_manifest_sha256"] == MANIFEST_SHA
    assert captured["zip_path"].is_file()
    with zipfile.ZipFile(captured["zip_path"]) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["parameters"] == {"threshold": 0.25}
    assert manifest["execution"] == {
        "executor": "remote_gpu", "device": "cuda:3", "environment": "autodl_primary",
    }
    # Derived hardware stays public (no private runtime reference).
    assert captured["hardware"] == {"executor": "remote_gpu", "device": "cuda:3"}


def test_publish_package_threads_explicit_hardware(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        package_publisher_module, "publish_result", lambda **kwargs: captured.update(kwargs)
    )
    kwargs = _d3a_seam_kwargs(
        tmp_path, output=PipelineOutput(detections=[]),
        pipeline_definition=ZOOMSPEC_FROZEN_DEFINITION,
        descriptor=LEGACY_DESCRIPTOR, output_label_space="spacenet_14",
    )
    kwargs["hardware"] = {"device_name": "RTX 5090"}
    publish_package(**kwargs)
    assert captured["hardware"] == {"device_name": "RTX 5090"}


def test_publisher_and_ingestor_imports_are_torch_free():
    import subprocess
    import sys

    repo_root = Path(__file__).resolve().parents[2]
    code = (
        "import sys;"
        "import app.remote_execution.package_publisher;"
        "import app.remote_execution.result_ingestor;"
        "assert 'torch' not in sys.modules, 'torch leaked';"
        "assert 'ultralytics' not in sys.modules, 'ultralytics leaked';"
        "print('OK')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=repo_root / "backend",
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
