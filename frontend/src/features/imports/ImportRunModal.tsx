import { Alert, Button, Form, Modal, Select, Typography } from "antd";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { importAnalysisPackage } from "../../api/client";
import type { AnalysisRun, RecordingDetail } from "../../api/types";
import { spectrumPathForRun } from "../signals/spectrumNavigation";

interface Props {
  open: boolean;
  recordings: RecordingDetail[];
  onClose: () => void;
}

export function ImportRunModal({ open, recordings, onClose }: Props) {
  const navigate = useNavigate();
  const [form] = Form.useForm<{ recordingId: string }>();
  const recordingId = Form.useWatch("recordingId", form);
  const [file, setFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [imported, setImported] = useState<AnalysisRun | null>(null);

  const canImport = !submitting && !imported && file !== null && Boolean(recordingId);

  const submitImport = async () => {
    const values = await form.validateFields();
    if (!file) return;
    setSubmitting(true);
    setError(null);
    try {
      const run = await importAnalysisPackage(values.recordingId, file);
      setImported(run);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to import analysis package.");
    } finally {
      setSubmitting(false);
    }
  };

  const openResults = () => {
    if (!imported) return;
    const recording = recordings.find((item) => item.id === imported.recordingId);
    if (!recording) return;
    navigate(spectrumPathForRun(recording.id, imported.id));
    resetAndClose();
  };

  const resetAndClose = () => {
    form.resetFields();
    setFile(null);
    setImported(null);
    setError(null);
    onClose();
  };

  return (
    <Modal
      title="Import Existing Run"
      open={open}
      onCancel={() => resetAndClose()}
      footer={imported ? [
        <Button key="close" onClick={() => resetAndClose()}>Close</Button>,
        <Button key="results" type="primary" onClick={openResults}>Open Results</Button>,
      ] : [
        <Button key="cancel" onClick={() => resetAndClose()}>Cancel</Button>,
        <Button key="import" type="primary" loading={submitting} disabled={!canImport} onClick={() => void submitImport()}>
          Import
        </Button>,
      ]}
    >
      {error ? <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} /> : null}
      {imported ? (
        <div>
          <Alert type="success" showIcon message="Analysis package imported successfully" style={{ marginBottom: 16 }} />
          <Typography.Paragraph style={{ marginBottom: 8 }}>
            {imported.pipelineId} · {imported.pipelineVersion}
          </Typography.Paragraph>
          <Typography.Text type="secondary">Run {imported.id}</Typography.Text>
        </div>
      ) : (
        <Form form={form} layout="vertical" onValuesChange={() => setError(null)}>
          <Form.Item name="recordingId" label="Local Recording" rules={[{ required: true, message: "Choose a Recording" }]}>
            <Select
              placeholder="Select a Recording"
              options={recordings.map((item) => ({ value: item.id, label: item.name }))}
            />
          </Form.Item>
          <Form.Item label={<label htmlFor="analysis-package-zip">Analysis Package ZIP</label>}>
            <input id="analysis-package-zip" type="file" accept=".zip,application/zip" onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
          </Form.Item>
          <Typography.Text type="secondary">
            Import a ZIP containing manifest.json and detections.json generated on an AutoDL/GPU server.
          </Typography.Text>
        </Form>
      )}
    </Modal>
  );
}