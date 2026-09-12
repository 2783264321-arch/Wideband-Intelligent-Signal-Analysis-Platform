from datetime import datetime, timezone
from pathlib import Path

import pytest

from benchmark_fixture import add_ground_truth, add_recording

from app.analysis.model import AnalysisRunModel
from app.analysis.schema import ExecutorAvailabilityRead
from app.analysis.service import AnalysisService
from app.core.errors import PlatformError
from app.dataset_experiments.model import (
    DatasetExperimentAttemptModel,
    DatasetExperimentItemModel,
)
from app.dataset_experiments.service import DatasetExperimentService
from app.pipelines.base import ExecutionCapability, Pipeline, PipelineDefinition, PipelineOutput
from app.pipelines.registry import PipelineRegistry
from app.recordings.model import RecordingModel
from app.remote_execution.identity import resolve_remote_recording_identity
from app.remote_execution.model_release import ResolvedModelRelease
from app.remote_execution.runtime import (
    ExecutionCertificate,
    ExecutionCertificateStore,
    ExecutorRegistry,
)

from executor_fixtures import FakeProvider

RUN = "a" * 40
MANIFEST = "b" * 64
RUNTIME_REF = "remote:test:cuda:1"


class G3RemotePipeline(Pipeline):
    @property
    def definition(self) -> PipelineDefinition:
        return PipelineDefinition(
            id="g3_remote", name="G3 Remote", version="1.0", label_space="spacenet_14",
            recommended_device="GPU", cpu_supported=False, stages=(), inspectable_stages=(),
            task_capability="detection_classification", executors_supported=("remote_gpu",),
            recommended_executor="remote_gpu", model_release_required=True,
            technical_execution_capabilities=(ExecutionCapability("remote_gpu", "cuda", "float16"),),
        )

    def run(self, recording, parameters, workspace) -> PipelineOutput:
        raise AssertionError("must not execute")


class _Probe:
    def availability(self, recording, pipeline, source_data_sha256, model_release=None):
        return ExecutorAvailabilityRead(
            executor="remote_gpu", available=True, reason_code=None, reason_message=None,
            remote_profile="autodl_primary", recommended=True,
        )


class _Store:
    def resolve(self, plugin_id, plugin_version, requested):
        release = type("R", (), {"model_release_id": requested or "golden",
                                 "asset_manifest_sha256": MANIFEST})()
        manifest = type("M", (), {"asset_manifest_sha256": MANIFEST})()
        return ResolvedModelRelease(release=release, manifest=manifest)


def _cert():
    return ExecutionCertificate(
        plugin_id="g3_remote", plugin_version="1.0", model_release_id="golden",
        executor="remote_gpu", device_type="cuda", precision="float16",
        runtime_ref=RUNTIME_REF, evidence_ref="g3-test",
    )


def _seed_remote_dataset(client, data_root, recording_id="rec_real"):
    target = data_root / "recordings" / recording_id / "raw.iq"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"\x00\x01\x02\x03" * 256)
    with client.app.state.database.session_factory() as session:
        add_recording(session, recording_id=recording_id, name=recording_id)
        add_ground_truth(session, gt_id=f"gt_{recording_id}", recording_id=recording_id,
                         class_id=9, class_name="LoRa 250kHz", t0=0.01, t1=0.02,
                         f0=2_440_600_000.0, f1=2_440_700_000.0)
        session.commit()


def _services_remote(client, data_root):
    session = client.app.state.database.session_factory()
    provider = FakeProvider("remote_gpu", runtime_ref=RUNTIME_REF, probe=_Probe())
    executor_registry = ExecutorRegistry(
        {"remote_gpu": provider}, ExecutionCertificateStore([_cert()])
    )
    registry = PipelineRegistry([G3RemotePipeline()])
    ds = DatasetExperimentService(session, registry, _Store(), executor_registry)
    analysis = AnalysisService(
        session, registry, client.app.state.job_manager,
        remote_executor_probe=_Probe(), identity_resolver=resolve_remote_recording_identity,
        orchestrator_commit_resolver=lambda project_root: RUN,
        model_release_store=_Store(), runtime_commit_config=RUN,
        project_root=Path("/tmp"), data_root=data_root, executor_registry=executor_registry,
    )
    return session, ds, analysis, provider


