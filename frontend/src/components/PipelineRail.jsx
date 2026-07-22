import { Fragment } from 'react';
import {
  Brain,
  Radar,
  AtSign,
  ScanSearch,
  Gauge,
  Sparkles,
  PenLine,
  Send,
} from 'lucide-react';
import { toDate } from '../utils/datetime';

/**
 * THE PIPELINE — a living rail of AI agents.
 * Props: { tasks, statusCounts, compact }
 *
 * Visual language:
 *  - idle   : dim, dormant
 *  - active : cyan, breathing halo + orbiting ring + data pulses travelling in
 *  - done   : gold, filled, soft glow
 *  - error  : red
 */
const STAGES = [
  { key: 'brain', label: 'Brain', icon: Brain, statuses: null, verb: 'learning your agency' },
  { key: 'discovery', label: 'Discover', icon: Radar, statuses: ['discovered'], verb: 'mapping the market' },
  { key: 'email_find', label: 'Email', icon: AtSign, statuses: ['finding_email'], verb: 'finding inboxes' },
  { key: 'audit', label: 'Audit', icon: ScanSearch, statuses: ['auditing', 'audited'], verb: 'x-raying sites' },
  { key: 'score', label: 'Score', icon: Gauge, statuses: ['scored'], verb: 'ranking fit' },
  { key: 'enrich', label: 'Enrich', icon: Sparkles, statuses: ['enriching', 'enriched'], verb: 'adding intel' },
  { key: 'write', label: 'Write', icon: PenLine, statuses: ['writing', 'written'], verb: 'drafting openers' },
  {
    key: 'send',
    label: 'Send',
    icon: Send,
    statuses: ['queued', 'sending', 'sent', 'opened', 'replied'],
    verb: 'landing pitches',
  },
];

function stageForTaskType(type) {
  const t = String(type || '').toLowerCase();
  if (t.includes('brain') || t.includes('profile') || t.includes('analyz')) return 'brain';
  if (t.includes('discover')) return 'discovery';
  if (t.includes('audit')) return 'audit';
  if (t.includes('scor')) return 'score';
  if (t.includes('enrich') || t.includes('social')) return 'enrich';
  if (t.includes('writ') || t.includes('compose')) return 'write';
  if (t.includes('send') || t.includes('outbox')) return 'send';
  if (t.includes('email') || t.includes('hunter')) return 'email_find';
  return null;
}

function taskTime(t) {
  const d = toDate(t.updated_at || t.created_at) || new Date(0);
  return Number.isNaN(d.getTime()) ? 0 : d.getTime();
}

function computeState(stageKey, stageTasks, count, missionLive) {
  const list = stageTasks[stageKey] || [];
  if (list.some((t) => t.status === 'running')) return 'active';
  if (list.length > 0) {
    const latest = [...list].sort((a, b) => taskTime(b) - taskTime(a))[0];
    if (latest.status === 'failed') return 'error';
    if (latest.status === 'completed') return 'done';
  }
  if (count > 0) return 'done';
  if (stageKey === 'brain' && missionLive) return 'done';
  return 'idle';
}

const NODE_CLS = {
  idle: 'border-trax9-border bg-trax9-panel-solid text-trax9-muted/60',
  active: 'border-trax9-cyan bg-trax9-cyan/10 text-trax9-cyan shadow-glow-cyan',
  done: 'border-trax9-gold bg-trax9-gold text-trax9-ink shadow-glow-gold',
  error: 'border-trax9-red/70 bg-trax9-red/10 text-trax9-red',
};
const LABEL_CLS = {
  idle: 'text-trax9-muted/60',
  active: 'text-trax9-cyan',
  done: 'text-trax9-gold',
  error: 'text-trax9-red',
};
const BADGE_CLS = {
  idle: 'border-trax9-border text-trax9-muted',
  active: 'border-trax9-cyan/60 text-trax9-cyan motion-safe:animate-tick',
  done: 'border-trax9-gold/50 text-trax9-gold',
  error: 'border-trax9-red/50 text-trax9-red',
};

/** Data pulses that travel along an active connector. */
function TravellingPulses() {
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden="true">
      {[0, 0.45, 0.9].map((delay, i) => (
        <span
          key={i}
          className="absolute top-1/2 left-0 h-1 w-1 -translate-y-1/2 rounded-full bg-trax9-cyan shadow-[0_0_6px_1px_rgba(5,195,222,0.9)] motion-safe:animate-travel"
          style={{ '--travel': '100%', animationDelay: `${delay}s` }}
        />
      ))}
    </div>
  );
}

