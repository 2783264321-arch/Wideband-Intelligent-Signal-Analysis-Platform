import { Descriptions } from "antd";
import type { DetectionResult } from "../../api/types";
import { bandwidthHz, centerFrequencyHz, durationS } from "./derived";

export function SignalSummary({ detection }: { detection: DetectionResult }) {
  return (
    <Descriptions bordered size="small" column={2}>
      <Descriptions.Item label="Signal Type">{detection.className}</Descriptions.Item>
      <Descriptions.Item label="Confidence">{(detection.confidence * 100).toFixed(1)}%</Descriptions.Item>
      <Descriptions.Item label="Center Frequency">{(centerFrequencyHz(detection) / 1e6).toFixed(3)} MHz</Descriptions.Item>
      <Descriptions.Item label="Bandwidth">{(bandwidthHz(detection) / 1e6).toFixed(3)} MHz</Descriptions.Item>
      <Descriptions.Item label="Time">{detection.tStartS.toFixed(6)}–{detection.tEndS.toFixed(6)} s</Descriptions.Item>
      <Descriptions.Item label="Duration">{(durationS(detection) * 1e3).toFixed(3)} ms</Descriptions.Item>
    </Descriptions>
  );
}
