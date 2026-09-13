"""BHQ-3 Amendment A1 — cache-aware memory admission gate (acceptance-only).

This is BHQ acceptance tooling, NOT production. It distinguishes genuine
non-reclaimable / anonymous memory pressure from high ``memory.current`` caused
mainly by clean, reclaimable file page cache, while remaining fail-closed.

Accounting model (deliberately conservative; total minus explicitly-clean):

    clean_file_cache   = max(file - shmem - file_dirty - file_writeback, 0)
    committed_floor    = max(memory.current - clean_file_cache, 0)
    raw_headroom       = memory.max - memory.current
    effective_headroom = memory.max - committed_floor
    clean_cache_ratio  = clean_file_cache / memory.current   (0.0 if current == 0)

``memory.stat:file`` includes tmpfs/shared memory, so ``shmem`` is NOT treated as
clean cache; dirty/writeback pages are NOT treated as immediately reclaimable;
``slab_reclaimable`` is deliberately NOT subtracted. Every other kernel/unknown
component stays implicitly charged inside ``committed_floor`` via
``memory.current`` (no additive whitelist that could omit future accounting).

Admission:
  Path A (raw):   raw_headroom >= 4 GiB                         -> mode "raw"
  Path B (guarded, only when Path A fails): all conditions hold -> "cache_guarded"
  otherwise                                                     -> "block"
Any missing/unparseable mandatory field fails closed.
"""
from __future__ import annotations

from pathlib import Path
import subprocess
import time

GIB = 1024 ** 3
MIB = 1024 ** 2

DEFAULT_CONFIG: dict = {
    # Path A
    "raw_headroom_gib": 4,
    # Path B
    "committed_floor_max_gib": 16,
    "effective_headroom_min_gib": 6,
    "clean_cache_ratio_min": 0.5,
    "dirty_writeback_max_mib": 512,
    "unevictable_max_mib": 256,
    "shmem_max_gib": 2,
    "psi_some_avg10_max": 5.0,
    "psi_full_avg10_max": 1.0,
    "host_memavailable_min_gib": 64,
    # live monitor
    "monitor_psi_full_avg10_max": 10.0,
    "monitor_psi_some_avg10_max": 25.0,
    "monitor_consecutive": 3,
    "monitor_committed_floor_delta_gib": 6,
    "monitor_effective_headroom_min_gib": 4,
    "monitor_near_max_bytes": 256 * MIB,
}

# Mandatory: required for accounting/thresholds. Unknown extra stat fields are
# still parsed but never required (they remain implicitly inside committed_floor).
MANDATORY_STAT = ("file", "shmem", "file_dirty", "file_writeback", "unevictable")
MANDATORY_EVENTS = ("max", "oom", "oom_kill")


class _Bad(Exception):
    pass


def _cfg(config) -> dict:
    merged = dict(DEFAULT_CONFIG)
    if config:
        merged.update(config)
    return merged


def _as_int(value, name: str) -> int:
    if value is None:
        raise _Bad(f"{name} is missing")
    if isinstance(value, bool):
        raise _Bad(f"{name} must be an integer, not bool")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if text == "max":
            raise _Bad(f"{name} is 'max' (unbounded; cache-aware admission N/A)")
        try:
            return int(text)
        except ValueError:
            raise _Bad(f"{name} is not an integer: {text!r}")
    raise _Bad(f"{name} is not an integer")


def _as_float(value, name: str) -> float:
    if value is None:
        raise _Bad(f"{name} is missing")
    if isinstance(value, bool):
        raise _Bad(f"{name} must be a number, not bool")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            raise _Bad(f"{name} is not a number: {value!r}")
    raise _Bad(f"{name} is not a number")


def _stat(snapshot, name: str) -> int:
    stat = snapshot.get("stat")
    if not isinstance(stat, dict):
        raise _Bad("memory.stat is missing")
    return _as_int(stat.get(name), f"stat.{name}")


def _event(snapshot, name: str) -> int:
    events = snapshot.get("events")
    if not isinstance(events, dict):
        raise _Bad("memory.events is missing")
    return _as_int(events.get(name), f"events.{name}")


def _pressure(snapshot, name: str) -> float:
    pressure = snapshot.get("pressure")
    if not isinstance(pressure, dict):
        raise _Bad("memory.pressure is missing")
    return _as_float(pressure.get(name), f"pressure.{name}")


