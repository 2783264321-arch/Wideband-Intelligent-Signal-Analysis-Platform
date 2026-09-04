import { Alert, Button, Card, Col, Row, Space, Spin, Typography } from "antd";
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { getDetection, getFFT, getGroundTruth, getSpectrogram, getWaveform } from "../api/client";
import type { DetectionResult, FFTData, GroundTruthResult, SpectrogramMeta, WaveformData } from "../api/types";
import { LineSeriesChart } from "../features/signal-detail/LineSeriesChart";
import { SignalSummary } from "../features/signals/SignalSummary";
import { SpectrogramViewer } from "../features/spectrum/SpectrogramViewer";

export function SignalDetailPage() {
  const navigate = useNavigate();
  const { detectionId = "" } = useParams();
  const [detection, setDetection] = useState<DetectionResult | null>(null);
  const [spectrogram, setSpectrogram] = useState<SpectrogramMeta | null>(null);
  const [groundTruth, setGroundTruth] = useState<GroundTruthResult[]>([]);
  const [waveform, setWaveform] = useState<WaveformData | null>(null);
  const [fft, setFFT] = useState<FFTData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    getDetection(detectionId)
      .then(async (item) => {
        if (!active) return;
        setDetection(item);
        const [nextSpectrogram, nextWaveform, nextFFT, nextGroundTruth] = await Promise.all([
          getSpectrogram(item.recordingId),
          getWaveform(item.recordingId, item.tStartS, item.tEndS),
          getFFT(item.id),
          getGroundTruth(item.recordingId).catch(() => []),
        ]);
        if (!active) return;
        setSpectrogram(nextSpectrogram);
        setWaveform(nextWaveform);
        setFFT(nextFFT);
        setGroundTruth(nextGroundTruth);
      })
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "Unable to load signal detail."); });
    return () => { active = false; };
  }, [detectionId]);

  if (error) return <Alert type="error" showIcon message="Unable to load signal detail" description={error} />;
  if (!detection || !spectrogram || !waveform || !fft) return <Spin tip="Loading signal detail..." />;

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <Typography.Title level={2} style={{ margin: 0 }}>Signal Detail · {detection.id}</Typography.Title>
        <Button onClick={() => navigate(`/spectrum/${detection.recordingId}?selected=${detection.id}`)}>Show in Spectrum</Button>
      </div>
      <SignalSummary detection={detection} />
      <Row gutter={16}>
        <Col xs={24} lg={12}>
          <Card title="Local Spectrogram Context">
            <SpectrogramViewer meta={spectrogram} detections={[detection]} groundTruth={groundTruth} selectedDetectionId={detection.id} />
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="FFT / Spectrum">
            <LineSeriesChart x={fft.frequencyHz} series={[{ name: "Magnitude (dB)", values: fft.magnitudeDb }]} xFormatter={(value) => `${(value / 1e6).toFixed(3)} MHz`} />
          </Card>
        </Col>
      </Row>
      <Row gutter={16}>
        <Col xs={24} lg={12}>
          <Card title="I/Q Waveform">
            <LineSeriesChart x={waveform.timeS} series={[{ name: "I", values: waveform.i }, { name: "Q", values: waveform.q, dashed: true }]} xFormatter={(value) => `${(value * 1e3).toFixed(3)} ms`} />
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="Processing Inspector">
            <div style={{ minHeight: 220, display: "grid", placeItems: "center", textAlign: "center" }}>
              Intermediate artifacts are optional and will appear here when the selected Pipeline exports them.
            </div>
          </Card>
        </Col>
      </Row>
    </Space>
  );
}
