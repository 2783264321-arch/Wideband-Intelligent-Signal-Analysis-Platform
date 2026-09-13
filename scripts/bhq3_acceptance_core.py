"""BHQ-3 acceptance core — real local_gpu worker acceptance (acceptance-only).

Drives the REAL production seams:
  create_app(settings)
    → real ExecutorRegistry (production store, or an in-memory temp-cert store)
    → POST /api/analysis-runs (executor=local_gpu)
    → LocalInferenceWorkerProvider.launch
    → /root/miniconda3/bin/python -m app.analysis.local_inference_worker
    → PluginRegistry → golden ModelRelease + verified assets → CUDA
    → AnalysisResultWriter → persisted DetectionResult → completed

Never writes a temporary certificate to production.
"""
from __future__ import annotations

import json
import os
import signal
import time
from pathlib import Path

from bhq3_common import (
    ASSET_PATHS,
    CERT_PATH,
    GIB,
    GPU_RUNTIME_REF,
    LABELS,
    MANIFEST_PATH,
    ML_PYTHON,
    MemoryMonitor,
    PLUGIN_VERSION,
    REPO,
    SPACENET_ROOT,
    WORK_ROOT,
    derive_memory,
    ensure_work_root,
    gpu_compute_apps,
    proc_peak_rss_kb,
    read_memory_snapshot,
    require_memory_admission,
    write_json,
)


def _worker_cmdline(pid: int):
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None
    parts = [p.decode("utf-8", errors="replace") for p in raw.split(b"\0") if p]
    return parts or None


def _terminate_verified_worker(pid: int, run_id: str) -> bool:
    """SIGTERM the exact local inference worker only when identity is proven."""
    argv = _worker_cmdline(pid)
    if not argv:
        return False
    try:
        index = argv.index("-m")
    except ValueError:
        return False
    if not (len(argv) > index + 2
            and argv[index + 1] == "app.analysis.local_inference_worker"
            and argv[index + 2] == run_id):
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _bootstrap_env_before_app_import() -> None:
    """Keep the module-level app.main app off any production/platform DB."""
    ensure_work_root()
    os.environ.setdefault("WSP_DATABASE_URL", f"sqlite:///{WORK_ROOT / 'app_main_bootstrap.db'}")
    os.environ.setdefault("WSP_DATA_ROOT", str(WORK_ROOT / "app_main_bootstrap_data"))
    os.environ.setdefault("WSP_LABEL_SPACE_ROOT", str(LABELS))


def _local_asset_paths(plugin_id: str) -> dict:
    from app.remote_execution.assets import load_pipeline_asset_manifest

    manifest = load_pipeline_asset_manifest(MANIFEST_PATH[plugin_id])
    namespace = f"{plugin_id}/{PLUGIN_VERSION[plugin_id]}/{manifest.asset_manifest_sha256}"
    return {namespace: {name: str(ASSET_PATHS[name]) for name in manifest.assets}}


def _settings(plugin_id: str, stem: str, mode: str):
    from app.core.config import Settings

    case_root = WORK_ROOT / f"{mode}_{plugin_id}_stem{stem}"
    data_root = case_root / "data"
    work_root = case_root / "work"
    data_root.mkdir(parents=True, exist_ok=True)
    work_root.mkdir(parents=True, exist_ok=True)
    return Settings(
        project_root=REPO,
        data_root=data_root,
        label_space_root=LABELS,
        database_url=f"sqlite:///{case_root / 'acceptance.db'}",
        local_gpu_python_path=ML_PYTHON,
        local_gpu_runtime_ref=GPU_RUNTIME_REF,
        local_inference_work_root=work_root,
        local_asset_paths=_local_asset_paths(plugin_id),
    )


def _seed_recording(app, stem: str) -> str:
    from app.datasets.spacenet import SpaceNetAdapter
    from app.ground_truth.model import GroundTruthModel
    from app.recordings.model import RecordingModel
    from uuid import uuid4

    adapter = SpaceNetAdapter(SPACENET_ROOT, LABELS, label_space_id="spacenet_14")
    sample = adapter.load("test", stem)
    recording_id = f"rec_bhq3_{uuid4().hex}"
    with app.state.database.session_factory() as session:
        session.add(
            RecordingModel(
                id=recording_id,
                name=sample.id,
                data_path=sample.data_path.name,
                external_path=str(sample.data_path),
                data_format=sample.data_format,
                source="spacenet",
                sample_rate_hz=sample.sample_rate_hz,
                center_frequency_hz=sample.center_frequency_hz,
                frequency_low_hz=sample.frequency_low_hz,
                frequency_high_hz=sample.frequency_high_hz,
                num_samples=sample.num_samples,
                duration_s=sample.duration_s,
                dataset_name="SpaceNet",
                dataset_split="test",
                label_space="spacenet_14",
                has_ground_truth=bool(sample.signals),
            )
        )
        for signal in sample.signals:
            session.add(
                GroundTruthModel(
                    id=f"gt_{uuid4().hex}",
                    recording_id=recording_id,
                    t_start_s=signal.t_start_s,
                    t_end_s=signal.t_end_s,
                    f_low_hz=signal.f_low_hz,
                    f_high_hz=signal.f_high_hz,
                    class_id=signal.class_id,
                    class_name=signal.class_name,
                )
            )
        session.commit()
    return recording_id


