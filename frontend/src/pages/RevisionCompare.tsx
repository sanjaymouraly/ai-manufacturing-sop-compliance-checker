import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from '../lib/api';
import { useAsync } from '../lib/useAsync';
import { Spinner } from '../components/Spinner';
import { ErrorBanner } from '../components/ErrorBanner';
import { EmptyState } from '../components/EmptyState';
import { Badge } from '../components/Badge';
import { humanize, SEVERITY_CLASSES, STATUS_CLASSES } from '../lib/format';
import { PageShell } from '../components/PageShell';

interface Delta {
  change_type: string;
  left_key?: string | null;
  right_key?: string | null;
  left_text?: string | null;
  right_text?: string | null;
  left_step?: string | null;
  right_step?: string | null;
  similarity?: number | null;
  severity_hint?: string | null;
  notes?: string[];
  parameter_change?: {
    parameter?: string | null;
    before?: string | null;
    after?: string | null;
    unit_before?: string | null;
    unit_after?: string | null;
  } | null;
}

const CHANGE_STYLES: Record<string, { badge: string; row: string; label: string }> = {
  added: {
    badge: 'bg-emerald-100 text-emerald-800 ring-emerald-600/20 dark:bg-emerald-950 dark:text-emerald-200 dark:ring-emerald-400/30',
    row: 'border-l-4 border-l-emerald-500',
    label: 'Added',
  },
  removed: {
    badge: 'bg-red-100 text-red-800 ring-red-600/20 dark:bg-red-950 dark:text-red-200 dark:ring-red-400/30',
    row: 'border-l-4 border-l-red-500',
    label: 'Removed',
  },
  parameter_changed: {
    badge: 'bg-orange-100 text-orange-800 ring-orange-600/20 dark:bg-orange-950 dark:text-orange-200 dark:ring-orange-400/30',
    row: 'border-l-4 border-l-orange-500',
    label: 'Limit changed',
  },
  modified: {
    badge: 'bg-amber-100 text-amber-900 ring-amber-600/20 dark:bg-amber-950 dark:text-amber-200 dark:ring-amber-400/30',
    row: 'border-l-4 border-l-amber-500',
    label: 'Modified',
  },
  reordered: {
    badge: 'bg-sky-100 text-sky-800 ring-sky-600/20 dark:bg-sky-950 dark:text-sky-200 dark:ring-sky-400/30',
    row: 'border-l-4 border-l-sky-500',
    label: 'Reordered',
  },
  unchanged: {
    badge: 'bg-slate-100 text-slate-600 ring-slate-500/20 dark:bg-slate-800 dark:text-slate-300 dark:ring-slate-400/30',
    row: 'border-l-4 border-l-slate-200 dark:border-l-slate-700',
    label: 'Unchanged',
  },
};

