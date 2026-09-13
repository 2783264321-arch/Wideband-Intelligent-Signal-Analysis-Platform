"""BHQ-3 C5 — real local_gpu acceptance harness (opt-in; skips when unavailable).

The full acceptance (CPN + ZoomSpec, stems 2/0) is run by the operator via
`scripts/bhq3_local_gpu_acceptance.py`. This test is a bounded, opt-in guard that
runs the CPN stem-2 case through the same committed harness when the ML runtime,
CUDA GPU, golden assets, and >=4 GiB cgroup headroom are all available.

Enable with WSP_BHQ3_REAL_GPU_ACCEPTANCE=1; otherwise it skips.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
ML_PYTHON = Path("/root/miniconda3/bin/python")
ASSETS = (
    Path("/root/autodl-tmp/release/weights/yolo26n_ls_stft_aug_best.pt"),
    Path("/root/autodl-tmp/release/support/normalization_ls_stft.json"),
    Path("/root/autodl-tmp/Claude/artifacts/frn_combined_v3_training/best.pt"),
    Path("/root/autodl-tmp/Claude/configs/frozen_full_pipeline_v26_aug_combined.yaml"),
)


def _cuda_available() -> bool:
    try:
        result = subprocess.run(
            [str(ML_PYTHON), "-c", "import torch; print(torch.cuda.is_available())"],
            capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and result.stdout.strip() == "True"


def _headroom_ok() -> bool:
    maximum = int(Path("/sys/fs/cgroup/memory.max").read_text().strip())
    current = int(Path("/sys/fs/cgroup/memory.current").read_text().strip())
    return (maximum - current) >= 4 * 1024 ** 3


def test_real_local_gpu_acceptance_harness_cpn_stem2():
    if os.environ.get("WSP_BHQ3_REAL_GPU_ACCEPTANCE") != "1":
        pytest.skip("WSP_BHQ3_REAL_GPU_ACCEPTANCE=1 not set")
    if not ML_PYTHON.is_file():
        pytest.skip("ML interpreter unavailable")
    if not all(path.is_file() for path in ASSETS):
        pytest.skip("golden assets unavailable")
    if not _cuda_available():
        pytest.skip("CUDA unavailable in the ML interpreter")
    if not _headroom_ok():
        pytest.skip("cgroup headroom < 4 GiB")

    env = dict(os.environ)
    env["PYTHONPATH"] = str(BACKEND) + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts" / "bhq3_local_gpu_acceptance.py"),
            "--plugin",
            "cpn_bandwidth_tier",
            "--stem",
            "2",
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO),
        timeout=1800,
    )
    assert result.returncode == 0, result.stderr
    assert "completed" in result.stdout
