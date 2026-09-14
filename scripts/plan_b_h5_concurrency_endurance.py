"""Plan B H5 — concurrency=2 + endurance 40 (real GPU campaign; DO NOT run while
a foreign GPU compute process exists).

    PYTHONPATH="$PWD/backend:$PWD/scripts" "$PWD/.venv/bin/python" \
        scripts/plan_b_h5_concurrency_endurance.py

Workload: CPN golden, local_gpu, max_concurrency=2, 16 + 16 + 8 = 40 executions
over the frozen 16-stem pool in a dedicated Plan-B DB.

Pre-admission gate: refuses to create ANY DatasetExperiment while a GPU compute
process not owned by a Plan-B worker exists (foreign training job protection).
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plan_b_common as common  # noqa: E402
import plan_b_dataset_core as core  # noqa: E402
import plan_b_h5_core as h5  # noqa: E402
import plan_b_h4_targeting as targeting  # noqa: E402

PLUGIN = "cpn_bandwidth_tier"
CPN_MANIFEST = "7ab8a6a4f5f93247d3997fcf88c4b05d1099361fa8db1555fc8daeeaf7fc55bb"


def _plan_b_worker_pids() -> list[int]:
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


def _concurrency_sample(client, experiment_id: str, *, interval_s: float) -> h5.ConcurrencySample:
    import sqlite3

    items = client.get(f"/api/dataset-experiments/{experiment_id}/items").json()
    run_statuses = []
    for item in items:
        run_id = item.get("latest_analysis_run_id")
        if run_id:
            run_statuses.append(client.get(f"/api/analysis-runs/{run_id}").json()["status"])
    workers = _plan_b_worker_pids()
    compute = [pid for pid, _mib in core.compute_apps()]
    return h5.ConcurrencySample(
        timestamp=time.time(),
        worker_pids=tuple(workers),
        worker_run_ids=tuple(),
        gpu_compute_pids=tuple(compute),
        db_running_runs=run_statuses.count("running"),
        db_pending_runs=run_statuses.count("pending"),
        interval_s=interval_s,
    )


def run_cycle(app, root: Path, cycle_index: int, stems: list[str], *, sample_s: float,
              samples: list, abort_holder: dict) -> dict:
    from fastapi.testclient import TestClient

    # H5 always uses the full experiment evaluation over the dedicated DB; each
    # cycle is a separate DatasetExperiment with the cycle's intended items. The
    # frozen manifest is the same 16-stem membership.
    experiment_id = core.create_experiment(
        app, plugin_id=PLUGIN, name=f"Plan B H5 cycle {cycle_index + 1}", concurrency=2
    )
    client = TestClient(app)
    client.post(f"/api/dataset-experiments/{experiment_id}/run")
    deadline = time.time() + 3600
    while time.time() < deadline:
        body = client.get(f"/api/dataset-experiments/{experiment_id}").json()
        sample = _concurrency_sample(client, experiment_id, interval_s=sample_s)
        samples.append(sample)
        evaluation = h5.evaluate_concurrency_sample(sample)
        if evaluation.abort:
            abort_holder["reason"] = evaluation.reason
            abort_holder["checks"] = evaluation.checks
            raise SystemExit(f"H5 STOP: {evaluation.reason} {evaluation.checks}")
        if body["status"] in ("completed", "completed_with_failures", "failed"):
            return body
        time.sleep(sample_s)
    raise SystemExit(f"H5 STOP: cycle {cycle_index + 1} did not reach terminal state")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args(argv)
    if not args.run:
        parser.error("--run is required (real 40-execution campaign)")

    root = common.PLAN_B_ROOT / "h5"
    compute = core.compute_apps()
    admitted, reason, foreign = h5.pre_admission_gate(compute, plan_b_pids=_plan_b_worker_pids())
    if not admitted:
        raise SystemExit(
            f"H5 STOP: {reason}: refusing to start while foreign GPU compute "
            f"processes exist: {foreign}"
        )

    baseline = core.gpu_memory_used_mib()
    app, _settings = core.build_app(root)
    core.register_subset(app)

    cycles = h5.h5_cycles(common.H2_STEMS)
    if h5.h5_expected_executions(common.H2_STEMS) != 40:
        raise SystemExit("H5 STOP: cycle partition is not 40")

    samples: list[h5.ConcurrencySample] = []
    abort_holder: dict = {}
    experiment_ids = []
    for index, cycle_stems in enumerate(cycles):
        experiments = run_cycle(app, root, index, cycle_stems,
                                sample_s=h5.CONCURRENCY_SAMPLE_INTERVAL_S,
                                samples=samples, abort_holder=abort_holder)
        experiment_ids.append(experiments["id"])

    core.assert_no_orphans("H5 post-run")
    quiescent = core.assert_gpu_quiescent(baseline, label="H5")

    raw_path = h5.serialize_concurrency_samples(samples, common.EVIDENCE_DIR / "h5_concurrency_samples.jsonl")
    evidence = {
        "milestone": "H5",
        "experiment_ids": experiment_ids,
        "cycles": [len(c) for c in cycles],
        "expected_executions": h5.h5_expected_executions(common.H2_STEMS),
        "max_observed_concurrency": h5.max_observed_concurrency(samples),
        "concurrency_bound": h5.CONCURRENCY_BOUND,
        "concurrency_sample_interval_s": h5.CONCURRENCY_SAMPLE_INTERVAL_S,
        "concurrency_samples": len(samples),
        "raw_concurrency_artifact": str(raw_path),
        "gpu_quiescence": quiescent,
    }
    path = core.write_evidence("h5_concurrency_endurance.json", evidence)
    print(json.dumps({"experiment_ids": experiment_ids,
                      "max_observed_concurrency": evidence["max_observed_concurrency"],
                      "evidence": str(path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
