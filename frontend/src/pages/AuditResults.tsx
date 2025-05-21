import { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api, downloadExport } from '../lib/api';
import type { CitationOut } from '../lib/api';
import { useAsync } from '../lib/useAsync';
import { useAuth } from '../lib/auth';
import { Spinner } from '../components/Spinner';
import { EmptyState } from '../components/EmptyState';
import { Badge } from '../components/Badge';
import { Disclaimer } from '../components/Disclaimer';
import { FindingCard } from '../components/FindingCard';
import { Highlight } from '../components/Highlight';
import { Modal } from '../components/Modal';
import { useToast } from '../components/Toast';
import { formatDate, formatDuration, humanize, OUTCOME_CLASSES, STATUS_CLASSES } from '../lib/format';
import { PageShell } from '../components/PageShell';

const OUTCOME_HELP: Record<string, string> = {
  no_detected_deviation:
    'No departure from the governing SOP was detected. This is not a statement that the run was compliant — only that this analysis found nothing.',
  deviations_found:
    'At least one departure from the governing SOP was detected with supporting evidence. A qualified reviewer must decide what it means.',
  human_review_required:
    'The analysis could not be settled automatically. A human reviewer must examine the evidence before any conclusion is drawn.',
  blocked_unresolved_authority:
    'The engine could not determine which SOP revision governed this run, so no comparison result can be trusted. Resolve the authority question first.',
};

function SourcePanel({
  title,
  text,
  citation,
}: {
  title: string;
  text: string;
  citation: CitationOut | null;
}) {
  const spans = citation
    ? [{ start: citation.char_start, end: citation.char_end, id: String(citation.id), tone: 'evidence' as const }]
    : [];
  return (
    <div>
      <p className="mb-2 text-sm font-semibold text-slate-900 dark:text-slate-100">{title}</p>
      <div className="max-h-[55vh] overflow-y-auto rounded-lg border border-slate-200 p-3 dark:border-slate-700">
        <Highlight text={text} spans={spans} activeId={citation ? String(citation.id) : null} />
      </div>
    </div>
  );
}

