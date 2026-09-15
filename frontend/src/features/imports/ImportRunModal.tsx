import { Alert, Button, Form, Modal, Select, Typography } from "antd";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { importAnalysisPackage } from "../../api/client";
import { toErrorText } from "../../api/errors";
import type { AnalysisRun, RecordingDetail } from "../../api/types";
import { spectrumPathForRun } from "../signals/spectrumNavigation";
import { useLocalization } from "../../localization/useLocalization";

interface Props {
  open: boolean;
  recordings: RecordingDetail[];
  onClose: () => void;
}

export function ImportRunModal({ open, recordings, onClose }: Props) {
  const { t } = useLocalization();
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
      setError(toErrorText(reason, "Unable to import analysis package."));
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
      title={t("common.importRun")}
      open={open}
      onCancel={() => resetAndClose()}
      footer={imported ? [
        <Button key="close" onClick={() => resetAndClose()}>{t("common.close")}</Button>,
        <Button key="results" type="primary" onClick={openResults}>{t("import.openResults")}</Button>,
      ] : [
        <Button key="cancel" onClick={() => resetAndClose()}>{t("common.cancel")}</Button>,
        <Button key="import" type="primary" loading={submitting} disabled={!canImport} onClick={() => void submitImport()}>
          {t("import.action")}
        </Button>,
      ]}
    >
      {error ? <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} /> : null}
      {imported ? (
        <div>
          <Alert type="success" showIcon message={t("import.success")} style={{ marginBottom: 16 }} />
          <Typography.Paragraph style={{ marginBottom: 8 }}>
            {imported.pipelineId} · {imported.pipelineVersion}
          </Typography.Paragraph>
          <Typography.Text type="secondary">{t("analysisRun.label", { id: imported.id })}</Typography.Text>
        </div>
      ) : (
        <Form form={form} layout="vertical" onValuesChange={() => setError(null)}>
          <Form.Item name="recordingId" label={t("recordings.title")} rules={[{ required: true, message: t("import.chooseRecording") }]}>
            <Select
              placeholder={t("import.selectRecording")}
              options={recordings.map((item) => ({ value: item.id, label: item.name }))}
            />
          </Form.Item>
          <Form.Item label={<label htmlFor="analysis-package-zip">{t("import.zipLabel")}</label>}>
            <input id="analysis-package-zip" type="file" accept=".zip,application/zip" onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
          </Form.Item>
          <Typography.Text type="secondary">
            {t("import.zipHint")}
          </Typography.Text>
        </Form>
      )}
    </Modal>
  );
}