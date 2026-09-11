import json
from pathlib import Path

import pytest

from app.core.config import Settings
from app.remote_execution.profile import RemoteProfile

MANIFEST_SHA = "16cc0534ed61603a84142da8a04af6642f9e7661848835fe5473199bec38ac08"
# Accepted M9.1 remote runtime commit (bound by the golden ExecutionCertificate);
# remote availability is now certificate-gated.
RUNTIME_COMMIT = "5bb5be4b04d04a071bc9d8f4f61172595ecee037"


def _set_valid_remote_env(tmp_path, monkeypatch):
    key = tmp_path / "id_ed25519"
    key.write_bytes(b"key")
    hosts = tmp_path / "known_hosts"
    hosts.write_bytes(b"hosts")
    env = {
        "WSP_REMOTE_PROFILE_NAME": "autodl_primary",
        "WSP_REMOTE_HOST": "auto.example.com",
        "WSP_REMOTE_PORT": "22",
        "WSP_REMOTE_USER": "root",
        "WSP_REMOTE_SSH_KEY_PATH": str(key),
        "WSP_REMOTE_KNOWN_HOSTS_PATH": str(hosts),
        "WSP_REMOTE_REPO_ROOT": "/root/repo",
        "WSP_REMOTE_JOB_ROOT": "/root/jobs",
        "WSP_REMOTE_PYTHON_PATH": "/opt/wsp-runtime/bin/python",
        "WSP_REMOTE_REQUIRED_RUNTIME_COMMIT": RUNTIME_COMMIT,
        "WSP_REMOTE_DATASET_ROOTS_JSON": json.dumps({"SpaceNet": "/root/autodl-tmp/SpaceNet_Dataset"}),
        "WSP_REMOTE_ASSET_PATHS_JSON": json.dumps({
            "zoomspec_yolo26n_aug_combined_frn_v3/1.0.0/" + "b" * 64: {
                "detector_checkpoint": "/root/models/best.pt"
            }
        }),
    }
    for name, value in env.items():
        monkeypatch.setenv(name, value)


def _set_core_remote_env(tmp_path, monkeypatch, *, dataset_json=None, asset_json=None):
    """Valid CORE RemoteProfile env; scientific mappings are optional (absent by
    default = Case B: core valid, scientific mappings missing)."""
    key = tmp_path / "id_ed25519"
    key.write_bytes(b"key")
    hosts = tmp_path / "known_hosts"
    hosts.write_bytes(b"hosts")
    env = {
        "WSP_REMOTE_PROFILE_NAME": "autodl_primary",
        "WSP_REMOTE_HOST": "auto.example.com",
        "WSP_REMOTE_PORT": "22",
        "WSP_REMOTE_USER": "root",
        "WSP_REMOTE_SSH_KEY_PATH": str(key),
        "WSP_REMOTE_KNOWN_HOSTS_PATH": str(hosts),
        "WSP_REMOTE_REPO_ROOT": "/root/repo",
        "WSP_REMOTE_JOB_ROOT": "/root/jobs",
        "WSP_REMOTE_PYTHON_PATH": "/opt/wsp-runtime/bin/python",
        "WSP_REMOTE_REQUIRED_RUNTIME_COMMIT": RUNTIME_COMMIT,
    }
    if dataset_json is not None:
        env["WSP_REMOTE_DATASET_ROOTS_JSON"] = dataset_json
    if asset_json is not None:
        env["WSP_REMOTE_ASSET_PATHS_JSON"] = asset_json
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    for name in ("WSP_REMOTE_DATASET_ROOTS_JSON", "WSP_REMOTE_ASSET_PATHS_JSON"):
        if name not in env:
            monkeypatch.delenv(name, raising=False)


def _repo_settings(tmp_path):
    """Settings rooted at the real repo so the tracked asset manifest resolves,
    with an isolated temp DB/data root."""
    repo_root = Path(__file__).resolve().parents[2]
    return Settings(
        project_root=repo_root,
        data_root=tmp_path / "data",
        label_space_root=repo_root / "label_spaces",
        database_url=f"sqlite:///{tmp_path / 'bootstrap.db'}",
    )


