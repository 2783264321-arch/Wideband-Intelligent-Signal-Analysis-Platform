import { Alert, Button, Input, InputNumber, Select, Space, Typography } from "antd";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { createDatasetExperiment, getExecutorSelection, listPipelines, PlatformApiError } from "../../api/client";
import type { PipelineDefinition } from "../../api/types";
import { ExecutionEnvironmentSelector } from "../execution-environment/ExecutionEnvironmentSelector";
import { effectiveSelectionForScope, optionsFromSelection, type BoundExecutorSelection } from "../execution-environment/executionEnvironment";
import type { ExecutionEnvironmentValue } from "../execution-environment/types";
import { toCreateRequest } from "./types";

function toErrorText(reason: unknown): string {
  if (reason instanceof PlatformApiError) return reason.display;
  if (reason instanceof Error) return reason.message;
  return String(reason);
}

/**
 * DatasetExperiment create form.
 *
 * - Dataset identity is the exact triple (no generic dataset catalog).
 * - Pipeline id/version come from the `/api/pipelines` projection.
 * - Execution environment uses dataset-scoped `/api/executor-selection` only
 *   (never the recording-scoped `/api/executor-availability`).
 * - No evaluation-protocol picker (backend default), `parameters: {}`.
 */
export function ExperimentCreateForm({ onCreated }: { onCreated?: (id: string) => void }) {
  const navigate = useNavigate();
  const [pipelines, setPipelines] = useState<PipelineDefinition[]>([]);
  const [name, setName] = useState("");
  const [datasetName, setDatasetName] = useState("");
  const [datasetSplit, setDatasetSplit] = useState("");
  const [datasetLabelSpace, setDatasetLabelSpace] = useState("");
  const [pipelineId, setPipelineId] = useState<string | null>(null);
  const [maxConcurrency, setMaxConcurrency] = useState(1);
  const [environment, setEnvironment] = useState<ExecutionEnvironmentValue>({ mode: "auto", executor: null });
  const [boundSelection, setBoundSelection] = useState<BoundExecutorSelection | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const datasetScopeKey = JSON.stringify([datasetName, datasetSplit, datasetLabelSpace, pipelineId]);
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
    // A scope identity change resets the user execution value to Auto; a stale
    // manual executor can never authorize a different dataset/pipeline scope.
    setEnvironment({ mode: "auto", executor: null });
    if (pipelineId === null || datasetName === "" || datasetSplit === "" || datasetLabelSpace === "") return undefined;
    const scopeKey = JSON.stringify([datasetName, datasetSplit, datasetLabelSpace, pipelineId]);
    let active = true;
    setLoading(true);
    void getExecutorSelection({
      scope: {
        kind: "dataset",
        datasetName,
        datasetSplit,
        datasetLabelSpace,
      },
      pipelineId,
    })
      .then((result) => { if (active) setBoundSelection({ scopeKey, value: result }); })
      .catch((reason: unknown) => { if (active) setError(toErrorText(reason)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [pipelineId, datasetName, datasetSplit, datasetLabelSpace]);

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
    datasetName.trim() !== "" &&
    datasetSplit.trim() !== "" &&
    datasetLabelSpace.trim() !== "";
  const canCreate = identityValid && selectedEnvironmentOption?.enabled === true && !submitting;

  const submit = async () => {
    setError(null);
    if (!canCreate) return;
    if (selectedPipeline === null) {
      setError("Select a pipeline.");
      return;
    }
    setSubmitting(true);
    try {
      const request = toCreateRequest({
        name,
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
      {error !== null ? <Alert type="error" showIcon message="Unable to create experiment" description={error} /> : null}
      <Space direction="vertical" size={4} style={{ width: "100%" }}>
        <Typography.Text>Name</Typography.Text>
        <Input aria-label="Name" value={name} onChange={(event) => setName(event.target.value)} />
        <Typography.Text>Dataset name</Typography.Text>
        <Input aria-label="Dataset name" value={datasetName} onChange={(event) => setDatasetName(event.target.value)} />
        <Typography.Text>Dataset split</Typography.Text>
        <Input aria-label="Dataset split" value={datasetSplit} onChange={(event) => setDatasetSplit(event.target.value)} />
        <Typography.Text>Label space</Typography.Text>
        <Input aria-label="Label space" value={datasetLabelSpace} onChange={(event) => setDatasetLabelSpace(event.target.value)} />
        <Typography.Text>Pipeline</Typography.Text>
        <Select
          aria-label="Pipeline"
          style={{ width: 360 }}
          value={pipelineId ?? undefined}
          onChange={setPipelineId}
          options={pipelines.map((item) => ({ value: item.id, label: `${item.name} ${item.version}` }))}
        />
        <Typography.Text>Max concurrency</Typography.Text>
        <InputNumber
          aria-label="Max concurrency"
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
      <Button type="primary" disabled={!canCreate} loading={submitting} onClick={() => void submit()}>Create Experiment</Button>
    </Space>
  );
}
