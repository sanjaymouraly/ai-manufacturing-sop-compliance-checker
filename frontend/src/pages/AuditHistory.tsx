import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../lib/api';
import { useAsync } from '../lib/useAsync';
import { Spinner } from '../components/Spinner';
import { ErrorBanner } from '../components/ErrorBanner';
import { EmptyState } from '../components/EmptyState';
import { Badge } from '../components/Badge';
import { formatDate, formatDuration, humanize, OUTCOME_CLASSES } from '../lib/format';

export default function AuditHistory() {
  const [outcome, setOutcome] = useState('');
  const [requiresReview, setRequiresReview] = useState(false);
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);

  const { data, error, loading, reload } = useAsync(
    () =>
      api.audits({
        outcome: outcome || undefined,
        requires_review: requiresReview ? 'true' : undefined,
        search: query || undefined,
        page,
        page_size: 25,
      }),
    [outcome, requiresReview, query, page],
  );

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">Audit history</h1>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
            Every analysis ever run, with its outcome and review state. Records are never deleted or rewritten.
          </p>
        </div>
        <Link to="/audits/new" className="btn-primary">New audit</Link>
      </div>

      <form
        className="card flex flex-wrap items-end gap-3 p-4"
        onSubmit={(e) => {
          e.preventDefault();
          setQuery(search.trim());
          setPage(1);
        }}
      >
        <div className="min-w-[200px]">
          <label className="label" htmlFor="h-outcome">Outcome</label>
          <select
            id="h-outcome"
            className="input"
            value={outcome}
            onChange={(e) => {
              setOutcome(e.target.value);
              setPage(1);
            }}
          >
            <option value="">Any outcome</option>
            {['no_detected_deviation', 'deviations_found', 'human_review_required', 'blocked_unresolved_authority'].map(
              (value) => (
                <option key={value} value={value}>{humanize(value)}</option>
              ),
            )}
          </select>
        </div>
        <div className="min-w-[200px] flex-1">
          <label className="label" htmlFor="h-search">Search</label>
          <input
            id="h-search"
            className="input"
            placeholder="Audit code, report code or SOP code…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <label className="flex items-center gap-2 pb-2.5 text-sm text-slate-700 dark:text-slate-300">
          <input
            type="checkbox"
            checked={requiresReview}
            onChange={(e) => {
              setRequiresReview(e.target.checked);
              setPage(1);
            }}
            className="h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500"
          />
          Only those needing review
        </label>
        <button type="submit" className="btn-secondary">Apply</button>
      </form>

      {loading && <Spinner label="Loading audits" />}
      {Boolean(error) && <ErrorBanner error={error} onRetry={reload} />}

      {data && data.items.length === 0 && (
        <EmptyState
          title="No analyses found"
          description="Run an analysis from the New Audit screen, or widen the filters."
          action={<Link to="/audits/new" className="btn-primary mt-3">Start a new audit</Link>}
        />
      )}

      {data && data.items.length > 0 && (
        <div className="card overflow-hidden">
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-slate-200 dark:divide-slate-800">
              <caption className="sr-only">Audit history with outcomes and review status</caption>
              <thead className="bg-slate-50 dark:bg-slate-900/60">
                <tr>
                  <th scope="col" className="th">Audit</th>
                  <th scope="col" className="th">Report</th>
                  <th scope="col" className="th">Governing SOP</th>
                  <th scope="col" className="th">Outcome</th>
                  <th scope="col" className="th">Findings</th>
                  <th scope="col" className="th">Review</th>
                  <th scope="col" className="th">Mode</th>
                  <th scope="col" className="th">Duration</th>
                  <th scope="col" className="th">Run at</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800/70">
                {data.items.map((audit) => (
                  <tr key={audit.id} className="hover:bg-slate-50 dark:hover:bg-slate-800/40">
                    <td className="td">
                      <Link
                        to={`/audits/${audit.id}`}
                        className="font-semibold text-brand-700 hover:underline dark:text-brand-300"
                      >
                        {audit.audit_code}
                      </Link>
                    </td>
                    <td className="td">{audit.report_code || '—'}</td>
                    <td className="td whitespace-nowrap">
                      {audit.sop_code ? `${audit.sop_code}${audit.revision_code ? ` rev ${audit.revision_code}` : ''}` : '—'}
                    </td>
                    <td className="td">
                      <Badge className={OUTCOME_CLASSES[audit.outcome] ?? ''}>{humanize(audit.outcome)}</Badge>
                    </td>
                    <td className="td tabular-nums">{audit.finding_count}</td>
                    <td className="td">
                      {audit.requires_human_review ? (
                        <Badge className="bg-amber-100 text-amber-900 ring-amber-600/20 dark:bg-amber-950 dark:text-amber-200 dark:ring-amber-400/30">
                          {humanize(audit.review_status)}
                        </Badge>
                      ) : (
                        <span className="text-xs text-slate-400 dark:text-slate-500">not required</span>
                      )}
                    </td>
                    <td className="td text-xs">{humanize(audit.mode)}</td>
                    <td className="td whitespace-nowrap text-xs tabular-nums">{formatDuration(audit.duration_ms)}</td>
                    <td className="td whitespace-nowrap text-xs">{formatDate(audit.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-200 px-3 py-2 dark:border-slate-800">
            <p className="text-xs text-slate-500 dark:text-slate-400">
              Page {data.page} of {data.pages} · {data.total} analyses
            </p>
            <div className="flex gap-2">
              <button
                type="button"
                className="btn-secondary"
                disabled={data.page <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
              >
                Previous
              </button>
              <button
                type="button"
                className="btn-secondary"
                disabled={data.page >= data.pages}
                onClick={() => setPage((p) => p + 1)}
              >
                Next
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
