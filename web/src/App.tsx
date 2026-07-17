import { Link, Route, Routes } from "react-router-dom";
import FactoryStatusBar from "./components/FactoryStatusBar";
import { useFactoryIdentity } from "./context/FactoryIdentityContext";
import ArticlesPage from "./pages/ArticlesPage";
import ArticleDetailPage from "./pages/ArticleDetailPage";
import DashboardPage from "./pages/DashboardPage";
import DeskDetailPage from "./pages/DeskDetailPage";
import DeskShiftPage from "./pages/DeskShiftPage";
import FlowCreatePage from "./pages/FlowCreatePage";
import FlowEditorPage from "./pages/FlowEditorPage";
import FlowBatchComparisonPage from "./pages/FlowBatchComparisonPage";
import FlowPerformancePage from "./pages/FlowPerformancePage";
import FlowsPage from "./pages/FlowsPage";
import PersonasPage from "./pages/PersonasPage";
import PersonaDetailPage from "./pages/PersonaDetailPage";
import ShiftsBoardPage from "./pages/ShiftsBoardPage";
import ShiftRosterReviewPage from "./pages/ShiftRosterReviewPage";
import StartFlowsPage from "./pages/StartFlowsPage";
import PromptsPage from "./pages/PromptsPage";
import QueuePage from "./pages/QueuePage";
import RunDetailPage from "./pages/RunDetailPage";
import SettingsPage from "./pages/SettingsPage";
import StatsPage from "./pages/StatsPage";

export default function App() {
  const { factoryName } = useFactoryIdentity();

  return (
    <div className="app-shell">
      <header className="app-header">
        <h1>{factoryName}</h1>
        <nav>
          <Link to="/">Home</Link>
          <Link to="/shifts">Shifts</Link>
          <Link to="/queue">Active</Link>
          <Link to="/start-flows">Plan a shift</Link>
          <Link to="/flows">Desks</Link>
          <Link to="/personas">Desk staff</Link>
          <Link to="/articles">Artifacts</Link>
          <Link to="/stats">Stats</Link>
          <Link to="/settings">Settings</Link>
        </nav>
      </header>
      <FactoryStatusBar />
      <main className="app-main">
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/queue" element={<QueuePage />} />
          <Route path="/shifts" element={<ShiftsBoardPage />} />
          <Route path="/shifts/review/:planId" element={<ShiftRosterReviewPage />} />
          <Route path="/start-flows" element={<StartFlowsPage />} />
          <Route path="/articles" element={<ArticlesPage />} />
          <Route path="/articles/:runId" element={<ArticleDetailPage />} />
          <Route path="/stats" element={<StatsPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/desks" element={<DeskDetailPage />} />
          <Route path="/desks/shift" element={<DeskShiftPage />} />
          <Route path="/flows" element={<FlowsPage />} />
          <Route path="/flows/new" element={<FlowCreatePage />} />
          <Route path="/flows/edit" element={<FlowEditorPage />} />
          <Route path="/flows/performance" element={<FlowPerformancePage />} />
          <Route path="/flows/batch" element={<FlowBatchComparisonPage />} />
          <Route path="/personas" element={<PersonasPage />} />
          <Route path="/personas/:slug" element={<PersonaDetailPage />} />
          <Route path="/prompts/:topicSlug" element={<PromptsPage />} />
          <Route path="/runs/:runId" element={<RunDetailPage />} />
        </Routes>
      </main>
    </div>
  );
}
