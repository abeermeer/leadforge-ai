/**
 * Fit score readout 0–100: red < 40, amber 40–69, green >= 70.
 * Renders an em dash when the lead hasn't been scored yet.
 */
export default function ScoreBadge({ score }) {
  if (score === null || score === undefined) {
    return (
      <span className="mono-readout inline-flex min-w-[2.5rem] items-center justify-center rounded-md border border-trax9-border px-2 py-0.5 text-xs text-trax9-muted/60">
        &mdash;
      </span>
    );
  }

  const n = Number(score);
  let cls;
  if (n >= 70) {
    cls = 'border-trax9-green/40 bg-trax9-green/10 text-trax9-green';
  } else if (n >= 40) {
    cls = 'border-trax9-gold/40 bg-trax9-gold/10 text-trax9-gold';
  } else {
    cls = 'border-trax9-red/40 bg-trax9-red/10 text-trax9-red';
  }

  return (
    <span
      className={[
        'mono-readout inline-flex min-w-[2.5rem] items-center justify-center rounded-md border px-2 py-0.5 text-xs font-semibold',
        cls,
      ].join(' ')}
      title={`Fit score ${n}/100`}
    >
      {n}
    </span>
  );
}
