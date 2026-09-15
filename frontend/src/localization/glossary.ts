/**
 * Normative domain glossary (concept key -> professional English / Chinese).
 * Components consume message keys, never ad-hoc literal pairs; this map
 * documents and test-verifies the controlled signal-domain terminology.
 */
export const DOMAIN_GLOSSARY = {
  recordings: { en: "Recordings", zh: "信号记录" },
  spectrumAnalysis: { en: "Spectrum Analysis", zh: "频谱分析" },
  spectrogram: { en: "Spectrogram", zh: "时频图" },
  signals: { en: "Signals", zh: "信号检测结果" },
  signalDetail: { en: "Signal Detail", zh: "检测结果详情" },
  classification: { en: "Classification", zh: "信号分类" },
  groundTruth: { en: "Ground Truth", zh: "真值标注（GT）" },
  analysisRun: { en: "Analysis Run", zh: "分析任务" },
  datasetExperiment: { en: "Dataset Experiment", zh: "数据集实验" },
  experimentItem: { en: "Experiment Item", zh: "实验样本" },
  attempt: { en: "Attempt", zh: "执行尝试" },
  evaluation: { en: "Evaluation", zh: "评测" },
  datasetEvaluation: { en: "Dataset Evaluation", zh: "数据集评测" },
  benchmark: { en: "Benchmark", zh: "基准评测" },
  compare: { en: "Compare", zh: "对比" },
  algorithmLab: { en: "Algorithm Lab", zh: "算法评测实验室" },
  caseAnalysis: { en: "Case Analysis", zh: "单记录分析" },
  caseComparison: { en: "Case Comparison", zh: "信号实例对比" },
  executionEnvironment: { en: "Execution Environment", zh: "执行环境" },
  pipeline: { en: "Pipeline", zh: "算法流水线" },
  provenance: { en: "Provenance", zh: "运行溯源信息" },
  retryFailedItems: { en: "Retry Failed Items", zh: "重试失败样本" },
  retryEvaluation: { en: "Retry Evaluation", zh: "重试评测" },
  perClassMetrics: { en: "Per-class Metrics", zh: "分类别指标" },
  confusion: { en: "Confusion", zh: "混淆统计" },
  sharedRecording: { en: "Shared Recording", zh: "共有信号记录" },
  auto: { en: "Auto", zh: "自动选择" },
  localCpu: { en: "Local CPU", zh: "本地 CPU" },
  localGpu: { en: "Local GPU", zh: "本地 GPU" },
  remoteGpu: { en: "Remote GPU", zh: "远程 GPU" },
} as const;
