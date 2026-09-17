"""P3: Dataset Analysis membership is independent of Evaluation (GT).

Proves ALL Dataset members participate regardless of Ground Truth, dataset_id is
authoritative, and Evaluation is only produced for complete GT coverage.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from benchmark_fixture import add_ground_truth, add_recording

from app.analysis.model import AnalysisRunModel
from app.analysis.service import AnalysisService
from app.benchmarks.model import DatasetEvaluationModel
from app.benchmarks.service import DatasetBenchmarkService
from app.core.errors import PlatformError
from app.dataset_experiments.coordinator import CoordinatorOutcome, DatasetExperimentCoordinator
from app.dataset_experiments.model import (
    DatasetExperimentAttemptModel,
    DatasetExperimentItemModel,
    DatasetExperimentModel,
)
from app.dataset_experiments.service import DatasetExperimentService
from app.datasets.analysis_manifest import build_dataset_analysis_manifest
from app.datasets.model import DatasetModel
from app.recordings.model import RecordingModel

PIPELINE = "stft_energy_detector"
VERSION = "1.0"
PROTOCOL = "physical_tf_detection_ap_v2"


def _dataset_with_members(session, dataset_id, *, total, gt_count, name="SpaceNet"):
    session.add(DatasetModel(
        id=dataset_id, name=name, split="test", adapter_id="spacenet", label_space="spacenet_14",
        local_root=f"D:/{dataset_id}", portable_fingerprint="a" * 64,
        sample_count=total, ground_truth_sample_count=gt_count,
    ))
    for index in range(total):
        rid = f"rec_{dataset_id}_{index}"
        add_recording(
            session, recording_id=rid, name=f"{dataset_id}_{index}",
            has_ground_truth=index < gt_count,
        )
        recording = session.get(RecordingModel, rid)
        recording.dataset_id = dataset_id
        recording.sample_key = f"{index:04d}"
        if index < gt_count:
            add_ground_truth(
                session, gt_id=f"gt_{dataset_id}_{index}", recording_id=rid, class_id=9,
                class_name="LoRa 250kHz", t0=0.01, t1=0.02,
                f0=2_440_600_000.0, f1=2_440_700_000.0,
            )
    session.commit()
    return dataset_id


def _items(experiment_id):
    return (
        select(DatasetExperimentItemModel)
        .where(DatasetExperimentItemModel.experiment_id == experiment_id)
        .order_by(DatasetExperimentItemModel.manifest_order)
    )


def _service(client, session):
    return DatasetExperimentService(
        session,
        client.app.state.pipeline_registry,
        client.app.state.model_release_store,
        client.app.state.executor_registry,
    )


def _create(client, session, dataset_id, *, mode="manual"):
    return _service(client, session).create_experiment(
        name="analysis", dataset_id=dataset_id,
        plugin_id=PIPELINE, plugin_version=VERSION,
        executor=None if mode == "auto" else "local_cpu",
        parameters={}, evaluation_protocol=PROTOCOL, max_concurrency=1,
        execution_mode=mode,
    )


# ---------- manifest: ALL members regardless of GT ----------

@pytest.mark.parametrize("gt_count", [0, 42, 100])
def test_manifest_always_includes_all_samples(session, gt_count):
    dataset_id = _dataset_with_members(session, f"ds_manifest_{gt_count}", total=100, gt_count=gt_count)
    manifest = build_dataset_analysis_manifest(session, dataset_id)
    assert manifest.expected_recordings == 100
    assert len(manifest.entries) == 100
    assert sum(1 for entry in manifest.entries if entry.gt_count > 0) == gt_count


def test_manifest_ordering_is_stable_by_sample_key(session):
    dataset_id = _dataset_with_members(session, "ds_order", total=3, gt_count=3)
    manifest = build_dataset_analysis_manifest(session, dataset_id)
    assert [entry.recording_name for entry in manifest.entries] == [
        "0000", "0001", "0002",
    ]


# ---------- create: dataset_id authority ----------

def test_create_with_dataset_id_derives_identity_and_persists(client, session):
    dataset_id = _dataset_with_members(session, "ds_create", total=3, gt_count=1)
    experiment = _create(client, session, dataset_id)
    assert experiment.dataset_id == dataset_id
    assert experiment.dataset_name == "SpaceNet"
    assert experiment.dataset_split == "test"
    assert experiment.dataset_label_space == "spacenet_14"
    items = list(session.scalars(_items(experiment.id)).all())
    assert len(items) == 3  # ALL members, including the 2 without GT


def test_create_with_dataset_id_rejects_conflicting_legacy_fields(client, session):
    dataset_id = _dataset_with_members(session, "ds_conflict", total=2, gt_count=2)
    with pytest.raises(PlatformError) as excinfo:
        _service(client, session).create_experiment(
            name="analysis", dataset_id=dataset_id, dataset_name="NotSpaceNet",
            plugin_id=PIPELINE, plugin_version=VERSION, executor="local_cpu",
            parameters={}, evaluation_protocol=PROTOCOL, max_concurrency=1,
        )
    assert excinfo.value.code == "EXECUTION_REQUEST_INVALID"


def test_standard_local_cpu_auto_valid_for_stft_energy(client, session):
    dataset_id = _dataset_with_members(session, "ds_auto", total=2, gt_count=2)
    experiment = _create(client, session, dataset_id, mode="auto")
    assert experiment.executor == "local_cpu"


# ---------- revalidation uses dataset membership ----------

def test_revalidation_uses_dataset_membership_and_detects_change(client, session):
    dataset_id = _dataset_with_members(session, "ds_reval", total=2, gt_count=2)
    experiment = _create(client, session, dataset_id)
    service = _service(client, session)
    service.revalidate_frozen_identity(experiment.id)  # unchanged -> ok

    # Add a new member -> frozen manifest no longer matches.
    add_recording(session, recording_id="rec_ds_reval_extra", name="ds_reval_extra")
    session.get(RecordingModel, "rec_ds_reval_extra").dataset_id = dataset_id
    session.get(RecordingModel, "rec_ds_reval_extra").sample_key = "9999"
    session.commit()
    with pytest.raises(PlatformError) as excinfo:
        service.revalidate_frozen_identity(experiment.id)
    assert excinfo.value.code == "DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED"


# ---------- Analysis vs Evaluation states ----------

def test_evaluation_eligibility_requires_complete_gt(client, session):
    ids = {
        "full": _dataset_with_members(session, "ds_full", total=2, gt_count=2),
        "partial": _dataset_with_members(session, "ds_partial", total=2, gt_count=1),
        "none": _dataset_with_members(session, "ds_none", total=2, gt_count=0),
    }
    service = _service(client, session)
    for key, dataset_id in ids.items():
        experiment = _create(client, session, dataset_id)
        eligible = service.experiment_evaluation_eligible(experiment.id)
        assert eligible is (key == "full")


def _complete_items(session, experiment):
    items = list(session.scalars(_items(experiment.id)).all())
    for order, item in enumerate(items):
        run_id = f"run_{experiment.id}_{order}"
        session.add(AnalysisRunModel(
            id=run_id, recording_id=item.recording_id, pipeline_id=PIPELINE,
            pipeline_version=VERSION, executor="local_cpu", status="completed",
            parameters_json={},
        ))
        session.add(DatasetExperimentAttemptModel(
            id=f"att_{experiment.id}_{order}", experiment_item_id=item.id,
            attempt_number=1, analysis_run_id=run_id,
        ))
        item.status = "completed"
    session.commit()


class StubBenchmarkJobManager:
    def start(self, evaluation_id):
        return 9090


def _coordinator(client):
    def services_factory(session):
        return (
            _service(client, session),
            AnalysisService(
                session, client.app.state.pipeline_registry, client.app.state.job_manager,
                executor_registry=client.app.state.executor_registry,
            ),
        )

    return DatasetExperimentCoordinator(
        session_factory=client.app.state.database.session_factory,
        services_factory=services_factory,
        benchmark_services_factory=lambda session: DatasetBenchmarkService(session),
        benchmark_job_manager=StubBenchmarkJobManager(),
    )


def test_no_gt_dataset_completes_without_evaluation(client, session):
    dataset_id = _dataset_with_members(session, "ds_nogt_run", total=2, gt_count=0)
    experiment = _create(client, session, dataset_id)
    _complete_items(session, experiment)
    experiment.status = "running"
    experiment.coordinator_token = "coord_nogt"
    session.commit()

    outcome = _coordinator(client).step(experiment.id, "coord_nogt")
    session.expire_all()
    experiment = session.get(DatasetExperimentModel, experiment.id)
    assert outcome == CoordinatorOutcome.EXPERIMENT_COMPLETED
    assert experiment.status == "completed"
    assert experiment.dataset_evaluation_id is None


def test_partial_gt_dataset_completes_without_whole_dataset_evaluation(client, session):
    dataset_id = _dataset_with_members(session, "ds_partial_run", total=3, gt_count=1)
    experiment = _create(client, session, dataset_id)
    _complete_items(session, experiment)
    experiment.status = "running"
    experiment.coordinator_token = "coord_partial"
    session.commit()

    outcome = _coordinator(client).step(experiment.id, "coord_partial")
    session.expire_all()
    experiment = session.get(DatasetExperimentModel, experiment.id)
    assert outcome == CoordinatorOutcome.EXPERIMENT_COMPLETED
    assert experiment.status == "completed"
    assert experiment.dataset_evaluation_id is None


def test_complete_gt_dataset_links_evaluation_carrying_dataset_id(client, session):
    dataset_id = _dataset_with_members(session, "ds_full_run", total=2, gt_count=2)
    experiment = _create(client, session, dataset_id)
    _complete_items(session, experiment)
    experiment.status = "running"
    experiment.coordinator_token = "coord_full"
    session.commit()

    outcome = _coordinator(client).step(experiment.id, "coord_full")
    session.expire_all()
    experiment = session.get(DatasetExperimentModel, experiment.id)
    assert outcome == CoordinatorOutcome.WAITING
    assert experiment.status == "evaluating"
    assert experiment.dataset_evaluation_id is not None
    evaluation = session.get(DatasetEvaluationModel, experiment.dataset_evaluation_id)
    assert evaluation.dataset_id == dataset_id


# ---------- list filter + legacy compatibility ----------

def test_list_experiments_filters_by_dataset_id(client, session):
    a = _dataset_with_members(session, "ds_list_a", total=1, gt_count=0)
    b = _dataset_with_members(session, "ds_list_b", total=1, gt_count=0)
    exp_a = _create(client, session, a)
    _create(client, session, b)
    service = _service(client, session)
    filtered = service.list_experiments(dataset_id=a)
    assert [item.id for item in filtered] == [exp_a.id]
    assert filtered[0].dataset_id == a
    assert len(service.list_experiments()) >= 2


def test_legacy_triple_experiment_still_works(client, session):
    _dataset_with_members(session, "ds_legacy", total=2, gt_count=2)
    # Mimic legacy creation through the dataset_name/split/label_space triple.
    experiment = _service(client, session).create_experiment(
        name="legacy", dataset_name="SpaceNet", dataset_split="test",
        dataset_label_space="spacenet_14", plugin_id=PIPELINE, plugin_version=VERSION,
        executor="local_cpu", parameters={}, evaluation_protocol=PROTOCOL, max_concurrency=1,
    )
    assert experiment.dataset_id is None
    assert experiment.status == "pending"
