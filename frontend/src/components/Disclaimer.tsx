import { DISCLAIMER } from '../lib/format';

export function Disclaimer({ compact = false }: { compact?: boolean }) {
  return (
    <aside
      className={`rounded-lg border border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-700/60 dark:bg-amber-950/60 dark:text-amber-100 ${
        compact ? 'px-3 py-2 text-xs' : 'px-4 py-3 text-sm'
      }`}
    >
      <strong className="font-semibold">Decision support only. </strong>
      <span>{DISCLAIMER.replace('Decision support only. ', '')}</span>
    </aside>
  );
}
