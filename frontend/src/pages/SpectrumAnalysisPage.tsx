import { Button, Card, Col, Row, Select, Space, Typography } from "antd";
import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { demoDetections, demoRecording, demoSpectrogram } from "../mocks/demo";
import { SpectrogramViewer } from "../features/spectrum/SpectrogramViewer";
import { SignalResultsPanel } from "../features/signals/SignalResultsPanel";

export function SpectrumAnalysisPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const initial = searchParams.get("selected") ?? undefined;
  const [selectedId, setSelectedId] = useState<string | undefined>(initial);
  const selected = useMemo(() => demoDetections.find((d) => d.id === selectedId), [selectedId]);

  const selectDetection = (id: string) => {
    setSelectedId(id);
    setSearchParams({ selected: id });
  };

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 16, alignItems: "center" }}>
        <div>
          <Typography.Title level={3} style={{ margin: 0 }}>{demoRecording.name}</Typography.Title>
          <Typography.Text type="secondary">Wideband spectrum workspace</Typography.Text>
        </div>
        <Space>
          <Select defaultValue="stft" style={{ width: 130 }} options={[{ value: "stft", label: "STFT" }, { value: "ls-stft", label: "LS-STFT" }]} />
          <Select defaultValue="dummy" style={{ width: 180 }} options={[{ value: "dummy", label: "Dummy Pipeline" }, { value: "zoomspec", label: "ZoomSpec (GPU)" }]} />
          <Button type="primary">Run Analysis</Button>
        </Space>
      </div>
      <Row gutter={16} align="stretch">
        <Col xs={24} xl={18}>
          <Card>
            <SpectrogramViewer meta={demoSpectrogram} detections={demoDetections} selectedDetectionId={selectedId} onSelectDetection={selectDetection} />
            {selected ? <Typography.Text style={{ display: "block", marginTop: 12 }}>Selected: {selected.className}</Typography.Text> : null}
          </Card>
        </Col>
        <Col xs={24} xl={6}>
          <Card style={{ height: "100%" }}>
            <SignalResultsPanel
              detections={demoDetections}
              selectedId={selectedId}
              onSelect={selectDetection}
              onViewDetails={(id) => navigate(`/signals/mock-run/${id}`)}
              onViewAll={() => navigate("/signals/mock-run")}
            />
          </Card>
        </Col>
      </Row>
    </Space>
  );
}
