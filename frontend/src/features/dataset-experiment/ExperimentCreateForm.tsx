import { Alert, Button, Input, InputNumber, Select, Space, Typography } from "antd";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { createDatasetExperiment, getExecutorSelection, listPipelines, PlatformApiError } from "../../api/client";
import type { ExecutionSelectionScope, PipelineDefinition } from "../../api/types";
import { useLocalization } from "../../localization/useLocalization";
import { ExecutionEnvironmentSelector } from "../execution-environment/ExecutionEnvironmentSelector";
import { effectiveSelectionForScope, optionsFromSelection, type BoundExecutorSelection } from "../execution-environment/executionEnvironment";
import type { ExecutionEnvironmentValue } from "../execution-environment/types";
import { toCreateRequest } from "./types";

function toErrorText(reason: unknown): string {
  if (reason instanceof PlatformApiError) return reason.display;
  if (reason instanceof Error) return reason.message;
  return String(reason);
}

export interface DatasetIdentityInput {
  datasetProjectionId: string;
  datasetName: string;
  datasetSplit: string;
  datasetLabelSpace: string;
}

export interface ExperimentCreateFormProps {
  onCreated?: (id: string) => void;
  initialDataset?: DatasetIdentityInput;
}

/**
 * DatasetExperiment create form.
 *
 * - When entered from a dataset projection, identity is prefilled and read-only,
 *   and execution selection uses the projection scope (`dataset_projection_id`),
 *   never the legacy name/split/label-space scope.
 * - No evaluation-protocol picker (backend default), `parameters: {}`.
 */
