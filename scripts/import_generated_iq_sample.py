#!/usr/bin/env python3
"""Import one generated int16 IQ scene as a WISA standalone sample.

The platform reads ``int16_interleaved_le`` natively, so the common case is a
DIRECT registration of the generated ``.iq`` with ``--format int16``. The
converter below remains available for other tools that expect ``complex64_le``
or ``float16_interleaved_le``.

Example (no conversion — recommended)
-------------------------------------
python scripts/import_generated_iq_sample.py \
    --iq  "D:/.../example_output/mixed_test/iq/scene_000000.iq" \
    --label "D:/.../example_output/mixed_test/labels/scene_000000.json" \
    --name "pioneer-scene0" --format int16

It deliberately does NOT import ground truth: the generator's label space
("systems" = preamble + modulation + symbol rate) has no counterpart in the
platform yet, so a standalone sample is registered for viewing and detection.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import urllib.error
import urllib.request

import numpy as np

INT16_LAYOUT = "little-endian int16, interleaved I,Q"


def register_file(iq_path: Path) -> tuple[int, str]:
    """Size/format of the source file when it is registered as-is (int16)."""
    byte_size = iq_path.stat().st_size
    if byte_size == 0 or byte_size % 4:
        raise SystemExit(f"{iq_path} is not a non-empty interleaved int16 file")
    return byte_size // 4, "int16_interleaved_le"


def convert(iq_path: Path, out_path: Path, output_format: str) -> tuple[int, str]:
    raw = np.fromfile(iq_path, dtype="<i2")
    if raw.size == 0 or raw.size % 2:
        raise SystemExit(f"{iq_path} is not a non-empty interleaved int16 file")
    pairs = raw.reshape(-1, 2).astype(np.float32)
    iq = pairs[:, 0] + 1j * pairs[:, 1]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if output_format == "complex64":
        iq.astype(np.complex64).tofile(out_path)  # exact for int16 sources
        data_format = "complex64_le"
    else:
        # Interleaved float16 I/Q, matching the SpaceNet on-disk layout.
        interleaved = np.empty(iq.size * 2, dtype="<f2")
        interleaved[0::2] = pairs[:, 0].astype(np.float16)
        interleaved[1::2] = pairs[:, 1].astype(np.float16)
        interleaved.tofile(out_path)
        data_format = "float16_interleaved_le"
    return int(iq.size), data_format


def register(api: str, *, path: Path, name: str, data_format: str,
             sample_rate_hz: float, center_frequency_hz: float) -> dict:
    body = json.dumps({
        "path": str(path),
        "name": name,
        "data_format": data_format,
        "sample_rate_hz": sample_rate_hz,
        "center_frequency_hz": center_frequency_hz,
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{api.rstrip('/')}/api/recordings/register-path",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        if error.code == 409 and "ALREADY_REGISTERED" in detail:
            # Idempotent re-run: reuse the existing standalone sample so ground
            # truth can be (re)imported without registering a duplicate.
            payload = json.loads(detail)
            recording_id = (payload.get("error", {}).get("details") or {}).get("recording_id")
            if recording_id:
                return {"id": recording_id, "already_registered": True}
        raise SystemExit(f"platform rejected the registration ({error.code}): {detail}")


def intersecting_events(label: dict, duration_s: float) -> list[dict]:
    return [
        event
        for event in label.get("events") or []
        if float(event.get("end_time_s", 0.0)) > 0.0
        and float(event.get("start_time_s", 0.0)) < duration_s
    ]


def import_ground_truth(
    api: str,
    *,
    recording_id: str,
    label: dict,
    duration_s: float,
    label_space: str,
    class_name: str,
) -> int:
    events = intersecting_events(label, duration_s)
    objects = []
    for event in events:
        # The generated window is a SLICE of a longer scene, so an event may start
        # or end outside it. Clip to the captured duration; a box truncated at the
        # window edge is the honest representation of a truncated capture.
        start = max(0.0, float(event["start_time_s"]))
        end = min(float(duration_s), float(event["end_time_s"]))
        if not (0.0 <= start < end <= float(duration_s)):
            continue
        objects.append(
            {
                # Ids must be unique across recordings: several scenes are slices of
                # the same planner, so bare event ids would collide.
                "id": f"evt_{recording_id}_{event['event_id']}",
                "t_start_s": start,
                "t_end_s": end,
                "f_low_hz": float(event["freq_low_hz"]),
                "f_high_hz": float(event["freq_high_hz"]),
                "class_id": int(event.get("detection_class_id", 0)),
                "class_name": class_name,
            }
        )
    body = json.dumps({"label_space": label_space, "objects": objects}).encode("utf-8")
    request = urllib.request.Request(
        f"{api.rstrip('/')}/api/recordings/{recording_id}/ground-truth",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            return len(json.loads(response.read().decode("utf-8")))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise SystemExit(f"platform rejected the ground truth ({error.code}): {detail}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--iq", type=Path, required=True, help="generated int16 interleaved .iq")
    parser.add_argument("--label", type=Path, required=True, help="matching scene_*.json")
    parser.add_argument("--name", required=True, help="sample name shown in the platform")
    parser.add_argument(
        "--format",
        choices=("int16", "complex64", "float16"),
        default="int16",
        help="int16 registers the source file as-is (platform-native); the others convert first",
    )
    parser.add_argument("--out", type=Path, default=None, help="converted output path (required unless --format int16)")
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--center-frequency-hz", type=float, default=None,
                        help="default: label digital_center_hz, else sample_rate/2")
    parser.add_argument("--dry-run", action="store_true", help="convert only; do not register")
    parser.add_argument(
        "--import-ground-truth",
        action="store_true",
        help="also import the events intersecting the window as ground truth",
    )
    parser.add_argument(
        "--ground-truth-label-space",
        default="signal_presence_v1",
        help="label space for the imported ground truth (default: the detector's Signal space)",
    )
    parser.add_argument(
        "--ground-truth-class-name",
        default="Signal",
        help="class name must match the label space exactly (signal_presence_v1 uses 'Signal')",
    )
    args = parser.parse_args()

    if not args.iq.is_file():
        raise SystemExit(f"IQ file not found: {args.iq}")
    label = json.loads(args.label.read_text(encoding="utf-8"))
    sample_rate_hz = float(label.get("sample_rate_hz") or 0.0)
    if sample_rate_hz <= 0:
        raise SystemExit("label does not provide sample_rate_hz")
    center = args.center_frequency_hz
    if center is None:
        center = float(label.get("digital_center_hz") or sample_rate_hz / 2.0)

    if args.format == "int16":
        samples, data_format = register_file(args.iq)
        registered_path = args.iq.resolve()
        converted = False
    else:
        if args.out is None:
            raise SystemExit("--out is required when converting (use --format int16 to register as-is)")
        samples, data_format = convert(args.iq, args.out, args.format)
        registered_path = args.out.resolve()
        converted = True
    duration_s = samples / sample_rate_hz
    summary = {
        "source_file": str(args.iq),
        "registered_file": str(registered_path),
        "converted": converted,
        "output_bytes": registered_path.stat().st_size,
        "data_format": data_format,
        "samples": samples,
        "sample_rate_hz": sample_rate_hz,
        "center_frequency_hz": center,
        "duration_s": duration_s,
        "dtype_note": f"source was {INT16_LAYOUT}",
    }

    if args.dry_run:
        print(json.dumps({**summary, "registered": False}, indent=2))
        return

    recording = register(
        args.api,
        path=registered_path,
        name=args.name,
        data_format=data_format,
        sample_rate_hz=sample_rate_hz,
        center_frequency_hz=center,
    )
    ground_truth_count = None
    if args.import_ground_truth:
        ground_truth_count = import_ground_truth(
            args.api,
            recording_id=recording["id"],
            label=label,
            duration_s=duration_s,
            label_space=args.ground_truth_label_space,
            class_name=args.ground_truth_class_name,
        )
    print(json.dumps(
        {
            **summary,
            "registered": True,
            "recording": recording,
            "ground_truth_imported": ground_truth_count,
        },
        indent=2,
        ensure_ascii=False,
    ))


if __name__ == "__main__":
    main()
