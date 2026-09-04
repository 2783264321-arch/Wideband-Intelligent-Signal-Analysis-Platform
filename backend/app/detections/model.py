from typing import Any, TYPE_CHECKING

from sqlalchemy import Float, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.analysis.model import AnalysisRunModel


class DetectionResultModel(Base):
    __tablename__ = "detection_results"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True)
    t_start_s: Mapped[float] = mapped_column(Float, nullable=False)
    t_end_s: Mapped[float] = mapped_column(Float, nullable=False)
    f_low_hz: Mapped[float] = mapped_column(Float, nullable=False)
    f_high_hz: Mapped[float] = mapped_column(Float, nullable=False)
    class_id: Mapped[int] = mapped_column(Integer, nullable=False)
    class_name: Mapped[str] = mapped_column(String(255), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    scores_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    run: Mapped["AnalysisRunModel"] = relationship(back_populates="detections")