export default function RevisionCompare() {
  const { documentId } = useParams();
  const docId = Number(documentId);
  const { data: doc, error, loading } = useAsync(() => api.document(docId), [docId]);

  const [left, setLeft] = useState<number | null>(null);
  const [right, setRight] = useState<number | null>(null);
  const [showUnchanged, setShowUnchanged] = useState(false);

  useEffect(() => {
    if (!doc || doc.revisions.length < 2 || left !== null) return;
    // Default to the two most recent revisions, oldest on the left.
    const sorted = [...doc.revisions].sort((a, b) => a.revision_code.localeCompare(b.revision_code, undefined, { numeric: true }));
    setLeft(sorted[sorted.length - 2].id);
    setRight(sorted[sorted.length - 1].id);
  }, [doc, left]);

  const {
    data: diff,
    error: diffError,
    loading: diffLoading,
  } = useAsync(
    () => (left && right && left !== right ? api.diff(left, right) : Promise.resolve(null)),
    [left, right],
  );

  if (loading || error || !doc) {
    return (
      <PageShell
        title="Compare revisions"
        description="What changed between two revisions of the same SOP."
        loading={loading}
        error={error}
        loadingLabel="Loading document"
      >
        {null}
      </PageShell>
    );
  }

  if (doc.revisions.length < 2) {
    return (
      <PageShell title="Compare revisions" description={`${doc.sop_code} - ${doc.title}`}>
      <EmptyState
        title="Only one revision on file"
        description={`${doc.sop_code} has a single revision, so there is nothing to compare yet.`}
        action={
          <Link to={`/sops/${doc.id}`} className="btn-secondary mt-3">
            Back to document
          </Link>
        }
      />
      </PageShell>
    );
  }

  const deltas = (diff?.deltas ?? []) as unknown as Delta[];
  const visible = showUnchanged ? deltas : deltas.filter((d) => d.change_type !== 'unchanged');

  return (
    <div className="space-y-5">
      <nav className="text-sm text-slate-500 dark:text-slate-400" aria-label="Breadcrumb">
        <Link to="/sops" className="hover:underline">SOP Library</Link>
        <span aria-hidden="true"> / </span>
        <Link to={`/sops/${doc.id}`} className="hover:underline">{doc.sop_code}</Link>
        <span aria-hidden="true"> / </span>
        <span className="text-slate-900 dark:text-slate-100">Compare</span>
      </nav>

      <div>
        <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">Compare revisions</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Requirement-level differences between two revisions of {doc.sop_code}. Pairing is by content
          similarity, so renumbered steps are still matched to each other.
        </p>
      </div>

      <div className="card flex flex-wrap items-end gap-3 p-4">
        <div className="min-w-[180px] flex-1">
          <label className="label" htmlFor="left-rev">Baseline (older)</label>
          <select
            id="left-rev"
            className="input"
            value={left ?? ''}
            onChange={(e) => setLeft(Number(e.target.value))}
          >
            {doc.revisions.map((rev) => (
              <option key={rev.id} value={rev.id}>
                Rev {rev.revision_code} — {humanize(rev.status)}
              </option>
            ))}
          </select>
        </div>
        <span aria-hidden="true" className="pb-2.5 text-lg text-slate-400">→</span>
        <div className="min-w-[180px] flex-1">
          <label className="label" htmlFor="right-rev">Comparison (newer)</label>
          <select
            id="right-rev"
            className="input"
            value={right ?? ''}
            onChange={(e) => setRight(Number(e.target.value))}
          >
            {doc.revisions.map((rev) => (
              <option key={rev.id} value={rev.id}>
                Rev {rev.revision_code} — {humanize(rev.status)}
              </option>
            ))}
          </select>
        </div>
        <label className="flex items-center gap-2 pb-2.5 text-sm text-slate-700 dark:text-slate-300">
          <input
            type="checkbox"
            checked={showUnchanged}
            onChange={(e) => setShowUnchanged(e.target.checked)}
            className="h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500"
          />
          Show unchanged
        </label>
      </div>

      {left === right && (
        <p className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-100">
          Pick two different revisions to see a diff.
        </p>
      )}

      {diffLoading && <Spinner label="Computing diff" />}
      {Boolean(diffError) && <ErrorBanner error={diffError} />}

      {diff && (
        <>
          <section className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6" aria-label="Diff summary">
            {Object.entries(diff.summary).map(([key, value]) => (
              <div key={key} className="card p-3">
                <p className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
                  {humanize(key)}
                </p>
                <p className="mt-1 text-xl font-bold tabular-nums text-slate-900 dark:text-slate-100">{value}</p>
              </div>
            ))}
          </section>

          <div className="flex flex-wrap items-center gap-3 text-sm">
            <Badge className={STATUS_CLASSES.superseded}>{diff.left_label}</Badge>
            <span aria-hidden="true" className="text-slate-400">compared to</span>
            <Badge className={STATUS_CLASSES.approved}>{diff.right_label}</Badge>
          </div>

          {visible.length === 0 ? (
            <EmptyState
              title="No differences to show"
              description="These two revisions carry identical requirements, or all differences are hidden by the filter."
            />
          ) : (
            <ul className="space-y-3">
              {visible.map((delta, index) => {
                const style = CHANGE_STYLES[delta.change_type] ?? CHANGE_STYLES.modified;
                return (
                  <li key={`${delta.change_type}-${index}`} className={`card overflow-hidden ${style.row}`}>
                    <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-4 py-2.5 dark:border-slate-800">
                      <Badge className={style.badge}>{style.label}</Badge>
                      {delta.severity_hint && (
                        <Badge className={SEVERITY_CLASSES[delta.severity_hint as 'minor'] ?? ''}>
                          {humanize(delta.severity_hint)} impact
                        </Badge>
                      )}
                      <code className="text-xs text-slate-500 dark:text-slate-400">
                        {delta.left_key ?? '—'} → {delta.right_key ?? '—'}
                      </code>
                      {typeof delta.similarity === 'number' && delta.similarity > 0 && (
                        <span className="ml-auto text-xs text-slate-400 dark:text-slate-500">
                          {(delta.similarity * 100).toFixed(0)}% similar
                        </span>
                      )}
                    </div>

                    <div className="grid gap-px bg-slate-100 sm:grid-cols-2 dark:bg-slate-800">
                      <div className="bg-white p-4 dark:bg-slate-900">
                        <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">
                          {diff.left_label}
                          {delta.left_step && ` · step ${delta.left_step}`}
                        </p>
                        <p className="text-sm text-slate-700 dark:text-slate-300">
                          {delta.left_text || <em className="text-slate-400">not present</em>}
                        </p>
                      </div>
                      <div className="bg-white p-4 dark:bg-slate-900">
                        <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">
                          {diff.right_label}
                          {delta.right_step && ` · step ${delta.right_step}`}
                        </p>
                        <p className="text-sm text-slate-700 dark:text-slate-300">
                          {delta.right_text || <em className="text-slate-400">not present</em>}
                        </p>
                      </div>
                    </div>

                    {delta.parameter_change && (
                      <div className="border-t border-slate-100 bg-orange-50 px-4 py-2.5 text-sm dark:border-slate-800 dark:bg-orange-950/40">
                        <span className="font-medium text-orange-900 dark:text-orange-200">
                          {delta.parameter_change.parameter}:
                        </span>{' '}
                        <code className="text-orange-900 dark:text-orange-200">
                          {delta.parameter_change.before || 'none'}
                        </code>{' '}
                        <span aria-hidden="true">→</span>{' '}
                        <code className="font-semibold text-orange-900 dark:text-orange-200">
                          {delta.parameter_change.after || 'none'}
                        </code>
                      </div>
                    )}

                    {delta.notes && delta.notes.length > 0 && (
                      <ul className="border-t border-slate-100 px-4 py-2.5 text-xs text-slate-600 dark:border-slate-800 dark:text-slate-400">
                        {delta.notes.map((note) => (
                          <li key={note}>• {note}</li>
                        ))}
                      </ul>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
