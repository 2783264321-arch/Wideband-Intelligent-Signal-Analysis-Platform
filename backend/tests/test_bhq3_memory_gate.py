"""BHQ-3 Amendment A1 — cache-aware memory admission gate (acceptance tooling).

Behavior tests for scripts/bhq3_memory_gate.py. Pure evaluation is exercised with
synthetic snapshot dicts; the reader is exercised against a temporary fake
cgroup/proc tree (never the real cgroup).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
_SPEC = importlib.util.spec_from_file_location(
    "bhq3_memory_gate", SCRIPTS / "bhq3_memory_gate.py"
)
gate = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gate)

GIB = 1024 ** 3
MIB = 1024 ** 2


def _base_snapshot() -> dict:
    return {
        "memory_max": 90 * GIB,
        "memory_current": 87 * GIB,
        "memory_high": 88 * GIB,
        "memory_low": 0,
        "memory_min": 0,
        "swap_current": 0,
        "swap_max": 0,
        "stat": {
            "anon": 1450 * MIB,
            "file": 85 * GIB,
            "shmem": 8192,
            "file_dirty": 16384,
            "file_writeback": 0,
            "unevictable": 0,
            "slab_reclaimable": 320 * MIB,
            "slab_unreclaimable": 12 * MIB,
            "kernel_stack": 12 * MIB,
            "pagetables": 12 * MIB,
            "percpu": 7488,
            "sock": 86016,
            "active_file": 78 * GIB,
            "inactive_file": 7 * GIB,
        },
        "events": {"low": 0, "high": 1334411, "max": 0, "oom": 0, "oom_kill": 0},
        "pressure": {
            "some_avg10": 0.0, "some_avg60": 0.0, "some_avg300": 0.39,
            "full_avg10": 0.0, "full_avg60": 0.0, "full_avg300": 0.39,
        },
        "mem_available_bytes": 652 * GIB,
    }


def _set(snapshot, **fields):
    for key, value in fields.items():
        if "." in key:
            head, tail = key.split(".", 1)
            snapshot[head][tail] = value
        else:
            snapshot[key] = value
    return snapshot


# ---------------------------------------------------------------------------
# Admission: Path A / Path B
# ---------------------------------------------------------------------------


def test_path_a_exact_boundary_is_raw():
    snap = _set(_base_snapshot(), memory_current=90 * GIB - 4 * GIB)
    result = gate.evaluate(snap)
    assert result["mode"] == "raw"
    assert result["derived"]["raw_headroom"] == 4 * GIB


def test_path_a_above_threshold_is_raw():
    result = gate.evaluate(_set(_base_snapshot(), memory_current=80 * GIB))
    assert result["mode"] == "raw"


def test_path_a_below_falls_through_to_cache_guarded():
    result = gate.evaluate(_base_snapshot())
    assert result["mode"] == "cache_guarded"


def test_measured_cache_dominant_snapshot_is_cache_guarded():
    result = gate.evaluate(_base_snapshot())
    assert result["mode"] == "cache_guarded"
    d = result["derived"]
    assert d["committed_floor"] < 16 * GIB
    assert d["effective_headroom"] >= 6 * GIB
    assert d["clean_cache_ratio"] >= 0.5


def test_committed_floor_exact_boundary():
    snap = _set(_base_snapshot(), memory_current=87 * GIB,
                **{"stat.shmem": 0, "stat.file_dirty": 0, "stat.file_writeback": 0,
                   "stat.file": 71 * GIB})
    # committed_floor == 16 GiB exactly -> allowed
    assert gate.evaluate(snap)["mode"] == "cache_guarded"
    snap2 = _set(snap, **{"stat.file": 71 * GIB - 1})
    assert gate.evaluate(snap2)["mode"] == "block"


def test_effective_headroom_exact_boundary():
    snap = _set(_base_snapshot(), memory_max=10 * GIB, memory_current=9 * GIB,
                **{"stat.shmem": 0, "stat.file_dirty": 0, "stat.file_writeback": 0,
                   "stat.file": 5 * GIB})
    # raw=1 GiB (<4); clean=5; committed=4; effective=6 GiB exactly -> allowed
    assert gate.evaluate(snap)["mode"] == "cache_guarded"
    snap2 = _set(snap, **{"stat.file": 5 * GIB - 1})
    result = gate.evaluate(snap2)
    assert result["mode"] == "block"
    assert any("effective" in r.lower() for r in result["reasons"])


def test_clean_cache_ratio_below_half_blocks():
    # Path B requires current > 86 GiB; dilute clean cache with a large non-file
    # component so the clean-cache ratio collapses and committed floor grows.
    snap = _set(_base_snapshot(), memory_current=87 * GIB,
                **{"stat.shmem": 0, "stat.file_dirty": 0, "stat.file_writeback": 0,
                   "stat.file": 30 * GIB})
    result = gate.evaluate(snap)
    assert result["mode"] == "block"
    assert any("clean" in r.lower() for r in result["reasons"])


def test_unevictable_exact_boundary():
    snap = _set(_base_snapshot(), **{"stat.unevictable": 256 * MIB})
    assert gate.evaluate(snap)["mode"] == "cache_guarded"
    snap2 = _set(_base_snapshot(), **{"stat.unevictable": 256 * MIB + 1})
    assert gate.evaluate(snap2)["mode"] == "block"


def test_shmem_exact_boundary():
    snap = _set(_base_snapshot(), **{"stat.shmem": 2 * GIB})
    # clean cache = 85 GiB - 2 GiB - 16 KiB; still ratio>=0.5 and floor small
    assert gate.evaluate(snap)["mode"] == "cache_guarded"
    snap2 = _set(_base_snapshot(), **{"stat.shmem": 2 * GIB + 1})
    assert gate.evaluate(snap2)["mode"] == "block"


def test_dirty_writeback_exact_boundary():
    snap = _set(_base_snapshot(), **{"stat.file_dirty": 512 * MIB})
    assert gate.evaluate(snap)["mode"] == "cache_guarded"
    snap2 = _set(_base_snapshot(), **{"stat.file_dirty": 512 * MIB + 1})
    assert gate.evaluate(snap2)["mode"] == "block"


# ---------------------------------------------------------------------------
# Accounting safety (total-minus-explicitly-clean)
# ---------------------------------------------------------------------------


def test_shmem_is_not_counted_as_clean_cache():
    snap = _set(_base_snapshot(), **{"stat.shmem": 60 * GIB})
    result = gate.evaluate(snap)
    d = result["derived"]
    assert d["clean_file_cache"] == 85 * GIB - 60 * GIB - 16384
    # ratio collapses -> block (shmem is unreclaimable here)
    assert result["mode"] == "block"


def test_dirty_and_writeback_excluded_from_clean_cache():
    snap = _set(_base_snapshot(), **{"stat.shmem": 0, "stat.file_dirty": 3 * GIB,
                                     "stat.file_writeback": 1 * GIB})
    d = gate.evaluate(snap)["derived"]
    assert d["clean_file_cache"] == 85 * GIB - 3 * GIB - 1 * GIB
    assert d["committed_floor"] == 87 * GIB - (85 * GIB - 4 * GIB)


def test_unknown_accounting_is_charged_into_committed_floor():
    base = gate.evaluate(_base_snapshot())["derived"]["committed_floor"]
    snap = _set(_base_snapshot(), memory_current=90 * GIB)  # +3 GiB unexplained
    d = gate.evaluate(snap)["derived"]
    assert d["committed_floor"] == base + 3 * GIB


def test_kernel_accounting_cannot_disappear():
    # Increasing kernel-side usage raises memory.current (and thus committed_floor)
    # without changing the explicit clean cache.
    snap = _set(_base_snapshot(), **{"stat.slab_reclaimable": 5 * GIB})
    snap["memory_current"] += 5 * GIB
    d = gate.evaluate(snap)["derived"]
    assert d["committed_floor"] == (87 + 5) * GIB - (85 * GIB - 16384 - 8192)


def test_no_subtraction_of_slab_reclaimable_from_floor():
    snap = _set(_base_snapshot(), **{"stat.slab_reclaimable": 8 * GIB})
    d = gate.evaluate(snap)["derived"]
    # slab_reclaimable is NOT subtracted; floor depends only on current - clean file
    assert d["committed_floor"] == 87 * GIB - (85 * GIB - 16384 - 8192)


# ---------------------------------------------------------------------------
# Fail closed
# ---------------------------------------------------------------------------


def test_block_on_missing_field():
    snap = _base_snapshot()
    del snap["stat"]
    assert gate.evaluate(snap)["mode"] == "block"


def test_block_on_malformed_numeric_field():
    snap = _set(_base_snapshot(), **{"stat.file": "not-a-number"})
    assert gate.evaluate(snap)["mode"] == "block"


def test_block_on_memory_max_is_max_keyword():
    snap = _set(_base_snapshot(), memory_max="max")
    assert gate.evaluate(snap)["mode"] == "block"


@pytest.mark.parametrize("mutate, needle", [
    (lambda s: _set(s, memory_low=1), "low"),
    (lambda s: _set(s, memory_min=1), "min"),
    (lambda s: _set(s, **{"events.max": 1}), "max"),
    (lambda s: _set(s, **{"events.oom": 1}), "oom"),
    (lambda s: _set(s, **{"events.oom_kill": 1}), "oom_kill"),
    (lambda s: _set(s, **{"pressure.some_avg10": 5.5}), "some"),
    (lambda s: _set(s, **{"pressure.full_avg10": 1.5}), "full"),
    (lambda s: _set(s, mem_available_bytes=63 * GIB), "MemAvailable"),
    (lambda s: _set(s, **{"stat.file": 10 * GIB}), "clean"),
])
def test_block_on_guarded_condition(mutate, needle):
    result = gate.evaluate(mutate(_base_snapshot()))
    assert result["mode"] == "block"
    assert any(needle.lower() in reason.lower() for reason in result["reasons"])


def test_high_events_are_not_a_blocker():
    snap = _set(_base_snapshot(), **{"events.high": 9_999_999})
    assert gate.evaluate(snap)["mode"] == "cache_guarded"


# ---------------------------------------------------------------------------
# Reader (fake tree)
# ---------------------------------------------------------------------------


def _write_tree(root: Path, snapshot: dict, drop: tuple[str, ...] = ()):
    (root / "memory.stat").write_text(
        "\n".join(f"{k} {v}" for k, v in snapshot["stat"].items()) + "\n"
    )
    (root / "memory.max").write_text(f"{snapshot['memory_max']}\n")
    (root / "memory.current").write_text(f"{snapshot['memory_current']}\n")
    (root / "memory.high").write_text(f"{snapshot['memory_high']}\n")
    (root / "memory.low").write_text(f"{snapshot['memory_low']}\n")
    (root / "memory.min").write_text(f"{snapshot['memory_min']}\n")
    (root / "memory.swap.current").write_text(f"{snapshot['swap_current']}\n")
    (root / "memory.swap.max").write_text(f"{snapshot['swap_max']}\n")
    (root / "memory.events").write_text(
        "\n".join(f"{k} {v}" for k, v in snapshot["events"].items()) + "\n"
    )
    p = snapshot["pressure"]
    (root / "memory.pressure").write_text(
        f"some avg10={p['some_avg10']} avg60={p['some_avg60']} avg300={p['some_avg300']} total=0\n"
        f"full avg10={p['full_avg10']} avg60={p['full_avg60']} avg300={p['full_avg300']} total=0\n"
    )
    meminfo = root / "meminfo"
    meminfo.write_text(f"MemTotal: 1 kB\nMemAvailable: {snapshot['mem_available_bytes'] // 1024} kB\n")
    for name in drop:
        (root / name).unlink()
    return meminfo


def test_reader_parses_fake_tree(tmp_path):
    snap = _base_snapshot()
    meminfo = _write_tree(tmp_path, snap)
    parsed = gate.read_snapshot(cgroup_root=tmp_path, meminfo=meminfo)
    assert parsed["memory_max"] == snap["memory_max"]
    assert parsed["stat"]["file"] == snap["stat"]["file"]
    assert parsed["events"]["oom_kill"] == 0
    assert parsed["pressure"]["full_avg10"] == 0.0
    assert parsed["mem_available_bytes"] == snap["mem_available_bytes"]
    assert gate.evaluate(parsed)["mode"] == "cache_guarded"


def test_reader_missing_mandatory_field_blocks(tmp_path):
    snap = _base_snapshot()
    meminfo = _write_tree(tmp_path, snap, drop=("memory.stat",))
    parsed = gate.read_snapshot(cgroup_root=tmp_path, meminfo=meminfo)
    assert gate.evaluate(parsed)["mode"] == "block"


# ---------------------------------------------------------------------------
# Live monitor
# ---------------------------------------------------------------------------


def _monitor():
    base = _base_snapshot()
    return base, gate.MemoryMonitor(base)


def test_monitor_quiet_snapshot_no_abort():
    base, monitor = _monitor()
    assert monitor.check(base) is None


def test_monitor_aborts_on_event_deltas():
    base, monitor = _monitor()
    assert "max" in monitor.check(_set(_base_snapshot(), **{"events.max": 1})).lower()
    base2, monitor2 = _monitor()
    assert "oom" in monitor2.check(_set(_base_snapshot(), **{"events.oom": 1})).lower()
    base3, monitor3 = _monitor()
    assert "oom_kill" in monitor3.check(_set(_base_snapshot(), **{"events.oom_kill": 1})).lower()


def test_monitor_aborts_on_persistent_psi_full():
    base, monitor = _monitor()
    high = _set(_base_snapshot(), **{"pressure.full_avg10": 11.0})
    assert monitor.check(high) is None
    assert monitor.check(high) is None
    reason = monitor.check(high)
    assert reason is not None and "full" in reason.lower()


def test_monitor_psi_full_counter_resets():
    base, monitor = _monitor()
    high = _set(_base_snapshot(), **{"pressure.full_avg10": 11.0})
    monitor.check(high)
    monitor.check(high)
    monitor.check(_base_snapshot())  # clears
    assert monitor.check(high) is None


def test_monitor_aborts_on_persistent_psi_some():
    base, monitor = _monitor()
    high = _set(_base_snapshot(), **{"pressure.some_avg10": 26.0})
    monitor.check(high)
    monitor.check(high)
    reason = monitor.check(high)
    assert reason is not None and "some" in reason.lower()


def test_monitor_aborts_on_committed_floor_growth():
    base, monitor = _monitor()
    snap = _set(_base_snapshot(), memory_current=87 * GIB + 7 * GIB)
    reason = monitor.check(snap)
    assert reason is not None and "committed" in reason.lower()


def test_monitor_aborts_on_low_effective_headroom():
    base, monitor = _monitor()
    # no clean cache -> committed floor ~ current -> effective headroom ~ 1 GiB
    snap = _set(_base_snapshot(), **{"stat.file": 0})
    reason = monitor.check(snap)
    assert reason is not None and "effective" in reason.lower()


def test_monitor_aborts_near_max_with_rising_full_psi():
    base = _set(_base_snapshot(), **{"pressure.full_avg10": 2.0})
    monitor = gate.MemoryMonitor(base)
    snap = _set(_base_snapshot(), memory_current=90 * GIB - 128 * MIB,
                **{"pressure.full_avg10": 3.0})
    reason = monitor.check(snap)
    assert reason is not None and "near" in reason.lower()


def test_monitor_ignores_expected_reclaim_signals():
    base, monitor = _monitor()
    snap = _set(_base_snapshot(), memory_current=80 * GIB,
                **{"events.high": 2_000_000, "stat.file": 78 * GIB})
    assert monitor.check(snap) is None


# ---------------------------------------------------------------------------
# Guarded subprocess (parent/child orchestration, no real model)
# ---------------------------------------------------------------------------


def test_run_guarded_subprocess_success_dummy_child():
    result = gate.run_guarded_subprocess(
        ["/bin/sh", "-c", "true"],
        snapshot_provider=lambda: _base_snapshot(),
        poll_s=0.05,
    )
    assert result["returncode"] == 0
    assert result["abort_reason"] is None
    assert result["gate"]["mode"] == "cache_guarded"


def test_run_guarded_subprocess_blocks_on_bad_baseline():
    bad = _set(_base_snapshot(), **{"events.oom_kill": 1})
    with pytest.raises(SystemExit):
        gate.run_guarded_subprocess(
            ["/bin/sh", "-c", "true"], snapshot_provider=lambda: bad
        )


def test_run_guarded_subprocess_aborts_and_terminates_on_oom():
    base = _base_snapshot()
    abort = _set(_base_snapshot(), **{"events.oom": 1})
    calls = {"n": 0}

    def provider():
        value = base if calls["n"] == 0 else abort
        calls["n"] += 1
        return value

    result = gate.run_guarded_subprocess(
        ["/bin/sh", "-c", "sleep 5"],
        snapshot_provider=provider,
        poll_s=0.02,
        timeout_s=5,
    )
    assert result["abort_reason"] is not None
    assert "oom" in result["abort_reason"].lower()
    assert result["returncode"] != 0


def test_run_guarded_subprocess_does_not_signal_unverified_pid():
    base = _base_snapshot()
    abort = _set(_base_snapshot(), **{"events.oom_kill": 1})
    calls = {"n": 0}

    def provider():
        value = base if calls["n"] == 0 else abort
        calls["n"] += 1
        return value

    result = gate.run_guarded_subprocess(
        ["/bin/sh", "-c", "sleep 1"],
        snapshot_provider=provider,
        pid_matcher=lambda pid: False,  # identity cannot be proven
        poll_s=0.02,
        timeout_s=5,
    )
    assert "unverified" in result["abort_reason"].lower()
