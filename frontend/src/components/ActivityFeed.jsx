import { format } from 'date-fns';
import { Activity } from 'lucide-react';

/**
 * "Agent Activity" feed panel — reverse-chron mono readout of task rows:
 *   [12:04:11] AUDIT · 14/40 · running
 * Running rows glow cyan with a soft pulse; feed scrolls past max height.
 */
const DOT_CLS = {
  pending: 'bg-trax9-muted',
  running: 'bg-trax9-cyan motion-safe:animate-pulse',
  completed: 'bg-trax9-green',
  failed: 'bg-trax9-red',
};

const STATUS_CLS = {
  pending: 'text-trax9-muted',
  running: 'text-trax9-cyan',
  completed: 'text-trax9-green',
  failed: 'text-trax9-red',
};

function stamp(value) {
  if (!value) return '--:--:--';
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? '--:--:--' : format(d, 'HH:mm:ss');
}

function taskTime(t) {
  const d = new Date(t.updated_at || t.created_at || 0);
  return Number.isNaN(d.getTime()) ? 0 : d.getTime();
}

export default function ActivityFeed({ tasks }) {
  const rows = [...(tasks || [])].sort((a, b) => taskTime(b) - taskTime(a));
  const anyRunning = rows.some((t) => t.status === 'running');

  return (
    <div className="panel flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-trax9-border px-4 py-3">
        <span className="label-caps">Agent Activity</span>
        {anyRunning && (
          <span className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-widest text-trax9-cyan">
            <span
              className="h-1.5 w-1.5 rounded-full bg-trax9-cyan motion-safe:animate-pulse"
              aria-hidden="true"
            />
            Live
          </span>
        )}
      </div>

      {rows.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 px-4 py-10 text-center">
          <Activity size={20} strokeWidth={1.5} className="text-trax9-muted/60" />
          <span className="mono-readout text-xs text-trax9-muted">All agents idle</span>
        </div>
      ) : (
        <ul className="max-h-72 flex-1 overflow-y-auto py-1" aria-label="Agent activity feed">
          {rows.map((t) => {
            const running = t.status === 'running';
            const typeLabel = String(t.type || 'task').replace(/_/g, ' ').toUpperCase();
            const total = Number(t.total_items) || 0;
            const done = Number(t.completed_items) || 0;
            const failed = Number(t.failed_items) || 0;

            return (
              <li
                key={t.id}
                className={[
                  'flex items-center gap-2 px-4 py-1.5 font-mono text-xs tabular-nums',
                  running
                    ? 'text-trax9-cyan motion-safe:animate-pulse'
                    : 'text-trax9-muted',
                ].join(' ')}
              >
                <span
                  className={[
                    'h-1.5 w-1.5 shrink-0 rounded-full',
                    DOT_CLS[t.status] || 'bg-trax9-muted',
                  ].join(' ')}
                  aria-hidden="true"
                />
                <span className="shrink-0">[{stamp(t.updated_at || t.created_at)}]</span>
                <span
                  className={[
                    'shrink-0 font-semibold',
                    running ? 'text-trax9-cyan' : 'text-trax9-text',
                  ].join(' ')}
                >
                  {typeLabel}
                </span>
                <span className="shrink-0">
                  &middot; {done}/{total || '?'}
                </span>
                {failed > 0 && (
                  <span className="shrink-0 text-trax9-red">&middot; {failed} failed</span>
                )}
                <span
                  className={['truncate', STATUS_CLS[t.status] || 'text-trax9-muted'].join(' ')}
                >
                  &middot; {t.status}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
