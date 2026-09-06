"""Deterministic test-only ORM fixtures for dataset benchmark tests.

Coordinates use a tiny valid 1 MHz Recording around 2.441 GHz so physical
bounds remain consistent across tests.
"""

from app.analysis.model import AnalysisRunModel
from app.detections.model import DetectionResultModel
from app.ground_truth.model import GroundTruthModel
from app.recordings.model import RecordingModel

SAMPLE_RATE_HZ = 1_000_000.0
CENTER_FREQUENCY_HZ = 2_441_000_000.0
FREQUENCY_LOW_HZ = 2_440_500_000.0
FREQUENCY_HIGH_HZ = 2_441_500_000.0


def add_recording(session, *, recording_id, name, dataset_name="SpaceNet", dataset_split="test",
                  label_space="spacenet_14", path_suffix=None, has_ground_truth=True):
    session.add(RecordingModel(
        id=recording_id,
        name=name,
        data_path=f"recordings/{recording_id}/raw.iq",
        data_format="complex64_le",
        source="custom",
        sample_rate_hz=SAMPLE_RATE_HZ,
        center_frequency_hz=CENTER_FREQUENCY_HZ,
        frequency_low_hz=FREQUENCY_LOW_HZ,
        frequency_high_hz=FREQUENCY_HIGH_HZ,
        num_samples=100000,
        duration_s=0.1,
        dataset_name=dataset_name,
        dataset_split=dataset_split,
        label_space=label_space,
        has_ground_truth=has_ground_truth,
    ))
    return recording_id


def add_ground_truth(session, *, gt_id, recording_id, class_id, class_name, t0, t1, f0, f1):
    session.add(GroundTruthModel(
        id=gt_id, recording_id=recording_id, t_start_s=t0, t_end_s=t1, f_low_hz=f0, f_high_hz=f1,
        class_id=class_id, class_name=class_name,
    ))
    return gt_id


def add_run(session, *, run_id, recording_id, pipeline_id="pipeline_x", pipeline_version="1.0",
            executor="imported", status="completed", created_at=None, parameters_json=None):
    session.add(AnalysisRunModel(
        id=run_id, recording_id=recording_id, pipeline_id=pipeline_id, pipeline_version=pipeline_version,
        executor=executor, status=status, parameters_json=parameters_json or {}, created_at=created_at,
    ))
    return run_id


def add_detection(session, *, detection_id, run_id, class_id, class_name, confidence, t0, t1, f0, f1):
    session.add(DetectionResultModel(
        id=detection_id, run_id=run_id, t_start_s=t0, t_end_s=t1, f_low_hz=f0, f_high_hz=f1,
        class_id=class_id, class_name=class_name, confidence=confidence,
    ))
    return detection_id