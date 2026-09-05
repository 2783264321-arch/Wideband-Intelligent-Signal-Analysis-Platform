"""M9.0 bridge exporter tests + M6 import verification.

Builds tiny synthetic packages through the bridge exporter and proves the
existing M6 ``PackageImportService`` accepts them end-to-end:
Analysis Package ZIP -> import -> AnalysisRun(executor=imported) ->
status=completed -> DetectionResult[].

The M6 production importer is NOT modified.
"""
from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
import sys
import zipfile

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from iq_fixture import write_tiny_iq  # noqa: E402

from research.m9_legacy_bridge.adapter import (  # noqa: E402
    LegacyDetectionAdapter,
    load_label_space,
)
from research.m9_legacy_bridge.exporter import (  # noqa: E402
    build_detections,
    build_manifest,
    build_metrics,
    export_package,
)
from research.m9_legacy_bridge.schema import (  # noqa: E402
    HistoricalEvaluation,
    PlatformDetection,
    Provenance,
    RecordingContext,
)

LABEL_SPACE = REPO_ROOT / "label_spaces" / "spacenet_14.json"


@pytest.fixture(scope="module")
def label_space() -> dict[int, str]:
    return load_label_space(LABEL_SPACE)


def _recording() -> RecordingContext:
    return RecordingContext(
        name="analysis-demo",
        duration_s=0.004096,
        frequency_low_hz=2440.5e6,
        frequency_high_hz=2441.5e6,
    )


def _detections(label_space) -> list[PlatformDetection]:
    return [
        PlatformDetection(
            t_start_s=0.0005,
            t_end_s=0.0035,
            f_low_hz=2441.06e6,
            f_high_hz=2441.10e6,
            class_id=6,
            class_name=label_space[6],
            confidence=0.94,
        )
    ]


def _historical() -> HistoricalEvaluation:
    return HistoricalEvaluation(
        scope="full historical SpaceNet advanced/test evaluation",
        mAP50=0.49706861157413673,
        mAP50_95=0.37325127587379914,
        source_report="tests/fake/test_full_val_map_augv3.json",
    )


def _provenance() -> Provenance:
    return Provenance(
        legacy_prediction_sha256="a" * 64,
        detector_checkpoint_sha256="b" * 64,
        frn_checkpoint_sha256="c" * 64,
        config_sha256="d" * 64,
    )


def test_manifest_matches_m6_wire_contract(label_space):
    manifest = build_manifest(recording=_recording())
    assert manifest["schema_version"] == 1
    assert manifest["pipeline"]["id"] == "zoomspec_yolo26n_aug_combined_frn_v3"
    assert manifest["pipeline"]["name"] == "ZoomSpec YOLOv26n Aug + Combined FRN V3"
    assert manifest["pipeline"]["version"] == "1.0.0"
    assert manifest["label_space"] == "spacenet_14"
    assert manifest["recording"] == {
        "name": "analysis-demo",
        "dataset": "SpaceNet advanced/test",
    }
    assert manifest["execution"]["executor"] == "historical_import"
    assert manifest["results"]["detections"] == "detections.json"
    assert manifest["results"]["metrics"] == "metrics.json"


def test_detections_document_has_array_of_package_detections(label_space):
    document = build_detections(_detections(label_space))
    assert set(document) == {"detections"}
    items = document["detections"]
    assert len(items) == 1
    assert items[0] == {
        "t_start_s": 0.0005,
        "t_end_s": 0.0035,
        "f_low_hz": 2441.06e6,
        "f_high_hz": 2441.10e6,
        "class_id": 6,
        "class_name": "BLE LE1M",
        "confidence": 0.94,
    }


def test_metrics_separates_historical_evaluation_from_provenance():
    metrics = build_metrics(historical=_historical(), provenance=_provenance())
    historical = metrics["historical_evaluation"]
    assert historical["scope"] == "full historical SpaceNet advanced/test evaluation"
    assert historical["mAP50"] == 0.49706861157413673
    assert historical["mAP50_95"] == 0.37325127587379914
    provenance = metrics["provenance"]
    assert provenance["legacy_prediction_sha256"] == "a" * 64
    assert provenance["detector_checkpoint_sha256"] == "b" * 64
    assert provenance["frn_checkpoint_sha256"] == "c" * 64
    assert provenance["config_sha256"] == "d" * 64
    # A single-sample metric must not be silently merged into the corpus metric.
    assert "sample_metric" not in metrics


