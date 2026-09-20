# WISA 试用交付说明 & 用户指南

> 交付内容：一个可以在本机独立运行的「宽带智能信号分析平台（V1）」，
> **内置了一条小型数据集、一条独立样本、一个可直接运行的检测算法**，
> 不需要 30 GB 的 SpaceNet 原始语料、不需要 GPU、不需要服务器。

---

## 1. 这份交付包含什么

| 内容 | 说明 |
|---|---|
| 平台代码 | 后端 FastAPI + 前端 React/Vite，纯本地运行 |
| **内置数据集 `Mini-SpaceNet`** | 从 SpaceNet test 集抽取的 **3 条真实样本**（`0`、`1`、`2`），含真值标注（GT） |
| **内置独立样本 `3`** | 同源的第 4 条样本，注册为「独立样本」，含真值标注 |
| **内置算法 `STFT Energy Detector`** | CPU 检测/定位流水线，免模型权重，开箱即可跑 |
| 数据目录 | `seed_data/`（随代码一起分发，勿删） |

首次启动时，平台会**自动**把 `seed_data/` 里的样本注册进数据库（可重复启动，不会重复导入）。

---

## 2. 环境要求

- **Python 3.11+**（后端）
- **Node.js 18+ / npm**（前端）
- Windows 10/11 或 Linux/macOS（本说明以 Windows 为例）
- 可选：一个装了 `torch` / `ultralytics` 的 Python 环境（用于以后接入模型类流水线）

---

## 3. 快速开始

### 3.1 一键脚本（推荐，Windows PowerShell）

```powershell
# 在仓库根目录执行
pwsh -File .\scripts\setup.ps1     # 建 venv、装后端&前端依赖、打印本机环境发现结果
pwsh -File .\scripts\start.ps1     # 启动后端(8000) + 前端(5173)
```

然后浏览器打开 <http://127.0.0.1:5173> 。

### 3.2 手动步骤

```powershell
# 1) 后端
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".\backend[dev]"

# 2) 前端
cd frontend
npm install
cd ..

# 3) 启动后端（新终端）
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000

# 4) 启动前端（新终端）
cd frontend
npm run dev -- --host 127.0.0.1 --port 5173
```

> 前端默认调用 `http://127.0.0.1:8000` 的后端；后端 CORS 只放行 `5173` 端口，
> 所以请保持前端在 **5173**。

---

## 4. 第一次打开后的完整流程

1. **数据管理**（侧边栏第一项）
   - 「数据集」标签页应看到 **`Mini-SpaceNet · test`**：样本数 3、真值标注 3。
   - 「独立样本」标签页应看到 **`3`**：`float16_interleaved_le`、有真值。
2. 点数据集卡片的 **浏览并分析样本** → 进入数据集详情
   - **概览与分析**：数据集的元信息 + 该数据集的分析列表 + `分析数据集`
   - **样本**：3 条样本，可 `打开样本`
3. 点某条样本 → 样本工作台（三个 Tab）
   - **基本信息**：信号数据形状 / 持续时间 / 信号格式 ｜ 频率范围 / Fs / Fc
   - **可视化**：时域 / 频谱 / 时频图
   - **检测记录**：时间 + 算法流水线 + 来源标记（本平台检测 / 外部导入），无哈希
4. 右上 **时频定位与识别** → 频谱工作台
   - 选中 **STFT Energy Detector**，点 **开始分析**
   - 完成后：时频图叠加检测框（橙色）与真值框（**白色虚线 + 编号**），右侧列出检测结果
   - 勾选 **检测结果** 控制橙色框；**已选中的检测结果（红框）始终显示**，与勾选无关
5. **数据集分析**：数据集详情 → 概览与分析 → **分析数据集** → 全部 3 条跑完
   - 因为有真值，会自动生成 **评测**（mAP@0.5 / mAP@0.5:0.95 / 精确率 / 召回率 / F1）
6. **对比**：在同一数据集里选两条评测 → **对比评测**（指标差值与样本差异；只呈现事实，不排名）
7. **导出 / 导入**
   - 分析详情页 → **导出结果** → 得到一个 `.zip`（**不含原始 IQ、不含本机路径**）
   - 另一台机器注册同一数据集后 → 数据管理 → 数据集卡片 **导入检测结果** → 选该 zip
   - 导入**不会重跑**推理；重复导入同一包会提示「此前已导入」

