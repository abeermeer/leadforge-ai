const ACCENTS = {
  gold: { icon: 'text-trax9-gold', chip: 'bg-trax9-gold/10 border-trax9-gold/25' },
  cyan: { icon: 'text-trax9-cyan', chip: 'bg-trax9-cyan/10 border-trax9-cyan/25' },
  green: { icon: 'text-trax9-green', chip: 'bg-trax9-green/10 border-trax9-green/25' },
  red: { icon: 'text-trax9-red', chip: 'bg-trax9-red/10 border-trax9-red/25' },
  violet: { icon: 'text-trax9-violet', chip: 'bg-trax9-violet/10 border-trax9-violet/25' },
};

/**
 * Key-number panel for dashboards.
 * `icon` is a lucide-react component (e.g. icon={Rocket}).
 * `accent` one of: gold (default) | cyan | green | red | violet.
 */
export default function StatCard({ label, value, icon: Icon, accent = 'gold' }) {
  const tone = ACCENTS[accent] || ACCENTS.gold;

  return (
    <div className="panel flex items-center gap-4 p-4 motion-safe:animate-fade-up">
      {Icon && (
        <div
          className={[
            'flex h-11 w-11 shrink-0 items-center justify-center rounded-lg border',
            tone.chip,
          ].join(' ')}
        >
          <Icon size={20} strokeWidth={1.75} className={tone.icon} />
        </div>
      )}
      <div className="min-w-0">
        <div className="label-caps truncate">{label}</div>
        <div className="mono-readout mt-0.5 truncate text-2xl font-semibold text-trax9-text">
          {value === null || value === undefined ? '—' : value}
        </div>
      </div>
    </div>
  );
}