def derive(snapshot) -> dict:
    """Pure derived metrics. Raises ``_Bad`` on any missing/malformed input."""
    memory_max = _as_int(snapshot.get("memory_max"), "memory.max")
    memory_current = _as_int(snapshot.get("memory_current"), "memory.current")
    file_bytes = _stat(snapshot, "file")
    shmem = _stat(snapshot, "shmem")
    file_dirty = _stat(snapshot, "file_dirty")
    file_writeback = _stat(snapshot, "file_writeback")

    clean_file_cache = max(file_bytes - shmem - file_dirty - file_writeback, 0)
    committed_floor = max(memory_current - clean_file_cache, 0)
    raw_headroom = memory_max - memory_current
    effective_headroom = memory_max - committed_floor
    clean_cache_ratio = (clean_file_cache / memory_current) if memory_current > 0 else 0.0
    return {
        "memory_max": memory_max,
        "memory_current": memory_current,
        "clean_file_cache": clean_file_cache,
        "committed_floor": committed_floor,
        "raw_headroom": raw_headroom,
        "effective_headroom": effective_headroom,
        "clean_cache_ratio": clean_cache_ratio,
        "file_dirty": file_dirty,
        "file_writeback": file_writeback,
        "dirty_writeback": file_dirty + file_writeback,
    }


def evaluate(snapshot, config=DEFAULT_CONFIG) -> dict:
    """Pure two-path admission evaluation. Never raises; fails closed."""
    cfg = _cfg(config)
    empty = {"mode": "block", "reasons": [], "checks": {}, "derived": {}, "thresholds": cfg}
    if not isinstance(snapshot, dict):
        empty["reasons"] = ["missing/malformed required field: snapshot is not a dict"]
        return empty
    try:
        derived = derive(snapshot)
        memory_low = _as_int(snapshot.get("memory_low"), "memory.low")
        memory_min = _as_int(snapshot.get("memory_min"), "memory.min")
        events = {name: _event(snapshot, name) for name in MANDATORY_EVENTS}
        psi_some = _pressure(snapshot, "some_avg10")
        psi_full = _pressure(snapshot, "full_avg10")
        unevictable = _stat(snapshot, "unevictable")
        shmem = _stat(snapshot, "shmem")
        mem_available = _as_int(snapshot.get("mem_available_bytes"), "mem_available_bytes")
    except _Bad as exc:
        empty["reasons"] = [f"missing/malformed required field: {exc}"]
        return empty

    # Path A — ordinary raw pass (unchanged).
    if derived["raw_headroom"] >= cfg["raw_headroom_gib"] * GIB:
        return {
            "mode": "raw",
            "reasons": [f"raw_headroom {derived['raw_headroom']} >= {cfg['raw_headroom_gib']} GiB (Path A)"],
            "checks": {"raw_headroom": True},
            "derived": derived,
            "thresholds": cfg,
        }

    # Path B — guarded cache-dominant admission.
    checks: dict = {}
    reasons: list = []

    def chk(name: str, ok: bool, message: str) -> None:
        checks[name] = ok
        if not ok:
            reasons.append(message)

    chk("committed_floor",
        derived["committed_floor"] <= cfg["committed_floor_max_gib"] * GIB,
        f"committed_floor {derived['committed_floor']} B > {cfg['committed_floor_max_gib']} GiB")
    chk("effective_headroom",
        derived["effective_headroom"] >= cfg["effective_headroom_min_gib"] * GIB,
        f"effective_headroom {derived['effective_headroom']} B < {cfg['effective_headroom_min_gib']} GiB")
    chk("clean_cache_ratio",
        derived["clean_cache_ratio"] >= cfg["clean_cache_ratio_min"],
        f"clean_file_cache ratio {derived['clean_cache_ratio']:.4f} < {cfg['clean_cache_ratio_min']}")
    chk("dirty_writeback",
        derived["dirty_writeback"] <= cfg["dirty_writeback_max_mib"] * MIB,
        f"file_dirty+file_writeback {derived['dirty_writeback']} B > {cfg['dirty_writeback_max_mib']} MiB")
    chk("unevictable",
        unevictable <= cfg["unevictable_max_mib"] * MIB,
        f"unevictable {unevictable} B > {cfg['unevictable_max_mib']} MiB")
    chk("shmem",
        shmem <= cfg["shmem_max_gib"] * GIB,
        f"shmem {shmem} B > {cfg['shmem_max_gib']} GiB")
    chk("memory_low_zero", memory_low == 0, f"memory.low != 0 ({memory_low})")
    chk("memory_min_zero", memory_min == 0, f"memory.min != 0 ({memory_min})")
    chk("events_max_zero", events["max"] == 0, f"memory.events.max > 0 ({events['max']})")
    chk("events_oom_zero", events["oom"] == 0, f"memory.events.oom > 0 ({events['oom']})")
    chk("events_oom_kill_zero", events["oom_kill"] == 0,
        f"memory.events.oom_kill > 0 ({events['oom_kill']})")
    chk("psi_some", psi_some <= cfg["psi_some_avg10_max"],
        f"PSI some avg10 {psi_some} > {cfg['psi_some_avg10_max']}")
    chk("psi_full", psi_full <= cfg["psi_full_avg10_max"],
        f"PSI full avg10 {psi_full} > {cfg['psi_full_avg10_max']}")
    chk("host_memavailable", mem_available >= cfg["host_memavailable_min_gib"] * GIB,
        f"host MemAvailable {mem_available} B < {cfg['host_memavailable_min_gib']} GiB")

    if not reasons:
        return {
            "mode": "cache_guarded",
            "reasons": ["all Path B guards satisfied"],
            "checks": checks,
            "derived": derived,
            "thresholds": cfg,
        }
    return {"mode": "block", "reasons": reasons, "checks": checks, "derived": derived, "thresholds": cfg}


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _parse_kv(path: Path) -> dict:
    text = _read_text(path)
    result: dict = {}
    if not text:
        return result
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            try:
                result[parts[0]] = int(parts[1])
            except ValueError:
                continue
    return result


