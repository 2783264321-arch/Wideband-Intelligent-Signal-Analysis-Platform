from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import exists, func, select, update
from sqlalchemy.orm import Session

from app.analysis.model import AnalysisRunModel
from app.benchmarks.model import DatasetEvaluationItemModel, DatasetEvaluationModel
from app.benchmarks.service import DatasetBenchmarkService, resolve_protocol_config
from app.core.errors import PlatformError
from app.datasets.analysis_manifest import build_dataset_analysis_manifest
from app.datasets.model import DatasetModel
from app.datasets.projection import DatasetProjectionResolver
from app.dataset_experiments.model import (
    DatasetExperimentAttemptModel,
    DatasetExperimentItemModel,
    DatasetExperimentModel,
)
from app.dataset_experiments.schema import (    DatasetExperimentAttemptRead,
    DatasetExperimentItemRead,
    DatasetExperimentRead,
)
from app.execution_selection.resolver import resolve_auto_execution
from app.pipelines.plugin import validate_plugin_parameters
from app.recordings.model import RecordingModel
from app.remote_execution.runtime import FROZEN_AUTHORITY_ITEM_CODES, RuntimeDescriptor

if TYPE_CHECKING:
    from app.analysis.service import AnalysisService

_ITEM_STATUSES = ("queued", "running", "completed", "failed")

# A1.1: a changed frozen runtime generation is a systemic execution-identity
# change (experiment-level), unlike a recoverable per-item provider/certificate
# loss (FROZEN_AUTHORITY_ITEM_CODES). It is never added to that item-code set.
_RUNTIME_DESCRIPTOR_INVALID = "RUNTIME_DESCRIPTOR_INVALID"


