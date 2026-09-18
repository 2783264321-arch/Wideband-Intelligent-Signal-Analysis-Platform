import { Alert, Form, Input, InputNumber, Modal, Select, Tabs, Typography } from "antd";
import { useState } from "react";
import { importRecording, registerRecordingPath } from "../../api/client";
import { toErrorText } from "../../api/errors";
import { useLocalization } from "../../localization/useLocalization";

type AddMode = "upload" | "path";

interface ImportFormValues {
  name: string;
  sampleRateHz?: number;
  centerFrequencyHz?: number;
  labelSpace?: string;
  path?: string;
  dataFormat?: string;
}

const PATH_FORMATS = [
  { value: "complex64_le", label: "complex64_le" },
  { value: "float16_interleaved_le", label: "float16_interleaved_le" },
];

/**
 * Derive sampling rate and center frequency from a SpaceNet sidecar the same way
 * the backend does (bandwidth = Fs, observation_range midpoint = Fc). Returns
 * null for anything that is not a readable SpaceNet document, in which case the
 * user must type the values.
 */
export function deriveFromSpaceNetJson(
  document: unknown,
): { sampleRateHz: number; centerFrequencyHz: number; labelSpace: string } | null {
  if (document === null || typeof document !== "object") return null;
  const payload = document as { observation_range?: unknown };
  const range = payload.observation_range;
  if (!Array.isArray(range) || range.length !== 2) return null;
  const [low, high] = range;
  if (typeof low !== "number" || typeof high !== "number") return null;
  if (!Number.isFinite(low) || !Number.isFinite(high) || low >= high) return null;
  return {
    sampleRateHz: (high - low) * 1e6,
    centerFrequencyHz: ((low + high) / 2) * 1e6,
    labelSpace: "spacenet_14",
  };
}

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
  const [metadataFile, setMetadataFile] = useState<File | null>(null);
  const [derived, setDerived] = useState<{ sampleRateHz: number; centerFrequencyHz: number } | null>(null);
  const [metadataError, setMetadataError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reset = () => {
    form.resetFields();
    setFile(null);
    setMetadataFile(null);
    setDerived(null);
    setMetadataError(null);
    setError(null);
  };

  const onMetadataSelected = async (selected: File | null) => {
    setMetadataFile(selected);
    setMetadataError(null);
    setDerived(null);
    if (!selected) return;
    try {
      const parsed = deriveFromSpaceNetJson(JSON.parse(await selected.text()));
      if (parsed === null) {
        setMetadataError(t("dataLibrary.uploadMetadataInvalid"));
        return;
      }
      setDerived({ sampleRateHz: parsed.sampleRateHz, centerFrequencyHz: parsed.centerFrequencyHz });
      form.setFieldsValue({
        sampleRateHz: parsed.sampleRateHz,
        centerFrequencyHz: parsed.centerFrequencyHz,
        labelSpace: form.getFieldValue("labelSpace") || parsed.labelSpace,
      });
    } catch {
      setMetadataError(t("dataLibrary.uploadMetadataInvalid"));
    }
  };

  const submit = async () => {
    let values: ImportFormValues;
    try {
      values = await form.validateFields();
    } catch {
      return; // antd renders the per-field validation errors.
    }
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
        // A SpaceNet sidecar pins the encoding (float16); otherwise the generic
        // upload is complex64. Let the server decide so a raw .bin is not misread.
        if (!metadataFile) body.append("data_format", "complex64_le");
        // Only send explicit values; when omitted the backend derives them from
        // the SpaceNet JSON sidecar (and fails closed if neither is available).
        if (values.sampleRateHz != null) body.append("sample_rate_hz", String(values.sampleRateHz));
        if (values.centerFrequencyHz != null) {
          body.append("center_frequency_hz", String(values.centerFrequencyHz));
        }
        if (values.labelSpace?.trim()) body.append("label_space", values.labelSpace.trim());
        if (metadataFile) body.append("metadata", metadataFile);
        const recording = await importRecording(body);
        reset();
        onImported?.(recording.id);
        onClose();
      } else {
        const recording = await registerRecordingPath({
          path: values.path?.trim() ?? "",
          name: values.name,
          dataFormat: values.dataFormat ?? "complex64_le",
          sampleRateHz: values.sampleRateHz ?? 0,
          centerFrequencyHz: values.centerFrequencyHz ?? 0,
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

  const rateRequired = mode === "path" || metadataFile === null;
  const rateHint = mode === "upload" ? t("dataLibrary.uploadMetadataHint") : t("dataLibrary.fsFcRequiredHint");

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
        <Form.Item name="name" label={t("recordings.fieldName")} rules={[{ required: true }]}>
          <Input placeholder="capture_001" />
        </Form.Item>

        {mode === "upload" ? (
          <>
            <Form.Item label={t("recordings.fieldIqFile")}>
              <input
                type="file"
                accept=".bin,.iq,.dat"
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              />
            </Form.Item>
            <Form.Item label={t("dataLibrary.uploadMetadataLabel")} tooltip={t("dataLibrary.uploadMetadataHint")}>
              <input
                type="file"
                accept=".json,application/json"
                aria-label={t("dataLibrary.uploadMetadataLabel")}
                onChange={(event) => void onMetadataSelected(event.target.files?.[0] ?? null)}
              />
            </Form.Item>
            {metadataError ? (
              <Alert type="error" showIcon message={metadataError} style={{ marginBottom: 16 }} />
            ) : null}
            {derived !== null ? (
              <Alert
                type="info"
                showIcon
                data-testid="upload-metadata-derived"
                message={t("dataLibrary.uploadMetadataDetected")}
                description={`Fs ${(derived.sampleRateHz / 1e6).toFixed(3)} MHz · Fc ${(
                  derived.centerFrequencyHz / 1e9
                ).toFixed(6)} GHz`}
                style={{ marginBottom: 16 }}
              />
            ) : null}
          </>
        ) : (
          <>
            <Form.Item name="path" label={t("dataLibrary.localPath")} rules={[{ required: true }]}>
              <Input placeholder="D:\\signals\\capture_001.iq" aria-label={t("dataLibrary.localPath")} />
            </Form.Item>
            <Form.Item name="dataFormat" label={t("recordings.fieldDataFormat")} rules={[{ required: true }]}>
              <Select options={PATH_FORMATS} />
            </Form.Item>
          </>
        )}

        <Form.Item
          name="sampleRateHz"
          label={t("recordings.fieldSampleRate")}
          rules={rateRequired ? [{ required: true }] : []}
        >
          <InputNumber style={{ width: "100%" }} min={1} />
        </Form.Item>
        <Form.Item
          name="centerFrequencyHz"
          label={t("recordings.fieldCenterFrequency")}
          rules={rateRequired ? [{ required: true }] : []}
        >
          <InputNumber style={{ width: "100%" }} />
        </Form.Item>
        <Form.Item name="labelSpace" label={t("form.labelSpace")}>
          <Input placeholder="spacenet_14" />
        </Form.Item>

        <Typography.Text type="secondary">{rateHint}</Typography.Text>
        {error ? <Typography.Paragraph type="danger">{error}</Typography.Paragraph> : null}
      </Form>
    </Modal>
  );
}
