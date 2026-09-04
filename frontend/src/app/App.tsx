import { Navigate, Route, Routes } from "react-router-dom";
import { MainLayout } from "./MainLayout";
import { RecordingsPage } from "../pages/RecordingsPage";
import { SpectrumAnalysisPage } from "../pages/SpectrumAnalysisPage";
import { SignalsPage } from "../pages/SignalsPage";
import { SignalDetailPage } from "../pages/SignalDetailPage";
import { AlgorithmLabPage } from "../pages/AlgorithmLabPage";

function SettingsPage() {
  return <div><h2>Settings</h2><p>V1 keeps runtime configuration intentionally minimal.</p></div>;
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
        <Route path="algorithm-lab" element={<AlgorithmLabPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/recordings" replace />} />
      </Route>
    </Routes>
  );
}
