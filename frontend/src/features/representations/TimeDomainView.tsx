import { Alert, Segmented, Space, Spin, Typography, theme } from "antd";
import { useEffect, useMemo, useState } from "react";
import { getWaveform } from "../../api/client";
import { toErrorText } from "../../api/errors";
import { useLocalization } from "../../localization/useLocalization";
import type { WaveformData } from "../../api/types";
import { SignalLinePlot, type LinePoint } from "./SignalLinePlot";

export type Mode = "iq" | "magnitude" | "phase";

/** Client-side derivations from returned I/Q (no backend round-trip). */
export function deriveValues(mode: Mode, i: number[], q: number[]): number[] {
  if (mode === "magnitude") return i.map((value, index) => Math.hypot(value, q[index] ?? 0));
  if (mode === "phase") return i.map((value, index) => Math.atan2(q[index] ?? 0, value));
  return i;
}

export interface TimeDomainViewProps {
  recordingId: string;
  durationS: number;
}

export function TimeDomainView({ recordingId, durationS }: TimeDomainViewProps) {
  const { t } = useLocalization();
  const { token } = theme.useToken();
  const [mode, setMode] = useState<Mode>("iq");
  const [waveform, setWaveform] = useState<WaveformData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    getWaveform(recordingId, 0, durationS, 4000)
      .then((data) => {
        if (active) setWaveform(data);
      })
      .catch((reason) => {
        if (active) setError(toErrorText(reason, t("representation.waveformError")));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [recordingId, durationS]);

  const series = useMemo(() => {
    if (!waveform) return [];
    const points = (values: number[]): LinePoint[] =>
      values.map((value, index) => ({ x: waveform.timeS[index] ?? index, y: value }));
    if (mode === "magnitude") {
      return [{ label: t("representation.modeMagnitude"), color: token.colorPrimary, points: points(deriveValues(mode, waveform.i, waveform.q)) }];
    }
    if (mode === "phase") {
      return [{ label: t("representation.modePhase"), color: token.colorPrimary, points: points(deriveValues(mode, waveform.i, waveform.q)) }];
    }
    return [
      { label: "I", color: token.colorPrimary, points: points(waveform.i) },
      { label: "Q", color: token.colorWarning, points: points(waveform.q) },
    ];
  }, [waveform, mode, token.colorPrimary, token.colorWarning, t]);

  if (loading) return <Spin />;
  if (error) return <Alert type="error" showIcon message={t("representation.waveformError")} description={error} />;
  if (!waveform || waveform.timeS.length === 0) {
    return <Typography.Text type="secondary">{t("dataLibrary.empty")}</Typography.Text>;
  }

  const yLabel =
    mode === "iq"
      ? t("representation.amplitude")
      : mode === "magnitude"
        ? t("representation.magnitude")
        : t("representation.phaseRadians");

  return (
    <Space direction="vertical" style={{ width: "100%" }} size="middle">
      <Segmented<Mode>
        data-testid="time-domain-mode"
        value={mode}
        onChange={(value) => setMode(value)}
        options={[
          { label: t("representation.modeIq"), value: "iq" },
          { label: t("representation.modeMagnitude"), value: "magnitude" },
          { label: t("representation.modePhase"), value: "phase" },
        ]}
      />
      <div data-testid="time-domain-plot">
        <SignalLinePlot
          series={series}
          height={380}
          xLabel={t("representation.timeAxis")}
          yLabel={yLabel}
          formatX={(value) => value.toFixed(4)}
          formatY={(value) => value.toFixed(2)}
          ariaLabel={t("representation.timeDomain")}
        />
      </div>
    </Space>
  );
}
