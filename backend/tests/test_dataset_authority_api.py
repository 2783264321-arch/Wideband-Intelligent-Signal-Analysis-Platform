"""P1 first-class dataset read API and legacy data-library compatibility."""
from app.datasets.model import DatasetModel
from app.recordings.model import RecordingModel


def _add_dataset(session, *, name="SpaceNet", split="test", root="D:/SpaceNet", count=1,
                 adapter_id="spacenet", label_space="spacenet_14", fingerprint=None):
    dataset = DatasetModel(
        id=f"ds_{root}_{name}_{split}".replace("/", "_").replace(":", "_").replace("\\", "_"),
        name=name, split=split, adapter_id=adapter_id, label_space=label_space,
        local_root=root, portable_fingerprint=fingerprint or ("a" * 64),
        sample_count=count, ground_truth_sample_count=0,
    )
    session.add(dataset)
    for index in range(count):
        sample_key = f"{name}_{index}"
        session.add(RecordingModel(
            id=f"rec_{dataset.id}_{index}", name=sample_key, data_path=f"{root}/{split}/{sample_key}.bin",
            data_format="float16_interleaved_le", source=adapter_id, external_path=f"{root}/{split}/{sample_key}.bin",
            sample_rate_hz=1.0, center_frequency_hz=2.0, frequency_low_hz=1.5, frequency_high_hz=2.5,
            num_samples=4, duration_s=0.4, dataset_name=name, dataset_split=split,
            label_space=label_space, has_ground_truth=False, dataset_id=dataset.id, sample_key=sample_key,
        ))
    return dataset


def test_list_datasets_paginates(client, session):
    _add_dataset(session, root="D:/SpaceNetA")
    _add_dataset(session, root="D:/SpaceNetB")
    _add_dataset(session, root="D:/SpaceNetC")
    session.commit()

    page = client.get("/api/datasets?limit=2&offset=0").json()
    assert page["total"] == 3 and len(page["items"]) == 2
    rest = client.get("/api/datasets?limit=2&offset=2").json()
    assert rest["total"] == 3 and len(rest["items"]) == 1


def test_get_dataset_and_unknown_404(client, session):
    dataset = _add_dataset(session, count=2)
    session.commit()

    detail = client.get(f"/api/datasets/{dataset.id}").json()
    assert detail["id"] == dataset.id
    assert detail["sample_count"] == 2
    assert detail["portable_fingerprint"] == "a" * 64
    assert set(detail) >= {
        "id", "name", "split", "adapter_id", "label_space", "local_root",
        "portable_fingerprint", "sample_count", "ground_truth_sample_count", "created_at",
    }

    assert client.get("/api/datasets/ds_missing").status_code == 404


def test_samples_paginate_and_search(client, session):
    dataset = _add_dataset(session, count=5)
    session.commit()

    first = client.get(f"/api/datasets/{dataset.id}/samples?limit=2&offset=0").json()
    assert first["dataset_id"] == dataset.id
    assert first["total"] == 5 and len(first["items"]) == 2
    assert all(item["sample_key"] for item in first["items"])

    searched = client.get(f"/api/datasets/{dataset.id}/samples?search=SpaceNet_2").json()
    assert searched["total"] == 1
    assert searched["items"][0]["sample_key"] == "SpaceNet_2"

    assert client.get("/api/datasets/ds_missing/samples").status_code == 404


def test_legacy_data_library_endpoint_still_works_alongside_authority(client, session):
    dataset = _add_dataset(session, count=2)
    session.commit()

    # Legacy projection API keeps returning the legated dataset grouping.
    legacy = client.get("/api/data-library/datasets").json()
    assert legacy["total"] == 1
    assert legacy["items"][0]["dataset_name"] == "SpaceNet"
    assert legacy["items"][0]["sample_count"] == 2

    # New authority API is independent and also available.
    authority = client.get("/api/datasets").json()
    assert authority["total"] == 1
    assert authority["items"][0]["id"] == dataset.id
