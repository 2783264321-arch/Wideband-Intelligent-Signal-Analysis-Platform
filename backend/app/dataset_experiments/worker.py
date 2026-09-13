import argparse
import time

DEFAULT_POLL_INTERVAL = 1.0


def run_coordinator(experiment_id, coordinator_token, *, settings=None,
                    poll_interval=DEFAULT_POLL_INTERVAL, max_iterations=None):
    from app.analysis.service import AnalysisService
    from app.core.config import Settings
    from app.dataset_experiments.coordinator import (
        DatasetExperimentCoordinator,
        EXIT_OUTCOMES,
    )
    from app.dataset_experiments.service import DatasetExperimentService
    from app.dataset_experiments.wiring import build_control_plane_dependencies
    from app.db.base import Base, load_domain_models
    from app.db.migrations import run_additive_migrations
    from app.db.session import Database

    settings = settings or Settings()
    database = Database(settings.database_url)
    load_domain_models()
    Base.metadata.create_all(database.engine)
    run_additive_migrations(database.engine)
    deps = build_control_plane_dependencies(settings)

    def services_factory(session):
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
        return ds, analysis

    coordinator = DatasetExperimentCoordinator(
        session_factory=database.session_factory,
        services_factory=services_factory,
        poll_interval=poll_interval,
        max_iterations=max_iterations,
    )
    iterations = 0
    while True:
        outcome = coordinator.step(experiment_id, coordinator_token)
        if outcome in EXIT_OUTCOMES:
            return 0
        iterations += 1
        if max_iterations is not None and iterations >= max_iterations:
            return 0
        time.sleep(poll_interval)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("experiment_id")
    parser.add_argument("--coordinator-token", required=True)
    args = parser.parse_args(argv)
    return run_coordinator(args.experiment_id, args.coordinator_token)


if __name__ == "__main__":
    raise SystemExit(main())