export function ExperimentCreateForm({ onCreated, initialDataset }: ExperimentCreateFormProps) {
  const { t } = useLocalization();
  const navigate = useNavigate();
  const projectionId = initialDataset?.datasetProjectionId ?? null;
  const [pipelines, setPipelines] = useState<PipelineDefinition[]>([]);
  const [name, setName] = useState("");
  const [datasetName, setDatasetName] = useState(initialDataset?.datasetName ?? "");
  const [datasetSplit, setDatasetSplit] = useState(initialDataset?.datasetSplit ?? "");
  const [datasetLabelSpace, setDatasetLabelSpace] = useState(initialDataset?.datasetLabelSpace ?? "");
  const [pipelineId, setPipelineId] = useState<string | null>(null);
  const [maxConcurrency, setMaxConcurrency] = useState(1);
  const [environment, setEnvironment] = useState<ExecutionEnvironmentValue>({ mode: "auto", executor: null });
  const [boundSelection, setBoundSelection] = useState<BoundExecutorSelection | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const identityLocked = projectionId !== null;
  const datasetScopeKey = JSON.stringify([projectionId, datasetName, datasetSplit, datasetLabelSpace, pipelineId]);
  const effectiveSelection = effectiveSelectionForScope(boundSelection, datasetScopeKey);

  useEffect(() => {
    let active = true;
    listPipelines()
      .then((items) => { if (active) setPipelines(items); })
      .catch((reason: unknown) => { if (active) setError(toErrorText(reason)); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    setBoundSelection(null);
    setEnvironment({ mode: "auto", executor: null });
    const identityReady =
      projectionId !== null || (datasetName !== "" && datasetSplit !== "" && datasetLabelSpace !== "");
    if (pipelineId === null || !identityReady) return undefined;
    const scopeKey = JSON.stringify([projectionId, datasetName, datasetSplit, datasetLabelSpace, pipelineId]);
    const scope: ExecutionSelectionScope =
      projectionId !== null
        ? { kind: "dataset_projection", datasetProjectionId: projectionId }
        : { kind: "dataset", datasetName, datasetSplit, datasetLabelSpace };
    let active = true;
    setLoading(true);
    void getExecutorSelection({ scope, pipelineId })
      .then((result) => { if (active) setBoundSelection({ scopeKey, value: result }); })
      .catch((reason: unknown) => { if (active) setError(toErrorText(reason)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [pipelineId, projectionId, datasetName, datasetSplit, datasetLabelSpace]);

  const selectedPipeline = useMemo(
    () => pipelines.find((item) => item.id === pipelineId) ?? null,
    [pipelines, pipelineId],
  );

  const environmentOptions = useMemo(() => optionsFromSelection(effectiveSelection), [effectiveSelection]);
  const selectedEnvironmentOption = useMemo(
    () => environmentOptions.find((option) => option.key === (environment.mode === "auto" ? "auto" : environment.executor)) ?? null,
    [environmentOptions, environment.mode, environment.executor],
  );
  const identityValid =
    selectedPipeline !== null &&
    name.trim() !== "" &&
    (identityLocked || (datasetName.trim() !== "" && datasetSplit.trim() !== "" && datasetLabelSpace.trim() !== ""));
  const canCreate = identityValid && selectedEnvironmentOption?.enabled === true && !submitting;

  const submit = async () => {
    setError(null);
    if (!canCreate) return;
    if (selectedPipeline === null) {
      setError(t("form.selectPipelineError"));
      return;
    }
    setSubmitting(true);
    try {
      const request = toCreateRequest({
        name,
        datasetProjectionId: projectionId,
        datasetName,
        datasetSplit,
        datasetLabelSpace,
        pluginId: selectedPipeline.id,
        pluginVersion: selectedPipeline.version,
        environment,
        maxConcurrency,
      });
      const created = await createDatasetExperiment(request);
      if (onCreated) onCreated(created.id);
      navigate(`/experiments/${created.id}`);
    } catch (reason) {
      setError(toErrorText(reason));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      {error !== null ? <Alert type="error" showIcon message={t("experiment.createError")} description={error} /> : null}
      <Space direction="vertical" size={4} style={{ width: "100%" }}>
        {identityLocked ? (
          <Typography.Text type="secondary">{t("experiment.datasetIdentityLocked")}</Typography.Text>
        ) : null}
        <Typography.Text>{t("form.name")}</Typography.Text>
        <Input aria-label={t("form.name")} value={name} onChange={(event) => setName(event.target.value)} />
        <Typography.Text>{t("form.datasetName")}</Typography.Text>
        <Input aria-label={t("form.datasetName")} readOnly={identityLocked} value={datasetName} onChange={(event) => setDatasetName(event.target.value)} />
        <Typography.Text>{t("form.datasetSplit")}</Typography.Text>
        <Input aria-label={t("form.datasetSplit")} readOnly={identityLocked} value={datasetSplit} onChange={(event) => setDatasetSplit(event.target.value)} />
        <Typography.Text>{t("form.labelSpace")}</Typography.Text>
        <Input aria-label={t("form.labelSpace")} readOnly={identityLocked} value={datasetLabelSpace} onChange={(event) => setDatasetLabelSpace(event.target.value)} />
        <Typography.Text>{t("form.pipeline")}</Typography.Text>
        <Select
          aria-label={t("form.pipeline")}
          style={{ width: 360 }}
          value={pipelineId ?? undefined}
          onChange={setPipelineId}
          options={pipelines.map((item) => ({ value: item.id, label: `${item.name} ${item.version}` }))}
        />
        <Typography.Text>{t("form.maxConcurrency")}</Typography.Text>
        <InputNumber
          aria-label={t("form.maxConcurrency")}
          min={1}
          value={maxConcurrency}
          onChange={(value) => setMaxConcurrency(value ?? 1)}
        />
      </Space>
      <ExecutionEnvironmentSelector
        selection={effectiveSelection}
        loading={loading}
        error={null}
        value={environment}
        onChange={setEnvironment}
      />
      <Button type="primary" disabled={!canCreate} loading={submitting} onClick={() => void submit()}>{t("common.createExperiment")}</Button>
    </Space>
  );
}
