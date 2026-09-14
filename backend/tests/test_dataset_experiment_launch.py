from datetime import datetime, timezone

import pytest

from benchmark_fixture import add_ground_truth, add_recording

from app.analysis.model import AnalysisRunModel
from app.analysis.service import AnalysisService
from app.core.errors import PlatformError
from app.dataset_experiments.model import (
    DatasetExperimentAttemptModel,
    DatasetExperimentItemModel,
    DatasetExperimentModel,
)
from app.dataset_experiments.service import DatasetExperimentService
from app.pipelines.base import ExecutionCapability, Pipeline, PipelineDefinition, PipelineOutput
from app.pipelines.registry import PipelineRegistry

from executor_fixtures import FakeProvider, FakeRegistry


class G3LocalPipeline(Pipeline):
    @property
    def definition(self) -> PipelineDefinition:
        return PipelineDefinition(
            id="g3_local", name="G3 Local", version="1.0", label_space="spacenet_14",
            recommended_device="CPU", cpu_supported=True, stages=(), inspectable_stages=(),
            task_capability="classification", executors_supported=("local_cpu",),
            technical_execution_capabilities=(ExecutionCapability("local_cpu", "cpu", "float32"),),
            parameter_schema={},
        )

    def run(self, recording, parameters, workspace) -> PipelineOutput:
        raise AssertionError("must not execute")


class RaisingLocalProvider(FakeProvider):
    def launch(self, run_id, *, coordinator_token):
        raise RuntimeError("boom")


def _seed_dataset(client, count=2):
    with client.app.state.database.session_factory() as session:
        for index in range(count):
            rid = f"rec_{index}"
            add_recording(session, recording_id=rid, name=f"name_{index}")
            add_ground_truth(session, gt_id=f"gt_{index}", recording_id=rid, class_id=9,
                             class_name="LoRa 250kHz", t0=0.01, t1=0.02,
                             f0=2_440_600_000.0, f1=2_440_700_000.0)
        session.commit()


def _services(client, *, provider=None):
    session = client.app.state.database.session_factory()
    provider = provider or FakeProvider("local_cpu")
    registry = PipelineRegistry([G3LocalPipeline()])
    executor_registry = FakeRegistry({"local_cpu": provider})
    ds = DatasetExperimentService(session, registry, None, executor_registry)
    analysis = AnalysisService(
        session, registry, client.app.state.job_manager, executor_registry=executor_registry,
    )
    return session, ds, analysis, provider


def _experiment(ds, *, status="running"):
    experiment = ds.create_experiment(
        name="g3", dataset_name="SpaceNet", dataset_split="test",
        dataset_label_space="spacenet_14", plugin_id="g3_local", plugin_version="1.0",
        executor="local_cpu", parameters={},
        evaluation_protocol="physical_tf_detection_ap_v2", max_concurrency=1,
    )
    if status != "pending":
        experiment.status = status
        ds.session.commit()
    return experiment


def _item(session, experiment_id, order=0):
    return session.query(DatasetExperimentItemModel).filter_by(
        experiment_id=experiment_id, manifest_order=order
    ).one()


def _owned_attempt(client):
    _seed_dataset(client)
    session, ds, analysis, provider = _services(client)
    experiment = _experiment(ds)
    item = _item(session, experiment.id)
    attempt = ds.start_item_attempt(
        experiment_id=experiment.id, item_id=item.id, analysis_service=analysis,
    )
    return session, ds, analysis, provider, experiment, item, attempt


def _launch(ds, experiment, item, attempt, analysis):
    return ds.launch_item_attempt(
        experiment_id=experiment.id, item_id=item.id,
        attempt_id=attempt.id, analysis_service=analysis,
    )


