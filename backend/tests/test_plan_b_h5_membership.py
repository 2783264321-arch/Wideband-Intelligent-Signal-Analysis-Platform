"""Plan B H5: exact 16+16+8 execution membership (CPU-only, real SpaceNet metadata).

Exercises the SAME membership-selection helper the real H5 campaign will use:
registers the H5_FULL and H5_TAIL8 dataset views in a temp DB and asserts the
prepared manifest membership is exact per cycle. No IQ copy, no inference.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import plan_b_common as common  # noqa: E402
import plan_b_h5_membership as membership  # noqa: E402

_SPACENET_PRESENT = (common.SPACENET_ROOT / "test" / "0.bin").is_file()


def test_cycle_partition_is_16_16_8() -> None:
    cycles = membership.h5_cycles()
    assert [c.index for c in cycles] == [0, 1, 2]
    assert [c.expected_items for c in cycles] == [16, 16, 8]
    assert cycles[0].dataset_name == membership.H5_FULL
    assert cycles[1].dataset_name == membership.H5_FULL
    assert cycles[2].dataset_name == membership.H5_TAIL8
    assert cycles[2].stems == tuple(common.H2_STEMS[:8])
    assert membership.total_expected_executions() == 40


def test_full_and_tail8_are_distinct_views() -> None:
    full = membership.h5_cycles()[0]
    tail = membership.h5_cycles()[2]
    assert full.dataset_name != tail.dataset_name
    assert len(full.stems) == 16 and len(tail.stems) == 8
    assert set(tail.stems).issubset(set(full.stems))


@pytest.mark.skipif(not _SPACENET_PRESENT, reason="SpaceNet dataset unavailable")
def test_real_membership_is_exact_per_cycle(tmp_path: Path) -> None:
    from app.core.config import Settings
    from app.main import create_app

    settings = Settings(
        project_root=REPO,
        data_root=tmp_path / "data",
        label_space_root=REPO / "label_spaces",
        database_url=f"sqlite:///{tmp_path / 'h5mem.db'}",
    )
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    app = create_app(settings)

    membership.register_dataset_view(app, dataset_name=membership.H5_FULL,
                                     stems=tuple(common.H2_STEMS))
    membership.register_dataset_view(app, dataset_name=membership.H5_TAIL8,
                                     stems=tuple(common.H2_STEMS[:8]))

    reports = [membership.assert_cycle_membership(app, cycle) for cycle in membership.h5_cycles()]
    assert [r["expected_items"] for r in reports] == [16, 16, 8]
    assert sorted(reports[2]["names"]) == sorted(common.H2_STEMS[:8])
    # The tail view must never silently resolve 16.
    assert reports[2]["expected_items"] != 16
    assert sum(r["expected_items"] for r in reports) == 40
