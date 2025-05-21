import { Link } from 'react-router-dom';
import { api } from '../lib/api';
import { useAsync } from '../lib/useAsync';
import { Disclaimer } from '../components/Disclaimer';
import { BarList, Donut, Sparkline } from '../components/Charts';
import { humanize } from '../lib/format';
import { PageShell } from '../components/PageShell';

const SEVERITY_BARS: Record<string, string> = {
  critical: 'bg-red-500',
  major: 'bg-orange-500',
  minor: 'bg-amber-400',
  info: 'bg-slate-400',
};

function StatCard({
  label,
  value,
  sub,
  to,
  tone = 'default',
}: {
  label: string;
  value: number | string;
  sub?: string;
  to?: string;
  tone?: 'default' | 'warn' | 'danger' | 'good';
}) {
  const tones = {
    default: 'text-slate-900 dark:text-slate-100',
    warn: 'text-amber-600 dark:text-amber-400',
    danger: 'text-red-600 dark:text-red-400',
    good: 'text-emerald-600 dark:text-emerald-400',
  };
  const body = (
    <div className="card h-full p-4 transition-shadow hover:shadow-md">
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">{label}</p>
      <p className={`mt-1.5 text-2xl font-bold tabular-nums ${tones[tone]}`}>{value}</p>
      {sub && <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{sub}</p>}
    </div>
  );
  return to ? (
    <Link to={to} className="block rounded-xl focus-visible:outline-none">
      {body}
    </Link>
  ) : (
    body
  );
}