def test_local_launch_commits_intent_before_physical_launch(client, monkeypatch):
    session, ds, analysis, provider, experiment, item, attempt = _owned_attempt(client)
    events = []
    real_commit = session.commit
    real_launch = analysis.launch_prepared_run

    def spy_commit():
        events.append("commit")
        return real_commit()

    def spy_launch(run_id):
        with client.app.state.database.session_factory() as fresh:
            assert fresh.get(DatasetExperimentAttemptModel, attempt.id).launch_requested_at is not None
        events.append("launch")
        return real_launch(run_id)

    monkeypatch.setattr(session, "commit", spy_commit)
    monkeypatch.setattr(analysis, "launch_prepared_run", spy_launch)

    launched = _launch(ds, experiment, item, attempt, analysis)

    assert events[0] == "commit"      # Transaction B committed first
    assert events[1] == "launch"      # physical launch second
    assert launched.launch_requested_at is not None
    assert provider.launches == [(launched.analysis_run_id, None)]  # exactly one local launch


def test_local_launch_records_intent_and_worker_pid(client):
    session, ds, analysis, provider, experiment, item, attempt = _owned_attempt(client)
    _launch(ds, experiment, item, attempt, analysis)
    with client.app.state.database.session_factory() as fresh:
        stored_attempt = fresh.get(DatasetExperimentAttemptModel, attempt.id)
        stored_run = fresh.get(AnalysisRunModel, attempt.analysis_run_id)
        assert stored_attempt.launch_requested_at is not None
        assert stored_run.status == "pending"
        assert stored_run.worker_pid is not None


def test_transaction_b_commit_failure_does_not_launch(client, monkeypatch):
    session, ds, analysis, provider, experiment, item, attempt = _owned_attempt(client)
    launches = []
    monkeypatch.setattr(analysis, "launch_prepared_run", lambda run_id: launches.append(run_id))

    def boom():
        raise RuntimeError("transaction b commit failed")

    monkeypatch.setattr(session, "commit", boom)
    with pytest.raises(RuntimeError):
        _launch(ds, experiment, item, attempt, analysis)
    assert launches == []
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetExperimentAttemptModel, attempt.id).launch_requested_at is None
        assert fresh.get(AnalysisRunModel, attempt.analysis_run_id).status == "pending"


def test_already_requested_local_attempt_cannot_first_launch_again(client):
    session, ds, analysis, provider, experiment, item, attempt = _owned_attempt(client)
    _launch(ds, experiment, item, attempt, analysis)
    with pytest.raises(PlatformError) as exc:
        _launch(ds, experiment, item, attempt, analysis)
    assert exc.value.code == "DATASET_EXPERIMENT_INVARIANT_VIOLATION"
    assert provider.launches == [(attempt.analysis_run_id, None)]  # no second launch


def test_launch_requires_same_session(client):
    session, ds, analysis, provider, experiment, item, attempt = _owned_attempt(client)
    other_session = client.app.state.database.session_factory()
    other_analysis = AnalysisService(
        other_session, PipelineRegistry([G3LocalPipeline()]), client.app.state.job_manager,
        executor_registry=FakeRegistry({"local_cpu": FakeProvider("local_cpu")}),
    )
    with pytest.raises(PlatformError) as exc:
        _launch(ds, experiment, item, attempt, other_analysis)
    assert exc.value.code == "DATASET_EXPERIMENT_INVARIANT_VIOLATION"
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetExperimentAttemptModel, attempt.id).launch_requested_at is None


def test_launch_rejects_ownership_mismatch(client):
    session, ds, analysis, provider, experiment, item, attempt = _owned_attempt(client)
    with pytest.raises(PlatformError) as exc:
        ds.launch_item_attempt(
            experiment_id=experiment.id, item_id="other-item",
            attempt_id=attempt.id, analysis_service=analysis,
        )
    assert exc.value.code == "DATASET_EXPERIMENT_ITEM_NOT_FOUND"
    assert provider.launches == []


def test_launch_rejects_run_with_worker_pid_before_transaction_b(client):
    session, ds, analysis, provider, experiment, item, attempt = _owned_attempt(client)
    run = session.get(AnalysisRunModel, attempt.analysis_run_id)
    run.status = "pending"
    run.worker_pid = 1
    session.commit()
    with pytest.raises(PlatformError) as exc:
        _launch(ds, experiment, item, attempt, analysis)
    assert exc.value.code == "DATASET_EXPERIMENT_INVARIANT_VIOLATION"
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetExperimentAttemptModel, attempt.id).launch_requested_at is None
    assert provider.launches == []