def _parse_scalar(path: Path):
    text = _read_text(path)
    if text is None:
        return None
    text = text.strip()
    if text == "max":
        return "max"
    try:
        return int(text)
    except ValueError:
        return None


def _parse_pressure(path: Path) -> dict:
    text = _read_text(path)
    result: dict = {}
    if not text:
        return result
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        kind = parts[0]
        for token in parts[1:]:
            if "=" in token:
                key, value = token.split("=", 1)
                try:
                    result[f"{kind}_{key}"] = float(value)
                except ValueError:
                    continue
    return result


def _parse_memavailable(path: Path):
    text = _read_text(path)
    if not text:
        return None
    for line in text.splitlines():
        if line.startswith("MemAvailable:"):
            parts = line.split()
            if len(parts) >= 2:
                try:
                    return int(parts[1]) * 1024  # kB -> bytes
                except ValueError:
                    return None
    return None


def read_snapshot(cgroup_root=None, meminfo=None) -> dict:
    """Read cgroup + /proc/meminfo state. Missing files become None/{}, never raise."""
    root = Path(cgroup_root) if cgroup_root is not None else Path("/sys/fs/cgroup")
    meminfo_path = Path(meminfo) if meminfo is not None else Path("/proc/meminfo")
    snapshot = {
        "memory_max": _parse_scalar(root / "memory.max"),
        "memory_current": _parse_scalar(root / "memory.current"),
        "memory_high": _parse_scalar(root / "memory.high"),
        "memory_low": _parse_scalar(root / "memory.low"),
        "memory_min": _parse_scalar(root / "memory.min"),
        "swap_current": _parse_scalar(root / "memory.swap.current"),
        "swap_max": _parse_scalar(root / "memory.swap.max"),
        "stat": _parse_kv(root / "memory.stat"),
        "events": _parse_kv(root / "memory.events"),
        "events_local": _parse_kv(root / "memory.events.local"),
        "pressure": _parse_pressure(root / "memory.pressure"),
        "mem_available_bytes": _parse_memavailable(meminfo_path),
    }
    return snapshot


def require_memory_admission(config=None, cgroup_root=None, meminfo=None):
    """Read a fresh snapshot; block with SystemExit if neither path admits."""
    snapshot = read_snapshot(cgroup_root=cgroup_root, meminfo=meminfo)
    result = evaluate(snapshot, config or DEFAULT_CONFIG)
    if result["mode"] == "block":
        raise SystemExit(
            "BHQ_3_BLOCKED_BY_CGROUP_MEMORY: " + "; ".join(result["reasons"])
        )
    return result, snapshot


# ---------------------------------------------------------------------------
# Live monitor
# ---------------------------------------------------------------------------


