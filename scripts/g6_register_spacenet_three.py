"""G6 acceptance tooling: register exactly 3 real SpaceNet Recordings (no IQ copy)."""
import json
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func

from app.benchmarks.service import DatasetBenchmarkService
from app.core.config import Settings
from app.datasets.spacenet import SpaceNetAdapter
from app.db.base import Base, load_domain_models
from app.db.migrations import run_additive_migrations
from app.db.session import Database
from app.ground_truth.model import GroundTruthModel
from app.recordings.model import RecordingModel

STEMS = ("0", "1", "2")
REPO = Path(__file__).resolve().parents[1]
DATA_ROOT = Path("/root/autodl-tmp/SpaceNet_Dataset/advanced")

settings = Settings()
database = Database(settings.database_url)
load_domain_models()
Base.metadata.create_all(database.engine)
run_additive_migrations(database.engine)

adapter = SpaceNetAdapter(DATA_ROOT, REPO / "label_spaces", "spacenet_14")

# Fail closed BEFORE any DB write.
for stem in STEMS:
    sample = adapter.load("test", stem)  # raises on missing/invalid pair
    if not sample.signals:
        raise SystemExit(f"STOP DATA-1: {stem} has zero GT signals")
    if not sample.data_path.is_file():
        raise SystemExit(f"STOP DATA-1: {sample.data_path} is not a file")

with database.session_factory() as session:
    for stem in STEMS:
        sample = adapter.load("test", stem)
        external_path = str(sample.data_path.resolve())
        recording_id = f"rec_{uuid4().hex}"
        session.add(RecordingModel(
            id=recording_id, name=sample.id, data_path=external_path,
            data_format=sample.data_format, source="spacenet",
            external_path=external_path, sample_rate_hz=sample.sample_rate_hz,
            center_frequency_hz=sample.center_frequency_hz,
            frequency_low_hz=sample.frequency_low_hz,
            frequency_high_hz=sample.frequency_high_hz,
            num_samples=sample.num_samples, duration_s=sample.duration_s,
            dataset_name="SpaceNet", dataset_split="test",
            label_space="spacenet_14", has_ground_truth=True,
        ))
        for signal in sample.signals:
            session.add(GroundTruthModel(
                id=f"gt_{uuid4().hex}", recording_id=recording_id,
                t_start_s=signal.t_start_s, t_end_s=signal.t_end_s,
                f_low_hz=signal.f_low_hz, f_high_hz=signal.f_high_hz,
                class_id=signal.class_id, class_name=signal.class_name,
            ))
    session.commit()

# Re-verify AFTER commit from a fresh Session.
with database.session_factory() as session:
    recordings = list(session.query(RecordingModel).filter_by(
        dataset_name="SpaceNet", dataset_split="test", label_space="spacenet_14").all())
    if len(recordings) != 3:
        raise SystemExit(f"STOP DATA-1: expected 3 SpaceNet recordings, found {len(recordings)}")
    if {r.name for r in recordings} != set(STEMS):
        raise SystemExit(f"STOP DATA-1: names {sorted(r.name for r in recordings)} != {list(STEMS)}")
    seen = []
    for recording in recordings:
        if not recording.external_path or not Path(recording.external_path).is_file():
            raise SystemExit(f"STOP DATA-1: external_path not a regular file for {recording.name}")
        if not recording.has_ground_truth:
            raise SystemExit(f"STOP DATA-1: {recording.name} has_ground_truth is False")
        gt_count = int(session.query(func.count(GroundTruthModel.id))
                       .filter_by(recording_id=recording.id).scalar() or 0)
        if gt_count <= 0:
            raise SystemExit(f"STOP DATA-1: {recording.name} has zero GT rows")
        seen.append({
            "recording_id": recording.id, "name": recording.name,
            "external_path": recording.external_path,
            "sample_rate_hz": recording.sample_rate_hz,
            "center_frequency_hz": recording.center_frequency_hz,
            "num_samples": recording.num_samples, "duration_s": recording.duration_s,
            "gt_count": gt_count,
        })
    preview = DatasetBenchmarkService(session).prepare_manifest(
        "SpaceNet", "test", "spacenet_14")
    if preview.expected_recordings != 3:
        raise SystemExit(
            f"STOP DATA-1: manifest expected_recordings {preview.expected_recordings} != 3")
    ordered = [(entry.manifest_order, entry.recording_name) for entry in preview.entries]
    if ordered != [(0, "0"), (1, "1"), (2, "2")]:
        raise SystemExit(f"STOP DATA-1: manifest order {ordered} not deterministic")
    print(json.dumps({
        "recordings": sorted(seen, key=lambda item: item["name"]),
        "manifest_hash": preview.recording_manifest_hash,
        "manifest_order": ordered,
    }, indent=2, default=str))