def test_create_app_wires_full_remote_lifecycle_with_valid_config(tmp_path, monkeypatch):
    _set_valid_remote_env(tmp_path, monkeypatch)
    settings = _repo_settings(tmp_path)
    from app.main import create_app

    app = create_app(settings)
    state = app.state
    assert state.remote_executor_probe is not None
    assert state.remote_coordinator_launcher is not None
    assert callable(state.identity_resolver)
    assert callable(state.orchestrator_commit_resolver)
    assert state.model_release_store is not None
    assert state.runtime_commit_config == RUNTIME_COMMIT
    assert state.project_root == settings.project_root
    assert state.data_root == settings.data_root
    assert state.remote_config_available is True


def test_create_app_invalid_config_stays_healthy(monkeypatch, tmp_path):
    # Complete-looking but invalid: SSH key path missing -> RemoteProfile fails.
    key = tmp_path / "missing_key"
    env = {
        "WSP_REMOTE_PROFILE_NAME": "autodl_primary",
        "WSP_REMOTE_HOST": "auto.example.com",
        "WSP_REMOTE_PORT": "22",
        "WSP_REMOTE_USER": "root",
        "WSP_REMOTE_SSH_KEY_PATH": str(key),  # does not exist
        "WSP_REMOTE_KNOWN_HOSTS_PATH": str(tmp_path / "kh"),
        "WSP_REMOTE_REPO_ROOT": "/root/repo",
        "WSP_REMOTE_JOB_ROOT": "/root/jobs",
        "WSP_REMOTE_PYTHON_PATH": "/opt/wsp-runtime/bin/python",
        "WSP_REMOTE_REQUIRED_RUNTIME_COMMIT": RUNTIME_COMMIT,
    }
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    from app.main import create_app

    app = create_app(_repo_settings(tmp_path))
    assert app.state.remote_config_available is False
    assert app.state.remote_executor_probe is None
    # app remains functional
    assert app.state.job_manager is not None


def test_create_app_missing_config_stays_healthy(monkeypatch, tmp_path):
    # Remove all remote env.
    for name in list(__import__("os").environ):
        if name.startswith("WSP_REMOTE_"):
            monkeypatch.delenv(name, raising=False)
    from app.main import create_app

    app = create_app(_repo_settings(tmp_path))
    assert app.state.remote_config_available is False
    assert app.state.remote_executor_probe is None
    assert app.state.remote_coordinator_launcher is None


def test_production_availability_route_available_with_fake_probe(monkeypatch, tmp_path):
    """create_app + monkeypatched SshRunner probe returns available=True when a
    compatible recording exists and the strict fake probe response matches."""
    import json
    from types import SimpleNamespace

    _set_valid_remote_env(tmp_path, monkeypatch)
    from app.remote_execution import transport

    probe_payload = json.dumps({
        "schema_version": 1,
        "status": "available",
        "remote_runtime_commit": RUNTIME_COMMIT,
        "asset_manifest_sha256": MANIFEST_SHA,
        "device": 0,
    }).encode("utf-8")

    def fake_run_runner(self, subcommand, args=()):
        assert subcommand == "probe"
        return SimpleNamespace(returncode=0, stdout=probe_payload, stderr=b"")

    monkeypatch.setattr(transport.SshRunner, "run_runner", fake_run_runner)

    from app.main import create_app

    app = create_app(_repo_settings(tmp_path))
    assert app.state.remote_config_available is True
    # register a compatible recording
    with app.state.database.session_factory() as session:
        from app.recordings.model import RecordingModel
        session.add(RecordingModel(
            id="rec", name="0", data_path="recordings/0/raw.iq", data_format="complex64_le",
            sample_rate_hz=1e6, center_frequency_hz=0.0, frequency_low_hz=-5e5, frequency_high_hz=5e5,
            num_samples=1000, duration_s=0.001, dataset_name="SpaceNet", dataset_split="test",
            label_space="spacenet_14", source_data_sha256="1" * 64,
        ))
        session.commit()
    with app.state.database.session_factory() as session:
        from app.analysis.service import AnalysisService
        service = AnalysisService(
            session,
            app.state.pipeline_registry,
            app.state.job_manager,
            app.state.remote_executor_probe,
            executor_registry=app.state.executor_registry,
            model_release_store=app.state.model_release_store,
        )
        availability = service.executor_availability("rec", "zoomspec_yolo26n_aug_combined_frn_v3")
    assert availability.available is True
    assert availability.reason_code is None


