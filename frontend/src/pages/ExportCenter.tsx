import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api, downloadExport } from '../lib/api';
import { useAsync } from '../lib/useAsync';
import { useAuth } from '../lib/auth';
import { Spinner } from '../components/Spinner';
import { ErrorBanner } from '../components/ErrorBanner';
import { EmptyState } from '../components/EmptyState';
import { Badge } from '../components/Badge';
import { Disclaimer } from '../components/Disclaimer';
import { useToast } from '../components/Toast';
import { formatDate, humanize, OUTCOME_CLASSES } from '../lib/format';

const FORMATS = [
  { id: 'pdf', label: 'PDF', hint: 'Formatted report for circulation and sign-off packs.' },
  { id: 'json', label: 'JSON', hint: 'Full machine-readable record including every citation and offset.' },
  { id: 'csv', label: 'CSV', hint: 'One row per finding, for spreadsheets and trend analysis.' },
];

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

export default function ExportCenter() {
  const { can } = useAuth();
  const { push } = useToast();
  const [selectedAudit, setSelectedAudit] = useState<number | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const { data: audits, loading: auditsLoading } = useAsync(() => api.audits({ page_size: 100 }), []);
  const { data: artifacts, error, loading, reload } = useAsync(() => api.exports({ page_size: 100 }), []);

  async function generate(format: string) {
    if (!selectedAudit) return;
    setBusy(format);
    try {
      const artifact = await api.createExport(selectedAudit, format);
      push('success', `Created ${artifact.file_name} (version ${artifact.version}).`);
      reload();
    } catch (err) {
      push('error', err instanceof Error ? err.message : 'Export failed');
    } finally {
      setBusy(null);
    }
  }

  async function download(auditId: number, format: string, fileName: string) {
    try {
      await downloadExport(auditId, format, fileName);
    } catch (err) {
      push('error', err instanceof Error ? err.message : 'Download failed');
    }
  }

  const chosen = audits?.items.find((a) => a.id === selectedAudit) ?? null;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">Export center</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Generate and re-download versioned audit reports. Every export carries the disclaimer, the resolved
          revision with its hash, and the full citation list.
        </p>
      </div>

      <Disclaimer />

      {can('auditor') && (
        <section className="card p-5">
          <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">Create a new export</h2>

          {auditsLoading && <Spinner label="Loading analyses" />}

          {audits && (
            <div className="mt-3 space-y-4">
              <div>
                <label className="label" htmlFor="export-audit">Analysis to export</label>
                <select
                  id="export-audit"
                  className="input max-w-xl"
                  value={selectedAudit ?? ''}
                  onChange={(e) => setSelectedAudit(e.target.value ? Number(e.target.value) : null)}
                >
                  <option value="">Choose an analysis…</option>
                  {audits.items.map((audit) => (
                    <option key={audit.id} value={audit.id}>
                      {audit.audit_code} — {audit.report_code} — {humanize(audit.outcome)} ({audit.finding_count} findings)
                    </option>
                  ))}
                </select>
              </div>

              {chosen && (
                <div className="flex flex-wrap items-center gap-2 rounded-lg bg-slate-50 px-3 py-2 text-sm dark:bg-slate-800/60">
                  <Badge className={OUTCOME_CLASSES[chosen.outcome] ?? ''}>{humanize(chosen.outcome)}</Badge>
                  <span className="text-slate-700 dark:text-slate-300">
                    {chosen.sop_code ? `${chosen.sop_code} rev ${chosen.revision_code}` : 'no resolved revision'} ·{' '}
                    {chosen.finding_count} findings
                  </span>
                  <Link to={`/audits/${chosen.id}`} className="ml-auto text-brand-700 underline dark:text-brand-300">
                    Open analysis
                  </Link>
                </div>
              )}

              <div className="grid gap-3 sm:grid-cols-3">
                {FORMATS.map((format) => (
                  <div key={format.id} className="rounded-lg border border-slate-200 p-4 dark:border-slate-700">
                    <p className="text-sm font-semibold text-slate-900 dark:text-slate-100">{format.label}</p>
                    <p className="mb-3 mt-1 text-xs text-slate-500 dark:text-slate-400">{format.hint}</p>
                    <button
                      type="button"
                      className="btn-primary w-full"
                      disabled={!selectedAudit || busy !== null}
                      onClick={() => generate(format.id)}
                    >
                      {busy === format.id ? 'Generating…' : `Generate ${format.label}`}
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </section>
      )}

      <section className="space-y-3">
        <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">Generated exports</h2>

        {loading && <Spinner label="Loading exports" />}
        {Boolean(error) && <ErrorBanner error={error} onRetry={reload} />}

        {artifacts && artifacts.items.length === 0 && (
          <EmptyState
            title="No exports yet"
            description="Generate one above, or export directly from an audit results page."
          />
        )}

        {artifacts && artifacts.items.length > 0 && (
          <div className="card overflow-hidden">
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-slate-200 dark:divide-slate-800">
                <caption className="sr-only">Previously generated export artefacts</caption>
                <thead className="bg-slate-50 dark:bg-slate-900/60">
                  <tr>
                    <th scope="col" className="th">File</th>
                    <th scope="col" className="th">Analysis</th>
                    <th scope="col" className="th">Format</th>
                    <th scope="col" className="th">Version</th>
                    <th scope="col" className="th">Size</th>
                    <th scope="col" className="th">SHA-256</th>
                    <th scope="col" className="th">Created</th>
                    <th scope="col" className="th"><span className="sr-only">Download</span></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-800/70">
                  {artifacts.items.map((artifact) => (
                    <tr key={artifact.id} className="hover:bg-slate-50 dark:hover:bg-slate-800/40">
                      <td className="td font-mono text-xs">{artifact.file_name}</td>
                      <td className="td">
                        <Link
                          to={`/audits/${artifact.audit_id}`}
                          className="text-brand-700 hover:underline dark:text-brand-300"
                        >
                          {artifact.audit_code ?? `#${artifact.audit_id}`}
                        </Link>
                      </td>
                      <td className="td"><Badge>{artifact.format.toUpperCase()}</Badge></td>
                      <td className="td tabular-nums">v{artifact.version}</td>
                      <td className="td whitespace-nowrap tabular-nums">{formatBytes(artifact.file_size)}</td>
                      <td className="td font-mono text-[11px] text-slate-500 dark:text-slate-400">
                        {artifact.sha256.slice(0, 16)}…
                      </td>
                      <td className="td whitespace-nowrap text-xs">
                        {formatDate(artifact.created_at)}
                        {artifact.created_by && (
                          <span className="block text-slate-400 dark:text-slate-500">by {artifact.created_by}</span>
                        )}
                      </td>
                      <td className="td text-right">
                        <button
                          type="button"
                          className="btn-ghost"
                          onClick={() => download(artifact.audit_id, artifact.format, artifact.file_name)}
                        >
                          Download
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="border-t border-slate-200 px-3 py-2 text-xs text-slate-500 dark:border-slate-800 dark:text-slate-400">
              Exports are versioned per analysis — regenerating never overwrites an earlier file.
            </p>
          </div>
        )}
      </section>
    </div>
  );
}
