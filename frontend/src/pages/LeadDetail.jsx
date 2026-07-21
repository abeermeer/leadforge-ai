import { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { format } from 'date-fns';
import {
  ArrowLeft,
  ExternalLink,
  Mail,
  Phone,
  MapPin,
  Globe,
  Check,
  X,
  AlertTriangle,
  Star,
  Users,
  Megaphone,
  Eye,
  Pencil,
  Send,
  RefreshCw,
  Save,
  Layers,
  Timer,
  FileText,
  Brain,
  Search,
  PenLine,
  MailX,
} from 'lucide-react';
import api from '../api/client';
import StatusBadge from '../components/StatusBadge';
import ScoreBadge from '../components/ScoreBadge';
import EmptyState from '../components/EmptyState';

/* ── helpers ─────────────────────────────────────────────────── */

/** First defined, non-null value among candidate keys. */
function pick(obj, ...keysList) {
  if (!obj || typeof obj !== 'object') return undefined;
  for (const k of keysList) {
    if (obj[k] !== undefined && obj[k] !== null) return obj[k];
  }
  return undefined;
}

const isEmptyObj = (o) => !o || typeof o !== 'object' || Object.keys(o).length === 0;

function fmtDate(iso, pattern = 'MMM d, HH:mm') {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : format(d, pattern);
}

/** Normalize a ratio that may be 0–1 or 0–100 into a 0–100 percent. */
function toPercent(v) {
  const n = Number(v);
  if (Number.isNaN(n)) return null;
  return Math.max(0, Math.min(100, n <= 1 ? Math.round(n * 100) : Math.round(n)));
}

function extractError(err) {
  const detail = err && err.response && err.response.data && err.response.data.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map((d) => (d && d.msg ? d.msg : String(d))).join(' · ');
  return 'Request failed.';
}

/* ── tiny presentational pieces ─────────────────────────────── */

function Chip({ children, tone = 'neutral' }) {
  const tones = {
    neutral: 'border-trax9-border bg-trax9-border/20 text-trax9-text',
    gold: 'border-trax9-gold/40 bg-trax9-gold/10 text-trax9-gold',
    cyan: 'border-trax9-cyan/40 bg-trax9-cyan/10 text-trax9-cyan',
    green: 'border-trax9-green/40 bg-trax9-green/10 text-trax9-green',
    red: 'border-trax9-red/40 bg-trax9-red/10 text-trax9-red',
    violet: 'border-trax9-violet/40 bg-trax9-violet/10 text-trax9-violet',
  };
  return (
    <span
      className={[
        'inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-medium',
        tones[tone] || tones.neutral,
      ].join(' ')}
    >
      {children}
    </span>
  );
}

function CheckRow({ label, ok }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-trax9-border/60 py-2 last:border-b-0">
      <span className="text-sm text-trax9-text">{label}</span>
      {ok ? (
        <span className="flex items-center gap-1.5 text-xs font-medium text-trax9-green">
          <Check size={14} /> Pass
        </span>
      ) : (
        <span className="flex items-center gap-1.5 text-xs font-medium text-trax9-red">
          <X size={14} /> Fail
        </span>
      )}
    </div>
  );
}

function MetricTile({ icon: Icon, label, value }) {
  return (
    <div className="rounded-lg border border-trax9-border bg-trax9-bg/40 p-3">
      <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.12em] text-trax9-muted">
        {Icon && <Icon size={12} />}
        {label}
      </div>
      <div className="mono-readout mt-1 text-lg font-semibold text-trax9-text">
        {value === null || value === undefined ? '—' : value}
      </div>
    </div>
  );
}

/** Pure-SVG circular score gauge (stroke-dasharray ring). */
function Gauge({ label, value }) {
  const has = value !== undefined && value !== null && !Number.isNaN(Number(value));
  const v = has ? toPercent(value) : null;
  const r = 34;
  const c = 2 * Math.PI * r;
  let stroke = '#e2e7f4';
  if (v !== null) {
    if (v >= 90) stroke = '#34d399';
    else if (v >= 50) stroke = '#f5a623';
    else stroke = '#f87171';
  }
  return (
    <div className="flex flex-col items-center gap-2">
      <svg width="84" height="84" viewBox="0 0 84 84" role="img" aria-label={`${label} score ${v === null ? 'unknown' : v}`}>
        <circle cx="42" cy="42" r={r} fill="none" stroke="#e2e7f4" strokeWidth="6" />
        {v !== null && (
          <circle
            cx="42"
            cy="42"
            r={r}
            fill="none"
            stroke={stroke}
            strokeWidth="6"
            strokeLinecap="round"
            strokeDasharray={`${(v / 100) * c} ${c}`}
            transform="rotate(-90 42 42)"
          />
        )}
        <text
          x="42"
          y="48"
          textAnchor="middle"
          fill={v === null ? '#8b94ab' : '#e6e9f2'}
          fontSize="18"
          fontFamily="ui-monospace, monospace"
          fontWeight="600"
        >
          {v === null ? '—' : v}
        </text>
      </svg>
      <div className="label-caps text-center">{label}</div>
    </div>
  );
}

