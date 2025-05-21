import type { Classification, Severity } from './api';

/** Turn a snake_case enum value into a human sentence for non-technical reviewers. */
export function humanize(value: string | null | undefined): string {
  if (!value) return '—';
  return value
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .replace(/\bSop\b/g, 'SOP')
    .replace(/\bOcr\b/g, 'OCR')
    .replace(/\bLlm\b/g, 'LLM')
    .replace(/\bAi\b/g, 'AI')
    .replace(/\bPh\b/g, 'pH');
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function formatDay(value: string | null | undefined): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: '2-digit' });
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—';
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

export const SEVERITY_CLASSES: Record<Severity, string> = {
  critical:
    'bg-red-100 text-red-800 ring-red-600/20 dark:bg-red-950 dark:text-red-200 dark:ring-red-400/30',
  major:
    'bg-orange-100 text-orange-800 ring-orange-600/20 dark:bg-orange-950 dark:text-orange-200 dark:ring-orange-400/30',
  minor:
    'bg-amber-100 text-amber-800 ring-amber-600/20 dark:bg-amber-950 dark:text-amber-200 dark:ring-amber-400/30',
  info: 'bg-slate-100 text-slate-700 ring-slate-500/20 dark:bg-slate-800 dark:text-slate-200 dark:ring-slate-400/30',
};

export const CLASSIFICATION_CLASSES: Record<Classification, string> = {
  confirmed_deviation:
    'bg-red-100 text-red-800 ring-red-600/20 dark:bg-red-950 dark:text-red-200 dark:ring-red-400/30',
  possible_deviation:
    'bg-orange-100 text-orange-800 ring-orange-600/20 dark:bg-orange-950 dark:text-orange-200 dark:ring-orange-400/30',
  insufficient_evidence:
    'bg-violet-100 text-violet-800 ring-violet-600/20 dark:bg-violet-950 dark:text-violet-200 dark:ring-violet-400/30',
  sop_conflict:
    'bg-fuchsia-100 text-fuchsia-800 ring-fuchsia-600/20 dark:bg-fuchsia-950 dark:text-fuchsia-200 dark:ring-fuchsia-400/30',
  no_detected_deviation:
    'bg-emerald-100 text-emerald-800 ring-emerald-600/20 dark:bg-emerald-950 dark:text-emerald-200 dark:ring-emerald-400/30',
};

/**
 * Plain-language gloss for each classification. The wording deliberately avoids any
 * language that could read as a compliance certification.
 */
export const CLASSIFICATION_HELP: Record<Classification, string> = {
  confirmed_deviation:
    'The report contradicts the governing SOP, and every supporting quote was matched back to stored source text.',
  possible_deviation:
    'Evidence points to a departure from the SOP, but something is uncertain - a reviewer must decide.',
  insufficient_evidence:
    'The record does not contain enough information to judge this requirement either way.',
  sop_conflict:
    'The SOP library itself is inconsistent here, so no conclusion about the run can be drawn until it is resolved.',
  no_detected_deviation:
    'No departure was detected for this item. This is not a certification of compliance.',
};

export const OUTCOME_CLASSES: Record<string, string> = {
  no_detected_deviation:
    'bg-emerald-100 text-emerald-800 ring-emerald-600/20 dark:bg-emerald-950 dark:text-emerald-200 dark:ring-emerald-400/30',
  deviations_found:
    'bg-red-100 text-red-800 ring-red-600/20 dark:bg-red-950 dark:text-red-200 dark:ring-red-400/30',
  human_review_required:
    'bg-amber-100 text-amber-900 ring-amber-600/20 dark:bg-amber-950 dark:text-amber-200 dark:ring-amber-400/30',
  blocked_unresolved_authority:
    'bg-fuchsia-100 text-fuchsia-800 ring-fuchsia-600/20 dark:bg-fuchsia-950 dark:text-fuchsia-200 dark:ring-fuchsia-400/30',
};

export const STATUS_CLASSES: Record<string, string> = {
  approved:
    'bg-emerald-100 text-emerald-800 ring-emerald-600/20 dark:bg-emerald-950 dark:text-emerald-200 dark:ring-emerald-400/30',
  draft: 'bg-slate-100 text-slate-700 ring-slate-500/20 dark:bg-slate-800 dark:text-slate-200 dark:ring-slate-400/30',
  superseded:
    'bg-sky-100 text-sky-800 ring-sky-600/20 dark:bg-sky-950 dark:text-sky-200 dark:ring-sky-400/30',
  archived:
    'bg-slate-100 text-slate-500 ring-slate-500/20 dark:bg-slate-800 dark:text-slate-400 dark:ring-slate-400/30',
  conflicting:
    'bg-fuchsia-100 text-fuchsia-800 ring-fuchsia-600/20 dark:bg-fuchsia-950 dark:text-fuchsia-200 dark:ring-fuchsia-400/30',
  open: 'bg-amber-100 text-amber-900 ring-amber-600/20 dark:bg-amber-950 dark:text-amber-200 dark:ring-amber-400/30',
  resolved:
    'bg-emerald-100 text-emerald-800 ring-emerald-600/20 dark:bg-emerald-950 dark:text-emerald-200 dark:ring-emerald-400/30',
  acknowledged:
    'bg-sky-100 text-sky-800 ring-sky-600/20 dark:bg-sky-950 dark:text-sky-200 dark:ring-sky-400/30',
  pending:
    'bg-slate-100 text-slate-700 ring-slate-500/20 dark:bg-slate-800 dark:text-slate-200 dark:ring-slate-400/30',
};

export const DISCLAIMER =
  'Decision support only. This tool does not certify compliance, approve product release, or replace a qualified human reviewer.';
