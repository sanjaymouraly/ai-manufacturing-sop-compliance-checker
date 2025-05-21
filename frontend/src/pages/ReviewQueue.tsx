import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../lib/api';
import type { FindingOut } from '../lib/api';
import { useAsync } from '../lib/useAsync';
import { useAuth } from '../lib/auth';
import { Spinner } from '../components/Spinner';
import { ErrorBanner } from '../components/ErrorBanner';
import { EmptyState } from '../components/EmptyState';
import { Disclaimer } from '../components/Disclaimer';
import { FindingCard } from '../components/FindingCard';
import { Modal } from '../components/Modal';
import { useToast } from '../components/Toast';
import { humanize } from '../lib/format';

interface QueueSummary {
  findings_by_review_status: Record<string, number>;
  open_findings_by_severity: Record<string, number>;
  audits_awaiting_review: number;
  audits_blocked_on_authority: number;
}

export default function ReviewQueue() {
  const { can } = useAuth();
  const { push } = useToast();

  const [severity, setSeverity] = useState('');
  const [reviewStatus, setReviewStatus] = useState('open');
  const [classification, setClassification] = useState('');
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');

  const [target, setTarget] = useState<FindingOut | null>(null);
  const [action, setAction] = useState('accept');
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [dialogError, setDialogError] = useState<unknown>(null);

  const { data: summary } = useAsync(
    () => api.queueSummary() as Promise<unknown> as Promise<QueueSummary>,
    [],
  );

  const { data, error, loading, reload } = useAsync(
    () =>
      api.findings({
        review_status: reviewStatus || undefined,
        severity: severity || undefined,
        classification: classification || undefined,
        search: query || undefined,
        page_size: 50,
      }),
    [reviewStatus, severity, classification, query],
  );

  async function submit() {
    if (!target) return;
    setBusy(true);
    setDialogError(null);
    try {
      await api.reviewFinding(target.id, action, reason);
      push('success', `Decision recorded on ${target.finding_code}.`);
      setTarget(null);
      setReason('');
      reload();
    } catch (err) {
      setDialogError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">Review &amp; escalation queue</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Findings waiting for a human decision. Nothing here is a conclusion — each item is evidence put in
          front of you so you can decide.
        </p>
      </div>

      <Disclaimer />

      {!can('reviewer') && (
        <aside className="rounded-lg border border-sky-300 bg-sky-50 px-4 py-3 text-sm text-sky-900 dark:border-sky-800 dark:bg-sky-950/60 dark:text-sky-100">
          <strong className="font-semibold">You have read-only access to this queue. </strong>
          Separation of duties means the auditor who ran an analysis cannot sign off their own findings —
          only a reviewer or administrator can record a decision here.
        </aside>
      )}

      {summary && (
        <section className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6" aria-label="Queue summary">
          {[
            { label: 'Open findings', value: summary.findings_by_review_status.open ?? 0, tone: 'text-amber-600 dark:text-amber-400' },
            { label: 'Critical open', value: summary.open_findings_by_severity.critical ?? 0, tone: 'text-red-600 dark:text-red-400' },
            { label: 'Major open', value: summary.open_findings_by_severity.major ?? 0, tone: 'text-orange-600 dark:text-orange-400' },
            { label: 'Analyses awaiting review', value: summary.audits_awaiting_review, tone: 'text-slate-900 dark:text-slate-100' },
            { label: 'Blocked on authority', value: summary.audits_blocked_on_authority, tone: 'text-fuchsia-600 dark:text-fuchsia-400' },
            { label: 'Accepted', value: summary.findings_by_review_status.accepted ?? 0, tone: 'text-emerald-600 dark:text-emerald-400' },
          ].map((stat) => (
            <div key={stat.label} className="card p-3">
              <p className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
                {stat.label}
              </p>
              <p className={`mt-1 text-xl font-bold tabular-nums ${stat.tone}`}>{stat.value}</p>
            </div>
          ))}
        </section>
      )}

      <form
        className="card flex flex-wrap items-end gap-3 p-4"
        onSubmit={(e) => {
          e.preventDefault();
          setQuery(search.trim());
        }}
      >
        <div className="min-w-[150px]">
          <label className="label" htmlFor="q-status">Review status</label>
          <select id="q-status" className="input" value={reviewStatus} onChange={(e) => setReviewStatus(e.target.value)}>
            <option value="">Any</option>
            {['open', 'in_review', 'accepted', 'rejected', 'info_requested', 'resolved'].map((value) => (
              <option key={value} value={value}>{humanize(value)}</option>
            ))}
          </select>
        </div>
        <div className="min-w-[130px]">
          <label className="label" htmlFor="q-severity">Severity</label>
          <select id="q-severity" className="input" value={severity} onChange={(e) => setSeverity(e.target.value)}>
            <option value="">Any</option>
            {['critical', 'major', 'minor', 'info'].map((value) => (
              <option key={value} value={value}>{humanize(value)}</option>
            ))}
          </select>
        </div>
        <div className="min-w-[180px]">
          <label className="label" htmlFor="q-class">Classification</label>
          <select id="q-class" className="input" value={classification} onChange={(e) => setClassification(e.target.value)}>
            <option value="">Any</option>
            {['confirmed_deviation', 'possible_deviation', 'insufficient_evidence', 'sop_conflict', 'no_detected_deviation'].map((value) => (
              <option key={value} value={value}>{humanize(value)}</option>
            ))}
          </select>
        </div>
        <div className="min-w-[200px] flex-1">
          <label className="label" htmlFor="q-search">Search</label>
          <input
            id="q-search"
            className="input"
            placeholder="Title, explanation or requirement key…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <button type="submit" className="btn-secondary">Apply</button>
      </form>

      {loading && <Spinner label="Loading queue" />}
      {Boolean(error) && <ErrorBanner error={error} onRetry={reload} />}

      {data && data.items.length === 0 && (
        <EmptyState
          title="Queue is clear"
          description="No findings match these filters. Try widening the review status or severity."
        />
      )}

      {data && data.items.length > 0 && (
        <>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Showing {data.items.length} of {data.total}, highest severity first.
          </p>
          <div className="space-y-3">
            {data.items.map((finding) => (
              <FindingCard
                key={finding.id}
                finding={finding}
                actions={
                  <>
                    <Link to={`/audits/${finding.audit_id}`} className="btn-secondary">
                      Open full analysis
                    </Link>
                    {can('reviewer') ? (
                      <button
                        type="button"
                        className="btn-primary"
                        onClick={() => {
                          setTarget(finding);
                          setAction('accept');
                          setReason('');
                        }}
                      >
                        Record decision
                      </button>
                    ) : (
                      <span className="self-center text-xs text-slate-500 dark:text-slate-400">
                        Reviewers and administrators record decisions — an auditor cannot sign off their own analysis.
                      </span>
                    )}
                  </>
                }
              />
            ))}
          </div>
        </>
      )}

      <Modal
        open={target !== null}
        onClose={() => setTarget(null)}
        title={target ? `Record a decision on ${target.finding_code}` : ''}
        footer={
          <>
            <button type="button" className="btn-secondary" onClick={() => setTarget(null)}>
              Cancel
            </button>
            <button
              type="button"
              className="btn-primary"
              onClick={submit}
              disabled={busy || reason.trim().length < 10}
            >
              {busy ? 'Saving…' : 'Record decision'}
            </button>
          </>
        }
      >
        {target && (
          <>
            <p className="rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-700 dark:bg-slate-800/60 dark:text-slate-300">
              {target.title}
            </p>
            <div>
              <label className="label" htmlFor="decision">Decision</label>
              <select id="decision" className="input" value={action} onChange={(e) => setAction(e.target.value)}>
                <option value="accept">Accept — the finding stands</option>
                <option value="reject">Reject — not a real deviation</option>
                <option value="request_info">Request more information</option>
                <option value="resolve">Resolve — action completed</option>
                <option value="reopen">Reopen</option>
              </select>
            </div>
            <div>
              <label className="label" htmlFor="decision-reason">
                Rationale (required, at least 10 characters)
              </label>
              <textarea
                id="decision-reason"
                className="input min-h-[110px]"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="What did you check, and what is the basis for this decision?"
              />
              <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                Recorded permanently in the append-only audit trail with your name.
              </p>
            </div>
            {Boolean(dialogError) && <ErrorBanner error={dialogError} />}
          </>
        )}
      </Modal>
    </div>
  );
}
