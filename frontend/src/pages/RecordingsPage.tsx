import { Alert, Button, Card, Empty, Form, Input, InputNumber, Modal, Space, Spin, Tag, Typography } from "antd";
import { useLocalization } from "../localization/useLocalization";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { importRecording, listRecordings, registerSpaceNetDataset } from "../api/client";
import { toErrorText } from "../api/errors";
import type { SpaceNetRegistrationSummary } from "../api/client";
import type { RecordingDetail } from "../api/types";
import { ImportRunModal } from "../features/imports/ImportRunModal";
import { BatchImportModal } from "../features/imports/BatchImportModal";

interface ImportFormValues {
  name: string;
  sampleRateHz: number;
  centerFrequencyHz: number;
  labelSpace?: string;
}

const PAGE_SIZE = 50;

export function RecordingsPage() {
  const navigate = useNavigate();
  const { t } = useLocalization();
  const [recordings, setRecordings] = useState<RecordingDetail[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [importRunOpen, setImportRunOpen] = useState(false);
  const [batchImportOpen, setBatchImportOpen] = useState(false);
  const [registerOpen, setRegisterOpen] = useState(false);
  const [datasetPath, setDatasetPath] = useState("");
  const [registering, setRegistering] = useState(false);
  const [registrationSummary, setRegistrationSummary] = useState<SpaceNetRegistrationSummary | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [form] = Form.useForm<ImportFormValues>();

  const refresh = async () => {
    setLoading(true);
    setError(null);
    try {
      const page = await listRecordings(PAGE_SIZE, 0);
      setRecordings(page.items);
      setTotal(page.total);
    } catch (reason) {
      setError(toErrorText(reason, t("recordings.loadError")));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void refresh(); }, []);

  const loadMore = async () => {
    setLoadingMore(true);
    setError(null);
    try {
      const page = await listRecordings(PAGE_SIZE, recordings.length);
      setRecordings((items) => [...items, ...page.items]);
      setTotal(page.total);
    } catch (reason) {
      setError(toErrorText(reason, t("recordings.loadMoreError")));
    } finally {
      setLoadingMore(false);
    }
  };

  const submitImport = async () => {
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
      setModalOpen(false);
      form.resetFields();
      setFile(null);
      await refresh();
      navigate(`/spectrum/${recording.id}`);
    } catch (reason) {
      setError(toErrorText(reason, t("recordings.importError")));
    } finally {
      setSubmitting(false);
    }
  };

  const submitRegistration = async () => {
    if (!datasetPath.trim()) return;
    setRegistering(true);
    setError(null);
    try {
      const summary = await registerSpaceNetDataset(datasetPath.trim());
      setRegistrationSummary(summary);
      await refresh();
    } catch (reason) {
      setError(toErrorText(reason, t("recordings.registerError")));
    } finally {
      setRegistering(false);
    }
  };

  const closeRegistration = () => {
    setRegisterOpen(false);
    setDatasetPath("");
    setRegistrationSummary(null);
  };

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 16 }}>
        <div>
          <Typography.Title level={2} style={{ marginBottom: 4 }}>{t("recordings.title")}</Typography.Title>
          <Typography.Text type="secondary">
            {total > 0 ? `${total} · ` : ""}{t("recordings.subtitle")}
          </Typography.Text>
        </div>
        <Space>
          <Button onClick={() => setRegisterOpen(true)}>{t("common.registerDataset")}</Button>
          <Button onClick={() => setImportRunOpen(true)}>{t("common.importRun")}</Button>
          <Button onClick={() => setBatchImportOpen(true)}>{t("batchImport.action")}</Button>
          <Button type="primary" onClick={() => setModalOpen(true)}>{t("common.importRecording")}</Button>
        </Space>
      </div>

      {error ? <Alert type="error" showIcon message={error} /> : null}
      {loading ? <Spin tip={t("common.loading")} /> : null}
      {!loading && recordings.length === 0 ? <Empty description={t("recordings.empty")} /> : null}
      {recordings.map((recording) => (
        <Card
          key={recording.id}
          title={recording.name}
          extra={recording.datasetName ? <Tag color="geekblue">{recording.datasetName}</Tag> : <Tag>{t("recordings.customIq")}</Tag>}
          actions={[
            <Button key="open" type="link" onClick={() => navigate(`/spectrum/${recording.id}`)}>{t("spectrum.open")}</Button>,
          ]}
        >
          <Space wrap>
            <Tag>Fs {(recording.sampleRateHz / 1e6).toFixed(3)} MHz</Tag>
            <Tag>Fc {(recording.centerFrequencyHz / 1e9).toFixed(6)} GHz</Tag>
            <Tag>{recording.durationS.toFixed(6)} s</Tag>
            <Tag>{recording.dataFormat}</Tag>
            {recording.hasGroundTruth ? <Tag>{t("common.groundTruth")}</Tag> : null}
          </Space>
        </Card>
      ))}
      {!loading && recordings.length < total ? (
        <div style={{ textAlign: "center" }}>
          <Button onClick={() => void loadMore()} loading={loadingMore}>
            {t("common.loadMore")} ({total - recordings.length})
          </Button>
        </div>
      ) : null}

      <Modal
        title={t("recordings.importModalTitle")}
        open={modalOpen}
        confirmLoading={submitting}
        onOk={() => void submitImport()}
        onCancel={() => setModalOpen(false)}
        okText={t("recordings.importConfirm")}
      >
        <Form form={form} layout="vertical" initialValues={{ labelSpace: "spacenet_14" }}>
          <Form.Item name="name" label={t("recordings.fieldName")} rules={[{ required: true }]}>
            <Input placeholder="tiny-demo" />
          </Form.Item>
          <Form.Item name="sampleRateHz" label={t("recordings.fieldSampleRate")} rules={[{ required: true }]}>
            <InputNumber style={{ width: "100%" }} min={1} />
          </Form.Item>
          <Form.Item name="centerFrequencyHz" label={t("recordings.fieldCenterFrequency")} rules={[{ required: true }]}>
            <InputNumber style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="labelSpace" label={t("form.labelSpace")}>
            <Input placeholder="spacenet_14" />
          </Form.Item>
          <Form.Item label={t("recordings.fieldIqFile")}>
            <input type="file" accept=".bin,.iq,.dat" onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
          </Form.Item>
          <Typography.Text type="secondary">{t("recordings.importHint")}</Typography.Text>
        </Form>
      </Modal>

      <Modal
        title={t("common.registerDataset")}
        open={registerOpen}
        confirmLoading={registering}
        onOk={() => void submitRegistration()}
        onCancel={closeRegistration}
        okText={t("recordings.registerConfirm")}
      >
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          {registrationSummary ? (
            <Alert
              type="success"
              showIcon
              message={t("recordings.registerSuccess")}
              description={t("recordings.registerSummary", {
                created: registrationSummary.created,
                skipped: registrationSummary.skipped,
                invalid: registrationSummary.invalid,
              })}
            />
          ) : null}
          <Input
            placeholder="D:\LGFiles\Wideband Signal Analysis Platform\SpaceNet\test"
            value={datasetPath}
            onChange={(event) => setDatasetPath(event.target.value)}
            aria-label={t("recordings.datasetPath")}
          />
          <Typography.Text type="secondary">
            {t("recordings.registerHint")}
          </Typography.Text>
        </Space>
      </Modal>

      <ImportRunModal open={importRunOpen} recordings={recordings} onClose={() => setImportRunOpen(false)} />

      <BatchImportModal open={batchImportOpen} onClose={() => setBatchImportOpen(false)} />
    </Space>
  );
}