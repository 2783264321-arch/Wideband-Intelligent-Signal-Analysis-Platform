import { Button, Space, Tabs, Typography } from "antd";
import { useLocalization } from "../localization/useLocalization";
import { useEffect } from "react";
import { useNavigate, useSearchParams, useLocation } from "react-router-dom";
import { CaseAnalysisView } from "../features/algorithm-lab/CaseAnalysisView";
import { rememberAlgorithmLabRoute } from "../app/workspaceMemory";

/**
 * Algorithm Lab is the per-recording deep A/B comparison workspace.
 * Dataset-level benchmarks were re-homed under the Experiments destination; the
 * legacy `/algorithm-lab?tab=benchmarks&benchmark=<id>` links are redirected to
 * `/experiments?tab=benchmarks&benchmark=<id>` for compatibility.
 *
 * The workspace always offers a way back to the sample it came from, so a
 * comparison deep-link is never a dead end.
 */
export function AlgorithmLabPage() {
  const { t } = useLocalization();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const location = useLocation();
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

  useEffect(() => {
    rememberAlgorithmLabRoute(location.search);
  }, [location.search]);

  const patch = (changes: Record<string, string | undefined>) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes)) {
      if (value === undefined) next.delete(key);
      else next.set(key, value);
    }
    setParams(next);
  };

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      {recordingId ? (
        <Space wrap>
          <Button data-testid="algorithm-lab-back" onClick={() => navigate(`/samples/${recordingId}`)}>
            {t("common.backTo")}
          </Button>
          <Typography.Text type="secondary">{t("algorithmLab.comparingFor")}</Typography.Text>
        </Space>
      ) : null}
      <Tabs
        activeKey="case"
        items={[
          {
            key: "case",
            label: t("algorithmLab.caseAnalysisTab"),
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
    </Space>
  );
}
