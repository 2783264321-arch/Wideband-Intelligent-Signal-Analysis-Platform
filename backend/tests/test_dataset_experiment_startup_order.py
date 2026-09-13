from datetime import datetime

import pytest


def test_startup_recovery_order(settings, monkeypatch):
    calls = []
    import app.remote_execution.recovery as rr
    import app.main as main_module
    import app.dataset_experiments.recovery as dr

    monkeypatch.setattr(
        rr, "mark_stale_local_cpu_runs_interrupted",
        lambda session: calls.append("local_stale"),
    )
    monkeypatch.setattr(
        main_module, "mark_stale_running_evaluations_interrupted",
        lambda session: calls.append("evaluation_stale"),
    )
    monkeypatch.setattr(
        dr, "recover_dataset_experiments",
        lambda session, **kwargs: calls.append("dataset_recovery"),
    )

    main_module.create_app(settings)
    assert calls == ["local_stale", "evaluation_stale", "dataset_recovery"]


def test_startup_recovery_order_with_remote(settings, monkeypatch):
    calls = []
    import app.remote_execution.recovery as rr
    import app.main as main_module
    import app.dataset_experiments.recovery as dr

    monkeypatch.setattr(
        rr, "mark_stale_local_cpu_runs_interrupted",
        lambda session: calls.append("local_stale"),
    )
    monkeypatch.setattr(
        main_module, "mark_stale_running_evaluations_interrupted",
        lambda session: calls.append("evaluation_stale"),
    )
    monkeypatch.setattr(
        rr, "coordinate_orphaned_remote_runs",
        lambda session, **kwargs: calls.append("remote_recovery"),
    )
    monkeypatch.setattr(
        dr, "recover_dataset_experiments",
        lambda session, **kwargs: calls.append("dataset_recovery"),
    )

    def wire(app, settings):
        app.state.remote_config_available = True
        app.state.remote_coordinator_launcher = object()

    monkeypatch.setattr(main_module, "_wire_remote_lifecycle", wire)

    main_module.create_app(settings)
    assert calls == [
        "local_stale", "evaluation_stale", "remote_recovery", "dataset_recovery"
    ]


def test_dataset_recovery_receives_built_executor_registry(settings, monkeypatch):
    captured = {}
    import app.dataset_experiments.recovery as dr
    from app.dataset_experiments.job_manager import DatasetExperimentJobManager

    def capture(session, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(dr, "recover_dataset_experiments", capture)

    import app.main as main_module
    main_module.create_app(settings)

    assert isinstance(captured["job_manager"], DatasetExperimentJobManager)
    assert captured["executor_registry"] is not None
    assert isinstance(captured["startup_recovery_cutoff"], datetime)
