import { Navigate, Route, Routes } from 'react-router-dom';
import { LoadingState } from '@fde/ui';
import { useAuth } from './lib/auth';
import Shell from './components/Shell';
import SignIn from './pages/SignIn';
import TodaysPicks from './pages/TodaysPicks';
import LiveSlate from './pages/LiveSlate';
import SlateLive from './pages/SlateLive';
import DataHealthLive from './pages/DataHealthLive';
import ModelAuditLive from './pages/ModelAuditLive';
import WeeklySlate from './pages/WeeklySlate';
import GameLab from './pages/GameLab';
import InjuryCenter from './pages/InjuryCenter';
import MarketMonitor from './pages/MarketMonitor';
import BetPortfolio from './pages/BetPortfolio';
import PerformanceLab from './pages/PerformanceLab';
import ModelAudit from './pages/ModelAudit';
import DataHealth from './pages/DataHealth';
import Settings from './pages/Settings';

export default function App() {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-bg">
        <LoadingState label="Starting Fourth Down Edge…" />
      </div>
    );
  }

  if (!user) {
    return (
      <Routes>
        <Route path="*" element={<SignIn />} />
      </Routes>
    );
  }

  return (
    <Routes>
      <Route element={<Shell />}>
        <Route path="/" element={<TodaysPicks />} />
        <Route path="/live" element={<LiveSlate />} />
        <Route path="/slate" element={<SlateLive />} />
        <Route path="/slate-demo" element={<WeeklySlate />} />
        <Route path="/game/:gameId" element={<GameLab />} />
        <Route path="/injuries" element={<InjuryCenter />} />
        <Route path="/market" element={<MarketMonitor />} />
        <Route path="/portfolio" element={<BetPortfolio />} />
        <Route path="/performance" element={<PerformanceLab />} />
        <Route path="/models" element={<ModelAuditLive />} />
        <Route path="/models-demo" element={<ModelAudit />} />
        <Route path="/health" element={<DataHealthLive />} />
        <Route path="/health-demo" element={<DataHealth />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
