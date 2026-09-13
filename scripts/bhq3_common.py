"""BHQ-3 acceptance tooling — shared helpers (acceptance-only, NOT production).

All BHQ-3 scripts import from here. Nothing in this package is used by the
production control plane or the local inference worker.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
LABELS = REPO / "label_spaces"
PIPELINES = BACKEND / "app" / "pipelines"
CERT_PATH = PIPELINES / "execution_certificates.json"

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

SPACENET_ROOT = Path("/root/autodl-tmp/SpaceNet_Dataset/advanced")
DETECTOR = Path("/root/autodl-tmp/release/weights/yolo26n_ls_stft_aug_best.pt")
NORMALIZATION = Path("/root/autodl-tmp/release/support/normalization_ls_stft.json")
FRN = Path("/root/autodl-tmp/Claude/artifacts/frn_combined_v3_training/best.pt")
FROZEN_CONFIG = Path("/root/autodl-tmp/Claude/configs/frozen_full_pipeline_v26_aug_combined.yaml")

ML_PYTHON = Path("/root/miniconda3/bin/python")
GPU_RUNTIME_REF = "local:autodl_primary:gpu:7b958347b5af"
WORK_ROOT = Path("/tmp/bhq3_work")
GIB = 1024 ** 3
MIN_HEADROOM_GIB = 4

PLUGIN_VERSION = {
    "cpn_bandwidth_tier": "1.0.0",
    "zoomspec_yolo26n_aug_combined_frn_v3": "1.0.0",
}
OUTPUT_LABEL_SPACE = {
    "cpn_bandwidth_tier": "cpn_bandwidth_tier_v1",
    "zoomspec_yolo26n_aug_combined_frn_v3": "spacenet_14",
}
MANIFEST_PATH = {
    "cpn_bandwidth_tier": PIPELINES / "cpn_bandwidth_tier" / "asset_manifest.json",
    "zoomspec_yolo26n_aug_combined_frn_v3": PIPELINES
    / "zoomspec_yolo26n_aug_combined_frn_v3"
    / "asset_manifest.json",
}
ASSET_PATHS = {
    "detector_checkpoint": DETECTOR,
    "ls_stft_normalization": NORMALIZATION,
    "frn_checkpoint": FRN,
    "frozen_config": FROZEN_CONFIG,
}


def ensure_work_root() -> Path:
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    return WORK_ROOT


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_assets(plugin_id: str) -> dict:
    """Repo manifest parser + verify_assets + independent sha256; fail closed."""
    from app.remote_execution.assets import load_pipeline_asset_manifest, verify_assets as _va

    manifest = load_pipeline_asset_manifest(MANIFEST_PATH[plugin_id])
    paths = {name: ASSET_PATHS[name] for name in manifest.assets}
    _va(manifest, paths)
    return {
        "asset_manifest_sha256": manifest.asset_manifest_sha256,
        "assets": {name: sha256_file(paths[name]) for name in manifest.assets},
    }


def load_normalization():
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.preprocessing import LSSTFTNormalization

    payload = json.loads(NORMALIZATION.read_text(encoding="utf-8"))
    return LSSTFTNormalization(
        percentile_low=float(payload["percentile_low"]),
        percentile_high=float(payload["percentile_high"]),
        value_low=float(payload["value_low"]),
        value_high=float(payload["value_high"]),
    )


def recording_input(stem: str):
    """Real RecordingInput derived from the production SpaceNetAdapter."""
    from app.datasets.spacenet import SpaceNetAdapter
    from app.pipelines.base import RecordingInput

    adapter = SpaceNetAdapter(SPACENET_ROOT, LABELS, label_space_id="spacenet_14")
    sample = adapter.load("test", stem)
    if sample.num_samples <= 0:
        raise SystemExit(f"stem {stem}: zero IQ")
    recording = RecordingInput(
        id=sample.id,
        data_path=sample.data_path,
        data_format=sample.data_format,
        sample_rate_hz=sample.sample_rate_hz,
        center_frequency_hz=sample.center_frequency_hz,
        frequency_low_hz=sample.frequency_low_hz,
        frequency_high_hz=sample.frequency_high_hz,
        duration_s=sample.duration_s,
        label_space="spacenet_14",
    )
    return recording, sample


def cgroup_headroom_bytes() -> int:
    maximum = int(Path("/sys/fs/cgroup/memory.max").read_text().strip())
    current = int(Path("/sys/fs/cgroup/memory.current").read_text().strip())
    return maximum - current


def require_headroom_gib(minimum_gib: int = MIN_HEADROOM_GIB) -> int:
    """Mandatory pre-launch cgroup gate. STOP BHQ_3_BLOCKED_BY_CGROUP_MEMORY."""
    headroom = cgroup_headroom_bytes()
    if headroom < minimum_gib * GIB:
        raise SystemExit(
            f"BHQ_3_BLOCKED_BY_CGROUP_MEMORY: headroom={headroom} < {minimum_gib} GiB"
        )
    return headroom


def gpu_compute_apps() -> list:
    import subprocess

    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15,
        )
    except Exception:
        return []
    if result.returncode != 0:
        return []
    out = []
    for line in result.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 2 and parts[0].isdigit():
            out.append((int(parts[0]), int(parts[1])))
    return out


def proc_peak_rss_kb(pid: int) -> int | None:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmHWM:"):
                return int(line.split()[1])
    except (OSError, ValueError):
        return None
    return None


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def minimal_env_for_backend() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(BACKEND) + os.pathsep + env.get("PYTHONPATH", "")
    return env
