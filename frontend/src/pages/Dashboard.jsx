import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { Users, ScanSearch, PenLine, Send, Radar, Plus } from 'lucide-react';
import { format, formatDistanceToNow, startOfDay, subDays } from 'date-fns';
import api from '../api/client';
import PipelineRail from '../components/PipelineRail';
import DeliverabilityPanel from '../components/DeliverabilityPanel';
import ActivityFeed from '../components/ActivityFeed';
import StatCard from '../components/StatCard';
import StatusBadge from '../components/StatusBadge';
import ScoreBadge from '../components/ScoreBadge';
import EmptyState from '../components/EmptyState';
import { toDate } from '../utils/datetime';

const AUDITED_PLUS = [
  'audited',
  'scored',
  'enriching',
  'enriched',
  'writing',
  'written',
  'queued',
  'sending',
  'sent',
  'opened',
  'replied',
];
const WRITTEN_PLUS = ['written', 'queued', 'sending', 'sent', 'opened', 'replied'];
const SENT_PLUS = ['sent', 'opened', 'replied'];

function sumStatuses(counts, statuses) {
  return statuses.reduce((sum, s) => sum + (counts[s] || 0), 0);
}

function safeDate(value) {
  if (!value) return null;
  const d = toDate(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

export default function Dashboard() {
  const navigate = useNavigate();
  const aliveRef = useRef(true);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [data, setData] = useState({ campaigns: [], leads: [], tasks: [], totalLeads: 0 });

  const fetchAll = useCallback(async () => {
    try {
      const res = await api.get('/campaigns', { params: { page: 1, page_size: 50 } });
      const campaigns = res.data.items || [];

      const perCampaign = await Promise.all(
        campaigns.map((c) =>
          Promise.all([
            api.get(`/campaigns/${c.id}/leads`, { params: { page: 1, page_size: 100 } }),
            api.get(`/campaigns/${c.id}/tasks`),
          ]).catch(() => [{ data: { items: [], total: 0 } }, { data: [] }])
        )
      );

      let leads = [];
      let tasks = [];
      let totalLeads = 0;
      perCampaign.forEach(([leadRes, taskRes], i) => {
        const items = (leadRes.data.items || []).map((l) => ({
          ...l,
          campaign_name: campaigns[i].name,
        }));
        leads = leads.concat(items);
        totalLeads += Number(leadRes.data.total) || items.length;
        tasks = tasks.concat(Array.isArray(taskRes.data) ? taskRes.data : []);
      });

      if (aliveRef.current) {
        setData({ campaigns, leads, tasks, totalLeads });
        setError('');
      }
    } catch {
      if (aliveRef.current) setError('Could not reach mission control API.');
    } finally {
      if (aliveRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    aliveRef.current = true;
    fetchAll();
    return () => {
      aliveRef.current = false;
    };
  }, [fetchAll]);

  const anyRunning = data.tasks.some((t) => t.status === 'running');

  // Poll every 5s while any agent task is running
  useEffect(() => {
    if (!anyRunning) return undefined;
    const timer = setInterval(fetchAll, 5000);
    return () => clearInterval(timer);
  }, [anyRunning, fetchAll]);

  const statusCounts = useMemo(() => {
    const counts = {};
    data.leads.forEach((l) => {
      if (!l.status) return;
      counts[l.status] = (counts[l.status] || 0) + 1;
    });
    return counts;
  }, [data.leads]);

  const audited = sumStatuses(statusCounts, AUDITED_PLUS);
  const written = sumStatuses(statusCounts, WRITTEN_PLUS);
  const sent = sumStatuses(statusCounts, SENT_PLUS);
  const replied = statusCounts.replied || 0;

  // Leads created per day, last 30 days — computed client-side from created_at
  const chartData = useMemo(() => {
    const days = [];
    const byKey = {};
    const today = startOfDay(new Date());
    for (let i = 29; i >= 0; i -= 1) {
      const d = subDays(today, i);
      const entry = { key: format(d, 'yyyy-MM-dd'), label: format(d, 'MMM d'), leads: 0 };
      days.push(entry);
      byKey[entry.key] = entry;
    }
    data.leads.forEach((l) => {
      const d = safeDate(l.created_at);
      if (!d) return;
      const entry = byKey[format(d, 'yyyy-MM-dd')];
      if (entry) entry.leads += 1;
    });
    return days;
  }, [data.leads]);

  const recentLeads = useMemo(() => {
    return [...data.leads]
      .sort((a, b) => {
        const ad = safeDate(a.created_at);
        const bd = safeDate(b.created_at);
        return (bd ? bd.getTime() : 0) - (ad ? ad.getTime() : 0);
      })
      .slice(0, 8);
  }, [data.leads]);

  if (loading) {
    return (
      <div className="flex min-h-[50vh] items-center justify-center">
        <span className="mono-readout text-xs tracking-[0.2em] text-trax9-muted">
          SYNCING MISSION DATA&hellip;
        </span>
      </div>
    );
  }

  return (
    <div className="space-y-6 motion-safe:animate-fade-up">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="label-caps mb-1 flex items-center gap-1.5">
            <span className="live-dot" aria-hidden="true" />
            The Machine
          </div>
          <h1 className="text-xl font-semibold text-trax9-text">Mission Overview</h1>
          <p className="mt-0.5 text-sm text-trax9-muted">
            {data.campaigns.length} campaign{data.campaigns.length === 1 ? '' : 's'} under
            command
          </p>
        </div>
        {anyRunning && (
          <span className="flex items-center gap-2 rounded-full border border-trax9-cyan/40 bg-trax9-cyan/10 px-3 py-1 text-xs font-semibold uppercase tracking-widest text-trax9-cyan">
            <span
              className="h-1.5 w-1.5 rounded-full bg-trax9-cyan motion-safe:animate-pulse"
              aria-hidden="true"
            />
            Agents Active
          </span>
        )}
      </div>

      {error && (
        <div className="rounded-lg border border-trax9-red/40 bg-trax9-red/10 px-4 py-3 text-sm text-trax9-red">
          {error}
        </div>
      )}

      {data.campaigns.length === 0 ? (
        <div className="space-y-4">
          <EmptyState
            icon={Radar}
            title="No campaigns deployed"
            hint="Deploy your first campaign to put the agent pipeline to work."
          />
          <div className="flex justify-center">
            <Link to="/campaigns" className="btn-gold">
              <Plus size={16} />
              Deploy First Campaign
            </Link>
          </div>
        </div>
      ) : (
        <>
          {/* Stat cards */}
          <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
            <StatCard label="Total Leads" value={data.totalLeads} icon={Users} accent="gold" />
            <StatCard label="Audited" value={audited} icon={ScanSearch} accent="cyan" />
            <StatCard label="Emails Written" value={written} icon={PenLine} accent="violet" />
            <StatCard
              label="Sent / Replied"
              value={`${sent} / ${replied}`}
              icon={Send}
              accent="green"
            />
          </div>

          {/* The Pipeline — aggregated across all campaigns */}
          <div className="node-card p-4 sm:p-5">
            <div className="mb-2 flex items-center justify-between">
              <span className="label-caps">Agent Pipeline &mdash; All Campaigns</span>
            </div>
            <PipelineRail compact tasks={data.tasks} statusCounts={statusCounts} />
          </div>

          {/* Deliverability — the numbers that decide whether sending survives */}
          <DeliverabilityPanel />

          {/* Chart + Activity feed */}
          <div className="grid gap-4 lg:grid-cols-3">
            <div className="panel p-4 lg:col-span-2">
              <div className="mb-3 flex items-center justify-between">
                <span className="label-caps">Leads Discovered &mdash; Last 30 Days</span>
              </div>
              <ResponsiveContainer width="100%" height={240}>
                <AreaChart data={chartData} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
                  <defs>
                    <linearGradient id="trax9GoldFill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#4914c4" stopOpacity={0.32} />
                      <stop offset="100%" stopColor="#4914c4" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke="#e2e7f4" strokeDasharray="3 3" vertical={false} />
                  <XAxis
                    dataKey="label"
                    tick={{ fill: '#8b94ab', fontSize: 11 }}
                    tickLine={false}
                    axisLine={{ stroke: '#e2e7f4' }}
                    interval="preserveStartEnd"
                    minTickGap={28}
                  />
                  <YAxis
                    allowDecimals={false}
                    tick={{ fill: '#8b94ab', fontSize: 11 }}
                    tickLine={false}
                    axisLine={false}
                    width={40}
                  />
                  <Tooltip
                    contentStyle={{
                      background: '#ffffff',
                      border: '1px solid #e2e7f4',
                      borderRadius: 8,
                      fontSize: 12,
                    }}
                    labelStyle={{ color: '#8b94ab' }}
                    itemStyle={{ color: '#4914c4' }}
                    cursor={{ stroke: '#38bdf8', strokeOpacity: 0.35 }}
                  />
                  <Area
                    type="monotone"
                    dataKey="leads"
                    name="Leads"
                    stroke="#4914c4"
                    strokeWidth={2}
                    fill="url(#trax9GoldFill)"
                    activeDot={{ r: 4, fill: '#4914c4', stroke: '#0a0f1e' }}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>

            <ActivityFeed tasks={data.tasks} />
          </div>

          {/* Recent leads mini-table */}
          <div className="panel overflow-hidden">
            <div className="flex items-center justify-between border-b border-trax9-border px-4 py-3">
              <span className="label-caps">Latest Intel &mdash; Recent Leads</span>
              <Link
                to="/campaigns"
                className="text-xs font-medium text-trax9-gold hover:underline"
              >
                All campaigns &rarr;
              </Link>
            </div>
            {recentLeads.length === 0 ? (
              <div className="px-4 py-8 text-center text-sm text-trax9-muted">
                No leads captured yet &mdash; deploy Discovery on a campaign.
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead>
                    <tr className="border-b border-trax9-border/60">
                      <th className="label-caps px-4 py-2.5 font-semibold">Company</th>
                      <th className="label-caps px-4 py-2.5 font-semibold">Campaign</th>
                      <th className="label-caps hidden px-4 py-2.5 font-semibold md:table-cell">
                        City
                      </th>
                      <th className="label-caps px-4 py-2.5 font-semibold">Status</th>
                      <th className="label-caps px-4 py-2.5 font-semibold">Score</th>
                      <th className="label-caps hidden px-4 py-2.5 font-semibold lg:table-cell">
                        Added
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {recentLeads.map((lead) => {
                      const created = safeDate(lead.created_at);
                      return (
                        <tr
                          key={lead.id}
                          onClick={() => navigate(`/leads/${lead.id}`)}
                          className="cursor-pointer border-b border-trax9-border/40 transition-colors last:border-b-0 hover:bg-trax9-border/20"
                        >
                          <td className="max-w-[220px] truncate px-4 py-2.5 font-medium text-trax9-text">
                            {lead.company_name || '—'}
                          </td>
                          <td className="max-w-[180px] truncate px-4 py-2.5 text-trax9-muted">
                            {lead.campaign_name || '—'}
                          </td>
                          <td className="hidden px-4 py-2.5 text-trax9-muted md:table-cell">
                            {lead.city || '—'}
                          </td>
                          <td className="px-4 py-2.5">
                            <StatusBadge status={lead.status} />
                          </td>
                          <td className="px-4 py-2.5">
                            <ScoreBadge score={lead.fit_score} />
                          </td>
                          <td className="mono-readout hidden px-4 py-2.5 text-xs text-trax9-muted lg:table-cell">
                            {created
                              ? formatDistanceToNow(created, { addSuffix: true })
                              : '—'}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}