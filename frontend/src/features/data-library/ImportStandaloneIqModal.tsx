import { Form, Input, InputNumber, Modal, Typography } from "antd";
import { useState } from "react";
import { importRecording } from "../../api/client";
import { toErrorText } from "../../api/errors";
import { useLocalization } from "../../localization/useLocalization";

interface ImportFormValues {
  name: string;
  sampleRateHz: number;
  centerFrequencyHz: number;
  labelSpace?: string;
}

export interface ImportStandaloneIqModalProps {
  open: boolean;
  onClose: () => void;
  onImported?: (recordingId: string) => void;
}

export function ImportStandaloneIqModal({ open, onClose, onImported }: ImportStandaloneIqModalProps) {
  const { t } = useLocalization();
  const [form] = Form.useForm<ImportFormValues>();
  const [file, setFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    const values = await form.validateFields();
    if (!file) {
      setError(t("recordings.chooseFileError"));
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const body = new FormData();
      body.append("file", file);
      body.append("name", values.name);
      body.append("sample_rate_hz", String(values.sampleRateHz));
      body.append("center_frequency_hz", String(values.centerFrequencyHz));
      body.append("data_format", "complex64_le");
      if (values.labelSpace?.trim()) body.append("label_space", values.labelSpace.trim());
      const recording = await importRecording(body);
      form.resetFields();
      setFile(null);
      onImported?.(recording.id);
      onClose();
    } catch (reason) {
      setError(toErrorText(reason, t("recordings.importError")));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      open={open}
      title={t("dataLibrary.importStandaloneIq")}
      confirmLoading={submitting}
      onOk={() => void submit()}
      onCancel={onClose}
      okText={t("recordings.importConfirm")}
    >
      <Form form={form} layout="vertical">
        <Form.Item name="name" label={t("recordings.fieldName")} rules={[{ required: true }]}>
          <Input placeholder="tiny-demo" />
        </Form.Item>
        <Form.Item name="sampleRateHz" label={t("recordings.fieldSampleRate")} rules={[{ required: true }]}>
          <InputNumber style={{ width: "100%" }} min={1} />
        </Form.Item>
        <Form.Item
          name="centerFrequencyHz"
          label={t("recordings.fieldCenterFrequency")}
          rules={[{ required: true }]}
        >
          <InputNumber style={{ width: "100%" }} />
        </Form.Item>
        <Form.Item name="labelSpace" label={t("form.labelSpace")}>
          <Input placeholder="spacenet_14" />
        </Form.Item>
        <Form.Item label={t("recordings.fieldIqFile")}>
          <input
            type="file"
            accept=".bin,.iq,.dat"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
          />
        </Form.Item>
        <Typography.Text type="secondary">{t("dataLibrary.fsFcRequiredHint")}</Typography.Text>
        {error ? <Typography.Paragraph type="danger">{error}</Typography.Paragraph> : null}
      </Form>
    </Modal>
  );
}
