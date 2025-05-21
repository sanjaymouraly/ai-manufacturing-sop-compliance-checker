import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, ApiError } from '../lib/api';
import type { ProcessReportSummary } from '../lib/api';
import { useAsync } from '../lib/useAsync';
import { Spinner } from '../components/Spinner';
import { ErrorBanner } from '../components/ErrorBanner';
import { Badge } from '../components/Badge';
import { Disclaimer } from '../components/Disclaimer';
import { useToast } from '../components/Toast';
import { formatDate, humanize } from '../lib/format';

const STEPS = ['Choose a process report', 'Confirm analysis settings', 'Run the analysis'];

function StepHeader({ current }: { current: number }) {
  return (
    <ol className="flex flex-wrap gap-2" aria-label="Audit workflow progress">
      {STEPS.map((label, index) => {
        const state = index < current ? 'done' : index === current ? 'active' : 'todo';
        return (
          <li
            key={label}
            aria-current={state === 'active' ? 'step' : undefined}
            className={`flex items-center gap-2 rounded-lg px-3 py-2 text-sm ${
              state === 'active'
                ? 'bg-brand-600 text-white'
                : state === 'done'
                  ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200'
                  : 'bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400'
            }`}
          >
            <span
              aria-hidden="true"
              className={`grid h-5 w-5 place-items-center rounded-full text-xs font-bold ${
                state === 'active' ? 'bg-white/20' : state === 'done' ? 'bg-emerald-600 text-white' : 'bg-slate-300 dark:bg-slate-700'
              }`}
            >
              {state === 'done' ? '✓' : index + 1}
            </span>
            {label}
          </li>
        );
      })}
    </ol>
  );
}

