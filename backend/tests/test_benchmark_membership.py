import pytest

from benchmark_fixture import add_ground_truth, add_recording, add_run

from app.benchmarks.manifest import ManifestGroundTruth, ManifestRecording, build_recording_manifest
from app.benchmarks.schema import PHYSICAL_TF_PROTOCOL
from app.benchmarks.service import DatasetBenchmarkService
from app.core.errors import PlatformError


def _service(client) -> DatasetBenchmarkService:
    with client.app.state.database.session_factory() as session:
        return DatasetBenchmarkService(session)


def _populate(client):
    database = client.app.state.database
    with database.session_factory() as session:
        add_recording(session, recording_id="rec_a", name="a")
        add_recording(session, recording_id="rec_b", name="b")
        add_recording(session, recording_id="rec_c", name="c")
        for rec_id, stem in (("rec_a", "a"), ("rec_b", "b"), ("rec_c", "c")):
            add_ground_truth(session, gt_id=f"gt_{rec_id}", recording_id=rec_id, class_id=9,
                             class_name="LoRa 250kHz", t0=0.01, t1=0.02, f0=2_440_600_000.0, f1=2_440_700_000.0)
        # rec_a resolved (1 completed), rec_b missing (0), rec_c ambiguous (2)
        add_run(session, run_id="run_a1", recording_id="rec_a", pipeline_id="pipeline_x", pipeline_version="1.0")
        add_run(session, run_id="run_c1", recording_id="rec_c", pipeline_id="pipeline_x", pipeline_version="1.0", created_at=__import__("datetime").datetime(2026, 1, 1))
        add_run(session, run_id="run_c2", recording_id="rec_c", pipeline_id="pipeline_x", pipeline_version="1.0", created_at=__import__("datetime").datetime(2026, 1, 2))
        session.commit()


def _current_hash(client):
    recordings = []
    with client.app.state.database.session_factory() as session:
        from app.recordings.model import RecordingModel
        rows = session.query(RecordingModel).filter(RecordingModel.has_ground_truth.is_(True)).order_by(RecordingModel.name).all()
        for row in rows:
            gts = [ManifestGroundTruth(t_start_s=g.t_start_s, t_end_s=g.t_end_s, f_low_hz=g.f_low_hz,
                                       f_high_hz=g.f_high_hz, class_id=g.class_id, class_name=g.class_name)
                   for g in row.ground_truth]
            recordings.append(ManifestRecording(
                recording_id=row.id, name=row.name, data_format=row.data_format, sample_rate_hz=row.sample_rate_hz,
                center_frequency_hz=row.center_frequency_hz, frequency_low_hz=row.frequency_low_hz,
                frequency_high_hz=row.frequency_high_hz, num_samples=row.num_samples, duration_s=row.duration_s,
                ground_truth=tuple(gts),
            ))
    return build_recording_manifest("SpaceNet", "test", "spacenet_14", recordings).sha256


def test_prepare_manifest_requires_gt_and_returns_deterministic_hash(client):
    _populate(client)
    svc = _service(client)
    manifest = svc.prepare_manifest("SpaceNet", "test", "spacenet_14")
    assert manifest.expected_recordings == 3
    assert len(manifest.entries) == 3
    assert manifest.recording_manifest_hash == _current_hash(client)
    assert [e.manifest_order for e in manifest.entries] == [0, 1, 2]


def test_pipeline_snapshot_reports_resolved_missing_and_ambiguous_without_auto_selection(client):
    _populate(client)
    svc = _service(client)
    snapshot = svc.resolve_pipeline_snapshot("SpaceNet", "test", "spacenet_14", "pipeline_x", "1.0")
    by_name = {entry.recording_name: entry for entry in snapshot.entries}
    assert by_name["a"].resolution == "resolved"
    assert by_name["a"].candidate_run_ids == ("run_a1",)
    assert by_name["b"].resolution == "missing"
    assert by_name["b"].candidate_run_ids == ()
    assert by_name["c"].resolution == "ambiguous"
    assert by_name["c"].candidate_run_ids == ("run_c1", "run_c2")
    assert not hasattr(by_name["c"], "chosen_run_id")


def _full_items(run_for_recording: dict | None = None):
    """Return one item per manifest Recording; missing ones default to None."""
    return [
        {"recording_id": "rec_a", "analysis_run_id": (run_for_recording or {}).get("rec_a")},
        {"recording_id": "rec_b", "analysis_run_id": (run_for_recording or {}).get("rec_b")},
        {"recording_id": "rec_c", "analysis_run_id": (run_for_recording or {}).get("rec_c")},
    ]


