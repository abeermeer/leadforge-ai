/**
 * Animated task progress readout.
 * `task` is a TaskOut: {type, status, total_items, completed_items, failed_items}.
 * Gold fill, mono counts; a scan sweep runs while the task is live.
 */
const STATUS_TEXT = {
  pending: 'text-trax9-muted',
  running: 'text-trax9-cyan',
  completed: 'text-trax9-green',
  failed: 'text-trax9-red',
};

export default function ProgressBar({ task }) {
  if (!task) return null;

  const total = Number(task.total_items) || 0;
  const done = Number(task.completed_items) || 0;
  const failed = Number(task.failed_items) || 0;
  const running = task.status === 'running';

  let pct;
  if (total > 0) {
    pct = Math.max(0, Math.min(100, Math.round((done / total) * 100)));
  } else {
    pct = task.status === 'completed' ? 100 : 0;
  }

  const typeLabel = String(task.type || 'task').replace(/_/g, ' ');
  const statusCls = STATUS_TEXT[task.status] || 'text-trax9-muted';

  return (
    <div className="w-full">
      <div className="mb-1.5 flex items-baseline justify-between gap-3">
        <span className="mono-readout text-[11px] font-semibold uppercase tracking-[0.14em] text-trax9-text">
          {typeLabel}
          <span className={['ml-2 normal-case tracking-normal', statusCls].join(' ')}>
            {task.status}
          </span>
        </span>
        <span className="mono-readout text-xs text-trax9-muted">
          {done}/{total || '?'}
          {failed > 0 && <span className="ml-2 text-trax9-red">{failed} failed</span>}
        </span>
      </div>

      <div
        className="relative h-2 overflow-hidden rounded-full border border-trax9-border bg-trax9-bg/80"
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={`${typeLabel} progress`}
      >
        <div
          className={[
            'h-full rounded-full bg-trax9-gold transition-[width] duration-500 ease-out',
            running ? 'motion-safe:animate-pulse-glow' : '',
          ].join(' ')}
          style={{ width: `${pct}%` }}
        />
        {running && (
          <div className="absolute inset-y-0 w-1/4 bg-gradient-to-r from-transparent via-white/20 to-transparent motion-safe:animate-scan" />
        )}
      </div>
    </div>
  );
}