def _remote_experiment(ds):
    experiment = ds.create_experiment(
        name="g3r", dataset_name="SpaceNet", dataset_split="test",
        dataset_label_space="spacenet_14", plugin_id="g3_remote", plugin_version="1.0",
        executor="remote_gpu", parameters={},
        evaluation_protocol="physical_tf_detection_ap_v2", max_concurrency=1,
        model_release_id="golden",
    )
    # G3-A only schedules a running experiment (G3-C owns pending -> running).
    experiment.status = "running"
    ds.session.commit()
    return experiment


def _queued_item(session, experiment_id):
    return session.query(DatasetExperimentItemModel).filter_by(
        experiment_id=experiment_id, manifest_order=0
    ).one()


def _remote_owned_attempt(client, tmp_path):
    data_root = tmp_path / "data"
    _seed_remote_dataset(client, data_root)
    session, ds, analysis, provider = _services_remote(client, data_root)
    experiment = _remote_experiment(ds)
    item = _queued_item(session, experiment.id)
    attempt = ds.start_item_attempt(experiment_id=experiment.id, item_id=item.id,
                                    analysis_service=analysis)
    return session, ds, analysis, provider, experiment, item, attempt


def _remote_launch(ds, experiment, item, attempt, analysis):
    return ds.launch_item_attempt(experiment_id=experiment.id, item_id=item.id,
                                  attempt_id=attempt.id, analysis_service=analysis)


def test_remote_launch_commits_intent_before_physical_launch(client, tmp_path, monkeypatch):
    session, ds, analysis, provider, experiment, item, attempt = _remote_owned_attempt(client, tmp_path)
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

    launched = _remote_launch(ds, experiment, item, attempt, analysis)

    assert events[0] == "commit"      # Transaction B committed first (remote too)
    assert events[1] == "launch"
    # Remote marker is set, but remote recovery does NOT interpret it as its fence.
    assert launched.launch_requested_at is not None
    frozen_token = session.get(
        AnalysisRunModel, attempt.analysis_run_id
    ).execution_metadata_json["coordinator_token"]
    assert provider.launches == [(attempt.analysis_run_id, frozen_token)]  # launched once


def test_remote_launch_records_intent_worker_pid_and_frozen_token(client, tmp_path):
    session, ds, analysis, provider, experiment, item, attempt = _remote_owned_attempt(client, tmp_path)
    frozen_token = session.get(
        AnalysisRunModel, attempt.analysis_run_id
    ).execution_metadata_json["coordinator_token"]

    launched = _remote_launch(ds, experiment, item, attempt, analysis)

    assert launched.launch_requested_at is not None
    assert provider.launches == [(attempt.analysis_run_id, frozen_token)]
    with client.app.state.database.session_factory() as fresh:
        stored_run = fresh.get(AnalysisRunModel, attempt.analysis_run_id)
        assert stored_run.status == "pending"
        assert stored_run.worker_pid is not None


def test_remote_double_launch_fails_closed(client, tmp_path):
    session, ds, analysis, provider, experiment, item, attempt = _remote_owned_attempt(client, tmp_path)
    _remote_launch(ds, experiment, item, attempt, analysis)
    with pytest.raises(PlatformError) as exc:
        _remote_launch(ds, experiment, item, attempt, analysis)
    assert exc.value.code == "DATASET_EXPERIMENT_INVARIANT_VIOLATION"  # CAS lost; no second launch
    assert len(provider.launches) == 1


def test_remote_real_stale_session_cas_does_not_launch(client, tmp_path):
    session, ds, analysis, provider, experiment, item, attempt = _remote_owned_attempt(client, tmp_path)
    assert attempt.launch_requested_at is None

    # Session B wins the real launch-intent CAS and commits it.
    with client.app.state.database.session_factory() as winner:
        winner.get(DatasetExperimentAttemptModel, attempt.id).launch_requested_at = datetime.now(timezone.utc)
        winner.commit()

    # Session A retains stale NULL ORM state; its real CAS must lose.
    assert attempt.launch_requested_at is None
    with pytest.raises(PlatformError) as exc:
        _remote_launch(ds, experiment, item, attempt, analysis)
    assert exc.value.code == "DATASET_EXPERIMENT_INVARIANT_VIOLATION"
    assert provider.launches == []  # loser performs zero provider launches

    with client.app.state.database.session_factory() as fresh:
        assert fresh.get(DatasetExperimentAttemptModel, attempt.id).launch_requested_at is not None

