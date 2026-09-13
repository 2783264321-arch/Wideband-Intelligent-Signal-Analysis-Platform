from __future__ import annotations

from benchmark_fixture import add_ground_truth, add_recording

from app.analysis.model import AnalysisRunModel
from app.analysis.service import AnalysisService
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


class RecordingJobManager:
    def __init__(self, *, token_probe=None, on_start=None, fail=False, pid=4242):
        self.calls = []
        self.fail = fail
        self.pid = pid
        self.token_probe = token_probe
        self.on_start = on_start

    def start(self, experiment_id, coordinator_token):
        self.calls.append((experiment_id, coordinator_token))
        if self.on_start is not None:
            self.on_start(experiment_id, coordinator_token)
        if self.token_probe is not None:
            self.token_probe(experiment_id, coordinator_token)
        if self.fail:
            raise RuntimeError("spawn failed")
        return self.pid


def seed_dataset(client, count=3):
    with client.app.state.database.session_factory() as session:
        for index in range(count):
            rid = f"rec_{index}"
            add_recording(session, recording_id=rid, name=f"name_{index}")
            add_ground_truth(session, gt_id=f"gt_{index}", recording_id=rid, class_id=9,
                             class_name="LoRa 250kHz", t0=0.01, t1=0.02,
                             f0=2_440_600_000.0, f1=2_440_700_000.0)
        session.commit()


def create_experiment(ds, *, max_concurrency=1):
    return ds.create_experiment(
        name="g3", dataset_name="SpaceNet", dataset_split="test",
        dataset_label_space="spacenet_14", plugin_id="g3_local", plugin_version="1.0",
        executor="local_cpu", parameters={},
        evaluation_protocol="physical_tf_detection_ap_v2", max_concurrency=max_concurrency,
    )


def item(session, experiment_id, order=0):
    return session.query(DatasetExperimentItemModel).filter_by(
        experiment_id=experiment_id, manifest_order=order
    ).one()


def local_services(client, *, provider=None):
    session = client.app.state.database.session_factory()
    provider = provider or FakeProvider("local_cpu")
    registry = PipelineRegistry([G3LocalPipeline()])
    executor_registry = FakeRegistry({"local_cpu": provider})
    ds = DatasetExperimentService(session, registry, None, executor_registry)
    analysis = AnalysisService(session, registry, client.app.state.job_manager,
                               executor_registry=executor_registry)
    return session, ds, analysis, provider


def running_experiment_with_token(client, token, *, max_concurrency=1, count=3, provider=None):
    seed_dataset(client, count=count)
    session, ds, analysis, provider = local_services(client, provider=provider)
    experiment = create_experiment(ds, max_concurrency=max_concurrency)
    experiment.status = "running"
    experiment.coordinator_token = token
    session.commit()
    return session, ds, analysis, provider, experiment


def completed_with_failures_experiment(client, *, failed=1, completed=0):
    from datetime import datetime, timezone

    count = failed + completed
    seed_dataset(client, count=count)
    session, ds, analysis, provider = local_services(client)
    experiment = create_experiment(ds, max_concurrency=1)
    experiment.status = "running"
    experiment.coordinator_token = "coord_old"
    session.commit()
    targets = list(
        session.query(DatasetExperimentItemModel)
        .filter_by(experiment_id=experiment.id)
        .order_by(DatasetExperimentItemModel.manifest_order)
        .all()
    )
    for index, target in enumerate(targets):
        if index < failed:
            target.status = "failed"
            target.last_error_type = "ANALYSIS_FAILED"
            target.last_error_message = "boom"
        else:
            target.status = "completed"
    experiment = session.get(DatasetExperimentModel, experiment.id)
    experiment.status = "completed_with_failures"
    experiment.completed_at = datetime.now(timezone.utc)
    session.commit()
    session.refresh(experiment)
    return session, ds, experiment, provider


def rotate_token(client, experiment_id, new_token):
    with client.app.state.database.session_factory() as session:
        session.get(DatasetExperimentModel, experiment_id).coordinator_token = new_token
        session.commit()


def mark_item_running_with_active_run(session, *, item, run_id="run_active",
                                      attempt_id="att_active", status="pending"):
    session.add(AnalysisRunModel(
        id=run_id, recording_id=item.recording_id, pipeline_id="g3_local",
        pipeline_version="1.0", executor="local_cpu", status=status, parameters_json={},
    ))
    session.add(DatasetExperimentAttemptModel(
        id=attempt_id, experiment_item_id=item.id, attempt_number=1, analysis_run_id=run_id,
    ))
    item.status = "running"
    session.commit()


def mark_item_completed_with_run(session, *, item, run_id, attempt_id):
    session.add(AnalysisRunModel(
        id=run_id, recording_id=item.recording_id, pipeline_id="g3_local",
        pipeline_version="1.0", executor="local_cpu", status="completed", parameters_json={},
    ))
    session.add(DatasetExperimentAttemptModel(
        id=attempt_id, experiment_item_id=item.id, attempt_number=1, analysis_run_id=run_id,
    ))
    item.status = "completed"
    session.commit()


def services_factory_for(client, *, provider=None, prepare_wrapper=None):
    def services_factory(session):
        registry = PipelineRegistry([G3LocalPipeline()])
        executor_registry = FakeRegistry({"local_cpu": provider or FakeProvider("local_cpu")})
        ds = DatasetExperimentService(session, registry, None, executor_registry)
        analysis = AnalysisService(session, registry, client.app.state.job_manager,
                                   executor_registry=executor_registry)
        if prepare_wrapper is not None:
            real_prepare = analysis.prepare_run
            analysis.prepare_run = lambda **kw: prepare_wrapper(real_prepare, **kw)
        return ds, analysis
    return services_factory


def step_once(client, experiment_id, token, *, provider=None, prepare_wrapper=None):
    from app.dataset_experiments.coordinator import DatasetExperimentCoordinator

    coordinator = DatasetExperimentCoordinator(
        session_factory=client.app.state.database.session_factory,
        services_factory=services_factory_for(
            client, provider=provider, prepare_wrapper=prepare_wrapper
        ),
    )
    return coordinator.step(experiment_id, token)
