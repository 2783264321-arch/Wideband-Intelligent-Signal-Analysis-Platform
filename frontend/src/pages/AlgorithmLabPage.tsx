import { useState } from "react";
import { CaseAnalysisView } from "../features/algorithm-lab/CaseAnalysisView";

export function AlgorithmLabPage() {
  const [recordingId, setRecordingId] = useState<string | undefined>();
  const [runAId, setRunAId] = useState<string | undefined>();
  const [runBId, setRunBId] = useState<string | undefined>();

  return (
    <CaseAnalysisView
      recordingId={recordingId}
      runAId={runAId}
      runBId={runBId}
      onRecordingChange={(id) => {
        setRecordingId(id);
        setRunAId(undefined);
        setRunBId(undefined);
      }}
      onRunAChange={setRunAId}
      onRunBChange={setRunBId}
    />
  );
}