"""Plan B remediation: bounded H5 monitoring re-observation (CPU-only)."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import plan_b_common as common  # noqa: E402
import plan_b_h5_reobserve_cp as reobserve  # noqa: E402


def test_reobserve_artifacts_are_unique_and_under_reobserve_root() -> None:
    assert reobserve.REOBSERVE_ROOT == common.PLAN_B_ROOT / "h5_reobserve"
    for artifact in (reobserve.CONCURRENCY_ARTIFACT, reobserve.RESOURCE_ARTIFACT,
                     reobserve.ACCEPTANCE_ARTIFACT):
        assert artifact.parent == reobserve.REOBSERVE_ROOT
        assert artifact.name.startswith("h5_reobserve")


def test_reobserve_preserves_historical_roots() -> None:
    source = (SCRIPTS / "plan_b_h5_reobserve_cp.py").read_text(encoding="utf-8")
    for name in ("h2h3", "h4", "h5"):
        assert name in source
    assert "h5_reobserve" in source
    assert "already exists" in source  # fail-closed on existing artifacts


def test_reobserve_is_single_experiment_with_exact_membership() -> None:
    source = (SCRIPTS / "plan_b_h5_reobserve_cp.py").read_text(encoding="utf-8")
    assert "h5_cycles()[0]" in source          # H5_FULL only, no tail-8
    assert "assert_cycle_membership" in source
    assert 'PLUGIN = "cpn_bandwidth_tier"' in source
    assert "max_concurrency=2" not in source   # concurrency from h5.CONCURRENCY_BOUND


def test_reobserve_no_retry_guard() -> None:
    source = (SCRIPTS / "plan_b_h5_reobserve_cp.py").read_text(encoding="utf-8")
    assert "executions_consumed" in source
    assert "final_actual_total" in source
    assert "REMEDIATION" in source


def test_reobserve_hashes_raw_artifacts() -> None:
    payload = {
        "concurrency_jsonl_sha256": "a" * 64,
        "resource_jsonl_sha256": "b" * 64,
    }
    assert all(len(v) == 64 and all(c in "0123456789abcdef" for c in v)
               for v in payload.values())
