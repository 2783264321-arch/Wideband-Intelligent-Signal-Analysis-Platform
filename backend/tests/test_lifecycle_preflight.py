"""Shared dependency preflight blockers, incl. imported-batch semantic state (B10)."""
from app.benchmarks.model import DatasetEvaluationItemModel, DatasetEvaluationModel
from app.lifecycle.preflight import find_recording_blockers, find_run_blockers

from benchmark_fixture import add_recording, add_ground_truth, add_run


def _evaluation(session, evaluation_id, recording_id, run_id):
    session.add(DatasetEvaluationModel(
        id=evaluation_id, name="e", dataset_name="SpaceNet", dataset_split="test",
        label_space="spacenet_14", pipeline_id="p", pipeline_version="1.0", status="completed",
        expected_recordings=1, evaluated_recordings=1, missing_recordings=0, coverage=1.0,
        comparable=True, recording_manifest_hash="0" * 64,
        evaluation_protocol="physical_tf_detection_ap_v2", protocol_config_json={},
    ))
    session.add(DatasetEvaluationItemModel(
        id=f"item_{evaluation_id}", evaluation_id=evaluation_id, manifest_order=0,
        recording_id=recording_id, analysis_run_id=run_id, status="included",
        gt_count=1, prediction_count=1,
    ))


def test_recording_blocker_covers_recording_and_owned_run(session):
    add_recording(session, recording_id="rec_x", name="x")
    add_run(session, run_id="run_x", recording_id="rec_x", executor="local_cpu", status="completed")
    _evaluation(session, "eval_x", "rec_x", "run_x")
    session.commit()

    blockers = find_recording_blockers(session, ["rec_x"])
    kinds = {(blocker.kind, blocker.reference) for blocker in blockers}
    assert ("dataset_evaluation", "recording") in kinds
    assert ("dataset_evaluation", "analysis_run") in kinds


def test_individual_imported_run_delete_is_blocked(session):
    add_recording(session, recording_id="rec_b", name="b")
    fingerprint = "a" * 64
    for index in range(3):
        add_run(
            session, run_id=f"run_b_{index}", recording_id="rec_b", executor="imported",
            status="completed",
            parameters_json={"batch_import": {"import_fingerprint": fingerprint, "item_key": f"k{index}"}},
        )
    session.commit()

    blockers = find_run_blockers(session, ["run_b_0"])
    assert any(blocker.kind == "imported_batch" and blocker.resource_id == fingerprint for blocker in blockers)


def test_complete_imported_batch_set_is_not_blocked(session):
    add_recording(session, recording_id="rec_c", name="c")
    fingerprint = "b" * 64
    for index in range(2):
        add_run(
            session, run_id=f"run_c_{index}", recording_id="rec_c", executor="imported",
            status="completed",
            parameters_json={"batch_import": {"import_fingerprint": fingerprint, "item_key": f"k{index}"}},
        )
    session.commit()

    blockers = find_run_blockers(session, ["run_c_0", "run_c_1"])
    assert all(blocker.kind != "imported_batch" for blocker in blockers)


from app.dataset_experiments.model import (
    DatasetExperimentAttemptModel,
    DatasetExperimentItemModel,
    DatasetExperimentModel,
)


def _experiment_attempt(session, *, experiment_id, item_id, attempt_id, recording_id, run_id):
    session.add(DatasetExperimentModel(
        id=experiment_id, name="e", dataset_name="SpaceNet", dataset_split="test",
        dataset_label_space="spacenet_14", recording_manifest_hash="0" * 64,
        plugin_id="p", plugin_version="1.0", parameters_json={}, executor="local_cpu",
        runtime_descriptor_json={}, evaluation_protocol="physical_tf_detection_ap_v2",
        max_concurrency=1, status="running",
    ))
    session.add(DatasetExperimentItemModel(
        id=item_id, experiment_id=experiment_id, manifest_order=0,
        recording_id=recording_id, status="running",
    ))
    session.add(DatasetExperimentAttemptModel(
        id=attempt_id, experiment_item_id=item_id, attempt_number=1, analysis_run_id=run_id,
    ))


def test_attempt_reference_emits_attempt_blocker(session):
    add_recording(session, recording_id="rec_att", name="att")
    add_run(session, run_id="run_att", recording_id="rec_att", executor="local_cpu", status="completed")
    _experiment_attempt(session, experiment_id="exp_att", item_id="item_att",
                        attempt_id="attempt_att", recording_id="rec_att", run_id="run_att")
    session.commit()

    blockers = find_run_blockers(session, ["run_att"])
    attempt_blockers = [b for b in blockers if b.kind == "dataset_experiment_attempt"]
    assert len(attempt_blockers) == 1
    assert attempt_blockers[0].resource_id == "attempt_att"
    assert attempt_blockers[0].reference == "analysis_run"


def test_recording_parent_exposes_attempt_blocker(session):
    add_recording(session, recording_id="rec_att2", name="att2")
    add_run(session, run_id="run_att2", recording_id="rec_att2", executor="local_cpu", status="completed")
    _experiment_attempt(session, experiment_id="exp_att2", item_id="item_att2",
                        attempt_id="attempt_att2", recording_id="rec_att2", run_id="run_att2")
    session.commit()

    blockers = find_recording_blockers(session, ["rec_att2"])
    kinds = {(b.kind, b.resource_id) for b in blockers}
    assert ("dataset_experiment", "exp_att2") in kinds
    assert ("dataset_experiment_attempt", "attempt_att2") in kinds
