import { useEffect, useState } from 'react';
import { MailCheck, AlertTriangle, Ban, Reply, Flag } from 'lucide-react';
import api from '../api/client';

/**
 * "How did last week's sending go" in one glance (ops 3.1).
 *
 * Bounce rate is the headline number because ESPs suspend accounts over it and
 * they tell you after the damage. It turns amber at 3% and red at 5% — the
 * thresholds at which providers start throttling.
 */
const WINDOWS = [7, 30];

function rateTone(rate) {
  if (rate >= 5) return { text: 'text-trax9-red', ring: 'border-trax9-red/50', label: 'critical' };
  if (rate >= 3) return { text: 'text-[#b45309]', ring: 'border-[#f59e0b]/50', label: 'watch' };
  return { text: 'text-trax9-green', ring: 'border-trax9-green/40', label: 'healthy' };
}

function Stat({ icon: Icon, label, value, tone = 'text-trax9-text' }) {
  return (
    <div className="flex items-center gap-2.5">
      <Icon size={15} strokeWidth={1.9} className="shrink-0 text-trax9-muted" />
      <div className="min-w-0">
        <div className={['mono-readout text-lg font-semibold leading-none', tone].join(' ')}>
          {value}
        </div>
        <div className="label-caps mt-1 truncate">{label}</div>
      </div>
    </div>
  );
}

export default function DeliverabilityPanel() {
  const [days, setDays] = useState(7);
  const [data, setData] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    setError('');
    api
      .get('/metrics/summary', { params: { days } })
      .then((res) => {
        if (!cancelled) setData(res.data);
      })
      .catch(() => {
        if (!cancelled) setError('Could not load deliverability metrics.');
      });
    return () => {
      cancelled = true;
    };
  }, [days]);

  const t = data ? data.totals : null;
  const tone = rateTone(t ? t.bounce_rate : 0);

  return (
    <div className="node-card p-4 sm:p-5">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <span className="label-caps">Deliverability &mdash; last {days} days</span>
        <div className="flex gap-1">
          {WINDOWS.map((d) => (
            <button
              key={d}
              type="button"
              onClick={() => setDays(d)}
              className={[
                'rounded-md px-2 py-1 text-[11px] font-semibold transition-colors',
                d === days
                  ? 'bg-trax9-gold text-white'
                  : 'text-trax9-muted hover:text-trax9-text',
              ].join(' ')}
            >
              {d}d
            </button>
          ))}
        </div>
      </div>

      {error && <p className="text-sm text-trax9-red">{error}</p>}

      {!error && !t && (
        <p className="mono-readout text-xs text-trax9-muted">LOADING METRICS&hellip;</p>
      )}

      {t && (
        <>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Stat icon={MailCheck} label="Delivered" value={t.delivered} />
            <Stat icon={Ban} label="Bounced" value={t.bounced} />
            <Stat icon={Reply} label="Replied" value={t.replied} />
            <Stat icon={Flag} label="Spam reports" value={t.spam} tone={t.spam > 0 ? 'text-trax9-red' : undefined} />
          </div>

          <div
            className={[
              'mt-4 flex flex-wrap items-center justify-between gap-2 rounded-lg border bg-white/60 px-3 py-2.5',
              tone.ring,
            ].join(' ')}
          >
            <span className="flex items-center gap-2 text-sm text-trax9-muted">
              <AlertTriangle size={15} className={tone.text} />
              Bounce rate
            </span>
            <span className={['mono-readout text-sm font-semibold', tone.text].join(' ')}>
              {t.bounce_rate}% &middot; {tone.label}
            </span>
          </div>

          {t.sent === 0 && (
            <p className="mt-3 text-xs text-trax9-muted">
              No sends in this window yet &mdash; numbers appear once a campaign sends.
            </p>
          )}
        </>
      )}
    </div>
  );
}