class MemoryMonitor:
    """Pre-launch baseline + per-sample abort conditions (PSI values are percentages)."""

    def __init__(self, baseline, config=DEFAULT_CONFIG):
        self.cfg = _cfg(config)
        self.baseline = baseline
        try:
            self._base_committed = derive(baseline)["committed_floor"]
        except _Bad:
            self._base_committed = 0
        events = baseline.get("events") if isinstance(baseline, dict) else None
        self._base_events = {
            name: int((events or {}).get(name, 0) or 0) for name in MANDATORY_EVENTS
        }
        pressure = baseline.get("pressure") if isinstance(baseline, dict) else None
        try:
            self._prev_full = float((pressure or {}).get("full_avg10", 0.0))
        except (TypeError, ValueError):
            self._prev_full = 0.0
        self._full_streak = 0
        self._some_streak = 0

    def check(self, snapshot) -> str | None:
        try:
            derived = derive(snapshot)
        except _Bad as exc:
            return f"abort: memory snapshot invalid ({exc})"

        events = snapshot.get("events") if isinstance(snapshot, dict) else None
        for name, needle in (("max", "max"), ("oom", "oom"), ("oom_kill", "oom_kill")):
            current = int((events or {}).get(name, 0) or 0)
            if current - self._base_events.get(name, 0) > 0:
                return f"abort: memory.events.{needle} increased"

        # Effective headroom (before committed-floor growth) so a collapsed cache
        # reports the effective-headroom reason.
        if derived["effective_headroom"] < self.cfg["monitor_effective_headroom_min_gib"] * GIB:
            return "abort: effective_headroom < 4 GiB"

        if (derived["committed_floor"] - self._base_committed) > (
            self.cfg["monitor_committed_floor_delta_gib"] * GIB
        ):
            return "abort: committed_floor rose > 6 GiB above baseline"

        pressure = snapshot.get("pressure") if isinstance(snapshot, dict) else None
        full = float((pressure or {}).get("full_avg10", 0.0) or 0.0)
        some = float((pressure or {}).get("some_avg10", 0.0) or 0.0)

        self._full_streak = self._full_streak + 1 if full > self.cfg["monitor_psi_full_avg10_max"] else 0
        if self._full_streak >= self.cfg["monitor_consecutive"]:
            return "abort: PSI full avg10 persistently > 10.0%"
        self._some_streak = self._some_streak + 1 if some > self.cfg["monitor_psi_some_avg10_max"] else 0
        if self._some_streak >= self.cfg["monitor_consecutive"]:
            return "abort: PSI some avg10 persistently > 25.0%"

        memory_max = derived["memory_max"]
        memory_current = derived["memory_current"]
        if (
            memory_current > memory_max - self.cfg["monitor_near_max_bytes"]
            and full > self._prev_full
        ):
            self._prev_full = full
            return "abort: memory.current near memory.max with rising PSI full"
        self._prev_full = full
        return None


# ---------------------------------------------------------------------------
# Guarded subprocess (acceptance-only parent/child runner)
# ---------------------------------------------------------------------------


def run_guarded_subprocess(
    argv,
    *,
    snapshot_provider=None,
    pid_matcher=None,
    config=DEFAULT_CONFIG,
    poll_s: float = 0.2,
    cgroup_root=None,
    meminfo=None,
    timeout_s: float | None = None,
):
    """Run one acceptance child under admission + live monitoring.

    - evaluates the two-path gate on a fresh baseline (blocks with SystemExit);
    - launches ``argv`` with ``shell=False``;
    - polls the cgroup snapshot (~poll_s) and aborts on MemoryMonitor conditions;
    - on abort sends SIGTERM to the child only (never SIGKILL). If ``pid_matcher``
      is given and returns False, the child is NOT signalled (identity unproven).
    """
    provider = snapshot_provider or (
        lambda: read_snapshot(cgroup_root=cgroup_root, meminfo=meminfo)
    )
    baseline = provider()
    gate_result = evaluate(baseline, config)
    if gate_result["mode"] == "block":
        raise SystemExit(
            "BHQ_3_BLOCKED_BY_CGROUP_MEMORY: " + "; ".join(gate_result["reasons"])
        )
    monitor = MemoryMonitor(baseline, config)
    proc = subprocess.Popen(argv, shell=False)
    abort_reason = None
    samples = 0
    deadline = (time.time() + timeout_s) if timeout_s else None
    try:
        while proc.poll() is None:
            snapshot = provider()
            samples += 1
            reason = monitor.check(snapshot)
            if reason:
                abort_reason = reason
                break
            if deadline is not None and time.time() > deadline:
                abort_reason = "abort: guarded child exceeded timeout"
                break
            time.sleep(poll_s)
        if abort_reason:
            identity_ok = pid_matcher is None or bool(pid_matcher(proc.pid))
            if identity_ok:
                try:
                    proc.terminate()  # SIGTERM only
                except ProcessLookupError:
                    pass
            if not identity_ok:
                abort_reason += " (child PID identity unverified; not signalled)"
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass  # never SIGKILL
        returncode = proc.wait()
    finally:
        if proc.poll() is None:  # pragma: no cover - safety net
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                pass
    return {
        "returncode": returncode,
        "abort_reason": abort_reason,
        "samples": samples,
        "gate": gate_result,
        "baseline": baseline,
    }
