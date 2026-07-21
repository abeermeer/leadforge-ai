import { useState } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import {
  LayoutDashboard,
  Rocket,
  Brain,
  Settings2,
  LogOut,
  Menu,
  X,
  ChevronsLeft,
  ChevronsRight,
} from 'lucide-react';
import { useAuth } from '../context/AuthContext';

const NAV_ITEMS = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, end: true },
  { to: '/campaigns', label: 'Campaigns', icon: Rocket },
  { to: '/onboarding', label: 'Agency Brain', icon: Brain },
  { to: '/settings', label: 'Settings', icon: Settings2 },
];

function pageTitle(pathname) {
  if (pathname === '/') return 'Dashboard';
  if (pathname.startsWith('/campaigns/')) return 'Campaign Detail';
  if (pathname.startsWith('/campaigns')) return 'Campaigns';
  if (pathname.startsWith('/leads/')) return 'Lead Intel';
  if (pathname.startsWith('/onboarding')) return 'Agency Brain';
  if (pathname.startsWith('/settings')) return 'Settings';
  return 'Mission Control';
}

function Wordmark({ collapsed }) {
  if (collapsed) {
    return (
      <img src="/trax9-logo-dark.png" alt="Trax9" className="h-7 w-auto" style={{ maxWidth: 34, objectFit: 'contain', objectPosition: 'left' }} />
    );
  }
  return (
    <div className="flex flex-col gap-1">
      <img src="/trax9-logo-dark.png" alt="Trax9" className="h-6 w-auto" style={{ maxWidth: 120 }} />
      <div className="text-[9px] font-semibold uppercase tracking-[0.3em] text-trax9-muted">
        Mission Control
      </div>
    </div>
  );
}

function NavLinks({ collapsed, onNavigate }) {
  return (
    <nav className="flex flex-1 flex-col gap-1 px-2 py-4" aria-label="Primary">
      {NAV_ITEMS.map(({ to, label, icon: Icon, end }) => (
        <NavLink
          key={to}
          to={to}
          end={end}
          onClick={onNavigate}
          title={collapsed ? label : undefined}
          className={({ isActive }) =>
            [
              'flex items-center gap-3 rounded-lg border-l-2 px-3 py-2.5 text-sm font-medium transition-colors',
              collapsed ? 'justify-center' : '',
              isActive
                ? 'border-trax9-gold bg-trax9-gold/5 text-trax9-gold'
                : 'border-transparent text-trax9-muted hover:bg-trax9-border/30 hover:text-trax9-text',
            ].join(' ')
          }
        >
          <Icon size={18} strokeWidth={1.75} className="shrink-0" />
          {!collapsed && <span>{label}</span>}
        </NavLink>
      ))}
    </nav>
  );
}

export default function Layout() {
  const { user, logout } = useAuth();
  const location = useLocation();
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);

  const title = pageTitle(location.pathname);

  return (
    <div className="flex min-h-screen bg-trax9-bg">
      {/* ── Sidebar (md and up) ─────────────────────────────── */}
      <aside
        className={[
          'sticky top-0 hidden h-screen flex-col border-r border-trax9-border bg-trax9-panel-solid/60 backdrop-blur-md transition-all md:flex',
          collapsed ? 'w-[68px]' : 'w-60',
        ].join(' ')}
      >
        <div
          className={[
            'flex h-16 items-center border-b border-trax9-border',
            collapsed ? 'justify-center px-2' : 'px-5',
          ].join(' ')}
        >
          <Wordmark collapsed={collapsed} />
        </div>

        <NavLinks collapsed={collapsed} />

        <button
          type="button"
          onClick={() => setCollapsed((c) => !c)}
          className="flex items-center justify-center gap-2 border-t border-trax9-border px-3 py-3 text-xs text-trax9-muted transition-colors hover:text-trax9-text"
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {collapsed ? <ChevronsRight size={16} /> : <ChevronsLeft size={16} />}
          {!collapsed && <span>Collapse</span>}
        </button>
      </aside>

      {/* ── Mobile drawer ───────────────────────────────────── */}
      {mobileOpen && (
        <div className="fixed inset-0 z-40 md:hidden" role="dialog" aria-modal="true">
          <div
            className="absolute inset-0 bg-black/60 backdrop-blur-sm"
            onClick={() => setMobileOpen(false)}
            aria-hidden="true"
          />
          <aside className="absolute inset-y-0 left-0 flex w-64 flex-col border-r border-trax9-border bg-trax9-panel-solid motion-safe:animate-fade-up">
            <div className="flex h-16 items-center justify-between border-b border-trax9-border px-5">
              <Wordmark collapsed={false} />
              <button
                type="button"
                onClick={() => setMobileOpen(false)}
                className="text-trax9-muted hover:text-trax9-text"
                aria-label="Close menu"
              >
                <X size={20} />
              </button>
            </div>
            <NavLinks collapsed={false} onNavigate={() => setMobileOpen(false)} />
          </aside>
        </div>
      )}

      {/* ── Main column ─────────────────────────────────────── */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-16 items-center justify-between border-b border-trax9-border bg-trax9-bg/80 px-4 backdrop-blur-md sm:px-6">
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => setMobileOpen(true)}
              className="text-trax9-muted hover:text-trax9-text md:hidden"
              aria-label="Open menu"
            >
              <Menu size={20} />
            </button>
            <h1 className="text-base font-semibold tracking-wide text-trax9-text">{title}</h1>
          </div>

          <div className="flex items-center gap-4">
            {user && (
              <div className="hidden items-center gap-2 sm:flex">
                <span className="h-1.5 w-1.5 rounded-full bg-trax9-green" aria-hidden="true" />
                <span className="text-sm text-trax9-muted">{user.name || user.email}</span>
              </div>
            )}
            <button
              type="button"
              onClick={logout}
              className="flex items-center gap-2 rounded-lg border border-trax9-border px-3 py-1.5 text-xs font-medium text-trax9-muted transition-colors hover:border-trax9-red/50 hover:text-trax9-red"
            >
              <LogOut size={14} />
              <span className="hidden sm:inline">Log out</span>
            </button>
          </div>
        </header>

        <main className="relative flex-1">
          <div className="glow-topleft pointer-events-none absolute inset-0" aria-hidden="true" />
          <div className="relative mx-auto w-full max-w-7xl px-4 py-6 sm:px-6">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}
