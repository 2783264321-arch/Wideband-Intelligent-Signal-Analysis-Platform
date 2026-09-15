import { Button, Modal, Space, Tabs } from "antd";
import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { ExperimentComparePage } from "./ExperimentComparePage";
import { ExperimentList } from "../features/dataset-experiment/ExperimentList";
import { ExperimentCreateForm } from "../features/dataset-experiment/ExperimentCreateForm";

export function ExperimentsPage() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [createOpen, setCreateOpen] = useState(false);
  const tab = params.get("tab") === "compare" ? "compare" : "experiments";

  const patch = (key: string) => {
    const next = new URLSearchParams(params);
    if (key === "experiments") next.delete("tab");
    else next.set("tab", key);
    setParams(next);
  };

  return (
    <>
      <Tabs
        activeKey={tab}
        onChange={patch}
        items={[
          {
            key: "experiments",
            label: "Experiments",
            children: (
              <Space direction="vertical" style={{ width: "100%" }}>
                <Button type="primary" onClick={() => setCreateOpen(true)}>New Experiment</Button>
                <ExperimentList />
              </Space>
            ),
          },
          { key: "compare", label: "Compare", children: <ExperimentComparePage /> },
        ]}
      />
      <Modal
        title="New Dataset Experiment"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        footer={null}
        destroyOnClose
      >
        <ExperimentCreateForm onCreated={(id) => { setCreateOpen(false); navigate(`/experiments/${id}`); }} />
      </Modal>
    </>
  );
}
