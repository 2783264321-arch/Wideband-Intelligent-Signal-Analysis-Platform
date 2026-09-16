import { Navigate, Route, Routes } from "react-router-dom";
import { MainLayout } from "./MainLayout";
import { DataLibraryPage } from "../pages/DataLibraryPage";
import { DatasetDetailPage } from "../pages/DatasetDetailPage";
import { StandaloneSampleDetailPage } from "../pages/StandaloneSampleDetailPage";
import { SpectrumAnalysisPage } from "../pages/SpectrumAnalysisPage";
import { SignalsPage } from "../pages/SignalsPage";
import { SignalDetailPage } from "../pages/SignalDetailPage";
import { AlgorithmLabPage } from "../pages/AlgorithmLabPage";
import { ExperimentsPage } from "../pages/ExperimentsPage";
import { ExperimentDetailPage } from "../pages/ExperimentDetailPage";
import { SettingsPage } from "../pages/SettingsPage";
import { UserGuidePage } from "../pages/UserGuidePage";

export function App() {
  return (
    <Routes>
      <Route element={<MainLayout />}>
        <Route index element={<Navigate to="/data-library" replace />} />
        <Route path="recordings" element={<Navigate to="/data-library" replace />} />
        <Route path="data-library" element={<DataLibraryPage />} />
        <Route path="data-library/datasets/:datasetProjectionId" element={<DatasetDetailPage />} />
        <Route path="data-library/samples/:recordingId" element={<StandaloneSampleDetailPage />} />
        <Route path="spectrum/:recordingId" element={<SpectrumAnalysisPage />} />
        <Route path="signals/:runId" element={<SignalsPage />} />
        <Route path="signals/:runId/:detectionId" element={<SignalDetailPage />} />
        <Route path="experiments" element={<ExperimentsPage />} />
        <Route path="experiments/:experimentId" element={<ExperimentDetailPage />} />
        <Route path="algorithm-lab" element={<AlgorithmLabPage />} />
        <Route path="guide" element={<UserGuidePage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/data-library" replace />} />
      </Route>
    </Routes>
  );
}
