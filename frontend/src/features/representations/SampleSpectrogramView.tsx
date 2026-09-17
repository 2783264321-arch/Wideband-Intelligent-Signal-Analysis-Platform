import { Alert, Spin } from "antd";
import { useEffect, useState } from "react";
import { getSpectrogram } from "../../api/client";
import { toErrorText } from "../../api/errors";
import { useLocalization } from "../../localization/useLocalization";
import type { SpectrogramMeta } from "../../api/types";
import { SpectrogramViewer } from "../spectrum/SpectrogramViewer";

export interface SampleSpectrogramViewProps {
  recordingId: string;
}

/**
 * Pure IQ -> STFT -> visual representation. No model inference, no overlays,
 * no AnalysisRun and no Ground Truth required.
 */
export function SampleSpectrogramView({ recordingId }: SampleSpectrogramViewProps) {
  const { t } = useLocalization();
  const [meta, setMeta] = useState<SpectrogramMeta | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    getSpectrogram(recordingId)
      .then((value) => {
        if (active) setMeta(value);
      })
      .catch((reason) => {
        if (active) setError(toErrorText(reason, t("representation.spectrogramError")));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [recordingId]);

  if (loading) return <Spin />;
  if (error) return <Alert type="error" showIcon message={t("representation.spectrogramError")} description={error} />;
  if (!meta) return null;

  return (
    <div data-testid="sample-spectrogram">
      <SpectrogramViewer meta={meta} detections={[]} groundTruth={[]} showOverlayLegend={false} />
    </div>
  );
}
