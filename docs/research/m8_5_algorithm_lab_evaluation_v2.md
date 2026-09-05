# Algorithm Lab Evaluation v2 — 评价口径说明

> Date: 2026-09-06
> Status: Evaluation methodology for the V1 Algorithm Lab single-recording comparison.

本文件定义 Algorithm Lab（M8.5 evaluation v2）中三类指标的精确含义，供接入模型的同学统一口径。

## 1. Localization matching（唯一的匹配来源）

所有指标共用**同一套** localization matching，平台不针对类别重复跑第二套 Hungarian assignment。

- 输入：Ground Truth bbox 与 prediction bbox 的物理坐标（`t_start_s, t_end_s, f_low_hz, f_high_hz`，单位秒与绝对 Hz）。
- 构建 GT × Prediction 的 time-frequency IoU 矩阵。
- `scipy.optimize.linear_sum_assignment` 做一对一最大 IoU 分配。
- 仅保留 `IoU >= 0.5` 的分配。
- **类别完全不参与 matching**（class-agnostic）。

## 2. Localization metrics

对每个 Run 独立：

```
TP = 保留的 match pair 数量
FP = total_predictions - TP
FN = total_gt - TP
Precision = TP / (TP + FP)
Recall    = TP / (TP + FN)
F1        = 2PR / (P + R)
Mean matched IoU = 各 TP pair 的 IoU 均值（TP=0 时为 null）
```

## 3. Classification-on-Matched

只对 Localization 已经产生的 `MatchPair(gt_index, pred_index, iou)` 做类别比较：

```
matched_count = TP
class_correct = matched pair 中 GT.class_id == pred.class_id 的数量
class_wrong   = matched_count - class_correct
matched_accuracy = class_correct / matched_count   # matched_count=0 时为 null
```

- `matched_count == 0` 时 `matched_accuracy = null`（而不是 0.0），因为「没有可评价的匹配」与「有匹配但全错」含义不同。
- 仅返回错误分类的聚合 `confusions`：同一 `GT class_id → pred class_id` 聚合 count，按 `(gt_class_id, pred_class_id)` 稳定排序。

## 4. Class-aware End-to-End

**仍然复用同一套 localization MatchPair**，不重新执行 Hungarian matching。

一个 pair 只有在 `IoU >= 0.5` **且** `GT.class_id == pred.class_id` 时才算 class-aware TP：

```
class_aware_tp = class_correct
class_aware_fp = total_predictions - class_correct
class_aware_fn = total_gt - class_correct
precision / recall / f1 同上公式
```

因此：bbox 定位正确但类别错误的 prediction →
- Localization: TP
- Classification: Wrong
- Class-aware: 1 FP + 1 FN

> **重要：Class-aware 指标不是 dataset-level mAP。**
> 它们由既有的 localization 一对一配对推导而来，不是 per-class AP / mAP。
> 标准 per-class AP / mAP（含 confidence sweep、PR curve）留待后续评价阶段，不在此指标范围内。

## 5. Detection-only pipeline（分类 N/A）

当 Run 的 pipeline：

- `task_capability == detection_localization`，或
- pipeline `label_space != recording Ground Truth label_space`

时，分类不适用：

```
classification_applicable = false
classification = null
class_aware    = null
reason = detection_only_pipeline / label_space_mismatch / unknown_classification_semantics
```

典型的 `stft_energy_detector` 输出 `class_id=0, class_name="Signal"`。这个 0 是 `signal_presence_v1` 的泛化类，**绝不能**与 `spacenet_14` 的 `class_id=0 (WiFi 20MHz QPSK)` 比较。

对 `executor=imported` 的 Run：M6 importer 已强制 `package.label_space == recording.label_space`，因此使用 `recording.label_space` 作为 label-space 兼容依据。

## 6. M8.5 指标 ≠ mAP

- 不实现 mAP50 / mAP50:95 / AP per class / PR curve / confidence sweep。
- 不实现 batch 2500 test evaluation。
- 不实现 confusion-matrix heatmap。
- 不改变 Analysis Package、Recording、DetectionResult contract，不改 M6 importer，不改 Pipeline 内部结构。