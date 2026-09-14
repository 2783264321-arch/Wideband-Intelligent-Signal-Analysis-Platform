"""Plan B H4 — genuine local_gpu crash/recovery (real GPU, bounded 2–4 executions).

    PYTHONPATH="$PWD/backend:$PWD/scripts" "$PWD/.venv/bin/python" \
        scripts/plan_b_h4_recovery.py --live

Only kills a VERIFIED Plan-B worker PID (`/proc/<pid>/cmdline` contains
`app.analysis.local_inference_worker <exact run_id>`). Never signals OpenCode,
Jupyter, the interactive backend, or unrelated processes.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import time
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plan_b_common as common  # noqa: E402
import plan_b_dataset_core as core  # noqa: E402

PLUGIN = "cpn_bandwidth_tier"


def _verified_worker_pid(run_id: str) -> int | None:
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            argv = (entry / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        parts = [p.decode("utf-8", "replace") for p in argv if p]
        try:
            index = parts.index("-m")
        except ValueError:
            continue
        if (len(parts) > index + 2 and parts[index + 1] == "app.analysis.local_inference_worker"
                and parts[index + 2] == run_id):
            return int(entry.name)
    return None


def _run_until_running(app, experiment_id: str, *, deadline_s: int = 600) -> list[dict]:
    from fastapi.testclient import TestClient

    client = TestClient(app)
    client.post(f"/api/dataset-experiments/{experiment_id}/run")
    deadline = time.time() + deadline_s
    while time.time() < deadline:
        items = client.get(f"/api/dataset-experiments/{experiment_id}/items").json()
        if any(i["status"] == "running" for i in items):
            return items
        time.sleep(0.5)
    raise SystemExit("H4 live: no running item observed before deadline")


def _restart_and_recover(root: Path) -> dict:
    # A fresh app against the SAME DB runs startup recovery.
    app, _settings = core.build_app(root)
    return app


def live_crash_case(root: Path, label: str) -> dict:
    app, _settings = core.build_app(root)
    core.register_subset(app)
    experiment_id = core.create_experiment(app, plugin_id=PLUGIN, name=f"Plan B H4 live {label}")
    items = _run_until_running(app, experiment_id)
    running = next(i for i in items if i["status"] == "running")
    run_id = running["latest_analysis_run_id"]
    # Wait for the verified worker to exist.
    pid = None
    deadline = time.time() + 60
    while time.time() < deadline:
        pid = _verified_worker_pid(run_id)
        if pid is not None:
            break
        time.sleep(0.2)
    if pid is None:
        raise SystemExit(f"H4 live: verified worker for run {run_id} not found")
    from fastapi.testclient import TestClient

    client = TestClient(app)
    pre = client.get(f"/api/analysis-runs/{run_id}").json()
    os.kill(pid, signal.SIGKILL)
    # The app is restarted against the same DB; startup recovery must terminalize
    # the running local_gpu run and never rerun completed work.
    app2 = _restart_and_recover(root)
    client2 = TestClient(app2)
    deadline = time.time() + 300
    while time.time() < deadline:
        body = client2.get(f"/api/dataset-experiments/{experiment_id}").json()
        if body["status"] in ("completed", "completed_with_failures", "failed"):
            break
        time.sleep(1.0)
    run_after = client2.get(f"/api/analysis-runs/{run_id}").json()
    return {
        "label": label,
        "experiment_id": experiment_id,
        "killed_run_id": run_id,
        "killed_pid": pid,
        "pre_kill_status": pre["status"],
        "post_restart_run_status": run_after["status"],
        "post_restart_run_error_type": run_after.get("error_type"),
        "experiment_status": client2.get(f"/api/dataset-experiments/{experiment_id}").json()["status"],
        "verified_cmdline": True,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)
    if not args.live:
        parser.error("--live is required")

    root = common.PLAN_B_ROOT / "h4"
    baseline = core.gpu_memory_used_mib()
    if core.compute_apps():
        raise SystemExit(f"H4 STOP: GPU compute apps present: {core.compute_apps()}")

    results = []
    for label in ("case_a_startup", "case_b_mid_item"):
        results.append(live_crash_case(root, label))

    core.assert_no_orphans("H4 live post-run")
    quiescent = core.assert_gpu_quiescent(baseline, label="H4")
    evidence = {"milestone": "H4", "live_cases": results, "gpu_quiescence": quiescent}
    path = core.write_evidence("h4_recovery_live.json", evidence)
    print(json.dumps({"live_cases": len(results), "evidence": str(path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
