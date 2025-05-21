export function ErrorBanner({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const message = error instanceof Error ? error.message : String(error ?? 'Unknown error');
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-900 dark:border-red-800 dark:bg-red-950 dark:text-red-100"
    >
      <span>{message}</span>
      {onRetry && (
        <button type="button" onClick={onRetry} className="btn-secondary">
          Retry
        </button>
      )}
    </div>
  );
}
