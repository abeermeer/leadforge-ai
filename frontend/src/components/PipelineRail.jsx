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
 * THE MACHINE — the pipeline rendered as a node-graph on a dotted canvas.
 * Agents are wired nodes; the wire into a node lights violet (done) or cyan
 * (active) as work flows through. Props unchanged: { tasks, statusCounts, compact }.
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

const ORB = {
  idle: 'bg-white border border-trax9-border [&_svg]:stroke-[#c2c6d6]',
  active: 'bg-[#eef1ff] border-2 border-trax9-cyan shadow-[0_0_0_4px_rgba(5,195,222,0.15)] [&_svg]:stroke-trax9-cyan',
  done: 'bg-gradient-to-br from-[#6d28d9] to-trax9-gold [&_svg]:stroke-white',
  error: 'bg-[#fdecec] border-2 border-trax9-red [&_svg]:stroke-trax9-red',
};
const NODE = {
  idle: 'border-trax9-border',
  active: 'border-trax9-cyan shadow-[0_6px_20px_-6px_rgba(5,195,222,0.4)]',
  done: 'border-[#d9d2f5]',
  error: 'border-trax9-red/50',
};
const NODE_LABEL = {
  idle: 'text-trax9-muted',
  active: 'text-[#0891a8]',
  done: 'text-trax9-gold',
  error: 'text-trax9-red',
};

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
  const runningTask = activeIdx >= 0
    ? (stageTasks[STAGES[activeIdx].key] || []).find((t) => t.status === 'running')
    : null;

  const orbCls = compact ? 'h-9 w-9' : 'h-11 w-11';
  const iconSize = compact ? 16 : 20;
  const nodeW = compact ? 'w-[74px]' : 'w-[104px]';

  return (
    <div className="machine-canvas relative overflow-hidden rounded-xl border border-trax9-border p-4 sm:p-5">
      {/* status line */}
      <div className="relative mb-4 flex items-center gap-2">
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
              <span className="text-[#0891a8]">{activeStage.label.toUpperCase()} AGENT</span>
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

      {/* the wired node rail */}
      <div className="relative overflow-x-auto px-1 pb-2 pt-3">
        <div className={['flex items-stretch', compact ? 'min-w-[640px]' : 'min-w-[820px]'].join(' ')}>
          {STAGES.map((stage, i) => {
            const state = states[i];
            const Icon = stage.icon;
            const count = counts[stage.key];

            let wire = 'bg-[#dcdff0]';
            let flowing = false;
            if (i > 0) {
              if (state === 'active') {
                wire = 'bg-trax9-cyan';
                flowing = true;
              } else if (state === 'done' && states[i - 1] === 'done') {
                wire = 'bg-gradient-to-r from-[#6d28d9] to-trax9-gold';
              }
            }

            return (
              <Fragment key={stage.key}>
                {i > 0 && (
                  <div className="relative flex-1 min-w-[16px]" aria-hidden="true">
                    <div className={['absolute left-0 right-0 top-[26px] h-[3px] rounded', wire].join(' ')} />
                    {flowing && (
                      <div className="absolute left-0 right-0 top-[26px] h-[3px] overflow-hidden rounded">
                        <span className="absolute h-1 w-1 -top-[2px] rounded-full bg-white shadow-[0_0_6px_2px_rgba(5,195,222,0.9)] motion-safe:animate-travel" style={{ '--travel': '100%' }} />
                      </div>
                    )}
                  </div>
                )}
                <div
                  className={[
                    'relative z-10 flex shrink-0 flex-col items-center rounded-2xl border bg-white px-1.5 pb-2.5 pt-3 shadow-sm transition-all',
                    nodeW,
                    NODE[state],
                  ].join(' ')}
                >
                  {count !== null && count > 0 && (
                    <span className="mono-readout absolute -right-1.5 -top-2 rounded-full bg-trax9-gold px-1.5 text-[10px] font-bold leading-4 text-white">
                      {count}
                    </span>
                  )}
                  <div
                    className={['grid place-items-center rounded-full', orbCls, ORB[state]].join(' ')}
                  >
                    {state === 'active' && (
                      <span className="absolute h-11 w-11 rounded-full border-2 border-trax9-cyan/40 motion-safe:animate-ping" aria-hidden="true" />
                    )}
                    <Icon size={iconSize} strokeWidth={1.9} className="relative" />
                  </div>
                  <div
                    className={[
                      'mt-2 text-center font-semibold uppercase tracking-wider',
                      compact ? 'text-[8.5px]' : 'text-[10px]',
                      NODE_LABEL[state],
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