export default function Dashboard() {
  const { data, error, loading, reload } = useAsync(() => api.dashboard(90), []);

  const t = data?.totals;
  const verification = data?.evidence_verification;
  const validity =
    !verification || verification.validity_rate === null
      ? '—'
      : `${(verification.validity_rate * 100).toFixed(1)}%`;

  return (
    <PageShell
      title="Dashboard"
      description="Activity across the SOP library, analyses and reviewer queue."
      loading={loading}
      error={error}
      onRetry={reload}
      actions={
        <>
          <Link to="/audits/new" className="btn-primary">
            New audit
          </Link>
          <Link to="/sops" className="btn-secondary">
            SOP library
          </Link>
        </>
      }
    >
      {!data || !t || !verification ? null : (
      <div className="space-y-6">

      <Disclaimer />

      <section aria-label="Key counts" className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <StatCard label="SOP documents" value={t.sop_documents} sub={`${t.sop_revisions} revisions`} to="/sops" />
        <StatCard label="Process reports" value={t.process_reports} sub="uploaded run records" />
        <StatCard label="Analyses run" value={t.audits} sub={`${t.findings} findings`} to="/audits" />
        <StatCard
          label="Needs human review"
          value={t.audits_requiring_review}
          sub="analyses flagged for a reviewer"
          tone={t.audits_requiring_review > 0 ? 'warn' : 'good'}
          to="/review"
        />
        <StatCard
          label="Open SOP conflicts"
          value={t.open_conflicts}
          sub="block conclusions"
          tone={t.open_conflicts > 0 ? 'danger' : 'good'}
          to="/conflicts"
        />
        <StatCard
          label="Citation validity"
          value={validity}
          sub={`${verification.citations_verified}/${verification.citations_total} verified`}
          tone={verification.validity_rate === 1 ? 'good' : 'warn'}
        />
      </section>

      <div className="grid gap-4 lg:grid-cols-2">
        <section className="card p-5">
          <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">Analysis outcomes</h2>
          <p className="mb-4 mt-1 text-xs text-slate-500 dark:text-slate-400">
            What each completed analysis concluded. An analysis can also be flagged for a reviewer while reporting one of the other outcomes, so that count is higher.</p>
          <Donut
            centerLabel="analyses"
            centerValue={t.audits}
            segments={[
              { label: 'no_detected_deviation', value: data.audits_by_outcome.no_detected_deviation ?? 0, color: 'stroke-emerald-500' },
              { label: 'deviations_found', value: data.audits_by_outcome.deviations_found ?? 0, color: 'stroke-red-500' },
              { label: 'human_review_required', value: data.audits_by_outcome.human_review_required ?? 0, color: 'stroke-amber-500' },
              { label: 'blocked_unresolved_authority', value: data.audits_by_outcome.blocked_unresolved_authority ?? 0, color: 'stroke-fuchsia-500' },
            ]}
          />
        </section>

        <section className="card p-5">
          <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">Findings by classification</h2>
          <p className="mb-4 mt-1 text-xs text-slate-500 dark:text-slate-400">
            A finding only reaches “confirmed” when every one of its quotes resolved to stored source text.
          </p>
          <BarList
            data={Object.entries(data.findings_by_classification).map(([label, value]) => ({ label, value }))}
            colorFor={(label) =>
              ({
                confirmed_deviation: 'bg-red-500',
                possible_deviation: 'bg-orange-500',
                insufficient_evidence: 'bg-violet-500',
                sop_conflict: 'bg-fuchsia-500',
                no_detected_deviation: 'bg-emerald-500',
              })[label] ?? 'bg-brand-500'
            }
          />
        </section>

        <section className="card p-5">
          <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">Findings by category</h2>
          <p className="mb-4 mt-1 text-xs text-slate-500 dark:text-slate-400">
            Which checks are producing findings across the whole library.
          </p>
          <div className="max-h-80 overflow-y-auto pr-1">
            <BarList
              data={Object.entries(data.findings_by_category)
                .map(([label, value]) => ({ label, value }))
                .sort((a, b) => b.value - a.value)}
            />
          </div>
        </section>

        <section className="card p-5">
          <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">Severity mix</h2>
          <p className="mb-4 mt-1 text-xs text-slate-500 dark:text-slate-400">
            Severity drives escalation: any critical or major finding forces human review.
          </p>
          <BarList
            data={['critical', 'major', 'minor', 'info'].map((label) => ({
              label,
              value: data.findings_by_severity[label] ?? 0,
            }))}
            colorFor={(label) => SEVERITY_BARS[label] ?? 'bg-brand-500'}
          />
        </section>

        <section className="card p-5">
          <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">
            Most frequently missing steps
          </h2>
          <p className="mb-4 mt-1 text-xs text-slate-500 dark:text-slate-400">
            Requirements most often absent from executed reports — useful for targeting operator training.
          </p>
          {data.top_missing_steps.length === 0 ? (
            <p className="py-6 text-center text-sm text-slate-500 dark:text-slate-400">
              No missing-step findings recorded.
            </p>
          ) : (
            <BarList
              data={data.top_missing_steps.map((row) => ({
                label: row.requirement_key,
                value: row.count,
              }))}
              colorFor={() => 'bg-orange-500'}
            />
          )}
        </section>

        <section className="card p-5">
          <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">Recurring SOP conflicts</h2>
          <p className="mb-4 mt-1 text-xs text-slate-500 dark:text-slate-400">
            Contradictions inside the SOP library itself, grouped by how they were detected.
          </p>
          {data.recurring_conflicts.length === 0 ? (
            <p className="py-6 text-center text-sm text-slate-500 dark:text-slate-400">
              No conflicts detected in the library.
            </p>
          ) : (
            <ul className="space-y-2">
              {data.recurring_conflicts.map((row, index) => (
                <li
                  key={`${row.type}-${index}`}
                  className="flex items-start justify-between gap-3 rounded-lg border border-slate-200 px-3 py-2 text-sm dark:border-slate-800"
                >
                  <span className="min-w-0">
                    <span className="block truncate font-medium text-slate-800 dark:text-slate-200">{row.title}</span>
                    <span className="text-xs text-slate-500 dark:text-slate-400">{humanize(row.type)}</span>
                  </span>
                  <span className="shrink-0 font-semibold tabular-nums">{row.count}</span>
                </li>
              ))}
            </ul>
          )}
          <Link to="/conflicts" className="btn-secondary mt-4 w-full">
            Open SOP Conflict Center
          </Link>
        </section>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <section className="card p-5 lg:col-span-2">
          <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">Analyses over time</h2>
          <p className="mb-3 mt-1 text-xs text-slate-500 dark:text-slate-400">
            Analyses started per day over the last {data.trend.length || 0} active day(s).
          </p>
          <Sparkline
            label="Analyses per day"
            points={data.trend.map((row) => ({ date: row.date, value: row.audits }))}
          />
        </section>

        <section className="card p-5">
          <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">Engine performance</h2>
          <dl className="mt-3 space-y-2 text-sm">
            {data.mode_performance.map((row) => (
              <div key={row.mode} className="flex items-center justify-between gap-3">
                <dt className="text-slate-600 dark:text-slate-400">{humanize(row.mode)}</dt>
                <dd className="font-medium tabular-nums text-slate-900 dark:text-slate-100">
                  {row.audits} runs · {row.avg_duration_ms.toFixed(0)} ms avg
                </dd>
              </div>
            ))}
            <div className="flex items-center justify-between gap-3 border-t border-slate-200 pt-2 dark:border-slate-800">
              <dt className="text-slate-600 dark:text-slate-400">Avg review turnaround</dt>
              <dd className="font-medium tabular-nums text-slate-900 dark:text-slate-100">
                {data.review_turnaround_hours_avg === null
                  ? 'No reviews closed yet'
                  : `${data.review_turnaround_hours_avg} h`}
              </dd>
            </div>
          </dl>
        </section>
      </div>

      <p className="rounded-lg bg-slate-100 px-4 py-3 text-xs text-slate-600 dark:bg-slate-900 dark:text-slate-400">
        {data.caveat}
      </p>
      </div>
      )}
    </PageShell>
  );
}
