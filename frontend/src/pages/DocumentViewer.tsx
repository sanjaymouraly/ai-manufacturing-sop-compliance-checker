import { useMemo, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { api } from '../lib/api';
import { useAsync } from '../lib/useAsync';
import { Spinner } from '../components/Spinner';
import { ErrorBanner } from '../components/ErrorBanner';
import { Badge } from '../components/Badge';
import { Highlight } from '../components/Highlight';
import type { HighlightSpan } from '../components/Highlight';
import { formatDay, humanize, STATUS_CLASSES } from '../lib/format';
import { PageShell } from '../components/PageShell';

export default function DocumentViewer() {
  const { documentId } = useParams();
  const [params, setParams] = useSearchParams();
  const docId = Number(documentId);

  const { data: doc, error, loading, reload } = useAsync(() => api.document(docId), [docId]);

  const revisionParam = params.get('revision');
  const selectedRevisionId = revisionParam ? Number(revisionParam) : doc?.revisions[0]?.id ?? null;

  const {
    data: revision,
    error: revError,
    loading: revLoading,
  } = useAsync(
    () => (selectedRevisionId ? api.revision(selectedRevisionId) : Promise.resolve(null)),
    [selectedRevisionId],
  );

  const [activeRequirement, setActiveRequirement] = useState<number | null>(null);
  const [showSections, setShowSections] = useState(true);

  const spans: HighlightSpan[] = useMemo(() => {
    if (!revision) return [];
    return revision.requirements.map((req) => ({
      start: req.char_start,
      end: req.char_end,
      id: String(req.id),
      tone: 'requirement' as const,
    }));
  }, [revision]);

  if (loading || error || !doc) {
    return (
      <PageShell
        title="SOP document"
        description="Stored revisions, extracted structure and source text."
        loading={loading}
        error={error}
        onRetry={reload}
        loadingLabel="Loading document"
      >
        {null}
      </PageShell>
    );
  }

  return (
    <div className="space-y-5">
      <nav className="text-sm text-slate-500 dark:text-slate-400" aria-label="Breadcrumb">
        <Link to="/sops" className="hover:underline">SOP Library</Link>
        <span aria-hidden="true"> / </span>
        <span className="text-slate-900 dark:text-slate-100">{doc.sop_code}</span>
      </nav>

      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">{doc.sop_code}</h1>
          <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">{doc.title}</p>
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-500">
            Owner: {doc.owner || 'unspecified'} · Process area: {doc.process_area ? humanize(doc.process_area) : 'unspecified'}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {doc.revisions.length > 1 && (
            <Link to={`/sops/${doc.id}/compare`} className="btn-secondary">
              Compare revisions
            </Link>
          )}
          {doc.has_open_conflicts && (
            <Link to="/conflicts" className="btn-secondary">
              View open conflicts
            </Link>
          )}
        </div>
      </div>

      {doc.has_open_conflicts && (
        <div
          role="alert"
          className="rounded-lg border border-fuchsia-300 bg-fuchsia-50 px-4 py-3 text-sm text-fuchsia-900 dark:border-fuchsia-800 dark:bg-fuchsia-950 dark:text-fuchsia-100"
        >
          <strong className="font-semibold">This SOP has an unresolved conflict.</strong> Analyses of runs governed
          by this SOP will be escalated for human review until the conflict is resolved in the Conflict Center.
        </div>
      )}

      <section aria-label="Revisions" className="card overflow-hidden">
        <h2 className="border-b border-slate-200 px-4 py-3 text-sm font-semibold text-slate-900 dark:border-slate-800 dark:text-slate-100">
          Revision history ({doc.revisions.length})
        </h2>
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-slate-200 dark:divide-slate-800">
            <thead className="bg-slate-50 dark:bg-slate-900/60">
              <tr>
                <th scope="col" className="th">Revision</th>
                <th scope="col" className="th">Status</th>
                <th scope="col" className="th">Effective</th>
                <th scope="col" className="th">Expires</th>
                <th scope="col" className="th">Approver</th>
                <th scope="col" className="th">Requirements</th>
                <th scope="col" className="th">Extraction</th>
                <th scope="col" className="th">Chain</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800/70">
              {doc.revisions.map((rev) => {
                const selected = rev.id === selectedRevisionId;
                return (
                  <tr
                    key={rev.id}
                    className={selected ? 'bg-brand-50 dark:bg-brand-950/40' : 'hover:bg-slate-50 dark:hover:bg-slate-800/40'}
                  >
                    <td className="td">
                      <button
                        type="button"
                        onClick={() => setParams({ revision: String(rev.id) })}
                        className="font-semibold text-brand-700 hover:underline dark:text-brand-300"
                        aria-current={selected ? 'true' : undefined}
                      >
                        Rev {rev.revision_code}
                      </button>
                    </td>
                    <td className="td">
                      <Badge className={STATUS_CLASSES[rev.status] ?? ''}>{humanize(rev.status)}</Badge>
                    </td>
                    <td className="td whitespace-nowrap text-xs">{formatDay(rev.effective_date)}</td>
                    <td className="td whitespace-nowrap text-xs">{formatDay(rev.expiry_date)}</td>
                    <td className="td text-xs">
                      {rev.approved_by || <span className="text-amber-600 dark:text-amber-400">unknown</span>}
                    </td>
                    <td className="td tabular-nums">{rev.requirement_count}</td>
                    <td className="td text-xs">
                      {rev.ocr_used && <Badge className={STATUS_CLASSES.conflicting}>OCR</Badge>}{' '}
                      <span
                        className={
                          rev.extraction_confidence < 0.95
                            ? 'font-medium text-amber-600 dark:text-amber-400'
                            : 'text-slate-500 dark:text-slate-400'
                        }
                      >
                        {(rev.extraction_confidence * 100).toFixed(0)}%
                      </span>
                    </td>
                    <td className="td text-xs text-slate-500 dark:text-slate-400">
                      {rev.supersedes_revision_code && <span className="block">supersedes {rev.supersedes_revision_code}</span>}
                      {rev.superseded_by_revision_code && <span className="block">superseded by {rev.superseded_by_revision_code}</span>}
                      {!rev.supersedes_revision_code && !rev.superseded_by_revision_code && '—'}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      {revLoading && <Spinner label="Loading revision text" />}
      {Boolean(revError) && <ErrorBanner error={revError} />}

      {revision && (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
          <section className="card flex min-h-0 flex-col overflow-hidden">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-4 py-3 dark:border-slate-800">
              <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                Rev {revision.revision_code} — source text
              </h2>
              <div className="flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
                <span className="inline-flex items-center gap-1.5">
                  <span aria-hidden="true" className="h-3 w-3 rounded-sm bg-brand-200 dark:bg-brand-500/40" />
                  extracted requirement
                </span>
                <span className="font-mono">sha256:{revision.file_hash.slice(0, 12)}…</span>
              </div>
            </div>
            {revision.extraction_warnings.length > 0 && (
              <ul className="border-b border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-950/60 dark:text-amber-100">
                {revision.extraction_warnings.map((warning) => (
                  <li key={warning}>⚠ {warning}</li>
                ))}
              </ul>
            )}
            <div className="max-h-[60vh] overflow-y-auto p-4">
              <Highlight text={revision.raw_text} spans={spans} activeId={activeRequirement ? String(activeRequirement) : null} />
            </div>
          </section>

          <div className="space-y-4">
            <section className="card overflow-hidden">
              <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3 dark:border-slate-800">
                <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                  Extracted requirements ({revision.requirements.length})
                </h2>
              </div>
              {revision.requirements.length === 0 ? (
                <p className="px-4 py-6 text-sm text-slate-500 dark:text-slate-400">
                  No requirements could be extracted from this revision. If the file is a scanned image, extraction
                  confidence will be low and every analysis against it is escalated rather than concluded.
                </p>
              ) : (
                <ul className="max-h-[32vh] divide-y divide-slate-100 overflow-y-auto dark:divide-slate-800/70">
                  {revision.requirements.map((req) => (
                    <li key={req.id}>
                      <button
                        type="button"
                        onClick={() => setActiveRequirement(req.id)}
                        className={`w-full px-4 py-3 text-left transition-colors hover:bg-slate-50 dark:hover:bg-slate-800/50 ${
                          activeRequirement === req.id ? 'bg-brand-50 dark:bg-brand-950/40' : ''
                        }`}
                      >
                        <span className="flex items-center gap-2">
                          <code className="rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                            {req.req_key}
                          </code>
                          {req.step_number && <Badge>Step {req.step_number}</Badge>}
                          {req.requires_record && <Badge title="A record or signature is required">record</Badge>}
                          {req.condition_text && <Badge title={req.condition_text}>conditional</Badge>}
                        </span>
                        <span className="mt-1.5 block text-sm text-slate-700 dark:text-slate-300">{req.text}</span>
                        {req.parameter_name && (
                          <span className="mt-1 block text-xs font-medium text-brand-700 dark:text-brand-300">
                            {req.parameter_name}
                            {req.comparator ? ` ${req.comparator}` : ''}{' '}
                            {req.comparator === 'between'
                              ? `${req.value_min}–${req.value_max}`
                              : (req.value_min ?? req.value_max ?? '')}
                            {req.tolerance ? ` ±${req.tolerance}` : ''} {req.unit ?? ''}
                          </span>
                        )}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className="card overflow-hidden">
              <button
                type="button"
                onClick={() => setShowSections((v) => !v)}
                className="flex w-full items-center justify-between border-b border-slate-200 px-4 py-3 text-sm font-semibold text-slate-900 dark:border-slate-800 dark:text-slate-100"
                aria-expanded={showSections}
              >
                <span>Document structure ({revision.sections.length})</span>
                <span aria-hidden="true">{showSections ? '▾' : '▸'}</span>
              </button>
              {showSections && (
                <ul className="max-h-64 overflow-y-auto px-4 py-2 text-sm">
                  {revision.sections.map((section) => (
                    <li
                      key={section.id}
                      className="py-1 text-slate-700 dark:text-slate-300"
                      style={{ paddingLeft: `${Math.max(0, section.level - 1) * 12}px` }}
                    >
                      <span className="text-slate-400 dark:text-slate-500">{section.section_path}</span> {section.heading}
                      {section.page !== null && (
                        <span className="ml-1 text-xs text-slate-400 dark:text-slate-500">p.{section.page}</span>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>
        </div>
      )}
    </div>
  );
}