function RatingStars({ rating }) {
  const n = Number(rating);
  if (Number.isNaN(n)) return null;
  return (
    <div className="flex items-center gap-1" aria-label={`${n} out of 5 stars`}>
      {[1, 2, 3, 4, 5].map((i) => (
        <Star
          key={i}
          size={16}
          className={i <= Math.round(n) ? 'text-trax9-gold' : 'text-trax9-border'}
          fill={i <= Math.round(n) ? '#f5a623' : 'none'}
          strokeWidth={1.5}
        />
      ))}
      <span className="mono-readout ml-1.5 text-sm font-semibold text-trax9-text">{n.toFixed(1)}</span>
    </div>
  );
}

/* ── tab contents ────────────────────────────────────────────── */

function WebsiteTab({ data }) {
  if (isEmptyObj(data)) {
    return <EmptyState icon={Globe} title="No website audit yet" hint="Run the AUDIT agent from the campaign to x-ray this site." />;
  }

  const tech = pick(data, 'tech_stack', 'technologies', 'stack') || [];

  const loadRaw = pick(data, 'load_time_ms', 'load_ms', 'load_time_s', 'load_time', 'load_seconds');
  let loadDisplay = null;
  if (loadRaw !== undefined) {
    const n = Number(loadRaw);
    if (!Number.isNaN(n)) loadDisplay = n > 1000 ? `${(n / 1000).toFixed(2)} s` : `${n.toFixed(2)} s`;
  }

  const sizeRaw = pick(data, 'page_size_kb', 'page_size_bytes', 'page_size');
  let sizeDisplay = null;
  if (sizeRaw !== undefined) {
    const n = Number(sizeRaw);
    if (!Number.isNaN(n)) sizeDisplay = n > 10000 ? `${Math.round(n / 1024)} KB` : `${Math.round(n)} KB`;
  }

  // Pass/fail checks — only render rows the audit actually reported
  const rows = [];
  const addRow = (label, val) => {
    if (typeof val === 'boolean') rows.push({ label, ok: val });
  };
  addRow(
    'Title tag',
    pick(data, 'title_ok', 'has_title') ??
      (typeof data.title === 'string' ? data.title.trim().length > 0 : undefined)
  );
  addRow(
    'Meta description',
    pick(data, 'meta_ok', 'meta_description_ok', 'has_meta_description') ??
      (typeof data.meta_description === 'string' ? data.meta_description.trim().length > 0 : undefined)
  );
  addRow('SSL / HTTPS', pick(data, 'ssl', 'has_ssl', 'https'));
  addRow('Mobile viewport', pick(data, 'has_viewport', 'mobile_friendly', 'viewport'));
  if (data.checks && typeof data.checks === 'object') {
    Object.entries(data.checks).forEach(([k, v]) => {
      if (typeof v === 'boolean') rows.push({ label: k.replace(/_/g, ' '), ok: v });
    });
  }

  const altPct = toPercent(pick(data, 'alt_ratio', 'img_alt_ratio', 'images_with_alt_ratio', 'alt_text_ratio'));

  return (
    <div className="space-y-6">
      <div className="grid gap-3 sm:grid-cols-2">
        <MetricTile icon={Timer} label="Load time" value={loadDisplay} />
        <MetricTile icon={FileText} label="Page size" value={sizeDisplay} />
      </div>

      {Array.isArray(tech) && tech.length > 0 && (
        <div>
          <div className="label-caps mb-2 flex items-center gap-1.5">
            <Layers size={12} /> Tech stack
          </div>
          <div className="flex flex-wrap gap-1.5">
            {tech.map((t, i) => (
              <Chip key={i} tone="cyan">
                {typeof t === 'string' ? t : JSON.stringify(t)}
              </Chip>
            ))}
          </div>
        </div>
      )}

      {rows.length > 0 && (
        <div>
          <div className="label-caps mb-1">On-page checks</div>
          <div className="rounded-lg border border-trax9-border bg-trax9-bg/40 px-4 py-1">
            {rows.map((r, i) => (
              <CheckRow key={i} label={r.label} ok={r.ok} />
            ))}
          </div>
        </div>
      )}

      {altPct !== null && (
        <div>
          <div className="mb-1.5 flex items-baseline justify-between">
            <span className="label-caps">Image alt coverage</span>
            <span className="mono-readout text-sm font-semibold text-trax9-text">{altPct}%</span>
          </div>
          <div className="h-2 overflow-hidden rounded-full border border-trax9-border bg-trax9-bg/80">
            <div
              className="h-full rounded-full bg-trax9-gold transition-[width] duration-500"
              style={{ width: `${altPct}%` }}
            />
          </div>
        </div>
      )}
    </div>
  );
}

