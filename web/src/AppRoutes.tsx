import { Navigate, Route, Routes } from 'react-router-dom'

import AppShell from './layout/AppShell'
import HistoryPage from './pages/History'
import PromptsPage from './pages/Prompts'
import SceneLibraryPage from './pages/SceneLibrary'
import SettingsPage from './pages/Settings'
import WorkspacePage from './pages/Workspace'

export default function AppRoutes() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route path="/" element={<WorkspacePage />} />
        <Route path="/scenes" element={<SceneLibraryPage />} />
        <Route path="/history" element={<HistoryPage />} />
        <Route path="/prompts" element={<PromptsPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
