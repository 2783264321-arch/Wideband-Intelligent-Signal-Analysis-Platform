import { Button, Modal, Space, Tabs } from "antd";
import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { getDatasetProjection } from "../api/client";
import { ExperimentComparePage } from "./ExperimentComparePage";
import { ExperimentList } from "../features/dataset-experiment/ExperimentList";
import { ExperimentCreateForm } from "../features/dataset-experiment/ExperimentCreateForm";
import type { DatasetIdentityInput } from "../features/dataset-experiment/ExperimentCreateForm";
import { DatasetBenchmarksView } from "../features/dataset-benchmarks/DatasetBenchmarksView";
import { useLocalization } from "../localization/useLocalization";

export function ExperimentsPage() {
  const { t } = useLocalization();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [createOpen, setCreateOpen] = useState(false);
  const [initialDataset, setInitialDataset] = useState<DatasetIdentityInput | null>(null);
  const requestedTab = params.get("tab");
  const tab = requestedTab === "compare" ? "compare" : requestedTab === "benchmarks" ? "benchmarks" : "experiments";
  const benchmarkId = params.get("benchmark") ?? undefined;
  const projectionParam = params.get("datasetProjectionId") ?? undefined;

  useEffect(() => {
    if (!projectionParam) return undefined;
    let active = true;
    getDatasetProjection(projectionParam)
      .then((projection) => {
        if (!active) return;
        setInitialDataset({
          datasetProjectionId: projection.datasetProjectionId,
          datasetName: projection.datasetName,
          datasetSplit: projection.datasetSplit,
          datasetLabelSpace: projection.labelSpace ?? "",
        });
        setCreateOpen(true);
      })
      .catch(() => {
        // Projection unavailable; the user can still open the generic create form.
      });
    return () => {
      active = false;
    };
  }, [projectionParam]);

  const patch = (changes: Record<string, string | undefined>) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes)) {
      if (value === undefined) next.delete(key);
      else next.set(key, value);
    }
    setParams(next);
  };

  return (
    <>
      <Tabs
        activeKey={tab}
        onChange={(key) => patch({ tab: key === "experiments" ? undefined : key })}
        items={[
          {
            key: "experiments",
            label: t("nav.experiments"),
            children: (
              <Space direction="vertical" style={{ width: "100%" }}>
                <Button type="primary" onClick={() => { setInitialDataset(null); setCreateOpen(true); }}>{t("common.newExperiment")}</Button>
                <ExperimentList />
              </Space>
            ),
          },
          { key: "compare", label: t("common.compare"), children: <ExperimentComparePage /> },
          {
            key: "benchmarks",
            label: t("benchmarks.tabLabel"),
            children: (
              <DatasetBenchmarksView
                selectedBenchmarkId={benchmarkId}
                onBenchmarkOpen={(id) => patch({ tab: "benchmarks", benchmark: id })}
                onOpenCase={(recordingId, runAId, runBId) =>
                  navigate(`/algorithm-lab?recording=${recordingId}&runA=${runAId}&runB=${runBId}`)}
              />
            ),
          },
        ]}
      />
      <Modal
        title={t("experiment.modalTitle")}
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        footer={null}
        destroyOnClose
      >
        <ExperimentCreateForm initialDataset={initialDataset ?? undefined} onCreated={(id) => { setCreateOpen(false); navigate(`/experiments/${id}`); }} />
      </Modal>
    </>
  );
}
