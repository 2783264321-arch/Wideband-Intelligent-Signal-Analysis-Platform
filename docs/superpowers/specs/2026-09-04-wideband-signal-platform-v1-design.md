# 宽带信号智能分析与科研验证平台 V1 设计规格

- 文档版本：V1.0
- 日期：2026-09-04
- 设计状态：已用户批准；2026-09-04 根据 SpaceNet 进阶库真实数据契约补充修订
- 产品定位：Local-first、CPU-friendly 的离线宽带信号智能分析与科研验证平台
- 主要数据基准：SpaceNet（2025 中国“AI+无线电”相关赛事数据集，非遥感 SpaceNet）
- 项目仓库：`git@gitee.com:liguanglai/wideband-intelligent-signal-analysis-platform.git`
- 主要参考算法：
  - *An End-to-End Deep Learning Framework for Wideband Signal Recognition*
  - *ZoomSpec: A Coarse-to-Fine Framework for Wideband Spectrum Sensing*

---

## 1. 产品目标

本平台面向宽带 IQ 数据的离线分析与科研展示，核心目标是将“宽带信号检测 + 时频定位 + 信号制式分类 + 单信号分析 + 算法比较”整合为一个统一 Web 工作台。

平台不追求工业级实时 SDR 输入，不要求本机具备 NVIDIA GPU。V1 采用本地优先的轻量架构：本地负责数据管理、DSP、结果展示和轻量 CPU 推理；重型模型可在 AutoDL 等 GPU 环境运行后，以标准 Analysis Package 导回本机。

V1 要形成两条清晰链路：

### 1.1 产品分析链

```text
Recording
   ↓
Pipeline
   ↓
AnalysisRun
   ↓
DetectionResult
   ↓
Spectrum Analysis / Signals / Signal Detail
```

回答的问题是：

> 这一段宽带信号里有哪些信号、在哪里、是什么制式？

### 1.2 科研验证链

```text
AnalysisRun A
      +
AnalysisRun B
      ↓
Experiment
      ↓
Metrics / Case Study / Processing Inspector
```

回答的问题是：

> 不同算法为什么有差异，哪个模块有效，某个样本为什么漏检或分类错误？

---

## 2. V1 范围

### 2.1 必须支持

- 离线 IQ 数据导入
- SpaceNet 数据适配
- Recording 数据管理
- STFT
- LS-STFT
- 宽带 Spectrogram 显示
- Zoom / Pan / 时间频率坐标查看
- 自动信号检测
- 时频 Bounding Box
- 信号制式分类
- Confidence 展示
- Signal List
- 中心频率、带宽、起止时间、持续时间展示
- Signal Detail
- Prediction / Ground Truth 对照
- STFT / LS-STFT 对比
- 已注册 Pipeline 的运行与结果展示
- Existing Run 导入
- AutoDL Analysis Package 导入
- Algorithm Lab：已有 Run 对比、指标比较、样本级 A/B 比较、Ablation Group、Processing Inspector
- JSON / CSV 结果导出

### 2.2 V1.5 可增强

- Replay Mode
- ROI 框选分析
- PSD / FFT 的更丰富交互
- 典型失败案例自动筛选
- Remote AutoDL Executor
- 轻量实验历史
- SigMF 更完整兼容

### 2.3 V2 再考虑

- Open-set Recognition 完整链路
- 未知信号池
- Annotation
- Dataset Builder
- 模型版本管理
- 批量实验管理
- 更完整插件系统

### 2.4 明确不做

- SDR 实时采集
- 真正 real-time streaming
- DOA / TDOA / FDOA
- 地理定位
- 多站融合
- 完整解调
- 协议解码
- 在线训练
- 超参数搜索
- AutoML
- 多用户权限系统
- Kubernetes / 微服务化
- V1 阶段引入 Redis / Celery / PostgreSQL / MinIO，除非后续明确批准

---

