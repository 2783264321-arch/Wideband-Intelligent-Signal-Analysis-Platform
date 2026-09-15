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


def build_reobserve_payload(*, name: str = "Plan B H5 monitoring re-observation") -> dict:
    """Exact outgoing DatasetExperiment create payload for the re-observation.

    Workspace Dataset Name is the H5_FULL view (the frozen 16-stem dataset
    identity), NOT the generic ``common.DATASET_NAME`` ("SpaceNet"). This is a
    pure builder so the exact outgoing identity can be asserted by a CPU test.
    """
    return {
        "name": name,
        "dataset_name": membership.H5_FULL,
        "dataset_split": membership.H5_SPLIT,
        "dataset_label_space": membership.H5_LABEL_SPACE,
        "plugin_id": PLUGIN,
        "plugin_version": common.PLUGIN_VERSION[PLUGIN],
        "model_release_id": common.MODEL_RELEASE,
        "executor": "local_gpu",
        "parameters": {},
        "max_concurrency": h5.CONCURRENCY_BOUND,
    }


def create_reobserve_experiment(app, *, name: str = "Plan B H5 monitoring re-observation") -> str:
    """Create the single re-observation experiment with the EXACT H5_FULL identity."""
    from fastapi.testclient import TestClient

    created = TestClient(app).post(
        "/api/dataset-experiments", json=build_reobserve_payload(name=name)
    )
    if created.status_code != 201:
        raise SystemExit(f"REMEDIATION STOP: create -> {created.status_code} {created.text}")
    return created.json()["id"]


def workload_identity(experiment_read: dict) -> dict:
    """Persisted workload identity projection used by final acceptance."""
    return {
        "dataset_name": experiment_read.get("dataset_name"),
        "dataset_split": experiment_read.get("dataset_split"),
        "dataset_label_space": experiment_read.get("dataset_label_space"),
        "plugin_id": experiment_read.get("plugin_id"),
        "plugin_version": experiment_read.get("plugin_version"),
        "model_release_id": experiment_read.get("model_release_id"),
        "executor": experiment_read.get("executor"),
        "max_concurrency": experiment_read.get("max_concurrency"),
    }


def expected_workload_identity() -> dict:
    return {
        "dataset_name": membership.H5_FULL,
        "dataset_split": membership.H5_SPLIT,
        "dataset_label_space": membership.H5_LABEL_SPACE,
        "plugin_id": PLUGIN,
        "plugin_version": common.PLUGIN_VERSION[PLUGIN],
        "model_release_id": common.MODEL_RELEASE,
        "executor": "local_gpu",
        "max_concurrency": h5.CONCURRENCY_BOUND,
    }


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


def parse_concurrency_jsonl(path: Path) -> list[dict]:
    """Parse persisted concurrency JSONL (the authoritative NC-02 artifact)."""
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def parse_resource_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def _is_usable_number(value, *, minimum: float | None = None,
                      maximum: float | None = None) -> bool:
    """True iff value is a real finite number within optional bounds.

    Rejects None, booleans, non-numeric types, NaN/inf, and out-of-range values.
    """
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    if value != value:  # NaN
        return False
    if value in (float("inf"), float("-inf")):
        return False
    if minimum is not None and value < minimum:
        return False
    if maximum is not None and value > maximum:
        return False
    return True


# Required persisted resource fields with (minimum, maximum); None = unbounded.
_REQUIRED_RESOURCE_NUMERIC = (
    "timestamp",
    "gpu_memory_used_mib",
    "gpu_memory_free_mib",
    "gpu_utilization_pct",
    "cgroup_memory_current_bytes",
    "cgroup_clean_file_cache_bytes",
    "cgroup_committed_floor_bytes",
    "cgroup_effective_headroom_bytes",
    "cgroup_pressure_some_avg10",
    "cgroup_pressure_full_avg10",
    "cgroup_events_max",
    "cgroup_events_oom",
    "cgroup_events_oom_kill",
    "disk_free_bytes",
    "db_completed_items",
    "db_failed_items",
    "db_running_runs",
    "db_pending_runs",
    "db_attempt_count",
    "db_run_count",
)
_REQUIRED_RESOURCE_BOUNDS = {
    "timestamp": (0.0, None),
    "gpu_utilization_pct": (0.0, 100.0),
    "cgroup_pressure_some_avg10": (0.0, None),
    "cgroup_pressure_full_avg10": (0.0, None),
}