def test_launch_rejects_non_pending_run(client):
    session, ds, analysis, provider, experiment, item, attempt = _owned_attempt(client)
    run = session.get(AnalysisRunModel, attempt.analysis_run_id)
    run.status = "completed"
    session.commit()
    with pytest.raises(PlatformError) as exc:
        _launch(ds, experiment, item, attempt, analysis)
    assert exc.value.code == "DATASET_EXPERIMENT_INVARIANT_VIOLATION"
    assert provider.launches == []


def test_launch_rejects_non_running_item(client):
    session, ds, analysis, provider, experiment, item, attempt = _owned_attempt(client)
    item.status = "completed"
    session.commit()
    with pytest.raises(PlatformError) as exc:
        _launch(ds, experiment, item, attempt, analysis)
    assert exc.value.code == "DATASET_EXPERIMENT_INVARIANT_VIOLATION"
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetExperimentAttemptModel, attempt.id).launch_requested_at is None
    assert provider.launches == []


def test_launch_rejects_pending_experiment(client):
    session, ds, analysis, provider, experiment, item, attempt = _owned_attempt(client)
    experiment.status = "pending"
    session.commit()
    with pytest.raises(PlatformError) as exc:
        _launch(ds, experiment, item, attempt, analysis)
    assert exc.value.code == "DATASET_EXPERIMENT_INVARIANT_VIOLATION"
    assert provider.launches == []


def test_launch_rejects_run_identity_mismatch(client):
    session, ds, analysis, provider, experiment, item, attempt = _owned_attempt(client)
    run = session.get(AnalysisRunModel, attempt.analysis_run_id)
    run.pipeline_id = "other_pipeline"
    session.commit()
    with pytest.raises(PlatformError) as exc:
        _launch(ds, experiment, item, attempt, analysis)
    assert exc.value.code == "DATASET_EXPERIMENT_INVARIANT_VIOLATION"
    assert provider.launches == []


def test_launch_revalidates_frozen_identity(client):
    session, ds, analysis, provider, experiment, item, attempt = _owned_attempt(client)
    experiment.parameters_json = {"unexpected": 1}  # drift vs empty schema
    session.commit()
    with pytest.raises(PlatformError) as exc:
        _launch(ds, experiment, item, attempt, analysis)
    assert exc.value.code == "DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED"
    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetExperimentAttemptModel, attempt.id).launch_requested_at is None
    assert provider.launches == []


def test_launch_failure_preserves_intent_and_failed_run(client):
    _seed_dataset(client)
    raising = RaisingLocalProvider("local_cpu")
    session = client.app.state.database.session_factory()
    registry = PipelineRegistry([G3LocalPipeline()])
    executor_registry = FakeRegistry({"local_cpu": raising})
    ds = DatasetExperimentService(session, registry, None, executor_registry)
    analysis = AnalysisService(session, registry, client.app.state.job_manager,
                               executor_registry=executor_registry)
    experiment = _experiment(ds)
    item = _item(session, experiment.id)
    attempt = ds.start_item_attempt(experiment_id=experiment.id, item_id=item.id,
                                    analysis_service=analysis)

    with pytest.raises(PlatformError) as exc:
        _launch(ds, experiment, item, attempt, analysis)
    assert exc.value.code == "ANALYSIS_FAILED"
    with client.app.state.database.session_factory() as fresh:
        stored_attempt = fresh.get(DatasetExperimentAttemptModel, attempt.id)
        stored_run = fresh.get(AnalysisRunModel, attempt.analysis_run_id)
        assert stored_attempt.launch_requested_at is not None  # intent preserved
        assert stored_run.status == "failed"
        assert stored_run.error_type == "ANALYSIS_FAILED"
        assert stored_run.error_message == "boom"


# ---------------------------------------------------------------------------
# Plan A1 / Task 4 — authority before intent + pending-Run terminalization
# ---------------------------------------------------------------------------


def _owned_attempt_with_token(client, token="T"):
    session, ds, analysis, provider, experiment, item, attempt = _owned_attempt(client)
    experiment.coordinator_token = token
    session.commit()
    return session, ds, analysis, provider, experiment, item, attempt


