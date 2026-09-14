"""Plan B H3: comparison contract + H2 reuse (control-plane only)."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def test_h3_reuses_h2_experiment_a() -> None:
    source = (SCRIPTS / "plan_b_h3_zoomspec.py").read_text(encoding="utf-8")
    assert "H2_EXPERIMENT_ID" in source
    assert "cpn_bandwidth_tier" in source  # only guarding the H2 experiment, never creating a new CPN one
    assert "create_experiment(app, plugin_id=PLUGIN" in source
    assert 'PLUGIN = "zoomspec_yolo26n_aug_combined_frn_v3"' in source


def test_h3_manifest_equality_gate_present() -> None:
    source = (SCRIPTS / "plan_b_h3_zoomspec.py").read_text(encoding="utf-8")
    assert "manifest_equal" in source
    assert "16cc0534ed61603a84142da8a04af6642f9e7661848835fe5473199bec38ac08" in source


def test_h3_compare_delta_contract() -> None:
    source = (SCRIPTS / "plan_b_h3_zoomspec.py").read_text(encoding="utf-8")
    for required in ("localization_ap50", "localization_ap50_95",
                     "class_aware_map50", "class_aware_map50_95", "matched_accuracy"):
        assert required in source
    assert "algorithm-lab/compare" in source