function SeoTab({ data }) {
  if (isEmptyObj(data)) {
    return <EmptyState icon={Search} title="No SEO audit yet" hint="PageSpeed and on-page analysis appear here after the audit runs." />;
  }

  // Scores may sit at the top level or under a nested `scores` object
  const src = data.scores && typeof data.scores === 'object' ? { ...data, ...data.scores } : data;
  const gauges = [
    { label: 'Performance', value: pick(src, 'performance', 'performance_score') },
    { label: 'Accessibility', value: pick(src, 'accessibility', 'accessibility_score') },
    { label: 'Best Practices', value: pick(src, 'best_practices', 'best-practices', 'bestPractices') },
    { label: 'SEO', value: pick(src, 'seo', 'seo_score') },
  ];
  const anyGauge = gauges.some((g) => g.value !== undefined && g.value !== null);

  const onpage = pick(src, 'onpage_score', 'on_page_score');
  const issues = pick(data, 'issues', 'onpage_issues', 'problems') || [];

  return (
    <div className="space-y-6">
      {anyGauge && (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          {gauges.map((g) => (
            <Gauge key={g.label} label={g.label} value={g.value} />
          ))}
        </div>
      )}

      {onpage !== undefined && (
        <div className="flex items-baseline gap-3 rounded-lg border border-trax9-border bg-trax9-bg/40 p-4">
          <span className="label-caps">On-page score</span>
          <span className="mono-readout text-4xl font-bold text-trax9-cyan">{toPercent(onpage)}</span>
          <span className="mono-readout text-sm text-trax9-muted">/100</span>
        </div>
      )}

      {Array.isArray(issues) && issues.length > 0 && (
        <div>
          <div className="label-caps mb-2">Issues found</div>
          <ul className="space-y-1.5">
            {issues.map((issue, i) => {
              const text =
                typeof issue === 'string'
                  ? issue
                  : (issue && (issue.message || issue.title || issue.description)) || JSON.stringify(issue);
              const sev = (issue && issue.severity) || '';
              const critical = /critical|high|error/i.test(String(sev));
              return (
                <li key={i} className="flex items-start gap-2 text-sm text-trax9-text">
                  <AlertTriangle
                    size={14}
                    className={['mt-0.5 shrink-0', critical ? 'text-trax9-red' : 'text-trax9-gold'].join(' ')}
                  />
                  <span>{text}</span>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {!anyGauge && onpage === undefined && (!Array.isArray(issues) || issues.length === 0) && (
        <EmptyState icon={Search} title="SEO data incomplete" hint="The audit returned no usable SEO metrics for this lead." />
      )}
    </div>
  );
}

function AdSampleCard({ sample }) {
  if (typeof sample === 'string') {
    return (
      <div className="rounded-lg border border-trax9-border bg-trax9-bg/40 p-4 text-sm text-trax9-text">
        {sample}
      </div>
    );
  }
  const headline = pick(sample, 'headline', 'title');
  const text = pick(sample, 'text', 'body', 'description', 'copy');
  const cta = pick(sample, 'cta', 'call_to_action', 'cta_text');
  return (
    <div className="rounded-lg border border-trax9-border bg-trax9-bg/40 p-4">
      {headline && <div className="text-sm font-semibold text-trax9-text">{headline}</div>}
      {text && <p className="mt-1.5 text-sm leading-relaxed text-trax9-muted">{text}</p>}
      {cta && (
        <div className="mt-3">
          <Chip tone="gold">{cta}</Chip>
        </div>
      )}
    </div>
  );
}

function MetaAdsTab({ data }) {
  if (isEmptyObj(data)) {
    return <EmptyState icon={Megaphone} title="No Meta Ads intel yet" hint="Ad Library findings land here once the audit runs." />;
  }
  const hasAds = pick(data, 'has_ads', 'is_advertiser', 'active');
  const count = pick(data, 'ads_count', 'count', 'total_ads');
  const samples = pick(data, 'samples', 'sample_ads', 'ads') || [];
  const libraryUrl = pick(data, 'ad_library_url', 'library_url', 'url');

  return (
    <div className="space-y-5">
      <div
        className={[
          'flex flex-wrap items-center justify-between gap-3 rounded-lg border p-4',
          hasAds
            ? 'border-trax9-green/40 bg-trax9-green/10'
            : 'border-trax9-border bg-trax9-bg/40',
        ].join(' ')}
      >
        <div className="flex items-center gap-3">
          <Megaphone size={18} className={hasAds ? 'text-trax9-green' : 'text-trax9-muted'} />
          <div>
            <div className={['text-sm font-semibold', hasAds ? 'text-trax9-green' : 'text-trax9-text'].join(' ')}>
              {hasAds ? 'Running Meta ads' : 'No active Meta ads detected'}
            </div>
            {count !== undefined && (
              <div className="mono-readout text-xs text-trax9-muted">{count} active creatives</div>
            )}
          </div>
        </div>
        {libraryUrl && (
          <a
            href={libraryUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="btn-ghost px-3 py-1.5 text-xs"
          >
            Ad Library <ExternalLink size={12} />
          </a>
        )}
      </div>

      {Array.isArray(samples) && samples.length > 0 && (
        <div>
          <div className="label-caps mb-2">Sample creatives</div>
          <div className="grid gap-3 sm:grid-cols-2">
            {samples.map((s, i) => (
              <AdSampleCard key={i} sample={s} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function GoogleAdsTab({ data }) {
  if (isEmptyObj(data)) {
    return <EmptyState icon={Search} title="No Google Ads intel yet" hint="Transparency Center findings land here once the audit runs." />;
  }
  const isAdv = pick(data, 'is_advertiser', 'has_ads', 'active');
  const formats = pick(data, 'formats', 'ad_formats') || [];
  const samples = pick(data, 'samples', 'sample_ads', 'creatives') || [];

  return (
    <div className="space-y-5">
      <div
        className={[
          'flex items-center gap-3 rounded-lg border p-4',
          isAdv ? 'border-trax9-green/40 bg-trax9-green/10' : 'border-trax9-border bg-trax9-bg/40',
        ].join(' ')}
      >
        <Search size={18} className={isAdv ? 'text-trax9-green' : 'text-trax9-muted'} />
        <div className={['text-sm font-semibold', isAdv ? 'text-trax9-green' : 'text-trax9-text'].join(' ')}>
          {isAdv ? 'Verified Google advertiser' : 'Not currently advertising on Google'}
        </div>
      </div>

      {Array.isArray(formats) && formats.length > 0 && (
        <div>
          <div className="label-caps mb-2">Ad formats</div>
          <div className="flex flex-wrap gap-1.5">
            {formats.map((f, i) => (
              <Chip key={i} tone="cyan">
                {typeof f === 'string' ? f : JSON.stringify(f)}
              </Chip>
            ))}
          </div>
        </div>
      )}

      {Array.isArray(samples) && samples.length > 0 && (
        <div>
          <div className="label-caps mb-2">Sample creatives</div>
          <div className="grid gap-3 sm:grid-cols-2">
            {samples.map((s, i) => (
              <AdSampleCard key={i} sample={s} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function BrandTab({ data }) {
  if (isEmptyObj(data)) {
    return <EmptyState icon={Brain} title="No brand analysis yet" hint="The AI brand read appears after the audit stage." />;
  }
  const industry = pick(data, 'industry', 'vertical');
  const audience = pick(data, 'audience', 'target_audience');
  const positioning = pick(data, 'positioning', 'positioning_summary');
  const strengths = pick(data, 'strengths') || [];
  const weaknesses = pick(data, 'weaknesses') || [];
  const painPoints = pick(data, 'pain_points', 'painpoints') || [];

  return (
    <div className="space-y-5">
      <div className="rounded-lg border border-trax9-violet/30 bg-trax9-violet/5 p-4">
        <div className="mb-3 flex items-center gap-2 text-trax9-violet">
          <Brain size={16} />
          <span className="text-[11px] font-semibold uppercase tracking-[0.14em]">AI brand read</span>
        </div>
        <div className="space-y-2.5">
          {industry && (
            <div className="flex flex-col gap-0.5 sm:flex-row sm:gap-3">
              <span className="w-28 shrink-0 text-xs font-medium text-trax9-muted">Industry</span>
              <span className="text-sm text-trax9-text">{industry}</span>
            </div>
          )}
          {audience && (
            <div className="flex flex-col gap-0.5 sm:flex-row sm:gap-3">
              <span className="w-28 shrink-0 text-xs font-medium text-trax9-muted">Audience</span>
              <span className="text-sm text-trax9-text">{audience}</span>
            </div>
          )}
          {positioning && (
            <div className="flex flex-col gap-0.5 sm:flex-row sm:gap-3">
              <span className="w-28 shrink-0 text-xs font-medium text-trax9-muted">Positioning</span>
              <span className="text-sm italic text-trax9-text">{positioning}</span>
            </div>
          )}
        </div>
      </div>

      {(strengths.length > 0 || weaknesses.length > 0) && (
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="rounded-lg border border-trax9-border bg-trax9-bg/40 p-4">
            <div className="label-caps mb-2 text-trax9-green">Strengths</div>
            <ul className="space-y-1.5">
              {strengths.map((s, i) => (
                <li key={i} className="flex items-start gap-2 text-sm text-trax9-text">
                  <Check size={14} className="mt-0.5 shrink-0 text-trax9-green" />
                  {typeof s === 'string' ? s : JSON.stringify(s)}
                </li>
              ))}
              {strengths.length === 0 && <li className="text-sm text-trax9-muted">None noted.</li>}
            </ul>
          </div>
          <div className="rounded-lg border border-trax9-border bg-trax9-bg/40 p-4">
            <div className="label-caps mb-2 text-trax9-red">Weaknesses</div>
            <ul className="space-y-1.5">
              {weaknesses.map((w, i) => (
                <li key={i} className="flex items-start gap-2 text-sm text-trax9-text">
                  <X size={14} className="mt-0.5 shrink-0 text-trax9-red" />
                  {typeof w === 'string' ? w : JSON.stringify(w)}
                </li>
              ))}
              {weaknesses.length === 0 && <li className="text-sm text-trax9-muted">None noted.</li>}
            </ul>
          </div>
        </div>
      )}

      {painPoints.length > 0 && (
        <div>
          <div className="label-caps mb-2">Pain points to hit in outreach</div>
          <div className="flex flex-wrap gap-1.5">
            {painPoints.map((p, i) => (
              <Chip key={i} tone="violet">
                {typeof p === 'string' ? p : JSON.stringify(p)}
              </Chip>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function SocialTab({ data }) {
  const skipped =
    !data ||
    isEmptyObj(data) ||
    data.skipped === true ||
    data.status === 'skipped';
  if (skipped) {
    return (
      <EmptyState
        icon={Users}
        title="No social intel"
        hint="Social enrichment was skipped for this lead — its fit score sat below your enrichment threshold, or the ENRICH agent hasn't run yet."
      />
    );
  }

  const rating = pick(data, 'rating', 'reviews_rating') ?? (data.reviews && pick(data.reviews, 'rating', 'score'));
  const reviewCount =
    pick(data, 'reviews_count', 'review_count') ?? (data.reviews && pick(data.reviews, 'count', 'total'));
  const linkedinSize =
    pick(data, 'linkedin_size', 'linkedin_company_size', 'company_size') ??
    (data.linkedin && pick(data.linkedin, 'size', 'company_size', 'employees'));
  const followers = pick(data, 'follower_count', 'followers', 'total_followers');
  const signals = pick(data, 'signals', 'social_signals') || [];

  return (
    <div className="space-y-5">
      {(rating !== undefined || reviewCount !== undefined) && (
        <div className="flex flex-wrap items-center gap-4 rounded-lg border border-trax9-border bg-trax9-bg/40 p-4">
          {rating !== undefined && <RatingStars rating={rating} />}
          {reviewCount !== undefined && (
            <span className="mono-readout text-sm text-trax9-muted">{reviewCount} reviews</span>
          )}
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        {linkedinSize !== undefined && (
          <MetricTile icon={Users} label="LinkedIn size" value={linkedinSize} />
        )}
        {followers !== undefined && (
          <MetricTile icon={Users} label="Followers" value={Number(followers).toLocaleString ? Number(followers).toLocaleString() : followers} />
        )}
      </div>

      {Array.isArray(signals) && signals.length > 0 && (
        <div>
          <div className="label-caps mb-2">Signals</div>
          <div className="flex flex-wrap gap-1.5">
            {signals.map((s, i) => (
              <Chip key={i} tone="cyan">
                {typeof s === 'string' ? s : JSON.stringify(s)}
              </Chip>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/* ── tabs shell ──────────────────────────────────────────────── */

const TABS = [
  { id: 'website', label: 'Website' },
  { id: 'seo', label: 'SEO' },
  { id: 'meta', label: 'Meta Ads' },
  { id: 'google', label: 'Google Ads' },
  { id: 'brand', label: 'Brand' },
  { id: 'social', label: 'Social' },
];

function TabBar({ active, onChange }) {
  return (
    <div className="flex gap-1 overflow-x-auto border-b border-trax9-border" role="tablist" aria-label="Audit intel">
      {TABS.map(({ id, label }) => {
        const isActive = active === id;
        const activeCls =
          id === 'brand'
            ? 'border-trax9-violet text-trax9-violet'
            : 'border-trax9-gold text-trax9-gold';
        return (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={isActive}
            onClick={() => onChange(id)}
            className={[
              'whitespace-nowrap border-b-2 px-4 py-2.5 text-sm transition-colors',
              isActive
                ? `${activeCls} font-semibold`
                : 'border-transparent font-medium text-trax9-muted hover:text-trax9-text',
            ].join(' ')}
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}

/* ── score reason chips ─────────────────────────────────────── */

function reasonToText(r) {
  if (typeof r === 'string') return r;
  if (r && typeof r === 'object') {
    const pts = pick(r, 'points', 'delta', 'score');
    const label = pick(r, 'reason', 'label', 'text') || '';
    if (pts !== undefined) {
      const n = Number(pts);
      return `${n > 0 ? '+' : ''}${n} ${label}`.trim();
    }
    return String(label || JSON.stringify(r));
  }
  return String(r);
}

function ScoreReasons({ reasons }) {
  if (!Array.isArray(reasons) || reasons.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {reasons.map((r, i) => {
        const text = reasonToText(r);
        const negative = /^\s*-/.test(text);
        return (
          <span
            key={i}
            className={[
              'mono-readout inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-medium',
              negative
                ? 'border-trax9-red/40 bg-trax9-red/10 text-trax9-red'
                : 'border-trax9-green/40 bg-trax9-green/10 text-trax9-green',
            ].join(' ')}
          >
            {text}
          </span>
        );
      })}
    </div>
  );
}

/* ── page ────────────────────────────────────────────────────── */

export default function LeadDetail() {
  const { id } = useParams();
  const navigate = useNavigate();

  const [lead, setLead] = useState(null);
  const [loadState, setLoadState] = useState('loading'); // loading | ok | error
  const [tab, setTab] = useState('website');

  // Email editor
  const [subject, setSubject] = useState('');
  const [body, setBody] = useState('');
  const [preview, setPreview] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveFlash, setSaveFlash] = useState(false);
  const [saveError, setSaveError] = useState('');
  const flashTimer = useRef(null);

  useEffect(() => {
    let cancelled = false;
    setLoadState('loading');
    api
      .get(`/leads/${id}`)
      .then((res) => {
        if (cancelled) return;
        setLead(res.data);
        setSubject(res.data.email_subject || '');
        setBody(res.data.email_body || '');
        setLoadState('ok');
      })
      .catch(() => {
        if (!cancelled) setLoadState('error');
      });
    return () => {
      cancelled = true;
      if (flashTimer.current) clearTimeout(flashTimer.current);
    };
  }, [id]);

  async function handleSaveEmail() {
    if (saving) return;
    setSaveError('');
    setSaving(true);
    try {
      const res = await api.put(`/leads/${id}`, { email_subject: subject, email_body: body });
      if (res.data && res.data.id) setLead(res.data);
      setSaveFlash(true);
      if (flashTimer.current) clearTimeout(flashTimer.current);
      flashTimer.current = setTimeout(() => setSaveFlash(false), 2500);
    } catch (err) {
      setSaveError(extractError(err));
    } finally {
      setSaving(false);
    }
  }

  if (loadState === 'loading') {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <div className="mono-readout text-xs uppercase tracking-[0.3em] text-trax9-muted">
          Loading lead intel…
        </div>
      </div>
    );
  }

  if (loadState === 'error' || !lead) {
    return (
      <div className="mx-auto max-w-2xl">
        <EmptyState
          icon={AlertTriangle}
          title="Lead not found"
          hint="It may have been deleted, or the backend is unreachable."
        />
        <div className="mt-4 flex justify-center">
          <button type="button" className="btn-ghost" onClick={() => navigate(-1)}>
            <ArrowLeft size={15} /> Back
          </button>
        </div>
      </div>
    );
  }

  const audit = lead.audit_data || {};
  const confidence = toPercent(lead.email_confidence);
  const confidenceTone =
    confidence === null ? '' : confidence >= 80 ? 'green' : confidence >= 50 ? 'gold' : 'red';
  const discovered = fmtDate(lead.created_at, 'MMM d, yyyy');
  const hasEmailDraft = !!(lead.email_subject || lead.email_body || subject || body);

  const timeline = [
    { label: 'Sent', value: fmtDate(lead.sent_at) },
    { label: 'Opened', value: fmtDate(lead.opened_at) },
    { label: 'Replied', value: fmtDate(lead.replied_at) },
  ].filter((t) => t.value);

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      {/* Back */}
      <button
        type="button"
        onClick={() => navigate(-1)}
        className="flex items-center gap-1.5 text-sm font-medium text-trax9-muted transition-colors hover:text-trax9-text"
      >
        <ArrowLeft size={15} /> Back to campaign
      </button>

      {/* ── Company header ─────────────────────────────────── */}
      <header className="panel p-6 motion-safe:animate-fade-up">
        <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="text-2xl font-semibold tracking-tight text-trax9-text">
                {lead.company_name}
              </h1>
              {lead.website && (
                <a
                  href={lead.website}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-1 text-sm text-trax9-cyan transition-colors hover:text-trax9-text"
                >
                  <Globe size={14} />
                  <span className="max-w-[220px] truncate">
                    {lead.website.replace(/^https?:\/\//, '')}
                  </span>
                  <ExternalLink size={12} />
                </a>
              )}
            </div>

            <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-1.5 text-sm text-trax9-muted">
              {lead.email && (
                <span className="flex items-center gap-1.5">
                  <Mail size={14} />
                  <span className="mono-readout text-trax9-text">{lead.email}</span>
                  {confidence !== null && (
                    <Chip tone={confidenceTone}>{confidence}% conf</Chip>
                  )}
                </span>
              )}
              {lead.phone && (
                <span className="flex items-center gap-1.5">
                  <Phone size={14} />
                  <span className="mono-readout">{lead.phone}</span>
                </span>
              )}
              {(lead.address || lead.city) && (
                <span className="flex items-center gap-1.5">
                  <MapPin size={14} />
                  {lead.address || lead.city}
                </span>
              )}
              {discovered && (
                <span className="mono-readout text-xs">discovered {discovered}</span>
              )}
            </div>
          </div>

          {/* Status + big score */}
          <div className="flex shrink-0 items-center gap-5">
            <StatusBadge status={lead.status} />
            <div className="flex flex-col items-center gap-1 rounded-xl border border-trax9-border bg-trax9-bg/40 px-5 py-3">
              <span className="label-caps">Fit</span>
              <span className="mono-readout text-3xl font-bold leading-none text-trax9-gold">
                {lead.fit_score === null || lead.fit_score === undefined ? '—' : lead.fit_score}
              </span>
              <ScoreBadge score={lead.fit_score} />
            </div>
          </div>
        </div>

        {/* Score reasons */}
        {Array.isArray(lead.score_reasons) && lead.score_reasons.length > 0 && (
          <div className="mt-5 border-t border-trax9-border pt-4">
            <div className="label-caps mb-2">Score breakdown</div>
            <ScoreReasons reasons={lead.score_reasons} />
          </div>
        )}
      </header>

      {/* ── Audit intel tabs ───────────────────────────────── */}
      <section className="panel motion-safe:animate-fade-up">
        <TabBar active={tab} onChange={setTab} />
        <div className="p-5 sm:p-6" role="tabpanel">
          {tab === 'website' && <WebsiteTab data={audit.website} />}
          {tab === 'seo' && <SeoTab data={audit.seo} />}
          {tab === 'meta' && <MetaAdsTab data={audit.meta_ads} />}
          {tab === 'google' && <GoogleAdsTab data={audit.google_ads} />}
          {tab === 'brand' && <BrandTab data={audit.brand_rnd} />}
          {tab === 'social' && <SocialTab data={audit.social} />}
        </div>
      </section>

      {/* ── Outreach email ─────────────────────────────────── */}
      <section className="panel p-5 motion-safe:animate-fade-up sm:p-6">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <PenLine size={16} className="text-trax9-gold" />
            <h2 className="text-sm font-semibold uppercase tracking-[0.14em] text-trax9-text">
              Outreach email
            </h2>
          </div>
          {timeline.length > 0 && (
            <div className="mono-readout flex flex-wrap gap-x-4 gap-y-1 text-xs text-trax9-muted">
              {timeline.map((t) => (
                <span key={t.label}>
                  <span className="text-trax9-green">{t.label}</span> {t.value}
                </span>
              ))}
            </div>
          )}
        </div>

        {!hasEmailDraft ? (
          <EmptyState
            icon={MailX}
            title="No email drafted yet"
            hint="The WRITE agent drafts personalized copy once the lead is scored and enriched."
          />
        ) : (
          <>
            {/* Edit / preview toggle */}
            <div className="mb-4 inline-grid grid-cols-2 gap-1 rounded-lg border border-trax9-border bg-trax9-bg/60 p-1">
              <button
                type="button"
                onClick={() => setPreview(false)}
                aria-pressed={!preview}
                className={[
                  'flex items-center gap-1.5 rounded-md px-3 py-1 text-xs transition-colors',
                  !preview
                    ? 'bg-trax9-gold font-semibold text-trax9-ink'
                    : 'font-medium text-trax9-muted hover:text-trax9-text',
                ].join(' ')}
              >
                <Pencil size={12} /> Edit
              </button>
              <button
                type="button"
                onClick={() => setPreview(true)}
                aria-pressed={preview}
                className={[
                  'flex items-center gap-1.5 rounded-md px-3 py-1 text-xs transition-colors',
                  preview
                    ? 'bg-trax9-gold font-semibold text-trax9-ink'
                    : 'font-medium text-trax9-muted hover:text-trax9-text',
                ].join(' ')}
              >
                <Eye size={12} /> Preview
              </button>
            </div>

            {preview ? (
              <div className="rounded-lg border border-trax9-border bg-trax9-bg/40 p-5">
                <div className="border-b border-trax9-border pb-3">
                  <span className="label-caps mr-2">Subject</span>
                  <span className="text-sm font-semibold text-trax9-text">
                    {subject || <span className="text-trax9-muted">(no subject)</span>}
                  </span>
                </div>
                <div className="mt-4 whitespace-pre-wrap text-sm leading-relaxed text-trax9-text">
                  {body || <span className="text-trax9-muted">(empty body)</span>}
                </div>
              </div>
            ) : (
              <div className="space-y-4">
                <label className="block">
                  <span className="label-caps">Subject</span>
                  <input
                    type="text"
                    className="input-dark mt-1.5"
                    value={subject}
                    onChange={(e) => setSubject(e.target.value)}
                    placeholder="Subject line"
                  />
                </label>
                <label className="block">
                  <span className="label-caps">Body</span>
                  <textarea
                    className="input-dark mt-1.5 min-h-[260px] resize-y leading-relaxed"
                    value={body}
                    onChange={(e) => setBody(e.target.value)}
                    placeholder="Email body"
                  />
                </label>
              </div>
            )}

            {/* Actions */}
            <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-trax9-border pt-4">
              <div className="flex items-center gap-2">
                <span title="Phase 4">
                  <button type="button" className="btn-ghost" disabled>
                    <RefreshCw size={15} /> Regenerate
                  </button>
                </span>
                <span title="Phase 4">
                  <button type="button" className="btn-ghost" disabled>
                    <Send size={15} /> Send
                  </button>
                </span>
              </div>
              <div className="flex items-center gap-3">
                {saveError && (
                  <span className="text-xs text-trax9-red motion-safe:animate-fade-up" role="alert">
                    {saveError}
                  </span>
                )}
                {saveFlash && (
                  <span className="flex items-center gap-1.5 text-xs font-medium text-trax9-green motion-safe:animate-fade-up">
                    <Check size={14} /> Saved
                  </span>
                )}
                <button type="button" className="btn-gold" onClick={handleSaveEmail} disabled={saving}>
                  <Save size={15} />
                  {saving ? 'Saving…' : 'Save copy'}
                </button>
              </div>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