## 3. 核心任务定义

从深度学习角度，任务等价于将图像目标检测映射到时频域：

```text
Wideband IQ
   ↓
STFT / LS-STFT
   ↓
Spectrogram
   ↓
Detector / Multi-stage Pipeline
   ↓
Time-Frequency BBox + Signal Type + Confidence
```

统一 DetectionResult 使用物理坐标：

- `t_start_s`
- `t_end_s`
- `f_low_hz`
- `f_high_hz`
- `class_id`
- `class_name`
- `confidence`

平台派生：

- `center_frequency_hz = (f_low_hz + f_high_hz) / 2`
- `bandwidth_hz = f_high_hz - f_low_hz`
- `duration_s = t_end_s - t_start_s`

禁止把像素 BBox 作为平台统一坐标。STFT 与 LS-STFT 可使用不同频率映射，但必须在 Pipeline 输出阶段还原到真实秒和 Hz。

---

## 4. 产品页面

V1 使用 5 个主页面。

### 4.1 Recordings

定位：数据中心与系统首页。

主要能力：

- 浏览 Recording
- 导入自定义 IQ
- 展示 SpaceNet Demo 样本
- 显示基本 metadata
- 显示是否存在 Ground Truth
- 显示最近 AnalysisRun
- 打开 Spectrum Analysis
- 导入 Existing Run

不额外做空泛 Dashboard。

### 4.2 Spectrum Analysis

定位：产品主工作台，视觉中心为宽带 Spectrogram。

推荐布局：左侧主导航 + 中央频谱区 + 右侧 Signal Results。

主要能力：

- Recording 基本信息
- STFT / LS-STFT 表示选择
- Pipeline 选择
- Run Analysis
- Prediction Overlay
- Ground Truth Overlay
- BBox 高亮
- Signal 选择
- Signal List 快速浏览
- View All Signals
- View Details
- Compare in Algorithm Lab

单击信号只选中，不立即跳页；通过 `View Details` 进入 Signal Detail。

### 4.3 Signals

定位：同一个 AnalysisRun 的数据表视角。

主要字段：

- ID
- Signal Type
- Confidence
- Center Frequency
- Bandwidth
- Time Range
- Duration

支持：

- Signal Type 筛选
- Confidence 筛选
- Frequency Range 筛选
- Time Range 筛选
- Show in Spectrum
- View Details
- CSV / JSON 导出

研究模式下可增加：Prediction、GT、IoU、Status。

### 4.4 Signal Detail

定位：单个检测目标的深入分析页。

顶部 Summary：

- Signal Type
- Confidence
- Center Frequency
- Bandwidth
- Start / End Time
- Duration

通用可视化：

- Local Spectrogram
- Spectrum / PSD
- I/Q waveform
- FFT
- Prediction vs Ground Truth
- T-F IoU（有 GT 时）

若 Pipeline 提供 IntermediateArtifact，则动态显示 Processing Inspector。

V1 不强制星座图、解调和协议解析。

### 4.5 Algorithm Lab

定位：受控 Pipeline 的科研实验工作台，而非自由拖拽 Pipeline Builder。

核心能力：

1. Pipeline Comparison
2. Ablation Study
3. Sample Comparison
4. Processing Inspector

实验输入：

- Dataset / Recording
- Scope：Full Test Set / Selected Recordings / Single Recording
- Existing AnalysisRuns 或已注册 Pipelines

核心指标：

- Detection / Localization：mAP@50、mAP@50:95、Precision、Recall
- Classification：Accuracy、Macro F1（当 LabelSpace 与任务定义允许时）
- Latency：仅在硬件环境可比时直接横向比较

Sample Comparison 支持：

- 同步 Zoom
- 相同 GT Overlay
- A Missed / B Correct
- Both Correct
- Misclassification
- Low IoU

Processing Inspector 只展示已注册 Pipeline 的固定 Stage，不允许拖拽、删除或任意重连。

