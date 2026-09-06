# M8.6C-2 Real Dataset Benchmark Acceptance

Date: 2026-09-06
Branch: `feature/m8-6c-dataset-benchmark-ui-real-evaluation`
Base: `feature/v1-core @ 1036e51b70a351dfcae0330c8e5e725844640bc8`
Plan: `docs/superpowers/plans/2026-09-06-m8-6c-2-real-benchmark-acceptance.md`

## Scope

This is the single formal 2500-sample SpaceNet test benchmark acceptance. It evaluates only the frozen platform rows already persisted by M8.6B:

- 2500 AnalysisRuns
- 33373 DetectionResults

No inference, training, NMS recomputation, DSP, GPU work, re-import, GroundTruth mutation, or evaluator modification was performed. Exactly one formal DatasetEvaluation was created and run.

## Implementation identity

- platform commit SHA: `102f6e633af8edcdcc4a56feacea1a0c86336b72` (feature branch HEAD used for C-2; the C-1 implementation commits are on this branch)
- DatasetEvaluation id: `eval_23bfb7a075e04d80a30e70754042a61c`
- batch fingerprint: `c52cf0e752ebcead0a88b53a32a1b8b5172c38a7acb80ecbfc256c55d4c1cfd5`
- recording manifest hash: `91496138ee4a10590e6e304e70b9bca8120e70c783c61df7aaad84e42d64181b`
- dataset / split / label space: `SpaceNet` / `test` / `spacenet_14`
- pipeline / version: `zoomspec_yolo26n_aug_combined_frn_v3` / `1.0.0`

## Protocol

- evaluation_protocol: `physical_tf_detection_ap_v2`
- protocol_config_json:

```json
{
  "ap_interpolation": "101_point_max_precision",
  "ap_recall_points": 101,
  "confidence_field": "DetectionResult.confidence",
  "diagnostic_iou_threshold": 0.5,
  "diagnostic_matching": "hungarian_class_agnostic",
  "gt_duplicate_policy": "exact_physical_class_dedup",
  "ground_truth_view": "evaluation_canonical",
  "iou_thresholds": [0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95],
  "ranking_tie_break": ["confidence_desc", "manifest_order", "t_start_s", "f_low_hz", "t_end_s", "f_high_hz", "class_id"]
}
```

## Coverage / inputs

- expected_recordings: 2500
- evaluated_recordings: 2500
- missing_recordings: 0
- coverage: 1.0
- comparable: true
- status: completed
- predictions: 33373
- raw GroundTruth (SpaceNet/test/spacenet_14): 20018
- canonical GroundTruth: 19962
- duplicates removed: 56
- duplicate policy: exact_physical_class_dedup
- classification_applicable: true

## Platform metrics

### Localization

- AP50: `0.8666496180384053`
- AP50:95: `0.6715938311249926`
- operating (IoU 0.5): tp `17942`, fp `15431`, fn `2020`, precision `0.5376202319240104`, recall `0.8988077346959222`, f1 `0.6728039748757851`

### Classification on Matched

- matched_count: `17942`
- class_correct: `14625`
- class_wrong: `3317`
- matched_accuracy: `0.8151265187827444`

### End-to-End / Class-aware

- mAP50: `0.49706861157413673`
- mAP50:95: `0.3732512758737991`
- operating (IoU 0.5): tp `14625`, fp `18748`, fn `5337`, precision `0.43822850807539027`, recall `0.7326420198376916`, f1 `0.5484203618636917`

### Per-class (14 classes)