def test_authority_missing_before_intent_terminalizes_run(client):
    session, ds, analysis, provider, experiment, item, attempt = _owned_attempt_with_token(client)
    run_id = attempt.analysis_run_id
    ds.executor_registry._providers.pop("local_cpu")  # provider disappears

    with pytest.raises(PlatformError) as exc:
        ds.launch_item_attempt(
            experiment_id=experiment.id, item_id=item.id, attempt_id=attempt.id,
            analysis_service=analysis, coordinator_token="T",
        )
    assert exc.value.code == "EXECUTION_CAPABILITY_UNAVAILABLE"
    with client.app.state.database.session_factory() as fresh:
        att = fresh.get(DatasetExperimentAttemptModel, attempt.id)
        run = fresh.get(AnalysisRunModel, run_id)
        itm = fresh.get(DatasetExperimentItemModel, item.id)
    assert att.launch_requested_at is None            # no intent claimed
    assert run.status == "interrupted"                # terminalized
    assert run.error_type == "EXECUTION_CAPABILITY_UNAVAILABLE"
    assert run.worker_pid is None
    assert itm.status == "running"                    # projected failed by reconcile


def test_authority_fail_close_requires_generation(client):
    session, ds, analysis, provider, experiment, item, attempt = _owned_attempt_with_token(client, token="T")
    run_id = attempt.analysis_run_id
    ds.executor_registry._providers.pop("local_cpu")
    experiment.coordinator_token = "T2"               # generation rotated
    session.commit()

    with pytest.raises(PlatformError) as exc:
        ds.launch_item_attempt(
            experiment_id=experiment.id, item_id=item.id, attempt_id=attempt.id,
            analysis_service=analysis, coordinator_token="T",  # stale
        )
    assert exc.value.code == "DATASET_EXPERIMENT_FENCE_LOST"
    with client.app.state.database.session_factory() as fresh:
        run = fresh.get(AnalysisRunModel, run_id)
        att = fresh.get(DatasetExperimentAttemptModel, attempt.id)
    assert run.status == "pending"                    # stale actor terminalized nothing
    assert att.launch_requested_at is None


# ---------------------------------------------------------------------------
# Plan A1.1 — Window C RED reproducer (diagnostic, temporary).
# ---------------------------------------------------------------------------


def test_window_c_runtime_drift_after_transaction_b_before_physical_launch(client):
    """Window C: runtime generation drifts AFTER the DatasetExperiment
    pre-Transaction-B authority check / Transaction-B durable intent commit and
    BEFORE ``AnalysisService.launch_prepared_run`` performs its own local
    authority revalidation.

    Expected (desired) safe invariant: a terminal Experiment must NOT retain a
    running Item. On the current candidate this should reveal the violation.
    """
    from app.dataset_experiments.coordinator import DatasetExperimentCoordinator

    _seed_dataset(client, count=1)
    provider_a = FakeProvider("local_cpu")
    provider_b = FakeProvider("local_cpu", runtime_ref="fake:local_cpu:genB")

    setup_session = client.app.state.database.session_factory()
    registry = PipelineRegistry([G3LocalPipeline()])
    setup_ds = DatasetExperimentService(
        setup_session, registry, None, FakeRegistry({"local_cpu": provider_a})
    )
    experiment = _experiment(setup_ds, status="running")
    experiment.coordinator_token = "T"
    setup_session.commit()
    experiment_id = experiment.id
    setup_session.close()

    def services_factory(session):
        executor_registry = FakeRegistry({"local_cpu": provider_a})
        ds = DatasetExperimentService(session, registry, None, executor_registry)
        analysis = AnalysisService(
            session, registry, client.app.state.job_manager, executor_registry=executor_registry
        )
        real_launch = analysis.launch_prepared_run

        def drifting_launch(run_id):
            # Drift lands AFTER Transaction B committed the launch intent and
            # BEFORE AnalysisService's local frozen-authority revalidation.
            executor_registry._providers["local_cpu"] = provider_b
            return real_launch(run_id)

        analysis.launch_prepared_run = drifting_launch
        return ds, analysis

    coordinator = DatasetExperimentCoordinator(
        session_factory=client.app.state.database.session_factory,
        services_factory=services_factory,
    )
    outcome = coordinator.step(experiment_id, "T")

    with client.app.state.database.session_factory() as fresh:
        stored_experiment = fresh.get(DatasetExperimentModel, experiment_id)
        stored_item = _item(fresh, experiment_id, order=0)
        attempts = (
            fresh.query(DatasetExperimentAttemptModel)
            .filter_by(experiment_item_id=stored_item.id)
            .order_by(DatasetExperimentAttemptModel.attempt_number)
            .all()
        )
        attempt = attempts[-1]
        stored_run = fresh.get(AnalysisRunModel, attempt.analysis_run_id)

    # Window-C preconditions: prove the drift really landed after Transaction B.
    assert attempt.launch_requested_at is not None       # Transaction B committed intent
    assert stored_run.status == "failed"                 # local authority check terminalized the Run
    assert stored_run.error_type == "RUNTIME_DESCRIPTOR_INVALID"
    assert stored_run.worker_pid is None
    assert provider_a.launches == []                     # no physical launch
    assert provider_b.launches == []                     # no physical launch

    # Desired lifecycle invariant: a terminal Experiment must not retain a
    # running Item.
    assert stored_experiment.status == "failed"
    assert stored_item.status == "failed", (
        f"terminal Experiment retained Item status={stored_item.status!r} "
        f"with Run status={stored_run.status!r}"
    )