def _validate_persisted_resource_rows(rows: list[dict]) -> dict:
    """Fail closed unless every required field is a usable, in-range number."""
    for index, row in enumerate(rows):
        for field in _REQUIRED_RESOURCE_NUMERIC:
            if field not in row:
                raise SystemExit(
                    f"REMEDIATION FAILED: persisted resource sample {index} missing field {field!r}"
                )
            minimum, maximum = _REQUIRED_RESOURCE_BOUNDS.get(field, (0.0, None))
            if not _is_usable_number(row[field], minimum=minimum, maximum=maximum):
                raise SystemExit(
                    f"REMEDIATION FAILED: persisted resource sample {index} field "
                    f"{field!r} is not usable ({row[field]!r})"
                )
    return {"validated_samples": len(rows)}


def _collect_worker_memory_evidence(rows: list[dict]) -> tuple[int, list[dict]]:
    """Return (usable_pair_sample_count, observed_pairs).

    A usable pair requires a PID present in BOTH worker_rss_kb and worker_vmhwm_kb
    with numeric VmHWM >= VmRSS > 0.
    """
    usable = 0
    observed: list[dict] = []
    for row in rows:
        rss = row.get("worker_rss_kb") or {}
        vmhwm = row.get("worker_vmhwm_kb") or {}
        if not isinstance(rss, dict) or not isinstance(vmhwm, dict):
            continue
        common = set(rss) & set(vmhwm)
        valid_pairs = []
        for pid in common:
            r = rss[pid]
            h = vmhwm[pid]
            if _is_usable_number(r, minimum=1) and _is_usable_number(h, minimum=1) and h >= r:
                valid_pairs.append({"pid": pid, "rss_kb": r, "vmhwm_kb": h})
        if valid_pairs:
            usable += 1
            observed.extend(valid_pairs)
    return usable, observed


def assert_monitors_healthy(after_stop: dict,
                            concurrency_monitor: monitor.ConcurrencyMonitor,
                            resource_monitor: monitor.ResourceMonitor) -> dict:
    """Final monitor health gate (Finding E). Fails closed on ANY residual issue."""
    checks = {
        "concurrency_abort_reason_none": concurrency_monitor.abort_reason is None,
        "concurrency_thread_failure_none": concurrency_monitor.thread_failure is None,
        "resource_abort_reason_none": resource_monitor.abort_reason is None,
        "resource_thread_failure_none": resource_monitor.thread_failure is None,
        "concurrency_thread_stopped": after_stop.get("concurrency_thread_stopped") is True,
        "resource_thread_stopped": after_stop.get("resource_thread_stopped") is True,
    }
    if not all(checks.values()):
        failed = {k: v for k, v in checks.items() if not v}
        raise SystemExit(f"REMEDIATION FAILED: monitor health gate failed: {failed}")
    return checks