def test_http_executor_availability_route_available(monkeypatch, tmp_path):
    """Full HTTP acceptance: create_app wiring -> app.state -> analysis.router ->
    AnalysisService -> probe, via GET /api/executor-availability."""
    import json
    from types import SimpleNamespace

    _set_valid_remote_env(tmp_path, monkeypatch)
    from app.remote_execution import transport

    probe_payload = json.dumps({
        "schema_version": 1,
        "status": "available",
        "remote_runtime_commit": RUNTIME_COMMIT,
        "asset_manifest_sha256": MANIFEST_SHA,
        "device": 0,
    }).encode("utf-8")

    def fake_run_runner(self, subcommand, args=()):
        assert subcommand == "probe"
        return SimpleNamespace(returncode=0, stdout=probe_payload, stderr=b"")

    monkeypatch.setattr(transport.SshRunner, "run_runner", fake_run_runner)

    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app(_repo_settings(tmp_path))
    with app.state.database.session_factory() as session:
        from app.recordings.model import RecordingModel
        session.add(RecordingModel(
            id="rec_http", name="0", data_path="recordings/0/raw.iq", data_format="complex64_le",
            sample_rate_hz=1e6, center_frequency_hz=0.0, frequency_low_hz=-5e5, frequency_high_hz=5e5,
            num_samples=1000, duration_s=0.001, dataset_name="SpaceNet", dataset_split="test",
            label_space="spacenet_14", source_data_sha256="1" * 64,
        ))
        session.commit()

    client = TestClient(app)
    response = client.get(
        "/api/executor-availability",
        params={"recording_id": "rec_http", "pipeline_id": "zoomspec_yolo26n_aug_combined_frn_v3"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert body["reason_code"] is None


# ---------------------------------------------------------------- Task 3: Case A/B


def _seed_db(settings, *, remote_run_status="running"):
    """Create tables and seed one remote_gpu run (plus a recording)."""
    from app.analysis.model import AnalysisRunModel
    from app.db.base import Base, load_domain_models
    from app.db.session import Database
    from app.detections.model import DetectionResultModel
    from app.recordings.model import RecordingModel

    database = Database(settings.database_url)
    load_domain_models()
    Base.metadata.create_all(database.engine)
    with database.session_factory() as session:
        session.add(RecordingModel(
            id="rec", name="0", data_path="recordings/0/raw.iq", data_format="complex64_le",
            sample_rate_hz=1e6, center_frequency_hz=0.0, frequency_low_hz=-5e5, frequency_high_hz=5e5,
            num_samples=1000, duration_s=0.001, dataset_name="SpaceNet", dataset_split="test",
            label_space="spacenet_14", source_data_sha256="1" * 64,
        ))
        session.add(AnalysisRunModel(
            id="run_x", recording_id="rec",
            pipeline_id="zoomspec_yolo26n_aug_combined_frn_v3",
            pipeline_version="1.0.0", executor="remote_gpu",
            status=remote_run_status, parameters_json={},
            execution_metadata_json={"coordinator_token": "old_token"},
        ))
        if remote_run_status == "completed":
            session.add(DetectionResultModel(
                id="det_x", run_id="run_x", t_start_s=0.01, t_end_s=0.02,
                f_low_hz=2440600000.0, f_high_hz=2440700000.0,
                class_id=9, class_name="LoRa 250kHz", confidence=0.94,
            ))
        session.commit()


def _record_launch(monkeypatch):
    launches = []
    monkeypatch.setattr(
        "app.remote_execution.coordinator_job_manager.CoordinatorJobManager.launch",
        lambda self, run_id, coordinator_token, **kwargs: launches.append(run_id) or 0,
    )
    return launches


def _missing_ssh_key_env(tmp_path, monkeypatch):
    key = tmp_path / "missing_key"
    env = {
        "WSP_REMOTE_PROFILE_NAME": "autodl_primary",
        "WSP_REMOTE_HOST": "auto.example.com",
        "WSP_REMOTE_PORT": "22",
        "WSP_REMOTE_USER": "root",
        "WSP_REMOTE_SSH_KEY_PATH": str(key),
        "WSP_REMOTE_KNOWN_HOSTS_PATH": str(tmp_path / "kh"),
        "WSP_REMOTE_REPO_ROOT": "/root/repo",
        "WSP_REMOTE_JOB_ROOT": "/root/jobs",
        "WSP_REMOTE_PYTHON_PATH": "/opt/wsp-runtime/bin/python",
        "WSP_REMOTE_REQUIRED_RUNTIME_COMMIT": RUNTIME_COMMIT,
    }
    for name, value in env.items():
        monkeypatch.setenv(name, value)


def test_case_a_missing_ssh_key_disables_lifecycle_no_recovery_launch(tmp_path, monkeypatch):
    """Case A (core RemoteProfile invalid) -> config False, no probe/launcher,
    existing remote run untouched, NO recovery launch."""
    _missing_ssh_key_env(tmp_path, monkeypatch)
    settings = _repo_settings(tmp_path)
    _seed_db(settings, remote_run_status="running")
    launches = _record_launch(monkeypatch)

    from app.main import create_app

    app = create_app(settings)
    assert app.state.remote_config_available is False
    assert app.state.remote_executor_probe is None
    assert app.state.remote_coordinator_launcher is None
    assert launches == []
    with app.state.database.session_factory() as session:
        from app.analysis.model import AnalysisRunModel
        run = session.get(AnalysisRunModel, "run_x")
        assert run.status == "running"  # not interrupted
        assert run.execution_metadata_json["coordinator_token"] == "old_token"


def test_case_b_core_valid_missing_scientific_mappings_config_true(tmp_path, monkeypatch):
    """Case B (core valid, scientific mappings missing) -> config True; real
    probe + coordinator launcher remain wired; recovery may re-coordinate."""
    _set_core_remote_env(tmp_path, monkeypatch)  # no dataset/asset JSON
    settings = _repo_settings(tmp_path)
    _seed_db(settings, remote_run_status="running")
    launches = _record_launch(monkeypatch)

    from app.main import create_app

    app = create_app(settings)
    assert app.state.remote_config_available is True
    from app.remote_execution.executor import SshRemoteExecutorProbe
    from app.remote_execution.coordinator_job_manager import CoordinatorJobManager
    assert isinstance(app.state.remote_executor_probe, SshRemoteExecutorProbe)
    assert isinstance(app.state.remote_coordinator_launcher, CoordinatorJobManager)
    # recovery MAY launch a reconciliation coordinator for the orphaned run
    assert launches == ["run_x"]
    with app.state.database.session_factory() as session:
        from app.analysis.model import AnalysisRunModel
        run = session.get(AnalysisRunModel, "run_x")
        assert run.status == "running"
        assert run.execution_metadata_json["coordinator_token"] != "old_token"


def test_case_b_availability_unavailable_and_create_run_rejected(tmp_path, monkeypatch):
    """Case B probe fails its full-worker preflight -> availability
    REMOTE_TRANSPORT_UNAVAILABLE; create_run(remote_gpu) rejected; no run row."""
    _set_core_remote_env(tmp_path, monkeypatch)
    settings = _repo_settings(tmp_path)
    _seed_db(settings, remote_run_status="running")
    _record_launch(monkeypatch)

    from app.main import create_app

    app = create_app(settings)
    from fastapi.testclient import TestClient
    client = TestClient(app)

    response = client.get(
        "/api/executor-availability",
        params={"recording_id": "rec", "pipeline_id": "zoomspec_yolo26n_aug_combined_frn_v3"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is False
    assert body["reason_code"] == "REMOTE_TRANSPORT_UNAVAILABLE"

    created = client.post("/api/analysis-runs", json={
        "recording_id": "rec",
        "pipeline_id": "zoomspec_yolo26n_aug_combined_frn_v3",
        "executor": "remote_gpu",
        "parameters": {},
    })
    assert created.status_code == 400
    assert created.json()["error"]["code"] == "REMOTE_TRANSPORT_UNAVAILABLE"
    with app.state.database.session_factory() as session:
        from app.analysis.model import AnalysisRunModel
        assert session.query(AnalysisRunModel).filter(
            AnalysisRunModel.executor == "remote_gpu").count() == 1  # only run_x


def test_case_b_completed_run_unchanged(tmp_path, monkeypatch):
    _set_core_remote_env(tmp_path, monkeypatch)
    settings = _repo_settings(tmp_path)
    _seed_db(settings, remote_run_status="completed")
    _record_launch(monkeypatch)

    from app.main import create_app

    app = create_app(settings)
    with app.state.database.session_factory() as session:
        from app.analysis.model import AnalysisRunModel
        from app.detections.model import DetectionResultModel
        run = session.get(AnalysisRunModel, "run_x")
        assert run.status == "completed"
        rows = list(session.query(DetectionResultModel)
                    .filter(DetectionResultModel.run_id == "run_x").all())
        assert len(rows) == 1
        assert rows[0].id == "det_x"
        assert rows[0].confidence == 0.94