---

## 5. 用户主流程

### 5.1 常规分析

```text
进入平台
  ↓
Recordings
  ↓
选择 / 导入 Recording
  ↓
Open Spectrum
  ↓
Spectrum Analysis
  ↓
选择 Pipeline
  ↓
Run Analysis
  ↓
BBox + Signal Type + Confidence
  ↓
选择 Signal
  ├─ View All → Signals
  └─ View Details → Signal Detail
```

### 5.2 科研比较

```text
Spectrum Analysis
  ↓
Compare in Algorithm Lab
  ↓
自动携带 Recording + 当前 Run
  ↓
选择另一 AnalysisRun / Pipeline
  ↓
Compare
  ↓
Overall Metrics
  ↓
Sample Comparison
  ↓
Case Study
  ↓
Processing Inspector
```

---

## 6. 核心数据对象

### 6.1 Recording

表示一段可分析宽带 IQ 数据。

建议字段：

- `id`
- `name`
- `data_path`
- `data_format`
- `data_type`
- `sample_rate_hz`
- `center_frequency_hz`
- `frequency_low_hz`
- `frequency_high_hz`
- `num_samples`
- `duration_s`
- `source`
- `dataset_name`
- `dataset_split`
- `label_space`
- `has_ground_truth`
- `created_at`

Recording 不等于 IQ 文件。不同原始格式由 Adapter 统一读取。

### 6.2 GroundTruth

单条 GT：

- `recording_id`
- `t_start_s`
- `t_end_s`
- `f_low_hz`
- `f_high_hz`
- `class_id`
- `class_name`

统一使用秒与 Hz。

### 6.3 LabelSpace

用于定义数据集或任务的类别空间。

SpaceNet 进阶库使用 `spacenet_14`，官方 class id 顺序为：

| class_id | class_name | 典型带宽（用户提供的服务器实测/指南记录） |
|---:|---|---|
| 0 | WiFi 20MHz QPSK | 20 MHz |
| 1 | WiFi 20MHz 16QAM | 20 MHz |
| 2 | WiFi 20MHz 64QAM | 20 MHz |
| 3 | WiFi 40MHz QPSK | 40 MHz |
| 4 | WiFi 40MHz 16QAM | 40 MHz |
| 5 | WiFi 40MHz 64QAM | 40 MHz |
| 6 | BLE LE1M | 1 MHz |
| 7 | BLE LE2M | 2 MHz |
| 8 | Zigbee | 2 MHz |
| 9 | LoRa 250kHz | 约 52–62 kHz（标签实测） |
| 10 | SRRC QPSK | 10 MHz |
| 11 | SRRC 16QAM | 10 MHz |
| 12 | AM | 约 200 kHz |
| 13 | FM | 约 120 kHz |

平台必须把该顺序固化为单一 `spacenet_14` LabelSpace 源，SpaceNetAdapter、GroundTruth、Pipeline 输出和前端展示均引用同一 mapping，禁止各模块自行复制一份类别列表。

Pipeline 必须声明自己的 LabelSpace。

### 6.4 Pipeline

表示完整分析算法，而非单一神经网络。

建议元数据：

- `id`
- `name`
- `version`
- `description`
- `label_space`
- `recommended_device`
- `cpu_supported`
- `stages`
- `inspectable_stages`
- `supported_parameters`

### 6.5 AnalysisRun

表示某 Pipeline 在某 Recording 上的一次执行。

字段：

- `id`
- `recording_id`
- `pipeline_id`
- `pipeline_version`
- `executor`
- `status`
- `parameters`
- `started_at`
- `finished_at`
- `runtime`
- `hardware_info`
- `result_count`
- `metrics`

状态：

- `pending`
- `running`
- `completed`
- `failed`
- `interrupted`

### 6.6 DetectionResult

所有 Pipeline 的强制统一输出。

字段：

