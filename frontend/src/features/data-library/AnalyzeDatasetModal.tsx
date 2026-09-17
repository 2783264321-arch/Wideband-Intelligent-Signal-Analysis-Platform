import { Alert, Collapse, InputNumber, Modal, Select, Space, Typography } from "antd";
import { useEffect, useMemo, useState } from "react";
import {
  createDatasetExperiment,
  getExecutorSelection,
  listPipelines,
  PlatformApiError,
  runDatasetExperiment,
} from "../../api/client";
import type { DatasetSummary, ExecutionSelectionScope, PipelineDefinition } from "../../api/types";
import { useLocalization } from "../../localization/useLocalization";
import { ExecutionEnvironmentSelector } from "../execution-environment/ExecutionEnvironmentSelector";
import {
  effectiveSelectionForScope,
  optionsFromSelection,
  type BoundExecutorSelection,
} from "../execution-environment/executionEnvironment";
import type { ExecutionEnvironmentValue } from "../execution-environment/types";

function toErrorText(reason: unknown): string {
  if (reason instanceof PlatformApiError) return reason.display;
  if (reason instanceof Error) return reason.message;
  return String(reason);
}

/** Same pragmatic preference as single-sample analysis. */
function preferredPipelineId(pipelines: PipelineDefinition[]): string {
  const preferred =
    pipelines.find((item) => item.id === "stft_energy_detector") ??
    pipelines.find((item) => item.taskCapability === "detection_localization") ??
    pipelines[0];
  return preferred?.id ?? "";
}

export interface AnalyzeDatasetModalProps {
  dataset: DatasetSummary;
  open: boolean;
  onClose: () => void;
  onStarted: (experimentId: string) => void;
}

export function AnalyzeDatasetModal({ dataset, open, onClose, onStarted }: AnalyzeDatasetModalProps) {
  const { t } = useLocalization();
  const labelSpace = dataset.labelSpace ?? "_";
  const [pipelines, setPipelines] = useState<PipelineDefinition[]>([]);
  const [pipelineId, setPipelineId] = useState("");
  const [maxConcurrency, setMaxConcurrency] = useState(1);
  const [environment, setEnvironment] = useState<ExecutionEnvironmentValue>({ mode: "auto", executor: null });
  const [boundSelection, setBoundSelection] = useState<BoundExecutorSelection | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const scopeKey = JSON.stringify([dataset.id, pipelineId]);
  const effectiveSelection = effectiveSelectionForScope(boundSelection, scopeKey);

  useEffect(() => {
    if (!open) return undefined;
    let active = true;
    setError(null);
    listPipelines()
      .then((items) => {
        if (!active) return;
        setPipelines(items);
        setPipelineId((current) => preferredPipelineId(items) || current);
      })
      .catch((reason: unknown) => { if (active) setError(toErrorText(reason)); });
    return () => { active = false; };
  }, [open]);

  useEffect(() => {
    setBoundSelection(null);
    setEnvironment({ mode: "auto", executor: null });
    if (!open || pipelineId === "") return undefined;
    const scope: ExecutionSelectionScope = {
      kind: "dataset",
      datasetName: dataset.name,
      datasetSplit: dataset.split,
      datasetLabelSpace: labelSpace,
    };
    let active = true;
    setLoading(true);
    void getExecutorSelection({ scope, pipelineId })
      .then((result) => { if (active) setBoundSelection({ scopeKey, value: result }); })
      .catch((reason: unknown) => { if (active) setError(toErrorText(reason)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [open, pipelineId, dataset.id]);

  const selectedPipeline = useMemo(
    () => pipelines.find((item) => item.id === pipelineId) ?? null,
    [pipelines, pipelineId],
  );
  const environmentOptions = useMemo(() => optionsFromSelection(effectiveSelection), [effectiveSelection]);
  const selectedEnvironmentOption = useMemo(
    () => environmentOptions.find((option) => option.key === (environment.mode === "auto" ? "auto" : environment.executor)) ?? null,
    [environmentOptions, environment.mode, environment.executor],
  );
  const canStart = selectedPipeline !== null && selectedEnvironmentOption?.enabled === true && !submitting;

  const start = async () => {
    if (!canStart || selectedPipeline === null) return;
    setSubmitting(true);
    setError(null);
    try {
      const created = await createDatasetExperiment({
        name: `${dataset.name} · ${selectedPipeline.name}`,
        datasetId: dataset.id,
        datasetName: dataset.name,
        datasetSplit: dataset.split,
        datasetLabelSpace: dataset.labelSpace ?? "",
        pluginId: selectedPipeline.id,
        pluginVersion: selectedPipeline.version,
        executionMode: environment.mode,
        ...(environment.mode === "manual" && environment.executor !== null
          ? { executor: environment.executor }
          : {}),
        parameters: {},
        maxConcurrency,
      });
      await runDatasetExperiment(created.id);
      onStarted(created.id);
      onClose();
    } catch (reason) {
      setError(toErrorText(reason));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      open={open}
      title={t("datasetAnalysis.analyzeDataset")}
      confirmLoading={submitting}
      onOk={() => void start()}
      onCancel={onClose}
      okText={t("datasetAnalysis.startAnalysis")}
      okButtonProps={{ disabled: !canStart }}
      data-testid="analyze-dataset-modal"
    >
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        <div>
          <Typography.Text type="secondary">{t("datasetAnalysis.pipeline")}</Typography.Text>
          <Select
            data-testid="analyze-dataset-pipeline"
            style={{ width: "100%" }}
            value={pipelineId || undefined}
            onChange={setPipelineId}
            options={pipelines.map((item) => ({ value: item.id, label: item.name }))}
          />
        </div>
        <ExecutionEnvironmentSelector
          selection={effectiveSelection}
          loading={loading}
          error={null}
          value={environment}
          onChange={setEnvironment}
        />
        <Collapse
          ghost
          items={[{
            key: "advanced",
            label: t("datasetAnalysis.advanced"),
            children: (
              <Space>
                <Typography.Text>{t("datasetAnalysis.maxConcurrency")}</Typography.Text>
                <InputNumber
                  aria-label={t("datasetAnalysis.maxConcurrency")}
                  min={1}
                  value={maxConcurrency}
                  onChange={(value) => setMaxConcurrency(value ?? 1)}
                />
              </Space>
            ),
          }]}
        />
        {error ? <Alert type="error" showIcon message={t("datasetAnalysis.startError")} description={error} /> : null}
      </Space>
    </Modal>
  );
}