export default function AuditResults() {
  const { auditId } = useParams();
  const id = Number(auditId);
  const { can, user } = useAuth();
  const { push } = useToast();

  const { data: audit, error, loading, reload } = useAsync(() => api.audit(id), [id]);
  const { data: history, reload: reloadHistory } = useAsync(() => api.auditHistory(id), [id]);

  const [severityFilter, setSeverityFilter] = useState<string>('all');
  const [classFilter, setClassFilter] = useState<string>('all');
  const [evidenceCitation, setEvidenceCitation] = useState<CitationOut | null>(null);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [reviewAction, setReviewAction] = useState('accept');
  const [reviewReason, setReviewReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [exporting, setExporting] = useState<string | null>(null);

  const { data: evidenceSource } = useAsync(async () => {
    if (!evidenceCitation) return null;
    if (evidenceCitation.source_type === 'sop' && evidenceCitation.revision_id) {
      const revision = await api.revision(evidenceCitation.revision_id);
      return { title: evidenceCitation.document_label, text: revision.raw_text };
    }
    if (evidenceCitation.report_id) {
      const report = await api.report(evidenceCitation.report_id);
      return { title: report.report_code, text: report.raw_text };
    }
    return null;
  }, [evidenceCitation?.id ?? 0]);

  const findings = useMemo(() => {
    if (!audit) return [];
    return audit.findings.filter(
      (f) =>
        (severityFilter === 'all' || f.severity === severityFilter) &&
        (classFilter === 'all' || f.classification === classFilter),
    );
  }, [audit, severityFilter, classFilter]);

  async function submitReview() {
    if (!audit) return;
    setBusy(true);
    try {
      await api.reviewAudit(audit.id, reviewAction, reviewReason);
      push('success', 'Review recorded. The original machine findings are preserved unchanged.');
      setReviewOpen(false);
      setReviewReason('');
      reload();
      reloadHistory();
    } catch (err) {
      push('error', err instanceof Error ? err.message : 'Review failed');
    } finally {
      setBusy(false);
    }
  }

  async function exportAs(format: string) {
    if (!audit) return;
    setExporting(format);
    try {
      await api.createExport(audit.id, format);
      await downloadExport(audit.id, format, `${audit.audit_code}.${format}`);
      push('success', `Exported ${audit.audit_code} as ${format.toUpperCase()}.`);
    } catch (err) {
      push('error', err instanceof Error ? err.message : 'Export failed');
    } finally {
      setExporting(null);
    }
  }

  if (loading || error || !audit) {
    return (
      <PageShell
        title="Analysis results"
        description="Evidence-backed findings from one process report."
        loading={loading}
        error={error}
        onRetry={reload}
        loadingLabel="Loading audit"
      >
        {null}
      </PageShell>
    );
  }

  const counts = audit.findings.reduce<Record<string, number>>((acc, f) => {
    acc[f.severity] = (acc[f.severity] ?? 0) + 1;
    return acc;
  }, {});
  const invalidCitations = audit.findings.flatMap((f) => f.citations).filter((c) => !c.verified).length;
  const resolution = audit.version_resolution;

  return (
    <div className="space-y-5">
      <nav className="text-sm text-slate-500 dark:text-slate-400" aria-label="Breadcrumb">
        <Link to="/audits" className="hover:underline">Audit History</Link>
        <span aria-hidden="true"> / </span>
        <span className="text-slate-900 dark:text-slate-100">{audit.audit_code}</span>
      </nav>

      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">{audit.audit_code}</h1>
          <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
            Report {audit.report_code} analysed against{' '}
            {audit.sop_code ? (
              <strong>{audit.sop_code} rev {audit.revision_code}</strong>
            ) : (
              <em>no resolved revision</em>
            )}{' '}
            · {humanize(audit.mode)} mode · {formatDuration(audit.duration_ms)} · {formatDate(audit.created_at)}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {can('auditor') &&
            ['pdf', 'json', 'csv'].map((format) => (
              <button
                key={format}
                type="button"
                className="btn-secondary"
                disabled={exporting !== null}
                onClick={() => exportAs(format)}
              >
                {exporting === format ? 'Exporting…' : `Export ${format.toUpperCase()}`}
              </button>
            ))}
          {can('reviewer') && (
            <button type="button" className="btn-primary" onClick={() => setReviewOpen(true)}>
              Record review decision
            </button>
          )}
        </div>
      </div>

      <section
        className={`rounded-xl border-2 p-5 ${
          audit.outcome === 'no_detected_deviation'
            ? 'border-emerald-300 bg-emerald-50 dark:border-emerald-800 dark:bg-emerald-950/50'
            : audit.outcome === 'deviations_found'
              ? 'border-red-300 bg-red-50 dark:border-red-800 dark:bg-red-950/50'
              : 'border-amber-300 bg-amber-50 dark:border-amber-800 dark:bg-amber-950/50'
        }`}
        aria-label="Analysis outcome"
      >
        <div className="flex flex-wrap items-center gap-3">
          <Badge className={`${OUTCOME_CLASSES[audit.outcome]} !text-sm !px-3 !py-1`}>
            {humanize(audit.outcome)}
          </Badge>
          {audit.requires_human_review && (
            <Badge className="!px-3 !py-1 !text-sm bg-amber-200 text-amber-900 ring-amber-700/30 dark:bg-amber-900 dark:text-amber-100">
              Human review required
            </Badge>
          )}
          <span className="text-sm text-slate-600 dark:text-slate-400">
            {audit.findings.length} finding{audit.findings.length === 1 ? '' : 's'}
          </span>
        </div>
        <p className="mt-3 max-w-3xl text-sm text-slate-700 dark:text-slate-300">{OUTCOME_HELP[audit.outcome]}</p>
      </section>

      <Disclaimer />

      <section className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6" aria-label="Finding counts">
        {['critical', 'major', 'minor', 'info'].map((severity) => (
          <button
            key={severity}
            type="button"
            onClick={() => setSeverityFilter(severityFilter === severity ? 'all' : severity)}
            aria-pressed={severityFilter === severity}
            className={`card p-3 text-left transition-shadow hover:shadow-md ${
              severityFilter === severity ? 'ring-2 ring-brand-500' : ''
            }`}
          >
            <p className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
              {humanize(severity)}
            </p>
            <p className="mt-1 text-xl font-bold tabular-nums text-slate-900 dark:text-slate-100">
              {counts[severity] ?? 0}
            </p>
          </button>
        ))}
        <div className="card p-3">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
            Citations
          </p>
          <p className="mt-1 text-xl font-bold tabular-nums text-slate-900 dark:text-slate-100">
            {audit.findings.reduce((n, f) => n + f.citations.length, 0)}
          </p>
        </div>
        <div className="card p-3">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
            Unverifiable
          </p>
          <p
            className={`mt-1 text-xl font-bold tabular-nums ${
              invalidCitations > 0 ? 'text-red-600 dark:text-red-400' : 'text-emerald-600 dark:text-emerald-400'
            }`}
          >
            {invalidCitations}
          </p>
        </div>
      </section>

      <section className="card p-5">
        <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">
          Which SOP revision governed this run?
        </h2>
        <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
          Newest is never assumed authoritative. The engine must be able to justify its choice, or it blocks.
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {resolution.blocked ? (
            <Badge className="bg-fuchsia-100 text-fuchsia-800 ring-fuchsia-600/20 dark:bg-fuchsia-950 dark:text-fuchsia-200 dark:ring-fuchsia-400/30">
              Could not be determined
            </Badge>
          ) : (
            <Badge className="bg-emerald-100 text-emerald-800 ring-emerald-600/20 dark:bg-emerald-950 dark:text-emerald-200 dark:ring-emerald-400/30">
              Rev {resolution.resolved_revision_code}
            </Badge>
          )}
          <span className="text-sm text-slate-600 dark:text-slate-400">
            Basis: {humanize(resolution.authority_basis)}
          </span>
        </div>

        {resolution.declared_revision_code && (
          <p className="mt-3 text-sm text-slate-600 dark:text-slate-400">
            The report itself declares revision <strong>{resolution.declared_revision_code}</strong>
            {resolution.declared_matches_resolved === false && (
              <span className="font-medium text-amber-700 dark:text-amber-300">
                {' '}
                — which is <em>not</em> the revision the engine resolved. A declared revision is a claim, not
                authority.
              </span>
            )}
            {resolution.declared_matches_resolved === true && ', which matches the resolved revision.'}
          </p>
        )}

        {resolution.candidates.length > 0 && (
          <details className="mt-3" open={resolution.blocked}>
            <summary className="cursor-pointer text-sm font-medium text-brand-700 dark:text-brand-300">
              Show all {resolution.candidates.length} revision(s) considered and why
            </summary>
            <ul className="mt-2 space-y-2 text-sm">
              {resolution.candidates.map((candidate) => (
                <li
                  key={candidate.revision_id}
                  className="rounded-lg border border-slate-200 p-3 dark:border-slate-700"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge
                      className={
                        candidate.authoritative
                          ? 'bg-emerald-100 text-emerald-800 ring-emerald-600/20 dark:bg-emerald-950 dark:text-emerald-200 dark:ring-emerald-400/30'
                          : ''
                      }
                    >
                      Rev {candidate.revision_code}
                    </Badge>
                    <Badge className={STATUS_CLASSES[candidate.status] ?? ''}>{humanize(candidate.status)}</Badge>
                    <span className="text-xs text-slate-500 dark:text-slate-400">
                      effective {candidate.effective_date ?? 'unknown'} → {candidate.expiry_date ?? 'open-ended'}
                    </span>
                    {candidate.authoritative && (
                      <span className="text-xs font-medium text-emerald-700 dark:text-emerald-300">
                        selected as authoritative
                      </span>
                    )}
                  </div>
                  {candidate.reasons.length > 0 && (
                    <ul className="mt-1.5 space-y-0.5 text-xs text-slate-600 dark:text-slate-400">
                      {candidate.reasons.map((reason) => (
                        <li key={reason}>• {reason}</li>
                      ))}
                    </ul>
                  )}
                </li>
              ))}
            </ul>
          </details>
        )}

        {resolution.notes.length > 0 && (
          <ul className="mt-3 text-xs text-slate-500 dark:text-slate-400">
            {resolution.notes.map((note) => (
              <li key={note}>• {note}</li>
            ))}
          </ul>
        )}
      </section>

      <section className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">
            Findings ({findings.length}
            {findings.length !== audit.findings.length && ` of ${audit.findings.length}`})
          </h2>
          <div className="flex flex-wrap gap-2">
            <label className="sr-only" htmlFor="class-filter">Filter by classification</label>
            <select
              id="class-filter"
              className="input max-w-[220px]"
              value={classFilter}
              onChange={(e) => setClassFilter(e.target.value)}
            >
              <option value="all">All classifications</option>
              {[
                'confirmed_deviation',
                'possible_deviation',
                'insufficient_evidence',
                'sop_conflict',
                'no_detected_deviation',
              ].map((value) => (
                <option key={value} value={value}>
                  {humanize(value)}
                </option>
              ))}
            </select>
            {(severityFilter !== 'all' || classFilter !== 'all') && (
              <button
                type="button"
                className="btn-ghost"
                onClick={() => {
                  setSeverityFilter('all');
                  setClassFilter('all');
                }}
              >
                Clear filters
              </button>
            )}
          </div>
        </div>

        {findings.length === 0 ? (
          <EmptyState
            title={audit.findings.length === 0 ? 'No findings were raised' : 'No findings match these filters'}
            description={
              audit.findings.length === 0
                ? 'The deterministic engine found no departure from the governing SOP. This is not a certification of compliance.'
                : undefined
            }
          />
        ) : (
          <div className="space-y-3">
            {findings.map((finding) => (
              <FindingCard
                key={finding.id}
                finding={finding}
                onCitationClick={setEvidenceCitation}
              />
            ))}
          </div>
        )}
      </section>

      <section className="card p-5">
        <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">Decision history</h2>
        <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
          Append-only. Reviewer decisions are recorded alongside the machine findings, never on top of them.
        </p>
        {!history || history.length === 0 ? (
          <p className="mt-3 text-sm text-slate-500 dark:text-slate-400">No reviewer action recorded yet.</p>
        ) : (
          <ol className="mt-3 space-y-3">
            {history.map((action) => (
              <li key={action.id} className="border-l-2 border-slate-200 pl-3 dark:border-slate-700">
                <p className="text-sm font-medium text-slate-900 dark:text-slate-100">
                  {humanize(action.action)}
                  {action.from_state && action.to_state && (
                    <span className="font-normal text-slate-500 dark:text-slate-400">
                      {' '}
                      ({humanize(action.from_state)} → {humanize(action.to_state)})
                    </span>
                  )}
                </p>
                <p className="text-xs text-slate-500 dark:text-slate-400">
                  {action.actor_name || 'system'} · {formatDate(action.created_at)}
                </p>
                <p className="mt-1 text-sm text-slate-700 dark:text-slate-300">{action.reason}</p>
              </li>
            ))}
          </ol>
        )}
      </section>

      <details className="card p-5">
        <summary className="cursor-pointer text-base font-semibold text-slate-900 dark:text-slate-100">
          Pipeline timing &amp; engine details
        </summary>
        <dl className="mt-3 grid gap-x-6 gap-y-1.5 text-sm sm:grid-cols-2 lg:grid-cols-3">
          {audit.pipeline_log.map((stage) => (
            <div key={stage.stage} className="flex justify-between gap-3">
              <dt className="text-slate-600 dark:text-slate-400">{humanize(stage.stage)}</dt>
              <dd className="font-medium tabular-nums text-slate-900 dark:text-slate-100">
                {formatDuration(stage.duration_ms)}
              </dd>
            </div>
          ))}
        </dl>
        <p className="mt-3 text-xs text-slate-500 dark:text-slate-400">
          Engine {audit.engine_version} · provider {audit.provider_used || 'none (deterministic)'} · model{' '}
          {audit.model_used || 'n/a'} · cost ${(audit.cost_usd ?? 0).toFixed(4)}
        </p>
      </details>

      <Modal
        open={evidenceCitation !== null}
        onClose={() => setEvidenceCitation(null)}
        title="Evidence in context"
        wide
        footer={
          <button type="button" className="btn-secondary" onClick={() => setEvidenceCitation(null)}>
            Close
          </button>
        }
      >
        {evidenceCitation && (
          <>
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <Badge>{evidenceCitation.source_type === 'sop' ? 'SOP' : 'Process report'}</Badge>
              <span className="font-medium">{evidenceCitation.document_label}</span>
              <span className="font-mono text-xs text-slate-500">
                chars {evidenceCitation.char_start}–{evidenceCitation.char_end}
              </span>
            </div>
            {evidenceSource ? (
              <SourcePanel title={evidenceSource.title} text={evidenceSource.text} citation={evidenceCitation} />
            ) : (
              <Spinner label="Loading source document" />
            )}
          </>
        )}
      </Modal>

      <Modal
        open={reviewOpen}
        onClose={() => setReviewOpen(false)}
        title={`Record a review decision for ${audit.audit_code}`}
        footer={
          <>
            <button type="button" className="btn-secondary" onClick={() => setReviewOpen(false)}>
              Cancel
            </button>
            <button
              type="button"
              className="btn-primary"
              onClick={submitReview}
              disabled={busy || reviewReason.trim().length < 10}
            >
              {busy ? 'Saving…' : 'Record decision'}
            </button>
          </>
        }
      >
        <p className="text-sm text-slate-600 dark:text-slate-400">
          Signed in as <strong>{user?.full_name || user?.username}</strong>. Your decision is appended to the
          audit trail with your name and a timestamp; it never alters the machine findings.
        </p>
        <div>
          <label className="label" htmlFor="review-action">Decision</label>
          <select id="review-action" className="input" value={reviewAction} onChange={(e) => setReviewAction(e.target.value)}>
            <option value="accept">Accept the analysis</option>
            <option value="reject">Reject the analysis</option>
            <option value="request_info">Request more information</option>
            <option value="reopen">Reopen for further work</option>
          </select>
        </div>
        <div>
          <label className="label" htmlFor="review-reason">
            Rationale (required, at least 10 characters)
          </label>
          <textarea
            id="review-reason"
            className="input min-h-[110px]"
            value={reviewReason}
            onChange={(e) => setReviewReason(e.target.value)}
            placeholder="Explain what you checked and why you reached this decision…"
          />
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
            {reviewReason.trim().length}/10 characters minimum
          </p>
        </div>
      </Modal>

    </div>
  );
}
