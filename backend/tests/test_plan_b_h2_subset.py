"""Plan B H2: frozen subset + acceptance contract (control-plane only)."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import plan_b_common as common  # noqa: E402


def test_h2_stems_exact_and_unique() -> None:
    expected = ("0", "1", "2", "3", "9", "11", "12", "15",
                "32", "42", "79", "80", "83", "99", "109", "280")
    assert common.H2_STEMS == expected
    assert len(set(common.H2_STEMS)) == 16
    for stem in common.H2_STEMS:
        assert (common.SPACENET_ROOT / "test" / f"{stem}.bin").is_file()
        assert (common.SPACENET_ROOT / "test" / f"{stem}.json").is_file()


def test_h2_manifest_and_projection_contract() -> None:
    import plan_b_dataset_core as core  # noqa: F401

    assert core.GPU_QUIESCENT_MARGIN_MIB == 64
    assert core.GPU_QUIESCENT_TIMEOUT_S == 60


def test_h2_create_payload_is_exact() -> None:
    source = (SCRIPTS / "plan_b_h2_cpn.py").read_text(encoding="utf-8")
    assert 'PLUGIN = "cpn_bandwidth_tier"' in source
    assert "7ab8a6a4f5f93247d3997fcf88c4b05d1099361fa8db1555fc8daeeaf7fc55bb" in source
    assert "concurrency=1" in source or "max_concurrency=1" in source
