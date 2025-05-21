/**
 * Dependency-free SVG/CSS charts. Every chart also exposes its numbers as text
 * so screen readers and non-technical reviewers never depend on colour alone.
 */
import { humanize } from '../lib/format';

export function BarList({
  data,
  colorFor,
  emptyLabel = 'Nothing recorded yet',
  max: explicitMax,
}: {
  data: Array<{ label: string; value: number; hint?: string }>;
  colorFor?: (label: string, index: number) => string;
  emptyLabel?: string;
  max?: number;
}) {
  const rows = data.filter((d) => d.value > 0);
  if (rows.length === 0) {
    return <p className="py-6 text-center text-sm text-slate-500 dark:text-slate-400">{emptyLabel}</p>;
  }
  const max = explicitMax ?? Math.max(...rows.map((r) => r.value), 1);

  return (
    <ul className="space-y-2.5">
      {rows.map((row, index) => (
        <li key={row.label}>
          <div className="mb-1 flex items-baseline justify-between gap-3 text-sm">
            <span className="truncate text-slate-700 dark:text-slate-300" title={row.hint ?? row.label}>
              {humanize(row.label)}
            </span>
            <span className="shrink-0 font-semibold tabular-nums text-slate-900 dark:text-slate-100">
              {row.value}
            </span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
            <div
              className={`h-full rounded-full ${colorFor?.(row.label, index) ?? 'bg-brand-500'}`}
              style={{ width: `${Math.max(3, (row.value / max) * 100)}%` }}
            />
          </div>
        </li>
      ))}
    </ul>
  );
}

export function Donut({
  segments,
  centerLabel,
  centerValue,
}: {
  segments: Array<{ label: string; value: number; color: string }>;
  centerLabel: string;
  centerValue: string | number;
}) {
  const total = segments.reduce((sum, s) => sum + s.value, 0);
  const radius = 54;
  const circumference = 2 * Math.PI * radius;
  let offset = 0;

  return (
    <div className="flex flex-wrap items-center gap-6">
      <svg viewBox="0 0 140 140" className="h-36 w-36 shrink-0 -rotate-90" role="img" aria-label={`${centerLabel}: ${centerValue}`}>
        <circle cx="70" cy="70" r={radius} fill="none" strokeWidth="18" className="stroke-slate-100 dark:stroke-slate-800" />
        {total > 0 &&
          segments
            .filter((s) => s.value > 0)
            .map((segment) => {
              const length = (segment.value / total) * circumference;
              const element = (
                <circle
                  key={segment.label}
                  cx="70"
                  cy="70"
                  r={radius}
                  fill="none"
                  strokeWidth="18"
                  className={segment.color}
                  strokeDasharray={`${length} ${circumference - length}`}
                  strokeDashoffset={-offset}
                />
              );
              offset += length;
              return element;
            })}
        <text
          x="70"
          y="66"
          textAnchor="middle"
          className="rotate-90 fill-slate-900 text-[20px] font-bold dark:fill-slate-100"
          transform="rotate(90 70 70)"
        >
          {centerValue}
        </text>
        <text
          x="70"
          y="84"
          textAnchor="middle"
          className="fill-slate-500 text-[9px] uppercase tracking-wide dark:fill-slate-400"
          transform="rotate(90 70 70)"
        >
          {centerLabel}
        </text>
      </svg>
      <ul className="min-w-0 flex-1 space-y-1.5 text-sm">
        {segments.map((segment) => (
          <li key={segment.label} className="flex items-center justify-between gap-3">
            <span className="flex min-w-0 items-center gap-2">
              <span aria-hidden="true" className={`h-2.5 w-2.5 shrink-0 rounded-sm ${segment.color.replace('stroke-', 'bg-')}`} />
              <span className="truncate text-slate-700 dark:text-slate-300">{humanize(segment.label)}</span>
            </span>
            <span className="shrink-0 font-semibold tabular-nums text-slate-900 dark:text-slate-100">
              {segment.value}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function Sparkline({
  points,
  label,
}: {
  points: Array<{ date: string; value: number }>;
  label: string;
}) {
  if (points.length === 0) {
    return <p className="py-6 text-center text-sm text-slate-500 dark:text-slate-400">No activity in this window.</p>;
  }
  const max = Math.max(...points.map((p) => p.value), 1);
  const width = 100;
  const height = 32;
  const step = points.length > 1 ? width / (points.length - 1) : 0;
  const path = points
    .map((p, i) => `${i === 0 ? 'M' : 'L'} ${(i * step).toFixed(2)} ${(height - (p.value / max) * height).toFixed(2)}`)
    .join(' ');

  return (
    <div>
      <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" className="h-16 w-full" role="img" aria-label={label}>
        <path d={path} fill="none" strokeWidth="1.5" vectorEffect="non-scaling-stroke" className="stroke-brand-500" />
      </svg>
      <div className="mt-1 flex justify-between text-[11px] text-slate-500 dark:text-slate-400">
        <span>{points[0].date}</span>
        <span>peak {max}</span>
        <span>{points[points.length - 1].date}</span>
      </div>
    </div>
  );
}
