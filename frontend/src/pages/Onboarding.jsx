import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Brain,
  Sparkles,
  Rocket,
  RefreshCw,
  MapPin,
  Tag,
  Quote,
  Check,
  Target,
} from 'lucide-react';
import api from '../api/client';
import EmptyState from '../components/EmptyState';

/** Honest scan readout — cycles while the request is genuinely in flight. */
const SCAN_LINES = [
  'Reading site…',
  'Extracting services…',
  'Profiling ideal client…',
  'Mining keyword space…',
  'Calibrating positioning…',
];

function extractError(err) {
  const detail = err && err.response && err.response.data && err.response.data.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map((d) => (d && d.msg ? d.msg : String(d))).join(' · ');
  return 'Analysis failed. Check the URL and your API keys in Settings.';
}

function serviceText(s) {
  if (typeof s === 'string') return { title: s, desc: null };
  if (s && typeof s === 'object') {
    return { title: s.name || s.title || s.service || 'Service', desc: s.description || s.desc || null };
  }
  return { title: String(s), desc: null };
}

function Chip({ children, tone = 'neutral' }) {
  const tones = {
    neutral: 'border-trax9-border bg-trax9-border/20 text-trax9-text',
    gold: 'border-trax9-gold/40 bg-trax9-gold/10 text-trax9-gold',
    cyan: 'border-trax9-cyan/40 bg-trax9-cyan/10 text-trax9-cyan',
    violet: 'border-trax9-violet/40 bg-trax9-violet/10 text-trax9-violet',
  };
  return (
    <span
      className={[
        'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium',
        tones[tone] || tones.neutral,
      ].join(' ')}
    >
      {children}
    </span>
  );
}

function BrainRing({ analyzing }) {
  return (
    <div
      className={[
        'flex h-20 w-20 items-center justify-center rounded-full border-2',
        analyzing
          ? 'border-trax9-cyan/70 shadow-glow-cyan'
          : 'border-trax9-gold/70 motion-safe:animate-pulse-glow',
      ].join(' ')}
    >
      <Brain
        size={34}
        strokeWidth={1.5}
        className={analyzing ? 'text-trax9-cyan motion-safe:animate-pulse' : 'text-trax9-gold'}
      />
    </div>
  );
}

