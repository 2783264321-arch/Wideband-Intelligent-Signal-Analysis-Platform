"""P1 Batch B: first-class dataset analysis-history bridge (projection hidden)."""
from app.datasets.model import DatasetModel
from app.datasets.projection import DatasetProjectionResolver
from app.recordings.model import RecordingModel

from benchmark_fixture import add_run


def _dataset_with_members(session, tmp_path):
    dataset_id = "ds_hist"
    session.add(DatasetModel(
        id=dataset_id, name="SpaceNet", split="test", adapter_id="spacenet",
        label_space="spacenet_14", local_root=str(tmp_path.resolve()), portable_fingerprint="a" * 64,
        sample_count=2, ground_truth_sample_count=2,
    ))
    members = []
    for index in range(2):
        data_path = tmp_path / f"h{index}.bin"
        data_path.write_bytes(b"\x00" * 8)
        recording = RecordingModel(
            id=f"rec_hist_{index}", name=f"h{index}", data_path=str(data_path),
            data_format="float16_interleaved_le", source="spacenet", external_path=str(data_path),
            sample_rate_hz=1.0, center_frequency_hz=2.0, frequency_low_hz=1.5, frequency_high_hz=2.5,
            num_samples=2, duration_s=2.0, dataset_name="SpaceNet", dataset_split="test",
            label_space="spacenet_14", has_ground_truth=True, dataset_id=dataset_id, sample_key=f"h{index}",
        )
        session.add(recording)
        members.append(recording)
    return dataset_id, members


def test_dataset_analysis_history_bridge_returns_items_without_projection_id(client, session, tmp_path):
    dataset_id, members = _dataset_with_members(session, tmp_path)
    fingerprint = "c" * 64
    for index, recording in enumerate(members):
        add_run(
            session, run_id=f"run_hist_{index}", recording_id=recording.id,
            pipeline_id="zoomspec", pipeline_version="1.0", executor="imported", status="completed",
            parameters_json={"batch_import": {
                "import_fingerprint": fingerprint, "item_key": f"key_{index}",
                "batch_id": "batch_hist", "archive_sha256": "d" * 64,
            }},
        )
    session.commit()

    body = client.get(f"/api/datasets/{dataset_id}/analysis-history").json()
    assert body["dataset_id"] == dataset_id
    assert body["total"] >= 1
    assert "dataset_projection_id" not in body
    batch = next(item for item in body["items"] if item["kind"] == "imported_batch")
    assert batch["executor"] == "imported"
    assert batch["completed_items"] == 2
    assert batch["expected_items"] == 2


def test_unknown_dataset_history_is_404(client):
    response = client.get("/api/datasets/ds_missing/analysis-history")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "DATASET_NOT_FOUND"


def test_legacy_projection_history_endpoint_still_works(client, session, tmp_path):
    _dataset_id, members = _dataset_with_members(session, tmp_path)
    add_run(
        session, run_id="run_legacy_h", recording_id=members[0].id, executor="imported", status="completed",
        parameters_json={"batch_import": {
            "import_fingerprint": "e" * 64, "item_key": "key_legacy",
            "batch_id": "batch_legacy", "archive_sha256": "f" * 64,
        }},
    )
    session.commit()
    projection = DatasetProjectionResolver(session).find_for_recording(members[0])
    assert projection is not None

    body = client.get(
        f"/api/data-library/datasets/{projection.dataset_projection_id}/analysis-history"
    ).json()
    assert body["dataset_projection_id"] == projection.dataset_projection_id
    assert body["total"] >= 1