def test_create_evaluation_rejects_stale_manifest_hash(client):
    _populate(client)
    svc = _service(client)
    with pytest.raises(PlatformError) as exc:
        svc.create_evaluation(
            name="eval", dataset_name="SpaceNet", dataset_split="test", label_space="spacenet_14",
            recording_manifest_hash="0" * 64,
            items=_full_items({"rec_a": "run_a1"}),
        )
    assert exc.value.code == "DATASET_MANIFEST_CHANGED"


def test_create_evaluation_freezes_exact_recording_to_run_mapping(client):
    _populate(client)
    svc = _service(client)
    evaluation = svc.create_evaluation(
        name="eval", dataset_name="SpaceNet", dataset_split="test", label_space="spacenet_14",
        recording_manifest_hash=_current_hash(client),
        items=_full_items({"rec_a": "run_a1"}),
        allow_incomplete=True,
    )
    assert evaluation.status == "pending"
    assert evaluation.expected_recordings == 3
    assert evaluation.evaluated_recordings == 1
    assert evaluation.missing_recordings == 2
    assert evaluation.coverage == pytest.approx(1 / 3)
    assert evaluation.comparable is False
    assert evaluation.evaluation_protocol == PHYSICAL_TF_PROTOCOL
    by_order = {item.manifest_order: item for item in evaluation.items}
    assert by_order[0].analysis_run_id == "run_a1"
    assert by_order[0].status == "included"
    assert by_order[1].analysis_run_id is None
    assert by_order[1].status == "missing_run"


def test_newer_run_created_after_freeze_does_not_change_membership(client):
    _populate(client)
    svc = _service(client)
    evaluation = svc.create_evaluation(
        name="eval", dataset_name="SpaceNet", dataset_split="test", label_space="spacenet_14",
        recording_manifest_hash=_current_hash(client),
        items=_full_items({"rec_a": "run_a1"}),
        allow_incomplete=True,
    )
    database = client.app.state.database
    with database.session_factory() as session:
        add_run(session, run_id="run_a2", recording_id="rec_a", pipeline_id="pipeline_x", pipeline_version="1.0",
                created_at=__import__("datetime").datetime(2026, 2, 1))
        session.commit()
    with database.session_factory() as session:
        model = __import__("app.benchmarks.model", fromlist=["DatasetEvaluationModel"]).DatasetEvaluationModel
        stored = session.get(model, evaluation.id)
        item_a = next(i for i in stored.items if i.recording_id == "rec_a")
        assert item_a.analysis_run_id == "run_a1"


def test_create_evaluation_rejects_run_for_wrong_recording(client):
    _populate(client)
    svc = _service(client)
    with pytest.raises(PlatformError) as exc:
        svc.create_evaluation(
            name="eval", dataset_name="SpaceNet", dataset_split="test", label_space="spacenet_14",
            recording_manifest_hash=_current_hash(client),
            items=_full_items({"rec_b": "run_a1"}),
        )
    assert exc.value.code == "INVALID_BENCHMARK_MEMBERSHIP"


def test_create_evaluation_rejects_noncompleted_run(client):
    _populate(client)
    database = client.app.state.database
    with database.session_factory() as session:
        add_run(session, run_id="run_b_running", recording_id="rec_b", pipeline_id="pipeline_x",
                pipeline_version="1.0", status="running")
        session.commit()
    svc = _service(client)
    with pytest.raises(PlatformError) as exc:
        svc.create_evaluation(
            name="eval", dataset_name="SpaceNet", dataset_split="test", label_space="spacenet_14",
            recording_manifest_hash=_current_hash(client),
            items=_full_items({"rec_b": "run_b_running"}),
        )
    assert exc.value.code == "INVALID_BENCHMARK_MEMBERSHIP"


def test_create_evaluation_rejects_mixed_pipeline_or_version(client):
    _populate(client)
    database = client.app.state.database
    with database.session_factory() as session:
        add_run(session, run_id="run_b_other", recording_id="rec_b", pipeline_id="pipeline_y", pipeline_version="2.0")
        session.commit()
    svc = _service(client)
    with pytest.raises(PlatformError) as exc:
        svc.create_evaluation(
            name="eval", dataset_name="SpaceNet", dataset_split="test", label_space="spacenet_14",
            recording_manifest_hash=_current_hash(client),
            items=_full_items({"rec_a": "run_a1", "rec_b": "run_b_other"}),
        )
    assert exc.value.code == "INVALID_BENCHMARK_MEMBERSHIP"