def test_export_package_writes_valid_zip(tmp_path, label_space):
    zip_path = export_package(
        output_dir=tmp_path,
        recording=_recording(),
        detections=_detections(label_space),
        historical=_historical(),
        provenance=_provenance(),
    )
    assert zip_path.name == "analysis-demo.analysis.zip"
    assert zip_path.is_file()
    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
        assert names == {"manifest.json", "detections.json", "metrics.json"}
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["recording"]["name"] == "analysis-demo"
        detections = json.loads(archive.read("detections.json"))
        assert len(detections["detections"]) == 1
        metrics = json.loads(archive.read("metrics.json"))
        assert "historical_evaluation" in metrics
        assert "provenance" in metrics


def test_export_package_rejects_empty_detections(tmp_path):
    with pytest.raises(Exception):
        export_package(
            output_dir=tmp_path,
            recording=_recording(),
            detections=[],
            historical=_historical(),
            provenance=_provenance(),
        )


def _register_recording(client, *, name="analysis-demo"):
    with write_tiny_iq(Path(__file__).with_name(".tiny_iq.bin")).open("rb") as handle:
        response = client.post(
            "/api/recordings",
            data={
                "name": name,
                "sample_rate_hz": "1000000",
                "center_frequency_hz": "2441000000",
                "data_format": "complex64_le",
                "label_space": "spacenet_14",
            },
            files={"file": ("tiny.bin", handle, "application/octet-stream")},
        )
    assert response.status_code == 201, response.text
    return response.json()


def _post_zip(client, recording_id, zip_path):
    with zip_path.open("rb") as handle:
        return client.post(
            "/api/imported-runs",
            data={"recording_id": recording_id},
            files={"file": ("analysis.zip", handle, "application/zip")},
        )


def test_bridge_package_imports_through_m6(client, tmp_path, label_space):
    recording = _register_recording(client)
    zip_path = export_package(
        output_dir=tmp_path,
        recording=RecordingContext(
            name=recording["name"],
            duration_s=recording["duration_s"],
            frequency_low_hz=recording["frequency_low_hz"],
            frequency_high_hz=recording["frequency_high_hz"],
        ),
        detections=_detections(label_space),
        historical=_historical(),
        provenance=_provenance(),
    )

    response = _post_zip(client, recording["id"], zip_path)
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["executor"] == "imported"
    assert run["status"] == "completed"
    assert run["pipeline_id"] == "zoomspec_yolo26n_aug_combined_frn_v3"
    assert run["pipeline_version"] == "1.0.0"

    detections = client.get(f"/api/analysis-runs/{run['id']}/detections")
    assert detections.status_code == 200
    items = detections.json()
    assert len(items) == 1
    assert items[0]["recording_id"] == recording["id"]
    assert items[0]["class_id"] == 6
    assert items[0]["class_name"] == "BLE LE1M"
    assert items[0]["confidence"] == 0.94
    assert items[0]["t_start_s"] == 0.0005
    assert items[0]["f_low_hz"] == 2441.06e6


def test_bridge_package_through_m6_rejects_bbox_outside_recording(client, tmp_path, label_space):
    recording = _register_recording(client)
    outside = _detections(label_space)
    outside[0] = PlatformDetection(
        t_start_s=0.0005,
        t_end_s=0.0035,
        f_low_hz=2446.0e6,  # outside [2440.5e6, 2441.5e6]
        f_high_hz=2446.1e6,
        class_id=6,
        class_name=label_space[6],
        confidence=0.94,
    )
    zip_path = export_package(
        output_dir=tmp_path,
        recording=RecordingContext(
            name=recording["name"],
            duration_s=recording["duration_s"],
            frequency_low_hz=recording["frequency_low_hz"],
            frequency_high_hz=recording["frequency_high_hz"],
        ),
        detections=outside,
        historical=_historical(),
        provenance=_provenance(),
    )
    response = _post_zip(client, recording["id"], zip_path)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_IMPORT_PACKAGE"