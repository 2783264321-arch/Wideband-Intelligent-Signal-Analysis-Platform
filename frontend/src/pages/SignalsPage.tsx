import { Alert, Button, Spin, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { getDetections } from "../api/client";
import type { DetectionResult } from "../api/types";
import { bandwidthHz, centerFrequencyHz, durationS } from "../features/signals/derived";
import { spectrumPathForRun } from "../features/signals/spectrumNavigation";

export function SignalsPage() {
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
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "Unable to load detections."); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [runId]);

  const filters = useMemo(() => [...new Set(detections.map((d) => d.className))].map((value) => ({ text: value, value })), [detections]);
  const recordingId = detections[0]?.recordingId;
  const columns: ColumnsType<DetectionResult> = [
    { title: "ID", dataIndex: "id" },
    { title: "Signal Type", dataIndex: "className", filters, onFilter: (value, row) => row.className === value },
    { title: "Confidence", dataIndex: "confidence", sorter: (a, b) => a.confidence - b.confidence, render: (value: number) => `${(value * 100).toFixed(1)}%` },
    { title: "Center Freq", render: (_, d) => `${(centerFrequencyHz(d) / 1e6).toFixed(3)} MHz` },
    { title: "Bandwidth", render: (_, d) => `${(bandwidthHz(d) / 1e6).toFixed(3)} MHz` },
    { title: "Time", render: (_, d) => `${d.tStartS.toFixed(6)}–${d.tEndS.toFixed(6)} s` },
    { title: "Duration", render: (_, d) => `${(durationS(d) * 1e3).toFixed(3)} ms` },
    { title: "", render: (_, d) => <Button type="link" onClick={() => navigate(`/signals/${runId}/${d.id}`)}>View Details</Button> },
  ];

  if (error) return <Alert type="error" showIcon message="Unable to load signals" description={error} />;
  if (loading) return <Spin tip="Loading detected signals..." />;

  return (
    <>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <div>
          <Typography.Title level={2} style={{ marginBottom: 0 }}>Signals</Typography.Title>
          <Typography.Text type="secondary">Persisted DetectionResults for {runId}</Typography.Text>
        </div>
        <Button disabled={!recordingId} onClick={() => recordingId && navigate(spectrumPathForRun(recordingId, runId))}>Show in Spectrum</Button>
      </div>
      <Table rowKey="id" dataSource={detections} columns={columns} pagination={false} />
    </>
  );
}
