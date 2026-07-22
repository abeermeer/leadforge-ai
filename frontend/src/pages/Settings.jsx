import { useCallback, useEffect, useRef, useState } from 'react';
import {
  KeyRound,
  Cpu,
  SlidersHorizontal,
  Send,
  Inbox,
  Save,
  CheckCircle2,
} from 'lucide-react';
import api from '../api/client';

const KEY_DEFS = [
  { k: 'anthropic', label: 'Anthropic' },
  { k: 'openai', label: 'OpenAI' },
  { k: 'sendgrid', label: 'SendGrid' },
  { k: 'google_places', label: 'Google Places' },
  { k: 'google_custom_search', label: 'Google Custom Search' },
  { k: 'google_custom_search_cx', label: 'Google Custom Search CX' },
  { k: 'google_pagespeed', label: 'Google PageSpeed' },
  { k: 'hunter', label: 'Hunter.io' },
  { k: 'socialcrawl', label: 'SocialCrawl' },
];

const ACCENTS = {
  gold: 'border-trax9-gold/25 bg-trax9-gold/10 text-trax9-gold',
  cyan: 'border-trax9-cyan/25 bg-trax9-cyan/10 text-trax9-cyan',
  violet: 'border-trax9-violet/25 bg-trax9-violet/10 text-trax9-violet',
  green: 'border-trax9-green/25 bg-trax9-green/10 text-trax9-green',
};

function extractError(err) {
  const detail = err && err.response && err.response.data && err.response.data.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map((d) => (d && d.msg ? d.msg : String(d))).join(' · ');
  return 'Save failed. Try again.';
}

/** Glass section panel with its own save footer. */
function Section({ icon: Icon, title, hint, accent = 'gold', onSave, busy, flash, error, children }) {
  return (
    <section className="panel p-5 motion-safe:animate-fade-up sm:p-6">
      <header className="mb-5 flex items-start gap-3">
        <div
          className={[
            'flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border',
            ACCENTS[accent] || ACCENTS.gold,
          ].join(' ')}
        >
          <Icon size={18} strokeWidth={1.75} />
        </div>
        <div>
          <h2 className="text-sm font-semibold uppercase tracking-[0.14em] text-trax9-text">{title}</h2>
          {hint && <p className="mt-0.5 text-xs text-trax9-muted">{hint}</p>}
        </div>
      </header>

      {children}

      <footer className="mt-6 flex items-center justify-end gap-3 border-t border-trax9-border pt-4">
        {error && (
          <span className="text-xs text-trax9-red motion-safe:animate-fade-up" role="alert">
            {error}
          </span>
        )}
        {flash && (
          <span className="flex items-center gap-1.5 text-xs font-medium text-trax9-green motion-safe:animate-fade-up">
            <CheckCircle2 size={14} />
            Saved
          </span>
        )}
        <button type="button" className="btn-gold" onClick={onSave} disabled={busy}>
          <Save size={15} />
          {busy ? 'Saving…' : 'Save'}
        </button>
      </footer>
    </section>
  );
}

function Field({ label, note, children }) {
  return (
    <label className="block">
      <span className="label-caps">{label}</span>
      <div className="mt-1.5">{children}</div>
      {note && <p className="mt-1.5 text-xs text-trax9-muted">{note}</p>}
    </label>
  );
}

function Toggle({ checked, onChange, label }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className={[
        'relative h-6 w-11 shrink-0 rounded-full border transition-colors',
        checked ? 'border-trax9-gold bg-trax9-gold/80' : 'border-trax9-border bg-trax9-border/40',
      ].join(' ')}
    >
      <span
        className={[
          'absolute left-[2px] top-[2px] h-[18px] w-[18px] rounded-full transition-transform',
          checked ? 'translate-x-5 bg-trax9-ink' : 'translate-x-0 bg-trax9-muted',
        ].join(' ')}
        aria-hidden="true"
      />
    </button>
  );
}