def test_window_c_stale_generation_does_not_fail_item(client):
    """Window C generation fencing: if the coordinator generation is rotated
    after Transaction B but before the physical launch, a stale actor must not
    mutate the owned Item.

    AnalysisService may still terminalize the Run before the DatasetExperiment
    fence loss is discovered (acceptable under the ownership boundary); the Item
    must remain untouched and the propagated error is
    ``DATASET_EXPERIMENT_FENCE_LOST``.
    """
    _seed_dataset(client, count=1)
    provider_a = FakeProvider("local_cpu")
    provider_b = FakeProvider("local_cpu", runtime_ref="fake:local_cpu:genB")

    session = client.app.state.database.session_factory()
    registry = PipelineRegistry([G3LocalPipeline()])
    executor_registry = FakeRegistry({"local_cpu": provider_a})
    ds = DatasetExperimentService(session, registry, None, executor_registry)
    analysis = AnalysisService(
        session, registry, client.app.state.job_manager, executor_registry=executor_registry
    )
    experiment = _experiment(ds, status="running")
    experiment.coordinator_token = "T"
    session.commit()
    target = _item(session, experiment.id, order=0)
    attempt = ds.start_item_attempt(
        experiment_id=experiment.id, item_id=target.id,
        analysis_service=analysis, coordinator_token="T",
    )

    real_launch = analysis.launch_prepared_run

    def drifting_and_rotating(run_id):
        # Drift A->B after Transaction B; rotate generation so the old token is stale.
        executor_registry._providers["local_cpu"] = provider_b
        with client.app.state.database.session_factory() as other:
            other.get(DatasetExperimentModel, experiment.id).coordinator_token = "T2"
            other.commit()
        return real_launch(run_id)

    analysis.launch_prepared_run = drifting_and_rotating

    with pytest.raises(PlatformError) as exc:
        ds.launch_item_attempt(
            experiment_id=experiment.id, item_id=target.id, attempt_id=attempt.id,
            analysis_service=analysis, coordinator_token="T",  # stale
        )
    assert exc.value.code == "DATASET_EXPERIMENT_FENCE_LOST"

    with client.app.state.database.session_factory() as fresh:
        stored_run = fresh.get(AnalysisRunModel, attempt.analysis_run_id)
        stored_item = fresh.get(DatasetExperimentItemModel, target.id)
        stored_attempt = fresh.get(DatasetExperimentAttemptModel, attempt.id)

    assert stored_item.status == "running"              # stale actor mutated nothing
    assert stored_run.status == "failed"                # AnalysisService terminalized first
    assert stored_run.error_type == "RUNTIME_DESCRIPTOR_INVALID"
    assert stored_attempt.launch_requested_at is not None
    assert stored_run.worker_pid is None
    assert provider_b.launches == []