def assert_raw_evidence_quality(*, samples_path: Path, resource_path: Path,
                                concurrency_monitor: monitor.ConcurrencyMonitor,
                                resource_monitor: monitor.ResourceMonitor,
                                cgroup_events_baseline: dict | None) -> dict:
    """Accept ONLY genuine PERSISTED evidence (Findings E/F/G).

    The persisted JSONL artifacts are the authority for NC-02, not in-memory
    state. Parses both JSONL files, cross-checks counts, and fails closed on
    monitor failure or nonzero cgroup event deltas.
    """
    if not samples_path.exists() or samples_path.stat().st_size == 0:
        raise SystemExit("REMEDIATION FAILED: concurrency JSONL missing or empty")
    if not resource_path.exists() or resource_path.stat().st_size == 0:
        raise SystemExit("REMEDIATION FAILED: resource JSONL missing or empty")

    persisted_c = parse_concurrency_jsonl(samples_path)
    persisted_r = parse_resource_jsonl(resource_path)
    in_memory_c = list(concurrency_monitor.samples)
    in_memory_r = list(resource_monitor.samples)

    if not persisted_c:
        raise SystemExit("REMEDIATION STOP: persisted concurrency JSONL has no samples")
    if not persisted_r:
        raise SystemExit("REMEDIATION STOP: persisted resource JSONL has no samples")
    if len(persisted_c) != len(in_memory_c):
        raise SystemExit(
            f"REMEDIATION FAILED: persisted/in-memory concurrency count mismatch "
            f"({len(persisted_c)} != {len(in_memory_c)})"
        )
    if len(persisted_r) != len(in_memory_r):
        raise SystemExit(
            f"REMEDIATION FAILED: persisted/in-memory resource count mismatch "
            f"({len(persisted_r)} != {len(in_memory_r)})"
        )

    # Concurrency: derive ownership/GPU evidence from PERSISTED rows.
    owned = 0
    unique_runs: set = set()
    gpu_owned = 0
    for row in persisted_c:
        ownership = row.get("ownership") or []
        worker_pids = set(row.get("worker_pids") or ())
        gpu_pids = set(row.get("gpu_compute_pids") or ())
        mapped = {o.get("pid") for o in ownership if o.get("pid") is not None}
        if mapped:
            owned += 1
            unique_runs |= {o.get("run_id") for o in ownership if o.get("pid") is not None}
        if gpu_pids and gpu_pids.issubset(worker_pids):
            gpu_owned += 1
    max_gap = max(float(row.get("interval_s", 0.0)) for row in persisted_c)
    max_concurrency = max(len(set(row.get("worker_pids") or ())) for row in persisted_c)

    if owned == 0 or gpu_owned == 0 or not unique_runs:
        raise SystemExit(
            f"REMEDIATION FAILED: persisted ownership evidence missing "
            f"(owned={owned}, gpu_owned={gpu_owned}, unique_runs={len(unique_runs)})"
        )
    if max_gap > h5.H5_MAX_SAMPLE_GAP_S:
        raise SystemExit(f"REMEDIATION FAILED: persisted max sample gap {max_gap} > {h5.H5_MAX_SAMPLE_GAP_S}")
    if max_concurrency > h5.CONCURRENCY_BOUND:
        raise SystemExit(f"REMEDIATION FAILED: persisted max concurrency {max_concurrency} > {h5.CONCURRENCY_BOUND}")

    # Resource: every required field must be USABLE numeric evidence.
    _validate_persisted_resource_rows(persisted_r)
    worker_pairs, observed_pairs = _collect_worker_memory_evidence(persisted_r)
    if worker_pairs == 0:
        raise SystemExit(
            "REMEDIATION FAILED: no persisted sample with usable common-PID "
            "worker RSS+VmHWM evidence (need VmHWM >= VmRSS > 0)"
        )

    # Finding F: cgroup event deltas from the MANDATORY campaign baseline.
    if not isinstance(cgroup_events_baseline, dict):
        raise SystemExit("REMEDIATION FAILED: cgroup_events_baseline is missing/malformed")
    for key in ("max", "oom", "oom_kill"):
        if key not in cgroup_events_baseline or not _is_usable_number(
            cgroup_events_baseline[key], minimum=0
        ):
            raise SystemExit(
                f"REMEDIATION FAILED: cgroup_events_baseline missing/invalid key {key!r}"
            )
    baseline = {key: cgroup_events_baseline[key] for key in ("max", "oom", "oom_kill")}
    final_events = {
        "cgroup_events_max": persisted_r[-1]["cgroup_events_max"],
        "cgroup_events_oom": persisted_r[-1]["cgroup_events_oom"],
        "cgroup_events_oom_kill": persisted_r[-1]["cgroup_events_oom_kill"],
    }
    deltas = h5.cgroup_event_deltas(baseline=baseline, current=final_events)
    if deltas["max"] != 0 or deltas["oom"] != 0 or deltas["oom_kill"] != 0:
        raise SystemExit(f"REMEDIATION FAILED: cgroup event delta nonzero: {deltas}")

    def _span(field: str) -> dict:
        values = [row[field] for row in persisted_r]
        return {"min": min(values), "max": max(values)}

    resource_span_s = (
        float(persisted_r[-1]["timestamp"]) - float(persisted_r[0]["timestamp"])
        if len(persisted_r) > 1 else 0.0
    )
    return {
        "persisted_concurrency_sample_count": len(persisted_c),
        "persisted_resource_sample_count": len(persisted_r),
        "in_memory_concurrency_sample_count": len(in_memory_c),
        "in_memory_resource_sample_count": len(in_memory_r),
        "owned_worker_samples": owned,
        "gpu_owned_samples": gpu_owned,
        "unique_owned_run_ids": len(unique_runs),
        "max_observed_concurrency": max_concurrency,
        "max_sample_gap_s": max_gap,
        "worker_rss_vmhwm_samples": worker_pairs,
        "worker_memory_observed_pairs": observed_pairs,
        "cgroup_events_baseline": baseline,
        "cgroup_events_final": final_events,
        "cgroup_event_deltas": deltas,
        "resource_sample_span_s": resource_span_s,
        "observed_ranges": {
            "gpu_memory_used_mib": _span("gpu_memory_used_mib"),
            "gpu_memory_free_mib": _span("gpu_memory_free_mib"),
            "gpu_utilization_pct": _span("gpu_utilization_pct"),
            "cgroup_memory_current_bytes": _span("cgroup_memory_current_bytes"),
            "cgroup_clean_file_cache_bytes": _span("cgroup_clean_file_cache_bytes"),
            "cgroup_committed_floor_bytes": _span("cgroup_committed_floor_bytes"),
            "cgroup_effective_headroom_bytes": _span("cgroup_effective_headroom_bytes"),
            "cgroup_pressure_some_avg10": _span("cgroup_pressure_some_avg10"),
            "cgroup_pressure_full_avg10": _span("cgroup_pressure_full_avg10"),
            "disk_free_bytes": _span("disk_free_bytes"),
        },
        "concurrency_monitor_failure": concurrency_monitor.thread_failure,
        "resource_monitor_failure": resource_monitor.thread_failure,
    }