---

## 5. 内置数据说明（重要）

- `Mini-SpaceNet` 的 3 条样本与独立样本 `3` 都是 **SpaceNet 的真实 `bin`+`json` 文件**，
  只是数量被裁到最小，便于快速跑通全流程。
- 数据集与样本使用与完整 SpaceNet 完全相同的适配器、指纹与真值语义；
  因此**导出→跨机导入**能正确匹配（匹配依据是内容指纹，与本机路径无关）。
- `seed_data/` 是必需目录：删除后平台仍可启动，但不会再自动出现内置数据。

> 关于评测指标：内置的 `STFT Energy Detector` 是**类别无关**的能量检测器（只做“有/无信号”的时频定位），
> 而 SpaceNet 的真值是 14 类标注。因此用它跑出来的 mAP / 精确率会**偏低**，这是**预期行为**，不是缺陷：
> 该检测器的用途是验证「数据 → 分析 → 评测 → 对比 → 导出/导入」整条链路，而不是卷指标。
> 若要得到有意义的分类指标，需要接入带模型权重的分类流水线（见 §6）。

---

## 6. 本地运行环境（算法执行）如何配置

平台把「算法流水线」与「本地运行环境」分开：

- **内置 `STFT Energy Detector` 不需要任何额外环境**。默认情况下平台用一个内置的
  Standard 本地 CPU 路径直接运行它（用控制面 Python，仅需 numpy/scipy）。
- 未来接入**模型类流水线**（如 YOLO/ZoomSpec）时，需要指定一个**装了 `torch` / `ultralytics`
  的 Python 解释器**（可以是 conda 环境）。平台**不会**自己去 pip 安装这些库。

### 6.1 平台能自动发现什么

平台会扫描并列出本机的候选解释器（只读诊断，不会改动任何配置）：

- 显式配置的：`WSP_LOCAL_CPU_PYTHON_PATH`
- 控制面自身解释器
- 额外声明的：`WSP_EXTRA_PYTHON_PATHS`（`;` 分隔的路径列表）
- **conda 环境**：`CONDA_ENVS_PATH` / `CONDA_PREFIX` / `CONDA_EXE` 以及常见安装目录下的 `envs/*`

界面位置：频谱工作台 → **为什么不能运行？**（或「环境详情」）→ 「本机找到的 Python 环境」，
会显示每个环境的 **Python 版本 + 是否装 torch / ultralytics**。
命令行等价：

```powershell
wisa runtime discover
```

### 6.2 如果发现不到（或想指定自己的环境）

```powershell
# 1) 先看本机有哪些环境、哪个有 torch/ultralytics
wisa runtime discover

# 2) 为选定的解释器生成「运行身份」（runtime_ref 必须推导，不能自己编）
wisa runtime ref --python D:\path\to\env\python.exe --family myenv
#   -> runtime_ref: local:myenv:cpu:xxxxxxxxxxxx

# 3) 把它设为平台要用的本地 CPU 环境（启动后端前设置）
$env:WSP_LOCAL_CPU_PYTHON_PATH = "D:\path\to\env\python.exe"
$env:WSP_LOCAL_CPU_RUNTIME_REF  = "local:myenv:cpu:xxxxxxxxxxxx"
$env:WSP_RUNTIME_FAMILY        = "myenv"

# 4) 让平台在该环境上认证内置算法（免模型权重，立即通过）
wisa qualify --plugin stft_energy_detector --executor local_cpu
wisa certificate install --from <上一步输出里的 evidence_dir>

# 5) 重启后端，验证
#    GET /api/executor-selection?pipeline_id=stft_energy_detector&recording_id=<id>
#    期望 certified=true, available=true
```

> ⚠️ **必须知道的坑**：一旦设置了 `WSP_LOCAL_CPU_PYTHON_PATH` + `WSP_LOCAL_CPU_RUNTIME_REF`，
> 平台会启用「严格本地环境」，并**不再**使用内置 Standard 回退；此时**没有证书的算法（包括内置的
> STFT）都会变成不可运行**。所以第 4 步的 qualify + certificate install 是**必需**的，不是可选。

