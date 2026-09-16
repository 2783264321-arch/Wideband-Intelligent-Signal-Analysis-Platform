"""Fail-safe deletion endpoints: standalone delete, dataset member guard,
atomic dataset removal, external-file preservation, managed cleanup, and
DB-failure restore (B11)."""
import pytest

from app.benchmarks.model import DatasetEvaluationItemModel, DatasetEvaluationModel
from app.datasets.projection import DatasetProjectionResolver
from app.lifecycle.service import delete_analysis_run
from app.recordings.model import RecordingModel

from benchmark_fixture import add_recording, add_run


def _add_root_member(session, root, name, *, split="test"):
    data_path = root / split / f"{name}.bin"
    metadata_path = root / split / f"{name}.json"
    data_path.parent.mkdir(parents=True, exist_ok=True)
    data_path.write_bytes(b"\x00" * 8)
    metadata_path.write_text("{}")
    recording = RecordingModel(
        id=f"rec_{root.name}_{name}", name=name, data_path=str(data_path),
        data_format="float16_interleaved_le", source="spacenet", external_path=str(data_path),
        sample_rate_hz=1.0, center_frequency_hz=2.0, frequency_low_hz=1.5,
        frequency_high_hz=2.5, num_samples=1, duration_s=1.0,
        dataset_name="SpaceNet", dataset_split=split, label_space="spacenet_14",
        has_ground_truth=True,
    )
    session.add(recording)
    return recording, data_path, metadata_path


def _reference(session, recording_id, run_id):
    session.add(DatasetEvaluationModel(
        id=f"eval_{recording_id}", name="e", dataset_name="SpaceNet", dataset_split="test",
        label_space="spacenet_14", pipeline_id="p", pipeline_version="1.0", status="completed",
        expected_recordings=1, evaluated_recordings=1, missing_recordings=0, coverage=1.0,
        comparable=True, recording_manifest_hash="0" * 64,
        evaluation_protocol="physical_tf_detection_ap_v2", protocol_config_json={},
    ))
    session.add(DatasetEvaluationItemModel(
        id=f"item_{recording_id}", evaluation_id=f"eval_{recording_id}", manifest_order=0,
        recording_id=recording_id, analysis_run_id=run_id, status="included",
        gt_count=1, prediction_count=1,
    ))


def test_standalone_delete_succeeds_and_removes_owned_run(client, session):
    add_recording(session, recording_id="rec_s", name="s", dataset_name=None,
                  dataset_split=None, label_space=None)
    add_run(session, run_id="run_s", recording_id="rec_s", executor="local_cpu", status="completed")
    session.commit()

    assert client.delete("/api/recordings/rec_s").status_code == 204
    session.expire_all()
    assert session.query(RecordingModel).filter_by(id="rec_s").first() is None


def test_dataset_member_cannot_be_deleted_as_standalone(client, session, tmp_path):
    recording, _data, _meta = _add_root_member(session, tmp_path / "SpaceNet-M", "m1")
    session.commit()
    response = client.delete(f"/api/recordings/{recording.id}")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "RECORDING_IS_DATASET_MEMBER"


def test_dataset_removal_is_atomic_when_one_member_is_blocked(client, session, tmp_path):
    root = tmp_path / "SpaceNet-AT"
    members = [_add_root_member(session, root, f"at{index}")[0] for index in range(3)]
    add_run(session, run_id="run_at", recording_id=members[0].id, executor="local_cpu", status="completed")
    _reference(session, members[0].id, "run_at")
    session.commit()
    pid = DatasetProjectionResolver(session).find_for_recording(members[0]).dataset_projection_id

    response = client.delete(f"/api/data-library/datasets/{pid}")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DATASET_REMOVE_BLOCKED"
    # zero members removed
    remaining = {row.id for row in session.query(RecordingModel).all() if row.id.startswith("rec_SpaceNet-AT")}
    assert remaining == {member.id for member in members}


def test_dataset_removal_preserves_external_files(client, session, tmp_path):
    root = tmp_path / "SpaceNet-EXT"
    members = [_add_root_member(session, root, f"ext{index}") for index in range(2)]
    session.commit()
    pid = DatasetProjectionResolver(session).find_for_recording(members[0][0]).dataset_projection_id

    response = client.delete(f"/api/data-library/datasets/{pid}")
    assert response.status_code == 204
    for _recording, data_path, metadata_path in members:
        assert data_path.exists()
        assert metadata_path.exists()


def test_single_import_package_directory_is_removed(client, session):
    add_recording(session, recording_id="rec_i", name="i")
    add_run(session, run_id="run_i", recording_id="rec_i", executor="imported", status="completed",
            parameters_json={"package": {"schema_version": 1}})
    session.commit()
    data_root = client.app.state.settings.data_root
    package_dir = data_root / "imports" / "run_i"
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "manifest.json").write_text("{}")

    assert client.delete("/api/analysis-runs/run_i").status_code == 204
    assert not package_dir.exists()


def test_db_failure_restores_quarantined_files(client, session, monkeypatch):
    add_recording(session, recording_id="rec_q", name="q")
    add_run(session, run_id="run_q", recording_id="rec_q", executor="imported", status="completed",
            parameters_json={"package": {"schema_version": 1}})
    session.commit()
    data_root = client.app.state.settings.data_root
    package_dir = data_root / "imports" / "run_q"
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "manifest.json").write_text("{}")

    def boom():
        raise RuntimeError("db failure")

    monkeypatch.setattr(session, "commit", boom)
    with pytest.raises(RuntimeError):
        delete_analysis_run(session, client.app.state.storage, "run_q")
    assert package_dir.exists()