- `id`
- `analysis_run_id`
- `t_start_s`
- `t_end_s`
- `f_low_hz`
- `f_high_hz`
- `class_id`
- `class_name`
- `confidence`
- `scores`（可选，如 detection score / classification score）

最终 `confidence` 定义由 Pipeline 决定，平台不自行把多阶段分数相乘。

### 6.7 IntermediateArtifact

可选对象，用于 Algorithm Lab 与 Signal Detail。

字段：

- `id`
- `analysis_run_id`
- `stage_name`
- `artifact_type`
- `scope`：recording / signal
- `detection_id`（signal-level 时）
- `path`
- `metadata`

通用 `artifact_type`：

- `spectrogram`
- `iq`
- `spectrum`
- `tensor`
- `detections`
- `image`
- `json`

### 6.8 Experiment

用于 Algorithm Lab 组织多个 AnalysisRun。

字段：

- `id`
- `name`
- `recording_scope`
- `pipeline_ids`
- `analysis_run_ids`
- `created_at`

V1 优先支持比较已有 Runs。

---

## 7. Pipeline 契约

核心原则：

> Pipeline Interface 是平台契约，不是算法内部模板。

不强迫所有算法实现相同的 `preprocess/detect/classify` 阶段。

逻辑接口：

```text
PipelineDefinition
PipelineInput
PipelineOutput
```

### 7.1 PipelineDefinition

描述：

- 我是谁
- 使用什么 LabelSpace
- 是否支持 CPU
- 推荐设备
- 有哪些 Stage
- 哪些 Stage 可查看
- 允许哪些推理参数

### 7.2 PipelineInput

包含：

- Recording
- Parameters
- Workspace

Pipeline 自行通过 Recording 读取 IQ，不由前端传大数组。

### 7.3 PipelineOutput

强制：

- `detections[]`

可选：

- `artifacts[]`
- `run_metadata`

---

## 8. 三种算法的统一接入

### 8.1 YOLO-STFT

```text
Recording
  ↓
STFT
  ↓
Spectrogram
  ↓
YOLO
  ↓
Pixel BBox + Class + Confidence
  ↓
Coordinate Adapter
  ↓
DetectionResult[]
```

可选 Artifact：STFT Spectrogram、YOLO raw detections。

### 8.2 End-to-End Wideband Recognition

```text
Recording
  ↓
STFT
  ↓
Detector
  ↓
BBox + Initial Class
  ↓
Signal Extraction
  ↓
Classifier
  ↓
Refined Class
  ↓
Result Merger
  ↓
DetectionResult[]
```

BBox 可来自第一阶段，最终类别来自 refined classification。

### 8.3 ZoomSpec

```text
Recording
  ↓
LS-STFT
  ↓
CPN
  ↓
Coarse Proposal
  ↓
AHLP
  ↓
Purified Baseband IQ
  ↓
FRN
  ↓
Fine Result
  ↓
DetectionResult[]
```

可选 Artifact：

- LS-STFT spectrogram + frequency mapping
- CPN proposals
- extracted IQ
- purified IQ
- FFT magnitude
- FRN intermediate/fine result

平台不要求其他 Pipeline 具备这些 Artifact。

---

## 9. Executor 设计

Pipeline 决定“怎么算”，Executor 决定“在哪里算”。

### 9.1 V1 Executor

- `LocalCPUExecutor`
- `ImportedRunExecutor`

### 9.2 V1.5

- `RemoteAutoDLExecutor`

AnalysisRun 记录：

- Executor 类型
- 原始硬件信息
- 运行环境

Latency 仅在硬件环境具有可比性时进行直接横向比较。

---

## 10. 技术栈

### 10.1 V1 推荐

