import { useState } from 'react';
import { Navigate, useLocation, useNavigate } from 'react-router-dom';
import { Brain, Compass, AtSign, SearchCheck, Gauge, Sparkles, PenLine, Send } from 'lucide-react';
import { useAuth } from '../context/AuthContext';

/** Decorative agent rail for the brand panel — pure ornament, no data. */
const AGENTS = [
  { name: 'BRAIN', desc: 'learns your agency', icon: Brain, tone: 'text-trax9-violet', dot: 'bg-trax9-violet' },
  { name: 'DISCOVER', desc: 'maps the market', icon: Compass, tone: 'text-trax9-cyan', dot: 'bg-trax9-cyan', pulse: true },
  { name: 'EMAIL FIND', desc: 'locates decision makers', icon: AtSign, tone: 'text-trax9-cyan', dot: 'bg-trax9-cyan' },
  { name: 'AUDIT', desc: 'x-rays their marketing', icon: SearchCheck, tone: 'text-trax9-cyan', dot: 'bg-trax9-cyan', pulse: true },
  { name: 'SCORE', desc: 'ranks the fit', icon: Gauge, tone: 'text-trax9-gold', dot: 'bg-trax9-gold' },
  { name: 'ENRICH', desc: 'adds social intel', icon: Sparkles, tone: 'text-trax9-cyan', dot: 'bg-trax9-cyan' },
  { name: 'WRITE', desc: 'drafts the opener', icon: PenLine, tone: 'text-trax9-violet', dot: 'bg-trax9-violet' },
  { name: 'SEND', desc: 'lands the pitch', icon: Send, tone: 'text-trax9-gold', dot: 'bg-trax9-gold' },
];

function extractError(err) {
  const detail = err && err.response && err.response.data && err.response.data.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => (d && d.msg ? d.msg : String(d)))
      .join(' · ');
  }
  if (err && err.message === 'Network Error') return 'Cannot reach the Trax9 backend.';
  return 'Unable to authenticate. Check your credentials.';
}

