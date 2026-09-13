"""G6 real acceptance driver: MEM-1/MEM-2, real run, fail-closed assertions."""
import json
import os
import signal
import time
from pathlib import Path

from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[1]
EVIDENCE = REPO / "data" / "g6_work" / "g6_evidence.json"
MEM_MAX_PATH = Path("/sys/fs/cgroup/memory.max")
MEM_NOW_PATH = Path("/sys/fs/cgroup/memory.current")
GIB = 1073741824
MEM2_RESERVE = 268435456  # 256 MiB
POLL_TIMEOUT_S = 3600


def read_mem(path):
    text = path.read_text().strip()
    return int(text) if text.isdigit() else (1 << 62)


mem_max = read_mem(MEM_MAX_PATH)
mem_initial = read_mem(MEM_NOW_PATH)
if mem_max - mem_initial < GIB:
    raise SystemExit(
        f"G6 ACCEPTANCE BLOCKED BY MEM-1: headroom {mem_max - mem_initial} < 1 GiB")

from app.main import create_app
from app.core.config import Settings
from app.db.session import Database
from app.analysis.model import AnalysisRunModel
from app.dataset_experiments.model import (
    DatasetExperimentAttemptModel,
    DatasetExperimentItemModel,
    DatasetExperimentModel,
)
from app.benchmarks.model import DatasetEvaluationItemModel, DatasetEvaluationModel
from app.detections.model import DetectionResultModel
from app.recordings.model import RecordingModel

settings = Settings()
database = Database(settings.database_url)


def read_proc_argv(pid):
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
        return None
    if not raw:
        return None
    return [
        part.decode("utf-8", errors="replace")
        for part in raw.split(b"\0")
        if part
    ]


def is_local_run_worker(argv, run_id):
    if not argv:
        return False
    try:
        index = argv.index("-m")
    except ValueError:
        return False
    return (
        len(argv) > index + 2
        and argv[index + 1] == "app.analysis.local_inference_worker"
        and argv[index + 2] == run_id
    )


def is_dataset_experiment_worker(argv, experiment_id, coordinator_token):
    if not argv:
        return False
    try:
        module_index = argv.index("-m")
        token_flag_index = argv.index("--coordinator-token")
    except ValueError:
        return False
    return (
        len(argv) > module_index + 2
        and argv[module_index + 1] == "app.dataset_experiments.worker"
        and argv[module_index + 2] == experiment_id
        and len(argv) > token_flag_index + 1
        and argv[token_flag_index + 1] == coordinator_token
    )


def acceptance_owned_processes(experiment_id):
    """Return (coordinator_pid_or_None, sorted inference_worker_pids)."""
    coordinator = None
    inference_workers = []
    with database.session_factory() as session:
        experiment = session.get(DatasetExperimentModel, experiment_id)
        if experiment is None:
            return coordinator, inference_workers
        if (
            experiment.status in {"running", "evaluating"}
            and experiment.worker_pid is not None
            and experiment.coordinator_token is not None
        ):
            pid = int(experiment.worker_pid)
            if is_dataset_experiment_worker(
                read_proc_argv(pid), experiment.id, experiment.coordinator_token
            ):
                coordinator = pid
        items = session.query(DatasetExperimentItemModel).filter_by(
            experiment_id=experiment_id).all()
        for item in items:
            attempts = session.query(DatasetExperimentAttemptModel).filter_by(
                experiment_item_id=item.id).all()
            for attempt in attempts:
                run = session.get(AnalysisRunModel, attempt.analysis_run_id)
                if (
                    run is not None
                    and run.status in {"pending", "running"}
                    and run.worker_pid is not None
                ):
                    pid = int(run.worker_pid)
                    if is_local_run_worker(read_proc_argv(pid), run.id):
                        inference_workers.append(pid)
    return coordinator, sorted(set(inference_workers))


def safe_sigterm(pid):
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass


def cleanup_acceptance_processes(experiment_id):
    """SIGTERM only currently active, exact-identity G6 processes."""
    coordinator, inference_workers = acceptance_owned_processes(experiment_id)
    if coordinator is not None:
        safe_sigterm(coordinator)
    for pid in inference_workers:
        if pid != coordinator:
            safe_sigterm(pid)


# pre-run snapshot (run-isolation evidence)
with database.session_factory() as session:
    recording_ids = [recording.id for recording in session.query(RecordingModel).all()]
    pre_run_ids = {
        run.id for run in session.query(AnalysisRunModel)
        .filter(AnalysisRunModel.recording_id.in_(recording_ids)).all()
    }

app = create_app(settings)
client = TestClient(app)
payload = {
    "name": "g6-real-acceptance",
    "dataset_name": "SpaceNet", "dataset_split": "test",
    "dataset_label_space": "spacenet_14",
    "plugin_id": "cpn_bandwidth_tier", "plugin_version": "1.0.0",
    "model_release_id": "golden", "executor": "local_cpu",
    "parameters": {}, "evaluation_protocol": "physical_tf_detection_ap_v2",
    "max_concurrency": 1,
}
created = client.post("/api/dataset-experiments", json=payload)
if created.status_code != 201:
    raise SystemExit(f"STOP RUN-1: create -> {created.status_code} {created.text}")
experiment_id = created.json()["id"]
run_response = client.post(f"/api/dataset-experiments/{experiment_id}/run")
if run_response.status_code != 202:
    raise SystemExit(f"STOP RUN-1: run -> {run_response.status_code} {run_response.text}")

