"""Data Library read API: projection list/detail, samples, standalone, and
analysis history incl. imported BAPv1 (B3, B4, B5)."""
from pathlib import Path

from app.ground_truth.model import GroundTruthModel
from app.recordings.model import RecordingModel

from benchmark_fixture import add_run


def _add_member(session, root, name, *, gt=True, source="spacenet", dataset_name="SpaceNet",
                split="test", label_space="spacenet_14"):
    data_path = root / split / f"{name}.bin"
    recording = RecordingModel(
        id=f"rec_{root.name}_{name}", name=name, data_path=str(data_path),
        data_format="float16_interleaved_le", source=source, external_path=str(data_path),
        sample_rate_hz=1.0, center_frequency_hz=2.0, frequency_low_hz=1.5,
        frequency_high_hz=2.5, num_samples=1, duration_s=1.0,
        dataset_name=dataset_name, dataset_split=split, label_space=label_space,
        has_ground_truth=gt,
    )
    session.add(recording)
    if gt:
        session.add(GroundTruthModel(
            id=f"gt_{recording.id}", recording_id=recording.id, t_start_s=0.1, t_end_s=0.2,
            f_low_hz=1.6, f_high_hz=1.7, class_id=1, class_name="WiFi",
        ))
    return recording


def _add_standalone(session, name):
    session.add(RecordingModel(
        id=f"rec_standalone_{name}", name=name, data_path=f"recordings/rec_standalone_{name}/raw.iq",
        data_format="complex64_le", source="custom", external_path=None,
        sample_rate_hz=1.0, center_frequency_hz=0.0, frequency_low_hz=-0.5, frequency_high_hz=0.5,
        num_samples=1, duration_s=1.0, dataset_name=None, dataset_split=None, label_space=None,
        has_ground_truth=False,
    ))


def test_projection_list_and_detail(client, session, tmp_path):
    root_a = tmp_path / "SpaceNet-A"
    root_b = tmp_path / "SpaceNet-B"
    a = [_add_member(session, root_a, "a1"), _add_member(session, root_a, "a2")]
    for name in ["b1", "b2", "b3"]:
        _add_member(session, root_b, name)
    session.commit()
    from app.datasets.projection import DatasetProjectionResolver
    pid_a = DatasetProjectionResolver(session).find_for_recording(a[0]).dataset_projection_id

    listing = client.get("/api/data-library/datasets").json()
    assert listing["total"] == 2
    assert {item["sample_count"] for item in listing["items"]} == {2, 3}

    detail = client.get(f"/api/data-library/datasets/{pid_a}").json()
    assert detail["sample_count"] == 2
    assert detail["ground_truth_sample_count"] == 2
    assert detail["dataset_name"] == "SpaceNet"

    assert client.get("/api/data-library/datasets/dsproj_missing").status_code == 404


def test_dataset_samples_paginate_and_search(client, session, tmp_path):
    root = tmp_path / "SpaceNet-P"
    for index in range(5):
        _add_member(session, root, f"sample_{index}")
    session.commit()
    from app.datasets.projection import DatasetProjectionResolver
    member = session.query(RecordingModel).order_by(RecordingModel.name).first()
    pid = DatasetProjectionResolver(session).find_for_recording(member).dataset_projection_id

    page = client.get(f"/api/data-library/datasets/{pid}/samples?limit=2&offset=0").json()
    assert page["total"] == 5
    assert len(page["items"]) == 2

    search = client.get(f"/api/data-library/datasets/{pid}/samples?search=sample_3").json()
    assert search["total"] == 1
    assert search["items"][0]["name"] == "sample_3"
    assert search["items"][0]["sample_rate_derived"] is True


def test_standalone_samples_exclude_dataset_members(client, session, tmp_path):
    root = tmp_path / "SpaceNet-X"
    _add_member(session, root, "member_1")
    _add_standalone(session, "standalone_1")
    session.commit()

    body = client.get("/api/data-library/standalone-samples").json()
    names = {item["name"] for item in body["items"]}
    assert names == {"standalone_1"}


def test_standalone_samples_are_newest_first(client, session, tmp_path):
    from datetime import datetime, timedelta, timezone

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for index, name in enumerate(["older", "middle", "newest"]):
        recording = RecordingModel(
            id=f"rec_standalone_{name}", name=name,
            data_path=f"recordings/rec_standalone_{name}/raw.iq",
            data_format="complex64_le", source="custom", external_path=None,
            sample_rate_hz=1.0, center_frequency_hz=0.0, frequency_low_hz=-0.5,
            frequency_high_hz=0.5, num_samples=1, duration_s=1.0, dataset_name=None,
            dataset_split=None, label_space=None, has_ground_truth=False,
            created_at=base + timedelta(minutes=index),
        )
        session.add(recording)
    session.commit()

    body = client.get("/api/data-library/standalone-samples").json()
    names = [item["name"] for item in body["items"]]
    assert names == ["newest", "middle", "older"]


def test_analysis_history_includes_imported_batch_before_evaluation(client, session, tmp_path):
    root = tmp_path / "SpaceNet-H"
    members = [_add_member(session, root, "h1"), _add_member(session, root, "h2")]
    session.commit()
    from app.datasets.projection import DatasetProjectionResolver
    pid = DatasetProjectionResolver(session).find_for_recording(members[0]).dataset_projection_id

    fingerprint = "c" * 64
    for index, recording in enumerate(members):
        add_run(
            session, run_id=f"run_h_{index}", recording_id=recording.id,
            pipeline_id="zoomspec", pipeline_version="1.0", executor="imported", status="completed",
            parameters_json={"batch_import": {
                "import_fingerprint": fingerprint, "item_key": f"key_{index}",
                "batch_id": "batch_h", "archive_sha256": "d" * 64,
            }},
        )
    session.commit()

    history = client.get(f"/api/data-library/datasets/{pid}/analysis-history").json()
    kinds = [item["kind"] for item in history["items"]]
    assert "imported_batch" in kinds
    batch = next(item for item in history["items"] if item["kind"] == "imported_batch")
    assert batch["executor"] == "imported"
    assert batch["completed_items"] == 2
    assert batch["expected_items"] == 2
    assert batch["dataset_evaluation_id"] is None
    assert batch["name"] == "zoomspec 1.0"
    # no evaluation exists yet
    assert all(not item["dataset_evaluation_id"] for item in history["items"] if item["kind"] == "imported_batch")
