from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analysis.model import AnalysisRunModel
from app.benchmarks.model import DatasetEvaluationItemModel, DatasetEvaluationModel
from app.core.errors import PlatformError
from app.data_library.schema import (
    DatasetAnalysisHistoryItemRead,
    DatasetProjectionSummaryRead,
    DatasetSampleRead,
    StandaloneSampleRead,
)
from app.dataset_experiments.model import DatasetExperimentItemModel, DatasetExperimentModel
from app.datasets.projection import DatasetProjection, DatasetProjectionResolver
from app.recordings.model import RecordingModel


class DataLibraryService:
    """Server-side read model over the deterministic dataset projection."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self._resolver = DatasetProjectionResolver(session)
        self._grouped_cache: dict[str, list[RecordingModel]] | None = None

    # ---------- shared single-pass grouping ----------

    def _grouped(self) -> dict[str, list[RecordingModel]]:
        if self._grouped_cache is None:
            self._grouped_cache = self._resolver.grouped()
        return self._grouped_cache

    def _projection_members(self, dataset_projection_id: str) -> tuple[DatasetProjection, list[RecordingModel]]:
        members = self._grouped().get(dataset_projection_id)
        if members is None:
            raise PlatformError(
                "DATASET_PROJECTION_NOT_FOUND", "Dataset projection was not found.", 404
            )
        projection = self._resolver.find_for_recording(members[0])
        assert projection is not None  # dataset members always have dataset metadata
        return projection, members

    def _analysis_counts(self, recording_ids: list[str]) -> dict[str, int]:
        if not recording_ids:
            return {}
        rows = self.session.execute(
            select(AnalysisRunModel.recording_id, func.count(AnalysisRunModel.id))
            .where(AnalysisRunModel.recording_id.in_(recording_ids))
            .group_by(AnalysisRunModel.recording_id)
        ).all()
        return {recording_id: int(count) for recording_id, count in rows}

    @staticmethod
    def _summary(projection: DatasetProjection, members: list[RecordingModel]) -> DatasetProjectionSummaryRead:
        external = projection.source != "custom"
        return DatasetProjectionSummaryRead(
            dataset_projection_id=projection.dataset_projection_id,
            source=projection.source,
            dataset_name=projection.dataset_name,
            dataset_split=projection.dataset_split,
            label_space=projection.label_space,
            sample_count=len(members),
            ground_truth_sample_count=sum(1 for member in members if member.has_ground_truth),
            external=external,
            source_location=projection.source_location if external else None,
        )

    # ---------- dataset projection listing/detail ----------

    def list_dataset_projections(
        self, limit: int, offset: int
    ) -> tuple[list[DatasetProjectionSummaryRead], int]:
        rows: list[tuple[DatasetProjection, list[RecordingModel]]] = []
        for members in self._grouped().values():
            projection = self._resolver.find_for_recording(members[0])
            if projection is not None:
                rows.append((projection, members))
        rows.sort(
            key=lambda item: (
                item[0].dataset_name,
                item[0].dataset_split,
                item[0].source,
                item[0].dataset_projection_id,
            )
        )
        page = rows[offset : offset + limit]
        return [self._summary(projection, members) for projection, members in page], len(rows)

    def get_dataset_projection(self, dataset_projection_id: str) -> DatasetProjectionSummaryRead:
        projection, members = self._projection_members(dataset_projection_id)
        return self._summary(projection, members)

    # ---------- sample browsing ----------

    def list_dataset_samples(
        self, dataset_projection_id: str, limit: int, offset: int, search: str | None
    ) -> tuple[list[DatasetSampleRead], int]:
        projection, members = self._projection_members(dataset_projection_id)
        if search:
            needle = search.strip().lower()
            members = [member for member in members if needle in member.name.lower()]
        members = sorted(members, key=lambda member: (member.name, member.id))
        total = len(members)
        page = members[offset : offset + limit]
        counts = self._analysis_counts([member.id for member in page])
        derived = projection.source == "spacenet"
        items = [
            DatasetSampleRead(
                id=member.id,
                name=member.name,
                sample_rate_hz=member.sample_rate_hz,
                center_frequency_hz=member.center_frequency_hz,
                frequency_low_hz=member.frequency_low_hz,
                frequency_high_hz=member.frequency_high_hz,
                duration_s=member.duration_s,
                has_ground_truth=member.has_ground_truth,
                analysis_count=counts.get(member.id, 0),
                sample_rate_derived=derived,
                center_frequency_derived=derived,
            )
            for member in page
        ]
        return items, total

    def list_standalone_samples(
        self, limit: int, offset: int, search: str | None
    ) -> tuple[list[StandaloneSampleRead], int]:
        statement = select(RecordingModel).where(RecordingModel.dataset_name.is_(None))
        if search:
            statement = statement.where(RecordingModel.name.ilike(f"%{search.strip()}%"))
        total = self.session.scalar(select(func.count()).select_from(statement.subquery())) or 0
        rows = list(
            self.session.scalars(
                statement.order_by(RecordingModel.created_at, RecordingModel.id)
                .limit(limit)
                .offset(offset)
            ).all()
        )
        counts = self._analysis_counts([row.id for row in rows])
        items = [
            StandaloneSampleRead(
                id=row.id,
                name=row.name,
                source=row.source,
                sample_rate_hz=row.sample_rate_hz,
                center_frequency_hz=row.center_frequency_hz,
                frequency_low_hz=row.frequency_low_hz,
                frequency_high_hz=row.frequency_high_hz,
                duration_s=row.duration_s,
                data_format=row.data_format,
                has_ground_truth=row.has_ground_truth,
                analysis_count=counts.get(row.id, 0),
            )
            for row in rows
        ]
        return items, total

    # ---------- analysis history (experiment / evaluation / imported_batch) ----------

    def list_dataset_analysis_history(
        self, dataset_projection_id: str
    ) -> list[DatasetAnalysisHistoryItemRead]:
        projection, members = self._projection_members(dataset_projection_id)
        member_ids = {member.id for member in members}
        expected = sum(1 for member in members if member.has_ground_truth)
        label_space = projection.label_space or ""
        items: list[DatasetAnalysisHistoryItemRead] = []

        experiments = self.session.scalars(
            select(DatasetExperimentModel).where(
                DatasetExperimentModel.dataset_name == projection.dataset_name,
                DatasetExperimentModel.dataset_split == projection.dataset_split,
                DatasetExperimentModel.dataset_label_space == label_space,
            )
        ).all()
        for experiment in experiments:
            item_rows = list(
                self.session.scalars(
                    select(DatasetExperimentItemModel).where(
                        DatasetExperimentItemModel.experiment_id == experiment.id
                    )
                ).all()
            )
            ids = {row.recording_id for row in item_rows}
            if not ids or not ids <= member_ids:
                continue
            completed = sum(1 for row in item_rows if row.status == "completed")
            failed = sum(1 for row in item_rows if row.status == "failed")
            items.append(
                DatasetAnalysisHistoryItemRead(
                    kind="experiment",
                    resource_id=experiment.id,
                    name=experiment.name,
                    pipeline_id=experiment.plugin_id,
                    pipeline_version=experiment.plugin_version,
                    status=experiment.status,
                    executor=experiment.executor,
                    expected_items=len(item_rows),
                    completed_items=completed,
                    failed_items=failed,
                    coverage=(completed / len(item_rows)) if item_rows else None,
                    created_at=experiment.created_at,
                    dataset_evaluation_id=experiment.dataset_evaluation_id,
                )
            )

        evaluations = self.session.scalars(
            select(DatasetEvaluationModel).where(
                DatasetEvaluationModel.dataset_name == projection.dataset_name,
                DatasetEvaluationModel.dataset_split == projection.dataset_split,
                DatasetEvaluationModel.label_space == label_space,
            )
        ).all()
        for evaluation in evaluations:
            item_rows = list(
                self.session.scalars(
                    select(DatasetEvaluationItemModel).where(
                        DatasetEvaluationItemModel.evaluation_id == evaluation.id
                    )
                ).all()
            )
            ids = {row.recording_id for row in item_rows}
            if not ids or not ids <= member_ids:
                continue
            items.append(
                DatasetAnalysisHistoryItemRead(
                    kind="evaluation",
                    resource_id=evaluation.id,
                    name=evaluation.name,
                    pipeline_id=evaluation.pipeline_id,
                    pipeline_version=evaluation.pipeline_version,
                    status=evaluation.status,
                    executor=None,
                    expected_items=evaluation.expected_recordings,
                    completed_items=evaluation.evaluated_recordings,
                    failed_items=evaluation.missing_recordings,
                    coverage=evaluation.coverage,
                    created_at=evaluation.created_at,
                    dataset_evaluation_id=evaluation.id,
                )
            )

        items.extend(self._imported_batch_history(member_ids, expected))
        items.sort(key=lambda item: item.created_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return items

    def _imported_batch_history(
        self, member_ids: set[str], expected: int
    ) -> list[DatasetAnalysisHistoryItemRead]:
        batch_runs = list(
            self.session.scalars(
                select(AnalysisRunModel).where(
                    AnalysisRunModel.executor == "imported",
                    AnalysisRunModel.status == "completed",
                )
            ).all()
        )
        fingerprint_runs: dict[str, list[AnalysisRunModel]] = {}
        for run in batch_runs:
            payload = (run.parameters_json or {}).get("batch_import")
            if not isinstance(payload, dict):
                continue
            fingerprint = payload.get("import_fingerprint")
            if not isinstance(fingerprint, str) or not fingerprint:
                continue
            fingerprint_runs.setdefault(fingerprint, []).append(run)

        results: list[DatasetAnalysisHistoryItemRead] = []
        for fingerprint, runs in fingerprint_runs.items():
            # A batch belongs to exactly one projection; never absorb siblings.
            if any(run.recording_id not in member_ids for run in runs):
                continue
            pipeline_ids = {run.pipeline_id for run in runs}
            pipeline_versions = {run.pipeline_version for run in runs}
            if len(pipeline_ids) != 1 or len(pipeline_versions) != 1:
                continue
            payload = (runs[0].parameters_json or {}).get("batch_import", {})
            completed = len(runs)
            results.append(
                DatasetAnalysisHistoryItemRead(
                    kind="imported_batch",
                    resource_id=fingerprint,
                    name=f"{runs[0].pipeline_id} {runs[0].pipeline_version}",
                    pipeline_id=runs[0].pipeline_id,
                    pipeline_version=runs[0].pipeline_version,
                    status="completed",
                    executor="imported",
                    expected_items=expected,
                    completed_items=completed,
                    failed_items=0,
                    coverage=(completed / expected) if expected else 0.0,
                    created_at=max((run.created_at for run in runs), default=None),
                    dataset_evaluation_id=None,
                    batch_id=payload.get("batch_id"),
                    archive_sha256=payload.get("archive_sha256"),
                )
            )
        return results
