"""Coordinator cold-subprocess bootstrap regression (Task 12F-C corrective).

The REAL standalone entrypoint ``python -m app.remote_execution.coordinator``
must open its first SQLAlchemy session only after ``load_domain_models()`` has
registered every ORM model (notably ``DetectionResultModel``, referenced by the
``AnalysisRunModel`` mapper). Live Gate A proved the child died before submit
with ``InvalidRequestError ... 'DetectionResultModel' failed to locate a name``.

This regression launches the ACTUAL subprocess against an isolated SQLite DB
with a completed ``remote_gpu`` run and valid minimal RemoteProfile env: the
child must exit 0 and perform NO SSH/network I/O (the completed path returns
before submit).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = str(Path(__file__).resolve().parents[1])
RUN_ID = "run_cold"
TOKEN = "tok_cold"
RUNTIME = "a" * 40
MANIFEST = "b" * 64


def _seed(db_url: str) -> None:
    from app.analysis.model import AnalysisRunModel
    from app.db.base import Base, load_domain_models
    from app.db.session import Database
    from app.recordings.model import RecordingModel
    from app.remote_execution.request_builder import freeze_request_provenance

    load_domain_models()
    database = Database(db_url)
    Base.metadata.create_all(database.engine)
    metadata = freeze_request_provenance(
        local_run_id=RUN_ID,
        recording_fingerprint="2" * 64,
        source_data_sha256="1" * 64,
        dataset_name="SpaceNet",
        dataset_split="test",
        dataset_key="0",
        label_space="spacenet_14",
        pipeline_id="zoomspec_yolo26n_aug_combined_frn_v3",
        pipeline_version="1.0.0",
        required_remote_runtime_commit=RUNTIME,
        orchestrator_commit=RUNTIME,
        asset_manifest_sha256=MANIFEST,
        remote_profile="autodl_primary",
    )
    metadata["coordinator_token"] = TOKEN
    with database.session_factory() as session:
        session.add(RecordingModel(
            id="rec", name="0", data_path="recordings/0/raw.iq", data_format="complex64_le",
            sample_rate_hz=1e6, center_frequency_hz=0.0, frequency_low_hz=-5e5, frequency_high_hz=5e5,
            num_samples=1000, duration_s=0.001, dataset_name="SpaceNet", dataset_split="test",
            label_space="spacenet_14", source_data_sha256="1" * 64,
        ))
        session.add(AnalysisRunModel(
            id=RUN_ID, recording_id="rec",
            pipeline_id="zoomspec_yolo26n_aug_combined_frn_v3",
            pipeline_version="1.0.0", executor="remote_gpu", status="completed",
            parameters_json={}, execution_metadata_json=metadata,
        ))
        session.commit()


def _child_env(tmp_path: Path, database_url: str) -> dict[str, str]:
    key = tmp_path / "id_ed25519"
    key.write_bytes(b"key")
    hosts = tmp_path / "known_hosts"
    hosts.write_bytes(b"hosts")
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "PYTHONPATH": BACKEND_ROOT,
        "WSP_DATABASE_URL": database_url,
        "WSP_PROJECT_ROOT": str(tmp_path / "project"),
        "WSP_DATA_ROOT": str(tmp_path / "data"),
        "WSP_LABEL_SPACE_ROOT": str(tmp_path / "label_spaces"),
        "WSP_REMOTE_PROFILE_NAME": "autodl_primary",
        "WSP_REMOTE_HOST": "127.0.0.1",
        "WSP_REMOTE_PORT": "22",
        "WSP_REMOTE_USER": "root",
        "WSP_REMOTE_SSH_KEY_PATH": str(key),
        "WSP_REMOTE_KNOWN_HOSTS_PATH": str(hosts),
        "WSP_REMOTE_REPO_ROOT": "/root/repo",
        "WSP_REMOTE_JOB_ROOT": "/root/jobs",
        "WSP_REMOTE_PYTHON_PATH": "/opt/wsp-runtime/bin/python",
        "WSP_REMOTE_REQUIRED_RUNTIME_COMMIT": RUNTIME,
        "WSP_REMOTE_DATASET_ROOTS_JSON": json.dumps({"SpaceNet": "/root/autodl-tmp/SpaceNet_Dataset"}),
        "WSP_REMOTE_ASSET_PATHS_JSON": json.dumps({
            "zoomspec_yolo26n_aug_combined_frn_v3/1.0.0/" + "b" * 64: {
                "detector_checkpoint": "/root/models/best.pt"
            }
        }),
    }
    return env


def test_coordinator_cold_subprocess_completed_exit_zero(tmp_path):
    """The real subprocess entrypoint must bootstrap ORM models before its first
    session use, exit 0 on an already-completed run, and perform NO SSH/network."""
    database_url = f"sqlite:///{tmp_path / 'cold.db'}"
    _seed(database_url)
    env = _child_env(tmp_path, database_url)
    result = subprocess.run(
        [sys.executable, "-m", "app.remote_execution.coordinator", RUN_ID,
         "--coordinator-token", TOKEN],
        env=env,
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stderr
    assert "InvalidRequestError" not in result.stderr
    assert "Traceback" not in result.stderr
    assert "DetectionResultModel" not in result.stderr