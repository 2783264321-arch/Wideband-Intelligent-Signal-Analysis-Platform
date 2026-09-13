from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import exists, func, select, update
from sqlalchemy.orm import Session

from app.analysis.model import AnalysisRunModel
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

if TYPE_CHECKING:
    from app.analysis.service import AnalysisService

_ITEM_STATUSES = ("queued", "running", "completed", "failed")


@dataclass(frozen=True)
class ReconcileSummary:
    expected: int
    queued: int
    running: int
    completed: int
    failed: int


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

    # ---------- frozen identity revalidation (G3+ seam) ----------

    def revalidate_frozen_identity(self, experiment_id):
        try:
            experiment = self._get(experiment_id)
        except PlatformError as exc:
            if exc.code != "DATASET_EXPERIMENT_NOT_FOUND":
                raise
            raise PlatformError(
                "DATASET_EXPERIMENT_ORCHESTRATION_FAILED",
                "Frozen experiment no longer exists; cannot revalidate.",
                409,
            ) from exc

        # 1. Frozen dataset membership hash.
        try:
            manifest = self._manifest_preview(
                experiment.dataset_name, experiment.dataset_split, experiment.dataset_label_space
            )
        except PlatformError as exc:
            raise PlatformError(
                "DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED",
                "Frozen dataset manifest can no longer be resolved.",
                409,
            ) from exc
        if manifest.recording_manifest_hash != experiment.recording_manifest_hash:
            raise PlatformError(
                "DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED",
                "Frozen dataset manifest hash no longer matches the current manifest.",
                409,
            )

        # 2. Item membership / order must equal the frozen manifest exactly.
        self._assert_item_membership(experiment, manifest)

        # 3. Exact plugin version identity.
        try:
            definition = self._resolve_definition(experiment.plugin_id, experiment.plugin_version)
        except PlatformError as exc:
            raise PlatformError(
                "DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED",
                "Frozen plugin id/version no longer resolves exactly.",
                409,
            ) from exc

        # 4. Frozen parameters must still validate against the exact plugin.
        try:
            validate_plugin_parameters(definition, experiment.parameters_json or {})
        except PlatformError as exc:
            raise PlatformError(
                "DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED",
                "Frozen parameters are no longer valid for the frozen plugin identity.",
                409,
            ) from exc

        # 5. Frozen evaluation protocol must remain within the supported set.
        try:
            resolve_protocol_config(experiment.evaluation_protocol)
        except PlatformError as exc:
            raise PlatformError(
                "DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED",
                "Frozen evaluation protocol is no longer supported.",
                409,
            ) from exc

        # 6. Frozen max_concurrency must remain structurally valid.
        if experiment.max_concurrency < 1:
            raise PlatformError(
                "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                "Frozen max_concurrency is invalid; it must be >= 1.",
                409,
            )

        # 7. Release identity (id + AssetManifest SHA) through the single G1 seam.
        frozen_release_id = experiment.model_release_id
        frozen_asset_sha = experiment.asset_manifest_sha256
        try:
            resolved = self._resolve_release(definition, frozen_release_id)
        except PlatformError as exc:
            raise PlatformError(
                "DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED",
                "Frozen model release identity can no longer be resolved.",
                409,
            ) from exc
        if definition.model_release_required:
            if (
                resolved is None
                or resolved.release.model_release_id != frozen_release_id
                or resolved.manifest.asset_manifest_sha256 != frozen_asset_sha
            ):
                raise PlatformError(
                    "DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED",
                    "Frozen model release or asset manifest hash has changed.",
                    409,
                )
        elif (
            resolved is not None
            or frozen_release_id is not None
            or frozen_asset_sha is not None
        ):
            raise PlatformError(
                "DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED",
                "A release-less plugin carries a forbidden frozen release identity.",
                409,
            )

        # 8. Executor capability + exact certificate + provider RuntimeDescriptor.
        try:
            provider = self.executor_registry.provider(experiment.executor)
        except PlatformError as exc:
            raise PlatformError(
                "DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED",
                "Frozen executor is no longer technically supported.",
                409,
            ) from exc
        if self.executor_registry.certified_capability(
            definition, frozen_release_id, experiment.executor
        ) is None:
            raise PlatformError(
                "DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED",
                "Exact execution certificate no longer exists for the frozen identity.",
                409,
            )
        if provider.runtime_descriptor().to_metadata() != (experiment.runtime_descriptor_json or {}):
            raise PlatformError(
                "DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED",
                "Provider RuntimeDescriptor no longer matches the frozen descriptor.",
                409,
            )

        return experiment

    def _assert_item_membership(self, experiment, manifest):
        items = list(
            self.session.scalars(
                select(DatasetExperimentItemModel)
                .where(DatasetExperimentItemModel.experiment_id == experiment.id)
                .order_by(DatasetExperimentItemModel.manifest_order)
            ).all()
        )
        expected = [(entry.manifest_order, entry.recording_id) for entry in manifest.entries]
        actual = [(item.manifest_order, item.recording_id) for item in items]
        if actual != expected:
            raise PlatformError(
                "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                "Frozen Item membership/order is inconsistent with the frozen manifest.",
                409,
            )

    # ---------- Transaction A: durable execution ownership (G3-A) ----------

    def start_item_attempt(self, *, experiment_id, item_id, analysis_service,
                           coordinator_token=None):
        """G3-A Transaction A: durably bind one queued Item to a newly prepared Run.

        Requires the Experiment to already be ``running`` (G3-C owns
        ``pending -> running``). Revalidates frozen identity, stages a pending
        AnalysisRun through the G2 AnalysisService.prepare_run seam on the SAME
        Session (all external I/O happens here, before any write lock), acquires
        the Item with a conditional queued->running CAS, then derives the next
        attempt number from Attempt history under that claim, persists the
        Attempt, and commits exactly once. Never writes launch_requested_at and
        never launches.
        """
        if analysis_service is None or getattr(analysis_service, "session", None) is not self.session:
            raise PlatformError(
                "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                "AnalysisService must share the DatasetExperimentService Session.",
                409,
            )

        experiment = self.revalidate_frozen_identity(experiment_id)

        if experiment.status != "running":
            raise PlatformError(
                "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                "Experiment is not running; G3-A only schedules a running experiment.",
                409,
            )

        item = self.session.get(DatasetExperimentItemModel, item_id)
        if item is None:
            raise PlatformError(
                "DATASET_EXPERIMENT_ITEM_NOT_FOUND", "Dataset experiment item was not found.", 404
            )
        if item.experiment_id != experiment.id:
            raise PlatformError(
                "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                "Item does not belong to the frozen experiment.",
                409,
            )
        if item.status != "queued":
            raise PlatformError(
                "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                "Only a queued item can start a new attempt.",
                409,
            )
        recording_id = item.recording_id

        try:
            # All external I/O (availability/probe/identity/source hash) happens
            # here, before any write lock is acquired below.
            run = analysis_service.prepare_run(
                recording_id=recording_id,
                pipeline_id=experiment.plugin_id,
                executor=experiment.executor,
                parameters=dict(experiment.parameters_json or {}),
                model_release_id=experiment.model_release_id,
            )

            with self.session.no_autoflush:
                # 1. Acquire ownership: conditional queued->running CAS.
                claimed = self._claim_queued_item(
                    item_id=item_id, experiment_id=experiment.id,
                    coordinator_token=coordinator_token,
                )
                if claimed != 1:
                    if coordinator_token is not None:
                        self._require_experiment_generation(experiment.id, coordinator_token)
                    raise PlatformError(
                        "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                        "Item is no longer queued; another start won or it is not eligible.",
                        409,
                    )
                # 2. Derive the next attempt number under the acquired claim.
                attempt_number = self._next_attempt_number(item_id)
                attempt = DatasetExperimentAttemptModel(
                    id=f"expattempt_{uuid4().hex}",
                    experiment_item_id=item_id,
                    attempt_number=attempt_number,
                    analysis_run_id=run.id,
                )
                self.session.add(attempt)
                self.session.expire(item)

            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

        self.session.refresh(attempt)
        return attempt

    def _require_experiment_generation(self, experiment_id, coordinator_token):
        row = self.session.execute(
            select(DatasetExperimentModel.status, DatasetExperimentModel.coordinator_token)
            .where(DatasetExperimentModel.id == experiment_id)
        ).one_or_none()
        if row is None or row[0] != "running" or row[1] != coordinator_token:
            raise PlatformError(
                "DATASET_EXPERIMENT_FENCE_LOST",
                "Coordinator generation no longer owns the experiment.",
                409,
            )

    def _claim_queued_item(self, *, item_id, experiment_id, coordinator_token=None):
        statement = (
            update(DatasetExperimentItemModel)
            .where(
                DatasetExperimentItemModel.id == item_id,
                DatasetExperimentItemModel.experiment_id == experiment_id,
                DatasetExperimentItemModel.status == "queued",
            )
            .values(status="running")
            .execution_options(synchronize_session=False)
        )
        if coordinator_token is not None:
            statement = statement.where(
                exists().where(
                    DatasetExperimentModel.id == experiment_id,
                    DatasetExperimentModel.status == "running",
                    DatasetExperimentModel.coordinator_token == coordinator_token,
                )
            )
        result = self.session.execute(statement)
        return int(result.rowcount or 0)

    def _next_attempt_number(self, item_id):
        current = self.session.scalar(
            select(func.max(DatasetExperimentAttemptModel.attempt_number)).where(
                DatasetExperimentAttemptModel.experiment_item_id == item_id
            )
        )
        return int(current or 0) + 1

    # ---------- Transaction B + durable first launch (G3-B) ----------

    def launch_item_attempt(self, *, experiment_id, item_id, attempt_id, analysis_service,
                            coordinator_token=None):
        """G3-B: durably claim first launch for ANY executor, then physically launch once.

        Revalidates frozen identity, validates the complete ownership chain
        Experiment -> Item -> Attempt -> AnalysisRun, then commits Transaction B
        (Attempt.launch_requested_at = now) with a conditional CAS for ALL
        executors. Only the CAS winner calls the sealed G2
        AnalysisService.launch_prepared_run primitive. For remote_gpu the marker is
        the first-launch claim / audit marker only; remote recovery continues to
        use coordinator-token rotation/fencing and never reads it. Never
        prepares/creates a Run, never retries, never recovers.
        """
        if analysis_service is None or getattr(analysis_service, "session", None) is not self.session:
            raise PlatformError(
                "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                "AnalysisService must share the DatasetExperimentService Session.",
                409,
            )

        # Frozen identity is revalidated because Transaction A and Transaction B
        # are distinct durable transactions; a first launch must not proceed if
        # identity drifted between them. Read-only; before any write lock.
        experiment = self.revalidate_frozen_identity(experiment_id)
        if experiment.status != "running":
            raise PlatformError(
                "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                "Experiment is not running; G3-B only launches a running experiment.",
                409,
            )

        item = self.session.get(DatasetExperimentItemModel, item_id)
        if item is None:
            raise PlatformError(
                "DATASET_EXPERIMENT_ITEM_NOT_FOUND", "Dataset experiment item was not found.", 404
            )
        if item.experiment_id != experiment.id:
            raise PlatformError(
                "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                "Item does not belong to the expected experiment.",
                409,
            )
        if item.status != "running":
            raise PlatformError(
                "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                "Only a running item can first-launch an attempt.",
                409,
            )

        attempt = self.session.get(DatasetExperimentAttemptModel, attempt_id)
        if attempt is None:
            raise PlatformError(
                "DATASET_EXPERIMENT_ATTEMPT_NOT_FOUND",
                "Dataset experiment attempt was not found.",
                404,
            )
        if attempt.experiment_item_id != item.id:
            raise PlatformError(
                "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                "Attempt does not belong to the expected item.",
                409,
            )

        run = self.session.get(AnalysisRunModel, attempt.analysis_run_id)
        if run is None:
            raise PlatformError(
                "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                "Attempt does not reference a persisted analysis run.",
                409,
            )
        if run.recording_id != item.recording_id:
            raise PlatformError(
                "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                "AnalysisRun does not match the attempt's recording.",
                409,
            )
        if run.pipeline_id != experiment.plugin_id or run.pipeline_version != experiment.plugin_version:
            raise PlatformError(
                "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                "AnalysisRun pipeline identity does not match the frozen experiment.",
                409,
            )
        if run.executor != experiment.executor:
            raise PlatformError(
                "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                "AnalysisRun executor does not match the frozen experiment.",
                409,
            )
        if run.status != "pending":
            raise PlatformError(
                "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                "Only a pending analysis run can be first-launched.",
                409,
            )
        if run.worker_pid is not None:
            raise PlatformError(
                "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                "Analysis run already has a worker; it cannot be first-launched.",
                409,
            )

        now = datetime.now(timezone.utc)
        try:
            with self.session.no_autoflush:
                claimed = self._claim_launch_intent(
                    attempt_id=attempt_id, item_id=item_id, requested_at=now,
                    experiment_id=experiment.id, coordinator_token=coordinator_token,
                )
                if claimed != 1:
                    if coordinator_token is not None:
                        self._require_experiment_generation(experiment.id, coordinator_token)
                    raise PlatformError(
                        "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                        "Launch intent is already recorded or the attempt is not eligible.",
                        409,
                    )
            self.session.commit()  # Transaction B (all executors)
        except Exception:
            self.session.rollback()
            raise

        analysis_service.launch_prepared_run(run.id)
        self.session.refresh(attempt)
        return attempt

    def _claim_launch_intent(self, *, attempt_id, item_id, requested_at,
                             experiment_id=None, coordinator_token=None):
        statement = (
            update(DatasetExperimentAttemptModel)
            .where(
                DatasetExperimentAttemptModel.id == attempt_id,
                DatasetExperimentAttemptModel.experiment_item_id == item_id,
                DatasetExperimentAttemptModel.launch_requested_at.is_(None),
            )
            .values(launch_requested_at=requested_at)
            .execution_options(synchronize_session=False)
        )
        if coordinator_token is not None:
            statement = statement.where(
                exists().where(
                    DatasetExperimentModel.id == experiment_id,
                    DatasetExperimentModel.status == "running",
                    DatasetExperimentModel.coordinator_token == coordinator_token,
                )
            )
        result = self.session.execute(statement)
        return int(result.rowcount or 0)

    # ---------- reconciliation + scheduling selection (G3-C) ----------

    def _load_attempts_by_item(self, item_ids):
        attempts = list(
            self.session.scalars(
                select(DatasetExperimentAttemptModel)
                .where(DatasetExperimentAttemptModel.experiment_item_id.in_(item_ids))
                .order_by(
                    DatasetExperimentAttemptModel.experiment_item_id,
                    DatasetExperimentAttemptModel.attempt_number,
                )
            ).all()
        )
        by_item: dict[str, list] = {}
        for attempt in attempts:
            by_item.setdefault(attempt.experiment_item_id, []).append(attempt)
        return by_item

    def _load_runs_by_id(self, run_ids):
        if not run_ids:
            return {}
        return {
            run.id: run
            for run in self.session.scalars(
                select(AnalysisRunModel).where(AnalysisRunModel.id.in_(run_ids))
            ).all()
        }

    def reconcile_items(self, experiment_id, coordinator_token=None):
        try:
            if coordinator_token is not None:
                fence = self.session.execute(
                    update(DatasetExperimentModel)
                    .where(
                        DatasetExperimentModel.id == experiment_id,
                        DatasetExperimentModel.status == "running",
                        DatasetExperimentModel.coordinator_token == coordinator_token,
                    )
                    .values(status="running")
                    .execution_options(synchronize_session=False)
                )
                if int(fence.rowcount or 0) != 1:
                    raise PlatformError(
                        "DATASET_EXPERIMENT_FENCE_LOST",
                        "Coordinator generation no longer owns the experiment.",
                        409,
                    )
            experiment = self.session.get(DatasetExperimentModel, experiment_id)
            if experiment is None:
                raise PlatformError(
                    "DATASET_EXPERIMENT_NOT_FOUND", "Dataset experiment was not found.", 404
                )
            items = list(
                self.session.scalars(
                    select(DatasetExperimentItemModel)
                    .where(DatasetExperimentItemModel.experiment_id == experiment_id)
                    .order_by(DatasetExperimentItemModel.manifest_order)
                ).all()
            )
            if not items:
                raise PlatformError(
                    "DATASET_EXPERIMENT_INVARIANT_VIOLATION", "Experiment has no items.", 409
                )
            attempts_by_item = self._load_attempts_by_item([item.id for item in items])
            run_ids = {
                attempt.analysis_run_id
                for attempts in attempts_by_item.values()
                for attempt in attempts
            }
            runs_by_id = self._load_runs_by_id(run_ids)
            now = datetime.now(timezone.utc)
            for item in items:
                attempts = attempts_by_item.get(item.id, [])
                for attempt in attempts:
                    run = runs_by_id.get(attempt.analysis_run_id)
                    if run is None:
                        raise PlatformError(
                            "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                            "Attempt references a missing analysis run.", 409,
                        )
                    if run.recording_id != item.recording_id:
                        raise PlatformError(
                            "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                            "Attempt run does not match the item recording.", 409,
                        )
                    if (
                        run.pipeline_id != experiment.plugin_id
                        or run.pipeline_version != experiment.plugin_version
                        or run.executor != experiment.executor
                    ):
                        raise PlatformError(
                            "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                            "Attempt run identity does not match the frozen experiment.", 409,
                        )
                latest = attempts[-1] if attempts else None
                latest_run = runs_by_id.get(latest.analysis_run_id) if latest else None
                active = [
                    attempt for attempt in attempts
                    if runs_by_id[attempt.analysis_run_id].status in {"pending", "running"}
                ]
                if item.status == "queued":
                    if active:
                        raise PlatformError(
                            "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                            "Queued item has an active attempt.", 409,
                        )
                    if latest is not None and latest_run.status not in {"failed", "interrupted"}:
                        raise PlatformError(
                            "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                            "Queued item has a non-retryable historical attempt.", 409,
                        )
                    continue
                if item.status == "running":
                    if latest is None or latest_run is None:
                        raise PlatformError(
                            "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                            "Running item has no authoritative attempt/run.", 409,
                        )
                    if latest_run.status in {"pending", "running"}:
                        if active != [latest]:
                            raise PlatformError(
                                "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                                "The active attempt is not the latest attempt.", 409,
                            )
                        continue
                    if latest_run.status == "completed":
                        if active:
                            raise PlatformError(
                                "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                                "Terminal latest attempt has an older active attempt.", 409,
                            )
                        item.status = "completed"
                        item.updated_at = now
                    elif latest_run.status in {"failed", "interrupted"}:
                        if active:
                            raise PlatformError(
                                "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                                "Terminal latest attempt has an older active attempt.", 409,
                            )
                        item.status = "failed"
                        item.last_error_type = latest_run.error_type
                        item.last_error_message = latest_run.error_message
                        item.updated_at = now
                    else:
                        raise PlatformError(
                            "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                            "Running item references an unknown run status.", 409,
                        )
                elif item.status == "completed":
                    if latest is None or latest_run is None or latest_run.status != "completed":
                        raise PlatformError(
                            "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                            "Completed item disagrees with its authoritative run.", 409,
                        )
                    if active:
                        raise PlatformError(
                            "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                            "Completed item retains an active attempt.", 409,
                        )
                elif item.status == "failed":
                    if latest is not None and (
                        latest_run is None or latest_run.status not in {"failed", "interrupted"}
                    ):
                        raise PlatformError(
                            "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                            "Failed item disagrees with its authoritative run.", 409,
                        )
                    if active:
                        raise PlatformError(
                            "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                            "Failed item retains an active attempt.", 409,
                        )
                else:
                    raise PlatformError(
                        "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                        "Item has an unknown status.", 409,
                    )
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        counts = {"queued": 0, "running": 0, "completed": 0, "failed": 0}
        for item in items:
            counts[item.status] = counts.get(item.status, 0) + 1
        return ReconcileSummary(
            expected=len(items), queued=counts["queued"], running=counts["running"],
            completed=counts["completed"], failed=counts["failed"],
        )

    def select_queued_items(self, experiment_id, limit):
        if limit <= 0:
            return []
        return list(
            self.session.scalars(
                select(DatasetExperimentItemModel.id)
                .where(
                    DatasetExperimentItemModel.experiment_id == experiment_id,
                    DatasetExperimentItemModel.status == "queued",
                )
                .order_by(DatasetExperimentItemModel.manifest_order.asc())
                .limit(limit)
            ).all()
        )

    # ---------- coordinator ownership + terminal/failure writes (G3-C) ----------

    def start_experiment(self, experiment_id, job_manager):
        experiment = self.session.get(DatasetExperimentModel, experiment_id)
        if experiment is None:
            raise PlatformError(
                "DATASET_EXPERIMENT_NOT_FOUND", "Dataset experiment was not found.", 404
            )
        token = f"coord_{uuid4().hex}"
        now = datetime.now(timezone.utc)
        try:
            with self.session.no_autoflush:
                claimed = self.session.execute(
                    update(DatasetExperimentModel)
                    .where(
                        DatasetExperimentModel.id == experiment_id,
                        DatasetExperimentModel.status == "pending",
                    )
                    .values(
                        status="running", coordinator_token=token, started_at=now,
                        heartbeat_at=now, worker_pid=None,
                        error_type=None, error_message=None,
                    )
                    .execution_options(synchronize_session=False)
                )
                claimed_count = int(claimed.rowcount or 0)
            if claimed_count != 1:
                self.session.rollback()
                raise PlatformError(
                    "DATASET_EXPERIMENT_INVALID_TRANSITION",
                    "Only a pending experiment can be started.",
                    409,
                )
            self.session.commit()
        except PlatformError:
            raise
        except Exception:
            self.session.rollback()
            raise

        try:
            worker_pid = job_manager.start(experiment_id, token)
        except Exception as exc:
            with self.session.no_autoflush:
                self.session.execute(
                    update(DatasetExperimentModel)
                    .where(
                        DatasetExperimentModel.id == experiment_id,
                        DatasetExperimentModel.coordinator_token == token,
                    )
                    .values(
                        status="failed", worker_pid=None,
                        error_type="DATASET_EXPERIMENT_ORCHESTRATION_FAILED",
                        error_message=str(exc)[:1000],
                        completed_at=datetime.now(timezone.utc),
                    )
                    .execution_options(synchronize_session=False)
                )
            self.session.commit()
            raise PlatformError(
                "DATASET_EXPERIMENT_ORCHESTRATION_FAILED",
                "Unable to start DatasetExperiment coordinator.",
            ) from exc

        try:
            with self.session.no_autoflush:
                self.session.execute(
                    update(DatasetExperimentModel)
                    .where(
                        DatasetExperimentModel.id == experiment_id,
                        DatasetExperimentModel.coordinator_token == token,
                        DatasetExperimentModel.status == "running",
                    )
                    .values(worker_pid=worker_pid)
                    .execution_options(synchronize_session=False)
                )
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        self.session.expire_all()
        return self.session.get(DatasetExperimentModel, experiment_id)

    def refresh_coordinator_heartbeat(self, experiment_id, coordinator_token):
        try:
            with self.session.no_autoflush:
                result = self.session.execute(
                    update(DatasetExperimentModel)
                    .where(
                        DatasetExperimentModel.id == experiment_id,
                        DatasetExperimentModel.coordinator_token == coordinator_token,
                        DatasetExperimentModel.status == "running",
                    )
                    .values(heartbeat_at=datetime.now(timezone.utc))
                    .execution_options(synchronize_session=False)
                )
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return int(result.rowcount or 0) == 1

    def mark_experiment_completed_with_failures(self, experiment_id, coordinator_token):
        try:
            with self.session.no_autoflush:
                result = self.session.execute(
                    update(DatasetExperimentModel)
                    .where(
                        DatasetExperimentModel.id == experiment_id,
                        DatasetExperimentModel.coordinator_token == coordinator_token,
                        DatasetExperimentModel.status == "running",
                    )
                    .values(
                        status="completed_with_failures",
                        completed_at=datetime.now(timezone.utc),
                        error_type=None, error_message=None,
                    )
                    .execution_options(synchronize_session=False)
                )
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return int(result.rowcount or 0) == 1

    def _fail_experiment(self, experiment_id, coordinator_token, error_type, error_message):
        try:
            with self.session.no_autoflush:
                result = self.session.execute(
                    update(DatasetExperimentModel)
                    .where(
                        DatasetExperimentModel.id == experiment_id,
                        DatasetExperimentModel.coordinator_token == coordinator_token,
                        DatasetExperimentModel.status == "running",
                    )
                    .values(
                        status="failed", error_type=error_type,
                        error_message=(error_message or "")[:1000],
                        completed_at=datetime.now(timezone.utc),
                    )
                    .execution_options(synchronize_session=False)
                )
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return int(result.rowcount or 0) == 1

    def _mark_item_failed(self, item_id, error_type, error_message, *,
                          experiment_id, coordinator_token=None):
        statement = (
            update(DatasetExperimentItemModel)
            .where(
                DatasetExperimentItemModel.id == item_id,
                DatasetExperimentItemModel.experiment_id == experiment_id,
                DatasetExperimentItemModel.status.in_(("queued", "running")),
            )
            .values(
                status="failed", last_error_type=error_type,
                last_error_message=(error_message or "")[:1000],
                updated_at=datetime.now(timezone.utc),
            )
            .execution_options(synchronize_session=False)
        )
        if coordinator_token is not None:
            statement = statement.where(
                exists().where(
                    DatasetExperimentModel.id == experiment_id,
                    DatasetExperimentModel.status == "running",
                    DatasetExperimentModel.coordinator_token == coordinator_token,
                )
            )
        try:
            result = self.session.execute(statement)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        if int(result.rowcount or 0) == 1:
            return True
        if coordinator_token is not None:
            self._require_experiment_generation(experiment_id, coordinator_token)
        row = self.session.execute(
            select(DatasetExperimentItemModel.status, DatasetExperimentItemModel.experiment_id)
            .where(DatasetExperimentItemModel.id == item_id)
        ).one_or_none()
        if row is None:
            raise PlatformError(
                "DATASET_EXPERIMENT_ITEM_NOT_FOUND", "Dataset experiment item was not found.", 404
            )
        if row[1] != experiment_id:
            raise PlatformError(
                "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                "Item does not belong to the expected experiment.",
                409,
            )
        if row[0] in {"completed", "failed"}:
            return False
        raise PlatformError(
            "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
            "Item cannot be marked failed from its current state.",
            409,
        )
