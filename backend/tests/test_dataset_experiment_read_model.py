import pytest
from pydantic import ValidationError

from benchmark_fixture import add_ground_truth, add_recording

from app.analysis.model import AnalysisRunModel
from app.core.errors import PlatformError
from app.dataset_experiments.model import (
    DatasetExperimentAttemptModel,
    DatasetExperimentItemModel,
    DatasetExperimentModel,
)
from app.dataset_experiments.schema import DatasetExperimentCreate
from app.dataset_experiments.service import DatasetExperimentService


def test_create_request_rejects_platform_frozen_fields():
    base = dict(
        name="e", dataset_name="SpaceNet", dataset_split="test",
        dataset_label_space="spacenet_14", plugin_id="p", plugin_version="1.0",
        executor="local_cpu", max_concurrency=1,
    )
    DatasetExperimentCreate(**base)  # accepts
    for forbidden in ("recording_manifest_hash", "asset_manifest_sha256", "runtime_descriptor_json"):
        with pytest.raises(ValidationError):
            DatasetExperimentCreate(**base, **{forbidden: "x"})


def test_create_request_requires_max_concurrency_at_least_one():
    with pytest.raises(ValidationError):
        DatasetExperimentCreate(
            name="e", dataset_name="SpaceNet", dataset_split="test",
            dataset_label_space="spacenet_14", plugin_id="p", plugin_version="1.0",
            executor="local_cpu", max_concurrency=0,
        )


def test_create_request_defaults_protocol_and_parameters():
    payload = DatasetExperimentCreate(
        name="e", dataset_name="SpaceNet", dataset_split="test",
        dataset_label_space="spacenet_14", plugin_id="p", plugin_version="1.0",
        executor="local_cpu", max_concurrency=2,
    )
    assert payload.parameters == {}
    assert payload.evaluation_protocol == "physical_tf_detection_ap_v2"
    assert payload.model_release_id is None


def _seed_experiment(client):
    database = client.app.state.database
    with database.session_factory() as session:
        add_recording(session, recording_id="rec_a", name="a")
        add_recording(session, recording_id="rec_b", name="b")
        add_recording(session, recording_id="rec_c", name="c")
        add_ground_truth(session, gt_id="gt_a", recording_id="rec_a", class_id=9,
                         class_name="LoRa 250kHz", t0=0.01, t1=0.02,
                         f0=2_440_600_000.0, f1=2_440_700_000.0)
        add_ground_truth(session, gt_id="gt_b", recording_id="rec_b", class_id=6,
                         class_name="BLE LE1M", t0=0.03, t1=0.04,
                         f0=2_440_800_000.0, f1=2_440_900_000.0)
        add_ground_truth(session, gt_id="gt_c", recording_id="rec_c", class_id=9,
                         class_name="LoRa 250kHz", t0=0.05, t1=0.06,
                         f0=2_441_000_000.0, f1=2_441_100_000.0)
        session.add(DatasetExperimentModel(
            id="exp_1", name="e", dataset_name="SpaceNet", dataset_split="test",
            dataset_label_space="spacenet_14", recording_manifest_hash="a" * 64,
            plugin_id="p", plugin_version="1.0", model_release_id=None,
            asset_manifest_sha256=None, executor="local_cpu",
            runtime_descriptor_json={"executor": "local_cpu", "device_type": "cpu",
                                     "device_index": None, "precision": "float32",
                                     "environment_ref": "x", "environment_label": "x"},
            parameters_json={}, evaluation_protocol="physical_tf_detection_ap_v2",
            max_concurrency=2, status="pending",
        ))
        session.add_all([
            DatasetExperimentItemModel(id="ei_1", experiment_id="exp_1", manifest_order=0,
                                       recording_id="rec_a", status="completed"),
            DatasetExperimentItemModel(id="ei_2", experiment_id="exp_1", manifest_order=1,
                                       recording_id="rec_b", status="failed"),
            DatasetExperimentItemModel(id="ei_3", experiment_id="exp_1", manifest_order=2,
                                       recording_id="rec_c", status="running"),
        ])
        session.commit()
    return database


def test_counts_are_derived_from_item_rows(client):
    database = _seed_experiment(client)
    with database.session_factory() as session:
        read = DatasetExperimentService(session, None, None, None).get_experiment("exp_1")
    assert (read.expected_items, read.queued_items, read.running_items,
            read.completed_items, read.failed_items) == (3, 0, 1, 1, 1)
    assert read.attempt_count == 0