export default function PipelineRail({ tasks = [], statusCounts = {}, compact = false }) {
  const stageTasks = {};
  (tasks || []).forEach((t) => {
    const key = stageForTaskType(t.type);
    if (!key) return;
    (stageTasks[key] = stageTasks[key] || []).push(t);
  });

  const counts = {};
  let totalLeads = 0;
  STAGES.forEach((s) => {
    if (!s.statuses) return (counts[s.key] = null);
    const n = s.statuses.reduce((sum, st) => sum + (Number(statusCounts[st]) || 0), 0);
    counts[s.key] = n;
    totalLeads += n;
  });
  const missionLive = totalLeads > 0 || (tasks || []).length > 0;
  const states = STAGES.map((s) => computeState(s.key, stageTasks, counts[s.key] || 0, missionLive));

  const activeIdx = states.indexOf('active');
  const activeStage = activeIdx >= 0 ? STAGES[activeIdx] : null;
  const runningTask = activeIdx >= 0 ? (stageTasks[STAGES[activeIdx].key] || []).find((t) => t.status === 'running') : null;

  const circleCls = compact ? 'h-11 w-11' : 'h-16 w-16';
  const iconSize = compact ? 17 : 22;
  const colCls = compact ? 'w-[68px]' : 'w-24';
  const connectorMt = compact ? 'mt-[42px]' : 'mt-[54px]';

  return (
    <div className="relative">
      {/* Live status line — reads like an agent narrating its work */}
      {!compact && (
        <div className="mb-2 flex items-center gap-2 px-2">
          <span
            className={[
              'h-2 w-2 rounded-full',
              activeStage
                ? 'bg-trax9-cyan shadow-glow-cyan motion-safe:animate-blink'
                : missionLive
                  ? 'bg-trax9-gold'
                  : 'bg-trax9-muted/50',
            ].join(' ')}
          />
          <span className="mono-readout text-[11px] tracking-wide text-trax9-muted">
            {activeStage ? (
              <>
                <span className="text-trax9-cyan">{activeStage.label.toUpperCase()} AGENT</span>
                {' · '}
                {activeStage.verb}
                {runningTask ? ` · ${runningTask.completed_items}/${runningTask.total_items}` : ''}
                <span className="motion-safe:animate-blink"> ▋</span>
              </>
            ) : missionLive ? (
              <span className="text-trax9-gold/80">ALL AGENTS · standing by</span>
            ) : (
              'AGENT PIPELINE · idle'
            )}
          </span>
        </div>
      )}

      <div className="overflow-x-auto" role="group" aria-label="Agent pipeline">
        <div
          className={[
            'flex items-start',
            compact ? 'min-w-[600px] px-1 py-2' : 'min-w-[760px] px-2 py-3',
          ].join(' ')}
        >
          {STAGES.map((stage, i) => {
            const state = states[i];
            const Icon = stage.icon;
            const count = counts[stage.key];

            let baseCls = 'stroke-trax9-border';
            let flowing = false;
            if (i > 0) {
              if (state === 'active') {
                baseCls = 'stroke-trax9-cyan/40';
                flowing = true;
              } else if (state === 'done' && states[i - 1] === 'done') {
                baseCls = 'stroke-trax9-gold/45';
              }
            }

            return (
              <Fragment key={stage.key}>
                {i > 0 && (
                  <div className={['relative min-w-[18px] flex-1', connectorMt].join(' ')} aria-hidden="true">
                    <svg width="100%" height="2" className="block overflow-visible">
                      <line
                        x1="0"
                        y1="1"
                        x2="100%"
                        y2="1"
                        strokeWidth="2"
                        strokeDasharray="5 7"
                        className={[baseCls, flowing ? 'motion-safe:animate-flow' : ''].join(' ')}
                      />
                    </svg>
                    {flowing && <TravellingPulses />}
                  </div>
                )}

                <div className={['flex shrink-0 flex-col items-center', colCls].join(' ')}>
                  <div className="flex h-5 items-center">
                    {count !== null && count > 0 && (
                      <span
                        className={[
                          'mono-readout rounded-full border px-1.5 text-[10px] leading-4',
                          BADGE_CLS[state],
                        ].join(' ')}
                        title={`${count} leads`}
                      >
                        {count}
                      </span>
                    )}
                  </div>

                  <div
                    className={[
                      'relative flex items-center justify-center rounded-full border-2 transition-all duration-500',
                      circleCls,
                      NODE_CLS[state],
                    ].join(' ')}
                    aria-label={`${stage.label}: ${state}`}
                  >
                    {/* breathing halo + orbit ring while working */}
                    {state === 'active' && (
                      <>
                        <span
                          className="absolute inset-0 rounded-full bg-trax9-cyan/25 motion-safe:animate-breathe"
                          aria-hidden="true"
                        />
                        <span
                          className="absolute -inset-1.5 rounded-full border border-dashed border-trax9-cyan/40 motion-safe:animate-orbit"
                          aria-hidden="true"
                        />
                      </>
                    )}
                    {state === 'done' && stage.key !== 'brain' && (
                      <span
                        className="absolute inset-0 rounded-full motion-safe:animate-pulse-glow"
                        aria-hidden="true"
                      />
                    )}
                    <Icon size={iconSize} strokeWidth={1.75} className="relative z-10" />
                  </div>

                  <div
                    className={[
                      'mt-2 text-center font-semibold uppercase tracking-wider',
                      compact ? 'text-[9px]' : 'text-[11px]',
                      LABEL_CLS[state],
                    ].join(' ')}
                  >
                    {stage.label}
                  </div>
                </div>
              </Fragment>
            );
          })}
        </div>
      </div>
    </div>
  );
}