import { DownloadOutlined } from "@ant-design/icons";
import { Alert, Button, Space, Typography } from "antd";
import { useState } from "react";
import { exportAnalysisBundle } from "../../api/analysisBundles";
import { toErrorText } from "../../api/errors";
import { useLocalization } from "../../localization/useLocalization";

const EXPORTABLE_STATUSES = new Set(["completed", "completed_with_failures"]);

export interface ExportAnalysisBundleButtonProps {
  experimentId: string;
  status: string;
}

/**
 * "Export Results" action for a completed Dataset Analysis.
 *
 * Renders only for exportable terminal statuses (completed /
 * completed_with_failures). A partial analysis is still exportable, but the
 * partial nature is called out so users understand the result set is partial.
 * No internal bundle/transport vocabulary is exposed.
 */
export function ExportAnalysisBundleButton({
  experimentId,
  status,
}: ExportAnalysisBundleButtonProps) {
  const { t } = useLocalization();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [exported, setExported] = useState<string | null>(null);

  if (!EXPORTABLE_STATUSES.has(status)) {
    return null;
  }
  const partial = status === "completed_with_failures";

  const onExport = async () => {
    setBusy(true);
    setError(null);
    try {
      setExported(await exportAnalysisBundle(experimentId));
    } catch (reason) {
      setError(toErrorText(reason, t("analysisBundle.exportFailed")));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Space direction="vertical" size={4}>
      <Space size={8} wrap align="center">
        <Button
          icon={<DownloadOutlined />}
          loading={busy}
          onClick={() => void onExport()}
          data-testid="export-analysis-bundle-button"
        >
          {t("analysisBundle.exportResults")}
        </Button>
        {partial ? (
          <Typography.Text type="warning" data-testid="analysis-bundle-partial-note">
            {t("analysisBundle.partialNote")}
          </Typography.Text>
        ) : null}
      </Space>
      {exported !== null ? (
        <Typography.Text type="secondary" data-testid="analysis-bundle-exported">
          {t("analysisBundle.exported")}: {exported}
        </Typography.Text>
      ) : null}
      {error !== null ? <Alert type="error" showIcon message={error} /> : null}
    </Space>
  );
}