function BrandPanel() {
  return (
    <div className="relative hidden flex-col justify-between overflow-hidden border-r border-trax9-border bg-trax9-bg lg:flex lg:w-1/2">
      {/* Grid backdrop */}
      <div
        className="absolute inset-0 opacity-[0.35]"
        style={{
          backgroundImage:
            'linear-gradient(rgba(73,20,196,0.12) 1px, transparent 1px), linear-gradient(90deg, rgba(73,20,196,0.12) 1px, transparent 1px)',
          backgroundSize: '44px 44px',
        }}
        aria-hidden="true"
      />
      {/* Gold radial glow */}
      <div
        className="absolute inset-0"
        style={{
          background:
            'radial-gradient(720px 480px at 20% 10%, rgba(73,20,196,0.08), transparent 60%), radial-gradient(560px 420px at 85% 90%, rgba(0,123,255,0.06), transparent 60%)',
        }}
        aria-hidden="true"
      />
      {/* Scan sweep */}
      <div className="pointer-events-none absolute inset-x-0 top-0 h-full overflow-hidden" aria-hidden="true">
        <div className="absolute inset-y-0 w-1/4 bg-gradient-to-r from-transparent via-trax9-gold/[0.04] to-transparent motion-safe:animate-scan" />
      </div>

      <div className="relative px-12 pt-14">
        <img src="/trax9-logo-dark.png" alt="Trax9" className="h-11 w-auto" style={{ maxWidth: 200 }} />
        <div className="mt-3 text-[10px] font-semibold uppercase tracking-[0.4em] text-trax9-muted">
          Mission Control
        </div>
        <p className="mt-8 max-w-md text-2xl font-light leading-snug text-trax9-text">
          AI agents that <span className="font-semibold text-trax9-gold">find</span>,{' '}
          <span className="font-semibold text-trax9-gold">audit</span>, and{' '}
          <span className="font-semibold text-trax9-gold">win</span> your next clients
        </p>
      </div>

      {/* Decorative agent rail */}
      <div className="relative px-12 pb-14">
        <div className="label-caps mb-4">Agent pipeline · standing by</div>
        <ol className="relative space-y-0 border-l border-trax9-border pl-6">
          {AGENTS.map(({ name, desc, icon: Icon, tone, dot, pulse }) => (
            <li key={name} className="relative flex items-baseline gap-3 py-1.5">
              <span
                className={[
                  'absolute -left-[27.5px] top-1/2 h-[7px] w-[7px] -translate-y-1/2 rounded-full',
                  dot,
                  pulse ? 'motion-safe:animate-pulse' : 'opacity-60',
                ].join(' ')}
                aria-hidden="true"
              />
              <Icon size={13} strokeWidth={1.75} className={['shrink-0 translate-y-0.5', tone].join(' ')} />
              <span className="mono-readout w-24 shrink-0 text-xs font-semibold text-trax9-text">{name}</span>
              <span className="text-xs text-trax9-muted">{desc}</span>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}

export default function Login() {
  const { token, login, register } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const [mode, setMode] = useState('login'); // login | register
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  if (token) {
    return <Navigate to="/" replace />;
  }

  const from = (location.state && location.state.from && location.state.from.pathname) || '/';

  async function handleSubmit(e) {
    e.preventDefault();
    if (submitting) return;
    setError('');
    setSubmitting(true);
    try {
      if (mode === 'login') {
        await login(email.trim(), password);
      } else {
        await register(email.trim(), password, name.trim());
      }
      navigate(from, { replace: true });
    } catch (err) {
      setError(extractError(err));
    } finally {
      setSubmitting(false);
    }
  }

  function switchMode(next) {
    setMode(next);
    setError('');
  }

  return (
    <div className="flex min-h-screen bg-trax9-bg">
      <BrandPanel />

      {/* ── Auth card ─────────────────────────────────────────── */}
      <div className="relative flex flex-1 items-center justify-center px-4 py-10">
        <div
          className="pointer-events-none absolute inset-0"
          style={{
            background: 'radial-gradient(520px 380px at 50% 0%, rgba(73,20,196,0.05), transparent 65%)',
          }}
          aria-hidden="true"
        />
        <div className="panel relative w-full max-w-md p-8 motion-safe:animate-fade-up">
          {/* Mobile wordmark (brand panel hidden below lg) */}
          <div className="mb-8 flex flex-col items-center gap-1 lg:hidden">
            <img src="/trax9-logo-dark.png" alt="Trax9" className="h-8 w-auto" style={{ maxWidth: 150 }} />
            <div className="text-[9px] font-semibold uppercase tracking-[0.35em] text-trax9-muted">
              Mission Control
            </div>
          </div>

          <h1 className="text-lg font-semibold text-trax9-text">
            {mode === 'login' ? 'Operator sign-in' : 'New operator'}
          </h1>
          <p className="mt-1 text-sm text-trax9-muted">
            {mode === 'login'
              ? 'Authenticate to resume your campaigns.'
              : 'Create an account and teach the machine what you sell.'}
          </p>

          {/* Mode toggle */}
          <div className="mt-6 grid grid-cols-2 gap-1 rounded-lg border border-trax9-border bg-trax9-bg/60 p-1" role="tablist" aria-label="Authentication mode">
            {[
              { id: 'login', label: 'Log in' },
              { id: 'register', label: 'Register' },
            ].map(({ id, label }) => (
              <button
                key={id}
                type="button"
                role="tab"
                aria-selected={mode === id}
                onClick={() => switchMode(id)}
                className={[
                  'rounded-md py-1.5 text-sm transition-colors',
                  mode === id
                    ? 'bg-trax9-gold font-semibold text-trax9-ink'
                    : 'font-medium text-trax9-muted hover:text-trax9-text',
                ].join(' ')}
              >
                {label}
              </button>
            ))}
          </div>

          <form onSubmit={handleSubmit} className="mt-6 space-y-4">
            {mode === 'register' && (
              <label className="block">
                <span className="label-caps">Name</span>
                <input
                  type="text"
                  className="input-dark mt-1.5"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Ada Operator"
                  autoComplete="name"
                  required
                />
              </label>
            )}

            <label className="block">
              <span className="label-caps">Email</span>
              <input
                type="email"
                className="input-dark mt-1.5"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@agency.com"
                autoComplete="email"
                required
              />
            </label>

            <label className="block">
              <span className="label-caps">Password</span>
              <input
                type="password"
                className="input-dark mt-1.5"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
                minLength={mode === 'register' ? 8 : undefined}
                required
              />
            </label>

            {error && (
              <div
                className="rounded-lg border border-trax9-red/40 bg-trax9-red/10 px-3 py-2 text-sm text-trax9-red motion-safe:animate-fade-up"
                role="alert"
              >
                {error}
              </div>
            )}

            <button type="submit" className="btn-gold w-full" disabled={submitting}>
              {submitting
                ? 'Authenticating…'
                : mode === 'login'
                  ? 'Enter Mission Control'
                  : 'Create account'}
            </button>
          </form>

          <div className="mono-readout mt-8 text-center text-[10px] uppercase tracking-[0.3em] text-trax9-muted/60">
            Trax9 // Outbound Ops
          </div>
        </div>
      </div>
    </div>
  );
}