| class_id | class_name | GT | Pred | AP50 | AP50:95 | P | R | F1 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | WiFi 20MHz QPSK | 852 | 1718 | 0.1910008623753412 | 0.12954934674671198 | 0.1839348079161816 | 0.37089201877934275 | 0.24591439688715955 |
| 1 | WiFi 20MHz 16QAM | 915 | 1192 | 0.14752354327945374 | 0.10055460636755079 | 0.18624161073825504 | 0.24262295081967214 | 0.2107261509254865 |
| 2 | WiFi 20MHz 64QAM | 933 | 1519 | 0.32905099033371227 | 0.22633226892596267 | 0.24489795918367346 | 0.3987138263665595 | 0.3034257748776509 |
| 3 | WiFi 40MHz QPSK | 390 | 814 | 0.3135401576306124 | 0.21615477631206098 | 0.24447174447174447 | 0.5102564102564102 | 0.33056478405315615 |
| 4 | WiFi 40MHz 16QAM | 378 | 691 | 0.21014007297803255 | 0.13440237302630742 | 0.16353111432706222 | 0.29894179894179895 | 0.21141253507951355 |
| 5 | WiFi 40MHz 64QAM | 410 | 545 | 0.16791169635543787 | 0.11114509913352691 | 0.20733944954128442 | 0.275609756097561 | 0.23664921465968589 |
| 6 | BLE LE1M | 1962 | 3336 | 0.8668379924323835 | 0.7411290057223427 | 0.5176858513189448 | 0.8802242609582059 | 0.6519441298603247 |
| 7 | BLE LE2M | 2033 | 2647 | 0.8845649094113031 | 0.7392599188680838 | 0.690970910464677 | 0.8996556812592228 | 0.7816239316239316 |
| 8 | Zigbee | 4042 | 5266 | 0.899749295878718 | 0.7956102840101 | 0.6992024306874288 | 0.9109351806036615 | 0.7911474000859475 |
| 9 | LoRa 250kHz | 3945 | 6278 | 0.8745683692167207 | 0.6295866515057087 | 0.562918126791972 | 0.8958174904942966 | 0.6913821774430207 |
| 10 | SRRC QPSK | 1038 | 2111 | 0.36264810844302714 | 0.26539464226730025 | 0.2505921364282331 | 0.5096339113680154 | 0.3359796760876469 |
| 11 | SRRC 16QAM | 1025 | 1705 | 0.43609833761596867 | 0.3504342796259746 | 0.33255131964809387 | 0.5531707317073171 | 0.4153846153846154 |
| 12 | AM | 1013 | 2279 | 0.7171941503624939 | 0.45603099170476574 | 0.3444493198771391 | 0.7749259624876604 | 0.4769137302551641 |
| 13 | FM | 1026 | 3272 | 0.5581320757247098 | 0.3299336180167901 | 0.19468215158924204 | 0.6208576998050682 | 0.29641693811074915 |

### Confusion

- total confusion rows: `105`
- top confusions by count: SRRC QPSK→SRRC 16QAM `329`; WiFi 20MHz 16QAM→WiFi 20MHz QPSK `327`; SRRC 16QAM→SRRC QPSK `273`; WiFi 20MHz 64QAM→WiFi 20MHz QPSK `238`; WiFi 20MHz QPSK→WiFi 20MHz 16QAM `199`.

## Deterministic recomputation

Independent in-memory recomputation over the same frozen evaluation membership (`BenchmarkInputLoader` + `build_protocol_view` + normal evaluation functions), without creating a second evaluation or re-running the worker, produced identical values (tolerance `abs_tol=1e-15`, `rel_tol=0`):

```
deterministic_recompute=PASS
recomputed map50  0.49706861157413673  == persisted
recomputed map50_95 0.3732512758737991  == persisted
```

## Historical parity audit

- historical mAP50: `0.49706861157413673` (reference only)
- platform mAP50: `0.49706861157413673`
- delta_map50: `0.0`
- historical mAP50:95: `0.37325127587379914` (reference only)
- platform mAP50:95: `0.3732512758737991`
- delta_map50_95: `-5.551115123125783e-17`
- tolerance: `abs_tol = 1e-12`

Both deltas are within `1e-12`; mAP50 matches exactly and mAP50:95 differs only by a single 64-bit floating-point ULP (`~5.6e-17`).

**Parity conclusion: PARITY CONFIRMED**

No 服务器opencode legacy-evaluator audit was needed: the platform `physical_tf_detection_ap_v2` result reproduces the frozen historical reference values within the classification tolerance.

## Database integrity

After the formal evaluation:

- raw SpaceNet/test/spacenet_14 GroundTruth count remains `20018`
- AnalysisRun rows unchanged (no new runs)
- DetectionResult rows unchanged (no new detections)
- exactly one DatasetEvaluation with the formal name exists, status `completed`
- no inference, training, NMS recomputation, DSP, GPU work, re-import, or GroundTruth mutation was performed