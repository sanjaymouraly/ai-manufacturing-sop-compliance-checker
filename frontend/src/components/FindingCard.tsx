import { useState } from 'react';
import type { FindingOut } from '../lib/api';
import { Badge } from './Badge';
import {
  CLASSIFICATION_CLASSES,
  CLASSIFICATION_HELP,
  humanize,
  SEVERITY_CLASSES,
} from '../lib/format';

export function FindingCard({
  finding,
  onCitationClick,
  actions,
  defaultOpen = false,
}: {
  finding: FindingOut;
  onCitationClick?: (citation: FindingOut['citations'][number]) => void;
  actions?: React.ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const invalidCitations = finding.citations.filter((c) => !c.verified);
  // Per-citation notes are already shown beside their quote; repeating them in
  // the summary list below is noise.
  const citationNotes = new Set(
    finding.citations.map((c) => c.verification_note).filter((n): n is string => Boolean(n)),
  );
  const extraVerificationNotes = finding.verification_notes.filter((n) => !citationNotes.has(n));

  return (
    <article
      id={`finding-${finding.id}`}
      className="card overflow-hidden"
      aria-labelledby={`finding-title-${finding.id}`}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-start gap-3 px-4 py-3 text-left hover:bg-slate-50 dark:hover:bg-slate-800/50"
      >
        <span aria-hidden="true" className="mt-1 text-slate-400">
          {open ? '▾' : '▸'}
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-2">
            <Badge className={SEVERITY_CLASSES[finding.severity]}>{humanize(finding.severity)}</Badge>
            <Badge className={CLASSIFICATION_CLASSES[finding.classification]} title={CLASSIFICATION_HELP[finding.classification]}>
              {humanize(finding.classification)}
            </Badge>
            <Badge>{humanize(finding.category)}</Badge>
            <code className="text-xs text-slate-400 dark:text-slate-500">{finding.finding_code}</code>
            {finding.detector !== 'deterministic' && (
              <Badge className="bg-violet-100 text-violet-800 ring-violet-600/20 dark:bg-violet-950 dark:text-violet-200 dark:ring-violet-400/30">
                {humanize(finding.detector)}
              </Badge>
            )}
            {invalidCitations.length > 0 && (
              <Badge className="bg-red-100 text-red-800 ring-red-600/20 dark:bg-red-950 dark:text-red-200 dark:ring-red-400/30">
                {invalidCitations.length} unverifiable quote{invalidCitations.length === 1 ? '' : 's'}
              </Badge>
            )}
            {finding.review_status !== 'open' && (
              <Badge className="bg-sky-100 text-sky-800 ring-sky-600/20 dark:bg-sky-950 dark:text-sky-200 dark:ring-sky-400/30">
                {humanize(finding.review_status)}
              </Badge>
            )}
          </span>
          <span id={`finding-title-${finding.id}`} className="mt-1.5 block text-sm font-semibold text-slate-900 dark:text-slate-100">
            {finding.title}
          </span>
          {!open && (
            <span className="mt-0.5 block truncate text-xs text-slate-500 dark:text-slate-400">
              {finding.explanation}
            </span>
          )}
        </span>
        <span className="shrink-0 text-right text-xs text-slate-400 dark:text-slate-500">
          confidence
          <span className="block font-semibold tabular-nums text-slate-600 dark:text-slate-300">
            {(finding.confidence * 100).toFixed(0)}%
          </span>
        </span>
      </button>

      {open && (
        <div className="space-y-4 border-t border-slate-100 px-4 py-4 dark:border-slate-800">
          <p className="rounded-lg bg-slate-50 px-3 py-2 text-xs italic text-slate-600 dark:bg-slate-800/60 dark:text-slate-400">
            {CLASSIFICATION_HELP[finding.classification]}
          </p>

          {(finding.expected || finding.observed) && (
            <div className="grid gap-px overflow-hidden rounded-lg bg-slate-200 sm:grid-cols-2 dark:bg-slate-800">
              <div className="bg-white p-3 dark:bg-slate-900">
                <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                  SOP expects
                </p>
                <p className="mt-1 text-sm text-slate-800 dark:text-slate-200">{finding.expected || '—'}</p>
              </div>
              <div className="bg-white p-3 dark:bg-slate-900">
                <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                  Report shows
                </p>
                <p className="mt-1 text-sm text-slate-800 dark:text-slate-200">{finding.observed || '—'}</p>
              </div>
            </div>
          )}

          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Why this was raised
            </p>
            <p className="mt-1 text-sm text-slate-700 dark:text-slate-300">{finding.explanation}</p>
          </div>

          {finding.suggested_action && (
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                Suggested next step for a reviewer
              </p>
              <p className="mt-1 text-sm text-slate-700 dark:text-slate-300">{finding.suggested_action}</p>
            </div>
          )}

          {finding.uncertainty_notes.length > 0 && (
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                Uncertainty
              </p>
              <ul className="mt-1 space-y-0.5 text-sm text-amber-700 dark:text-amber-300">
                {finding.uncertainty_notes.map((note) => (
                  <li key={note}>• {note}</li>
                ))}
              </ul>
            </div>
          )}

          <div>
            <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Evidence ({finding.citations.length} quote{finding.citations.length === 1 ? '' : 's'})
            </p>
            {finding.citations.length === 0 ? (
              <p className="text-sm italic text-slate-500 dark:text-slate-400">
                No source quote could be attached, so this finding cannot be confirmed.
              </p>
            ) : (
              <ul className="space-y-2">
                {finding.citations.map((citation) => (
                  <li
                    key={citation.id}
                    className={`rounded-lg border p-3 ${
                      citation.verified
                        ? 'border-slate-200 dark:border-slate-700'
                        : 'border-red-300 bg-red-50 dark:border-red-800 dark:bg-red-950/50'
                    }`}
                  >
                    <div className="mb-1.5 flex flex-wrap items-center gap-2 text-xs">
                      <Badge className={citation.source_type === 'sop' ? 'bg-brand-100 text-brand-800 ring-brand-600/20 dark:bg-brand-950 dark:text-brand-200 dark:ring-brand-400/30' : ''}>
                        {citation.source_type === 'sop' ? 'SOP' : 'Report'}
                      </Badge>
                      <span className="font-medium text-slate-700 dark:text-slate-300">{citation.document_label}</span>
                      {citation.section_path && <span className="text-slate-500 dark:text-slate-400">§{citation.section_path}</span>}
                      {citation.page !== null && <span className="text-slate-500 dark:text-slate-400">p.{citation.page}</span>}
                      <span className="font-mono text-[11px] text-slate-400 dark:text-slate-500">
                        chars {citation.char_start}–{citation.char_end}
                      </span>
                      {citation.verified ? (
                        <Badge className="bg-emerald-100 text-emerald-800 ring-emerald-600/20 dark:bg-emerald-950 dark:text-emerald-200 dark:ring-emerald-400/30">
                          ✓ verified against source
                        </Badge>
                      ) : (
                        <Badge className="bg-red-100 text-red-800 ring-red-600/20 dark:bg-red-950 dark:text-red-200 dark:ring-red-400/30">
                          ✕ could not be verified
                        </Badge>
                      )}
                      {onCitationClick && (
                        <button
                          type="button"
                          className="ml-auto text-brand-700 underline hover:no-underline dark:text-brand-300"
                          onClick={() => onCitationClick(citation)}
                        >
                          Show in source
                        </button>
                      )}
                    </div>
                    <blockquote className="border-l-2 border-slate-300 pl-3 font-mono text-[13px] leading-relaxed text-slate-700 dark:border-slate-600 dark:text-slate-300">
                      {citation.quote}
                    </blockquote>
                    {citation.verification_note && (
                      <p
                        className={
                          citation.verified
                            ? 'mt-1.5 text-xs text-slate-500 dark:text-slate-400'
                            : 'mt-1.5 text-xs text-red-700 dark:text-red-300'
                        }
                      >
                        {citation.verification_note}
                      </p>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>

          <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 border-t border-slate-100 pt-3 text-xs sm:grid-cols-4 dark:border-slate-800">
            {[
              ['Requirement', finding.requirement_key || '—'],
              ['Governing SOP', finding.sop_code ? `${finding.sop_code} rev ${finding.revision_code ?? '?'}` : '—'],
              ['Evidence', humanize(finding.evidence_sufficiency)],
              ['Verification', humanize(finding.verification_status)],
            ].map(([label, value]) => (
              <div key={label}>
                <dt className="text-slate-500 dark:text-slate-400">{label}</dt>
                <dd className="font-medium text-slate-800 dark:text-slate-200">{value}</dd>
              </div>
            ))}
          </dl>

          {extraVerificationNotes.length > 0 && (
            <ul className="text-xs text-slate-500 dark:text-slate-400">
              {extraVerificationNotes.map((note) => (
                <li key={note}>• {note}</li>
              ))}
            </ul>
          )}

          {finding.reviewer_comment && (
            <p className="rounded-lg border border-sky-200 bg-sky-50 px-3 py-2 text-sm text-sky-900 dark:border-sky-800 dark:bg-sky-950 dark:text-sky-100">
              <strong>Reviewer note:</strong> {finding.reviewer_comment}
            </p>
          )}

          {actions && <div className="flex flex-wrap gap-2 border-t border-slate-100 pt-3 dark:border-slate-800">{actions}</div>}
        </div>
      )}
    </article>
  );
}