deadline = time.time() + POLL_TIMEOUT_S
status = None
mem_peak = mem_initial
mem2_triggered = False
next_mem_sample = time.time()
while time.time() < deadline:
    with database.session_factory() as session:
        experiment = session.get(DatasetExperimentModel, experiment_id)
        status = experiment.status if experiment is not None else None
    now = time.time()
    if now >= next_mem_sample:
        current = read_mem(MEM_NOW_PATH)
        mem_peak = max(mem_peak, current)
        if current > mem_max - MEM2_RESERVE:
            mem2_triggered = True
            break
        next_mem_sample = now + 5
    if status in {"completed", "failed", "completed_with_failures"}:
        break
    time.sleep(1)

mem_final = read_mem(MEM_NOW_PATH)
mem_peak = max(mem_peak, mem_final)

if mem2_triggered:
    cleanup_acceptance_processes(experiment_id)
    raise SystemExit(
        f"G6 ACCEPTANCE BLOCKED BY MEM-2: peak {mem_peak} > {mem_max - MEM2_RESERVE}")

if status not in {"completed", "failed", "completed_with_failures"}:
    cleanup_acceptance_processes(experiment_id)
    raise SystemExit("G6 ACCEPTANCE BLOCKED BY RUN-1: timeout")

# collect evidence
evidence = {
    "base_sha": "3d9f47b3e9ccbdd4147cf858712202396c256d8d",
    "experiment_id": experiment_id,
    "pre_run_ids": sorted(pre_run_ids),
    "final_experiment_status": status,
    "memory": {
        "max": mem_max, "initial": mem_initial, "peak": mem_peak,
        "final": mem_final, "mem1_ok": True, "mem2_triggered": False,
    },
}
with database.session_factory() as session:
    experiment = session.get(DatasetExperimentModel, experiment_id)
    evidence["dataset_evaluation_id"] = experiment.dataset_evaluation_id
    items = list(
        session.query(DatasetExperimentItemModel)
        .filter_by(experiment_id=experiment_id)
        .order_by(DatasetExperimentItemModel.manifest_order)
        .all()
    )
    evidence["items"] = []
    runs = []
    for item in items:
        attempts = list(
            session.query(DatasetExperimentAttemptModel)
            .filter_by(experiment_item_id=item.id)
            .order_by(DatasetExperimentAttemptModel.attempt_number)
            .all()
        )
        evidence["items"].append({
            "item_id": item.id,
            "manifest_order": item.manifest_order,
            "status": item.status,
            "attempts": [
                {"attempt_id": a.id, "attempt_number": a.attempt_number,
                 "analysis_run_id": a.analysis_run_id,
                 "launch_requested_at": str(a.launch_requested_at)}
                for a in attempts
            ],
        })
        for attempt in attempts:
            run = session.get(AnalysisRunModel, attempt.analysis_run_id)
            runs.append({
                "run_id": run.id,
                "status": run.status,
                "executor": run.executor,
                "worker_pid": run.worker_pid,
                "detections": session.query(DetectionResultModel)
                .filter_by(run_id=run.id).count(),
            })
    evidence["runs"] = runs
    if experiment.dataset_evaluation_id:
        evaluation = session.get(DatasetEvaluationModel, experiment.dataset_evaluation_id)
        aggregate = evaluation.aggregate_metrics_json or {}
        evidence["evaluation"] = {
            "id": evaluation.id,
            "status": evaluation.status,
            "expected_recordings": evaluation.expected_recordings,
            "evaluated_recordings": evaluation.evaluated_recordings,
            "missing_recordings": evaluation.missing_recordings,
            "coverage": evaluation.coverage,
            "included_items": session.query(DatasetEvaluationItemModel)
            .filter_by(evaluation_id=evaluation.id, status="included").count(),
            "classification_applicable": aggregate.get("classification_applicable"),
            "classification_reason": aggregate.get("classification_reason"),
            "classification_on_matched": aggregate.get("classification_on_matched"),
            "class_aware": aggregate.get("class_aware"),
            "localization": aggregate.get("localization"),
            "per_class_metrics_json": evaluation.per_class_metrics_json,
        }

# ALL acceptance assertions BEFORE writing success evidence.
assert status == "completed", f"experiment terminal status {status}"
assert len(evidence["items"]) == 3, evidence["items"]
assert sum(len(i["attempts"]) for i in evidence["items"]) == 3
assert len(evidence["runs"]) == 3, evidence["runs"]
assert all(r["status"] == "completed" and r["executor"] == "local_cpu"
           for r in evidence["runs"]), evidence["runs"]
assert all(r["run_id"] not in pre_run_ids for r in evidence["runs"]), "historical run reuse"
assert len({r["run_id"] for r in evidence["runs"]}) == 3
assert evidence["dataset_evaluation_id"] is not None
assert evidence["evaluation"]["status"] == "completed"
assert evidence["evaluation"]["expected_recordings"] == 3
assert evidence["evaluation"]["evaluated_recordings"] == 3
assert evidence["evaluation"]["coverage"] == 1.0
assert evidence["evaluation"]["missing_recordings"] == 0
assert evidence["evaluation"]["included_items"] == 3
assert evidence["evaluation"]["localization"] is not None
assert isinstance(evidence["evaluation"]["localization"]["ap50"], (int, float))
assert isinstance(evidence["evaluation"]["localization"]["ap50_95"], (int, float))
assert evidence["evaluation"]["classification_applicable"] is False
assert evidence["evaluation"]["classification_reason"] == "label_space_mismatch"
assert evidence["evaluation"]["classification_on_matched"] is None
assert evidence["evaluation"]["class_aware"] is None
assert evidence["evaluation"]["per_class_metrics_json"] == []

EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
EVIDENCE.write_text(json.dumps(evidence, indent=2, default=str))
print(json.dumps(evidence, indent=2, default=str))
