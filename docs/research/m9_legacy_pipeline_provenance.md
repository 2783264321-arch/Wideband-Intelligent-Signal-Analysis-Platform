# M9.0 Legacy Pipeline Provenance

> Scope: document the exact provenance of the first real historical deep-learning pipeline
> bridged into the platform as an Analysis Package v1. No model is re-run in M9.0.

## Pipeline identity

| Field | Value |
|---|---|
| pipeline_id | `zoomspec_yolo26n_aug_combined_frn_v3` |
| pipeline_name | ZoomSpec YOLOv26n Aug + Combined FRN V3 |
| pipeline_version | 1.0.0 (platform Adapter version of the frozen historical scheme) |
| architecture_family | multi_stage |
| task_capability | detection_classification |
| label_space | spacenet_14 |

## Architecture family / representation

- **Detector (CPN)**: Ultralytics YOLOv26n, trained on LS-STFT `paper_strict`
  representation (640x640, n_fft=2048, win_length=2048, hop_length=1024), augmented with
  I/Q-domain signal-injection data (base 6,746 + augmented 13,492 = 20,238 training images).
- **Signal extraction**: AHLP (heterodyne to baseband, Hamming FIR low-pass, safe decimation),
  deterministic, not a learned model.
- **Classifier/regressor (FRN)**: ZoomSpec dual-domain (I/Q + FFT) network, Combined FRN V3
  (channels=128, fusion_attention, bandwidth_context, center_regression), fine-tuned jointly
  from four data sources.

## Detector

- **Architecture**: YOLOv26n (3 bandwidth classes: narrow/mid/wide)
- **Checkpoint path**: `/root/autodl-tmp/Claude/runs/cpn/ls_stft_yolo26n_aug_warm/weights/best.pt`
- **Checkpoint SHA256**: `eba4fa4b112a0e61cc1013e96f99d1ae82b845f4be1e8b1f80bd2089d1f82311`
- Inference config: confidence 0.003, NMS IoU 0.7, max_det 300, image_size 640,
  physical_box_conversion `ls_grid_pixel_edges`.

## AHLP / signal extraction

- Deterministic DSP (heterodyne + Hamming FIR + safe decimation), no checkpoint.
- Frozen parameters: kappa=0.2, eta=0.1, order_constant=3.3, min_numtaps=31,
  max_numtaps=4095, context_ratio=0.1.

## FRN

- **Architecture**: ZoomSpec FRN dual-domain, Combined V3
- **Checkpoint path**: `/root/autodl-tmp/Claude/artifacts/frn_combined_v3_training/best.pt`
- **Checkpoint SHA256**: `da6087da2fbfbaa5ba0e2cb210d08c24ee8b2af8418329d32216f7c77253be67`
- Config: `frn_combined_v3.yaml` SHA256 `7cb7d2de5534d126260ab73b0e17a7e1187d846477ed3f43174fc1ea151924ac`
- Decode: end_norm_clamp = min(1-epsilon, start_norm + duration_norm), epsilon 1e-6.

## Frozen pipeline config

- **Path**: `/root/autodl-tmp/Claude/configs/frozen_full_pipeline_v26_aug_combined.yaml`
- **SHA256**: `030dbfa77353f876728252c2f247b47816baf8921a7641bb8873ae9035d9d7ec`
- Postprocess: score_mode geometric, score_threshold 0.001, physical_class_nms_iou 0.7.
- Normalization reference: `ZoomSpec/reports/normalization_ls_stft.json`
  (used at CPN cache build; detections themselves are already physical).

## Dataset / split

- **Dataset**: SpaceNet advanced/test
- **Dataset dir**: `/root/autodl-tmp/SpaceNet_Dataset/advanced/test`
- **Split manifest path**: `/root/autodl-tmp/Claude/reports_claude/test_manifest.json`
- **Split manifest SHA256**: `ea5b41d0cd6b3393be75ece3f3bbc8aee38e782ef421e8cd0d1b3e580839f5b6`
- Manifest keys: `dataset_dir`, `seed=42`, `test_ids` (2,500 stems), `test_summary`.

## Historical test predictions

- **Path**: `/root/autodl-tmp/Claude/reports_claude/test_val_detections_augv3.jsonl`
- **SHA256**: `950ad87ec355169b1904da4364f296ade5854926d4e02dc83049d9585859efcd`
- 33,373 detection records across all 2,500 test samples.

