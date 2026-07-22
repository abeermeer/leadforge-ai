import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  ArrowLeft,
  Radar,
  ScanSearch,
  PenLine,
  Zap,
  Loader2,
  Search,
  ArrowUpDown,
  Inbox,
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
} from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';
import api from '../api/client';
import PipelineRail from '../components/PipelineRail';
import ActivityFeed from '../components/ActivityFeed';
import ProgressBar from '../components/ProgressBar';
import StatusBadge from '../components/StatusBadge';
import ScoreBadge from '../components/ScoreBadge';
import EmptyState from '../components/EmptyState';
import { toDate } from '../utils/datetime';

const PAGE_SIZE = 25;

const LEAD_STATUSES = [
  'discovered',
  'finding_email',
  'auditing',
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
  'bounced',
  'unsubscribed',
  'failed',
];

function safeDate(value) {
  if (!value) return null;
  const d = toDate(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

function hostOf(url) {
  if (!url) return '';
  try {
    return new URL(/^https?:\/\//i.test(url) ? url : `https://${url}`).hostname.replace(
      /^www\./,
      ''
    );
  } catch {
    return url;
  }
}

/** Email confidence dot: green >= 80%, gold >= 50%, red below, dim when no email. */
function confidenceDot(lead) {
  if (!lead.email) return 'bg-trax9-border';
  let c = Number(lead.email_confidence);
  if (Number.isNaN(c)) return 'bg-trax9-muted';
  if (c > 1) c /= 100;
  if (c >= 0.8) return 'bg-trax9-green';
  if (c >= 0.5) return 'bg-trax9-gold';
  return 'bg-trax9-red';
}

export default function CampaignDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const aliveRef = useRef(true);

  const [campaign, setCampaign] = useState(null);
  const [tasks, setTasks] = useState([]);
  const [allLeads, setAllLeads] = useState([]); // page_size 200 snapshot for the rail
  const [table, setTable] = useState({ total: 0, items: [] });

  const [statusFilter, setStatusFilter] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [sortDir, setSortDir] = useState(null); // null | 'desc' | 'asc' on fit_score

  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);
  const [error, setError] = useState('');
  const [actionBusy, setActionBusy] = useState(''); // 'discover'|'audit'|'write'|'autopilot'|'audit-sel'|'write-sel'
  const [selected, setSelected] = useState(new Set()); // selected lead ids

  // ---- data fetchers -------------------------------------------------------

  const fetchCore = useCallback(async () => {
    try {
      const [cRes, tRes, lRes] = await Promise.all([
        api.get(`/campaigns/${id}`),
        api.get(`/campaigns/${id}/tasks`),
        api.get(`/campaigns/${id}/leads`, { params: { page: 1, page_size: 200 } }),
      ]);
      if (!aliveRef.current) return;
      setCampaign(cRes.data);
      setTasks(Array.isArray(tRes.data) ? tRes.data : []);
      setAllLeads(lRes.data.items || []);
      setError('');
    } catch (err) {
      if (!aliveRef.current) return;
      if (err.response && err.response.status === 404) setNotFound(true);
      else setError('Could not load campaign telemetry.');
    } finally {
      if (aliveRef.current) setLoading(false);
    }
  }, [id]);

  const fetchTable = useCallback(async () => {
    try {
      const params = { page, page_size: PAGE_SIZE };
      if (statusFilter) params.status = statusFilter;
      if (search) params.search = search;
      const { data } = await api.get(`/campaigns/${id}/leads`, { params });
      if (aliveRef.current) {
        setTable({ total: Number(data.total) || 0, items: data.items || [] });
      }
    } catch {
      /* keep last table state on transient errors */
    }
  }, [id, page, statusFilter, search]);

  useEffect(() => {
    aliveRef.current = true;
    setLoading(true);
    setNotFound(false);
    fetchCore();
    return () => {
      aliveRef.current = false;
    };
  }, [fetchCore]);

  useEffect(() => {
    fetchTable();
  }, [fetchTable]);

  // Debounce the search box (300ms) into the actual query param
  useEffect(() => {
    const timer = setTimeout(() => {
      setSearch(searchInput.trim());
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [searchInput]);

  const anyRunning = tasks.some((t) => t.status === 'running');

  // Live polling: tasks + leads every 3s while any agent is running
  useEffect(() => {
    if (!anyRunning) return undefined;
    const timer = setInterval(() => {
      fetchCore();
      fetchTable();
    }, 3000);
    return () => clearInterval(timer);
  }, [anyRunning, fetchCore, fetchTable]);

  // ---- derived state -------------------------------------------------------

  const statusCounts = useMemo(() => {
    const counts = {};
    allLeads.forEach((l) => {
      if (!l.status) return;
      counts[l.status] = (counts[l.status] || 0) + 1;
    });
    return counts;
  }, [allLeads]);

  const runningTasks = tasks.filter((t) => t.status === 'running');

  const typeBusy = useCallback(
    (frag) =>
      tasks.some(
        (t) =>
          String(t.type || '')
            .toLowerCase()
            .includes(frag) &&
          (t.status === 'running' || t.status === 'pending')
      ),
    [tasks]
  );

  const discoverBusy = actionBusy === 'discover' || typeBusy('discover');
  const auditBusy = actionBusy === 'audit' || typeBusy('audit');
  const writeBusy = actionBusy === 'write' || typeBusy('write');

  const rows = useMemo(() => {
    const items = [...table.items];
    if (sortDir) {
      items.sort((a, b) => {
        const av = a.fit_score === null || a.fit_score === undefined ? -1 : Number(a.fit_score);
        const bv = b.fit_score === null || b.fit_score === undefined ? -1 : Number(b.fit_score);
        return sortDir === 'asc' ? av - bv : bv - av;
      });
    }
    return items;
  }, [table.items, sortDir]);

  const totalPages = Math.max(1, Math.ceil(table.total / PAGE_SIZE));

  // ---- actions -------------------------------------------------------------

  const trigger = async (kind, body) => {
    setActionBusy(kind);
    setError('');
    try {
      await api.post(`/campaigns/${id}/${kind}`, body);
      if (aliveRef.current) setSelected(new Set());
      await fetchCore();
    } catch (err) {
      const detail =
        err.response && err.response.data && err.response.data.detail
          ? String(err.response.data.detail)
          : `Could not launch ${kind} agent.`;
      if (aliveRef.current) setError(detail);
    } finally {
      if (aliveRef.current) setActionBusy('');
    }
  };

  // ---- lead selection (checkboxes -> run audit/write on a subset) ----------
  const toggleOne = (leadId) =>
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(leadId) ? next.delete(leadId) : next.add(leadId);
      return next;
    });
  const toggleAll = () =>
    setSelected((prev) => (prev.size === rows.length ? new Set() : new Set(rows.map((r) => r.id))));
  const selectedIds = () => Array.from(selected);

  // ---- render --------------------------------------------------------------

  if (notFound) {
    return (
      <div className="space-y-4 motion-safe:animate-fade-up">
        <EmptyState
          icon={AlertTriangle}
          title="Campaign not found"
          hint="It may have been deleted, or the link is stale."
        />
        <div className="flex justify-center">
          <Link to="/campaigns" className="btn-ghost">
            <ArrowLeft size={16} />
            Back to campaigns
          </Link>
        </div>
      </div>
    );
  }

  if (loading || !campaign) {
    return (
      <div className="flex min-h-[50vh] items-center justify-center">
        <span className="mono-readout text-xs tracking-[0.2em] text-trax9-muted">
          ESTABLISHING UPLINK&hellip;
        </span>
      </div>
    );
  }

  const created = safeDate(campaign.created_at);

  return (
    <div className="space-y-6 motion-safe:animate-fade-up">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <Link
            to="/campaigns"
            className="rounded-md border border-trax9-border p-1.5 text-trax9-muted transition-colors hover:border-trax9-gold/50 hover:text-trax9-gold"
            aria-label="Back to campaigns"
          >
            <ArrowLeft size={16} />
          </Link>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2.5">
              <h1 className="truncate text-xl font-semibold text-trax9-text">
                {campaign.name}
              </h1>
              <StatusBadge status={campaign.status} />
            </div>
            <p className="mono-readout mt-0.5 text-xs text-trax9-muted">
              {created
                ? `Deployed ${formatDistanceToNow(created, { addSuffix: true })}`
                : 'Deployment time unknown'}
            </p>
          </div>
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

      {/* THE PIPELINE — full size */}
      <div className="node-card p-4 sm:p-5">
        <div className="mb-2 flex items-center gap-1.5">
          <span className="live-dot" aria-hidden="true" />
          <span className="label-caps">The Machine &mdash; Agent Pipeline</span>
        </div>
        <PipelineRail tasks={tasks} statusCounts={statusCounts} />
      </div>

      {/* Agent triggers + live progress */}
      <div className="panel space-y-4 p-4">
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            className="btn-gold"
            onClick={() => trigger('autopilot')}
            disabled={!!actionBusy}
            title="Discover → audit → score → enrich → write, all in one run"
          >
            {actionBusy === 'autopilot' ? (
              <Loader2 size={16} className="motion-safe:animate-spin" />
            ) : (
              <Zap size={16} />
            )}
            {actionBusy === 'autopilot' ? 'Running Pipeline…' : 'Run Full Pipeline'}
          </button>
          <span className="mx-1 hidden text-xs text-trax9-muted sm:inline">or run a stage:</span>
          <button
            type="button"
            className="btn-ghost"
            onClick={() => trigger('discover')}
            disabled={discoverBusy}
          >
            {discoverBusy ? (
              <Loader2 size={16} className="motion-safe:animate-spin" />
            ) : (
              <Radar size={16} />
            )}
            {discoverBusy ? 'Discovery Running…' : 'Deploy Discovery'}
          </button>
          <button
            type="button"
            className="btn-ghost"
            onClick={() => trigger('audit')}
            disabled={auditBusy}
          >
            {auditBusy ? (
              <Loader2 size={16} className="motion-safe:animate-spin" />
            ) : (
              <ScanSearch size={16} />
            )}
            {auditBusy ? 'Audit Running…' : 'Launch Audit'}
          </button>
          <button
            type="button"
            className="btn-ghost"
            onClick={() => trigger('write')}
            disabled={writeBusy}
            title="Draft outreach emails for every scored lead"
          >
            {writeBusy ? (
              <Loader2 size={16} className="motion-safe:animate-spin" />
            ) : (
              <PenLine size={16} />
            )}
            {writeBusy ? 'Writing…' : 'Write Emails'}
          </button>
          <span className="mono-readout ml-auto text-xs text-trax9-muted">
            {allLeads.length > 0
              ? `${table.total || allLeads.length} leads tracked`
              : 'No leads yet — deploy Discovery'}
          </span>
        </div>

        {runningTasks.length > 0 && (
          <div className="space-y-3 border-t border-trax9-border/60 pt-4">
            {runningTasks.map((t) => (
              <ProgressBar key={t.id} task={t} />
            ))}
          </div>
        )}
      </div>

      {/* Leads table + Activity feed */}
      <div className="grid gap-4 xl:grid-cols-3">
        <div className="panel overflow-hidden xl:col-span-2">
          <div className="flex flex-wrap items-center gap-3 border-b border-trax9-border px-4 py-3">
            <span className="label-caps">Leads</span>
            <div className="relative ml-auto">
              <Search
                size={14}
                className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-trax9-muted/60"
              />
              <input
                type="text"
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                placeholder="Search company or email…"
                className="input-dark w-52 py-1.5 pl-8 text-xs"
                aria-label="Search leads"
              />
            </div>
            <select
              value={statusFilter}
              onChange={(e) => {
                setStatusFilter(e.target.value);
                setPage(1);
              }}
              className="input-dark w-auto py-1.5 text-xs"
              aria-label="Filter by status"
            >
              <option value="">All statuses</option>
              {LEAD_STATUSES.map((s) => (
                <option key={s} value={s}>
                  {s.replace(/_/g, ' ')}
                </option>
              ))}
            </select>
          </div>

          {rows.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={Inbox}
                title="No leads on this frequency"
                hint={
                  statusFilter || search
                    ? 'No leads match the current filters.'
                    : 'Deploy Discovery to start pulling in targets.'
                }
              />
            </div>
          ) : (
            <>
              {selected.size > 0 && (
                <div className="flex flex-wrap items-center gap-3 border-b border-trax9-border/60 bg-trax9-gold/[0.06] px-4 py-2.5">
                  <span className="mono-readout text-xs font-semibold text-trax9-text">
                    {selected.size} selected
                  </span>
                  <button
                    type="button"
                    className="btn-gold px-3 py-1.5 text-xs"
                    disabled={!!actionBusy}
                    onClick={() => trigger('audit', { lead_ids: selectedIds() })}
                  >
                    {actionBusy === 'audit' ? (
                      <Loader2 size={13} className="motion-safe:animate-spin" />
                    ) : (
                      <ScanSearch size={13} />
                    )}
                    Audit selected
                  </button>
                  <button
                    type="button"
                    className="btn-ghost px-3 py-1.5 text-xs"
                    disabled={!!actionBusy}
                    onClick={() => trigger('write', { lead_ids: selectedIds() })}
                  >
                    {actionBusy === 'write' ? (
                      <Loader2 size={13} className="motion-safe:animate-spin" />
                    ) : (
                      <PenLine size={13} />
                    )}
                    Write selected
                  </button>
                  <button
                    type="button"
                    className="ml-auto text-xs text-trax9-muted hover:text-trax9-text"
                    onClick={() => setSelected(new Set())}
                  >
                    Clear
                  </button>
                </div>
              )}
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead>
                    <tr className="border-b border-trax9-border/60">
                      <th className="w-9 px-4 py-2.5">
                        <input
                          type="checkbox"
                          aria-label="Select all leads"
                          checked={rows.length > 0 && selected.size === rows.length}
                          onChange={toggleAll}
                          className="h-4 w-4 accent-trax9-gold"
                        />
                      </th>
                      <th className="label-caps px-4 py-2.5 font-semibold">Company</th>
                      <th className="label-caps hidden px-4 py-2.5 font-semibold lg:table-cell">
                        Website
                      </th>
                      <th className="label-caps px-4 py-2.5 font-semibold">Email</th>
                      <th className="label-caps hidden px-4 py-2.5 font-semibold md:table-cell">
                        City
                      </th>
                      <th className="label-caps px-4 py-2.5 font-semibold">Status</th>
                      <th className="px-4 py-2.5">
                        <button
                          type="button"
                          onClick={() =>
                            setSortDir((d) => (d === 'desc' ? 'asc' : 'desc'))
                          }
                          className="label-caps flex items-center gap-1 font-semibold transition-colors hover:text-trax9-gold"
                          title="Sort by fit score"
                        >
                          Score
                          <ArrowUpDown
                            size={12}
                            className={sortDir ? 'text-trax9-gold' : ''}
                          />
                        </button>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((lead) => (
                      <tr
                        key={lead.id}
                        onClick={() => navigate(`/leads/${lead.id}`)}
                        className={[
                          'cursor-pointer border-b border-trax9-border/40 transition-colors last:border-b-0 hover:bg-trax9-border/20',
                          selected.has(lead.id) ? 'bg-trax9-gold/[0.05]' : '',
                        ].join(' ')}
                      >
                        <td className="px-4 py-2.5" onClick={(e) => e.stopPropagation()}>
                          <input
                            type="checkbox"
                            aria-label={`Select ${lead.company_name}`}
                            checked={selected.has(lead.id)}
                            onChange={() => toggleOne(lead.id)}
                            className="h-4 w-4 accent-trax9-gold"
                          />
                        </td>
                        <td className="max-w-[200px] truncate px-4 py-2.5 font-medium text-trax9-text">
                          {lead.company_name || '—'}
                        </td>
                        <td className="mono-readout hidden max-w-[160px] truncate px-4 py-2.5 text-xs text-trax9-muted lg:table-cell">
                          {hostOf(lead.website) || '—'}
                        </td>
                        <td className="px-4 py-2.5">
                          <span className="flex items-center gap-1.5">
                            <span
                              className={[
                                'h-1.5 w-1.5 shrink-0 rounded-full',
                                confidenceDot(lead),
                              ].join(' ')}
                              aria-hidden="true"
                              title={
                                lead.email
                                  ? `Confidence: ${lead.email_confidence ?? '?'}`
                                  : 'No email found yet'
                              }
                            />
                            <span className="mono-readout max-w-[180px] truncate text-xs text-trax9-muted">
                              {lead.email || '—'}
                            </span>
                          </span>
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
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="flex items-center justify-between border-t border-trax9-border px-4 py-3">
                <span className="mono-readout text-xs text-trax9-muted">
                  Page {page} of {totalPages} &middot; {table.total} leads
                </span>
                <div className="flex gap-2">
                  <button
                    type="button"
                    className="btn-ghost px-2.5 py-1.5"
                    onClick={() => setPage((p) => Math.max(1, p - 1))}
                    disabled={page <= 1}
                    aria-label="Previous page"
                  >
                    <ChevronLeft size={16} />
                  </button>
                  <button
                    type="button"
                    className="btn-ghost px-2.5 py-1.5"
                    onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                    disabled={page >= totalPages}
                    aria-label="Next page"
                  >
                    <ChevronRight size={16} />
                  </button>
                </div>
              </div>
            </>
          )}
        </div>

        <ActivityFeed tasks={tasks} />
      </div>
    </div>
  );
}