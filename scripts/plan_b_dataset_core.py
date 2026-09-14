"""Plan B H2/H3 shared dataset core (acceptance-only).

Registers the frozen 16-stem SpaceNet subset into a dedicated Plan-B DB via the
existing external-path semantics (no IQ copy), drives DatasetExperiments through
the real production seams, enforces the approved acceptance checks, and provides
the exact GPU-quiescence rule.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import plan_b_common as common

GPU_QUIESCENT_MARGIN_MIB = 64
GPU_QUIESCENT_TIMEOUT_S = 60


def _bootstrap(root: Path) -> None:
    common.bootstrap_env_before_app_import(root)


def build_app(root: Path):
    _bootstrap(root)
    from app.main import create_app
    from app.core.config import Settings

    settings = Settings()
    return create_app(settings), settings


def register_subset(app) -> list[str]:
    """Register exactly the frozen 16 stems idempotently; fail closed otherwise."""
    from sqlalchemy import func

    from app.datasets.spacenet import SpaceNetAdapter
    from app.ground_truth.model import GroundTruthModel
    from app.recordings.model import RecordingModel

    adapter = SpaceNetAdapter(common.SPACENET_ROOT, common.REPO / "label_spaces", "spacenet_14")
    samples = {}
    for stem in common.H2_STEMS:
        sample = adapter.load("test", stem)
        if sample.num_samples <= 0 or not sample.signals:
            raise SystemExit(f"H2 STOP: stem {stem} has zero IQ/GT")
        samples[stem] = sample

    with app.state.database.session_factory() as session:
        existing = {
            row.name
            for row in session.query(RecordingModel)
            .filter_by(dataset_name=common.DATASET_NAME, dataset_split=common.DATASET_SPLIT,
                       label_space=common.DATASET_LABEL_SPACE)
            .all()
        }
        for stem in common.H2_STEMS:
            if stem in existing:
                continue
            sample = samples[stem]
            recording_id = f"rec_planb_{uuid4().hex}"
            session.add(RecordingModel(
                id=recording_id, name=sample.id, data_path=sample.data_path.name,
                external_path=str(sample.data_path), data_format=sample.data_format,
                source="spacenet", sample_rate_hz=sample.sample_rate_hz,
                center_frequency_hz=sample.center_frequency_hz,
                frequency_low_hz=sample.frequency_low_hz,
                frequency_high_hz=sample.frequency_high_hz,
                num_samples=sample.num_samples, duration_s=sample.duration_s,
                dataset_name=common.DATASET_NAME, dataset_split=common.DATASET_SPLIT,
                label_space=common.DATASET_LABEL_SPACE, has_ground_truth=True,
            ))
            for signal in sample.signals:
                session.add(GroundTruthModel(
                    id=f"gt_{uuid4().hex}", recording_id=recording_id,
                    t_start_s=signal.t_start_s, t_end_s=signal.t_end_s,
                    f_low_hz=signal.f_low_hz, f_high_hz=signal.f_high_hz,
                    class_id=signal.class_id, class_name=signal.class_name,
                ))
        session.commit()

    with app.state.database.session_factory() as session:
        rows = (session.query(RecordingModel)
                .filter_by(dataset_name=common.DATASET_NAME, dataset_split=common.DATASET_SPLIT,
                           label_space=common.DATASET_LABEL_SPACE).all())
        names = sorted(r.name for r in rows)
        if names != sorted(common.H2_STEMS):
            raise SystemExit(f"H2 STOP: registered names mismatch: {names}")
        return [r.id for r in rows]


def manifest_preview(app) -> dict:
    from app.benchmarks.service import DatasetBenchmarkService

    with app.state.database.session_factory() as session:
        preview = DatasetBenchmarkService(session).prepare_manifest(
            common.DATASET_NAME, common.DATASET_SPLIT, common.DATASET_LABEL_SPACE
        )
        return {
            "recording_manifest_hash": preview.recording_manifest_hash,
            "expected_recordings": preview.expected_recordings,
            "manifest_order": [(e.manifest_order, e.recording_name) for e in preview.entries],
        }


def create_experiment(app, *, plugin_id: str, name: str, concurrency: int = 1) -> str:
    from fastapi.testclient import TestClient

    client = TestClient(app)
    payload = {
        "name": name,
        "dataset_name": common.DATASET_NAME,
        "dataset_split": common.DATASET_SPLIT,
        "dataset_label_space": common.DATASET_LABEL_SPACE,
        "plugin_id": plugin_id,
        "plugin_version": common.PLUGIN_VERSION[plugin_id],
        "model_release_id": common.MODEL_RELEASE,
        "executor": "local_gpu",
        "parameters": {},
        "max_concurrency": concurrency,
    }
    created = client.post("/api/dataset-experiments", json=payload)
    if created.status_code != 201:
        raise SystemExit(f"H2/H3 STOP: create -> {created.status_code} {created.text}")
    return created.json()["id"]


def run_experiment(app, experiment_id: str, *, deadline_s: int = 3600) -> dict:
    from fastapi.testclient import TestClient

    client = TestClient(app)
    started = client.post(f"/api/dataset-experiments/{experiment_id}/run")
    if started.status_code not in (200, 202):
        raise SystemExit(f"H2/H3 STOP: run -> {started.status_code} {started.text}")
    deadline = time.time() + deadline_s
    while time.time() < deadline:
        body = client.get(f"/api/dataset-experiments/{experiment_id}").json()
        if body["status"] in ("completed", "completed_with_failures", "failed"):
            return body
        time.sleep(1.0)
    raise SystemExit(f"H2/H3 STOP: experiment {experiment_id} did not reach terminal state")


def fetch_items(app, experiment_id: str) -> list[dict]:
    from fastapi.testclient import TestClient

    client = TestClient(app)
    return client.get(f"/api/dataset-experiments/{experiment_id}/items").json()


def fetch_attempts(app, experiment_id: str, item_id: str) -> list[dict]:
    from fastapi.testclient import TestClient

    client = TestClient(app)
    return client.get(f"/api/dataset-experiments/{experiment_id}/items/{item_id}/attempts").json()


def fetch_evaluation(app, evaluation_id: str) -> dict:
    from fastapi.testclient import TestClient

    client = TestClient(app)
    return client.get(f"/api/dataset-benchmarks/{evaluation_id}").json()


def gpu_memory_used_mib() -> int:
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=30,
    ).stdout.strip().splitlines()
    return int(out[0])


def compute_apps() -> list[tuple[int, int]]:
    out = subprocess.run(
        ["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=30,
    ).stdout.strip()
    apps = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 2 and parts[0].isdigit():
            apps.append((int(parts[0]), int(parts[1])))
    return apps


def assert_gpu_quiescent(baseline_mib: int, *, label: str) -> dict:
    apps = compute_apps()
    if apps:
        raise SystemExit(f"{label}: GPU_MEMORY_NOT_QUIESCENT compute apps present: {apps}")
    deadline = time.time() + GPU_QUIESCENT_TIMEOUT_S
    used = gpu_memory_used_mib()
    while time.time() < deadline:
        used = gpu_memory_used_mib()
        if used <= baseline_mib + GPU_QUIESCENT_MARGIN_MIB:
            return {"baseline_mib": baseline_mib, "final_mib": used, "quiescent": True}
        time.sleep(2.0)
    raise SystemExit(
        f"{label}: GPU_MEMORY_NOT_QUIESCENT used={used} > baseline+{GPU_QUIESCENT_MARGIN_MIB}"
    )


def worker_pids() -> list[int]:
    pids = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmd = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        if "app.analysis.local_inference_worker" in cmd:
            pids.append(int(entry.name))
    return pids


def assert_no_orphans(label: str) -> None:
    pids = worker_pids()
    if pids:
        raise SystemExit(f"{label}: orphan qualification worker PIDs: {pids}")


def write_evidence(name: str, payload: dict) -> Path:
    common.EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    path = common.EVIDENCE_DIR / name
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return path