export default function NewAudit() {
  const navigate = useNavigate();
  const { push } = useToast();
  const [step, setStep] = useState(0);
  const [selected, setSelected] = useState<ProcessReportSummary | null>(null);
  const [mode, setMode] = useState('deterministic');
  const [search, setSearch] = useState('');
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<unknown>(null);

  const [uploadBusy, setUploadBusy] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const { data, error, loading, reload } = useAsync(() => api.reports({ page_size: 200 }), []);

  const reports = (data?.items ?? []).filter((report) => {
    if (!search.trim()) return true;
    const needle = search.toLowerCase();
    return [report.report_code, report.title, report.batch_id, report.declared_sop_code, report.file_name]
      .filter(Boolean)
      .some((value) => String(value).toLowerCase().includes(needle));
  });

  async function uploadReport(file: File) {
    setUploadBusy(true);
    setUploadError(null);
    const form = new FormData();
    form.append('file', file);
    try {
      const report = await api.uploadReport(form);
      push('success', `Uploaded ${report.report_code} — ${report.event_count} events parsed.`);
      reload();
      setSelected(report);
    } catch (err) {
      const message = err instanceof ApiError ? err.message : String(err);
      setUploadError(
        err instanceof ApiError && err.status === 415
          ? `${message} Supported formats: PDF, DOCX, TXT, MD, CSV, JSON.`
          : message,
      );
    } finally {
      setUploadBusy(false);
    }
  }

  async function run() {
    if (!selected) return;
    setRunning(true);
    setRunError(null);
    try {
      const audit = await api.runAudit(selected.id, mode);
      push('success', `${audit.audit_code} complete — ${audit.findings.length} findings.`);
      navigate(`/audits/${audit.id}`);
    } catch (err) {
      setRunError(err);
      setRunning(false);
    }
  }

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">New audit</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Pick an executed process report. The engine decides which SOP revision governed that run before it
          compares anything.
        </p>
      </div>

      <StepHeader current={step} />
      <Disclaimer />

      {step === 0 && (
        <div className="space-y-4">
          <div className="card p-4">
            <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">Upload a new process report</h2>
            <p className="mb-3 mt-1 text-xs text-slate-500 dark:text-slate-400">
              PDF, DOCX, TXT, MD, CSV or JSON. The file is stored unchanged and treated as untrusted input.
            </p>
            <input
              type="file"
              aria-label="Process report file"
              accept=".pdf,.docx,.txt,.md,.csv,.json"
              disabled={uploadBusy}
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) void uploadReport(file);
                e.target.value = '';
              }}
              className="input file:mr-3 file:rounded file:border-0 file:bg-brand-600 file:px-3 file:py-1.5 file:text-sm file:text-white"
            />
            {uploadBusy && <p className="mt-2 text-sm text-slate-500">Parsing…</p>}
            {Boolean(uploadError) && <div className="mt-3"><ErrorBanner error={new Error(uploadError ?? '')} /></div>}
          </div>

          <div className="card overflow-hidden">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-4 py-3 dark:border-slate-800">
              <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                Existing reports {data ? `(${data.total})` : ''}
              </h2>
              <input
                className="input max-w-xs"
                placeholder="Filter reports…"
                aria-label="Filter process reports"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>

            {loading && <Spinner label="Loading reports" />}
            {Boolean(error) && <div className="p-4"><ErrorBanner error={error} onRetry={reload} /></div>}

            {data && (
              <ul className="max-h-[55vh] divide-y divide-slate-100 overflow-y-auto dark:divide-slate-800/70">
                {reports.map((report) => {
                  const isSelected = selected?.id === report.id;
                  const injected = report.injection_flags.length > 0;
                  return (
                    <li key={report.id}>
                      <button
                        type="button"
                        onClick={() => setSelected(report)}
                        aria-pressed={isSelected}
                        className={`flex w-full flex-wrap items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-slate-50 dark:hover:bg-slate-800/50 ${
                          isSelected ? 'bg-brand-50 dark:bg-brand-950/40' : ''
                        }`}
                      >
                        <span
                          aria-hidden="true"
                          className={`grid h-5 w-5 shrink-0 place-items-center rounded-full border-2 ${
                            isSelected ? 'border-brand-600 bg-brand-600 text-white' : 'border-slate-300 dark:border-slate-600'
                          }`}
                        >
                          {isSelected && <span className="text-[10px]">✓</span>}
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="flex flex-wrap items-center gap-2">
                            <span className="font-semibold text-slate-900 dark:text-slate-100">{report.report_code}</span>
                            {report.declared_sop_code && (
                              <Badge>
                                declares {report.declared_sop_code}
                                {report.declared_revision_code ? ` rev ${report.declared_revision_code}` : ''}
                              </Badge>
                            )}
                            {injected && (
                              <Badge className="bg-red-100 text-red-800 ring-red-600/20 dark:bg-red-950 dark:text-red-200 dark:ring-red-400/30">
                                injection flagged
                              </Badge>
                            )}
                            {report.extraction_confidence < 0.95 && (
                              <Badge className="bg-amber-100 text-amber-900 ring-amber-600/20 dark:bg-amber-950 dark:text-amber-200 dark:ring-amber-400/30">
                                extraction {(report.extraction_confidence * 100).toFixed(0)}%
                              </Badge>
                            )}
                          </span>
                          <span className="mt-0.5 block truncate text-sm text-slate-600 dark:text-slate-400">
                            {report.title || report.file_name}
                          </span>
                          <span className="mt-0.5 block text-xs text-slate-500 dark:text-slate-500">
                            {report.event_count} events · batch {report.batch_id || '—'} · executed{' '}
                            {report.executed_at ? formatDate(report.executed_at) : 'date unknown'}
                          </span>
                        </span>
                      </button>
                    </li>
                  );
                })}
                {reports.length === 0 && (
                  <li className="px-4 py-8 text-center text-sm text-slate-500 dark:text-slate-400">
                    No reports match “{search}”.
                  </li>
                )}
              </ul>
            )}
          </div>

          <div className="flex justify-end">
            <button type="button" className="btn-primary" disabled={!selected} onClick={() => setStep(1)}>
              Continue
            </button>
          </div>
        </div>
      )}

      {step === 1 && selected && (
        <div className="space-y-4">
          <section className="card p-5">
            <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">Selected report</h2>
            <dl className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {[
                ['Report code', selected.report_code],
                ['Title', selected.title || selected.file_name],
                ['Batch', selected.batch_id || '—'],
                ['Line', selected.line_id || '—'],
                ['Product', selected.product_code || '—'],
                ['Site', selected.site || '—'],
                ['Operator', selected.operator || '—'],
                ['Executed at', selected.executed_at ? formatDate(selected.executed_at) : 'not stated'],
                ['Declared SOP', selected.declared_sop_code
                  ? `${selected.declared_sop_code}${selected.declared_revision_code ? ` rev ${selected.declared_revision_code}` : ''}`
                  : 'not declared'],
                ['Parsed events', String(selected.event_count)],
              ].map(([label, value]) => (
                <div key={label}>
                  <dt className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">{label}</dt>
                  <dd className="mt-0.5 text-sm text-slate-900 dark:text-slate-100">{value}</dd>
                </div>
              ))}
            </dl>
            {selected.injection_flags.length > 0 && (
              <p className="mt-4 rounded-lg border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-900 dark:border-red-800 dark:bg-red-950 dark:text-red-100">
                This report contains text resembling instructions to the analysis engine. It will be quarantined,
                reported as a finding, and never followed.
              </p>
            )}
          </section>

          <section className="card p-5">
            <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">Analysis mode</h2>
            <p className="mb-3 mt-1 text-xs text-slate-500 dark:text-slate-400">
              Deterministic rules always run and always own every verifiable comparison. An AI provider can only
              add lower-confidence observations on top — it can never upgrade a finding to “confirmed”.
            </p>
            <div className="space-y-2">
              {[
                {
                  value: 'deterministic',
                  title: 'Deterministic only (offline)',
                  body: 'Rule-based engine. Fully reproducible, no network calls, no cost.',
                },
                {
                  value: 'hybrid',
                  title: 'Deterministic + AI assist',
                  body: 'Adds AI-assisted observations for narrative contradictions. Requires a configured provider; findings stay advisory.',
                },
              ].map((option) => (
                <label
                  key={option.value}
                  className={`flex cursor-pointer items-start gap-3 rounded-lg border p-3 transition-colors ${
                    mode === option.value
                      ? 'border-brand-500 bg-brand-50 dark:border-brand-500 dark:bg-brand-950/40'
                      : 'border-slate-200 hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-slate-800/50'
                  }`}
                >
                  <input
                    type="radio"
                    name="mode"
                    value={option.value}
                    checked={mode === option.value}
                    onChange={(e) => setMode(e.target.value)}
                    className="mt-1 h-4 w-4 text-brand-600 focus:ring-brand-500"
                  />
                  <span>
                    <span className="block text-sm font-medium text-slate-900 dark:text-slate-100">{option.title}</span>
                    <span className="block text-xs text-slate-500 dark:text-slate-400">{option.body}</span>
                  </span>
                </label>
              ))}
            </div>
          </section>

          <div className="flex justify-between">
            <button type="button" className="btn-secondary" onClick={() => setStep(0)}>
              Back
            </button>
            <button type="button" className="btn-primary" onClick={() => setStep(2)}>
              Continue
            </button>
          </div>
        </div>
      )}

      {step === 2 && selected && (
        <div className="space-y-4">
          <section className="card p-5">
            <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">Ready to run</h2>
            <p className="mt-2 text-sm text-slate-600 dark:text-slate-400">
              {selected.report_code} will be analysed in <strong>{humanize(mode)}</strong> mode. The pipeline runs:
            </p>
            <ol className="mt-3 grid gap-1.5 text-sm text-slate-600 sm:grid-cols-2 dark:text-slate-400">
              {[
                'Resolve which SOP revision governed this run',
                'Extract requirements from that revision',
                'Retrieve matching evidence from the report',
                'Compare parameters, sequence and records',
                'Detect contradictions and unsupported claims',
                'Verify every quote against stored source text',
                'Classify risk and decide on escalation',
              ].map((line, index) => (
                <li key={line} className="flex gap-2">
                  <span aria-hidden="true" className="text-brand-500">{index + 1}.</span>
                  {line}
                </li>
              ))}
            </ol>
          </section>

          {Boolean(runError) && <ErrorBanner error={runError} />}

          <div className="flex justify-between">
            <button type="button" className="btn-secondary" onClick={() => setStep(1)} disabled={running}>
              Back
            </button>
            <button type="button" className="btn-primary" onClick={run} disabled={running}>
              {running ? 'Analysing…' : 'Run analysis'}
            </button>
          </div>

          {running && <Spinner label="Running the audit pipeline" />}
        </div>
      )}
    </div>
  );
}
