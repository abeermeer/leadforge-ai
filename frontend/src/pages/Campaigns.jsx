import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Plus, X, Rocket, Loader2, ChevronLeft, ChevronRight } from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';
import api from '../api/client';
import StatusBadge from '../components/StatusBadge';
import EmptyState from '../components/EmptyState';
import { toDate } from '../utils/datetime';

const PAGE_SIZE = 20;

function toList(value) {
  if (Array.isArray(value)) return value;
  if (typeof value === 'string' && value.trim()) {
    return value
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean);
  }
  return [];
}

function safeDate(value) {
  if (!value) return null;
  const d = toDate(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** Chip row with a "+n" overflow indicator. */
function ChipList({ items, tone = 'gold' }) {
  const list = toList(items);
  if (list.length === 0) return <span className="text-trax9-muted/60">—</span>;
  const shown = list.slice(0, 3);
  const extra = list.length - shown.length;
  const toneCls =
    tone === 'cyan'
      ? 'border-trax9-cyan/30 bg-trax9-cyan/10 text-trax9-cyan'
      : 'border-trax9-gold/30 bg-trax9-gold/10 text-trax9-gold';
  return (
    <span className="flex flex-wrap items-center gap-1">
      {shown.map((item) => (
        <span
          key={item}
          className={['rounded-full border px-2 py-0.5 text-[11px] font-medium', toneCls].join(
            ' '
          )}
        >
          {item}
        </span>
      ))}
      {extra > 0 && (
        <span className="rounded-full border border-trax9-border px-2 py-0.5 text-[11px] text-trax9-muted">
          +{extra}
        </span>
      )}
    </span>
  );
}

/** Tag input: Enter adds a tag, X removes one. */
function TagInput({ id, label, tags, onChange, placeholder, helper }) {
  const [draft, setDraft] = useState('');

  const addDraft = () => {
    const value = draft.trim();
    if (value && !tags.includes(value)) onChange([...tags, value]);
    setDraft('');
  };

  const onKeyDown = (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      addDraft();
    } else if (e.key === 'Backspace' && draft === '' && tags.length > 0) {
      onChange(tags.slice(0, -1));
    }
  };

  return (
    <div>
      <label htmlFor={id} className="label-caps mb-1.5 block">
        {label}
      </label>
      <div className="flex flex-wrap items-center gap-1.5 rounded-lg border border-trax9-border bg-trax9-bg/60 px-2 py-1.5 transition-colors focus-within:border-trax9-cyan/60">
        {tags.map((tag) => (
          <span
            key={tag}
            className="flex items-center gap-1 rounded-full border border-trax9-gold/30 bg-trax9-gold/10 px-2 py-0.5 text-xs font-medium text-trax9-gold"
          >
            {tag}
            <button
              type="button"
              onClick={() => onChange(tags.filter((t) => t !== tag))}
              className="rounded-full text-trax9-gold/70 transition-colors hover:text-trax9-red"
              aria-label={`Remove ${tag}`}
            >
              <X size={12} />
            </button>
          </span>
        ))}
        <input
          id={id}
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          onBlur={addDraft}
          placeholder={tags.length === 0 ? placeholder : ''}
          className="min-w-[120px] flex-1 bg-transparent px-1 py-0.5 text-sm text-trax9-text placeholder-trax9-muted/60 outline-none"
        />
      </div>
      {helper && <p className="mt-1.5 text-xs text-trax9-muted/80">{helper}</p>}
    </div>
  );
}

/** New Campaign modal — posts and hands the created campaign back up. */
function NewCampaignModal({ onClose, onCreated }) {
  const [name, setName] = useState('');
  const [keywords, setKeywords] = useState([]);
  const [locations, setLocations] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const submit = async (e) => {
    e.preventDefault();
    if (!name.trim() || busy) return;
    setBusy(true);
    setError('');
    try {
      const payload = { name: name.trim() };
      if (keywords.length > 0) payload.seed_keywords = keywords;
      if (locations.length > 0) payload.target_locations = locations;
      const { data } = await api.post('/campaigns', payload);
      onCreated(data);
    } catch (err) {
      const detail =
        err.response && err.response.data && err.response.data.detail
          ? String(err.response.data.detail)
          : 'Launch failed — check the API and try again.';
      setError(detail);
      setBusy(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      role="dialog"
      aria-modal="true"
      aria-label="New campaign"
    >
      <div className="node-card w-full max-w-lg p-6 motion-safe:animate-fade-up">
        <div className="mb-5 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-trax9-text">Deploy New Campaign</h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1 text-trax9-muted transition-colors hover:text-trax9-text"
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>

        <form onSubmit={submit} className="space-y-4">
          <div>
            <label htmlFor="campaign-name" className="label-caps mb-1.5 block">
              Campaign Name
            </label>
            <input
              id="campaign-name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Dentists — East Coast Q3"
              className="input-dark"
              autoFocus
              required
            />
          </div>

          <TagInput
            id="campaign-keywords"
            label="Seed Keywords"
            tags={keywords}
            onChange={setKeywords}
            placeholder="Type a keyword, press Enter"
            helper="Auto-filled from your Agency Brain when left empty"
          />

          <TagInput
            id="campaign-locations"
            label="Target Locations"
            tags={locations}
            onChange={setLocations}
            placeholder="Type a location, press Enter"
            helper="Auto-filled from your Agency Brain when left empty"
          />

          {error && (
            <div className="rounded-lg border border-trax9-red/40 bg-trax9-red/10 px-3 py-2 text-sm text-trax9-red">
              {error}
            </div>
          )}

          <div className="flex justify-end gap-3 pt-2">
            <button type="button" onClick={onClose} className="btn-ghost">
              Cancel
            </button>
            <button type="submit" className="btn-gold" disabled={busy || !name.trim()}>
              {busy ? (
                <Loader2 size={16} className="motion-safe:animate-spin" />
              ) : (
                <Rocket size={16} />
              )}
              {busy ? 'Deploying…' : 'Create Campaign'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default function Campaigns() {
  const navigate = useNavigate();
  const aliveRef = useRef(true);
  const [page, setPage] = useState(1);
  const [resp, setResp] = useState({ total: 0, items: [] });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [modalOpen, setModalOpen] = useState(false);

  const fetchCampaigns = useCallback(async () => {
    try {
      const { data } = await api.get('/campaigns', {
        params: { page, page_size: PAGE_SIZE },
      });
      if (aliveRef.current) {
        setResp({ total: Number(data.total) || 0, items: data.items || [] });
        setError('');
      }
    } catch {
      if (aliveRef.current) setError('Could not load campaigns.');
    } finally {
      if (aliveRef.current) setLoading(false);
    }
  }, [page]);

  useEffect(() => {
    aliveRef.current = true;
    setLoading(true);
    fetchCampaigns();
    return () => {
      aliveRef.current = false;
    };
  }, [fetchCampaigns]);

  const totalPages = Math.max(1, Math.ceil(resp.total / PAGE_SIZE));

  return (
    <div className="space-y-6 motion-safe:animate-fade-up">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="label-caps mb-1 flex items-center gap-1.5">
            <span className="live-dot" aria-hidden="true" />
            Deployments
          </div>
          <h1 className="text-xl font-semibold text-trax9-text">Campaigns</h1>
          <p className="mt-0.5 text-sm text-trax9-muted">
            {resp.total} mission{resp.total === 1 ? '' : 's'} on record
          </p>
        </div>
        <button type="button" onClick={() => setModalOpen(true)} className="btn-gold">
          <Plus size={16} />
          New Campaign
        </button>
      </div>

      {error && (
        <div className="rounded-lg border border-trax9-red/40 bg-trax9-red/10 px-4 py-3 text-sm text-trax9-red">
          {error}
        </div>
      )}

      {loading ? (
        <div className="flex min-h-[30vh] items-center justify-center">
          <span className="mono-readout text-xs tracking-[0.2em] text-trax9-muted">
            LOADING CAMPAIGNS&hellip;
          </span>
        </div>
      ) : resp.items.length === 0 ? (
        <EmptyState
          icon={Rocket}
          title="No campaigns yet"
          hint="Deploy a campaign and the agent pipeline will discover, audit and pitch leads for you."
        />
      ) : (
        <div className="panel overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-trax9-border/60">
                  <th className="label-caps px-4 py-3 font-semibold">Name</th>
                  <th className="label-caps px-4 py-3 font-semibold">Keywords</th>
                  <th className="label-caps hidden px-4 py-3 font-semibold md:table-cell">
                    Locations
                  </th>
                  <th className="label-caps px-4 py-3 font-semibold">Status</th>
                  <th className="label-caps hidden px-4 py-3 font-semibold lg:table-cell">
                    Created
                  </th>
                </tr>
              </thead>
              <tbody>
                {resp.items.map((c) => {
                  const created = safeDate(c.created_at);
                  return (
                    <tr
                      key={c.id}
                      onClick={() => navigate(`/campaigns/${c.id}`)}
                      className="cursor-pointer border-b border-trax9-border/40 transition-colors last:border-b-0 hover:bg-trax9-border/20"
                    >
                      <td className="max-w-[240px] truncate px-4 py-3 font-medium text-trax9-text">
                        {c.name}
                      </td>
                      <td className="px-4 py-3">
                        <ChipList items={c.seed_keywords} tone="gold" />
                      </td>
                      <td className="hidden px-4 py-3 md:table-cell">
                        <ChipList items={c.target_locations} tone="cyan" />
                      </td>
                      <td className="px-4 py-3">
                        <StatusBadge status={c.status} />
                      </td>
                      <td className="mono-readout hidden px-4 py-3 text-xs text-trax9-muted lg:table-cell">
                        {created ? formatDistanceToNow(created, { addSuffix: true }) : '—'}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {totalPages > 1 && (
            <div className="flex items-center justify-between border-t border-trax9-border px-4 py-3">
              <span className="mono-readout text-xs text-trax9-muted">
                Page {page} of {totalPages}
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
          )}
        </div>
      )}

      {modalOpen && (
        <NewCampaignModal
          onClose={() => setModalOpen(false)}
          onCreated={(campaign) => {
            setModalOpen(false);
            navigate(`/campaigns/${campaign.id}`);
          }}
        />
      )}
    </div>
  );
}