def cas_fail_closed_pending_run(session, *, experiment_id, coordinator_token,
                                item_id, attempt_id, run_id, error_type, error_message):
    """Generation-fenced ``pending -> interrupted`` for an unlaunched run.

    Fence: experiment running + coordinator_token AND item running AND attempt
    binds the item/run. Returns ``"interrupted"`` or ``"already_interrupted"``;
    raises ``DATASET_EXPERIMENT_FENCE_LOST`` for a stale generation.
    """
    statement = (
        update(AnalysisRunModel)
        .where(
            AnalysisRunModel.id == run_id,
            AnalysisRunModel.status == "pending",
            exists().where(
                DatasetExperimentModel.id == experiment_id,
                DatasetExperimentModel.status == "running",
                DatasetExperimentModel.coordinator_token == coordinator_token,
            ),
            exists().where(
                DatasetExperimentItemModel.id == item_id,
                DatasetExperimentItemModel.experiment_id == experiment_id,
                DatasetExperimentItemModel.status == "running",
            ),
            exists().where(
                DatasetExperimentAttemptModel.id == attempt_id,
                DatasetExperimentAttemptModel.experiment_item_id == item_id,
                DatasetExperimentAttemptModel.analysis_run_id == run_id,
            ),
        )
        .values(
            status="interrupted",
            error_type=error_type,
            error_message=error_message,
            finished_at=datetime.now(timezone.utc),
        )
        .execution_options(synchronize_session=False)
    )
    try:
        result = session.execute(statement)
        rowcount = int(result.rowcount or 0)
        session.commit()
    except Exception:
        session.rollback()
        raise
    if rowcount == 1:
        return "interrupted"

    generation = session.execute(
        select(DatasetExperimentModel.status, DatasetExperimentModel.coordinator_token)
        .where(DatasetExperimentModel.id == experiment_id)
    ).one_or_none()
    if generation is None or generation[0] != "running" or generation[1] != coordinator_token:
        raise PlatformError(
            "DATASET_EXPERIMENT_FENCE_LOST",
            "Coordinator generation no longer owns the experiment.",
            409,
        )
    run_status = session.execute(
        select(AnalysisRunModel.status).where(AnalysisRunModel.id == run_id)
    ).scalar_one_or_none()
    if run_status == "interrupted":
        return "already_interrupted"
    if run_status is None:
        raise PlatformError(
            "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
            "Unlaunched run no longer exists.",
            409,
        )
    raise PlatformError(
        "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
        "Unlaunched run could not be terminalized from its current state.",
        409,
    )


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
        items = [item for item, _ in rows]
        attempts_by_item = self._load_attempts_by_item([item.id for item in items])
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
                latest_analysis_run_id=(
                    attempts_by_item[item.id][-1].analysis_run_id
                    if attempts_by_item.get(item.id) else None
                ),
            )
            for item, recording_name in rows
        ]

    def list_attempts(self, item_id: str, *, experiment_id: str | None = None) -> list[DatasetExperimentAttemptRead]:
        item = self.session.get(DatasetExperimentItemModel, item_id)
        if item is None or (experiment_id is not None and item.experiment_id != experiment_id):
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
        selection_meta = dict(experiment.runtime_descriptor_json or {}).get("execution_selection") or {}
        return DatasetExperimentRead(
            id=experiment.id,
            name=experiment.name,
            dataset_name=experiment.dataset_name,
            dataset_split=experiment.dataset_split,
            dataset_label_space=experiment.dataset_label_space,
            dataset_projection_id=experiment.dataset_projection_id,
            dataset_id=experiment.dataset_id,
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
            requested_execution_mode=selection_meta.get("requested_execution_mode"),
            auto_reason_code=selection_meta.get("auto_reason_code"),
            auto_reason=selection_meta.get("auto_reason"),
            workload_class=selection_meta.get("workload_class"),
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
        dataset_name=None,
        dataset_split=None,
        dataset_label_space=None,
        plugin_id,
        plugin_version,
        executor=None,
        parameters,
        evaluation_protocol,
        max_concurrency,
        model_release_id=None,
        execution_mode="manual",
        dataset_projection_id=None,
        dataset_id=None,
    ):
        if max_concurrency < 1:
            raise PlatformError(
                "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                "max_concurrency must be >= 1.",
                422,
            )
        if execution_mode not in ("manual", "auto"):
            raise PlatformError(
                "EXECUTION_REQUEST_INVALID", "execution_mode must be 'manual' or 'auto'."
            )

        if dataset_id is not None:
            if dataset_projection_id is not None:
                raise PlatformError(
                    "EXECUTION_REQUEST_INVALID",
                    "dataset_id and dataset_projection_id are mutually exclusive.",
                )
            model = self.session.get(DatasetModel, dataset_id)
            if model is None:
                raise PlatformError("DATASET_NOT_FOUND", "Dataset was not found.", 404)
            derived_name = model.name
            derived_split = model.split
            derived_label_space = model.label_space or ""
            if dataset_name is not None and dataset_name != derived_name:
                raise PlatformError(
                    "EXECUTION_REQUEST_INVALID",
                    "Supplied dataset_name does not match the dataset authority.",
                )
            if dataset_split is not None and dataset_split != derived_split:
                raise PlatformError(
                    "EXECUTION_REQUEST_INVALID",
                    "Supplied dataset_split does not match the dataset authority.",
                )
            if dataset_label_space is not None and dataset_label_space != derived_label_space:
                raise PlatformError(
                    "EXECUTION_REQUEST_INVALID",
                    "Supplied dataset_label_space does not match the dataset authority.",
                )
            dataset_name = derived_name
            dataset_split = derived_split
            dataset_label_space = derived_label_space
            manifest = build_dataset_analysis_manifest(self.session, dataset_id)
        elif dataset_projection_id is not None:
            projection = DatasetProjectionResolver(self.session).get(dataset_projection_id)
            if dataset_name is not None and dataset_name != projection.dataset_name:
                raise PlatformError(
                    "EXECUTION_REQUEST_INVALID",
                    "Supplied dataset_name does not match the dataset projection.",
                )
            if dataset_split is not None and dataset_split != projection.dataset_split:
                raise PlatformError(
                    "EXECUTION_REQUEST_INVALID",
                    "Supplied dataset_split does not match the dataset projection.",
                )
            if dataset_label_space is not None and dataset_label_space != (projection.label_space or ""):
                raise PlatformError(
                    "EXECUTION_REQUEST_INVALID",
                    "Supplied dataset_label_space does not match the dataset projection.",
                )
            dataset_name = projection.dataset_name
            dataset_split = projection.dataset_split
            dataset_label_space = projection.label_space or ""
            manifest = DatasetBenchmarkService(self.session).prepare_projection_manifest(
                dataset_projection_id
            )
        else:
            if dataset_name is None or dataset_split is None or dataset_label_space is None:
                raise PlatformError(
                    "EXECUTION_REQUEST_INVALID",
                    "Dataset identity requires dataset_projection_id or the "
                    "dataset_name/split/label_space triple.",
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

        selection = None
        if execution_mode == "auto":
            if executor is not None:
                raise PlatformError(
                    "EXECUTION_REQUEST_INVALID",
                    "execution_mode='auto' must not specify an executor.",
                )
            if self.executor_registry is None:
                raise PlatformError(
                    "EXECUTION_CAPABILITY_UNAVAILABLE", "Executor registry is not configured."
                )
            if not manifest.entries:
                raise PlatformError(
                    "DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                    "Auto execution requires at least one dataset recording.",
                    409,
                )
            probe_recording = self.session.get(RecordingModel, manifest.entries[0].recording_id)
            if probe_recording is None:
                raise PlatformError("RECORDING_NOT_FOUND", "Recording was not found.", 404)
            selection = resolve_auto_execution(
                definition=definition,
                model_release=resolved_release,
                probe_recording=probe_recording,
                executor_registry=self.executor_registry,
                dataset_item_count=manifest.expected_recordings,
            )
            if selection.resolved_executor is None:
                raise PlatformError(selection.reason_code, selection.reason)
            frozen_executor = selection.resolved_executor
        else:
            if executor is None:
                raise PlatformError(
                    "EXECUTION_REQUEST_INVALID",
                    "manual execution requires an explicit executor.",
                )
            frozen_executor = executor

        provider, descriptor = self._validate_execution(definition, frozen_release_id, frozen_executor)

        provenance = {
            "requested_execution_mode": execution_mode,
            "resolved_executor": provider.name,
        }
        if selection is not None:
            provenance.update({
                "auto_reason_code": selection.reason_code,
                "auto_reason": selection.reason,
                "workload_class": selection.workload_class,
            })
        runtime_descriptor_json = {
            **descriptor.to_metadata(),
            "execution_selection": provenance,
        }

        experiment = DatasetExperimentModel(
            id=f"exp_{uuid4().hex}",
            name=name,
            dataset_name=dataset_name,
            dataset_split=dataset_split,
            dataset_label_space=dataset_label_space,
            dataset_projection_id=dataset_projection_id,
            dataset_id=dataset_id,
            recording_manifest_hash=manifest.recording_manifest_hash,
            plugin_id=definition.plugin_id,
            plugin_version=definition.plugin_version,
            model_release_id=frozen_release_id,
            asset_manifest_sha256=frozen_asset_sha,
            parameters_json=dict(parameters),
            executor=provider.name,
            runtime_descriptor_json=runtime_descriptor_json,
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
            if experiment.dataset_id is not None:
                manifest = build_dataset_analysis_manifest(self.session, experiment.dataset_id)
            elif experiment.dataset_projection_id is not None:
                manifest = DatasetBenchmarkService(self.session).prepare_projection_manifest(
                    experiment.dataset_projection_id
                )
            else:
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

        # NOTE (Plan A1 / P3; A1.1): frozen executor authority (provider
        # registration, exact certificate, provider descriptor match) is not part
        # of experiment identity revalidation here; it is validated by the shared
        # `_validate_frozen_execution_authority` helper before BOTH durable
        # transactions (Transaction A in `start_item_attempt`, Transaction B in
        # `launch_item_attempt`), so a changed runtime generation is rejected
        # before either write. Dataset/release identity checks above remain
        # experiment-level.
        return experiment

    def _validate_frozen_execution_authority(self, experiment):
        """Cheap, deterministic frozen runtime-authority check. Never probes.

        Resolves the exact frozen plugin definition, parses the frozen runtime
        descriptor, and requires the current deployment provider to match it
        exactly (full ``RuntimeDescriptor.to_metadata()``) with an exact
        certificate. Used before Transaction A and before Transaction B so a
        runtime generation change is rejected before either durable write.

        Raises ``RUNTIME_DESCRIPTOR_INVALID`` / ``EXECUTION_NOT_CERTIFIED`` /
        ``EXECUTION_CAPABILITY_UNAVAILABLE`` (or
        ``DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED`` on plugin-version
        drift). Never substitutes executor/runtime and never probes.
        """
        definition = self.registry.get(experiment.plugin_id).definition
        if definition.version != experiment.plugin_version:
            raise PlatformError(
                "DATASET_EXPERIMENT_EXECUTION_IDENTITY_CHANGED",
                "Active plugin version no longer matches the frozen plugin version.",
                409,
            )
        frozen_descriptor = RuntimeDescriptor.from_metadata(experiment.runtime_descriptor_json)
        self.executor_registry.validate_frozen_execution_authority(
            definition, experiment.model_release_id, experiment.executor, frozen_descriptor
        )

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

        # Pre-Transaction-A frozen runtime-authority revalidation (A1.1). A changed
        # runtime generation must not create a durable pending Run at all; reject
        # it here, before any external I/O or write lock, so no Attempt/Run/worker
        # is ever created and the Item stays queued.
        self._validate_frozen_execution_authority(experiment)

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

        # Pre-intent frozen execution-authority revalidation (Plan A1 / P3; A1.1).
        # Must happen BEFORE Transaction B claims the launch intent, so an
        # unavailable authority never leaves a durable intent with no worker.
        try:
            self._validate_frozen_execution_authority(experiment)
        except PlatformError as exc:
            if coordinator_token is not None:
                if exc.code in FROZEN_AUTHORITY_ITEM_CODES:
                    self.fail_closed_unlaunched_run(
                        experiment_id=experiment.id,
                        coordinator_token=coordinator_token,
                        item_id=item.id,
                        attempt_id=attempt.id,
                        run_id=run.id,
                        error_type=exc.code,
                        error_message=exc.message,
                    )
                elif exc.code == _RUNTIME_DESCRIPTOR_INVALID:
                    # Window B (A1.1): Transaction A already committed a pending
                    # Run bound to a running Item, but the frozen runtime
                    # generation changed. RUNTIME_DESCRIPTOR_INVALID stays
                    # experiment-level, so the coordinator will NOT fail the owned
                    # Item; terminalize the owned Run and fail the owned Item here
                    # under the same generation fencing, then re-raise so the
                    # Experiment is failed without orphaning Run/Item.
                    self.fail_closed_unlaunched_run(
                        experiment_id=experiment.id,
                        coordinator_token=coordinator_token,
                        item_id=item.id,
                        attempt_id=attempt.id,
                        run_id=run.id,
                        error_type=exc.code,
                        error_message=exc.message,
                    )
                    self._mark_item_failed(
                        item.id, exc.code, exc.message,
                        experiment_id=experiment.id, coordinator_token=coordinator_token,
                    )
            raise

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

        # Physical launch (A1.1 Window C). A local frozen-authority loss
        # discovered HERE — runtime generation drift between the Transaction B
        # intent commit and this launch — propagates as experiment-level
        # RUNTIME_DESCRIPTOR_INVALID. AnalysisService has already terminalized the
        # owned local Run; the owned Item must also be failed under the same
        # generation fencing so a terminal Experiment never retains a running
        # Item. The durable launch intent is historical truth: do not clear
        # launch_requested_at, and do not rewrite the Run.
        try:
            analysis_service.launch_prepared_run(run.id)
        except PlatformError as exc:
            if exc.code == _RUNTIME_DESCRIPTOR_INVALID and coordinator_token is not None:
                self._mark_item_failed(
                    item.id, exc.code, exc.message,
                    experiment_id=experiment.id, coordinator_token=coordinator_token,
                )
            raise
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

    def fail_closed_unlaunched_run(self, *, experiment_id, coordinator_token,
                                   item_id, attempt_id, run_id,
                                   error_type, error_message):
        """Generation-fenced ``pending -> interrupted`` for an unlaunched run."""
        return cas_fail_closed_pending_run(
            self.session,
            experiment_id=experiment_id,
            coordinator_token=coordinator_token,
            item_id=item_id,
            attempt_id=attempt_id,
            run_id=run_id,
            error_type=error_type,
            error_message=error_message,
        )

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

    def refresh_coordinator_heartbeat(self, experiment_id, coordinator_token,
                                      *, expected_status="running"):
        try:
            with self.session.no_autoflush:
                result = self.session.execute(
                    update(DatasetExperimentModel)
                    .where(
                        DatasetExperimentModel.id == experiment_id,
                        DatasetExperimentModel.coordinator_token == coordinator_token,
                        DatasetExperimentModel.status == expected_status,
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

    def retry_failed(self, experiment_id, job_manager):
        """G4 explicit Retry Failed. Valid only from ``completed_with_failures``.

        One durable transaction: revalidate status, requeue failed Items only,
        clear retryable Item error projection, transition the Experiment back to
        ``running`` under a fresh coordinator generation, and clear the terminal
        orchestration projection. Creates NO Attempt and NO AnalysisRun.

        Then, outside the transaction: spawn the new coordinator, then persist
        ``worker_pid`` under the fresh-generation guard. A spawn failure
        generation-fenced-restores the pre-retry terminal Experiment projection
        and the requeued Items, but ONLY if this generation still owns the
        Experiment (see ``_restore_retry_failed``).
        """
        experiment = self.session.get(DatasetExperimentModel, experiment_id)
        if experiment is None:
            raise PlatformError(
                "DATASET_EXPERIMENT_NOT_FOUND", "Dataset experiment was not found.", 404
            )
        if experiment.status != "completed_with_failures":
            raise PlatformError(
                "DATASET_EXPERIMENT_INVALID_TRANSITION",
                "Only a completed_with_failures experiment can retry failed items.",
                409,
            )

        terminal_snapshot = {
            "completed_at": experiment.completed_at,
            "heartbeat_at": experiment.heartbeat_at,
            "coordinator_token": experiment.coordinator_token,
            "worker_pid": experiment.worker_pid,
            "error_type": experiment.error_type,
            "error_message": experiment.error_message,
        }

        failed_rows = self.session.execute(
            select(
                DatasetExperimentItemModel.id,
                DatasetExperimentItemModel.last_error_type,
                DatasetExperimentItemModel.last_error_message,
            ).where(
                DatasetExperimentItemModel.experiment_id == experiment_id,
                DatasetExperimentItemModel.status == "failed",
            )
        ).all()
        if not failed_rows:
            raise PlatformError(
                "DATASET_EXPERIMENT_INVALID_TRANSITION",
                "Experiment has no failed items to retry.",
                409,
            )
        snapshot = [(row[0], row[1], row[2]) for row in failed_rows]

        token = f"coord_{uuid4().hex}"
        now = datetime.now(timezone.utc)
        try:
            claimed_count = 0
            with self.session.no_autoflush:
                claimed = self.session.execute(
                    update(DatasetExperimentModel)
                    .where(
                        DatasetExperimentModel.id == experiment_id,
                        DatasetExperimentModel.status == "completed_with_failures",
                    )
                    .values(
                        status="running",
                        coordinator_token=token,
                        worker_pid=None,
                        heartbeat_at=now,
                        completed_at=None,
                        error_type=None,
                        error_message=None,
                    )
                    .execution_options(synchronize_session=False)
                )
                claimed_count = int(claimed.rowcount or 0)
                if claimed_count == 1:
                    self.session.execute(
                        update(DatasetExperimentItemModel)
                        .where(
                            DatasetExperimentItemModel.experiment_id == experiment_id,
                            DatasetExperimentItemModel.status == "failed",
                        )
                        .values(
                            status="queued",
                            last_error_type=None,
                            last_error_message=None,
                            updated_at=now,
                        )
                        .execution_options(synchronize_session=False)
                    )
            if claimed_count != 1:
                self.session.rollback()
                raise PlatformError(
                    "DATASET_EXPERIMENT_INVALID_TRANSITION",
                    "Only a completed_with_failures experiment can retry failed items.",
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
            restored = self._restore_retry_failed(
                experiment_id, token, snapshot, terminal_snapshot
            )
            if not restored:
                raise PlatformError(
                    "DATASET_EXPERIMENT_FENCE_LOST",
                    "Retry Failed lost its coordinator generation before the spawn "
                    "failure could be compensated; a newer generation owns the "
                    "experiment and no Item was mutated.",
                    409,
                ) from exc
            raise PlatformError(
                "DATASET_EXPERIMENT_ORCHESTRATION_FAILED",
                "Unable to start DatasetExperiment coordinator for retry.",
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

    def _restore_retry_failed(self, experiment_id, coordinator_token,
                              item_snapshot, terminal_snapshot):
        """Generation-fenced compensation for a Retry Failed spawn failure.

        The compensation transaction FIRST must durably re-acquire ownership of
        the retry generation:

            CAS UPDATE dataset_experiments
                SET status='completed_with_failures',
                    coordinator_token = terminal_snapshot token,
                    worker_pid = terminal_snapshot pid,
                    heartbeat_at = terminal_snapshot heartbeat,
                    completed_at = terminal_snapshot completed_at,
                    error_type = terminal_snapshot error_type,
                    error_message = terminal_snapshot error_message
                WHERE id=:id AND coordinator_token=:retry_token AND status='running'

        Only when that UPDATE affects exactly one row (rowcount == 1) does this
        caller still own the generation, and only THEN are the requeued Items
        restored inside the SAME transaction. Each Item UPDATE also requires
        ``id`` AND ``experiment_id`` AND the expected retry-created state
        ``status='queued'``.

        If the ownership CAS affects zero rows (a newer generation already took
        over), the transaction is rolled back immediately, ZERO Items are
        mutated, and this returns ``False`` so the caller raises
        ``DATASET_EXPERIMENT_FENCE_LOST`` instead of clobbering the newer owner.

        Restoring ``completed_at``/``heartbeat_at``/``coordinator_token``/
        ``worker_pid``/``error_type``/``error_message`` from the pre-retry
        terminal snapshot makes this a genuine terminal-projection restore, not a
        normalized re-write.

        Used only when ``job_manager.start`` raises, which proves no coordinator
        process was created.
        """
        now = datetime.now(timezone.utc)
        try:
            with self.session.no_autoflush:
                claimed = self.session.execute(
                    update(DatasetExperimentModel)
                    .where(
                        DatasetExperimentModel.id == experiment_id,
                        DatasetExperimentModel.coordinator_token == coordinator_token,
                        DatasetExperimentModel.status == "running",
                    )
                    .values(
                        status="completed_with_failures",
                        coordinator_token=terminal_snapshot["coordinator_token"],
                        worker_pid=terminal_snapshot["worker_pid"],
                        heartbeat_at=terminal_snapshot["heartbeat_at"],
                        completed_at=terminal_snapshot["completed_at"],
                        error_type=terminal_snapshot["error_type"],
                        error_message=terminal_snapshot["error_message"],
                    )
                    .execution_options(synchronize_session=False)
                )
                if int(claimed.rowcount or 0) != 1:
                    self.session.rollback()
                    return False
                for item_id, error_type, error_message in item_snapshot:
                    self.session.execute(
                        update(DatasetExperimentItemModel)
                        .where(
                            DatasetExperimentItemModel.id == item_id,
                            DatasetExperimentItemModel.experiment_id == experiment_id,
                            DatasetExperimentItemModel.status == "queued",
                        )
                        .values(
                            status="failed",
                            last_error_type=error_type,
                            last_error_message=error_message,
                            updated_at=now,
                        )
                        .execution_options(synchronize_session=False)
                    )
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return True

    def _fail_experiment(self, experiment_id, coordinator_token, error_type,
                         error_message, *, expected_status="running"):
        try:
            with self.session.no_autoflush:
                result = self.session.execute(
                    update(DatasetExperimentModel)
                    .where(
                        DatasetExperimentModel.id == experiment_id,
                        DatasetExperimentModel.coordinator_token == coordinator_token,
                        DatasetExperimentModel.status == expected_status,
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

    # ---------- G5 evaluation ownership ----------

    def experiment_evaluation_eligible(self, experiment_id) -> bool:
        """Evaluation requires complete GT coverage across all frozen items.

        Dataset Analysis itself never requires GT; this only gates the optional
        linked Evaluation. Legacy projection experiments are all-GT by
        construction, so their behavior is unchanged.
        """
        recordings = list(
            self.session.scalars(
                select(RecordingModel)
                .join(
                    DatasetExperimentItemModel,
                    DatasetExperimentItemModel.recording_id == RecordingModel.id,
                )
                .where(DatasetExperimentItemModel.experiment_id == experiment_id)
            ).all()
        )
        if not recordings:
            return False
        return all(recording.has_ground_truth for recording in recordings)

    def mark_experiment_completed_without_evaluation(self, experiment_id, coordinator_token):
        """Generation-fenced ``running -> completed`` with NO linked evaluation."""
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
                        status="completed",
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

    def mark_experiment_completed(self, experiment_id, coordinator_token):
        try:
            with self.session.no_autoflush:
                result = self.session.execute(
                    update(DatasetExperimentModel)
                    .where(
                        DatasetExperimentModel.id == experiment_id,
                        DatasetExperimentModel.coordinator_token == coordinator_token,
                        DatasetExperimentModel.status == "evaluating",
                    )
                    .values(status="completed",
                            completed_at=datetime.now(timezone.utc),
                            error_type=None, error_message=None)
                    .execution_options(synchronize_session=False)
                )
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return int(result.rowcount or 0) == 1

    def link_evaluation(self, experiment_id, evaluation_id, coordinator_token):
        try:
            with self.session.no_autoflush:
                result = self.session.execute(
                    update(DatasetExperimentModel)
                    .where(
                        DatasetExperimentModel.id == experiment_id,
                        DatasetExperimentModel.coordinator_token == coordinator_token,
                        DatasetExperimentModel.status == "running",
                        DatasetExperimentModel.dataset_evaluation_id.is_(None),
                    )
                    .values(dataset_evaluation_id=evaluation_id, status="evaluating")
                    .execution_options(synchronize_session=False)
                )
            rowcount = int(result.rowcount or 0)
            if rowcount != 1:
                self.session.rollback()
                return False
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return True

    def build_evaluation_membership(self, experiment_id):
        experiment = self._get(experiment_id)
        items = list(self.session.scalars(
            select(DatasetExperimentItemModel)
            .where(DatasetExperimentItemModel.experiment_id == experiment_id)
            .order_by(DatasetExperimentItemModel.manifest_order)
        ).all())
        if not items:
            raise PlatformError("DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                                "Experiment has no items.", 409)
        attempts_by_item = self._load_attempts_by_item([item.id for item in items])
        run_ids = {
            attempt.analysis_run_id
            for attempts in attempts_by_item.values() for attempt in attempts
        }
        runs_by_id = self._load_runs_by_id(run_ids)
        membership = []
        for item in items:
            if item.status != "completed":
                raise PlatformError("DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                                    "Evaluation requires every item completed.", 409)
            attempts = attempts_by_item.get(item.id, [])
            if not attempts:
                raise PlatformError("DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                                    "Completed item has no attempt.", 409)
            latest = attempts[-1]
            run = runs_by_id.get(latest.analysis_run_id)
            if run is None or run.status != "completed":
                raise PlatformError("DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                                    "Latest attempt has no completed AnalysisRun.", 409)
            if run.recording_id != item.recording_id:
                raise PlatformError("DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                                    "AnalysisRun recording mismatch.", 409)
            if (run.pipeline_id != experiment.plugin_id
                    or run.pipeline_version != experiment.plugin_version
                    or run.executor != experiment.executor):
                raise PlatformError("DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                                    "AnalysisRun identity does not match the frozen experiment.", 409)
            membership.append({"recording_id": item.recording_id,
                               "analysis_run_id": run.id})
        return membership

    def validate_evaluation_linkage(self, experiment_id):
        experiment = self._get(experiment_id)
        evaluation_id = experiment.dataset_evaluation_id
        if evaluation_id is None:
            raise PlatformError("DATASET_EXPERIMENT_ORCHESTRATION_FAILED",
                                "Experiment has no linked DatasetEvaluation.", 409)
        evaluation = self.session.get(DatasetEvaluationModel, evaluation_id)
        if evaluation is None:
            raise PlatformError("DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                                "Linked DatasetEvaluation is missing.", 409)
        if evaluation.recording_manifest_hash != experiment.recording_manifest_hash:
            raise PlatformError("DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                                "Linked evaluation manifest hash mismatch.", 409)
        if experiment.dataset_id is not None and evaluation.dataset_id != experiment.dataset_id:
            raise PlatformError("DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                                "Linked evaluation dataset_id mismatch.", 409)
        if (evaluation.dataset_name != experiment.dataset_name
                or evaluation.dataset_split != experiment.dataset_split
                or evaluation.label_space != experiment.dataset_label_space):
            raise PlatformError("DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                                "Linked evaluation dataset identity mismatch.", 409)
        if evaluation.evaluation_protocol != experiment.evaluation_protocol:
            raise PlatformError("DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                                "Linked evaluation protocol mismatch.", 409)
        if (evaluation.pipeline_id != experiment.plugin_id
                or evaluation.pipeline_version != experiment.plugin_version):
            raise PlatformError("DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                                "Linked evaluation pipeline identity mismatch.", 409)
        experiment_items = list(self.session.scalars(
            select(DatasetExperimentItemModel)
            .where(DatasetExperimentItemModel.experiment_id == experiment_id)
            .order_by(DatasetExperimentItemModel.manifest_order)
        ).all())
        evaluation_items = list(self.session.scalars(
            select(DatasetEvaluationItemModel)
            .where(DatasetEvaluationItemModel.evaluation_id == evaluation_id)
            .order_by(DatasetEvaluationItemModel.manifest_order)
        ).all())
        membership = self.build_evaluation_membership(experiment_id)
        if (len(evaluation_items) != len(experiment_items)
                or len(membership) != len(experiment_items)):
            raise PlatformError("DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                                "Linked evaluation membership size mismatch.", 409)
        experiment_order = [item.manifest_order for item in experiment_items]
        evaluation_order = [item.manifest_order for item in evaluation_items]
        if evaluation_order != experiment_order:
            raise PlatformError("DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                                "Linked evaluation manifest order mismatch.", 409)
        for experiment_item, evaluation_item, expected in zip(
                experiment_items, evaluation_items, membership):
            if (evaluation_item.manifest_order != experiment_item.manifest_order
                    or evaluation_item.recording_id != expected["recording_id"]
                    or evaluation_item.analysis_run_id != expected["analysis_run_id"]):
                raise PlatformError("DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                                    "Linked evaluation membership mismatch.", 409)
            if evaluation_item.status != "included":
                raise PlatformError("DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                                    "Linked evaluation item is not fully included.", 409)
        if (evaluation.expected_recordings != len(experiment_items)
                or evaluation.missing_recordings != 0
                or evaluation.coverage != 1.0):
            raise PlatformError("DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                                "Linked evaluation is not complete.", 409)
        return experiment, evaluation

    def reset_interrupted_evaluation(self, experiment_id, evaluation_id,
                                     coordinator_token):
        """Generation-fenced metrics-only reset of an interrupted linked evaluation."""
        try:
            with self.session.no_autoflush:
                fence = self.session.execute(
                    update(DatasetExperimentModel)
                    .where(
                        DatasetExperimentModel.id == experiment_id,
                        DatasetExperimentModel.status == "evaluating",
                        DatasetExperimentModel.coordinator_token == coordinator_token,
                        DatasetExperimentModel.dataset_evaluation_id == evaluation_id,
                    )
                    .values(heartbeat_at=datetime.now(timezone.utc))
                    .execution_options(synchronize_session=False)
                )
                if int(fence.rowcount or 0) != 1:
                    self.session.rollback()
                    raise PlatformError(
                        "DATASET_EXPERIMENT_FENCE_LOST",
                        "Coordinator generation no longer owns the experiment.", 409)
                DatasetBenchmarkService(self.session).prepare_retry_evaluation(evaluation_id)
            self.session.commit()
        except PlatformError:
            self.session.rollback()
            raise
        except Exception:
            self.session.rollback()
            raise
        return True

    def start_linked_evaluation(self, experiment_id, evaluation_id,
                                coordinator_token, benchmark_job_manager):
        """Generation-fenced + uniquely-claimed automatic evaluation start."""
        try:
            with self.session.no_autoflush:
                claim = self.session.execute(
                    update(DatasetEvaluationModel)
                    .where(
                        DatasetEvaluationModel.id == evaluation_id,
                        DatasetEvaluationModel.status == "pending",
                        DatasetEvaluationModel.worker_pid.is_(None),
                        exists().where(
                            DatasetExperimentModel.id == experiment_id,
                            DatasetExperimentModel.status == "evaluating",
                            DatasetExperimentModel.coordinator_token == coordinator_token,
                            DatasetExperimentModel.dataset_evaluation_id == evaluation_id,
                        ),
                    )
                    .values(status="running", started_at=datetime.now(timezone.utc),
                            error_type=None, error_message=None)
                    .execution_options(synchronize_session=False)
                )
            if int(claim.rowcount or 0) != 1:
                self.session.rollback()
                result = self._diagnose_start_claim_miss(
                    experiment_id, evaluation_id, coordinator_token)
                self.session.rollback()
                return result
            self.session.commit()
        except PlatformError:
            self.session.rollback()
            raise
        except Exception:
            self.session.rollback()
            raise

        self.session.rollback()

        try:
            worker_pid = benchmark_job_manager.start(evaluation_id)
        except Exception as exc:
            try:
                with self.session.no_autoflush:
                    failed = self.session.execute(
                        update(DatasetEvaluationModel)
                        .where(
                            DatasetEvaluationModel.id == evaluation_id,
                            DatasetEvaluationModel.status == "running",
                            DatasetEvaluationModel.worker_pid.is_(None),
                            exists().where(
                                DatasetExperimentModel.id == experiment_id,
                                DatasetExperimentModel.status == "evaluating",
                                DatasetExperimentModel.coordinator_token == coordinator_token,
                                DatasetExperimentModel.dataset_evaluation_id == evaluation_id,
                            ),
                        )
                        .values(status="failed", error_type="BENCHMARK_FAILED",
                                error_message=str(exc)[:1000],
                                completed_at=datetime.now(timezone.utc))
                        .execution_options(synchronize_session=False)
                    )
                if int(failed.rowcount or 0) != 1:
                    self.session.rollback()
                    raise PlatformError("DATASET_EXPERIMENT_FENCE_LOST",
                                        "Evaluation failure write lost its generation.", 409)
                self.session.commit()
            except PlatformError:
                self.session.rollback()
                raise
            except Exception:
                self.session.rollback()
                raise
            raise PlatformError("DATASET_EXPERIMENT_EVALUATION_FAILED",
                                "Unable to start formal DatasetEvaluation.") from exc

        try:
            with self.session.no_autoflush:
                persisted = self.session.execute(
                    update(DatasetEvaluationModel)
                    .where(
                        DatasetEvaluationModel.id == evaluation_id,
                        exists().where(
                            DatasetExperimentModel.id == experiment_id,
                            DatasetExperimentModel.status == "evaluating",
                            DatasetExperimentModel.coordinator_token == coordinator_token,
                            DatasetExperimentModel.dataset_evaluation_id == evaluation_id,
                        ),
                    )
                    .values(worker_pid=worker_pid)
                    .execution_options(synchronize_session=False)
                )
            if int(persisted.rowcount or 0) != 1:
                self.session.rollback()
                raise PlatformError("DATASET_EXPERIMENT_FENCE_LOST",
                                    "Evaluation PID persist lost its generation.", 409)
            self.session.commit()
        except PlatformError:
            self.session.rollback()
            raise
        except Exception:
            self.session.rollback()
            return "uncertain"
        return "started"

    def _diagnose_start_claim_miss(self, experiment_id, evaluation_id,
                                   coordinator_token):
        generation = self.session.execute(
            select(DatasetExperimentModel.status,
                   DatasetExperimentModel.coordinator_token,
                   DatasetExperimentModel.dataset_evaluation_id)
            .where(DatasetExperimentModel.id == experiment_id)
        ).one_or_none()
        if (generation is None or generation[0] != "evaluating"
                or generation[1] != coordinator_token
                or generation[2] != evaluation_id):
            raise PlatformError("DATASET_EXPERIMENT_FENCE_LOST",
                                "Coordinator generation no longer owns the experiment.", 409)
        evaluation = self.session.get(DatasetEvaluationModel, evaluation_id)
        if evaluation is None:
            raise PlatformError("DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                                "Linked DatasetEvaluation is missing.", 409)
        if (evaluation.status in {"running", "completed", "failed"}
                or (evaluation.status == "pending" and evaluation.worker_pid is not None)):
            return "already_started"
        raise PlatformError("DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                            "Evaluation start claim missed in an impossible state.", 409)

    def list_experiments(self, dataset_id: str | None = None):
        statement = select(DatasetExperimentModel)
        if dataset_id is not None:
            statement = statement.where(DatasetExperimentModel.dataset_id == dataset_id)
        experiments = list(self.session.scalars(
            statement.order_by(DatasetExperimentModel.created_at, DatasetExperimentModel.id)
        ).all())
        return [self._to_read(experiment) for experiment in experiments]

    def retry_evaluation(self, experiment_id, job_manager):
        experiment = self._get(experiment_id)
        if experiment.status != "failed":
            raise PlatformError("DATASET_EXPERIMENT_INVALID_TRANSITION",
                                "Only a failed experiment can retry evaluation.", 409)
        if experiment.dataset_evaluation_id is None:
            raise PlatformError("DATASET_EXPERIMENT_INVALID_TRANSITION",
                                "Experiment has no linked evaluation to retry.", 409)
        items = list(self.session.scalars(
            select(DatasetExperimentItemModel)
            .where(DatasetExperimentItemModel.experiment_id == experiment_id)
        ).all())
        if not items:
            raise PlatformError("DATASET_EXPERIMENT_INVARIANT_VIOLATION",
                                "Experiment has no items.", 409)
        counts = {"queued": 0, "running": 0, "completed": 0, "failed": 0}
        for item in items:
            counts[item.status] = counts.get(item.status, 0) + 1
        if (counts["completed"] != len(items) or counts["failed"] or
                counts["queued"] or counts["running"]):
            raise PlatformError("DATASET_EXPERIMENT_INVALID_TRANSITION",
                                "Retry Evaluation requires complete inference.", 409)

        experiment, evaluation = self.validate_evaluation_linkage(experiment_id)
        if evaluation.status not in {"failed", "interrupted"}:
            raise PlatformError("DATASET_EXPERIMENT_INVALID_TRANSITION",
                                "Linked evaluation is not retryable.", 409)

        experiment_snapshot = {
            "status": experiment.status,
            "completed_at": experiment.completed_at,
            "heartbeat_at": experiment.heartbeat_at,
            "coordinator_token": experiment.coordinator_token,
            "worker_pid": experiment.worker_pid,
            "error_type": experiment.error_type,
            "error_message": experiment.error_message,
            "dataset_evaluation_id": experiment.dataset_evaluation_id,
        }
        benchmarks = DatasetBenchmarkService(self.session)
        evaluation_snapshot = benchmarks.snapshot_retry_evaluation(evaluation.id)
        token = f"coord_{uuid4().hex}"
        now = datetime.now(timezone.utc)
        try:
            benchmarks.prepare_retry_evaluation(evaluation.id)
            claimed = self.session.execute(
                update(DatasetExperimentModel)
                .where(
                    DatasetExperimentModel.id == experiment_id,
                    DatasetExperimentModel.status == "failed",
                    DatasetExperimentModel.dataset_evaluation_id == evaluation.id,
                )
                .values(status="evaluating", coordinator_token=token, worker_pid=None,
                        heartbeat_at=now, completed_at=None,
                        error_type=None, error_message=None)
                .execution_options(synchronize_session=False)
            )
            if int(claimed.rowcount or 0) != 1:
                self.session.rollback()
                raise PlatformError("DATASET_EXPERIMENT_INVALID_TRANSITION",
                                    "Retry Evaluation lost its ownership CAS.", 409)
            self.session.commit()
        except PlatformError:
            self.session.rollback()
            raise
        except Exception:
            self.session.rollback()
            raise

        try:
            worker_pid = job_manager.start(experiment_id, token)
        except Exception as exc:
            restored = self._restore_retry_evaluation(
                experiment_id, token, evaluation.id,
                experiment_snapshot, evaluation_snapshot)
            if not restored:
                raise PlatformError("DATASET_EXPERIMENT_FENCE_LOST",
                                    "Retry Evaluation lost its generation; no evaluation "
                                    "was mutated.", 409) from exc
            raise PlatformError("DATASET_EXPERIMENT_ORCHESTRATION_FAILED",
                                "Unable to start DatasetExperiment coordinator for "
                                "evaluation retry.") from exc

        try:
            with self.session.no_autoflush:
                persisted = self.session.execute(
                    update(DatasetExperimentModel)
                    .where(
                        DatasetExperimentModel.id == experiment_id,
                        DatasetExperimentModel.coordinator_token == token,
                        DatasetExperimentModel.status == "evaluating",
                    )
                    .values(worker_pid=worker_pid)
                    .execution_options(synchronize_session=False)
                )
            if int(persisted.rowcount or 0) != 1:
                self.session.rollback()
                raise PlatformError("DATASET_EXPERIMENT_FENCE_LOST",
                                    "A newer generation owns the experiment.", 409)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        self.session.expire_all()
        return self.session.get(DatasetExperimentModel, experiment_id)

    def _restore_retry_evaluation(self, experiment_id, retry_token, evaluation_id,
                                  experiment_snapshot, evaluation_snapshot):
        try:
            with self.session.no_autoflush:
                claimed = self.session.execute(
                    update(DatasetExperimentModel)
                    .where(
                        DatasetExperimentModel.id == experiment_id,
                        DatasetExperimentModel.status == "evaluating",
                        DatasetExperimentModel.coordinator_token == retry_token,
                        DatasetExperimentModel.dataset_evaluation_id == evaluation_id,
                    )
                    .values(
                        status=experiment_snapshot["status"],
                        completed_at=experiment_snapshot["completed_at"],
                        heartbeat_at=experiment_snapshot["heartbeat_at"],
                        coordinator_token=experiment_snapshot["coordinator_token"],
                        worker_pid=experiment_snapshot["worker_pid"],
                        error_type=experiment_snapshot["error_type"],
                        error_message=experiment_snapshot["error_message"],
                    )
                    .execution_options(synchronize_session=False)
                )
                if int(claimed.rowcount or 0) != 1:
                    self.session.rollback()
                    return False
                DatasetBenchmarkService(self.session).restore_retry_evaluation(
                    evaluation_id, evaluation_snapshot)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return True
