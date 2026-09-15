import { Alert, Button, Spin, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { getDetections } from "../api/client";
import { toErrorText } from "../api/errors";
import type { DetectionResult } from "../api/types";
import { bandwidthHz, centerFrequencyHz, durationS } from "../features/signals/derived";
import { spectrumPathForRun } from "../features/signals/spectrumNavigation";
import { useLocalization } from "../localization/useLocalization";

export function SignalsPage() {
  const { t } = useLocalization();
  const navigate = useNavigate();
  const { runId = "" } = useParams();
  const [detections, setDetections] = useState<DetectionResult[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    setLoading(true);
    getDetections(runId)
      .then((items) => { if (active) setDetections(items); })
      .catch((reason) => { if (active) setError(toErrorText(reason, t("signals.loadErrorDetail"))); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [runId]);

  const filters = useMemo(() => [...new Set(detections.map((d) => d.className))].map((value) => ({ text: value, value })), [detections]);
  const recordingId = detections[0]?.recordingId;
  const columns: ColumnsType<DetectionResult> = [
    { title: "ID", dataIndex: "id" },
    { title: t("signals.columnType"), dataIndex: "className", filters, onFilter: (value, row) => row.className === value },
    { title: t("signals.columnConfidence"), dataIndex: "confidence", sorter: (a, b) => a.confidence - b.confidence, render: (value: number) => `${(value * 100).toFixed(1)}%` },
    { title: t("signals.columnCenterFrequency"), render: (_, d) => `${(centerFrequencyHz(d) / 1e6).toFixed(3)} MHz` },
    { title: t("signals.columnBandwidth"), render: (_, d) => `${(bandwidthHz(d) / 1e6).toFixed(3)} MHz` },
    { title: t("signals.columnTime"), render: (_, d) => `${d.tStartS.toFixed(6)}–${d.tEndS.toFixed(6)} s` },
    { title: t("signals.columnDuration"), render: (_, d) => `${(durationS(d) * 1e3).toFixed(3)} ms` },
    { title: "", render: (_, d) => <Button type="link" onClick={() => navigate(`/signals/${runId}/${d.id}`)}>{t("common.viewDetails")}</Button> },
  ];

  if (error) return <Alert type="error" showIcon message={t("signals.loadError")} description={error} />;
  if (loading) return <Spin tip={t("signals.loading")} />;

  return (
    <>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <div>
          <Typography.Title level={2} style={{ marginBottom: 0 }}>{t("signals.title")}</Typography.Title>
          <Typography.Text type="secondary">{t("signals.subtitle", { id: runId })}</Typography.Text>
        </div>
        <Button disabled={!recordingId} onClick={() => recordingId && navigate(spectrumPathForRun(recordingId, runId))}>{t("signals.showInSpectrum")}</Button>
      </div>
      <Table rowKey="id" dataSource={detections} columns={columns} pagination={false} />
    </>
  );
}
