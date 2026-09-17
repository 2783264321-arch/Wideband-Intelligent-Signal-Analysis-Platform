from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.recordings.model import RecordingModel


class DatasetModel(Base):
    """First-class persisted dataset authority (P1).

    ``local_root`` is where THIS machine reads the dataset; ``portable_fingerprint``
    is a path-independent logical identity derived from the canonical sample
    manifest, suitable for comparing datasets registered on different machines.
    The absolute path never participates in the fingerprint.
    """

    __tablename__ = "datasets"
    __table_args__ = (
        UniqueConstraint(
            "adapter_id", "split", "local_root", "name", name="uq_datasets_local_identity"
        ),
        Index("ix_datasets_portable_fingerprint", "portable_fingerprint"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    split: Mapped[str] = mapped_column(String(64), nullable=False)
    adapter_id: Mapped[str] = mapped_column(String(64), nullable=False)
    label_space: Mapped[str | None] = mapped_column(String(128), nullable=True)
    local_root: Mapped[str] = mapped_column(String(1024), nullable=False)
    portable_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ground_truth_sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    samples: Mapped[list["RecordingModel"]] = relationship(back_populates="dataset")
