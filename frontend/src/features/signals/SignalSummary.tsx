import { Descriptions } from "antd";
import type { DetectionResult } from "../../api/types";
import { useLocalization } from "../../localization/useLocalization";
import { bandwidthHz, centerFrequencyHz, durationS } from "./derived";

export function SignalSummary({ detection }: { detection: DetectionResult }) {
  const { t } = useLocalization();
  return (
    <Descriptions bordered size="small" column={2}>
      <Descriptions.Item label={t("signals.columnType")}>{detection.className}</Descriptions.Item>
      <Descriptions.Item label={t("signals.columnConfidence")}>{(detection.confidence * 100).toFixed(1)}%</Descriptions.Item>
      <Descriptions.Item label={t("signals.columnCenterFrequency")}>{(centerFrequencyHz(detection) / 1e6).toFixed(3)} MHz</Descriptions.Item>
      <Descriptions.Item label={t("signals.columnBandwidth")}>{(bandwidthHz(detection) / 1e6).toFixed(3)} MHz</Descriptions.Item>
      <Descriptions.Item label={t("signals.columnTime")}>{detection.tStartS.toFixed(6)}–{detection.tEndS.toFixed(6)} s</Descriptions.Item>
      <Descriptions.Item label={t("signals.columnDuration")}>{(durationS(detection) * 1e3).toFixed(3)} ms</Descriptions.Item>
    </Descriptions>
  );
}