| 层 | 技术 |
|---|---|
| Frontend | React + TypeScript + Vite |
| UI | Ant Design |
| General Charts | Apache ECharts |
| Spectrogram | 独立 SpectrogramViewer，V1 可用 Canvas + raster/axis metadata |
| Backend | FastAPI |
| ORM | SQLAlchemy |
| Database | SQLite |
| DSP | NumPy + SciPy |
| DL Inference | PyTorch |
| Job Execution | Local subprocess / simple Job Manager |
| Storage | Local Filesystem |
| GPU | V1 不要求本机 GPU |
| Heavy Compute | AutoDL / 外部 GPU |
| Deployment | 开发阶段不要求 Docker |

### 10.2 V1 明确不引入

- Redis
- Celery
- RQ
- PostgreSQL
- MinIO
- Kubernetes
- 微服务

除非后续明确出现真实需求并获得批准。

---

## 11. 后端模块

### 11.1 Recording Service

负责：

- 导入 Recording
- 读取 metadata
- 列表 / 详情 / 删除
- 读取 IQ segment

### 11.2 DSP Service

负责：

- STFT
- LS-STFT
- FFT
- PSD
- IQ slicing
- frequency shift
- filtering
- decimation

### 11.3 Pipeline Engine

负责执行注册好的 Pipeline，并强制转换最终输出为 DetectionResult。

### 11.4 Analysis Service

负责：

- 创建 AnalysisRun
- 状态管理
- 启动 subprocess
- 保存 DetectionResult
- 保存 Artifact
- 调用 Evaluation Service

### 11.5 Evaluation Service

负责：

- T-F IoU
- mAP
- Precision
- Recall
- Classification Accuracy / F1
- confusion matrix
- Prediction / GT matching

### 11.6 Experiment Service

负责：

- Existing Run comparison
- 指标汇总
- Ablation group
- Case Study 筛选

### 11.7 Storage Service

统一管理：

- Recording file
- Artifact
- Model weights
- Cache

业务代码禁止散落硬编码绝对路径。

---

## 12. 存储策略

### 12.1 SQLite 存结构化数据

- Recording metadata
- Pipeline metadata
- AnalysisRun
- DetectionResult
- Experiment
- Metrics
- Artifact metadata

### 12.2 文件系统存大文件

- Raw IQ
- STFT / LS-STFT matrix
- preview raster
- purified IQ
- FFT artifact
- model weights
- imported packages

### 12.3 推荐目录

```text
wideband-platform/
│
├── frontend/
├── backend/
├── pipelines/
├── adapters/
├── label_spaces/
├── model_weights/
│
├── data/
│   ├── recordings/
│   ├── artifacts/
│   ├── imports/
│   └── cache/
│
├── scripts/
├── tests/
├── ARCHITECTURE.md
├── V1_SCOPE.md
└── platform.db
```

---

## 13. Spectrogram 规范

平台存储应保留真实数值矩阵，不能只保存 PNG。

V1 Web 显示可以使用：

```text
matrix.npy
   ↓
Backend render
   ↓
WebP/PNG raster
   +
axis metadata
```

### 13.1 STFT

可使用线性 frequency mapping。

### 13.2 LS-STFT

必须提供显式 frequency axis 或等价 mapping metadata。

前端所有 hover、BBox overlay、GT overlay 必须基于真实时间/频率坐标，而非假定每一行代表固定频宽。

---

## 14. Adapter 设计

原始数据格式与平台内部格式分离：

```text
SpaceNet / MAT / BIN / SigMF / Custom IQ
             ↓
          Adapter
             ↓
     Recording + GroundTruth
```

建议接口：

- `scan_dataset()`
- `read_recording_metadata()`
- `read_iq()`
- `read_labels()`
- `get_label_space()`
- `create_recording()`

### 14.1 SpaceNetAdapter

根据用户提供的 AutoDL 服务器实测与本地复现指南，SpaceNet 进阶库真实契约如下。该契约作为 V1 Adapter 的实施输入。

真实目录：

```text
SpaceNet_Dataset/advanced/
├── train/    # 7500 对 .bin + .json；按 stem 配对
└── test/     # 2500 对 .bin + .json；按 stem 配对
```

