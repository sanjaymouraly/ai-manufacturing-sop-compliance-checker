import type { ReactNode } from 'react';
import { ErrorBanner } from './ErrorBanner';
import { Spinner } from './Spinner';

type Props = {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
  loading?: boolean;
  error?: unknown;
  onRetry?: () => void;
  loadingLabel?: string;
  children: ReactNode;
};

/**
 * Standard page frame.
 *
 * The heading and description render in every state - loading, error and
 * loaded - so a reader is never left on a page with no title, screen-reader
 * users keep a stable landmark, and tests can anchor on the h1.
 */
export function PageShell({
  title,
  description,
  actions,
  loading = false,
  error,
  onRetry,
  loadingLabel,
  children,
}: Props) {
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">{title}</h1>
          {Boolean(description) && (
            <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">{description}</p>
          )}
        </div>
        {!loading && !error && Boolean(actions) && <div className="flex flex-wrap gap-2">{actions}</div>}
      </div>

      {loading ? (
        <Spinner label={loadingLabel ?? `Loading ${title.toLowerCase()}`} />
      ) : error ? (
        <ErrorBanner error={error} onRetry={onRetry} />
      ) : (
        children
      )}
    </div>
  );
}
