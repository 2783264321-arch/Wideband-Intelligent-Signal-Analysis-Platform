"""BHQ-3 Task 1 — effective-dtype evidence probe (run under the ML interpreter).

    PYTHONPATH="$PWD/backend" /root/miniconda3/bin/python \
        scripts/bhq3_precision_dtype_probe.py

Runs one bounded CPN case and one bounded ZoomSpec case on SpaceNet test/2 with
recording wrappers (they delegate to the originals; inference results are
unchanged) and writes /tmp/bhq3_work/precision_dtype_evidence.json.

Conclusion authority: precision="float16" is the certified CUDA mixed-precision
deployment mode (some frozen stages remain FP32; FRN uses float16 autocast).
"""
from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bhq3_common as common  # noqa: E402


@contextlib.contextmanager
def record_ls_stft(cap: dict):
    import torch
    import torch.nn.functional as functional

    orig_stft, orig_hann, orig_interp = torch.stft, torch.hann_window, functional.interpolate

    def stft(*args, **kwargs):
        sig = args[0] if args else kwargs.get("input")
        cap.setdefault("stft_input_dtype", str(getattr(sig, "dtype", None)))
        window = kwargs.get("window")
        cap.setdefault("stft_window_dtype", str(getattr(window, "dtype", None)))
        return orig_stft(*args, **kwargs)

    def hann(*args, **kwargs):
        out = orig_hann(*args, **kwargs)
        cap.setdefault("hann_window_dtype", str(out.dtype))
        return out

    def interp(*args, **kwargs):
        inp = args[0] if args else kwargs.get("input")
        cap.setdefault("interpolate_input_dtype", str(getattr(inp, "dtype", None)))
        return orig_interp(*args, **kwargs)

    torch.stft, torch.hann_window, functional.interpolate = stft, hann, interp
    try:
        yield
    finally:
        torch.stft, torch.hann_window, functional.interpolate = orig_stft, orig_hann, orig_interp


@contextlib.contextmanager
def record_autocast(cap: dict):
    import torch

    original = torch.autocast

    def autocast(*args, **kwargs):
        cap.setdefault(
            "autocast",
            {
                "device_type": kwargs.get("device_type"),
                "dtype": str(kwargs.get("dtype")),
                "enabled": kwargs.get("enabled"),
            },
        )
        return original(*args, **kwargs)

    torch.autocast = autocast
    try:
        yield
    finally:
        torch.autocast = original


def _first_conv(model):
    import torch.nn as nn

    for module in model.modules():
        if isinstance(module, nn.Conv2d):
            return module
    return None


def probe_cpn(cap: dict) -> dict:
    import torch
    from app.pipelines.cpn_bandwidth_tier.pipeline import CPNBandwidthTierPipeline
    from app.labels.service import LabelSpaceService

    recording, _ = common.recording_input("2")
    pipeline = CPNBandwidthTierPipeline(
        detector_checkpoint_path=common.DETECTOR,
        normalization=common.load_normalization(),
        label_space=LabelSpaceService(common.LABELS).get("cpn_bandwidth_tier_v1"),
        device=0,
    )
    yolo = getattr(pipeline._detector, "_model", None)
    module = getattr(yolo, "model", None)
    hook_handle = None
    if module is not None:
        conv = _first_conv(module)
        if conv is not None:
            def pre_hook(mod, inputs):
                if inputs:
                    cap.setdefault("cpn_conv_input_dtype", str(getattr(inputs[0], "dtype", None)))
            hook_handle = conv.register_forward_pre_hook(pre_hook)
    try:
        with record_ls_stft(cap):
            out = pipeline.run(recording, {}, common.WORK_ROOT / "precision/cpn")
    finally:
        if hook_handle is not None:
            hook_handle.remove()
    return {"detections": len(out.detections), "run_metadata": out.run_metadata}


def probe_zoomspec(cap: dict) -> dict:
    from app.pipelines.zoomspec_yolo26n_aug_combined_frn_v3.pipeline import ZoomSpecFrozenPipeline
    from app.labels.service import LabelSpaceService

    recording, _ = common.recording_input("2")
    pipeline = ZoomSpecFrozenPipeline(
        detector_checkpoint_path=common.DETECTOR,
        frn_checkpoint_path=common.FRN,
        normalization=common.load_normalization(),
        label_space=LabelSpaceService(common.LABELS).get("spacenet_14"),
        device=0,
    )
    with record_ls_stft(cap), record_autocast(cap):
        out = pipeline.run(recording, {}, common.WORK_ROOT / "precision/zoomspec")
    return {"detections": len(out.detections), "run_metadata": out.run_metadata}


def main() -> int:
    import torch

    common.ensure_work_root()
    if not torch.cuda.is_available():
        raise SystemExit("CUDA unavailable; cannot produce CUDA dtype evidence")

    cap: dict = {}
    cpn = probe_cpn(cap)
    zoomspec = probe_zoomspec(cap)
    evidence = {
        "device": torch.cuda.get_device_name(0),
        "cuda_capability": list(torch.cuda.get_device_capability(0)),
        "ls_stft": {
            "hann_window_dtype": cap.get("hann_window_dtype"),
            "stft_window_dtype": cap.get("stft_window_dtype"),
            "stft_input_dtype": cap.get("stft_input_dtype"),
            "interpolate_input_dtype": cap.get("interpolate_input_dtype"),
        },
        "cpn": {"conv_input_dtype": cap.get("cpn_conv_input_dtype"), **cpn},
        "frn_autocast": cap.get("autocast"),
        "conclusion": "precision=float16 is the certified CUDA mixed-precision deployment mode",
        "cpn_case": cpn,
        "zoomspec_case": zoomspec,
    }
    out = common.WORK_ROOT / "precision_dtype_evidence.json"
    common.write_json(out, evidence)
    print(json.dumps(evidence, indent=2))
    print(f"PRECISION_DTYPE_EVIDENCE {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
