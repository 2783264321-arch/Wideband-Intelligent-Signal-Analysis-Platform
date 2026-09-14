"""Plan B B3 — real GPU runtime qualification / certificate flow.

    PYTHONPATH="$PWD/backend:$PWD/scripts" "$PWD/.venv/bin/python" \
        scripts/plan_b_certificate_flow.py --real

Runs the accepted A3 workflow end to end for the current installation:
    runtime doctor -> bhq3_gpu_v1 / match
    wisa qualify CPN golden local_gpu
    wisa qualify ZoomSpec golden local_gpu
    wisa certificate install --from <evidence_dir>   (expected: already_certified)

NO model inference. This is operator/runtime qualification only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plan_b_common as common  # noqa: E402

PLUGINS = (
    ("cpn_bandwidth_tier", "1.0.0"),
    ("zoomspec_yolo26n_aug_combined_frn_v3", "1.0.0"),
)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_cli(argv: list[str]) -> tuple[int, dict]:
    """Invoke the operator CLI as a real subprocess (`python -m app.cli ...`).

    Uses the SAME environment already applied by ``bootstrap_env_before_app_import``
    so the operator command runs against the Plan-B configuration exactly as an
    operator would run it.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = str(common.BACKEND) + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", *argv],
        capture_output=True, text=True, env=env, cwd=str(common.REPO),
    )
    text = (result.stdout or "").strip()
    payload: dict = {}
    if text:
        try:
            payload = json.loads(text)
        except ValueError:
            # `certificate install` prints the JSON document followed by a
            # human-readable restart note; parse the leading JSON object.
            decoder = json.JSONDecoder()
            try:
                obj, _ = decoder.raw_decode(text)
                payload = obj if isinstance(obj, dict) else {"raw": text}
            except ValueError:
                payload = {"raw": text}
    if result.returncode != 0:
        payload.setdefault("stderr", (result.stderr or "").strip())
    return result.returncode, payload


def real_flow() -> int:
    b3_root = common.PLAN_B_ROOT / "b3"
    common.bootstrap_env_before_app_import(b3_root)

    from app.core.config import Settings
    from app.runtime_qualification.evidence import evidence_directory

    settings = Settings()
    repo_cert_path = (
        Path(__file__).resolve().parents[1]
        / "backend" / "app" / "pipelines" / "execution_certificates.json"
    )
    repo_before = _sha256_file(repo_cert_path)

    evidence: dict = {
        "runtime_family": common.RUNTIME_FAMILY,
        "expected_runtime_ref": common.GPU_RUNTIME_REF,
        "repo_certificate_sha256_before": repo_before,
        "plugins": {},
    }

    code, doctor = _run_cli(["runtime", "doctor"])
    if code != 0:
        raise SystemExit(f"B3 doctor failed: {doctor}")
    gpu = next(p for p in doctor.get("providers", []) if p["executor"] == "local_gpu")
    evidence["doctor"] = {
        "identity_scheme": gpu["identity_scheme"],
        "identity_status": gpu["identity_status"],
        "configured_runtime_ref": gpu["configured_runtime_ref"],
        "derived_runtime_ref": gpu["derived_runtime_ref"],
        "runtime_family": doctor.get("runtime_family"),
        "interpreter_available": gpu["interpreter"]["available"],
        "gpu_available": gpu["gpu"]["available"],
    }
    if gpu["identity_scheme"] != "bhq3_gpu_v1" or gpu["identity_status"] != "match":
        raise SystemExit(f"B3 doctor not matched: {evidence['doctor']}")
    if gpu["derived_runtime_ref"] != common.GPU_RUNTIME_REF:
        raise SystemExit(f"B3 derived runtime_ref mismatch: {gpu['derived_runtime_ref']}")

    for plugin_id, version in PLUGINS:
        code, qual = _run_cli([
            "qualify", "--plugin", plugin_id, "--plugin-version", version,
            "--executor", "local_gpu", "--model-release", common.MODEL_RELEASE,
        ])
        if code != 0 or not qual.get("passed"):
            raise SystemExit(f"B3 qualify failed for {plugin_id}: {qual}")
        runtime_ref = qual["runtime_ref"]
        evdir = evidence_directory(settings.data_root, executor="local_gpu", runtime_ref=runtime_ref)
        code, install = _run_cli(["certificate", "install", "--from", str(evdir)])
        if code != 0:
            raise SystemExit(f"B3 install failed for {plugin_id}: {install}")
        evidence["plugins"][plugin_id] = {
            "qualification_type": qual["qualification_type"],
            "identity_scheme": qual["identity_scheme"],
            "runtime_ref": runtime_ref,
            "evidence_dir": str(evdir),
            "install_status": install.get("status"),
            "install_certificate": install.get("certificate"),
        }

    repo_after = _sha256_file(repo_cert_path)
    evidence["repo_certificate_sha256_after"] = repo_after
    evidence["repo_certificate_unchanged"] = repo_after == repo_before
    evidence["all_already_certified"] = all(
        entry["install_status"] == "already_certified"
        for entry in evidence["plugins"].values()
    )
    if not evidence["repo_certificate_unchanged"]:
        raise SystemExit("B3 mutated the repo-default certificate store.")
    if not evidence["all_already_certified"]:
        raise SystemExit("B3 expected already_certified for both plugins.")

    common.EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    out = common.EVIDENCE_DIR / "b3_certificate_flow.json"
    out.write_text(json.dumps(evidence, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(evidence, indent=2, sort_keys=True))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--real", action="store_true")
    args = parser.parse_args(argv)
    if not args.real:
        parser.error("--real is required (this performs the real runtime qualification flow)")
    return real_flow()


if __name__ == "__main__":
    raise SystemExit(main())
