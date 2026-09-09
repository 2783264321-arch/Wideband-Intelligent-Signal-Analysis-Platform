from pathlib import Path

import pytest

from app.remote_execution.recovery import mark_stale_local_cpu_runs_interrupted
from app.recordings.model import RecordingModel


def _add_run(client, run_id, executor, status):
    from app.analysis.model import AnalysisRunModel
    with client.app.state.database.session_factory() as session:
        session.add(RecordingModel(
            id=f"rec_{run_id}", name="0", data_path="recordings/0/raw.iq", data_format="complex64_le",
            sample_rate_hz=1e6, center_frequency_hz=0.0, frequency_low_hz=-5e5, frequency_high_hz=5e5,
            num_samples=1000, duration_s=0.001, dataset_name="SpaceNet", dataset_split="test",
            label_space="spacenet_14", source_data_sha256="1" * 64,
        ))
        session.add(AnalysisRunModel(
            id=run_id, recording_id=f"rec_{run_id}", pipeline_id="dummy", pipeline_version="1.0",
            executor=executor, status=status, parameters_json={},
        ))
        session.commit()


def _get_run_status(client, run_id):
    from app.analysis.model import AnalysisRunModel
    with client.app.state.database.session_factory() as session:
        return session.get(AnalysisRunModel, run_id).status


def test_stale_helper_interrupts_local_cpu_only(client):
    _add_run(client, "run_local", "local_cpu", "running")
    _add_run(client, "run_remote", "remote_gpu", "running")
    with client.app.state.database.session_factory() as session:
        n = mark_stale_local_cpu_runs_interrupted(session)
    assert n == 1
    assert _get_run_status(client, "run_local") == "interrupted"
    assert _get_run_status(client, "run_remote") == "running"


def test_legacy_stale_helper_delegates_to_local_only(client):
    from app.analysis.service import mark_stale_running_runs_interrupted

    _add_run(client, "run_local", "local_cpu", "running")
    _add_run(client, "run_remote", "remote_gpu", "running")
    with client.app.state.database.session_factory() as session:
        n = mark_stale_running_runs_interrupted(session)
    assert n == 1
    assert _get_run_status(client, "run_local") == "interrupted"
    assert _get_run_status(client, "run_remote") == "running"