每个样本：

- `<stem>.bin`：小端 `float16` 交错 I/Q，布局为 `I0,Q0,I1,Q1,...`。每个复采样点占 4 字节，因此 `num_complex_samples = file_size_bytes / 4`。
- `<stem>.json`：标签与观测频段 metadata。`.bin` 与 `.json` 必须按相同 stem 成对，stem 是样本主键。

JSON 核心字段：

```json
{
  "observation_range": [2401.0, 2431.0],
  "signals": [
    {
      "signal_id": 0,
      "start_frequency": 2417.97385,
      "end_frequency": 2418.02615,
      "start_time": 32.0,
      "end_time": 80.0,
      "class": 9
    }
  ]
}
```

单位与派生规则：

- `observation_range`：MHz；`sample_rate_hz = (hi_mhz - lo_mhz) * 1e6`。
- `center_frequency_hz = ((hi_mhz + lo_mhz) / 2) * 1e6`。
- `start_frequency` / `end_frequency`：绝对频率，MHz；平台转换为 Hz。
- `start_time` / `end_time`：ms；平台转换为 s。
- `duration_s = num_complex_samples / sample_rate_hz`。
- `class`：0–13，使用 `spacenet_14`。

校验规则：

- `lo <= start_frequency < end_frequency <= hi`。
- `0 <= start_time < end_time <= duration_ms`。
- `0 <= class <= 13`。
- `start_time == 0` 或 `end_time == duration_ms` 的边界截断信号是合法 GT，不得丢弃。
- `.bin` 字节数必须能被 4 整除。

SpaceNetAdapter 负责：

1. 扫描 `advanced/train` 与 `advanced/test`。
2. 按 stem 严格配对 `.bin` / `.json`，报告缺失配对。
3. 使用小端 float16 读取交错 I/Q，并转换为平台内部复数数组。
4. 从 `observation_range` 派生 sample rate、中心频率与观察频段。
5. 从文件大小与 sample rate 派生 duration。
6. 将 ms/MHz 标签统一转换为 seconds/Hz GroundTruth。
7. 将 class id 映射到 `spacenet_14`。
8. 保留边界截断 GT。
9. 允许平台 Recording 引用原始 `.bin` 路径，V1 不要求复制整个数据集。

ZoomSpec 相关实现约束：CPN 只承担 3 个带宽档粗检，14 类信号制式识别由 FRN 承担；这一内部结构属于 ZoomSpecPipeline，不应泄漏为 SpaceNetAdapter 的职责。

---

## 15. Analysis Package 规范

用途：AutoDL / 外部 GPU 结果与本地平台之间的正式交换格式。

### 15.1 最低合法包

```text
analysis_package/
├── manifest.json
└── detections.json
```

### 15.2 完整包

```text
analysis_package/
├── manifest.json
├── detections.json
├── metrics.json
└── artifacts/
    ├── index.json
    ├── ls_stft/
    ├── cpn/
    ├── signal_001/
    └── ...
```

### 15.3 manifest.json 必备信息

- `schema_version`
- pipeline id / name / version
- label_space
- recording name / dataset metadata
- executor
- execution hardware
- detections path
- metrics path（可选）
- artifact index（可选）

### 15.4 detections.json

单条 DetectionResult：

- `id`
- `t_start_s`
- `t_end_s`
- `f_low_hz`
- `f_high_hz`
- `class_id`
- `class_name`
- `confidence`
- `scores`（可选）

### 15.5 metrics.json

指标必须同时记录评价条件，例如：

- mAP@50
- mAP@50:95
- Precision
- Recall
- Accuracy / Macro F1
- IoU threshold 定义
- LabelSpace

### 15.6 Artifact

使用 `artifacts/index.json` 注册：

- stage
- type
- scope
- detection_id（可选）
- path
- metadata

