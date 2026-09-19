import { Button, Modal, Space, Typography } from "antd";
import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { ExperimentComparePage } from "./ExperimentComparePage";
import { ExperimentList } from "../features/dataset-experiment/ExperimentList";
import { ExperimentCreateForm } from "../features/dataset-experiment/ExperimentCreateForm";
import { DatasetBenchmarksView } from "../features/dataset-benchmarks/DatasetBenchmarksView";
import { useLocalization } from "../localization/useLocalization";

/**
 * Cross-dataset overview of Dataset Analyses.
 *
 * This is the GLOBAL view: every Dataset Analysis across every dataset. The
 * richer per-dataset workbench (analyze / evaluate / compare) lives on the
 * Dataset page, so this page no longer duplicates it behind extra tabs.
 *
 * `?tab=compare` and `?tab=benchmarks` are still honored as deep links for
 * backwards compatibility, but neither is advertised in the UI.
 */
export function ExperimentsPage() {
  const { t } = useLocalization();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [createOpen, setCreateOpen] = useState(false);
  const tab = params.get("tab");
  const benchmarkId = params.get("benchmark") ?? undefined;

  if (tab === "compare") {
    return <ExperimentComparePage />;
  }
  if (tab === "benchmarks") {
    return (
      <DatasetBenchmarksView
        selectedBenchmarkId={benchmarkId}
        onBenchmarkOpen={() => undefined}
        onOpenCase={(recordingId, runAId, runBId) =>
          navigate(`/algorithm-lab?recording=${recordingId}&runA=${runAId}&runB=${runBId}`)
        }
      />
    );
  }

  return (
    <>
      <Space direction="vertical" style={{ width: "100%" }} size="middle">
        <Typography.Title level={3} style={{ margin: 0 }}>
          {t("experiments.allTitle")}
        </Typography.Title>
        <Button type="primary" onClick={() => setCreateOpen(true)}>
          {t("common.newExperiment")}
        </Button>
        <ExperimentList />
      </Space>
      <Modal
        title={t("experiment.modalTitle")}
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        footer={null}
        destroyOnClose
      >
        <ExperimentCreateForm
          onCreated={(id) => {
            setCreateOpen(false);
            navigate(`/experiments/${id}`);
          }}
        />
      </Modal>
    </>
  );
}
