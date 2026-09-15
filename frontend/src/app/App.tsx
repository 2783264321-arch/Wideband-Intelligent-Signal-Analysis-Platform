import { Navigate, Route, Routes } from "react-router-dom";
import { MainLayout } from "./MainLayout";
import { RecordingsPage } from "../pages/RecordingsPage";
import { SpectrumAnalysisPage } from "../pages/SpectrumAnalysisPage";
import { SignalsPage } from "../pages/SignalsPage";
import { SignalDetailPage } from "../pages/SignalDetailPage";
import { AlgorithmLabPage } from "../pages/AlgorithmLabPage";
import { ExperimentsPage } from "../pages/ExperimentsPage";
import { ExperimentDetailPage } from "../pages/ExperimentDetailPage";
import { useLocalization } from "../localization/useLocalization";

function SettingsPage() {
  const { t } = useLocalization();
  return <div><h2>{t("settings.title")}</h2><p>{t("settings.subtitle")}</p></div>;
}

export function App() {
  return (
    <Routes>
      <Route element={<MainLayout />}>
        <Route index element={<Navigate to="/recordings" replace />} />
        <Route path="recordings" element={<RecordingsPage />} />
        <Route path="spectrum/:recordingId" element={<SpectrumAnalysisPage />} />
        <Route path="signals/:runId" element={<SignalsPage />} />
        <Route path="signals/:runId/:detectionId" element={<SignalDetailPage />} />
        <Route path="experiments" element={<ExperimentsPage />} />
        <Route path="experiments/:experimentId" element={<ExperimentDetailPage />} />
        <Route path="algorithm-lab" element={<AlgorithmLabPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/recordings" replace />} />
      </Route>
    </Routes>
  );
}