## Historical test metrics

- **Path**: `/root/autodl-tmp/Claude/reports_claude/test_full_val_map_augv3.json`
- **SHA256**: `b951a37f044c8e3e9d14be08c8caa1d20714ea251980230045de0e13efa6cdc2`
- Verified values (from this report, re-read directly):
  - images = 2500
  - canonical_ground_truth = 19962
  - predictions = 33373
  - map_50 = 0.49706861157413673
  - map_50_95 = 0.37325127587379914
  - duplicate_ground_truth_policy = "exact physical/class duplicates removed"
- These match the frozen config's recorded `official_test.test_result`
  (map_50 0.4971, map_50_95 0.3733, predictions 33373, canonical_ground_truth 19962).

## Writer code (how the predictions were produced)

- Driver: `/root/autodl-tmp/Claude/scripts/run_test_new_pipeline.py`
  1. `ZoomSpec/scripts/evaluate_cpn.py` (CPN proposals on test, aug_warm weights, conf 0.003)
  2. `ZoomSpec/scripts/run_frn_on_proposals.py` (AHLP→FRN→fusion→physical NMS, 4 shards,
     score_mode geometric, score_threshold 0.001, nms_iou 0.7)
  3. `ZoomSpec/scripts/merge_detection_jsonl.py` → `test_val_detections_augv3.jsonl`
  4. `ZoomSpec/scripts/evaluate_detections.py` → `test_full_val_map_augv3.json`
- Detection record writer (`run_frn_on_proposals.py`):
  `{"sample_id", "t0_s", "t1_s", "f0_hz", "f1_hz", "class_id", "score"}`

## Verified legacy schema (per detection record)

| Field | Legacy | Platform | Unit / semantics |
|---|---|---|---|
| sample identity | `sample_id` | Recording `name` | SpaceNet test file stem, e.g. `0` → `0.bin`/`0.json` |
| time start | `t0_s` | `t_start_s` | seconds |
| time end | `t1_s` | `t_end_s` | seconds |
| freq start | `f0_hz` | `f_low_hz` | absolute Hz |
| freq end | `f1_hz` | `f_high_hz` | absolute Hz |
| class id | `class_id` | `class_id` | int in [0,13] |
| class name | (derived) | `class_name` | canonical `spacenet_14` name |
| confidence | `score` | `confidence` | final frozen-pipeline score, [0,1] |

## bbox format

- **Coordinates**: absolute physical time (seconds) + absolute frequency (Hz).
  Produced by `run_frn_on_proposals.py` directly as `t0_s/t1_s/f0_hz/f1_hz`.
- t0/t1 clipped to the observation [0, duration_s]; f0/f1 clipped to [f_lo_hz, f_hi_hz].

## class schema

- Canonical `spacenet_14` (id → name):
  0 WiFi 20MHz QPSK, 1 WiFi 20MHz 16QAM, 2 WiFi 20MHz 64QAM,
  3 WiFi 40MHz QPSK, 4 WiFi 40MHz 16QAM, 5 WiFi 40MHz 64QAM,
  6 BLE LE1M, 7 BLE LE2M, 8 Zigbee, 9 LoRa 250kHz,
  10 SRRC QPSK, 11 SRRC 16QAM, 12 AM, 13 FM.
- Identical to platform `label_spaces/spacenet_14.json` (verified by diff).

## confidence semantics

- `score` is the **frozen pipeline's final post-process confidence**:
  `score = sqrt(proposal_score * signal_probability * class_probability)`
  (geometric fusion of CPN proposal confidence and FRN signal/class probabilities),
  then physical class-aware NMS (IoU 0.7), then threshold 0.001.
- Component scores (`proposal_score`, `signal_probability`, `class_probability`) are available
  only in per-sample diagnostics/raw shards, not in `test_val_detections_augv3.jsonl`.
  They are NOT re-computed in M9.0; the final `score` is preserved as platform `confidence`.
- No fabricated confidence is introduced by the bridge.

## sample identity mapping

- `sample_id` in the historical detections corresponds 1:1 to a SpaceNet advanced/test stem
  (`{stem}.bin` + `{stem}.json`) and to the platform Recording registered by
  `SpaceNetRegistrationService` (Recording `name` = stem, `source="spacenet"`,
  `dataset_split="test"`, `external_path` = the `.bin` path).