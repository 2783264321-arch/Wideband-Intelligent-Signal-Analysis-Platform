import { Button, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useNavigate } from "react-router-dom";
import type { DetectionResult } from "../api/types";
import { demoDetections, demoRecording } from "../mocks/demo";

export function SignalsPage() {
  const navigate = useNavigate();
  const columns: ColumnsType<DetectionResult> = [
    { title: "ID", dataIndex: "id" },
    { title: "Signal Type", dataIndex: "className", filters: [...new Set(demoDetections.map((d) => d.className))].map((value) => ({ text: value, value })), onFilter: (value, row) => row.className === value },
    { title: "Confidence", dataIndex: "confidence", sorter: (a, b) => a.confidence - b.confidence, render: (value: number) => `${(value * 100).toFixed(1)}%` },
    { title: "Center Freq", render: (_, d) => `${((d.fLowHz + d.fHighHz) / 2 / 1e6).toFixed(3)} MHz` },
    { title: "Bandwidth", render: (_, d) => `${((d.fHighHz - d.fLowHz) / 1e6).toFixed(3)} MHz` },
    { title: "Time", render: (_, d) => `${d.tStartS.toFixed(4)}–${d.tEndS.toFixed(4)} s` },
    { title: "Duration", render: (_, d) => `${((d.tEndS - d.tStartS) * 1e3).toFixed(2)} ms` },
    { title: "", render: (_, d) => <Button type="link" onClick={() => navigate(`/signals/mock-run/${d.id}`)}>View Details</Button> },
  ];

  return (
    <>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <div>
          <Typography.Title level={2} style={{ marginBottom: 0 }}>Signals</Typography.Title>
          <Typography.Text type="secondary">Detection results for {demoRecording.name}</Typography.Text>
        </div>
        <Button onClick={() => navigate(`/spectrum/${demoRecording.id}`)}>Show in Spectrum</Button>
      </div>
      <Table rowKey="id" dataSource={demoDetections} columns={columns} pagination={false} />
    </>
  );
}
