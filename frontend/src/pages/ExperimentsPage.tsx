import { Tabs } from "antd";
import { useSearchParams } from "react-router-dom";
import { ExperimentComparePage } from "./ExperimentComparePage";

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
        { key: "experiments", label: "Experiments", children: <div data-testid="experiments-tab">Experiments</div> },
        { key: "compare", label: "Compare", children: <ExperimentComparePage /> },
      ]}
    />
  );
}
