from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DatasetExperimentModel(Base):
    __tablename__ = "dataset_experiments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Frozen dataset membership
    dataset_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    dataset_split: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    dataset_label_space: Mapped[str] = mapped_column(String(128), nullable=False)
    recording_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # Frozen scientific identity
    plugin_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    plugin_version: Mapped[str] = mapped_column(String(64), nullable=False)
    model_release_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    asset_manifest_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parameters_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    # Frozen execution identity
    executor: Mapped[str] = mapped_column(String(64), nullable=False)
    runtime_descriptor_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    evaluation_protocol: Mapped[str] = mapped_column(String(128), nullable=False)
    max_concurrency: Mapped[int] = mapped_column(Integer, nullable=False)

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)

    dataset_evaluation_id: Mapped[str | None] = mapped_column(
        ForeignKey("dataset_evaluations.id"), nullable=True, index=True
    )

    # Coordinator ownership (unused in G1; owned by G3+)
    coordinator_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    worker_pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    error_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    items: Mapped[list["DatasetExperimentItemModel"]] = relationship(
        back_populates="experiment",
        cascade="all, delete-orphan",
        order_by="DatasetExperimentItemModel.manifest_order",
    )


class DatasetExperimentItemModel(Base):
    __tablename__ = "dataset_experiment_items"
    __table_args__ = (
        UniqueConstraint("experiment_id", "recording_id", name="uq_dataset_experiment_recording"),
        UniqueConstraint("experiment_id", "manifest_order", name="uq_dataset_experiment_manifest_order"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    experiment_id: Mapped[str] = mapped_column(
        ForeignKey("dataset_experiments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    manifest_order: Mapped[int] = mapped_column(Integer, nullable=False)
    recording_id: Mapped[str] = mapped_column(
        ForeignKey("recordings.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)

    last_error_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    experiment: Mapped["DatasetExperimentModel"] = relationship(back_populates="items")
    attempts: Mapped[list["DatasetExperimentAttemptModel"]] = relationship(
        back_populates="item",
        cascade="all, delete-orphan",
        order_by="DatasetExperimentAttemptModel.attempt_number",
    )


class DatasetExperimentAttemptModel(Base):
    __tablename__ = "dataset_experiment_attempts"
    __table_args__ = (
        UniqueConstraint("experiment_item_id", "attempt_number", name="uq_dataset_experiment_attempt_number"),
        UniqueConstraint("analysis_run_id", name="uq_dataset_experiment_attempt_run"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    experiment_item_id: Mapped[str] = mapped_column(
        ForeignKey("dataset_experiment_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    analysis_run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.id"), nullable=False, index=True
    )
    launch_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    item: Mapped["DatasetExperimentItemModel"] = relationship(back_populates="attempts")
