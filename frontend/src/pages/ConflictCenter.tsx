import { useState } from 'react';
import { api } from '../lib/api';
import type { ConflictOut } from '../lib/api';
import { useAsync } from '../lib/useAsync';
import { useAuth } from '../lib/auth';
import { Spinner } from '../components/Spinner';
import { ErrorBanner } from '../components/ErrorBanner';
import { EmptyState } from '../components/EmptyState';
import { Badge } from '../components/Badge';
import { Modal } from '../components/Modal';
import { useToast } from '../components/Toast';
import { formatDate, formatDay, humanize, SEVERITY_CLASSES, STATUS_CLASSES } from '../lib/format';

const CONFLICT_HELP: Record<string, string> = {
  overlapping_effective_dates:
    'Two approved revisions claim authority over the same period, so the engine cannot tell which one governed a run in that window.',
  unknown_approval:
    'A revision is marked approved but names no approver, so its authority cannot be established.',
  cross_document_contradiction:
    'Two different approved SOPs state incompatible limits for the same parameter. Following one necessarily breaks the other.',
  contradictory_requirements:
    'Two requirements inside the same revision contradict each other.',
  duplicate_revision_code:
    'Two revisions share a revision code, so references to that code are ambiguous.',
};

function EvidenceBlock({ label, evidence }: { label: string; evidence: Record<string, unknown> | null }) {
  if (!evidence) return null;

  const str = (key: string): string | null => {
    const v = evidence[key];
    if (v === null || v === undefined || v === '') return null;
    return String(v);
  };

  const quote = str('quote');
  const docLabel = str('document_label') ?? str('file_name') ?? label;
  const status = str('status');
  const effective = str('effective_date');
  const expiry = str('expiry_date');
  const section = str('section_path') ?? str('step_number');
  const page = str('page');
  const parameter = str('parameter');
  const note = str('note');
  const start = str('char_start');
  const end = str('char_end');

  // Provenance a reviewer actually needs. Raw database keys and null values are
  // deliberately not rendered - this panel is read by quality staff, not developers.
  const facts: { label: string; value: string }[] = [];
  if (status) facts.push({ label: 'Status', value: humanize(status) });
  if (effective) facts.push({ label: 'Effective', value: formatDay(effective) });
  if (expiry) facts.push({ label: 'Expires', value: formatDay(expiry) });
  if (parameter) facts.push({ label: 'Parameter', value: parameter });

  return (
    <div className="rounded-lg border border-slate-200 p-3 dark:border-slate-700">
      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
        {label}
      </p>
      <p className="mt-0.5 font-medium text-slate-900 dark:text-slate-100">{docLabel}</p>

      {quote ? (
        <figure className="mt-2">
          <blockquote className="border-l-4 border-slate-300 bg-slate-50 py-1.5 pl-3 pr-2 font-mono text-sm text-slate-800 dark:border-slate-600 dark:bg-slate-800/60 dark:text-slate-100">
            {quote}
          </blockquote>
          <figcaption className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-slate-500 dark:text-slate-400">
            {section && <span>&sect;{section}</span>}
            {page && <span>p.{page}</span>}
            {start && end && (
              <span className="font-mono">
                chars {start}&ndash;{end}
              </span>
            )}
          </figcaption>
        </figure>
      ) : (
        <p className="mt-2 text-sm italic text-slate-500 dark:text-slate-400">
          No quotable text was stored for this side.
        </p>
      )}

      {facts.length > 0 && (
        <dl className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs">
          {facts.map((f) => (
            <div key={f.label} className="flex gap-1.5">
              <dt className="text-slate-500 dark:text-slate-400">{f.label}:</dt>
              <dd className="font-medium text-slate-800 dark:text-slate-200">{f.value}</dd>
            </div>
          ))}
        </dl>
      )}

      {note && <p className="mt-2 text-xs text-slate-600 dark:text-slate-300">{note}</p>}
    </div>
  );
}

