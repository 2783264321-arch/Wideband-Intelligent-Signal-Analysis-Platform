import { Alert, Button, Descriptions, Input, Select, Space, Typography } from "antd";
import { useEffect, useState } from "react";
import { createDatasetBenchmark, listImportedBenchmarkBatches, PlatformApiError, resolveImportedBenchmarkBatch, runDatasetBenchmark } from "../../api/client";
import type { ImportedBatchResolution, ImportedBenchmarkBatch } from "../../api/types";

function toErrorText(error: unknown): string {
  if (error instanceof PlatformApiError) return error.display;
  if (error instanceof Error) return error.message;
  return String(error);
}

export function BenchmarkCreatePanel({ onCreated }: { onCreated: (id: string) => void }) {
  const [batches, setBatches] = useState<ImportedBenchmarkBatch[]>([]);
  const [fingerprint, setFingerprint] = useState<string>();
  const [resolution, setResolution] = useState<ImportedBatchResolution>();
  const [benchmarkName, setBenchmarkName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const readyToCreate = Boolean(
    resolution &&
    resolution.resolvedRecordings === resolution.expectedRecordings &&
    resolution.missingRecordings === 0 &&
    resolution.conflictCount === 0 &&
    benchmarkName.trim()
  );

  useEffect(() => {
    void listImportedBenchmarkBatches().then(setBatches).catch((e: unknown) => setError(toErrorText(e)));
  }, []);

  const resolve = async () => {
    if (!fingerprint) return;
    setBusy(true);
    setError(undefined);
    try {
      setResolution(await resolveImportedBenchmarkBatch(fingerprint));
    } catch (e) {
      setError(toErrorText(e));
    } finally {
      setBusy(false);
    }
  };

  const createAndRun = async () => {
    if (!resolution || !readyToCreate) return;
    setBusy(true);
    setError(undefined);
    try {
      const created = await createDatasetBenchmark({ name: benchmarkName.trim(), resolution });
      const started = await runDatasetBenchmark(created.id);
      onCreated(started.id);
    } catch (e) {
      setError(toErrorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      {error ? <Alert type="error" message={error} /> : null}
      <Select
        aria-label="Imported Analysis Batch"
        value={fingerprint}
        placeholder="Select an imported batch"
        onChange={(value) => { setFingerprint(value); setResolution(undefined); }}
        options={batches.map((batch) => ({
          value: batch.importFingerprint,
          disabled: !batch.ready,
          label: batch.ready
            ? `${batch.pipelineId} · ${batch.datasetName}/${batch.datasetSplit} · ${batch.runCount} runs`
            : `${batch.importFingerprint.slice(0, 12)}… · invalid provenance`,
        }))}
        style={{ width: "100%" }}
      />
      <Descriptions column={1} size="small">
        <Descriptions.Item label="Evaluation Protocol">
          <Typography.Text code>physical_tf_detection_ap_v2</Typography.Text>
        </Descriptions.Item>
        <Descriptions.Item label="GT Policy">exact physical/class dedup</Descriptions.Item>
        {resolution ? <>
          <Descriptions.Item label="Resolved">{resolution.resolvedRecordings} / {resolution.expectedRecordings}</Descriptions.Item>
          <Descriptions.Item label="Missing">{resolution.missingRecordings}</Descriptions.Item>
          <Descriptions.Item label="Conflicts">{resolution.conflictCount}</Descriptions.Item>
          <Descriptions.Item label="Manifest SHA256">{resolution.recordingManifestHash}</Descriptions.Item>
        </> : null}
      </Descriptions>
      <Button onClick={() => void resolve()} disabled={!fingerprint} loading={busy}>Resolve</Button>
      <Input aria-label="Benchmark Name" placeholder="Benchmark name" value={benchmarkName} onChange={(e) => setBenchmarkName(e.target.value)} />
      <Button type="primary" onClick={() => void createAndRun()} disabled={!readyToCreate} loading={busy}>{"Create & Run"}</Button>
    </Space>
  );
}