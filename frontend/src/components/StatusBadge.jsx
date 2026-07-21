/**
 * Pill badge for every lead status in the pipeline.
 * discovered → finding_email → auditing → audited → scored → enriching →
 * enriched → writing → written → queued → sending → sent → opened → replied
 * (+ bounced / unsubscribed / failed)
 */
const STATUS_MAP = {
  discovered: { label: 'Discovered', tone: 'neutral' },
  finding_email: { label: 'Finding Email', tone: 'live' },
  auditing: { label: 'Auditing', tone: 'live' },
  audited: { label: 'Audited', tone: 'brain' },
  scored: { label: 'Scored', tone: 'brain' },
  enriching: { label: 'Enriching', tone: 'live' },
  enriched: { label: 'Enriched', tone: 'brain' },
  writing: { label: 'Writing', tone: 'live' },
  written: { label: 'Written', tone: 'gold' },
  queued: { label: 'Queued', tone: 'gold' },
  sending: { label: 'Sending', tone: 'live' },
  sent: { label: 'Sent', tone: 'gold' },
  opened: { label: 'Opened', tone: 'success' },
  replied: { label: 'Replied', tone: 'success' },
  bounced: { label: 'Bounced', tone: 'error' },
  unsubscribed: { label: 'Unsubscribed', tone: 'error' },
  failed: { label: 'Failed', tone: 'error' },
};

const TONE_STYLES = {
  neutral: {
    pill: 'border-trax9-border bg-trax9-border/20 text-trax9-muted',
    dot: 'bg-trax9-muted',
    pulse: false,
  },
  live: {
    pill: 'border-trax9-cyan/40 bg-trax9-cyan/10 text-trax9-cyan',
    dot: 'bg-trax9-cyan',
    pulse: true,
  },
  brain: {
    pill: 'border-trax9-violet/40 bg-trax9-violet/10 text-trax9-violet',
    dot: 'bg-trax9-violet',
    pulse: false,
  },
  gold: {
    pill: 'border-trax9-gold/40 bg-trax9-gold/10 text-trax9-gold',
    dot: 'bg-trax9-gold',
    pulse: false,
  },
  success: {
    pill: 'border-trax9-green/40 bg-trax9-green/10 text-trax9-green',
    dot: 'bg-trax9-green',
    pulse: false,
  },
  error: {
    pill: 'border-trax9-red/40 bg-trax9-red/10 text-trax9-red',
    dot: 'bg-trax9-red',
    pulse: false,
  },
};

export default function StatusBadge({ status }) {
  const entry = STATUS_MAP[status] || {
    label: status ? String(status).replace(/_/g, ' ') : 'Unknown',
    tone: 'neutral',
  };
  const tone = TONE_STYLES[entry.tone] || TONE_STYLES.neutral;

  return (
    <span
      className={[
        'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium whitespace-nowrap',
        tone.pill,
      ].join(' ')}
    >
      <span
        className={[
          'h-1.5 w-1.5 shrink-0 rounded-full',
          tone.dot,
          tone.pulse ? 'motion-safe:animate-pulse' : '',
        ].join(' ')}
        aria-hidden="true"
      />
      {entry.label}
    </span>
  );
}
