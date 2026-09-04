#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.analysis.model import AnalysisRunModel
from app.core.config import Settings
from app.core.signal_validation import validate_label, validate_physical_box
from app.db.base import Base, load_domain_models
from app.db.session import Database
from app.detections.model import DetectionResultModel
from app.labels.service import LabelSpaceService
from app.recordings.model import RecordingModel


def main() -> int:
    parser = argparse.ArgumentParser(description="Load deterministic demo DetectionResults for an existing Recording.")
    parser.add_argument("--recording-id", required=True)
    parser.add_argument("--fixture", default=str(ROOT / "tests" / "fixtures" / "demo_detections.json"))
    args = parser.parse_args()

    settings = Settings(project_root=ROOT)
    database = Database(settings.database_url)
    load_domain_models()
    Base.metadata.create_all(database.engine)
    label_service = LabelSpaceService(settings.label_space_root)

    fixture = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
    run_id = f"run_{uuid4().hex}"

    with database.session_factory() as session:
        recording = session.get(RecordingModel, args.recording_id)
        if recording is None:
            raise SystemExit(f"Recording not found: {args.recording_id}")
        label_space = recording.label_space or "spacenet_14"
        run = AnalysisRunModel(
            id=run_id,
            recording_id=recording.id,
            pipeline_id="demo_fixture",
            pipeline_version="1.0",
            executor="imported",
            status="completed",
            parameters_json={},
        )
        session.add(run)
        for item in fixture["detections"]:
            validate_physical_box(
                recording,
                t_start_s=float(item["t_start_s"]),
                t_end_s=float(item["t_end_s"]),
                f_low_hz=float(item["f_low_hz"]),
                f_high_hz=float(item["f_high_hz"]),
                error_code="INVALID_DETECTION",
            )
            validate_label(
                label_service,
                label_space_id=label_space,
                class_id=int(item["class_id"]),
                class_name=str(item["class_name"]),
                error_code="INVALID_DETECTION",
            )
            session.add(
                DetectionResultModel(
                    id=f"det_{uuid4().hex}",
                    run_id=run_id,
                    t_start_s=float(item["t_start_s"]),
                    t_end_s=float(item["t_end_s"]),
                    f_low_hz=float(item["f_low_hz"]),
                    f_high_hz=float(item["f_high_hz"]),
                    class_id=int(item["class_id"]),
                    class_name=str(item["class_name"]),
                    confidence=float(item["confidence"]),
                    scores_json=item.get("scores"),
                )
            )
        session.commit()

    print(run_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
