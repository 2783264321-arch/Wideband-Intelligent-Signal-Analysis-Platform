import { Form, Input, InputNumber, Modal, Select, Tabs, Typography } from "antd";
import { useState } from "react";
import { importRecording, registerRecordingPath } from "../../api/client";
import { toErrorText } from "../../api/errors";
import { useLocalization } from "../../localization/useLocalization";

type AddMode = "upload" | "path";

interface ImportFormValues {
  name: string;
  sampleRateHz: number;
  centerFrequencyHz: number;
  labelSpace?: string;
  path?: string;
  dataFormat?: string;
}

const PATH_FORMATS = [
  { value: "complex64_le", label: "complex64_le" },
  { value: "float16_interleaved_le", label: "float16_interleaved_le" },
];

export interface ImportStandaloneIqModalProps {
  open: boolean;
  onClose: () => void;
  onImported?: (recordingId: string) => void;
}

export function ImportStandaloneIqModal({ open, onClose, onImported }: ImportStandaloneIqModalProps) {
  const { t } = useLocalization();
  const [form] = Form.useForm<ImportFormValues>();
  const [mode, setMode] = useState<AddMode>("upload");
  const [file, setFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reset = () => {
    form.resetFields();
    setFile(null);
    setError(null);
  };

  const submit = async () => {
    const values = await form.validateFields();
    setSubmitting(true);
    setError(null);
    try {
      if (mode === "upload") {
        if (!file) {
          setError(t("recordings.chooseFileError"));
          return;
        }
        const body = new FormData();
        body.append("file", file);
        body.append("name", values.name);
        body.append("sample_rate_hz", String(values.sampleRateHz));
        body.append("center_frequency_hz", String(values.centerFrequencyHz));
        body.append("data_format", "complex64_le");
        if (values.labelSpace?.trim()) body.append("label_space", values.labelSpace.trim());
        const recording = await importRecording(body);
        reset();
        onImported?.(recording.id);
        onClose();
      } else {
        const recording = await registerRecordingPath({
          path: values.path?.trim() ?? "",
          name: values.name,
          dataFormat: values.dataFormat ?? "complex64_le",
          sampleRateHz: values.sampleRateHz,
          centerFrequencyHz: values.centerFrequencyHz,
          labelSpace: values.labelSpace?.trim() ? values.labelSpace.trim() : null,
        });
        reset();
        onImported?.(recording.id);
        onClose();
      }
    } catch (reason) {
      setError(
        toErrorText(reason, mode === "upload" ? t("recordings.importError") : t("recordings.registerSampleError")),
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      open={open}
      title={t("dataLibrary.addStandaloneIq")}
      confirmLoading={submitting}
      onOk={() => void submit()}
      onCancel={() => {
        reset();
        onClose();
      }}
      okText={mode === "upload" ? t("recordings.importConfirm") : t("recordings.registerConfirm")}
    >
      <Tabs
        activeKey={mode}
        onChange={(key) => setMode(key as AddMode)}
        items={[
          { key: "upload", label: t("dataLibrary.uploadFile") },
          { key: "path", label: t("dataLibrary.registerLocalPath") },
        ]}
      />
      <Form form={form} layout="vertical" initialValues={{ dataFormat: "complex64_le" }}>
        {mode === "path" ? (
          <Form.Item name="path" label={t("dataLibrary.localPath")} rules={[{ required: true }]}>
            <Input placeholder="D:\\signals\\capture_001.iq" aria-label={t("dataLibrary.localPath")} />
          </Form.Item>
        ) : null}
        <Form.Item name="name" label={t("recordings.fieldName")} rules={[{ required: true }]}>
          <Input placeholder="capture_001" />
        </Form.Item>
        {mode === "path" ? (
          <Form.Item name="dataFormat" label={t("recordings.fieldDataFormat")} rules={[{ required: true }]}>
            <Select options={PATH_FORMATS} />
          </Form.Item>
        ) : null}
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
        {mode === "upload" ? (
          <Form.Item label={t("recordings.fieldIqFile")}>
            <input
              type="file"
              accept=".bin,.iq,.dat"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            />
          </Form.Item>
        ) : null}
        <Typography.Text type="secondary">{t("dataLibrary.fsFcRequiredHint")}</Typography.Text>
        {error ? <Typography.Paragraph type="danger">{error}</Typography.Paragraph> : null}
      </Form>
    </Modal>
  );
}
