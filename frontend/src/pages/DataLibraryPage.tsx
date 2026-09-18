import { Button, Dropdown, Input, Modal, Space, Tabs, Typography } from "antd";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { listRecordings, registerSpaceNetDataset } from "../api/client";
import { toErrorText } from "../api/errors";
import { PageHeader } from "../app/PageHeader";
import { useLocalization } from "../localization/useLocalization";
import type { RecordingDetail } from "../api/types";
import { BatchImportModal } from "../features/imports/BatchImportModal";
import { ImportRunModal } from "../features/imports/ImportRunModal";
import { ImportAnalysisBundleModal } from "../features/analysis-bundle/ImportAnalysisBundleModal";
import { DatasetList } from "../features/data-library/DatasetList";
import { ImportStandaloneIqModal } from "../features/data-library/ImportStandaloneIqModal";
import { StandaloneSampleList } from "../features/data-library/StandaloneSampleList";

export function DataLibraryPage() {
  const { t } = useLocalization();
  const navigate = useNavigate();
  const [iqOpen, setIqOpen] = useState(false);
  const [registerOpen, setRegisterOpen] = useState(false);
  const [singleOpen, setSingleOpen] = useState(false);
  const [batchOpen, setBatchOpen] = useState(false);
  const [bundleOpen, setBundleOpen] = useState(false);
  const [recordings, setRecordings] = useState<RecordingDetail[]>([]);
  const [datasetPath, setDatasetPath] = useState("");
  const [registering, setRegistering] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const openSingleImport = async () => {
    try {
      const page = await listRecordings(200, 0);
      setRecordings(page.items);
    } catch {
      setRecordings([]);
    }
    setSingleOpen(true);
  };

  const submitRegistration = async () => {
    if (!datasetPath.trim()) return;
    setRegistering(true);
    setError(null);
    try {
      await registerSpaceNetDataset(datasetPath.trim());
      setRegisterOpen(false);
      setDatasetPath("");
      navigate(0);
    } catch (reason) {
      setError(toErrorText(reason, t("recordings.registerError")));
    } finally {
      setRegistering(false);
    }
  };

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16 }}>
        <PageHeader titleKey="dataLibrary.title" subtitleKey="dataLibrary.subtitle" />
        <Space>
          <Dropdown
            menu={{
              items: [
                { key: "iq", label: t("dataLibrary.addStandaloneIq") },
                { key: "register", label: t("dataLibrary.registerDataset") },
              ],
              onClick: ({ key }) => {
                if (key === "iq") setIqOpen(true);
                if (key === "register") setRegisterOpen(true);
              },
            }}
          >
            <Button data-testid="add-data-button">{t("dataLibrary.addData")}</Button>
          </Dropdown>
          <Dropdown
            menu={{
              items: [
                { key: "single", label: t("dataLibrary.importSingleResult") },
                { key: "batch", label: t("dataLibrary.importBatchResult") },
                { key: "bundle", label: t("analysisBundle.importEntry") },
              ],
              onClick: ({ key }) => {
                if (key === "single") void openSingleImport();
                if (key === "batch") setBatchOpen(true);
                if (key === "bundle") setBundleOpen(true);
              },
            }}
          >
            <Button type="primary" data-testid="import-results-button">
              {t("dataLibrary.importResults")}
            </Button>
          </Dropdown>
        </Space>
      </div>

      {error ? <Typography.Text type="danger">{error}</Typography.Text> : null}

      <Tabs
        items={[
          { key: "datasets", label: t("dataLibrary.tabDatasets"), children: <DatasetList onImportResults={() => setBatchOpen(true)} /> },
          {
            key: "standalone",
            label: t("dataLibrary.tabStandalone"),
            children: <StandaloneSampleList />,
          },
        ]}
      />

      <ImportStandaloneIqModal
        open={iqOpen}
        onClose={() => setIqOpen(false)}
        onImported={() => navigate(0)}
      />
      <ImportRunModal open={singleOpen} recordings={recordings} onClose={() => setSingleOpen(false)} />
      <BatchImportModal open={batchOpen} onClose={() => setBatchOpen(false)} />
      <ImportAnalysisBundleModal
        open={bundleOpen}
        onClose={() => setBundleOpen(false)}
        onImported={() => navigate(0)}
      />

      <Modal
        title={t("dataLibrary.registerDataset")}
        open={registerOpen}
        confirmLoading={registering}
        onOk={() => void submitRegistration()}
        onCancel={() => {
          setRegisterOpen(false);
          setError(null);
        }}
        okText={t("recordings.registerConfirm")}
      >
        <Input
          value={datasetPath}
          onChange={(event) => setDatasetPath(event.target.value)}
          placeholder={t("recordings.datasetPath")}
          aria-label={t("recordings.datasetPath")}
        />
        <Typography.Text type="secondary">{t("recordings.registerHint")}</Typography.Text>
      </Modal>
    </Space>
  );
}