/** Cycling mono status readout shown while POST /profile/analyze is in flight. */
function ScanSequence() {
  const [step, setStep] = useState(0);

  useEffect(() => {
    const t = setInterval(() => {
      setStep((s) => Math.min(s + 1, SCAN_LINES.length - 1));
    }, 1500);
    return () => clearInterval(t);
  }, []);

  return (
    <div className="mx-auto mt-8 w-full max-w-sm text-left">
      <div className="relative mb-6 h-px w-full overflow-hidden bg-trax9-border">
        <div className="absolute inset-y-0 w-1/4 bg-trax9-cyan motion-safe:animate-scan" />
      </div>
      <ol className="space-y-2.5">
        {SCAN_LINES.map((line, i) => {
          const done = i < step;
          const active = i === step;
          return (
            <li
              key={line}
              className={[
                'mono-readout flex items-center gap-2.5 text-sm transition-opacity',
                done ? 'text-trax9-muted' : '',
                active ? 'text-trax9-cyan' : '',
                !done && !active ? 'text-trax9-muted/30' : '',
              ].join(' ')}
            >
              {done ? (
                <Check size={13} className="shrink-0 text-trax9-green/70" />
              ) : (
                <span
                  className={[
                    'h-[7px] w-[7px] shrink-0 rounded-full',
                    active ? 'bg-trax9-cyan motion-safe:animate-pulse' : 'bg-trax9-border',
                  ].join(' ')}
                  aria-hidden="true"
                />
              )}
              {line}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

/** Ideal-client panel renders whatever shape the brain returned. */
function IdealClientPanel({ idealClient }) {
  const entries = Object.entries(idealClient || {}).filter(
    ([, v]) => (Array.isArray(v) && v.length > 0) || (typeof v === 'string' && v.trim() !== '')
  );
  if (entries.length === 0) return null;

  const toneFor = (key) => {
    if (key.includes('industr')) return 'gold';
    if (key.includes('signal')) return 'cyan';
    return 'neutral';
  };

  return (
    <section className="panel p-5 motion-safe:animate-fade-up">
      <div className="mb-4 flex items-center gap-2">
        <Target size={16} className="text-trax9-gold" />
        <h2 className="label-caps">Ideal client</h2>
      </div>
      <div className="space-y-4">
        {entries.map(([key, value]) => {
          const label = key.replace(/_/g, ' ');
          if (Array.isArray(value)) {
            return (
              <div key={key}>
                <div className="mb-1.5 text-xs font-medium capitalize text-trax9-muted">{label}</div>
                <div className="flex flex-wrap gap-1.5">
                  {value.map((v, i) => (
                    <Chip key={`${key}-${i}`} tone={toneFor(key)}>
                      {typeof v === 'string' ? v : JSON.stringify(v)}
                    </Chip>
                  ))}
                </div>
              </div>
            );
          }
          return (
            <div key={key} className="flex flex-col gap-0.5 sm:flex-row sm:items-baseline sm:gap-3">
              <span className="w-36 shrink-0 text-xs font-medium capitalize text-trax9-muted">{label}</span>
              <span className="text-sm text-trax9-text">{value}</span>
            </div>
          );
        })}
      </div>
    </section>
  );
}

export default function Onboarding() {
  const navigate = useNavigate();
  // checking | input | analyzing | result
  const [phase, setPhase] = useState('checking');
  const [profile, setProfile] = useState(null);
  const [url, setUrl] = useState('');
  const [error, setError] = useState('');
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState('');
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    api
      .get('/profile')
      .then((res) => {
        if (!mountedRef.current) return;
        setProfile(res.data);
        setPhase('result');
      })
      .catch(() => {
        // 404 = no brain yet; any other failure also lands on the input view
        if (mountedRef.current) setPhase('input');
      });
    return () => {
      mountedRef.current = false;
    };
  }, []);

  async function handleAnalyze(e) {
    e.preventDefault();
    const website = url.trim();
    if (!website) return;
    setError('');
    setPhase('analyzing');
    try {
      const res = await api.post('/profile/analyze', { website });
      if (!mountedRef.current) return;
      setProfile(res.data);
      setPhase('result');
    } catch (err) {
      if (!mountedRef.current) return;
      setError(extractError(err));
      setPhase('input');
    }
  }

  async function handleCreateCampaign() {
    if (creating || !profile) return;
    setCreateError('');
    setCreating(true);
    try {
      const name = `${profile.company_name || 'Trax9'} — first sweep`;
      const res = await api.post('/campaigns', { name });
      navigate(`/campaigns/${res.data.id}`);
    } catch (err) {
      if (mountedRef.current) {
        setCreateError(extractError(err));
        setCreating(false);
      }
    }
  }

  /* ── Boot check ─────────────────────────────────────────── */
  if (phase === 'checking') {
    return (
      <div className="flex min-h-[50vh] items-center justify-center">
        <div className="mono-readout text-xs uppercase tracking-[0.3em] text-trax9-muted">
          Syncing agency brain…
        </div>
      </div>
    );
  }

  /* ── Hero input / analyzing ─────────────────────────────── */
  if (phase === 'input' || phase === 'analyzing') {
    const analyzing = phase === 'analyzing';
    return (
      <div className="flex min-h-[70vh] items-center justify-center">
        <div className="w-full max-w-xl text-center motion-safe:animate-fade-up">
          <div className="mb-7 flex justify-center">
            <BrainRing analyzing={analyzing} />
          </div>

          <h1 className="text-3xl font-semibold tracking-tight text-trax9-text">
            Teach the machine what you sell
          </h1>
          <p className="mx-auto mt-3 max-w-md text-sm leading-relaxed text-trax9-muted">
            Point the Agency Brain at your website. It reads your services, profiles your ideal
            client, and preloads every campaign with the right keywords and territories.
          </p>

          {analyzing ? (
            <ScanSequence />
          ) : (
            <>
              <form onSubmit={handleAnalyze} className="mx-auto mt-8 flex max-w-md gap-2">
                <input
                  type="text"
                  className="input-dark flex-1"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  placeholder="https://youragency.com"
                  aria-label="Your agency website URL"
                  autoFocus
                />
                <button type="submit" className="btn-gold shrink-0" disabled={!url.trim()}>
                  <Sparkles size={16} />
                  Analyze
                </button>
              </form>
              {error && (
                <div
                  className="mx-auto mt-4 max-w-md rounded-lg border border-trax9-red/40 bg-trax9-red/10 px-3 py-2 text-sm text-trax9-red motion-safe:animate-fade-up"
                  role="alert"
                >
                  {error}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    );
  }

  /* ── Result view ────────────────────────────────────────── */
  const services = Array.isArray(profile && profile.services) ? profile.services : [];
  const keywords = Array.isArray(profile && profile.suggested_keywords)
    ? profile.suggested_keywords
    : [];
  const locations = Array.isArray(profile && profile.suggested_locations)
    ? profile.suggested_locations
    : [];

  return (
    <div className="mx-auto max-w-4xl space-y-5">
      {/* Header */}
      <header className="panel flex flex-col gap-5 p-6 motion-safe:animate-fade-up sm:flex-row sm:items-center">
        <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full border-2 border-trax9-gold/70 motion-safe:animate-pulse-glow">
          <Brain size={24} strokeWidth={1.5} className="text-trax9-gold" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="label-caps">Agency brain · trained</div>
          <h1 className="mt-1 truncate text-2xl font-semibold text-trax9-text">
            {(profile && profile.company_name) || 'Your agency'}
          </h1>
        </div>
        <button
          type="button"
          className="btn-ghost shrink-0"
          onClick={() => {
            setError('');
            setPhase('input');
          }}
        >
          <RefreshCw size={15} />
          Re-analyze
        </button>
      </header>

      {/* Positioning */}
      {profile && profile.positioning && (
        <section className="panel p-5 motion-safe:animate-fade-up">
          <div className="mb-3 flex items-center gap-2">
            <Quote size={16} className="text-trax9-gold" />
            <h2 className="label-caps">Positioning</h2>
          </div>
          <blockquote className="border-l-2 border-trax9-gold pl-4 text-lg font-light italic leading-relaxed text-trax9-text">
            {profile.positioning}
          </blockquote>
        </section>
      )}

      {/* Services */}
      {services.length > 0 ? (
        <section className="motion-safe:animate-fade-up">
          <h2 className="label-caps mb-3">What you sell</h2>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {services.map((s, i) => {
              const { title, desc } = serviceText(s);
              return (
                <div key={i} className="panel flex items-start gap-3 p-4">
                  <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-trax9-gold/25 bg-trax9-gold/10">
                    <Sparkles size={15} className="text-trax9-gold" />
                  </div>
                  <div className="min-w-0">
                    <div className="text-sm font-semibold text-trax9-text">{title}</div>
                    {desc && <div className="mt-1 text-xs leading-relaxed text-trax9-muted">{desc}</div>}
                  </div>
                </div>
              );
            })}
          </div>
        </section>
      ) : (
        <EmptyState
          icon={Sparkles}
          title="No services extracted"
          hint="Re-analyze your site once it lists what you offer."
        />
      )}

      {/* Ideal client */}
      <IdealClientPanel idealClient={profile && profile.ideal_client} />

      {/* Keywords + Locations */}
      <div className="grid gap-5 md:grid-cols-2">
        <section className="panel p-5 motion-safe:animate-fade-up">
          <div className="mb-3 flex items-center gap-2">
            <Tag size={16} className="text-trax9-gold" />
            <h2 className="label-caps">Suggested keywords</h2>
          </div>
          {keywords.length > 0 ? (
            <div className="flex flex-wrap gap-1.5">
              {keywords.map((k, i) => (
                <Chip key={i} tone="gold">
                  {k}
                </Chip>
              ))}
            </div>
          ) : (
            <p className="text-sm text-trax9-muted">None suggested.</p>
          )}
        </section>

        <section className="panel p-5 motion-safe:animate-fade-up">
          <div className="mb-3 flex items-center gap-2">
            <MapPin size={16} className="text-trax9-cyan" />
            <h2 className="label-caps">Suggested territories</h2>
          </div>
          {locations.length > 0 ? (
            <div className="flex flex-wrap gap-1.5">
              {locations.map((l, i) => (
                <Chip key={i} tone="cyan">
                  <MapPin size={11} />
                  {l}
                </Chip>
              ))}
            </div>
          ) : (
            <p className="text-sm text-trax9-muted">None suggested.</p>
          )}
        </section>
      </div>

      {/* CTA */}
      <div className="flex flex-col items-center gap-3 pb-6 pt-2">
        <button type="button" className="btn-gold px-6 py-2.5 text-base" onClick={handleCreateCampaign} disabled={creating}>
          <Rocket size={18} />
          {creating ? 'Deploying…' : 'Create first campaign'}
        </button>
        {createError && (
          <div className="text-sm text-trax9-red motion-safe:animate-fade-up" role="alert">
            {createError}
          </div>
        )}
      </div>
    </div>
  );
}
