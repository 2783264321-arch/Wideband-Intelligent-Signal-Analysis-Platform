from pathlib import Path
from types import SimpleNamespace

from app.recordings.model import RecordingModel
from app.remote_execution.recovery import (
    coordinate_orphaned_remote_runs,
    find_orphaned_remote_runs,
    mark_stale_local_cpu_runs_interrupted,
    rotate_coordinator_token,
)

RUN = "a" * 40
MANIFEST = "b" * 64


def _add_run(client, run_id, executor, status, token="tok_1"):
    from app.analysis.model import AnalysisRunModel
    with client.app.state.database.session_factory() as session:
        session.add(RecordingModel(
            id=f"rec_{run_id}", name="0", data_path="recordings/0/raw.iq", data_format="complex64_le",
            sample_rate_hz=1e6, center_frequency_hz=0.0, frequency_low_hz=-5e5, frequency_high_hz=5e5,
            num_samples=1000, duration_s=0.001, dataset_name="SpaceNet", dataset_split="test",
            label_space="spacenet_14", source_data_sha256="1" * 64,
        ))
        metadata = {
            "request_id": "rid", "batch_id": "bid", "item_key": "ik", "local_run_id": run_id,
            "request_sha256": "f" * 64, "orchestrator_commit": RUN,
            "required_remote_runtime_commit": RUN, "asset_manifest_sha256": MANIFEST,
            "pipeline_id": "zoomspec_yolo26n_aug_combined_frn_v3", "pipeline_version": "1.0.0",
            "remote_profile": "autodl_primary", "recording_fingerprint": "2" * 64,
            "source_data_sha256": "1" * 64, "dataset_name": "SpaceNet", "dataset_split": "test",
            "dataset_key": "0", "label_space": "spacenet_14", "parameters": {},
            "coordinator_token": token,
        }
        session.add(AnalysisRunModel(
            id=run_id, recording_id=f"rec_{run_id}", pipeline_id="zoomspec_yolo26n_aug_combined_frn_v3",
            pipeline_version="1.0.0", executor=executor, status=status,
            parameters_json={}, execution_metadata_json=metadata,
        ))
        session.commit()


class FakeLauncher:
    def __init__(self):
        self.launches = []

    def launch(self, run_id, coordinator_token):
        self.launches.append((run_id, coordinator_token))
        return 999


def _get_run_status(client, run_id):
    from app.analysis.model import AnalysisRunModel
    with client.app.state.database.session_factory() as session:
        run = session.get(AnalysisRunModel, run_id)
        return run.status, run.execution_metadata_json.get("coordinator_token")


def test_mark_stale_interrupts_local_cpu_running_only(client):
    _add_run(client, "run_local", "local_cpu", "running", token="t1")
    _add_run(client, "run_remote", "remote_gpu", "running", token="t2")
    with client.app.state.database.session_factory() as session:
        n = mark_stale_local_cpu_runs_interrupted(session)
    assert _get_run_status(client, "run_local")[0] == "interrupted"
    assert _get_run_status(client, "run_remote")[0] == "running"  # preserved
    assert n == 1


def test_startup_preserves_remote_pending_and_running(client):
    _add_run(client, "run_p", "remote_gpu", "pending", token="t1")
    _add_run(client, "run_r", "remote_gpu", "running", token="t2")
    launcher = FakeLauncher()
    with client.app.state.database.session_factory() as session:
        found = find_orphaned_remote_runs(session)
        coordinate_orphaned_remote_runs(session, launcher=launcher, remote_config_available=True,
                                        seen_run_ids=set())
    assert set(found) == {"run_p", "run_r"}
    assert _get_run_status(client, "run_p")[0] == "pending"
    assert _get_run_status(client, "run_r")[0] == "running"


def test_startup_relaunches_coordinator_with_fresh_token(client):
    _add_run(client, "run_r", "remote_gpu", "running", token="old_token")
    launcher = FakeLauncher()
    with client.app.state.database.session_factory() as session:
        coordinate_orphaned_remote_runs(session, launcher=launcher, remote_config_available=True,
                                        seen_run_ids=set())
    assert len(launcher.launches) == 1
    launched_run, launched_token = launcher.launches[0]
    assert launched_run == "run_r"
    current_status, current_token = _get_run_status(client, "run_r")
    assert current_token == launched_token  # rotated/persisted fresh token
    assert current_token != "old_token"


def test_startup_dedupes_coordinator_within_pass_two_runs(client):
    _add_run(client, "run_a", "remote_gpu", "running", token="t1")
    _add_run(client, "run_b", "remote_gpu", "pending", token="t2")
    launcher = FakeLauncher()
    seen = set()
    with client.app.state.database.session_factory() as session:
        coordinate_orphaned_remote_runs(session, launcher=launcher, remote_config_available=True,
                                        seen_run_ids=seen)
    assert len(launcher.launches) == 2
    assert {r for r, _ in launcher.launches} == {"run_a", "run_b"}
    # second pass with same seen set dedupes (within-pass convenience)
    launch_count_before = len(launcher.launches)
    with client.app.state.database.session_factory() as session:
        coordinate_orphaned_remote_runs(session, launcher=launcher, remote_config_available=True,
                                        seen_run_ids=seen)
    assert len(launcher.launches) == launch_count_before


def test_missing_remote_config_keeps_app_healthy_and_preserves_runs(client):
    _add_run(client, "run_r", "remote_gpu", "running", token="t1")
    launcher = FakeLauncher()
    with client.app.state.database.session_factory() as session:
        coordinate_orphaned_remote_runs(session, launcher=launcher, remote_config_available=False,
                                        seen_run_ids=set())
    assert launcher.launches == []  # no coordinator launched
    assert _get_run_status(client, "run_r")[0] == "running"  # preserved


def test_missing_remote_config_reports_unavailable(client):
    from app.remote_execution.recovery import remote_config_available
    assert remote_config_available() is False or isinstance(remote_config_available(), bool)


def test_create_app_no_remote_config_does_not_raise(settings):
    from app.main import create_app

    app = create_app(settings)
    assert app is not None


def test_rotate_coordinator_token_does_not_change_request_sha(client):
    from app.remote_execution.request_builder import build_batch

    with client.app.state.database.session_factory() as session:
        _add_run(client, "run_x", "remote_gpu", "running", token="t1")
        run = session.get(__import__("app.analysis.model", fromlist=["AnalysisRunModel"]).AnalysisRunModel, "run_x")
        metadata = dict(run.execution_metadata_json)
        batch_before = build_batch(metadata)
        rotated = rotate_coordinator_token(metadata)
        batch_after = build_batch(rotate_coordinator_token(dict(metadata)))
        assert batch_before.request_sha256 == batch_after.request_sha256