export default function Settings() {
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [keysMasked, setKeysMasked] = useState({});

  // Editable form state (non-secret fields)
  const [form, setForm] = useState({
    ai_provider: 'anthropic',
    social_enrich_min_score: 60,
    from_email: '',
    from_name: '',
    physical_address: '',
    max_emails_per_day: 50,
    max_emails_per_hour: 10,
    send_start_hour: 9,
    send_end_hour: 17,
    warmup_enabled: false,
    imap_host: '',
    imap_user: '',
  });

  // Secret inputs — only non-empty values are ever sent
  const [keys, setKeys] = useState({});
  const [imapPassword, setImapPassword] = useState('');

  // Per-section save state
  const [busy, setBusy] = useState(null);
  const [flash, setFlash] = useState(null);
  const [errors, setErrors] = useState({});
  const flashTimer = useRef(null);

  const applySettings = useCallback((d) => {
    setKeysMasked(d.keys_masked || {});
    setForm((prev) => ({
      ...prev,
      ai_provider: d.ai_provider || 'anthropic',
      social_enrich_min_score:
        d.social_enrich_min_score === null || d.social_enrich_min_score === undefined
          ? 60
          : Number(d.social_enrich_min_score),
      from_email: d.from_email || '',
      from_name: d.from_name || '',
      physical_address: d.physical_address || '',
      max_emails_per_day: d.max_emails_per_day === undefined || d.max_emails_per_day === null ? 50 : d.max_emails_per_day,
      max_emails_per_hour: d.max_emails_per_hour === undefined || d.max_emails_per_hour === null ? 10 : d.max_emails_per_hour,
      send_start_hour: d.send_start_hour === undefined || d.send_start_hour === null ? 9 : d.send_start_hour,
      send_end_hour: d.send_end_hour === undefined || d.send_end_hour === null ? 17 : d.send_end_hour,
      warmup_enabled: !!d.warmup_enabled,
      imap_host: d.imap_host || '',
      imap_user: d.imap_user || '',
    }));
  }, []);

  useEffect(() => {
    let cancelled = false;
    api
      .get('/settings')
      .then((res) => {
        if (cancelled) return;
        applySettings(res.data || {});
        setLoaded(true);
      })
      .catch(() => {
        if (cancelled) return;
        setLoadError('Could not load settings.');
        setLoaded(true);
      });
    return () => {
      cancelled = true;
      if (flashTimer.current) clearTimeout(flashTimer.current);
    };
  }, [applySettings]);

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  async function saveSection(section, payload, after) {
    setBusy(section);
    setErrors((e) => ({ ...e, [section]: '' }));
    try {
      const res = await api.put('/settings', payload);
      if (res.data && res.data.keys_masked !== undefined) {
        applySettings(res.data);
      }
      if (after) after();
      setFlash(section);
      if (flashTimer.current) clearTimeout(flashTimer.current);
      flashTimer.current = setTimeout(() => setFlash(null), 2500);
    } catch (err) {
      setErrors((e) => ({ ...e, [section]: extractError(err) }));
    } finally {
      setBusy(null);
    }
  }

  function saveKeys() {
    const entries = {};
    KEY_DEFS.forEach(({ k }) => {
      const v = (keys[k] || '').trim();
      if (v) entries[k] = v;
    });
    if (Object.keys(entries).length === 0) {
      setErrors((e) => ({ ...e, keys: 'Enter at least one new key to update.' }));
      return;
    }
    saveSection('keys', { keys: entries }, () => setKeys({}));
  }

  function saveAi() {
    saveSection('ai', { ai_provider: form.ai_provider });
  }

  function saveEnrichment() {
    saveSection('enrichment', { social_enrich_min_score: Number(form.social_enrich_min_score) });
  }

  function saveSending() {
    saveSection('sending', {
      from_email: form.from_email,
      from_name: form.from_name,
      physical_address: form.physical_address,
      max_emails_per_day: Number(form.max_emails_per_day),
      max_emails_per_hour: Number(form.max_emails_per_hour),
      send_start_hour: Number(form.send_start_hour),
      send_end_hour: Number(form.send_end_hour),
      warmup_enabled: form.warmup_enabled,
    });
  }

  function saveReplies() {
    const payload = { imap_host: form.imap_host, imap_user: form.imap_user };
    const pw = imapPassword.trim();
    if (pw) payload.keys = { imap_password: pw };
    saveSection('replies', payload, () => setImapPassword(''));
  }

  if (!loaded) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <div className="mono-readout text-xs uppercase tracking-[0.3em] text-trax9-muted">
          Loading configuration…
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-4xl space-y-5">
      <div className="motion-safe:animate-fade-up">
        <div className="label-caps mb-1 flex items-center gap-1.5">
          <span className="live-dot" aria-hidden="true" />
          Control Room
        </div>
        <h1 className="text-xl font-semibold text-trax9-text">Settings</h1>
        <p className="mt-0.5 text-sm text-trax9-muted">
          Keys, models and guardrails that power the machine.
        </p>
      </div>

      {loadError && (
        <div className="rounded-lg border border-trax9-red/40 bg-trax9-red/10 px-4 py-3 text-sm text-trax9-red" role="alert">
          {loadError}
        </div>
      )}

      {/* ── API KEYS ─────────────────────────────────────────── */}
      <Section
        icon={KeyRound}
        title="API Keys"
        hint="Stored encrypted. Existing keys stay untouched unless you type a new value."
        accent="gold"
        onSave={saveKeys}
        busy={busy === 'keys'}
        flash={flash === 'keys'}
        error={errors.keys}
      >
        <div className="grid gap-4 sm:grid-cols-2">
          {KEY_DEFS.map(({ k, label }) => {
            const masked = keysMasked && keysMasked[k];
            return (
              <Field key={k} label={label}>
                <input
                  type="password"
                  className="input-dark font-mono"
                  value={keys[k] || ''}
                  onChange={(e) => setKeys((prev) => ({ ...prev, [k]: e.target.value }))}
                  placeholder={masked ? `${masked} — set` : 'not set'}
                  autoComplete="new-password"
                  spellCheck={false}
                />
              </Field>
            );
          })}
        </div>
      </Section>

      {/* ── AI ───────────────────────────────────────────────── */}
      <Section
        icon={Cpu}
        title="AI"
        hint="Which model family powers the Brain, scoring, and copywriting agents."
        accent="violet"
        onSave={saveAi}
        busy={busy === 'ai'}
        flash={flash === 'ai'}
        error={errors.ai}
      >
        <div className="label-caps mb-2">Provider</div>
        <div
          className="inline-grid grid-cols-2 gap-1 rounded-full border border-trax9-border bg-trax9-bg/60 p-1"
          role="radiogroup"
          aria-label="AI provider"
        >
          {['anthropic', 'openai'].map((p) => (
            <button
              key={p}
              type="button"
              role="radio"
              aria-checked={form.ai_provider === p}
              onClick={() => set('ai_provider', p)}
              className={[
                'rounded-full px-5 py-1.5 text-sm capitalize transition-colors',
                form.ai_provider === p
                  ? 'bg-trax9-gold font-semibold text-trax9-ink'
                  : 'font-medium text-trax9-muted hover:text-trax9-text',
              ].join(' ')}
            >
              {p}
            </button>
          ))}
        </div>
      </Section>

      {/* ── ENRICHMENT ───────────────────────────────────────── */}
      <Section
        icon={SlidersHorizontal}
        title="Enrichment"
        hint="Social enrichment costs credits — only run it on leads worth the spend."
        accent="cyan"
        onSave={saveEnrichment}
        busy={busy === 'enrichment'}
        flash={flash === 'enrichment'}
        error={errors.enrichment}
      >
        <div className="flex items-center justify-between gap-4">
          <span className="label-caps">SocialCrawl minimum fit score</span>
          <span className="mono-readout rounded-md border border-trax9-gold/40 bg-trax9-gold/10 px-2.5 py-0.5 text-sm font-semibold text-trax9-gold">
            {form.social_enrich_min_score}
          </span>
        </div>
        <input
          type="range"
          min={0}
          max={100}
          step={1}
          value={form.social_enrich_min_score}
          onChange={(e) => set('social_enrich_min_score', Number(e.target.value))}
          className="mt-3 h-1.5 w-full cursor-pointer appearance-none rounded-full bg-trax9-border accent-trax9-gold"
          aria-label="SocialCrawl minimum fit score"
        />
        <div className="mono-readout mt-1.5 flex justify-between text-[10px] text-trax9-muted">
          <span>0 · enrich everyone</span>
          <span>100 · enrich no one</span>
        </div>
      </Section>

      {/* ── SENDING ──────────────────────────────────────────── */}
      <Section
        icon={Send}
        title="Sending"
        hint="Identity, compliance, and throttle controls for the SEND agent."
        accent="gold"
        onSave={saveSending}
        busy={busy === 'sending'}
        flash={flash === 'sending'}
        error={errors.sending}
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="From email">
            <input
              type="email"
              className="input-dark"
              value={form.from_email}
              onChange={(e) => set('from_email', e.target.value)}
              placeholder="you@agency.com"
            />
          </Field>
          <Field label="From name">
            <input
              type="text"
              className="input-dark"
              value={form.from_name}
              onChange={(e) => set('from_name', e.target.value)}
              placeholder="Ada from Trax9"
            />
          </Field>
        </div>

        <div className="mt-4">
          <Field label="Physical address" note="Required by law in every outreach email.">
            <textarea
              className="input-dark min-h-[72px] resize-y"
              value={form.physical_address}
              onChange={(e) => set('physical_address', e.target.value)}
              placeholder={'123 Studio Lane\nAustin, TX 78701'}
            />
          </Field>
        </div>

        <div className="mt-4 grid gap-4 sm:grid-cols-4">
          <Field label="Max / day">
            <input
              type="number"
              min={1}
              max={2000}
              className="input-dark mono-readout"
              value={form.max_emails_per_day}
              onChange={(e) => set('max_emails_per_day', e.target.value)}
            />
          </Field>
          <Field label="Max / hour">
            <input
              type="number"
              min={1}
              max={500}
              className="input-dark mono-readout"
              value={form.max_emails_per_hour}
              onChange={(e) => set('max_emails_per_hour', e.target.value)}
            />
          </Field>
          <Field label="Window start" note="24h clock">
            <input
              type="number"
              min={0}
              max={23}
              className="input-dark mono-readout"
              value={form.send_start_hour}
              onChange={(e) => set('send_start_hour', e.target.value)}
            />
          </Field>
          <Field label="Window end" note="24h clock">
            <input
              type="number"
              min={0}
              max={23}
              className="input-dark mono-readout"
              value={form.send_end_hour}
              onChange={(e) => set('send_end_hour', e.target.value)}
            />
          </Field>
        </div>

        <div className="mt-5 flex items-center justify-between gap-4 rounded-lg border border-trax9-border bg-trax9-bg/40 px-4 py-3">
          <div>
            <div className="text-sm font-medium text-trax9-text">Warmup mode</div>
            <div className="text-xs text-trax9-muted">
              Ramp volume gradually to protect sender reputation.
            </div>
          </div>
          <Toggle
            checked={form.warmup_enabled}
            onChange={(v) => set('warmup_enabled', v)}
            label="Warmup mode"
          />
        </div>
      </Section>

      {/* ── REPLIES ──────────────────────────────────────────── */}
      <Section
        icon={Inbox}
        title="Replies"
        hint="IMAP inbox the reply-detection agent watches."
        accent="green"
        onSave={saveReplies}
        busy={busy === 'replies'}
        flash={flash === 'replies'}
        error={errors.replies}
      >
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="IMAP host">
            <input
              type="text"
              className="input-dark font-mono"
              value={form.imap_host}
              onChange={(e) => set('imap_host', e.target.value)}
              placeholder="imap.gmail.com"
              spellCheck={false}
            />
          </Field>
          <Field label="IMAP user">
            <input
              type="text"
              className="input-dark font-mono"
              value={form.imap_user}
              onChange={(e) => set('imap_user', e.target.value)}
              placeholder="you@agency.com"
              spellCheck={false}
            />
          </Field>
          <Field label="IMAP password">
            <input
              type="password"
              className="input-dark font-mono"
              value={imapPassword}
              onChange={(e) => setImapPassword(e.target.value)}
              placeholder={
                keysMasked && keysMasked.imap_password
                  ? `${keysMasked.imap_password} — set`
                  : 'not set'
              }
              autoComplete="new-password"
            />
          </Field>
        </div>
      </Section>
    </div>
  );
}
