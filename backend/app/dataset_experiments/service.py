from __future__ import annotations

from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.benchmarks.service import DatasetBenchmarkService, resolve_protocol_config
from app.core.errors import PlatformError
from app.dataset_experiments.model import (
    DatasetExperimentAttemptModel,
    DatasetExperimentItemModel,
    DatasetExperimentModel,
)
from app.dataset_experiments.schema import (
    DatasetExperimentAttemptRead,
    DatasetExperimentItemRead,
    DatasetExperimentRead,
)
from app.pipelines.plugin import validate_plugin_parameters
from app.recordings.model import RecordingModel

_ITEM_STATUSES = ("queued", "running", "completed", "failed")


class DatasetExperimentService:
    def __init__(self, session: Session, registry, model_release_store, executor_registry) -> None:
        self.session = session
        self.registry = registry
        self.model_release_store = model_release_store
        self.executor_registry = executor_registry

    # ---------- read models ----------

    def _get(self, experiment_id: str) -> DatasetExperimentModel:
        experiment = self.session.get(DatasetExperimentModel, experiment_id)
        if experiment is None:
            raise PlatformError("DATASET_EXPERIMENT_NOT_FOUND", "Dataset experiment was not found.", 404)
        return experiment

    def get_experiment(self, experiment_id: str) -> DatasetExperimentRead:
        return self._to_read(self._get(experiment_id))

    def list_items(self, experiment_id: str) -> list[DatasetExperimentItemRead]:
        self._get(experiment_id)
        rows = self.session.execute(
            select(DatasetExperimentItemModel, RecordingModel.name)
            .join(RecordingModel, DatasetExperimentItemModel.recording_id == RecordingModel.id)
            .where(DatasetExperimentItemModel.experiment_id == experiment_id)
            .order_by(DatasetExperimentItemModel.manifest_order)
        ).all()
        return [
            DatasetExperimentItemRead(
                id=item.id,
                experiment_id=item.experiment_id,
                manifest_order=item.manifest_order,
                recording_id=item.recording_id,
                recording_name=recording_name,
                status=item.status,
                last_error_type=item.last_error_type,
                last_error_message=item.last_error_message,
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
            for item, recording_name in rows
        ]

    def list_attempts(self, item_id: str) -> list[DatasetExperimentAttemptRead]:
        item = self.session.get(DatasetExperimentItemModel, item_id)
        if item is None:
            raise PlatformError("DATASET_EXPERIMENT_ITEM_NOT_FOUND", "Dataset experiment item was not found.", 404)
        rows = self.session.scalars(
            select(DatasetExperimentAttemptModel)
            .where(DatasetExperimentAttemptModel.experiment_item_id == item_id)
            .order_by(DatasetExperimentAttemptModel.attempt_number)
        ).all()
        return [DatasetExperimentAttemptRead.model_validate(row) for row in rows]

    def _to_read(self, experiment: DatasetExperimentModel) -> DatasetExperimentRead:
        status_counts = dict(
            self.session.execute(
                select(DatasetExperimentItemModel.status, func.count(DatasetExperimentItemModel.id))
                .where(DatasetExperimentItemModel.experiment_id == experiment.id)
                .group_by(DatasetExperimentItemModel.status)
            ).all()
        )
        attempt_count = int(
            self.session.scalar(
                select(func.count(DatasetExperimentAttemptModel.id))
                .join(
                    DatasetExperimentItemModel,
                    DatasetExperimentAttemptModel.experiment_item_id == DatasetExperimentItemModel.id,
                )
                .where(DatasetExperimentItemModel.experiment_id == experiment.id)
            )
            or 0
        )
        return DatasetExperimentRead(
            id=experiment.id,
            name=experiment.name,
            dataset_name=experiment.dataset_name,
            dataset_split=experiment.dataset_split,
            dataset_label_space=experiment.dataset_label_space,
            recording_manifest_hash=experiment.recording_manifest_hash,
            plugin_id=experiment.plugin_id,
            plugin_version=experiment.plugin_version,
            model_release_id=experiment.model_release_id,
            asset_manifest_sha256=experiment.asset_manifest_sha256,
            parameters_json=dict(experiment.parameters_json or {}),
            executor=experiment.executor,
            runtime_descriptor_json=dict(experiment.runtime_descriptor_json or {}),
            evaluation_protocol=experiment.evaluation_protocol,
            max_concurrency=experiment.max_concurrency,
            status=experiment.status,
            dataset_evaluation_id=experiment.dataset_evaluation_id,
            error_type=experiment.error_type,
            error_message=experiment.error_message,
            created_at=experiment.created_at,
            started_at=experiment.started_at,
            completed_at=experiment.completed_at,
            expected_items=sum(status_counts.values()),
            queued_items=status_counts.get("queued", 0),
            running_items=status_counts.get("running", 0),
            completed_items=status_counts.get("completed", 0),
            failed_items=status_counts.get("failed", 0),
            attempt_count=attempt_count,
        )

    # ---------- reusable authority seams ----------

    def _manifest_preview(self, dataset_name, dataset_split, dataset_label_space):
        return DatasetBenchmarkService(self.session).prepare_manifest(
            dataset_name, dataset_split, dataset_label_space
        )

    def _resolve_definition(self, plugin_id, plugin_version):
        try:
            pipeline = self.registry.get(plugin_id)
        except PlatformError as exc:
            raise PlatformError(
                "PLUGIN_NOT_FOUND",
                f"Plugin '{plugin_id}' is not registered.",
            ) from exc
        definition = pipeline.definition
        if definition.plugin_version != plugin_version:
            raise PlatformError(
                "PLUGIN_NOT_FOUND",
                f"Plugin '{plugin_id}' version '{plugin_version}' is not registered; "
                f"registered version is '{definition.plugin_version}'.",
            )
        return definition

    def _resolve_release(self, definition, requested):
        """Mirrors AnalysisService._resolve_release; single authority is ModelReleaseStore.resolve."""
        if not definition.model_release_required:
            if requested is not None:
                raise PlatformError(
                    "MODEL_RELEASE_MISMATCH",
                    "This plugin does not accept a model release identity.",
                )
            return None
        if self.model_release_store is None:
            raise PlatformError(
                "EXECUTION_CAPABILITY_UNAVAILABLE", "Model release store is not configured."
            )
        return self.model_release_store.resolve(
            definition.plugin_id, definition.plugin_version, requested
        )

    def _validate_execution(self, definition, model_release_id, executor):
        """Deployment identity only. Never probes a Recording, never calls availability_for."""
        provider = self.executor_registry.provider(executor)
        if self.executor_registry.certified_capability(
            definition, model_release_id, executor
        ) is None:
            raise PlatformError(
                "EXECUTION_NOT_CERTIFIED",
                "The requested executor has no exact platform certificate for this "
                "release and runtime.",
            )
        return provider, provider.runtime_descriptor()

    # ---------- creation ----------

    def create_experiment(
        self,
        *,
        name,
        dataset_name,
        dataset_split,
        dataset_label_space,
        plugin_id,
        plugin_version,
        executor,
        parameters,
        evaluation_protocol,
        max_concurrency,
        model_release_id=None,
    ):
        if max_concurrency < 1:
            raise PlatformError(
                "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                "max_concurrency must be >= 1.",
                422,
            )

        manifest = self._manifest_preview(dataset_name, dataset_split, dataset_label_space)
        definition = self._resolve_definition(plugin_id, plugin_version)
        validate_plugin_parameters(definition, parameters)
        resolved_release = self._resolve_release(definition, model_release_id)
        resolve_protocol_config(evaluation_protocol)

        frozen_release_id = (
            None if resolved_release is None else resolved_release.release.model_release_id
        )
        frozen_asset_sha = (
            None if resolved_release is None else resolved_release.manifest.asset_manifest_sha256
        )
        provider, descriptor = self._validate_execution(definition, frozen_release_id, executor)

        experiment = DatasetExperimentModel(
            id=f"exp_{uuid4().hex}",
            name=name,
            dataset_name=dataset_name,
            dataset_split=dataset_split,
            dataset_label_space=dataset_label_space,
            recording_manifest_hash=manifest.recording_manifest_hash,
            plugin_id=definition.plugin_id,
            plugin_version=definition.plugin_version,
            model_release_id=frozen_release_id,
            asset_manifest_sha256=frozen_asset_sha,
            parameters_json=dict(parameters),
            executor=provider.name,
            runtime_descriptor_json=descriptor.to_metadata(),
            evaluation_protocol=evaluation_protocol,
            max_concurrency=max_concurrency,
            status="pending",
        )
        try:
            self.session.add(experiment)
            item_rows = self._new_item_rows(experiment.id, manifest.entries)
            self.session.add_all(item_rows)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        self.session.refresh(experiment)
        return experiment

    def _new_item_rows(self, experiment_id, entries):
        return [
            DatasetExperimentItemModel(
                id=f"expitem_{uuid4().hex}",
                experiment_id=experiment_id,
                manifest_order=entry.manifest_order,
                recording_id=entry.recording_id,
                status="queued",
            )
            for entry in entries
        ]
