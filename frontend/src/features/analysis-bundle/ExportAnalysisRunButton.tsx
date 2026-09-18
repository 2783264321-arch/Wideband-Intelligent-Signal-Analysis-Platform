import { DownloadOutlined } from "@ant-design/icons";
import { Alert, Button, Space, Typography } from "antd";
import { useState } from "react";
import { exportAnalysisRunBundle } from "../../api/analysisBundles";
import { toErrorText } from "../../api/errors";
import { useLocalization } from "../../localization/useLocalization";

export interface ExportAnalysisRunButtonProps {
  runId: string;
  status: string;
}

/**
 * "Export Results" action for ONE completed single-sample analysis run.
 *
 * Mirror of the dataset-level export: produces a portable Analysis Bundle with
 * no raw IQ and no local paths. Hidden for runs that are not completed.
 */
export function ExportAnalysisRunButton({ runId, status }: { runId: string; status: string }) {
  const { t } = useLocalization();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [exported, setExported] = useState<string | null>(null);

  if (status !== "completed") {
    return null;
  }

  const onExport = async () => {
    setBusy(true);
    setError(null);
    try {
      setExported(await exportAnalysisRunBundle(runId));
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
          data-testid="export-analysis-run-button"
        >
          {t("analysisBundle.exportResults")}
        </Button>
      </Space>
      {exported !== null ? (
        <Typography.Text type="secondary" data-testid="analysis-run-exported">
          {t("analysisBundle.exported")}: {exported}
        </Typography.Text>
      ) : null}
      {error !== null ? <Alert type="error" showIcon message={error} /> : null}
    </Space>
  );
}
