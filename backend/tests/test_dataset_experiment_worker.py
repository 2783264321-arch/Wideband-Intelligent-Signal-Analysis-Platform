import subprocess
import sys
import textwrap
from pathlib import Path

from app.dataset_experiments import coordinator as coordinator_module
from app.dataset_experiments.coordinator import CoordinatorOutcome
from app.dataset_experiments.worker import run_coordinator

_BACKEND_ROOT = Path(__file__).resolve().parents[1]


def test_worker_loop_exits_on_terminal_outcome(client, monkeypatch):
    settings = client.app.state.settings
    calls = []

    def fake_step(self, experiment_id, coordinator_token):
        calls.append((experiment_id, coordinator_token))
        return CoordinatorOutcome.EXPERIMENT_TERMINAL

    monkeypatch.setattr(coordinator_module.DatasetExperimentCoordinator, "step", fake_step)
    result = run_coordinator("exp_x", "tok", settings=settings)
    assert result == 0
    assert calls == [("exp_x", "tok")]


def test_worker_loop_honors_max_iterations(client, monkeypatch):
    settings = client.app.state.settings
    calls = []

    def fake_step(self, experiment_id, coordinator_token):
        calls.append((experiment_id, coordinator_token))
        return CoordinatorOutcome.WAITING

    monkeypatch.setattr(coordinator_module.DatasetExperimentCoordinator, "step", fake_step)
    result = run_coordinator("exp_x", "tok", settings=settings, poll_interval=0.0, max_iterations=2)
    assert result == 0
    assert len(calls) == 2


def test_build_control_plane_dependencies_executes_torch_free():
    code = textwrap.dedent(
        """
        import sys, tempfile, pathlib
        from app.core.config import Settings
        from app.dataset_experiments.wiring import build_control_plane_dependencies
        tmp = pathlib.Path(tempfile.mkdtemp())
        settings = Settings(
            project_root=tmp, data_root=tmp / "data",
            label_space_root=tmp / "label_spaces",
            database_url=f"sqlite:///{tmp / 't.db'}",
        )
        deps = build_control_plane_dependencies(settings)
        assert deps.registry is not None
        assert deps.executor_registry is not None
        assert "torch" not in sys.modules, "torch imported by wiring"
        assert "ultralytics" not in sys.modules, "ultralytics imported by wiring"
        print("OK")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(_BACKEND_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_wiring_dependencies_construct_coordinator_services(client):
    from app.analysis.service import AnalysisService
    from app.dataset_experiments.service import DatasetExperimentService
    from app.dataset_experiments.wiring import build_control_plane_dependencies

    settings = client.app.state.settings
    deps = build_control_plane_dependencies(settings)
    with client.app.state.database.session_factory() as session:
        ds = DatasetExperimentService(
            session, deps.registry, deps.model_release_store, deps.executor_registry
        )
        analysis = AnalysisService(
            session, deps.registry, None,
            model_release_store=deps.model_release_store,
            executor_registry=deps.executor_registry,
            identity_resolver=deps.identity_resolver,
            orchestrator_commit_resolver=deps.orchestrator_commit_resolver,
            runtime_commit_config=deps.runtime_commit_config,
            project_root=deps.project_root,
            data_root=deps.data_root,
        )
        assert ds is not None
        assert analysis is not None


def test_coordinator_packages_import_is_torch_free():
    code = (
        "import sys; "
        "import app.dataset_experiments.coordinator; "
        "import app.dataset_experiments.job_manager; "
        "import app.dataset_experiments.wiring; "
        "assert 'torch' not in sys.modules, 'torch leaked into coordinator'; "
        "assert 'ultralytics' not in sys.modules, 'ultralytics leaked into coordinator'; "
        "print('OK')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(_BACKEND_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
