import { useEffect, useRef, useState } from 'react';
import { BrowserRouter, Navigate, Outlet, Route, Routes, useLocation } from 'react-router-dom';
import { AuthProvider, useAuth } from './context/AuthContext';
import api from './api/client';
import Layout from './components/Layout';
import Login from './pages/Login';
import Onboarding from './pages/Onboarding';
import Dashboard from './pages/Dashboard';
import Campaigns from './pages/Campaigns';
import CampaignDetail from './pages/CampaignDetail';
import LeadDetail from './pages/LeadDetail';
import Settings from './pages/Settings';

/** Full-screen boot readout shown while auth / profile state resolves. */
function BootScreen({ line = 'AUTHENTICATING' }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-trax9-bg">
      <div className="flex flex-col items-center gap-4">
        <div className="font-mono text-2xl font-bold tracking-[0.3em] text-trax9-text">
          TRAX<span className="text-trax9-gold">9</span>
        </div>
        <div className="relative h-px w-48 overflow-hidden bg-trax9-border">
          <div className="absolute inset-y-0 w-1/4 bg-trax9-gold motion-safe:animate-scan" />
        </div>
        <div className="mono-readout text-xs text-trax9-muted">{line}&hellip;</div>
      </div>
    </div>
  );
}

/**
 * Auth wall: no token -> /login. While the token is being exchanged
 * for an identity, hold on the boot screen.
 */
function RequireAuth() {
  const { token, loading } = useAuth();
  const location = useLocation();

  if (!token) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }
  if (loading) {
    return <BootScreen line="AUTHENTICATING" />;
  }
  return <ProfileGate />;
}

/**
 * Profile gate: after login, if GET /api/profile 404s the agency brain
 * hasn't been trained yet -> route the operator to /onboarding.
 * Once a profile exists we latch and never re-check this session.
 */
function ProfileGate() {
  const location = useLocation();
  const okRef = useRef(false);
  const [state, setState] = useState('checking'); // checking | ok | missing

  useEffect(() => {
    if (okRef.current) return undefined;
    let cancelled = false;
    setState('checking');
    api
      .get('/profile')
      .then(() => {
        okRef.current = true;
        if (!cancelled) setState('ok');
      })
      .catch((err) => {
        if (cancelled) return;
        if (err.response && err.response.status === 404) {
          setState('missing');
        } else {
          // Network/server hiccup: fail open, don't trap the user
          okRef.current = true;
          setState('ok');
        }
      });
    return () => {
      cancelled = true;
    };
  }, [location.pathname]);

  if (state === 'checking') {
    return <BootScreen line="SYNCING AGENCY BRAIN" />;
  }
  // Only nudge from the dashboard root. Settings must stay reachable without a
  // profile (that's where the AI key that trains the brain gets entered), and
  // the operator should be free to move around before onboarding.
  if (state === 'missing' && location.pathname === '/') {
    return <Navigate to="/onboarding" replace />;
  }
  return <Outlet />;
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route element={<RequireAuth />}>
            <Route element={<Layout />}>
              <Route path="/" element={<Dashboard />} />
              <Route path="/onboarding" element={<Onboarding />} />
              <Route path="/campaigns" element={<Campaigns />} />
              <Route path="/campaigns/:id" element={<CampaignDetail />} />
              <Route path="/leads/:id" element={<LeadDetail />} />
              <Route path="/settings" element={<Settings />} />
            </Route>
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}
