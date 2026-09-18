"""First-class dataset read API (P1). Deliberately independent of the legacy
DatasetProjection read model."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analysis.model import AnalysisRunModel
from app.core.errors import PlatformError
from app.datasets.model import DatasetModel
from app.datasets.ordering import numeric_aware_order
from app.datasets.schema import DatasetRead, DatasetSampleRead
from app.recordings.model import RecordingModel


def _to_dataset_read(dataset: DatasetModel) -> DatasetRead:
    return DatasetRead(
        id=dataset.id,
        name=dataset.name,
        split=dataset.split,
        adapter_id=dataset.adapter_id,
        label_space=dataset.label_space,
        local_root=dataset.local_root,
        portable_fingerprint=dataset.portable_fingerprint,
        sample_count=dataset.sample_count,
        ground_truth_sample_count=dataset.ground_truth_sample_count,
        created_at=dataset.created_at,
    )


class DatasetReadService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_datasets(self, limit: int, offset: int) -> tuple[list[DatasetRead], int]:
        total = int(self.session.scalar(select(func.count()).select_from(DatasetModel)) or 0)
        rows = list(
            self.session.scalars(
                select(DatasetModel)
                .order_by(DatasetModel.name, DatasetModel.split, DatasetModel.id)
                .limit(limit)
                .offset(offset)
            ).all()
        )
        return [_to_dataset_read(row) for row in rows], total

    def get_dataset(self, dataset_id: str) -> DatasetRead:
        dataset = self.session.get(DatasetModel, dataset_id)
        if dataset is None:
            raise PlatformError("DATASET_NOT_FOUND", "Dataset was not found.", 404)
        return _to_dataset_read(dataset)

    def list_samples(
        self, dataset_id: str, limit: int, offset: int, search: str | None
    ) -> tuple[list[DatasetSampleRead], int]:
        dataset = self.session.get(DatasetModel, dataset_id)
        if dataset is None:
            raise PlatformError("DATASET_NOT_FOUND", "Dataset was not found.", 404)
        statement = select(RecordingModel).where(RecordingModel.dataset_id == dataset_id)
        if search:
            statement = statement.where(RecordingModel.name.ilike(f"%{search.strip()}%"))
        total = int(
            self.session.scalar(select(func.count()).select_from(statement.subquery())) or 0
        )
        rows = list(
            self.session.scalars(
                statement.order_by(
                    *numeric_aware_order(RecordingModel.name), RecordingModel.id
                )
                .limit(limit)
                .offset(offset)
            ).all()
        )
        counts = self._analysis_counts([row.id for row in rows])
        items = [
            DatasetSampleRead(
                id=row.id,
                name=row.name,
                sample_key=row.sample_key,
                data_format=row.data_format,
                sample_rate_hz=row.sample_rate_hz,
                center_frequency_hz=row.center_frequency_hz,
                frequency_low_hz=row.frequency_low_hz,
                frequency_high_hz=row.frequency_high_hz,
                num_samples=row.num_samples,
                duration_s=row.duration_s,
                has_ground_truth=row.has_ground_truth,
                analysis_count=counts.get(row.id, 0),
            )
            for row in rows
        ]
        return items, total

    def list_analysis_history(self, dataset_id: str):
        """Compatibility bridge: derive the legacy projection from dataset members
        and reuse the existing history logic. Projection identity is never returned."""
        dataset = self.session.get(DatasetModel, dataset_id)
        if dataset is None:
            raise PlatformError("DATASET_NOT_FOUND", "Dataset was not found.", 404)
        members = list(
            self.session.scalars(
                select(RecordingModel).where(RecordingModel.dataset_id == dataset_id)
            ).all()
        )
        if not members:
            return []
        from app.data_library.service import DataLibraryService
        from app.datasets.projection import DatasetProjectionResolver

        projection = DatasetProjectionResolver(self.session).find_for_recording(members[0])
        if projection is None:
            return []
        return DataLibraryService(self.session).list_dataset_analysis_history(
            projection.dataset_projection_id
        )

    def _analysis_counts(self, recording_ids: list[str]) -> dict[str, int]:
        if not recording_ids:
            return {}
        rows = self.session.execute(
            select(AnalysisRunModel.recording_id, func.count(AnalysisRunModel.id))
            .where(AnalysisRunModel.recording_id.in_(recording_ids))
            .group_by(AnalysisRunModel.recording_id)
        ).all()
        return {recording_id: int(count) for recording_id, count in rows}
