"""P1 Batch B: first-class Dataset deletion (membership-based, FK-independent)."""
from app.benchmarks.model import DatasetEvaluationItemModel, DatasetEvaluationModel
from app.datasets.model import DatasetModel
from app.recordings.model import RecordingModel


def _dataset(session, dataset_id="ds_del", *, name="SpaceNet", split="test", root="D:/SpaceNet"):
    session.add(DatasetModel(
        id=dataset_id, name=name, split=split, adapter_id="spacenet", label_space="spacenet_14",
        local_root=root, portable_fingerprint="a" * 64, sample_count=0, ground_truth_sample_count=0,
    ))
    return dataset_id


def _member(session, dataset_id, index, tmp_path):
    data_path = tmp_path / f"m{index}.bin"
    data_path.write_bytes(b"\x00" * 8)
    session.add(RecordingModel(
        id=f"rec_del_{index}", name=f"m{index}", data_path=str(data_path), data_format="float16_interleaved_le",
        source="spacenet", external_path=str(data_path), sample_rate_hz=1.0, center_frequency_hz=2.0,
        frequency_low_hz=1.5, frequency_high_hz=2.5, num_samples=2, duration_s=2.0,
        dataset_name="SpaceNet", dataset_split="test", label_space="spacenet_14",
        has_ground_truth=True, dataset_id=dataset_id, sample_key=f"m{index}",
    ))
    return data_path


def _blocker(session, recording_id):
    session.add(DatasetEvaluationModel(
        id=f"eval_{recording_id}", name="e", dataset_name="SpaceNet", dataset_split="test",
        label_space="spacenet_14", pipeline_id="p", pipeline_version="1.0", status="completed",
        expected_recordings=1, evaluated_recordings=1, missing_recordings=0, coverage=1.0,
        comparable=True, recording_manifest_hash="0" * 64,
        evaluation_protocol="physical_tf_detection_ap_v2", protocol_config_json={},
    ))
    session.add(DatasetEvaluationItemModel(
        id=f"item_{recording_id}", evaluation_id=f"eval_{recording_id}", manifest_order=0,
        recording_id=recording_id, analysis_run_id=None, status="included", gt_count=1, prediction_count=1,
    ))


def test_delete_dataset_removes_members_but_preserves_external_iq(client, session, tmp_path):
    dataset_id = _dataset(session)
    files = [_member(session, dataset_id, index, tmp_path) for index in range(2)]
    session.commit()

    assert client.delete(f"/api/datasets/{dataset_id}").status_code == 204
    session.expire_all()

    assert session.get(DatasetModel, dataset_id) is None
    assert session.query(RecordingModel).filter(RecordingModel.dataset_id == dataset_id).count() == 0
    for path in files:
        assert path.exists()


def test_delete_unknown_dataset_is_404(client):
    response = client.delete("/api/datasets/ds_missing")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "DATASET_NOT_FOUND"


def test_delete_dataset_is_atomic_when_blocked(client, session, tmp_path):
    dataset_id = _dataset(session)
    _member(session, dataset_id, 0, tmp_path)
    _member(session, dataset_id, 1, tmp_path)
    _blocker(session, "rec_del_0")
    session.commit()

    response = client.delete(f"/api/datasets/{dataset_id}")
    assert response.status_code == 409
    body = response.json()["error"]
    assert body["code"] == "DATASET_REMOVE_BLOCKED"
    assert body["details"]["blockers"]
    session.expire_all()
    assert session.get(DatasetModel, dataset_id) is not None
    assert session.query(RecordingModel).filter(RecordingModel.dataset_id == dataset_id).count() == 2


def test_legacy_projection_delete_endpoint_still_exists(client, session, tmp_path):
    from app.datasets.projection import DatasetProjectionResolver

    dataset_id = _dataset(session)
    _member(session, dataset_id, 0, tmp_path)
    session.commit()
    projection = DatasetProjectionResolver(session).find_for_recording(
        session.get(RecordingModel, "rec_del_0")
    )
    assert projection is not None
    assert client.delete(f"/api/data-library/datasets/{projection.dataset_projection_id}").status_code == 204
