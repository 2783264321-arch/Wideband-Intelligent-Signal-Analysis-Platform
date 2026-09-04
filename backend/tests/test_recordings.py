from sqlalchemy import select

from app.analysis.model import AnalysisRunModel
from app.detections.model import DetectionResultModel
from app.ground_truth.model import GroundTruthModel
from app.recordings.model import RecordingModel


def test_core_entities_persist_and_reload(client):
    database = client.app.state.database
    with database.session_factory() as session:
        recording = RecordingModel(
            id="rec_test",
            name="test",
            data_path="recordings/rec_test/raw.iq",
            data_format="complex64_le",
            sample_rate_hz=1_000_000.0,
            center_frequency_hz=2_441_000_000.0,
            frequency_low_hz=2_440_500_000.0,
            frequency_high_hz=2_441_500_000.0,
            num_samples=4096,
            duration_s=0.004096,
            dataset_name=None,
            dataset_split=None,
            label_space="spacenet_14",
            has_ground_truth=True,
        )
        run = AnalysisRunModel(
            id="run_test",
            recording_id=recording.id,
            pipeline_id="dummy",
            pipeline_version="1.0",
            executor="local_cpu",
            status="completed",
            parameters_json={},
        )
        gt = GroundTruthModel(
            id="gt_test",
            recording_id=recording.id,
            t_start_s=0.0,
            t_end_s=0.004096,
            f_low_hz=2_440_900_000.0,
            f_high_hz=2_441_100_000.0,
            class_id=9,
            class_name="LoRa 250kHz",
        )
        detection = DetectionResultModel(
            id="det_test",
            run_id=run.id,
            t_start_s=0.0005,
            t_end_s=0.0035,
            f_low_hz=2_440_900_000.0,
            f_high_hz=2_441_100_000.0,
            class_id=9,
            class_name="LoRa 250kHz",
            confidence=0.92,
            scores_json={"classification": 0.92},
        )
        session.add_all([recording, run, gt, detection])
        session.commit()

    with database.session_factory() as fresh:
        stored = fresh.scalar(select(RecordingModel).where(RecordingModel.id == "rec_test"))
        assert stored is not None
        assert stored.analysis_runs[0].status == "completed"
        assert stored.ground_truth[0].class_name == "LoRa 250kHz"
        assert stored.analysis_runs[0].detections[0].confidence == 0.92
