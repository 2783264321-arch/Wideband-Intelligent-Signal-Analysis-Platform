import { Alert, Button, Card, Empty, Form, Input, InputNumber, Modal, Space, Tag, Typography } from "antd";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { importRecording, listRecordings } from "../api/client";
import type { RecordingDetail } from "../api/types";
import { ImportRunModal } from "../features/imports/ImportRunModal";

interface ImportFormValues {
  name: string;
  sampleRateHz: number;
  centerFrequencyHz: number;
  labelSpace?: string;
}

export function RecordingsPage() {
  const navigate = useNavigate();
  const [recordings, setRecordings] = useState<RecordingDetail[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [importRunOpen, setImportRunOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [form] = Form.useForm<ImportFormValues>();

  const refresh = async () => {
    setLoading(true);
    setError(null);
    try {
      setRecordings(await listRecordings());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to load recordings.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void refresh(); }, []);

  const submitImport = async () => {
    const values = await form.validateFields();
    if (!file) {
      setError("Choose a complex64 IQ file before importing.");
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
      setModalOpen(false);
      form.resetFields();
      setFile(null);
      await refresh();
      navigate(`/spectrum/${recording.id}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to import recording.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 16 }}>
        <div>
          <Typography.Title level={2} style={{ marginBottom: 4 }}>Recording Library</Typography.Title>
          <Typography.Text type="secondary">Open an offline IQ recording or import your own data.</Typography.Text>
        </div>
        <Space>
          <Button onClick={() => setImportRunOpen(true)}>Import Existing Run</Button>
          <Button type="primary" onClick={() => setModalOpen(true)}>Import Recording</Button>
        </Space>
      </div>

      {error ? <Alert type="error" showIcon message={error} /> : null}
      {!loading && recordings.length === 0 ? <Empty description="No recordings imported yet" /> : null}
      {recordings.map((recording) => (
        <Card
          key={recording.id}
          title={recording.name}
          extra={recording.datasetName ? <Tag>{recording.datasetName}</Tag> : <Tag>Custom IQ</Tag>}
          actions={[
            <Button key="open" type="link" onClick={() => navigate(`/spectrum/${recording.id}`)}>Open Spectrum</Button>,
          ]}
        >
          <Space wrap>
            <Tag>Fs {(recording.sampleRateHz / 1e6).toFixed(3)} MHz</Tag>
            <Tag>Fc {(recording.centerFrequencyHz / 1e9).toFixed(6)} GHz</Tag>
            <Tag>{recording.durationS.toFixed(6)} s</Tag>
            <Tag>{recording.dataFormat}</Tag>
            {recording.hasGroundTruth ? <Tag>Ground Truth</Tag> : null}
          </Space>
        </Card>
      ))}

      <Modal
        title="Import complex64 IQ Recording"
        open={modalOpen}
        confirmLoading={submitting}
        onOk={() => void submitImport()}
        onCancel={() => setModalOpen(false)}
        okText="Import"
      >
        <Form form={form} layout="vertical" initialValues={{ labelSpace: "spacenet_14" }}>
          <Form.Item name="name" label="Recording name" rules={[{ required: true }]}>
            <Input placeholder="tiny-demo" />
          </Form.Item>
          <Form.Item name="sampleRateHz" label="Sample rate (Hz)" rules={[{ required: true }]}>
            <InputNumber style={{ width: "100%" }} min={1} />
          </Form.Item>
          <Form.Item name="centerFrequencyHz" label="Center frequency (Hz)" rules={[{ required: true }]}>
            <InputNumber style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="labelSpace" label="Label space">
            <Input placeholder="spacenet_14" />
          </Form.Item>
          <Form.Item label="IQ file (.bin)">
            <input type="file" accept=".bin,.iq,.dat" onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
          </Form.Item>
          <Typography.Text type="secondary">V1 custom import expects little-endian complex64 interleaved I/Q.</Typography.Text>
        </Form>
      </Modal>

      <ImportRunModal open={importRunOpen} recordings={recordings} onClose={() => setImportRunOpen(false)} />
    </Space>
  );
}
