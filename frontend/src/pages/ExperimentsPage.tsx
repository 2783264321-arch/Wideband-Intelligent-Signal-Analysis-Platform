import { Tabs } from "antd";
import { useSearchParams } from "react-router-dom";
import { ExperimentComparePage } from "./ExperimentComparePage";
import { ExperimentList } from "../features/dataset-experiment/ExperimentList";

export function ExperimentsPage() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "compare" ? "compare" : "experiments";

  const patch = (key: string) => {
    const next = new URLSearchParams(params);
    if (key === "experiments") next.delete("tab");
    else next.set("tab", key);
    setParams(next);
  };

  return (
    <Tabs
      activeKey={tab}
      onChange={patch}
      items={[
        { key: "experiments", label: "Experiments", children: <ExperimentList /> },
        { key: "compare", label: "Compare", children: <ExperimentComparePage /> },
      ]}
    />
  );
}