def _temp_certificate_store(plugin_id: str):
    from app.remote_execution.runtime import (
        ExecutionCertificate,
        ExecutionCertificateStore,
        load_execution_certificates,
    )

    certs = list(load_execution_certificates(CERT_PATH))
    certs.append(
        ExecutionCertificate(
            plugin_id=plugin_id,
            plugin_version=PLUGIN_VERSION[plugin_id],
            model_release_id="golden",
            executor="local_gpu",
            device_type="cuda",
            precision="float16",
            runtime_ref=GPU_RUNTIME_REF,
            evidence_ref="m9_2_bhq3_temp_cert_acceptance",
        )
    )
    return ExecutionCertificateStore(certs)


def _detections(app, run_id: str):
    from app.detections.model import DetectionResultModel

    with app.state.database.session_factory() as session:
        return (
            session.query(DetectionResultModel)
            .filter(DetectionResultModel.run_id == run_id)
            .all()
        )


def _artifact_dir(data_root: Path, run_id: str) -> Path:
    from app.storage.service import StorageService

    return StorageService(data_root).artifact_dir(run_id)


def run_case(plugin_id: str, stem: str, mode: str, *, inject_temp_cert: bool) -> dict:
    _bootstrap_env_before_app_import()
    from fastapi.testclient import TestClient

    from app.analysis.local_executor import build_local_providers
    from app.main import create_app
    from app.remote_execution.runtime import ExecutorRegistry

    settings = _settings(plugin_id, stem, mode)
    app = create_app(settings)
    recording_id = _seed_recording(app, stem)

    if inject_temp_cert:
        providers = dict(build_local_providers(settings))
        assert "local_gpu" in providers, "local_gpu provider not registered"
        app.state.executor_registry = ExecutorRegistry(
            providers, _temp_certificate_store(plugin_id)
        )

    # Amendment A1: two-path admission (Path A raw OR guarded Path B) + live monitor.
    admission, baseline = require_memory_admission()
    monitor = MemoryMonitor(baseline)
    event_baseline = dict(baseline.get("events") or {})
    baseline_derived = derive_memory(baseline)
    baseline_pressure = baseline.get("pressure") or {}
    psi_full_peak = float(baseline_pressure.get("full_avg10", 0.0) or 0.0)
    psi_some_peak = float(baseline_pressure.get("some_avg10", 0.0) or 0.0)
    max_committed_floor = baseline_derived["committed_floor"]
    min_effective_headroom = baseline_derived["effective_headroom"]
    max_anon = int((baseline.get("stat") or {}).get("anon", 0) or 0)
    mem_samples = 0
    abort_reason = None

    client = TestClient(app)
    payload = {
        "recording_id": recording_id,
        "pipeline_id": plugin_id,
        "executor": "local_gpu",
        "model_release_id": "golden",
        "parameters": {},
    }
    out = WORK_ROOT / f"bhq3_{mode}_evidence_{plugin_id}_stem{stem}.json"
    t0 = time.perf_counter()
    created = client.post("/api/analysis-runs", json=payload)
    if created.status_code != 201:
        raise SystemExit(
            f"BHQ_3_BLOCKED_BY_REAL_LOCAL_GPU_ACCEPTANCE_FAILED: create -> "
            f"{created.status_code} {created.text}"
        )
    run_id = created.json()["id"]

    worker_pid = created.json().get("worker_pid")
    max_rss_kb = None
    max_gpu_mib = None
    deadline = time.time() + 900
    status = created.json().get("status")
    while time.time() < deadline:
        polled = client.get(f"/api/analysis-runs/{run_id}")
        assert polled.status_code == 200
        body = polled.json()
        status = body.get("status")
        worker_pid = body.get("worker_pid") or worker_pid

        # Live cgroup monitor at the existing ~0.2 s cadence.
        snapshot = read_memory_snapshot()
        mem_samples += 1
        try:
            derived = derive_memory(snapshot)
            max_committed_floor = max(max_committed_floor, derived["committed_floor"])
            min_effective_headroom = min(min_effective_headroom, derived["effective_headroom"])
        except Exception:  # noqa: BLE001 - monitor check reports malformed snapshots
            derived = None
        max_anon = max(max_anon, int((snapshot.get("stat") or {}).get("anon", 0) or 0))
        pressure = snapshot.get("pressure") or {}
        psi_full_peak = max(psi_full_peak, float(pressure.get("full_avg10", 0.0) or 0.0))
        psi_some_peak = max(psi_some_peak, float(pressure.get("some_avg10", 0.0) or 0.0))

        reason = monitor.check(snapshot)
        if reason:
            abort_reason = reason
            if worker_pid:
                if not _terminate_verified_worker(int(worker_pid), run_id):
                    abort_reason += " (worker PID identity unverified; not signalled)"
            break

        if worker_pid:
            rss = proc_peak_rss_kb(int(worker_pid))
            if rss is not None:
                max_rss_kb = rss if max_rss_kb is None else max(max_rss_kb, rss)
            for pid, mib in gpu_compute_apps():
                if pid == int(worker_pid):
                    max_gpu_mib = mib if max_gpu_mib is None else max(max_gpu_mib, mib)
        if status in {"completed", "failed", "interrupted"}:
            break
        time.sleep(0.2)
    wall = time.perf_counter() - t0

    final_snapshot = read_memory_snapshot()
    final_events = dict(final_snapshot.get("events") or {})
    event_deltas = {
        key: int(final_events.get(key, 0) or 0) - int(event_baseline.get(key, 0) or 0)
        for key in ("max", "oom", "oom_kill", "high")
    }
    memory_gate_evidence = {
        "mode": admission["mode"],
        "reasons": admission["reasons"],
        "pre_launch_derived": baseline_derived,
        "pre_launch_snapshot": baseline,
        "thresholds": admission["thresholds"],
        "event_baseline": event_baseline,
        "final_events": final_events,
        "event_deltas": event_deltas,
        "samples": mem_samples,
        "max_committed_floor": max_committed_floor,
        "min_effective_headroom": min_effective_headroom,
        "max_anon": max_anon,
        "psi_full_peak_avg10": psi_full_peak,
        "psi_some_peak_avg10": psi_some_peak,
        "abort_reason": abort_reason,
    }

    if abort_reason is not None:
        write_json(out, {
            "mode": mode, "plugin_id": plugin_id, "stem": stem, "run_id": run_id,
            "status": status, "memory_gate": memory_gate_evidence,
        })
        raise SystemExit(f"BHQ_3_BLOCKED_BY_CGROUP_MEMORY: {abort_reason} (run {run_id})")

    if status != "completed":
        raise SystemExit(
            f"BHQ_3_BLOCKED_BY_REAL_LOCAL_GPU_ACCEPTANCE_FAILED: run {run_id} status={status}"
        )

    for key in ("max", "oom", "oom_kill"):
        if event_deltas.get(key, 0) > 0:
            raise SystemExit(
                f"BHQ_3_BLOCKED_BY_CGROUP_MEMORY: post-case {key} delta "
                f"{event_deltas[key]} > 0 (run {run_id})"
            )

    detections = _detections(app, run_id)
    histogram: dict[str, int] = {}
    for detection in detections:
        key = f"{detection.class_id}:{detection.class_name}"
        histogram[key] = histogram.get(key, 0) + 1

    workspace = _artifact_dir(settings.data_root, run_id)
    run_metadata = {}
    metadata_path = workspace / "run_metadata.json"
    if metadata_path.is_file():
        run_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    body = client.get(f"/api/analysis-runs/{run_id}").json()
    evidence = {
        "mode": mode,
        "plugin_id": plugin_id,
        "plugin_version": PLUGIN_VERSION[plugin_id],
        "stem": stem,
        "run_id": run_id,
        "recording_id": recording_id,
        "status": status,
        "executor": body.get("executor"),
        "worker_pid": worker_pid,
        "wall_time_s": wall,
        "memory_gate": memory_gate_evidence,
        "peak_worker_rss_kb": max_rss_kb if max_rss_kb is not None else "unavailable",
        "peak_gpu_process_mib": max_gpu_mib if max_gpu_mib is not None else "unavailable",
        "detection_count": len(detections),
        "class_histogram": histogram,
        "run_metadata": run_metadata,
        "artifact_workspace": str(workspace),
    }
    write_json(out, evidence)
    print(json.dumps({k: evidence[k] for k in (
        "mode", "plugin_id", "stem", "run_id", "status", "executor",
        "detection_count", "class_histogram", "run_metadata",
        "peak_worker_rss_kb", "peak_gpu_process_mib", "wall_time_s",
    )}, indent=2))
    return evidence


def projection_for(plugin_id: str) -> dict:
    """Read-model projection + independently recomputed deployment-qualified set."""
    _bootstrap_env_before_app_import()
    from fastapi.testclient import TestClient

    from app.main import create_app

    settings = _settings(plugin_id, "2", "projection")
    app = create_app(settings)
    client = TestClient(app)
    pipelines = client.get("/api/pipelines").json()
    entry = next(p for p in pipelines if p["id"] == plugin_id)

    registry = app.state.executor_registry
    definition = app.state.pipeline_registry.get(plugin_id).definition
    resolved = app.state.model_release_store.resolve(
        plugin_id, PLUGIN_VERSION[plugin_id], "golden"
    )
    supported, recommended = registry.deployment_qualified_executors(
        definition, resolved.release.model_release_id
    )
    return {
        "plugin_id": plugin_id,
        "read_model_executors_supported": entry["executors_supported"],
        "read_model_recommended_executor": entry["recommended_executor"],
        "recomputed_supported": supported,
        "recomputed_recommended": recommended,
    }