### 6.3 交给 AI Agent 去配（推荐）

把下面整段发给你的编码 Agent（Cursor / Claude Code / Codex / opencode 等），让它照着做：

```text
目标：让本目录的 WISA 平台能在本机 CPU 上运行算法流水线，尽可能使用已有的 ML 环境。

约束：
- 不要修改任何应用代码；不要伪造/放宽执行证书；不要往平台运行环境之外安装东西。
- 如果做不下去，停下来报告卡在哪一步和原始报错，不要猜测。

步骤：
1. 用本仓库的虚拟环境安装后端依赖（python -m pip install -e ".\backend[dev]"），并 npm install（frontend）。
2. 运行 `wisa runtime discover`，列出所有候选解释器及其 Python 版本、torch / ultralytics 是否可用。
3. 选择「同时具备 torch 和 ultralytics」的解释器（conda 环境也可以）；若没有，报告缺什么库。
4. 运行 `wisa runtime ref --python <该解释器路径> --family <自定义名字>`，记录输出的 runtime_ref。
5. 设置环境变量后运行：
   WSP_LOCAL_CPU_PYTHON_PATH=<解释器> WSP_LOCAL_CPU_RUNTIME_REF=<上一步的 ref> WSP_RUNTIME_FAMILY=<family>
   `wisa qualify --plugin stft_energy_detector --executor local_cpu`
   然后 `wisa certificate install --from <evidence_dir>`
6. 启动后端（uvicorn app.main:app --app-dir backend --port 8000），请求
   `/api/executor-selection?pipeline_id=stft_energy_detector&recording_id=<任一录音 id>`
   确认返回 certified=true 且 available=true。
7. 汇报：使用的解释器、runtime_ref、证书是否创建、executor-selection 结果、以及任何报错原文。
```

---

## 7. 常见问题

| 现象 | 原因 / 处理 |
|---|---|
| 打开页面看不到 `Mini-SpaceNet` | 确认仓库根目录存在 `seed_data/mini-spacenet/test/{0,1,2}.{bin,json}`；删掉 `platform.db` 后重启会自动重新注册 |
| 「开始分析」按钮灰着 | 该算法在当前环境不可运行；点 **为什么不能运行？** 看每个执行器的原因 |
| 配置了本地环境后内置算法反而不能用 | 见 §6.2 的坑：必须跑 qualify + certificate install |
| 导出的包很大 | Analysis Bundle **只含计算结果**（detections），不含原始 IQ；若很大请确认没有把 IQ 打进去 |
| 端口被占用 | 后端 `8000`、前端 `5173`；本说明假设这两个端口 |
| 想接入完整 SpaceNet（2500 条） | 数据管理 → 添加数据 → 注册数据集 → 填 SpaceNet `test` 目录路径（仅登记元数据，不复制 IQ） |
| 有一套自己的 IQ（int16/float16/complex64 交错）想单独看 | 数据管理 → 添加数据 → 添加独立 IQ → 注册本地路径，选对 **数据格式**：
`complex64_le`(8B/采样) / `float16_interleaved_le`(4B) / `int16_interleaved_le`(4B)，填 Fs/Fc 即可。<br>
若是 `server_dataset_generator` 生成的场景，用 `scripts/import_generated_iq_sample.py`（直接读原始 int16，无需转码）：<br>
`python scripts/import_generated_iq_sample.py --iq scene_000000.iq --label scene_000000.json --out <临时路径> --name pioneer-scene0` |

---

## 8. 术语速查

- **数据集 / Dataset**：一批同类样本（这里是 `Mini-SpaceNet`）。
- **样本 / Sample**：一条 IQ 录音（数据集成员，或「独立样本」）。
- **算法流水线 / Pipeline**：对样本执行的处理链（这里是 `STFT Energy Detector`）。
- **检测结果 / Detections**：流水线输出的时频框。
- **真值标注 / GT**：人工标注的时频框，用于评测。
- **数据集分析 / Dataset Analysis**：对数据集所有样本批量运行算法。
- **评测 / Evaluation**：数据集分析 + 真值 → mAP/精确率/召回率/F1。
- **Analysis Bundle**：把「分析结果」打包带走/导入，**不重跑推理**。
