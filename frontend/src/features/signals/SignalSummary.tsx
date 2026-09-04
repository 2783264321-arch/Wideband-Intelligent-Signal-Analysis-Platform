import { Descriptions } from "antd";
import type { DetectionResult } from "../../api/types";

export function SignalSummary({ detection }: { detection: DetectionResult }) {
  const center = (detection.fLowHz + detection.fHighHz) / 2;
  const bandwidth = detection.fHighHz - detection.fLowHz;
  const duration = detection.tEndS - detection.tStartS;
  return (
    <Descriptions bordered size="small" column={2}>
      <Descriptions.Item label="Signal Type">{detection.className}</Descriptions.Item>
      <Descriptions.Item label="Confidence">{(detection.confidence * 100).toFixed(1)}%</Descriptions.Item>
      <Descriptions.Item label="Center Frequency">{(center / 1e6).toFixed(3)} MHz</Descriptions.Item>
      <Descriptions.Item label="Bandwidth">{(bandwidth / 1e6).toFixed(3)} MHz</Descriptions.Item>
      <Descriptions.Item label="Time">{detection.tStartS.toFixed(4)}–{detection.tEndS.toFixed(4)} s</Descriptions.Item>
      <Descriptions.Item label="Duration">{(duration * 1e3).toFixed(2)} ms</Descriptions.Item>
    </Descriptions>
  );
}
