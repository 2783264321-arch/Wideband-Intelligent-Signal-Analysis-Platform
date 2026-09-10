"""TASK D2B.5 — real local CPU acceptance and release-less certificates.

Capability declarations and the seeded certificates are pinned here; the real
subprocess acceptance uses a configured non-control-plane CPU interpreter and the
REAL LocalInferenceWorkerProvider -> subprocess -> local_inference_worker path.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from app.analysis.model import AnalysisRunModel
from app.analysis.local_executor import build_local_providers
from app.core.config import Settings
from app.db.base import Base, load_domain_models
from app.db.migrations import run_additive_migrations
from app.db.session import Database
from app.detections.model import DetectionResultModel
from app.pipelines.base import ExecutionCapability
from app.recordings.model import RecordingModel
from app.remote_execution.runtime import ExecutionCertificateStore, load_execution_certificates

REPO_ROOT = Path(__file__).resolve().parents[2]
LABEL_ROOT = REPO_ROOT / "label_spaces"
CERT_PATH = REPO_ROOT / "backend" / "app" / "pipelines" / "execution_certificates.json"

# Frozen local CPU runtime generation (derived from the accepted interpreter's
# python+numpy+scipy fingerprint). Must never be derived from the mutable path.
EXPECTED_RUNTIME_REF = "local:autodl_primary:cpu:a1237f8faae7"
EXPECTED_EVIDENCE_REF = "m9_2_local_cpu_acceptance"
LOCAL_CAPABILITY = ExecutionCapability("local_cpu", "cpu", "float32")


def test_dummy_declares_local_cpu_capability():
    from app.pipelines.dummy import DummyPipeline

    definition = DummyPipeline().definition
    assert LOCAL_CAPABILITY in definition.technical_execution_capabilities
    assert definition.model_release_required is False


def test_stft_declares_local_cpu_capability():
    from app.pipelines.stft_energy.pipeline import STFTEnergyDetectorPipeline

    definition = STFTEnergyDetectorPipeline().definition
    assert LOCAL_CAPABILITY in definition.technical_execution_capabilities
    assert definition.model_release_required is False


def _certificate_store() -> ExecutionCertificateStore:
    return ExecutionCertificateStore(load_execution_certificates(CERT_PATH))


@pytest.mark.parametrize("plugin_id", ["dummy", "stft_energy_detector"])
def test_seed_certifies_local_cpu_only_at_exact_runtime_ref(plugin_id):
    store = _certificate_store()
    base = dict(
        plugin_id=plugin_id,
        plugin_version="1.0",
        executor="local_cpu",
        device_type="cpu",
        precision="float32",
    )
    assert store.is_certified(model_release_id=None, runtime_ref=EXPECTED_RUNTIME_REF, **base)
    assert not store.is_certified(
        model_release_id=None, runtime_ref="local:autodl_primary:cpu:deadbeef", **base
    )
    assert not store.is_certified(
        model_release_id="golden", runtime_ref=EXPECTED_RUNTIME_REF, **base
    )


def test_zoomspec_golden_certificate_unchanged():
    store = _certificate_store()
    assert store.is_certified(
        plugin_id="zoomspec_yolo26n_aug_combined_frn_v3",
        plugin_version="1.0.0",
        model_release_id="golden",
        executor="remote_gpu",
        device_type="cuda",
        precision="float16",
        runtime_ref="remote:autodl_primary:5bb5be4b04d04a071bc9d8f4f61172595ecee037",
    )


# ---------------------------------------------------------------------------
# Real subprocess acceptance
# ---------------------------------------------------------------------------


def _discover_interpreter() -> Path | None:
    candidates = [os.environ.get("WSP_LOCAL_CPU_PYTHON_PATH"), "/root/miniconda3/bin/python"]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return Path(candidate)
    return None


def _fingerprint(interpreter: Path) -> str:
    code = (
        "import json,sys; import numpy,scipy; "
        "print(json.dumps({'python':sys.version.split()[0],'numpy':numpy.__version__,"
        "'scipy':scipy.__version__},sort_keys=True))"
    )
    output = subprocess.run(
        [str(interpreter), "-c", code], capture_output=True, text=True, check=True
    ).stdout.strip()
    return hashlib.sha256(output.encode("utf-8")).hexdigest()[:12]


def _database(settings: Settings) -> Database:
    database = Database(settings.database_url)
    load_domain_models()
    Base.metadata.create_all(database.engine)
    run_additive_migrations(database.engine)
    return database


def _write_iq(path: Path, *, num_samples: int = 200_000, sample_rate_hz: float = 1_000_000.0) -> None:
    import numpy as np

    rng = np.random.default_rng(9)
    iq = (rng.standard_normal(num_samples) + 1j * rng.standard_normal(num_samples)).astype(np.complex64)
    start = int(0.04 * sample_rate_hz)
    end = int(0.12 * sample_rate_hz)
    n = np.arange(end - start)
    iq[start:end] += (3.0 * np.exp(2j * np.pi * 100_000.0 / sample_rate_hz * n)).astype(np.complex64)
    iq.astype("<c8").tofile(path)


def _seed_run(
    database: Database,
    *,
    run_id: str,
    plugin_id: str,
    label_space: str,
    data_root: Path,
    descriptor: dict,
    num_samples: int,
    sample_rate_hz: float,
    center_frequency_hz: float,
) -> None:
    relative = f"recordings/{run_id}/raw.iq"
    data_path = data_root / relative
    data_path.parent.mkdir(parents=True, exist_ok=True)
    if plugin_id == "stft_energy_detector":
        _write_iq(data_path, num_samples=num_samples, sample_rate_hz=sample_rate_hz)
    else:
        data_path.write_bytes(b"\x00" * 16)
    with database.session_factory() as session:
        session.add(
            RecordingModel(
                id=f"rec_{run_id}",
                name=run_id,
                data_path=relative,
                data_format="complex64_le",
                source="custom",
                sample_rate_hz=sample_rate_hz,
                center_frequency_hz=center_frequency_hz,
                frequency_low_hz=center_frequency_hz - sample_rate_hz / 2,
                frequency_high_hz=center_frequency_hz + sample_rate_hz / 2,
                num_samples=num_samples,
                duration_s=num_samples / sample_rate_hz,
                dataset_name="SpaceNet",
                dataset_split="test",
                label_space=label_space,
                has_ground_truth=False,
            )
        )
        session.add(
            AnalysisRunModel(
                id=run_id,
                recording_id=f"rec_{run_id}",
                pipeline_id=plugin_id,
                pipeline_version="1.0",
                executor="local_cpu",
                status="pending",
                parameters_json={},
                execution_metadata_json={"runtime_descriptor": descriptor},
            )
        )
        session.commit()


def _wait_terminal(database: Database, run_id: str, timeout_s: float = 60.0) -> AnalysisRunModel:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        with database.session_factory() as session:
            run = session.get(AnalysisRunModel, run_id)
            if run.status in {"completed", "failed", "interrupted"}:
                return run
        time.sleep(0.1)
    raise AssertionError(f"run {run_id} did not reach a terminal state in time")


def _detection_count(database: Database, run_id: str) -> int:
    with database.session_factory() as session:
        return session.query(DetectionResultModel).filter(DetectionResultModel.run_id == run_id).count()


def test_real_local_cpu_acceptance_dummy_and_stft(tmp_path):
    interpreter = _discover_interpreter()
    if interpreter is None:
        pytest.skip("no configured non-control-plane local CPU interpreter available")
    if str(interpreter) == str(sys.executable):
        pytest.skip("configured interpreter equals the control-plane interpreter")

    fingerprint = _fingerprint(interpreter)
    runtime_ref = f"local:autodl_primary:cpu:{fingerprint}"
    assert runtime_ref == EXPECTED_RUNTIME_REF, (
        "accepted runtime generation changed; re-run acceptance and re-certify"
    )

    settings = Settings(
        project_root=REPO_ROOT,
        data_root=tmp_path / "data",
        label_space_root=LABEL_ROOT,
        database_url=f"sqlite:///{tmp_path / 'acceptance.db'}",
        local_cpu_python_path=interpreter,
        local_cpu_runtime_ref=runtime_ref,
        local_inference_work_root=tmp_path / "work",
    )
    provider = build_local_providers(settings)["local_cpu"]
    descriptor = provider.runtime_descriptor().to_metadata()
    assert descriptor["environment_label"] == runtime_ref

    database = _database(settings)
    sample_rate_hz = 1_000_000.0
    center = 2_441_000_000.0

    # Dummy acceptance
    _seed_run(
        database,
        run_id="acc_dummy",
        plugin_id="dummy",
        label_space="spacenet_14",
        data_root=settings.data_root,
        descriptor=descriptor,
        num_samples=4096,
        sample_rate_hz=sample_rate_hz,
        center_frequency_hz=center,
    )
    assert provider.launch("acc_dummy", coordinator_token=None) > 0
    dummy_run = _wait_terminal(database, "acc_dummy")
    assert dummy_run.status == "completed", (dummy_run.error_type, dummy_run.error_message)
    assert _detection_count(database, "acc_dummy") >= 1

    # STFT acceptance on deterministic synthetic IQ
    _seed_run(
        database,
        run_id="acc_stft",
        plugin_id="stft_energy_detector",
        label_space="spacenet_14",
        data_root=settings.data_root,
        descriptor=descriptor,
        num_samples=200_000,
        sample_rate_hz=sample_rate_hz,
        center_frequency_hz=center,
    )
    assert provider.launch("acc_stft", coordinator_token=None) > 0
    stft_run = _wait_terminal(database, "acc_stft")
    assert stft_run.status == "completed", (stft_run.error_type, stft_run.error_message)
    stft_detections = _detection_count(database, "acc_stft")
    assert stft_detections >= 1, "STFT acceptance expected the injected burst detection"