def test_incomplete_mapping_requires_allow_incomplete(client):
    _populate(client)
    svc = _service(client)
    with pytest.raises(PlatformError) as exc:
        svc.create_evaluation(
            name="eval", dataset_name="SpaceNet", dataset_split="test", label_space="spacenet_14",
            recording_manifest_hash=_current_hash(client),
            items=_full_items({"rec_a": "run_a1"}),
            allow_incomplete=False,
        )
    assert exc.value.code == "INVALID_BENCHMARK_MEMBERSHIP"


def test_incomplete_mapping_sets_coverage_and_comparable_false(client):
    _populate(client)
    svc = _service(client)
    evaluation = svc.create_evaluation(
        name="eval", dataset_name="SpaceNet", dataset_split="test", label_space="spacenet_14",
        recording_manifest_hash=_current_hash(client),
        items=_full_items({"rec_a": "run_a1"}),
        allow_incomplete=True,
    )
    assert evaluation.coverage == pytest.approx(1 / 3)
    assert evaluation.comparable is False
    assert evaluation.evaluated_recordings == 1
    assert evaluation.missing_recordings == 2


def test_create_evaluation_requires_at_least_one_included_run(client):
    _populate(client)
    svc = _service(client)
    with pytest.raises(PlatformError) as exc:
        svc.create_evaluation(
            name="eval", dataset_name="SpaceNet", dataset_split="test", label_space="spacenet_14",
            recording_manifest_hash=_current_hash(client),
            items=_full_items({}),
            allow_incomplete=True,
        )
    assert exc.value.code == "INVALID_BENCHMARK_MEMBERSHIP"

def _stub_pipeline(task_capability="classification", label_space="spacenet_14"):
    return type("_Pipeline", (), {"definition": type("_Definition", (), {
        "task_capability": task_capability, "label_space": label_space,
    })()})()


class _Registry:
    def __init__(self, mapping):
        self._mapping = mapping
    def get(self, pipeline_id):
        from app.core.errors import PlatformError
        pipeline = self._mapping.get(pipeline_id)
        if pipeline is None:
            raise PlatformError("PIPELINE_INCOMPATIBLE", "missing")
        return pipeline


def _run(pipeline_id="stft_energy_detector", executor="local_cpu"):
    return type("_Run", (), {"pipeline_id": pipeline_id, "executor": executor})()


def _recording(label_space="spacenet_14"):
    return type("_Recording", (), {"label_space": label_space})()


def test_capability_detection_only_pipeline(client):
    from app.evaluation.capability import classification_applicability
    registry = _Registry({"stft_energy_detector": _stub_pipeline(task_capability="detection_localization", label_space="signal_presence_v1")})
    result = classification_applicability(_run("stft_energy_detector"), _recording(), registry)
    assert result.applicable is False
    assert result.reason == "detection_only_pipeline"

def test_capability_classification_pipeline_matching_label_space(client):
    from app.evaluation.capability import classification_applicability
    registry = _Registry({"dummy": _stub_pipeline(task_capability="classification", label_space="spacenet_14")})
    result = classification_applicability(_run("dummy"), _recording(), registry)
    assert result.applicable is True
    assert result.reason is None


def test_capability_classification_pipeline_mismatched_label_space(client):
    from app.evaluation.capability import classification_applicability
    registry = _Registry({"dummy": _stub_pipeline(task_capability="classification", label_space="other")})
    result = classification_applicability(_run("dummy"), _recording("spacenet_14"), registry)
    assert result.applicable is False
    assert result.reason == "label_space_mismatch"


def test_capability_imported_run_with_recording_label_space(client):
    from app.evaluation.capability import classification_applicability
    result = classification_applicability(_run("zoomspec", executor="imported"), _recording("spacenet_14"), None)
    assert result.applicable is True
    assert result.reason is None


def test_capability_unknown_nonimported_run(client):
    from app.evaluation.capability import classification_applicability
    result = classification_applicability(_run("mystery", executor="local_cpu"), _recording("spacenet_14"), None)
    assert result.applicable is False
    assert result.reason == "unknown_classification_semantics"
