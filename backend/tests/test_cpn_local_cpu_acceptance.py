"""M9.2 F-A2 — CPN bandwidth tier local CPU scientific parity + certification.

This suite is the certification gate for the second real plugin on the local CPU
runtime. It contains:

- pure certificate/projection assertions that are RED until the F-A2 certificate
  is issued (exact tuple + deployment-qualified projection);
- a REAL scientific parity test that runs the accepted frozen CPN reference path
  and the CPN Plugin path on the same bounded real SpaceNet input and compares
  every propagated field (requires torch/ultralytics, so it is skipped in the
  control-plane interpreter and run with the configured ML interpreter);
- a REAL production full-recording acceptance test that launches the actual
  LocalInferenceWorkerProvider subprocess on a complete untruncated SpaceNet
  recording through the release-bound namespaced-asset path.

No training, no GPU. The ML interpreter is operator-configured; the real
acceptance tests skip when it (or the real assets/dataset) are unavailable.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from app.analysis.local_executor import LocalInferenceWorkerProvider, build_local_providers
from app.analysis.model import AnalysisRunModel
from app.core.config import Settings
from app.db.base import Base, load_domain_models
from app.db.migrations import run_additive_migrations
from app.db.session import Database
from app.detections.model import DetectionResultModel
from app.pipelines.plugin_registry import create_plugin_registry
from app.recordings.model import RecordingModel
from app.remote_execution.assets import load_pipeline_asset_manifest
from app.remote_execution.model_release import (
    ModelReleaseStore,
    load_model_release_defaults,
)
from app.remote_execution.runtime import (
    ExecutionCertificateStore,
    ExecutorRegistry,
    RuntimeDescriptor,
    load_execution_certificates,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
LABEL_ROOT = REPO_ROOT / "label_spaces"
PIPELINES_ROOT = BACKEND_ROOT / "app" / "pipelines"
CERT_PATH = PIPELINES_ROOT / "execution_certificates.json"

CPN_ID = "cpn_bandwidth_tier"
CPN_VERSION = "1.0.0"
CPN_RELEASE = "golden"
CPN_LABEL_SPACE = "cpn_bandwidth_tier_v1"
CPN_MANIFEST_SHA = "7ab8a6a4f5f93247d3997fcf88c4b05d1099361fa8db1555fc8daeeaf7fc55bb"
DETECTOR_SHA = "eba4fa4b112a0e61cc1013e96f99d1ae82b845f4be1e8b1f80bd2089d1f82311"
NORMALIZATION_SHA = "9b994655a279352b835b96cb00cefde89410fc6130458665dca7070de146d72f"

EXPECTED_RUNTIME_REF = "local:autodl_primary:cpu:a1237f8faae7"
EXPECTED_GENERATION_FINGERPRINT = "a1237f8faae7"
EVIDENCE_REF = "m9_2_fa2_cpn_local_cpu_acceptance"

_DEFAULT_INTERPRETERS = ("/root/miniconda3/bin/python",)
_DEFAULT_DETECTOR = Path("/root/autodl-tmp/release/weights/yolo26n_ls_stft_aug_best.pt")
_DEFAULT_NORMALIZATION = Path("/root/autodl-tmp/release/support/normalization_ls_stft.json")
_DEFAULT_SPACENET_ITEM = Path("/root/autodl-tmp/SpaceNet_Dataset/advanced/test/0.bin")

_BOUNDED_SAMPLES = 2_000_000

_CPN_NAMESPACE = f"{CPN_ID}/{CPN_VERSION}/{CPN_MANIFEST_SHA}"


# ---------------------------------------------------------------------------
# discovery helpers (operator-owned; env-overridable, skip when absent)
# ---------------------------------------------------------------------------


def _discover_interpreter() -> Path | None:
    candidates = [os.environ.get("WSP_LOCAL_CPU_PYTHON_PATH"), *_DEFAULT_INTERPRETERS]
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


def _discover_assets() -> tuple[Path, Path] | None:
    detector = Path(os.environ.get("WSP_FA2_DETECTOR_CHECKPOINT", str(_DEFAULT_DETECTOR)))
    normalization = Path(os.environ.get("WSP_FA2_LS_STFT_NORMALIZATION", str(_DEFAULT_NORMALIZATION)))
    if detector.is_file() and normalization.is_file():
        return detector, normalization
    return None


def _discover_spacenet_item() -> Path | None:
    item = Path(os.environ.get("WSP_FA2_SPACENET_ITEM", str(_DEFAULT_SPACENET_ITEM)))
    if item.is_file() and item.with_suffix(".json").is_file():
        return item
    return None


def _require_interpreter() -> Path:
    interpreter = _discover_interpreter()
    if interpreter is None:
        pytest.skip("no configured non-control-plane local CPU interpreter available")
    if str(interpreter) == str(sys.executable):
        pytest.skip("configured interpreter equals the control-plane interpreter")
    return interpreter


def _require_assets() -> tuple[Path, Path]:
    assets = _discover_assets()
    if assets is None:
        pytest.skip("real CPN deployment assets are not available")
    return assets


def _require_spacenet_item() -> Path:
    item = _discover_spacenet_item()
    if item is None:
        pytest.skip("real untruncated SpaceNet item is not available")
    return item


def _sample_metadata(item: Path) -> tuple[float, float, float, float, int]:
    payload = json.loads(item.with_suffix(".json").read_text(encoding="utf-8"))
    lo_mhz, hi_mhz = payload["observation_range"]
    sample_rate_hz = (hi_mhz - lo_mhz) * 1e6
    center_frequency_hz = ((lo_mhz + hi_mhz) / 2.0) * 1e6
    num_samples = item.stat().st_size // 4
    return lo_mhz * 1e6, hi_mhz * 1e6, sample_rate_hz, center_frequency_hz, num_samples


def _load_normalization(path: Path):
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.preprocessing import (
        LSSTFTNormalization,
    )

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return LSSTFTNormalization(
        percentile_low=float(payload["percentile_low"]),
        percentile_high=float(payload["percentile_high"]),
        value_low=float(payload["value_low"]),
        value_high=float(payload["value_high"]),
    )


def _bounded_recording(item: Path, tmp_path: Path):
    from app.pipelines.base import RecordingInput

    lo_hz, hi_hz, sample_rate_hz, center_hz, num_samples = _sample_metadata(item)
    count = min(_BOUNDED_SAMPLES, num_samples)
    bounded_path = tmp_path / f"{item.stem}_bounded_{count}.bin"
    with item.open("rb") as handle:
        bounded_path.write_bytes(handle.read(count * 4))
    return RecordingInput(
        id=f"{item.stem}_bounded",
        data_path=bounded_path,
        data_format="float16_interleaved_le",
        sample_rate_hz=sample_rate_hz,
        center_frequency_hz=center_hz,
        frequency_low_hz=lo_hz,
        frequency_high_hz=hi_hz,
        duration_s=count / sample_rate_hz,
        label_space="spacenet_14",
    )


# ---------------------------------------------------------------------------
# Certificate / projection (RED until F-A2 certificate is issued)
# ---------------------------------------------------------------------------


def _certificate_store() -> ExecutionCertificateStore:
    return ExecutionCertificateStore(load_execution_certificates(CERT_PATH))


def test_cpn_local_cpu_exact_tuple_certified():
    store = _certificate_store()
    assert store.is_certified(
        plugin_id=CPN_ID,
        plugin_version=CPN_VERSION,
        model_release_id=CPN_RELEASE,
        executor="local_cpu",
        device_type="cpu",
        precision="float32",
        runtime_ref=EXPECTED_RUNTIME_REF,
    ), "F-A2 CPN local CPU certificate is not issued"


def _descriptor_provider(name: str, device_type: str, precision: str, runtime_ref: str):
    descriptor = RuntimeDescriptor(
        executor=name,
        device_type=device_type,
        device_index=0 if device_type == "cuda" else None,
        precision=precision,
        environment_label=runtime_ref,
    )

    class _Provider:
        def __init__(self) -> None:
            self.name = name

        @property
        def runtime_ref(self) -> str:
            return runtime_ref

        def runtime_descriptor(self) -> RuntimeDescriptor:
            return descriptor

        def availability(self, definition, model_release, recording):  # pragma: no cover
            raise AssertionError("projection must never probe")

        def launch(self, run_id, *, coordinator_token):  # pragma: no cover
            raise AssertionError("projection must never launch")

    return _Provider()


def test_cpn_deployment_qualified_is_local_cpu_only():
    definition = create_plugin_registry().get(CPN_ID, CPN_VERSION).definition
    registry = ExecutorRegistry(
        {
            "local_cpu": _descriptor_provider(
                "local_cpu", "cpu", "float32", EXPECTED_RUNTIME_REF
            ),
            "local_gpu": _descriptor_provider(
                "local_gpu", "cuda", "float16", "local:autodl_primary:gpu:deadbeef"
            ),
            "remote_gpu": _descriptor_provider(
                "remote_gpu",
                "cuda",
                "float16",
                "remote:autodl_primary:5bb5be4b04d04a071bc9d8f4f61172595ecee037",
            ),
        },
        _certificate_store(),
    )
    supported, recommended = registry.deployment_qualified_executors(definition, CPN_RELEASE)
    assert supported == ["local_cpu"]
    assert "remote_gpu" not in supported
    assert "local_gpu" not in supported
    # recommended_execution is remote_gpu, which is not certified -> must be null.
    assert recommended is None


# ---------------------------------------------------------------------------
# ModelRelease / asset identity
# ---------------------------------------------------------------------------


def test_cpn_asset_identity_and_model_release_exact():
    detector, normalization = _require_assets()
    assert hashlib.sha256(detector.read_bytes()).hexdigest() == DETECTOR_SHA
    assert hashlib.sha256(normalization.read_bytes()).hexdigest() == NORMALIZATION_SHA

    manifest = load_pipeline_asset_manifest(PIPELINES_ROOT / CPN_ID / "asset_manifest.json")
    assert manifest.asset_manifest_sha256 == CPN_MANIFEST_SHA
    assert set(manifest.assets) == {"detector_checkpoint", "ls_stft_normalization"}

    store = ModelReleaseStore(
        PIPELINES_ROOT,
        load_model_release_defaults(PIPELINES_ROOT / "model_release_defaults.json"),
    )
    resolved = store.resolve(CPN_ID, CPN_VERSION, CPN_RELEASE)
    assert resolved.release.model_release_id == CPN_RELEASE
    assert resolved.manifest.asset_manifest_sha256 == CPN_MANIFEST_SHA
    assert resolved.manifest.assets == manifest.assets


# ---------------------------------------------------------------------------
# Real scientific parity: frozen reference vs CPN Plugin runtime
# ---------------------------------------------------------------------------


def test_real_frozen_cpn_reference_vs_cpn_plugin_parity(tmp_path):
    pytest.importorskip("torch")
    pytest.importorskip("ultralytics")
    detector, normalization = _require_assets()
    item = _require_spacenet_item()
    recording = _bounded_recording(item, tmp_path)
    norm = _load_normalization(normalization)

    # --- reference: accepted frozen science, no CPN Plugin wrapper ---
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.detector import CPNDetector
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.preprocessing import (
        build_ls_stft_spectrogram,
    )
    from app.recordings.reader import read_segment_from_path

    iq = read_segment_from_path(recording.data_path, recording.data_format)
    spectrogram = build_ls_stft_spectrogram(
        iq,
        sample_rate_hz=recording.sample_rate_hz,
        center_frequency_hz=recording.center_frequency_hz,
        frequency_low_hz=recording.frequency_low_hz,
        frequency_high_hz=recording.frequency_high_hz,
        normalization=norm,
        device="cpu",
    )
    proposals = CPNDetector(detector, device="cpu").detect_batch([spectrogram], batch_size=1)[0]

    # --- plugin: real generic PluginHandle -> build_runtime -> execute ---
    from app.labels.service import LabelSpaceService

    label_space = LabelSpaceService(LABEL_ROOT).get(CPN_LABEL_SPACE)
    handle = create_plugin_registry().get(CPN_ID, CPN_VERSION)
    runtime = handle.load_runtime(
        assets={
            "detector_checkpoint": detector,
            "ls_stft_normalization": normalization,
        },
        runtime_descriptor=RuntimeDescriptor("local_cpu", "cpu", None, "float32"),
        output_label_space=label_space,
    )
    output = runtime.execute(recording, {}, tmp_path / "parity_ws")

    assert len(proposals) == len(output.detections)
    reference = [
        (p.t_start_s, p.t_end_s, p.f_low_hz, p.f_high_hz, p.bandwidth_tier, p.confidence)
        for p in proposals
    ]
    plugin = [
        (d.t_start_s, d.t_end_s, d.f_low_hz, d.f_high_hz, d.class_id, d.confidence)
        for d in output.detections
    ]
    # Values are propagated directly: require exact equality.
    assert plugin == reference

    name_by_id = {label.id: label.name for label in label_space.classes}
    for detection in output.detections:
        assert detection.class_id in (0, 1, 2)
        assert detection.class_name == name_by_id[detection.class_id]
        assert detection.scores == {"cpn": detection.confidence}


# ---------------------------------------------------------------------------
# Real full untruncated SpaceNet production acceptance (provider subprocess)
# ---------------------------------------------------------------------------


def _database(settings: Settings) -> Database:
    database = Database(settings.database_url)
    load_domain_models()
    Base.metadata.create_all(database.engine)
    run_additive_migrations(database.engine)
    return database


def _seed_full_cpn_run(
    database: Database,
    *,
    run_id: str,
    item: Path,
    descriptor: dict,
) -> None:
    lo_hz, hi_hz, sample_rate_hz, center_hz, num_samples = _sample_metadata(item)
    with database.session_factory() as session:
        session.add(
            RecordingModel(
                id=f"rec_{run_id}",
                name=item.stem,
                data_path=item.name,
                external_path=str(item),
                data_format="float16_interleaved_le",
                source="custom",
                sample_rate_hz=sample_rate_hz,
                center_frequency_hz=center_hz,
                frequency_low_hz=lo_hz,
                frequency_high_hz=hi_hz,
                num_samples=num_samples,
                duration_s=num_samples / sample_rate_hz,
                dataset_name="SpaceNet",
                dataset_split="test",
                label_space="spacenet_14",
                has_ground_truth=False,
            )
        )
        session.add(
            AnalysisRunModel(
                id=run_id,
                recording_id=f"rec_{run_id}",
                pipeline_id=CPN_ID,
                pipeline_version=CPN_VERSION,
                executor="local_cpu",
                status="pending",
                parameters_json={},
                execution_metadata_json={
                    "runtime_descriptor": descriptor,
                    "model_release_id": CPN_RELEASE,
                    "asset_manifest_sha256": CPN_MANIFEST_SHA,
                },
            )
        )
        session.commit()


def _wait_terminal(database: Database, run_id: str, timeout_s: float) -> AnalysisRunModel:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        with database.session_factory() as session:
            run = session.get(AnalysisRunModel, run_id)
            if run is not None and run.status in {"completed", "failed", "interrupted"}:
                return run
        time.sleep(0.25)
    raise AssertionError(f"run {run_id} did not reach a terminal state in time")


def _detections(database: Database, run_id: str) -> list[DetectionResultModel]:
    with database.session_factory() as session:
        return (
            session.query(DetectionResultModel)
            .filter(DetectionResultModel.run_id == run_id)
            .all()
        )


def test_real_full_recording_local_cpu_acceptance(tmp_path):
    interpreter = _require_interpreter()
    assert _fingerprint(interpreter) == EXPECTED_GENERATION_FINGERPRINT, (
        "accepted local CPU runtime generation changed; re-run acceptance and re-certify"
    )
    detector, normalization = _require_assets()
    item = _require_spacenet_item()

    asset_paths = {
        _CPN_NAMESPACE: {
            "detector_checkpoint": str(detector),
            "ls_stft_normalization": str(normalization),
        }
    }
    settings = Settings(
        project_root=REPO_ROOT,
        data_root=tmp_path / "data",
        label_space_root=LABEL_ROOT,
        database_url=f"sqlite:///{tmp_path / 'fa2_acceptance.db'}",
        local_cpu_python_path=interpreter,
        local_cpu_runtime_ref=EXPECTED_RUNTIME_REF,
        local_inference_work_root=tmp_path / "work",
        local_asset_paths=asset_paths,
    )
    provider = build_local_providers(settings)["local_cpu"]
    descriptor = provider.runtime_descriptor().to_metadata()
    assert descriptor["executor"] == "local_cpu"
    assert descriptor["device_type"] == "cpu"
    assert descriptor["precision"] == "float32"
    assert descriptor["environment_label"] == EXPECTED_RUNTIME_REF

    database = _database(settings)
    _seed_full_cpn_run(database, run_id="fa2_cpn_full", item=item, descriptor=descriptor)
    assert provider.launch("fa2_cpn_full", coordinator_token=None) > 0

    started = time.time()
    run = _wait_terminal(database, "fa2_cpn_full", timeout_s=900.0)
    elapsed = time.time() - started

    assert run.status == "completed", (run.error_type, run.error_message)
    assert run.error_type is None
    assert run.error_message is None

    metadata = run.execution_metadata_json
    assert metadata["model_release_id"] == CPN_RELEASE
    assert metadata["asset_manifest_sha256"] == CPN_MANIFEST_SHA
    assert metadata["runtime_descriptor"]["executor"] == "local_cpu"
    assert metadata["runtime_descriptor"]["device_type"] == "cpu"
    assert metadata["runtime_descriptor"]["precision"] == "float32"

    detections = _detections(database, "fa2_cpn_full")
    names = {0: "Narrow", 1: "Mid", 2: "Wide"}
    for detection in detections:
        assert detection.class_id in names
        assert detection.class_name == names[detection.class_id]
        assert detection.scores_json == {"cpn": detection.confidence}

    # Timing/count are informational acceptance evidence.
    (tmp_path / "fa2_timing.json").write_text(
        json.dumps({"elapsed_s": elapsed, "detection_count": len(detections)}),
        encoding="utf-8",
    )
