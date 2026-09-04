import { Button, Card, Col, Empty, Row, Space, Typography } from "antd";
import { useNavigate, useParams } from "react-router-dom";
import { SignalSummary } from "../features/signals/SignalSummary";
import { SpectrogramViewer } from "../features/spectrum/SpectrogramViewer";
import { demoDetections, demoRecording, demoSpectrogram } from "../mocks/demo";

export function SignalDetailPage() {
  const navigate = useNavigate();
  const { detectionId } = useParams();
  const detection = demoDetections.find((item) => item.id === detectionId);
  if (!detection) return <Empty description="Signal not found" />;

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <Typography.Title level={2} style={{ margin: 0 }}>Signal Detail · {detection.id}</Typography.Title>
        <Button onClick={() => navigate(`/spectrum/${demoRecording.id}?selected=${detection.id}`)}>Show in Spectrum</Button>
      </div>
      <SignalSummary detection={detection} />
      <Row gutter={16}>
        <Col xs={24} lg={12}><Card title="Local Spectrogram"><SpectrogramViewer meta={demoSpectrogram} detections={[detection]} selectedDetectionId={detection.id} /></Card></Col>
        <Col xs={24} lg={12}><Card title="Spectrum / PSD"><div style={{ minHeight: 240, display: "grid", placeItems: "center" }}>DSP view will load on demand.</div></Card></Col>
      </Row>
      <Row gutter={16}>
        <Col xs={24} lg={12}><Card title="I/Q Waveform"><div style={{ minHeight: 180, display: "grid", placeItems: "center" }}>I/Q waveform placeholder</div></Card></Col>
        <Col xs={24} lg={12}><Card title="FFT"><div style={{ minHeight: 180, display: "grid", placeItems: "center" }}>FFT placeholder</div></Card></Col>
      </Row>
    </Space>
  );
}
