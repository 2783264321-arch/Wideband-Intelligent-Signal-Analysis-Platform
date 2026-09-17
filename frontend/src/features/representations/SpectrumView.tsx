import { Alert, Space, Spin, Typography, theme } from "antd";
import { useEffect, useMemo, useState } from "react";
import { getSpectrum } from "../../api/client";
import { toErrorText } from "../../api/errors";
import { useLocalization } from "../../localization/useLocalization";
import type { SpectrumData } from "../../api/types";
import { SignalLinePlot, type LinePoint } from "./SignalLinePlot";
import { formatFrequency, pickFrequencyScale } from "./frequency";

export interface SpectrumViewProps {
  recordingId: string;
}

export function SpectrumView({ recordingId }: SpectrumViewProps) {
  const { t } = useLocalization();
  const { token } = theme.useToken();
  const [spectrum, setSpectrum] = useState<SpectrumData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    getSpectrum(recordingId)
      .then((data) => {
        if (active) setSpectrum(data);
      })
      .catch((reason) => {
        if (active) setError(toErrorText(reason, t("representation.spectrumError")));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [recordingId]);

  const scale = useMemo(
    () => pickFrequencyScale(spectrum?.frequencyHz.reduce((max, value) => Math.max(max, Math.abs(value)), 0) ?? 0),
    [spectrum],
  );

  const series = useMemo(() => {
    if (!spectrum) return [];
    const points: LinePoint[] = spectrum.frequencyHz.map((frequency, index) => ({
      x: frequency,
      y: spectrum.powerDb[index] ?? 0,
    }));
    return [{ label: t("representation.powerAxis"), color: token.colorPrimary, points }];
  }, [spectrum, token.colorPrimary, t]);

  if (loading) return <Spin />;
  if (error) return <Alert type="error" showIcon message={t("representation.spectrumError")} description={error} />;
  if (!spectrum || spectrum.frequencyHz.length === 0) {
    return <Typography.Text type="secondary">{t("dataLibrary.empty")}</Typography.Text>;
  }

  return (
    <Space direction="vertical" style={{ width: "100%" }} size="small">
      <Typography.Text type="secondary" data-testid="spectrum-summary">
        {t("representation.fftSummary", { fft: spectrum.fftSize, segments: spectrum.segmentCount })}
      </Typography.Text>
      <div data-testid="spectrum-plot">
        <SignalLinePlot
          series={series}
          height={380}
          xLabel={`${t("representation.frequencyAxis")} (${scale.unit})`}
          yLabel={t("representation.powerAxis")}
          formatX={(value) => formatFrequency(value, scale)}
          formatY={(value) => value.toFixed(1)}
          ariaLabel={t("representation.spectrum")}
        />
      </div>
    </Space>
  );
}