def build_acceptance(*, experiment_id: str, terminal_summary: dict, items: list[dict],
                     attempts: dict, actual_runs: set, evaluation: dict,
                     cycle_stems, raw_evidence: dict, quiescence: dict,
                     concurrency_artifact: Path, resource_artifact: Path,
                     campaign_wall_time_s: float | None = None) -> dict:
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
        "workload_identity_exact": (
            workload_identity(terminal_summary) == expected_workload_identity()
        ),
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
        "workload_identity": workload_identity(terminal_summary),
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
        "campaign_wall_time_s": campaign_wall_time_s,
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
                             quiescence: dict, after_stop: dict,
                             cgroup_events_baseline: dict,
                             campaign_wall_time_s: float | None = None) -> dict:
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
    monitor_health = assert_monitors_healthy(after_stop, concurrency_monitor, resource_monitor)
    raw = assert_raw_evidence_quality(
        samples_path=concurrency_artifact,
        resource_path=resource_artifact,
        concurrency_monitor=concurrency_monitor,
        resource_monitor=resource_monitor,
        cgroup_events_baseline=cgroup_events_baseline,
    )
    raw["monitor_health"] = monitor_health
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
        campaign_wall_time_s=campaign_wall_time_s,
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
    after_stop = {
        "concurrency_thread_stopped": False,
        "resource_thread_stopped": False,
    }
    try:
        # Finding D: exact H5_FULL dataset identity, never the generic "SpaceNet".
        experiment_id = create_reobserve_experiment(app)
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
        # Finding E: prove monitor threads actually terminated after stop().
        after_stop["concurrency_thread_stopped"] = not concurrency_monitor.is_alive()
        after_stop["resource_thread_stopped"] = not resource_monitor.is_alive()

    core.assert_no_orphans("H5 reobserve post-run")
    quiescent = core.assert_gpu_quiescent(baseline, label="H5 reobserve")
    campaign_wall_time_s = time.time() - started_ts

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
        after_stop=after_stop,
        cgroup_events_baseline=cgroup_baseline,
        campaign_wall_time_s=campaign_wall_time_s,
    )
    ACCEPTANCE_ARTIFACT.write_text(
        json.dumps(acceptance, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )
    print(json.dumps({"experiment_id": experiment_id, "executions_consumed": 16,
                      "acceptance_ok": True, "evidence": str(ACCEPTANCE_ARTIFACT)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
