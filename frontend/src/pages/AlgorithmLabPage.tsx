import { Tabs } from "antd";
import { useEffect } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { CaseAnalysisView } from "../features/algorithm-lab/CaseAnalysisView";

/**
 * Algorithm Lab is the per-recording deep A/B comparison workspace.
 * Dataset-level benchmarks were re-homed under the Experiments destination; the
 * legacy `/algorithm-lab?tab=benchmarks&benchmark=<id>` links are redirected to
 * `/experiments?tab=benchmarks&benchmark=<id>` for compatibility.
 */
export function AlgorithmLabPage() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const recordingId = params.get("recording") ?? undefined;
  const runAId = params.get("runA") ?? undefined;
  const runBId = params.get("runB") ?? undefined;

  const tab = params.get("tab");
  useEffect(() => {
    if (tab !== "benchmarks") return;
    const next = new URLSearchParams({ tab: "benchmarks" });
    const benchmarkId = params.get("benchmark");
    if (benchmarkId) next.set("benchmark", benchmarkId);
    navigate(`/experiments?${next.toString()}`, { replace: true });
  }, [tab, params, navigate]);

  const patch = (changes: Record<string, string | undefined>) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes)) {
      if (value === undefined) next.delete(key);
      else next.set(key, value);
    }
    setParams(next);
  };

  return (
    <Tabs
      activeKey="case"
      items={[
        {
          key: "case",
          label: "Case Analysis",
          children: (
            <CaseAnalysisView
              recordingId={recordingId}
              runAId={runAId}
              runBId={runBId}
              onRecordingChange={(id) => patch({ recording: id, runA: undefined, runB: undefined })}
              onRunAChange={(id) => patch({ runA: id })}
              onRunBChange={(id) => patch({ runB: id })}
            />
          ),
        },
      ]}
    />
  );
}
