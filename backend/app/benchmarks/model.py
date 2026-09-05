from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class DatasetEvaluationModel(Base):
    __tablename__ = "dataset_evaluations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    dataset_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    dataset_split: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    label_space: Mapped[str] = mapped_column(String(128), nullable=False)
    pipeline_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    pipeline_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)

    expected_recordings: Mapped[int] = mapped_column(Integer, nullable=False)
    evaluated_recordings: Mapped[int] = mapped_column(Integer, nullable=False)
    missing_recordings: Mapped[int] = mapped_column(Integer, nullable=False)
    coverage: Mapped[float] = mapped_column(Float, nullable=False)
    comparable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    recording_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    evaluation_protocol: Mapped[str] = mapped_column(String(128), nullable=False)
    protocol_config_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    aggregate_metrics_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    per_class_metrics_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    confusion_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)

    progress_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    progress_current: Mapped[int | None] = mapped_column(Integer, nullable=True)
    progress_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    worker_pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    items: Mapped[list["DatasetEvaluationItemModel"]] = relationship(
        back_populates="evaluation", cascade="all, delete-orphan",
        order_by="DatasetEvaluationItemModel.manifest_order",
    )


class DatasetEvaluationItemModel(Base):
    __tablename__ = "dataset_evaluation_items"
    __table_args__ = (
        UniqueConstraint("evaluation_id", "recording_id", name="uq_dataset_eval_recording"),
        UniqueConstraint("evaluation_id", "manifest_order", name="uq_dataset_eval_manifest_order"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    evaluation_id: Mapped[str] = mapped_column(
        ForeignKey("dataset_evaluations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    manifest_order: Mapped[int] = mapped_column(Integer, nullable=False)
    recording_id: Mapped[str] = mapped_column(ForeignKey("recordings.id"), index=True, nullable=False)
    analysis_run_id: Mapped[str | None] = mapped_column(ForeignKey("analysis_runs.id"), index=True, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    gt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prediction_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    evaluation: Mapped[DatasetEvaluationModel] = relationship(back_populates="items")