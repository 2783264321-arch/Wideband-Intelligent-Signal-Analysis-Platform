"""BHQ-3 Task 6 — immutable local-GPU runtime identity (run under the ML interpreter).

    PYTHONPATH="$PWD/backend" /root/miniconda3/bin/python \
        scripts/bhq3_local_gpu_runtime_identity.py

Prints the generation material JSON, the 12-hex generation, and the runtime_ref.
Exits non-zero if the recomputed runtime_ref differs from the expected value.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys

EXPECTED_RUNTIME_REF = "local:autodl_primary:gpu:7b958347b5af"


def _driver_version() -> str:
    try:
        return subprocess.run(
            ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()
    except Exception:
        return "unknown"


def compute_runtime_identity() -> dict:
    import numpy
    import scipy
    import torch
    import ultralytics

    props = torch.cuda.get_device_properties(0)
    material = {
        "python": sys.version.split()[0],
        "torch": torch.__version__,
        "torch_cuda": torch.version.cuda,
        "ultralytics": ultralytics.__version__,
        "numpy": numpy.__version__,
        "scipy": scipy.__version__,
        "device_name": props.name,
        "compute_capability": f"{props.major}.{props.minor}",
        "driver_version": _driver_version(),
        "cuda_available": bool(torch.cuda.is_available()),
    }
    canonical = json.dumps(material, sort_keys=True, separators=(",", ":"))
    generation = hashlib.sha256(canonical.encode()).hexdigest()[:12]
    return {
        "material": material,
        "canonical": canonical,
        "generation": generation,
        "runtime_ref": f"local:autodl_primary:gpu:{generation}",
    }


def main() -> int:
    identity = compute_runtime_identity()
    print(json.dumps(identity, indent=2))
    if identity["runtime_ref"] != EXPECTED_RUNTIME_REF:
        print(
            f"STOP: runtime_ref {identity['runtime_ref']} != expected {EXPECTED_RUNTIME_REF}; "
            "regenerate evidence/certificates (never silently substitute).",
            file=sys.stderr,
        )
        return 1
    print(f"RUNTIME_IDENTITY_OK {identity['runtime_ref']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
