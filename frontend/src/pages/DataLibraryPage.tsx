import { Button, Dropdown, Input, Modal, Space, Tabs, Typography } from "antd";
import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { registerSpaceNetDataset } from "../api/client";
import { toErrorText } from "../api/errors";
import { PageHeader } from "../app/PageHeader";
import { useLocalization } from "../localization/useLocalization";
import { ImportAnalysisBundleModal } from "../features/analysis-bundle/ImportAnalysisBundleModal";
import { DatasetList } from "../features/data-library/DatasetList";
import { ImportStandaloneIqModal } from "../features/data-library/ImportStandaloneIqModal";
import { StandaloneSampleList } from "../features/data-library/StandaloneSampleList";

/** The data-library surface a result import was launched from. */
type ImportTarget = "dataset" | "sample";

/** The library tab key carried in the ?tab= query parameter. */
export type DataLibraryTab = "datasets" | "standalone";

export function DataLibraryPage() {
  const { t } = useLocalization();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const tab: DataLibraryTab = params.get("tab") === "standalone" ? "standalone" : "datasets";
  const [iqOpen, setIqOpen] = useState(false);
  const [registerOpen, setRegisterOpen] = useState(false);
  const [importTarget, setImportTarget] = useState<ImportTarget | null>(null);
  const [datasetPath, setDatasetPath] = useState("");
  const [registering, setRegistering] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
                { key: "register", label: t("dataLibrary.registerDataset") },
                { key: "iq", label: t("dataLibrary.addStandaloneIq") },
              ],
              onClick: ({ key }) => {
                if (key === "register") setRegisterOpen(true);
                if (key === "iq") setIqOpen(true);
              },
            }}
          >
            <Button data-testid="add-data-button">{t("dataLibrary.addData")}</Button>
          </Dropdown>
        </Space>
      </div>

      {error ? <Typography.Text type="danger">{error}</Typography.Text> : null}

      <Tabs
        activeKey={tab}
        onChange={(key) => setParams({ tab: key }, { replace: true })}
        items={[
          {
            key: "datasets",
            label: t("dataLibrary.tabDatasets"),
            children: (
              <DatasetList onImportResults={() => setImportTarget("dataset")} />
            ),
          },
          {
            key: "standalone",
            label: t("dataLibrary.tabStandalone"),
            children: (
              <StandaloneSampleList onImportResults={() => setImportTarget("sample")} />
            ),
          },
        ]}
      />

      <ImportStandaloneIqModal
        open={iqOpen}
        onClose={() => setIqOpen(false)}
        onImported={() => navigate(0)}
      />
      <ImportAnalysisBundleModal
        open={importTarget !== null}
        source={importTarget ?? "dataset"}
        onClose={() => setImportTarget(null)}
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
