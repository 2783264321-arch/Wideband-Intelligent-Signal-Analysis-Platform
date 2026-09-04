from typing import TYPE_CHECKING

from sqlalchemy import Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.recordings.model import RecordingModel


class GroundTruthModel(Base):
    __tablename__ = "ground_truth"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    recording_id: Mapped[str] = mapped_column(ForeignKey("recordings.id", ondelete="CASCADE"), index=True)
    t_start_s: Mapped[float] = mapped_column(Float, nullable=False)
    t_end_s: Mapped[float] = mapped_column(Float, nullable=False)
    f_low_hz: Mapped[float] = mapped_column(Float, nullable=False)
    f_high_hz: Mapped[float] = mapped_column(Float, nullable=False)
    class_id: Mapped[int] = mapped_column(Integer, nullable=False)
    class_name: Mapped[str] = mapped_column(String(255), nullable=False)

    recording: Mapped["RecordingModel"] = relationship(back_populates="ground_truth")