function ResolveDialog({
  conflict,
  onClose,
  onDone,
}: {
  conflict: ConflictOut | null;
  onClose: () => void;
  onDone: () => void;
}) {
  const { push } = useToast();
  const [choice, setChoice] = useState('revision_a');
  const [rationale, setRationale] = useState('');
  const [acceptRisk, setAcceptRisk] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  if (!conflict) return null;

  async function submit() {
    if (!conflict) return;
    setBusy(true);
    setError(null);
    try {
      await api.resolveConflict(conflict.id, choice, rationale, acceptRisk);
      push('success', `${conflict.conflict_code} resolved. The original revisions are preserved unchanged.`);
      onDone();
      onClose();
      setRationale('');
      setAcceptRisk(false);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title={`Resolve ${conflict.conflict_code}`}
      wide
      footer={
        <>
          <button type="button" className="btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn-primary"
            onClick={submit}
            disabled={busy || rationale.trim().length < 10}
          >
            {busy ? 'Recording…' : 'Record resolution'}
          </button>
        </>
      }
    >
      <p className="text-sm text-slate-600 dark:text-slate-400">{conflict.description}</p>

      <div>
        <span className="label">Which source should govern?</span>
        <div className="space-y-2">
          {[
            { value: 'revision_a', label: `Revision ${conflict.revision_a_code ?? 'A'} governs`, hint: 'Treat this revision as authoritative going forward.' },
            { value: 'revision_b', label: `Revision ${conflict.revision_b_code ?? 'B'} governs`, hint: 'Treat this revision as authoritative going forward.' },
            { value: 'escalate', label: 'Escalate — cannot be resolved here', hint: 'Keep the conflict open and route it to document control.' },
            { value: 'both_invalid', label: 'Neither is usable', hint: 'Both sources need to be withdrawn or reissued.' },
          ].map((option) => (
            <label
              key={option.value}
              className={`flex cursor-pointer items-start gap-3 rounded-lg border p-3 ${
                choice === option.value
                  ? 'border-brand-500 bg-brand-50 dark:bg-brand-950/40'
                  : 'border-slate-200 hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-slate-800/50'
              }`}
            >
              <input
                type="radio"
                name="choice"
                value={option.value}
                checked={choice === option.value}
                onChange={(e) => setChoice(e.target.value)}
                className="mt-0.5 h-4 w-4 text-brand-600 focus:ring-brand-500"
              />
              <span>
                <span className="block text-sm font-medium text-slate-900 dark:text-slate-100">{option.label}</span>
                <span className="block text-xs text-slate-500 dark:text-slate-400">{option.hint}</span>
              </span>
            </label>
          ))}
        </div>
      </div>

      <div>
        <label className="label" htmlFor="rationale">
          Documented rationale (required, at least 10 characters)
        </label>
        <textarea
          id="rationale"
          className="input min-h-[110px]"
          value={rationale}
          onChange={(e) => setRationale(e.target.value)}
          placeholder="Record what you checked, who you consulted, and the basis for this decision…"
        />
      </div>

      {conflict.blocks_conclusion && (
        <label className="flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm dark:border-amber-700 dark:bg-amber-950/60">
          <input
            type="checkbox"
            checked={acceptRisk}
            onChange={(e) => setAcceptRisk(e.target.checked)}
            className="mt-0.5 h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500"
          />
          <span className="text-amber-900 dark:text-amber-100">
            I understand this conflict currently blocks conclusions, and I am accepting responsibility for the
            residual risk of this decision.
          </span>
        </label>
      )}

      {Boolean(error) && <ErrorBanner error={error} />}
    </Modal>
  );
}

export default function ConflictCenter() {
  const { can } = useAuth();
  const [statusFilter, setStatusFilter] = useState('open,escalated');
  const [resolving, setResolving] = useState<ConflictOut | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);

  const { data, error, loading, reload } = useAsync(
    () => api.conflicts({ status: statusFilter === 'all' ? undefined : statusFilter, page_size: 100 }),
    [statusFilter],
  );

  const { data: historyData } = useAsync(
    () => (expanded ? api.conflictHistory(expanded) : Promise.resolve([])),
    [expanded],
  );

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">SOP Conflict Center</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Contradictions inside the SOP library itself. While a material conflict is open, every analysis of an
          affected SOP is escalated instead of concluded.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm text-slate-600 dark:text-slate-400">Show:</span>
        {[
          { value: 'open,escalated', label: 'Needs attention' },
          { value: 'escalated', label: 'Escalated' },
          { value: 'open', label: 'Open' },
          { value: 'resolved', label: 'Resolved' },
          { value: 'all', label: 'All' },
        ].map((option) => (
          <button
            key={option.value}
            type="button"
            onClick={() => setStatusFilter(option.value)}
            aria-pressed={statusFilter === option.value}
            className={statusFilter === option.value ? 'btn-primary' : 'btn-secondary'}
          >
            {option.label}
          </button>
        ))}
      </div>

      {loading && <Spinner label="Loading conflicts" />}
      {Boolean(error) && <ErrorBanner error={error} onRetry={reload} />}

      {data && data.items.length === 0 && (
        <EmptyState
          title={statusFilter.includes('open') ? 'No unresolved conflicts' : 'Nothing to show'}
          description={
            statusFilter.includes('open')
              ? 'The SOP library is internally consistent as far as the detectors can tell. New conflicts appear here automatically when documents are uploaded.'
              : 'Try a different filter.'
          }
        />
      )}

      {data && data.items.length > 0 && (
        <ul className="space-y-3">
          {data.items.map((conflict) => {
            const isOpen = expanded === conflict.id;
            return (
              <li key={conflict.id} className="card overflow-hidden">
                <button
                  type="button"
                  onClick={() => setExpanded(isOpen ? null : conflict.id)}
                  aria-expanded={isOpen}
                  className="flex w-full items-start gap-3 px-4 py-3 text-left hover:bg-slate-50 dark:hover:bg-slate-800/50"
                >
                  <span aria-hidden="true" className="mt-1 text-slate-400">{isOpen ? '▾' : '▸'}</span>
                  <span className="min-w-0 flex-1">
                    <span className="flex flex-wrap items-center gap-2">
                      <Badge className={SEVERITY_CLASSES[conflict.severity]}>{humanize(conflict.severity)}</Badge>
                      <Badge className={STATUS_CLASSES[conflict.status] ?? ''}>{humanize(conflict.status)}</Badge>
                      <Badge>{humanize(conflict.conflict_type)}</Badge>
                      <code className="text-xs text-slate-400 dark:text-slate-500">{conflict.conflict_code}</code>
                      {conflict.blocks_conclusion && conflict.status === 'open' && (
                        <Badge className="bg-red-100 text-red-800 ring-red-600/20 dark:bg-red-950 dark:text-red-200 dark:ring-red-400/30">
                          blocks conclusions
                        </Badge>
                      )}
                    </span>
                    <span className="mt-1.5 block text-sm font-semibold text-slate-900 dark:text-slate-100">
                      {conflict.title}
                    </span>
                    <span className="mt-0.5 block text-xs text-slate-500 dark:text-slate-400">
                      {conflict.sop_code ? `${conflict.sop_code} · ` : ''}detected {formatDate(conflict.created_at)}
                    </span>
                  </span>
                </button>

                {isOpen && (
                  <div className="space-y-4 border-t border-slate-100 px-4 py-4 dark:border-slate-800">
                    <p className="rounded-lg bg-slate-50 px-3 py-2 text-xs italic text-slate-600 dark:bg-slate-800/60 dark:text-slate-400">
                      {CONFLICT_HELP[conflict.conflict_type] ??
                        'The library contains two statements that cannot both be followed.'}
                    </p>

                    <p className="text-sm text-slate-700 dark:text-slate-300">{conflict.description}</p>

                    <div className="grid gap-3 sm:grid-cols-2">
                      <EvidenceBlock
                        label={`Source A${conflict.revision_a_code ? ` — rev ${conflict.revision_a_code}` : ''}`}
                        evidence={conflict.evidence_a}
                      />
                      <EvidenceBlock
                        label={`Source B${conflict.revision_b_code ? ` — rev ${conflict.revision_b_code}` : ''}`}
                        evidence={conflict.evidence_b}
                      />
                    </div>

                    {conflict.status === 'resolved' && (
                      <div className="rounded-lg border border-emerald-300 bg-emerald-50 px-3 py-2 text-sm dark:border-emerald-800 dark:bg-emerald-950/60">
                        <p className="font-medium text-emerald-900 dark:text-emerald-100">
                          Resolved as “{humanize(conflict.resolution_choice)}” by {conflict.resolved_by || 'unknown'} on{' '}
                          {formatDate(conflict.resolved_at)}
                        </p>
                        <p className="mt-1 text-emerald-800 dark:text-emerald-200">{conflict.resolution_rationale}</p>
                      </div>
                    )}

                    {historyData && historyData.length > 0 && (
                      <div>
                        <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                          Decision history
                        </p>
                        <ol className="space-y-2">
                          {historyData.map((action) => (
                            <li key={action.id} className="border-l-2 border-slate-200 pl-3 text-sm dark:border-slate-700">
                              <p className="font-medium text-slate-900 dark:text-slate-100">{humanize(action.action)}</p>
                              <p className="text-xs text-slate-500 dark:text-slate-400">
                                {action.actor_name || 'system'} · {formatDate(action.created_at)}
                              </p>
                              <p className="text-slate-700 dark:text-slate-300">{action.reason}</p>
                            </li>
                          ))}
                        </ol>
                      </div>
                    )}

                    {can('reviewer') && conflict.status !== 'resolved' && (
                      <button type="button" className="btn-primary" onClick={() => setResolving(conflict)}>
                        Resolve this conflict
                      </button>
                    )}
                    {!can('reviewer') && conflict.status !== 'resolved' && (
                      <p className="text-xs text-slate-500 dark:text-slate-400">
                        Only reviewers and administrators can resolve conflicts. The auditor who ran an analysis
                        cannot sign it off themselves.
                      </p>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}

      <ResolveDialog conflict={resolving} onClose={() => setResolving(null)} onDone={reload} />
    </div>
  );
}