Artifact 缺失不得阻止 AnalysisRun 导入，只影响 Processing Inspector 可展示内容。

### 15.7 导入规则

```text
ZIP
 ↓
解压到 temp
 ↓
校验 manifest
 ↓
校验 schema_version
 ↓
校验 detections
 ↓
用户匹配本地 Recording
 ↓
全部通过后创建 AnalysisRun
```

禁止先向 SQLite 写入部分记录再校验。

V1 采用人工确认 Recording 匹配，不要求复杂自动 fingerprint。

---

## 16. API 与页面数据流

V1 使用 REST + Polling，不使用 WebSocket。

建议核心 API：

```text
GET    /api/recordings
POST   /api/recordings
GET    /api/recordings/{id}
GET    /api/recordings/{id}/spectrogram
GET    /api/recordings/{id}/ground-truth
GET    /api/recordings/{id}/waveform

GET    /api/pipelines
GET    /api/pipelines/{id}

POST   /api/analysis-runs
GET    /api/analysis-runs/{id}
GET    /api/analysis-runs/{id}/detections
GET    /api/analysis-runs/{id}/artifacts

GET    /api/detections/{id}

POST   /api/imported-runs

POST   /api/experiments
GET    /api/experiments/{id}
```

### 16.1 Run Analysis

```text
Frontend
  ↓ POST /analysis-runs
FastAPI
  ↓ validate
Create AnalysisRun(pending)
  ↓ subprocess
Pipeline Engine
  ↓
DetectionResult + Artifact
  ↓
SQLite / Storage
  ↓
AnalysisRun(completed)
```

前端每 1-2 秒轮询 Run 状态。

### 16.2 Signal Detail

数据按需加载：

- Summary 首先加载
- I/Q Tab 打开时再读取波形
- FFT / PSD 按需计算或读取 Artifact

大数组不通过前端长期搬运；后端进行适当 display decimation。

---

## 17. 错误与恢复

标准 Run 状态：

- pending
- running
- completed
- failed
- interrupted

标准业务错误：

- `INVALID_RECORDING`
- `PIPELINE_INCOMPATIBLE`
- `MISSING_MODEL`
- `EXECUTOR_UNAVAILABLE`
- `ANALYSIS_FAILED`
- `INVALID_IMPORT_PACKAGE`
- `ARTIFACT_NOT_AVAILABLE`

前端显示业务化错误；Python traceback 保留在后台日志。

平台启动时，遗留 `running` Run 可标记为 `interrupted`，V1 不自动恢复长任务。

Artifact 保存失败不应自动判定整个 Run 失败，只要 DetectionResult 已成功产生。

Recording / Package 导入采用 temp → validate → commit 模式，避免半成品数据。

---

## 18. 测试策略

V1 不建设庞大企业测试体系，采用少量高价值测试。

### 18.1 Tiny Demo Dataset

准备小型固定 Recording 与 Ground Truth，支持数秒内完成测试。

### 18.2 固定 Analysis Package Fixture

准备：

```text
tests/fixtures/demo_analysis_package.zip
```

即使本机无 GPU 或真实模型暂不可用，也能完整测试主 UI 与 Algorithm Lab。

### 18.3 四类测试

1. 数据格式测试
   - Recording schema
   - Analysis Package schema
   - LabelSpace
2. 坐标测试（P0）
   - pixel ↔ seconds / Hz
   - STFT / LS-STFT physical coordinate consistency
3. Pipeline Contract Test
   - 使用 DummyPipeline 打通 Recording → AnalysisRun → DetectionResult
4. UI Smoke Test
   - Recordings → Spectrum → Signal → Detail
   - Import Run → Algorithm Lab → A/B Comparison

---

## 19. 开发里程碑

采用垂直切片，确保每 1-2 个阶段即可看到可运行成果。

### M0：Mock UI

- 五个页面骨架
- Mock spectrogram
- Mock detections
- BBox 点击与详情跳转

