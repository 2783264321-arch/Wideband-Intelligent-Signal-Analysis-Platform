from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.analysis.model import AnalysisRunModel
    from app.datasets.model import DatasetModel
    from app.ground_truth.model import GroundTruthModel


class RecordingModel(Base):
    __tablename__ = "recordings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    data_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    data_format: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(32), default="custom", nullable=False)
    external_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    sample_rate_hz: Mapped[float] = mapped_column(Float, nullable=False)
    center_frequency_hz: Mapped[float] = mapped_column(Float, nullable=False)
    frequency_low_hz: Mapped[float] = mapped_column(Float, nullable=False)
    frequency_high_hz: Mapped[float] = mapped_column(Float, nullable=False)
    num_samples: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_s: Mapped[float] = mapped_column(Float, nullable=False)
    dataset_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dataset_split: Mapped[str | None] = mapped_column(String(64), nullable=True)
    label_space: Mapped[str | None] = mapped_column(String(128), nullable=True)
    has_ground_truth: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source_data_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # P1 dataset authority: a recording is a dataset member when dataset_id is set,
    # otherwise it is a standalone sample. sample_key is the stable manifest key.
    dataset_id: Mapped[str | None] = mapped_column(ForeignKey("datasets.id"), nullable=True, index=True)
    sample_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    analysis_runs: Mapped[list["AnalysisRunModel"]] = relationship(back_populates="recording", cascade="all, delete-orphan")
    ground_truth: Mapped[list["GroundTruthModel"]] = relationship(back_populates="recording", cascade="all, delete-orphan")
    dataset: Mapped["DatasetModel | None"] = relationship(back_populates="samples")
