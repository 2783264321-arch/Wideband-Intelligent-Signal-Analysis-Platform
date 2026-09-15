"""Plan B remediation: bounded H5 monitoring re-observation (CPU-only).

These tests EXECUTE the real acceptance/builder code paths (not source-string
checks) so that a deterministic post-inference tooling defect cannot consume the
single authorized 16-execution window.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import plan_b_common as common  # noqa: E402
import plan_b_h5_core as h5  # noqa: E402
import plan_b_h5_membership as membership  # noqa: E402
import plan_b_h5_reobserve_cp as reobserve  # noqa: E402

SCRIPTS_DIR = SCRIPTS


# ---------------------------------------------------------------------------
# Finding D — exact dataset identity in the create payload
# ---------------------------------------------------------------------------

def test_reobserve_create_payload_uses_h5_full_not_spacenet() -> None:
    payload = reobserve.build_reobserve_payload()
    assert payload["dataset_name"] == membership.H5_FULL
    assert payload["dataset_name"] != common.DATASET_NAME
    assert payload["dataset_split"] == membership.H5_SPLIT
    assert payload["dataset_label_space"] == membership.H5_LABEL_SPACE


def test_reobserve_create_payload_wrong_identity_would_fail() -> None:
    payload = reobserve.build_reobserve_payload()
    bad = dict(payload)
    bad["dataset_name"] = common.DATASET_NAME  # "SpaceNet" is the wrong view
    assert bad != reobserve.expected_workload_identity()


def test_reobserve_does_not_use_generic_create_experiment() -> None:
    source = (SCRIPTS_DIR / "plan_b_h5_reobserve_cp.py").read_text(encoding="utf-8")
    assert "core.create_experiment(" not in source
    assert "create_reobserve_experiment(" in source


# ---------------------------------------------------------------------------
# Workload identity — plugin/version/release/executor/concurrency
# ---------------------------------------------------------------------------

def test_reobserve_workload_identity_exact() -> None:
    payload = reobserve.build_reobserve_payload()
    assert payload["plugin_id"] == "cpn_bandwidth_tier"
    assert payload["plugin_version"] == common.PLUGIN_VERSION["cpn_bandwidth_tier"]
    assert payload["model_release_id"] == common.MODEL_RELEASE
    assert payload["executor"] == "local_gpu"
    assert payload["max_concurrency"] == h5.CONCURRENCY_BOUND == 2
    assert payload["parameters"] == {}


def test_workload_identity_projection_matches_expected() -> None:
    payload = reobserve.build_reobserve_payload()
    read_model = {
        "dataset_name": payload["dataset_name"], "dataset_split": payload["dataset_split"],
        "dataset_label_space": payload["dataset_label_space"], "plugin_id": payload["plugin_id"],
        "plugin_version": payload["plugin_version"], "model_release_id": payload["model_release_id"],
        "executor": payload["executor"], "max_concurrency": payload["max_concurrency"],
    }
    assert reobserve.workload_identity(read_model) == reobserve.expected_workload_identity()


# ---------------------------------------------------------------------------
# Finding E — final monitor health fails closed
# ---------------------------------------------------------------------------

class _Mon:
    def __init__(self, *, abort_reason=None, thread_failure=None, alive=False):
        self.abort_reason = abort_reason
        self.thread_failure = thread_failure
        self._alive = alive
        self.samples = []

    def is_alive(self):
        return self._alive


def _healthy_pair():
    return _Mon(), _Mon()


def test_monitor_health_all_clear() -> None:
    c, r = _healthy_pair()
    checks = reobserve.assert_monitors_healthy(
        {"concurrency_thread_stopped": True, "resource_thread_stopped": True}, c, r)
    assert all(checks.values())


@pytest.mark.parametrize("which", ["concurrency_abort", "resource_abort",
                                   "concurrency_failure", "resource_failure",
                                   "concurrency_alive", "resource_alive"])
def test_monitor_health_fails_closed(which: str) -> None:
    c, r = _healthy_pair()
    stopped = {"concurrency_thread_stopped": True, "resource_thread_stopped": True}
    if which == "concurrency_abort":
        c.abort_reason = "CONCURRENCY_SAMPLING_GAP"
    elif which == "resource_abort":
        r.abort_reason = "A1_MEMORY_MONITOR_ABORT"
    elif which == "concurrency_failure":
        c.thread_failure = "boom"
    elif which == "resource_failure":
        r.thread_failure = "boom"
    elif which == "concurrency_alive":
        stopped["concurrency_thread_stopped"] = False
    elif which == "resource_alive":
        stopped["resource_thread_stopped"] = False
    with pytest.raises(SystemExit):
        reobserve.assert_monitors_healthy(stopped, c, r)


# ---------------------------------------------------------------------------
# Findings F + G — persisted evidence authority, cgroup deltas, RSS/VmHWM
# ---------------------------------------------------------------------------

_CGROUP_OK = {"max": 0, "oom": 0, "oom_kill": 0}


def _persisted_concurrency(tmp_path: Path):
    rows = [
        {"timestamp": 0.0, "interval_s": 0.25, "worker_pids": [11],
         "gpu_compute_pids": [11], "ownership": [{"run_id": "run_a", "pid": 11}],
         "worker_run_ids": ["run_a"], "db_running_runs": 1, "db_pending_runs": 0},
        {"timestamp": 0.25, "interval_s": 0.25, "worker_pids": [11, 22],
         "gpu_compute_pids": [11, 22],
         "ownership": [{"run_id": "run_a", "pid": 11}, {"run_id": "run_b", "pid": 22}],
         "worker_run_ids": ["run_a", "run_b"], "db_running_runs": 2, "db_pending_runs": 0},
    ]
    path = tmp_path / "c.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path, rows


def _persisted_resource(tmp_path: Path):
    rows = [
        {"timestamp": 0.0, "gpu_memory_used_mib": 10, "gpu_memory_free_mib": 100,
         "gpu_utilization_pct": 5, "cgroup_memory_current_bytes": 1,
         "cgroup_clean_file_cache_bytes": 2, "cgroup_committed_floor_bytes": 3,
         "cgroup_effective_headroom_bytes": 4, "cgroup_pressure_some_avg10": 0.0,
         "cgroup_pressure_full_avg10": 0.0, "cgroup_events_max": 0,
         "cgroup_events_oom": 0, "cgroup_events_oom_kill": 0, "disk_free_bytes": 10 ** 12,
         "db_completed_items": 1, "db_failed_items": 0, "db_running_runs": 1,
         "db_pending_runs": 0, "db_attempt_count": 1, "db_run_count": 1,
         "worker_rss_kb": {"11": 1000}, "worker_vmhwm_kb": {"11": 2000}},
    ]
    path = tmp_path / "r.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path, rows


class _SampleMon:
    def __init__(self, samples, thread_failure=None):
        self.samples = samples
        self.thread_failure = thread_failure


def _raw_call(tmp_path, *, cgroups=None, resource_rows=None):
    cpath, crows = _persisted_concurrency(tmp_path)
    rpath, rrows = _persisted_resource(tmp_path)
    if resource_rows is not None:
        rpath.write_text("\n".join(json.dumps(r) for r in resource_rows) + "\n", encoding="utf-8")
        rrows = resource_rows
    return reobserve.assert_raw_evidence_quality(
        samples_path=cpath, resource_path=rpath,
        concurrency_monitor=_SampleMon(crows),
        resource_monitor=_SampleMon(rrows),
        cgroup_events_baseline=cgroups or _CGROUP_OK,
    )


def test_persisted_concurrency_parsed(tmp_path: Path) -> None:
    result = _raw_call(tmp_path)
    assert result["persisted_concurrency_sample_count"] == 2
    assert result["owned_worker_samples"] == 2
    assert result["gpu_owned_samples"] == 2
    assert result["unique_owned_run_ids"] == 2
    assert result["max_observed_concurrency"] == 2


def test_persisted_resource_parsed(tmp_path: Path) -> None:
    result = _raw_call(tmp_path)
    assert result["persisted_resource_sample_count"] == 1
    assert result["worker_rss_vmhwm_samples"] == 1


def test_persisted_count_mismatch_fails(tmp_path: Path) -> None:
    cpath, _ = _persisted_concurrency(tmp_path)
    rpath, rrows = _persisted_resource(tmp_path)
    with pytest.raises(SystemExit):
        reobserve.assert_raw_evidence_quality(
            samples_path=cpath, resource_path=rpath,
            concurrency_monitor=_SampleMon([]),  # in-memory mismatch
            resource_monitor=_SampleMon(rrows),
            cgroup_events_baseline=_CGROUP_OK,
        )


def test_missing_worker_rss_evidence_fails(tmp_path: Path) -> None:
    rows = _persisted_resource(tmp_path)[1]
    rows[0]["worker_rss_kb"] = {}
    rows[0]["worker_vmhwm_kb"] = {}
    with pytest.raises(SystemExit):
        _raw_call(tmp_path, resource_rows=rows)


@pytest.mark.parametrize("over", [{"cgroup_events_max": 1},
                                  {"cgroup_events_oom": 1},
                                  {"cgroup_events_oom_kill": 1}])
def test_cgroup_delta_nonzero_fails(tmp_path: Path, over: dict) -> None:
    rows = _persisted_resource(tmp_path)[1]
    rows[0].update(over)
    with pytest.raises(SystemExit):
        _raw_call(tmp_path, resource_rows=rows)


def test_cgroup_delta_zero_passes(tmp_path: Path) -> None:
    result = _raw_call(tmp_path)
    assert result["cgroup_event_deltas"] == {"max": 0, "oom": 0, "oom_kill": 0}


def test_ownership_evidence_required(tmp_path: Path) -> None:
    cpath, _ = _persisted_concurrency(tmp_path)
    rpath, rrows = _persisted_resource(tmp_path)
    empty_rows = [{"timestamp": 0.0, "interval_s": 0.25, "worker_pids": [],
                   "gpu_compute_pids": [], "ownership": [], "worker_run_ids": [],
                   "db_running_runs": 0, "db_pending_runs": 0}]
    cpath.write_text(json.dumps(empty_rows[0]) + "\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        reobserve.assert_raw_evidence_quality(
            samples_path=cpath, resource_path=rpath,
            concurrency_monitor=_SampleMon(empty_rows),
            resource_monitor=_SampleMon(rrows),
            cgroup_events_baseline=_CGROUP_OK,
        )


# ---------------------------------------------------------------------------
# Existing guarantees preserved
# ---------------------------------------------------------------------------

def test_exactly_one_experiment_run_in_lifecycle() -> None:
    source = (SCRIPTS_DIR / "plan_b_h5_reobserve_cp.py").read_text(encoding="utf-8")
    assert source.count('f"/api/dataset-experiments/{experiment_id}/run"') == 1
    assert "core.run_experiment(" not in source


def test_post_run_acceptance_is_read_only() -> None:
    source = (SCRIPTS_DIR / "plan_b_h5_reobserve_cp.py").read_text(encoding="utf-8")
    assert "read_terminal_acceptance(" in source
    assert "core.fetch_items(" in source
    assert "core.fetch_attempts(" in source


def test_reobserve_artifacts_unique_under_root() -> None:
    assert reobserve.REOBSERVE_ROOT == common.PLAN_B_ROOT / "h5_reobserve"
    for a in (reobserve.CONCURRENCY_ARTIFACT, reobserve.RESOURCE_ARTIFACT,
              reobserve.ACCEPTANCE_ARTIFACT):
        assert a.parent == reobserve.REOBSERVE_ROOT
        assert a.name.startswith("h5_reobserve")


def test_reobserve_single_full_membership_only() -> None:
    source = (SCRIPTS_DIR / "plan_b_h5_reobserve_cp.py").read_text(encoding="utf-8")
    assert "h5_cycles()[0]" in source
    assert "assert_cycle_membership" in source
    assert 'PLUGIN = "cpn_bandwidth_tier"' in source


# ---------------------------------------------------------------------------
# Acceptance builder — membership ordering, identity, wall time
# ---------------------------------------------------------------------------

def _items_for(names):
    return [{"id": f"i{n}", "status": "completed", "recording_name": name}
            for n, name in enumerate(names)]


def _full_summary():
    payload = reobserve.build_reobserve_payload()
    return {
        "status": "completed", "expected_items": 16, "completed_items": 16,
        "failed_items": 0, "queued_items": 0, "running_items": 0, "attempt_count": 16,
        "dataset_name": payload["dataset_name"], "dataset_split": payload["dataset_split"],
        "dataset_label_space": payload["dataset_label_space"], "plugin_id": payload["plugin_id"],
        "plugin_version": payload["plugin_version"], "model_release_id": payload["model_release_id"],
        "executor": payload["executor"], "max_concurrency": payload["max_concurrency"],
    }


def _acceptance_kwargs(items):
    return dict(
        experiment_id="exp_x", terminal_summary=_full_summary(), items=items,
        attempts={i["id"]: [{"analysis_run_id": f"run_{n}"}] for n, i in enumerate(items)},
        actual_runs={f"run_{n}" for n in range(len(items))},
        evaluation={"status": "completed", "evaluated_recordings": 16,
                    "missing_recordings": 0, "coverage": 1.0},
        cycle_stems=common.H2_STEMS,
        raw_evidence={"owned_worker_samples": 1}, quiescence={"quiescent": True},
    )


def test_membership_exact_despite_lexical_order(tmp_path: Path) -> None:
    cpath = tmp_path / "c.jsonl"; cpath.write_text("x\n")
    rpath = tmp_path / "r.jsonl"; rpath.write_text("y\n")
    kwargs = _acceptance_kwargs(_items_for(common.H2_STEMS))
    kwargs["concurrency_artifact"] = cpath; kwargs["resource_artifact"] = rpath
    kwargs["campaign_wall_time_s"] = 12.5
    result = reobserve.build_acceptance(**kwargs)
    assert result["db_checks"]["recording_membership_exact"] is True
    assert result["db_checks"]["workload_identity_exact"] is True
    assert result["campaign_wall_time_s"] == 12.5
    assert result["executions_consumed"] == 16
    assert result["historical_actual_before"] == 136
    assert result["final_actual_total"] == 152


@pytest.mark.parametrize("names", [
    list(common.H2_STEMS[:15]),
    list(common.H2_STEMS[:15]) + ["999", "5"],
    list(common.H2_STEMS[:-1]) + ["777"],
])
def test_membership_mismatch_fails(tmp_path: Path, names) -> None:
    cpath = tmp_path / "c.jsonl"; cpath.write_text("x\n")
    rpath = tmp_path / "r.jsonl"; rpath.write_text("y\n")
    kwargs = _acceptance_kwargs(_items_for(names))
    kwargs["concurrency_artifact"] = cpath; kwargs["resource_artifact"] = rpath
    with pytest.raises(SystemExit):
        reobserve.build_acceptance(**kwargs)


def test_wrong_workload_identity_fails(tmp_path: Path) -> None:
    cpath = tmp_path / "c.jsonl"; cpath.write_text("x\n")
    rpath = tmp_path / "r.jsonl"; rpath.write_text("y\n")
    kwargs = _acceptance_kwargs(_items_for(common.H2_STEMS))
    kwargs["concurrency_artifact"] = cpath; kwargs["resource_artifact"] = rpath
    bad = _full_summary()
    bad["dataset_name"] = common.DATASET_NAME
    kwargs["terminal_summary"] = bad
    with pytest.raises(SystemExit):
        reobserve.build_acceptance(**kwargs)


# ---------------------------------------------------------------------------
# Finding I — usable resource evidence (numeric, non-null, in range)
# ---------------------------------------------------------------------------

def _raw_quality_with(tmp_path, *, resource_mutator=None, baseline=_CGROUP_OK):
    cpath, crows = _persisted_concurrency(tmp_path)
    rpath, rrows = _persisted_resource(tmp_path)
    if resource_mutator is not None:
        resource_mutator(rrows[0])
        rpath.write_text(json.dumps(rrows[0]) + "\n", encoding="utf-8")
    return reobserve.assert_raw_evidence_quality(
        samples_path=cpath, resource_path=rpath,
        concurrency_monitor=reobserve_mons(crows),
        resource_monitor=reobserve_mons(rrows),
        cgroup_events_baseline=baseline,
    )


def reobserve_mons(rows):
    return _SampleMon(rows)


@pytest.mark.parametrize("mutate", [
    lambda r: r.pop("disk_free_bytes"),
    lambda r: r.update({"disk_free_bytes": None}),
    lambda r: r.update({"disk_free_bytes": float("nan")}),
    lambda r: r.update({"cgroup_pressure_some_avg10": None}),
    lambda r: r.update({"db_run_count": None}),
    lambda r: r.update({"gpu_utilization_pct": 101}),
    lambda r: r.update({"gpu_utilization_pct": -1}),
    lambda r: r.update({"gpu_memory_used_mib": None}),
    lambda r: r.update({"cgroup_memory_current_bytes": None}),
    lambda r: r.update({"cgroup_clean_file_cache_bytes": None}),
    lambda r: r.update({"cgroup_committed_floor_bytes": None}),
    lambda r: r.update({"cgroup_effective_headroom_bytes": None}),
    lambda r: r.update({"cgroup_events_oom_kill": None}),
    lambda r: r.update({"timestamp": None}),
    lambda r: r.update({"db_completed_items": None}),
    lambda r: r.update({"db_failed_items": None}),
    lambda r: r.update({"db_running_runs": None}),
    lambda r: r.update({"db_pending_runs": None}),
    lambda r: r.update({"db_attempt_count": None}),
    lambda r: r.update({"gpu_memory_free_mib": "12"}),  # non-numeric
])
def test_resource_field_usability_fail_closed(tmp_path: Path, mutate) -> None:
    with pytest.raises(SystemExit):
        _raw_quality_with(tmp_path, resource_mutator=mutate)


def test_resource_valid_passes(tmp_path: Path) -> None:
    result = _raw_quality_with(tmp_path)
    assert result["persisted_resource_sample_count"] == 1
    assert result["worker_rss_vmhwm_samples"] == 1


@pytest.mark.parametrize("mutate", [
    lambda r: r.update({"worker_rss_kb": {"11": 1000}, "worker_vmhwm_kb": {}}),  # VmHWM absent
    lambda r: r.update({"worker_rss_kb": {}, "worker_vmhwm_kb": {"11": 2000}}),  # RSS absent
    lambda r: r.update({"worker_rss_kb": {"11": 1000}, "worker_vmhwm_kb": {"22": 2000}}),  # no common PID
    lambda r: r.update({"worker_rss_kb": {"11": 2000}, "worker_vmhwm_kb": {"11": 1000}}),  # VmHWM < RSS
    lambda r: r.update({"worker_rss_kb": {"11": 0}, "worker_vmhwm_kb": {"11": 0}}),        # not > 0
    lambda r: r.update({"worker_rss_kb": {}, "worker_vmhwm_kb": {}}),                       # empty
])
def test_worker_memory_evidence_fail_closed(tmp_path: Path, mutate) -> None:
    with pytest.raises(SystemExit):
        _raw_quality_with(tmp_path, resource_mutator=mutate)


def test_worker_memory_valid_passes(tmp_path: Path) -> None:
    result = _raw_quality_with(tmp_path)
    assert result["worker_rss_vmhwm_samples"] == 1
    assert result["worker_memory_observed_pairs"][0]["pid"] == "11"
    assert result["worker_memory_observed_pairs"][0]["vmhwm_kb"] >= result["worker_memory_observed_pairs"][0]["rss_kb"]


@pytest.mark.parametrize("baseline", [
    None,
    {},
    {"max": 0, "oom": 0},
    {"max": 0, "oom": 0, "oom_kill": None},
    {"max": 0, "oom": 0, "oom_kill": "x"},
])
def test_cgroup_baseline_fail_closed(tmp_path: Path, baseline) -> None:
    with pytest.raises(SystemExit):
        _raw_quality_with(tmp_path, baseline=baseline)


def test_observed_ranges_present(tmp_path: Path) -> None:
    result = _raw_quality_with(tmp_path)
    assert set(result["observed_ranges"]) >= {
        "gpu_memory_used_mib", "gpu_memory_free_mib", "gpu_utilization_pct",
        "cgroup_memory_current_bytes", "cgroup_clean_file_cache_bytes",
        "cgroup_committed_floor_bytes", "cgroup_effective_headroom_bytes",
        "cgroup_pressure_some_avg10", "cgroup_pressure_full_avg10", "disk_free_bytes",
    }