def test_attempt_count_is_derived(client):
    database = _seed_experiment(client)
    with database.session_factory() as session:
        session.add(AnalysisRunModel(
            id="run_x", recording_id="rec_a", pipeline_id="p", pipeline_version="1.0",
            executor="local_cpu", status="completed", parameters_json={},
        ))
        session.add(DatasetExperimentAttemptModel(
            id="ea_1", experiment_item_id="ei_1", attempt_number=1,
            analysis_run_id="run_x", launch_requested_at=None,
        ))
        session.commit()
    with database.session_factory() as session:
        read = DatasetExperimentService(session, None, None, None).get_experiment("exp_1")
    assert read.attempt_count == 1


def test_read_does_not_persist_counts():
    columns = set(DatasetExperimentModel.__table__.columns.keys())
    item_columns = set(DatasetExperimentItemModel.__table__.columns.keys())
    assert columns.isdisjoint(
        {"queued_items", "running_items", "completed_items", "failed_items", "attempt_count"}
    )
    assert item_columns.isdisjoint({"current_analysis_run_id", "attempt_count"})


def test_get_missing_experiment_raises(client):
    with client.app.state.database.session_factory() as session:
        with pytest.raises(PlatformError) as exc:
            DatasetExperimentService(session, None, None, None).get_experiment("missing")
    assert exc.value.code == "DATASET_EXPERIMENT_NOT_FOUND"


def test_list_items_ordered_by_manifest_order_and_enriched_with_recording_name(client):
    database = _seed_experiment(client)
    with database.session_factory() as session:
        items = DatasetExperimentService(session, None, None, None).list_items("exp_1")
    assert [item.manifest_order for item in items] == [0, 1, 2]
    assert [item.recording_id for item in items] == ["rec_a", "rec_b", "rec_c"]
    assert [item.recording_name for item in items] == ["a", "b", "c"]


def test_list_items_orders_by_manifest_order_regardless_of_insertion_order(client):
    database = client.app.state.database
    with database.session_factory() as session:
        add_recording(session, recording_id="rec_x", name="x")
        add_recording(session, recording_id="rec_y", name="y")
        add_recording(session, recording_id="rec_z", name="z")
        session.add(DatasetExperimentModel(
            id="exp_order", name="order", dataset_name="SpaceNet", dataset_split="test",
            dataset_label_space="spacenet_14", recording_manifest_hash="b" * 64,
            plugin_id="p", plugin_version="1.0", model_release_id=None,
            asset_manifest_sha256=None, executor="local_cpu",
            runtime_descriptor_json={"executor": "local_cpu"},
            parameters_json={}, evaluation_protocol="physical_tf_detection_ap_v2",
            max_concurrency=1, status="pending",
        ))
        session.add_all([
            DatasetExperimentItemModel(id="eo_2", experiment_id="exp_order", manifest_order=2,
                                       recording_id="rec_z", status="queued"),
            DatasetExperimentItemModel(id="eo_0", experiment_id="exp_order", manifest_order=0,
                                       recording_id="rec_x", status="queued"),
            DatasetExperimentItemModel(id="eo_1", experiment_id="exp_order", manifest_order=1,
                                       recording_id="rec_y", status="queued"),
        ])
        session.commit()
    with database.session_factory() as session:
        items = DatasetExperimentService(session, None, None, None).list_items("exp_order")
    assert [item.manifest_order for item in items] == [0, 1, 2]
    assert [item.recording_id for item in items] == ["rec_x", "rec_y", "rec_z"]


def test_list_attempts_ordered_by_attempt_number(client):
    database = _seed_experiment(client)
    with database.session_factory() as session:
        session.add_all([
            AnalysisRunModel(id="run_1", recording_id="rec_a", pipeline_id="p",
                             pipeline_version="1.0", executor="local_cpu",
                             status="failed", parameters_json={}),
            AnalysisRunModel(id="run_2", recording_id="rec_a", pipeline_id="p",
                             pipeline_version="1.0", executor="local_cpu",
                             status="completed", parameters_json={}),
        ])
        session.add_all([
            DatasetExperimentAttemptModel(id="ea_2", experiment_item_id="ei_1", attempt_number=2,
                                          analysis_run_id="run_2", launch_requested_at=None),
            DatasetExperimentAttemptModel(id="ea_1", experiment_item_id="ei_1", attempt_number=1,
                                          analysis_run_id="run_1", launch_requested_at=None),
        ])
        session.commit()
    with database.session_factory() as session:
        attempts = DatasetExperimentService(session, None, None, None).list_attempts("ei_1")
    assert [attempt.attempt_number for attempt in attempts] == [1, 2]
    assert [attempt.analysis_run_id for attempt in attempts] == ["run_1", "run_2"]


def test_list_attempts_missing_item_raises(client):
    with client.app.state.database.session_factory() as session:
        with pytest.raises(PlatformError) as exc:
            DatasetExperimentService(session, None, None, None).list_attempts("missing-item")
    assert exc.value.code == "DATASET_EXPERIMENT_ITEM_NOT_FOUND"
