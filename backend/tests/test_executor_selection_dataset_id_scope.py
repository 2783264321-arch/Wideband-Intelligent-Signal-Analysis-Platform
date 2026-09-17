"""P3A hotfix: executor-selection first-class dataset_id scope (GT-independent)."""
from benchmark_fixture import add_recording

from app.datasets.model import DatasetModel
from app.recordings.model import RecordingModel

PIPELINE = "stft_energy_detector"


def _dataset(session, dataset_id, *, total, gt_count):
    session.add(DatasetModel(
        id=dataset_id, name="ZeroGT", split="test", adapter_id="spacenet",
        label_space="spacenet_14", local_root=f"D:/{dataset_id}", portable_fingerprint="a" * 64,
        sample_count=total, ground_truth_sample_count=gt_count,
    ))
    for index in range(total):
        rid = f"rec_{dataset_id}_{index}"
        add_recording(session, recording_id=rid, name=f"{index:04d}", has_ground_truth=False)
        recording = session.get(RecordingModel, rid)
        recording.dataset_id = dataset_id
        recording.sample_key = f"{index:04d}"
    session.commit()
    return dataset_id


def _select(client, **extra):
    params = {"pipeline_id": PIPELINE}
    params.update(extra)
    return client.get("/api/executor-selection", params=params)


def test_zero_gt_dataset_resolves_standard_local_cpu(client, session):
    dataset_id = _dataset(session, "ds_zero_gt", total=3, gt_count=0)
    response = _select(client, dataset_id=dataset_id)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["requested_mode"] == "auto"
    assert body["resolved_executor"] == "local_cpu"


def test_dataset_id_scope_uses_all_members_and_never_requires_gt(client, session):
    # A dataset whose members carry NO legacy dataset identity and NO GT still
    # resolves: membership comes from dataset_id, not the GT-only projection.
    dataset_id = _dataset(session, "ds_all_members", total=5, gt_count=0)
    response = _select(client, dataset_id=dataset_id)
    assert response.status_code == 200, response.text
    assert response.json()["resolved_executor"] == "local_cpu"


def test_unknown_dataset_id_is_not_found(client):
    response = _select(client, dataset_id="ds_missing")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "DATASET_NOT_FOUND"


def test_dataset_id_and_legacy_triple_together_are_rejected(client, session):
    dataset_id = _dataset(session, "ds_conflict_scope", total=2, gt_count=0)
    response = _select(
        client, dataset_id=dataset_id, dataset_name="ZeroGT",
        dataset_split="test", dataset_label_space="spacenet_14",
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "EXECUTION_SELECTION_REQUEST_INVALID"
