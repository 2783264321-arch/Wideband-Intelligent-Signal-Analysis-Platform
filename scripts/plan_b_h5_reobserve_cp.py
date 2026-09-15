"""Plan B H5 — bounded monitoring re-observation (operator-authorized NC-02).

    PYTHONPATH="$PWD/backend:$PWD/scripts" "$PWD/.venv/bin/python" \
        scripts/plan_b_h5_reobserve_cp.py

Replaces ONLY the lost raw concurrency/resource monitoring evidence from the
original H5 campaign. It runs exactly ONE DatasetExperiment (CPN golden,
local_gpu, concurrency=2 (h5.CONCURRENCY_BOUND), exactly the frozen H5_FULL 16-stem
membership),
reusing the final corrected Plan-B monitors withOUT altering them.

Authorization boundary (operator ruling NC-01/NC-02):
  * exactly 16 new real model executions maximum (16 runs/attempts/items);
  * NO second campaign; on failure, report `BLOCKED` and stop — never retry;
  * final actual ceiling = 152.

Artifacts (unique; never overwritten; fail closed if they already exist):
  /root/autodl-tmp/plan_b_qual/h5_reobserve/h5_reobserve_concurrency_samples.jsonl
  /root/autodl-tmp/plan_b_qual/h5_reobserve/h5_reobserve_resource_samples.jsonl
  /root/autodl-tmp/plan_b_qual/h5_reobserve/h5_reobserve_acceptance.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plan_b_common as common  # noqa: E402
import plan_b_dataset_core as core  # noqa: E402
import plan_b_h5_core as h5  # noqa: E402
import plan_b_h5_membership as membership  # noqa: E402
import plan_b_h5_monitor as monitor  # noqa: E402

REOBSERVE_ROOT = common.PLAN_B_ROOT / "h5_reobserve"
CONCURRENCY_ARTIFACT = REOBSERVE_ROOT / "h5_reobserve_concurrency_samples.jsonl"
RESOURCE_ARTIFACT = REOBSERVE_ROOT / "h5_reobserve_resource_samples.jsonl"
ACCEPTANCE_ARTIFACT = REOBSERVE_ROOT / "h5_reobserve_acceptance.json"
PLUGIN = "cpn_bandwidth_tier"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pre_gate(root: Path) -> dict:
    """GPU setup verification + historical/fresh-state checks (no inference)."""
    historical_roots = [common.PLAN_B_ROOT / name for name in ("h2h3", "h4", "h5")]
    for path in historical_roots:
        if not path.is_dir():
            raise SystemExit(f"REMEDIATION STOP: historical evidence root missing: {path}")
    for artifact in (CONCURRENCY_ARTIFACT, RESOURCE_ARTIFACT, ACCEPTANCE_ARTIFACT):
        if artifact.exists():
            raise SystemExit(
                f"REMEDIATION STOP: evidence artifact already exists: {artifact} "
                "(never overwrite history)"
            )
    fresh = h5.check_h5_fresh_state(root / "qual.db")
    if not fresh["fresh"]:
        raise SystemExit(f"REMEDIATION STOP: {fresh['reason']} {fresh['counts']}")
    admission = h5.run_per_campaign_admission(core, plan_b_pids=())
    if not admission["admitted"]:
        raise SystemExit(f"REMEDIATION STOP: {admission['reason']}: {admission['detail']}")
    return admission


def assert_raw_evidence_quality(*, samples_path: Path, resource_path: Path,
                                concurrency_monitor: monitor.ConcurrencyMonitor,
                                resource_monitor: monitor.ResourceMonitor) -> dict:
    """Accept ONLY genuine persisted evidence (Issue 7 semantics)."""
    if not samples_path.exists() or samples_path.stat().st_size == 0:
        raise SystemExit("REMEDIATION FAILED: concurrency JSONL missing or empty")
    if not resource_path.exists() or resource_path.stat().st_size == 0:
        raise SystemExit("REMEDIATION FAILED: resource JSONL missing or empty")

    samples = list(concurrency_monitor.samples)
    owned = sum(
        1 for s in samples if any(getattr(o, "pid", None) is not None for o in getattr(s, "ownership", ()))
    )
    unique_runs = len({o.run_id for s in samples for o in getattr(s, "ownership", ())
                       if getattr(o, "pid", None) is not None})
    gpu_owned = sum(
        1 for s in samples
        if set(getattr(s, "gpu_compute_pids", ()))
        and set(getattr(s, "gpu_compute_pids", ())).issubset(set(getattr(s, "worker_pids", ())))
    )
    max_concurrency = h5.max_observed_concurrency(samples)
    max_gap = max((s.interval_s for s in samples), default=None)
    if not samples:
        raise SystemExit("REMEDIATION STOP: no concurrency samples")
    if unique_runs == 0 or owned == 0 or gpu_owned == 0:
        raise SystemExit(
            f"REMEDIATION FAILED: no ownership evidence (owned={owned}, gpu_owned={gpu_owned}, "
            f"unique_runs={unique_runs})"
        )
    if max_concurrency > h5.CONCURRENCY_BOUND:
        raise SystemExit("REMEDIATION FAILED: concurrency bound exceeded")
    if max_gap is None or max_gap > h5.H5_MAX_SAMPLE_GAP_S:
        raise SystemExit(f"REMEDIATION FAILED: max sample gap {max_gap} > {h5.H5_MAX_SAMPLE_GAP_S}")
    return {
        "concurrency_sample_count": len(samples),
        "resource_sample_count": len(resource_monitor.samples),
        "owned_worker_samples": owned,
        "gpu_owned_samples": gpu_owned,
        "unique_owned_run_ids": unique_runs,
        "max_observed_concurrency": max_concurrency,
        "max_sample_gap_s": max_gap,
        "concurrency_monitor_failure": concurrency_monitor.thread_failure,
        "resource_monitor_failure": resource_monitor.thread_failure,
    }


def build_acceptance(*, experiment_id: str, terminal_summary: dict, items: list[dict],
                     attempts: dict, actual_runs: set, evaluation: dict,
                     cycle_stems, raw_evidence: dict, quiescence: dict,
                     concurrency_artifact: Path, resource_artifact: Path) -> dict:
    """Pure, read-only acceptance builder.

    Performs NO experiment/run/attempt mutation and NO model inference. It only
    derives the acceptance document from already-read DB/API state. Raises
    ``SystemExit`` if any deterministic acceptance check fails.
    """
    completed = [i for i in items if i["status"] == "completed"]
    actual_names = sorted(i["recording_name"] for i in items)
    expected_names = sorted(cycle_stems)
    checks = {
        "status_completed": terminal_summary["status"] == "completed",
        "expected_items_16": terminal_summary["expected_items"] == 16,
        "completed_items_16": terminal_summary["completed_items"] == 16,
        "failed_items_0": terminal_summary["failed_items"] == 0,
        "queued_items_0": terminal_summary["queued_items"] == 0,
        "running_items_0": terminal_summary["running_items"] == 0,
        # Order-insensitive exact membership (frozen stems are not lexicographically sorted).
        "recording_membership_exact": (
            len(actual_names) == len(expected_names) and actual_names == expected_names
        ),
        "attempt_count_16": terminal_summary["attempt_count"] == 16,
        "one_attempt_per_item": all(
            attempts.get(item["id"]) is not None and len(attempts[item["id"]]) == 1
            for item in items
        ),
        "unique_runs_16": len(actual_runs) == 16,
        "evaluation_completed": evaluation["status"] == "completed",
        "evaluated_16": evaluation["evaluated_recordings"] == 16,
        "missing_0": evaluation["missing_recordings"] == 0,
        "coverage_1": evaluation["coverage"] == 1.0,
    }
    if not all(checks.values()):
        failed = {k: v for k, v in checks.items() if not v}
        raise SystemExit(f"REMEDIATION STOP: acceptance checks failed: {failed}")

    hashes = {
        "concurrency_jsonl_sha256": _sha256(concurrency_artifact),
        "resource_jsonl_sha256": _sha256(resource_artifact),
    }
    return {
        "milestone": "H5_REOBSERVE",
        "experiment_id": experiment_id,
        "expected_items": 16,
        "completed_items": len(completed),
        "actual_runs": len(actual_runs),
        "actual_attempts": sum(len(v) for v in attempts.values()),
        "evaluation": {
            "status": evaluation["status"],
            "evaluated_recordings": evaluation["evaluated_recordings"],
            "missing_recordings": evaluation["missing_recordings"],
            "coverage": evaluation["coverage"],
        },
        "membership": actual_names,
        "db_checks": checks,
        "raw_evidence": raw_evidence,
        "gpu_quiescence": quiescence,
        "executions_consumed": 16,
        "historical_actual_before": 136,
        "final_actual_total": 152,
        "artifacts": {
            "concurrency": str(concurrency_artifact),
            "resource": str(resource_artifact),
            "sha256": hashes,
        },
    }


def read_terminal_acceptance(app, *, experiment_id: str, final_summary: dict,
                             cycle_stems, concurrency_monitor, resource_monitor,
                             concurrency_artifact: Path, resource_artifact: Path,
                             quiescence: dict) -> dict:
    """Read-only post-run acceptance. Uses GET/DB reads ONLY (never POST /run)."""
    items = core.fetch_items(app, experiment_id)
    attempts: dict = {}
    actual_runs: set = set()
    for item in items:
        item_attempts = core.fetch_attempts(app, experiment_id, item["id"])
        attempts[item["id"]] = item_attempts
        for attempt in item_attempts:
            actual_runs.add(attempt["analysis_run_id"])
    evaluation = core.fetch_evaluation(app, final_summary["dataset_evaluation_id"])
    raw = assert_raw_evidence_quality(
        samples_path=concurrency_artifact,
        resource_path=resource_artifact,
        concurrency_monitor=concurrency_monitor,
        resource_monitor=resource_monitor,
    )
    return build_acceptance(
        experiment_id=experiment_id,
        terminal_summary=final_summary,
        items=items,
        attempts=attempts,
        actual_runs=actual_runs,
        evaluation=evaluation,
        cycle_stems=cycle_stems,
        raw_evidence=raw,
        quiescence=quiescence,
        concurrency_artifact=concurrency_artifact,
        resource_artifact=resource_artifact,
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args(argv)
    if not args.run:
        parser.error("--run is required (real 16-execution re-observation)")

    root = REOBSERVE_ROOT
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "work").mkdir(parents=True, exist_ok=True)
    admission = pre_gate(root)
    a1_snapshot = admission["a1_snapshot"]
    a1_mode = (admission.get("a1_admission") or {}).get("mode")
    baseline = admission["gpu_memory_baseline_mib"]
    cgroup_baseline = admission["cgroup_events_baseline"]
    started_ts = time.time()
    print(json.dumps({"pre_gate": {"a1_mode": a1_mode, "gpu_baseline_mib": baseline,
                                   "cgroup_baseline": cgroup_baseline}}, indent=2))

    app, _settings = core.build_app(root)
    cycle = membership.h5_cycles()[0]  # H5_FULL, exactly 16 stems, index 0
    membership.register_dataset_view(
        app, dataset_name=membership.H5_FULL, stems=tuple(common.H2_STEMS)
    )
    membership.assert_cycle_membership(app, cycle)

    def _run_ids(experiment_id: str) -> list[str]:
        from fastapi.testclient import TestClient

        items = TestClient(app).get(
            f"/api/dataset-experiments/{experiment_id}/items").json()
        return [i["latest_analysis_run_id"] for i in items if i.get("latest_analysis_run_id")]

    concurrency_monitor = monitor.ConcurrencyMonitor(
        app=app,
        evidence_path=CONCURRENCY_ARTIFACT,
        target_period_s=h5.H5_TARGET_SAMPLE_PERIOD_S,
        max_gap_s=h5.H5_MAX_SAMPLE_GAP_S,
        bound=h5.CONCURRENCY_BOUND,
        run_ids_for_experiment=_run_ids,
        gpu_compute_pids=lambda: [pid for pid, _mib in core.compute_apps()],
    )
    resource_monitor = monitor.ResourceMonitor(
        evidence_path=RESOURCE_ARTIFACT,
        interval_s=h5.ENDURANCE_SAMPLE_INTERVAL_S,
        gpu_metrics=core.gpu_metrics,
        run_ids_for_experiment=_run_ids,
        a1_baseline_snapshot=a1_snapshot,
        db_counts=lambda: core.db_counts(root),
    )
    concurrency_monitor.start()
    resource_monitor.start()

    experiment_id = None
    try:
        experiment_id = core.create_experiment(
            app, plugin_id=PLUGIN, name="Plan B H5 monitoring re-observation",
            concurrency=h5.CONCURRENCY_BOUND,
        )
        concurrency_monitor.activate(experiment_id)
        resource_monitor.activate(experiment_id)
        from fastapi.testclient import TestClient

        client = TestClient(app)
        started = client.post(f"/api/dataset-experiments/{experiment_id}/run")
        if started.status_code not in (200, 202):
            raise SystemExit(f"REMEDIATION STOP: run -> {started.status_code} {started.text}")
        deadline = time.time() + 3600
        summary = None
        while time.time() < deadline:
            for monitor_obj in (concurrency_monitor, resource_monitor):
                reason = monitor_obj.abort_reason or monitor_obj.thread_failure
                if reason:
                    raise SystemExit(f"REMEDIATION STOP: {reason}")
            body = client.get(f"/api/dataset-experiments/{experiment_id}").json()
            if body["status"] in ("completed", "completed_with_failures", "failed"):
                summary = body
                break
            time.sleep(0.5)
        if summary is None:
            raise SystemExit("REMEDIATION FAILED: did not reach terminal state")

        run_ids = _run_ids(experiment_id) if experiment_id else []
        try:
            core.wait_for_exact_workers_drained(run_ids, timeout_s=120)
        except core.WorkerDrainTimeout as exc:
            raise SystemExit(f"REMEDIATION STOP: H5_WORKER_DRAIN_TIMEOUT {exc}") from exc
        concurrency_monitor.deactivate()
        resource_monitor.deactivate()
    finally:
        concurrency_monitor.stop()
        resource_monitor.stop()

    core.assert_no_orphans("H5 reobserve post-run")
    quiescent = core.assert_gpu_quiescent(baseline, label="H5 reobserve")

    # Read-only acceptance: GET/DB reads ONLY. Never POST /run again.
    acceptance = read_terminal_acceptance(
        app,
        experiment_id=experiment_id,
        final_summary=summary,
        cycle_stems=cycle.stems,
        concurrency_monitor=concurrency_monitor,
        resource_monitor=resource_monitor,
        concurrency_artifact=CONCURRENCY_ARTIFACT,
        resource_artifact=RESOURCE_ARTIFACT,
        quiescence=quiescent,
    )
    ACCEPTANCE_ARTIFACT.write_text(
        json.dumps(acceptance, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )
    print(json.dumps({"experiment_id": experiment_id, "executions_consumed": 16,
                      "acceptance_ok": True, "evidence": str(ACCEPTANCE_ARTIFACT)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