### M1：真实 Recording

- SQLite
- Local Filesystem
- Custom IQ Import
- Recording 列表 / 详情

### M2：真实 Spectrogram

- IQ → STFT
- Spectrogram Viewer
- Zoom / Pan / 时间频率坐标

### M3：DetectionResult 主链

- 手工 detections.json
- BBox Overlay
- Signals
- Signal Detail
- Ground Truth

### M4：AnalysisRun + DummyPipeline

打通：

```text
Frontend → FastAPI → AnalysisRun → subprocess → DummyPipeline → DetectionResult → SQLite → UI
```

### M5：第一条真实 CPU Pipeline

先接简单 detector / YOLO-STFT，不追求 SOTA，验证 Pipeline Contract。

### M6：Imported Run

- Analysis Package schema
- AutoDL Result Import
- Recording Match

### M7：SpaceNetAdapter

根据实际下载的进阶库实现真实文件读取、GT、14 类 mapping。

### M8：Algorithm Lab

先做 Existing Run A vs Run B：

- metrics
- sample comparison
- case study

### M9：End-to-End Pipeline

接 Detector + Signal Extraction + Classifier，并验证平台无需大改。

### M10：ZoomSpec

接：

- LS-STFT
- CPN
- AHLP
- FRN
- Intermediate Artifacts
- Processing Inspector

---

## 20. Agent 开发守则

所有 Coding Agent 必须先阅读 `ARCHITECTURE.md` 与 `V1_SCOPE.md`。

必须遵守：

1. V1 local-first、CPU-first。
2. 未经明确批准不得引入 Redis / Celery / PostgreSQL / MinIO / Kubernetes。
3. Raw IQ 保留在后端存储，不在前端大规模搬运。
4. 所有最终 detection 坐标统一使用 seconds + Hz。
5. 所有 Pipeline 必须输出 DetectionResult。
6. 不统一限制 Pipeline 内部算法结构。
7. Artifact 是可选增强，不是接入前提。
8. 重型 GPU 结果可通过 Analysis Package 导入。
9. SpaceNet 特定逻辑只能存在于 SpaceNetAdapter / LabelSpace 适配层。
10. Frontend 不实现核心 DSP。
11. 不把训练平台塞入 V1。
12. 不为“未来可能需要”提前增加基础设施。

---

## 21. V1 验收场景

平台至少应能够完成以下演示：

1. 打开 Recordings 首页。
2. 选择一份 SpaceNet Demo Recording。
3. 打开宽带 Spectrogram。
4. 查看 STFT，并可切换 LS-STFT。
5. 打开一份已有 AnalysisRun 或运行 CPU-compatible Pipeline。
6. 在 Spectrogram 上叠加多个 BBox、Signal Type 与 Confidence。
7. 打开 / 关闭 Ground Truth。
8. 点击某个 Signal，在右侧查看中心频率、带宽、时间与类型。
9. 进入 Signals 查看全部结果。
10. 进入 Signal Detail 查看 Local Spectrogram、PSD、I/Q、FFT。
11. 导入 AutoDL 生成的 Analysis Package。
12. 在 Algorithm Lab 比较两个已有 Runs。
13. 查看总体 mAP / Precision / Recall。
14. 查看一个 A Missed / B Correct 的样本级案例。
15. 对支持 Artifact 的 Pipeline 打开 Processing Inspector。

完成该流程即可视为 V1 主产品与科研链路闭环。

---

## 22. 关键架构原则摘要

最终设计可归纳为：

> 以 Recording 为数据对象，以 Pipeline 为算法对象，以 Executor 为执行位置，以 AnalysisRun 为运行对象，以 DetectionResult 为统一输出，以 Artifact 表达可选中间过程，以 Experiment 承载科研比较。

平台坚持：

> 内部算法允许高度异构，对外结果必须高度统一；接口为未来留余地，实现只满足 V1。

