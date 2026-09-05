from io import BytesIO
import json
from pathlib import Path
import zipfile

import pytest

import batch_import_fixture as fix


def _zip_root(root: Path) -> BytesIO:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(root).as_posix())
    buffer.seek(0)
    return buffer


def _post(client, zip_bytes):
    return client.post("/api/imported-runs/batch", files={"file": ("tiny.analysis-batch.zip", zip_bytes, "application/zip")})


def _seed(client):
    with client.app.state.database.session_factory() as session:
        fix.seed_local_recordings(session)
        return fix.local_fingerprints(session)


def _build_root(client, tmp_path, *, fingerprints=None, names=("a", "b"), dets=None, outer_overrides=None):
    fingerprints = fingerprints if fingerprints is not None else _seed(client)
    root = Path(tmp_path) / "batch"
    root.mkdir(parents=True, exist_ok=True)
    dets = dets if dets is not None else {"a": [fix.detection()], "b": []}
    for index, name in enumerate(names):
        fix.write_child_package(root, f"{index:06d}", name=name,
                                fingerprint_sha256=fingerprints[name].sha256,
                                detections=dets.get(name, []))
    outer = fix.build_outer_manifest(client, fingerprints=fingerprints, **(outer_overrides or {})) if False else None
    # build outer manifest directly from fingerprints
    items = []
    for index, name in enumerate(names):
        fp = fingerprints[name]
        items.append({
            "key": f"{index:06d}", "package_path": f"items/{index:06d}",
            "recording": {"name": name, "fingerprint": {
                "schema": "recording_fingerprint_v1",
                "metadata": {k: fp.metadata[k] for k in fp.metadata},
                "ground_truth_sha256": fp.ground_truth_sha256, "sha256": fp.sha256,
            }},
        })
    outer = {
        "schema_version": 1, "batch_id": "batch_api",
        "pipeline": {"id": "pipeline_x", "name": "Pipeline X", "version": "1.0"},
        "label_space": fix.LABEL_SPACE,
        "dataset": {"name": fix.DATASET_NAME, "split": fix.DATASET_SPLIT},
        "expected_items": len(items),
        "execution": {"executor": "historical_import", "device": "gpu", "environment": "frozen"},
        "result_provenance": {"config_sha256": "a" * 64},
        "transport_provenance": {"exporter_version": "batch_analysis_package_v1"},
        "items": items,
    }
    outer.update(outer_overrides or {})
    (root / "batch_manifest.json").write_text(json.dumps(outer), encoding="utf-8")
    return root


def test_batch_import_api_happy_path(client, tmp_path):
    root = _build_root(client, tmp_path)
    response = _post(client, _zip_root(root))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["already_imported"] is False
    assert body["created_runs"] == 2
    assert body["created_detections"] == 1
    assert len(body["recording_run_mapping"]) == 2


def test_batch_import_api_idempotent_second_import(client, tmp_path):
    root = _build_root(client, tmp_path)
    zip_bytes = _zip_root(root)
    first = _post(client, zip_bytes).json()
    second = _post(client, zip_bytes)
    assert second.status_code == 201, second.text
    body = second.json()
    assert body["already_imported"] is True
    assert body["created_runs"] == 0
    assert body["created_detections"] == 0
    assert body["existing_runs"] == 2
    assert body["recording_run_mapping"] == first["recording_run_mapping"]


def test_batch_import_api_malformed_zip(client, tmp_path):
    bad = BytesIO(b"not a zip")
    response = _post(client, bad)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_BATCH_IMPORT_PACKAGE"


def test_batch_import_api_missing_recording(client, tmp_path):
    root = _build_root(client, tmp_path)
    # Remove local recordings so no candidate matches.
    with client.app.state.database.session_factory() as session:
        from app.recordings.model import RecordingModel
        for row in session.query(RecordingModel).all():
            row.name = "renamed"
        session.commit()
    response = _post(client, _zip_root(root))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "BATCH_RECORDING_NOT_FOUND"


def test_batch_import_api_fingerprint_mismatch(client, tmp_path):
    root = _build_root(client, tmp_path)
    outer = json.loads((root / "batch_manifest.json").read_text(encoding="utf-8"))
    outer["items"][0]["recording"]["fingerprint"]["metadata"]["sample_rate_hz"] = 999.0
    outer["items"][0]["recording"]["fingerprint"]["sha256"] = "1" * 64
    (root / "batch_manifest.json").write_text(json.dumps(outer), encoding="utf-8")
    response = _post(client, _zip_root(root))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "RECORDING_FINGERPRINT_MISMATCH"


def test_batch_import_api_dataset_manifest_mismatch(client, tmp_path):
    root = _build_root(client, tmp_path, outer_overrides={"recording_manifest_hash": "0" * 64})
    response = _post(client, _zip_root(root))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DATASET_MANIFEST_MISMATCH"


def test_batch_import_api_partial_state_inconsistent(client, tmp_path):
    root = _build_root(client, tmp_path)
    zip_bytes = _zip_root(root)
    assert _post(client, zip_bytes).status_code == 201
    with client.app.state.database.session_factory() as session:
        from app.analysis.model import AnalysisRunModel
        from app.detections.model import DetectionResultModel
        run = session.query(AnalysisRunModel).first()
        session.query(DetectionResultModel).filter(DetectionResultModel.run_id == run.id).delete()
        session.delete(run)
        session.commit()
    response = _post(client, zip_bytes)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "BATCH_IMPORT_STATE_INCONSISTENT"