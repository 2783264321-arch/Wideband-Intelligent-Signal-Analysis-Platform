from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

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
