import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api, ApiError } from '../lib/api';
import { useAsync } from '../lib/useAsync';
import { useAuth } from '../lib/auth';
import { Spinner } from '../components/Spinner';
import { ErrorBanner } from '../components/ErrorBanner';
import { EmptyState } from '../components/EmptyState';
import { Badge } from '../components/Badge';
import { Modal } from '../components/Modal';
import { useToast } from '../components/Toast';
import { formatDate, humanize, STATUS_CLASSES } from '../lib/format';

function UploadDialog({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: () => void }) {
  const { push } = useToast();
  const [file, setFile] = useState<File | null>(null);
  const [sopCode, setSopCode] = useState('');
  const [revisionCode, setRevisionCode] = useState('');
  const [statusOverride, setStatusOverride] = useState('');
  const [effectiveDate, setEffectiveDate] = useState('');
  const [approvedBy, setApprovedBy] = useState('');
  const [supersedes, setSupersedes] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!file) return;
    setBusy(true);
    setError(null);
    const form = new FormData();
    form.append('file', file);
    if (sopCode) form.append('sop_code', sopCode);
    if (revisionCode) form.append('revision_code', revisionCode);
    if (statusOverride) form.append('status_override', statusOverride);
    if (effectiveDate) form.append('effective_date', effectiveDate);
    if (approvedBy) form.append('approved_by', approvedBy);
    if (supersedes) form.append('supersedes_revision_code', supersedes);
    try {
      const revision = await api.uploadDocument(form);
      push('success', `Ingested ${revision.file_name} — ${revision.requirement_count} requirements extracted.`);
      onDone();
      onClose();
      setFile(null);
    } catch (err) {
      const message = err instanceof ApiError ? err.message : String(err);
      setError(
        err instanceof ApiError && err.status === 415
          ? `${message} Supported formats: PDF, DOCX, TXT, MD, CSV, JSON.`
          : message,
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Upload an SOP revision"
      wide
      footer={
        <>
          <button type="button" className="btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" form="sop-upload-form" className="btn-primary" disabled={busy || !file}>
            {busy ? 'Ingesting…' : 'Upload and parse'}
          </button>
        </>
      }
    >
      <form id="sop-upload-form" onSubmit={submit} className="space-y-4">
        <p className="text-sm text-slate-600 dark:text-slate-400">
          Metadata is read from the document itself. Fill a field below only to override what the parser finds —
          the original file is always stored unchanged.
        </p>

        <div>
          <label className="label" htmlFor="sop-file">
            SOP file (PDF, DOCX, TXT, MD, CSV, JSON)
          </label>
          <input
            id="sop-file"
            type="file"
            required
            accept=".pdf,.docx,.txt,.md,.csv,.json"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="input file:mr-3 file:rounded file:border-0 file:bg-brand-600 file:px-3 file:py-1.5 file:text-sm file:text-white"
          />
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <label className="label" htmlFor="sop-code">SOP code override</label>
            <input id="sop-code" className="input" value={sopCode} onChange={(e) => setSopCode(e.target.value)} placeholder="e.g. ASSY-101" />
          </div>
          <div>
            <label className="label" htmlFor="rev-code">Revision code override</label>
            <input id="rev-code" className="input" value={revisionCode} onChange={(e) => setRevisionCode(e.target.value)} placeholder="e.g. 3" />
          </div>
          <div>
            <label className="label" htmlFor="status-override">Status override</label>
            <select id="status-override" className="input" value={statusOverride} onChange={(e) => setStatusOverride(e.target.value)}>
              <option value="">Use value from document</option>
              <option value="draft">Draft</option>
              <option value="approved">Approved</option>
              <option value="superseded">Superseded</option>
              <option value="archived">Archived</option>
            </select>
          </div>
          <div>
            <label className="label" htmlFor="effective-date">Effective date override</label>
            <input id="effective-date" type="date" className="input" value={effectiveDate} onChange={(e) => setEffectiveDate(e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="approved-by">Approved by override</label>
            <input id="approved-by" className="input" value={approvedBy} onChange={(e) => setApprovedBy(e.target.value)} placeholder="Name of approver" />
          </div>
          <div>
            <label className="label" htmlFor="supersedes">Supersedes revision</label>
            <input id="supersedes" className="input" value={supersedes} onChange={(e) => setSupersedes(e.target.value)} placeholder="e.g. 2" />
          </div>
        </div>

        <p className="rounded-lg bg-slate-100 px-3 py-2 text-xs text-slate-600 dark:bg-slate-800 dark:text-slate-300">
          Uploaded documents are treated as untrusted input. Any text that looks like an instruction to the
          analysis engine is flagged as a prompt-injection attempt and never acted upon.
        </p>

        {Boolean(error) && <ErrorBanner error={new Error(error ?? '')} />}
      </form>
    </Modal>
  );
}

export default function SopLibrary() {
  const { can } = useAuth();
  const { push } = useToast();
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  const [uploadOpen, setUploadOpen] = useState(false);
  const [scanning, setScanning] = useState(false);

  const { data, error, loading, reload } = useAsync(
    () => api.documents({ search: query || undefined, page_size: 100 }),
    [query],
  );

  async function rescan() {
    setScanning(true);
    try {
      const result = await api.scanConflicts();
      push('success', result.message);
      reload();
    } catch (err) {
      push('error', err instanceof Error ? err.message : 'Conflict scan failed');
    } finally {
      setScanning(false);
    }
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">SOP Library</h1>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
            Every revision ever uploaded is preserved. Superseded revisions stay available as evidence.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {can('administrator') && (
            <button type="button" className="btn-secondary" onClick={rescan} disabled={scanning}>
              {scanning ? 'Scanning…' : 'Re-scan for conflicts'}
            </button>
          )}
          {can('auditor') && (
            <button type="button" className="btn-primary" onClick={() => setUploadOpen(true)}>
              Upload SOP
            </button>
          )}
        </div>
      </div>

      <form
        className="flex flex-wrap gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          setQuery(search.trim());
        }}
      >
        <label className="sr-only" htmlFor="sop-search">
          Search SOPs
        </label>
        <input
          id="sop-search"
          className="input max-w-md"
          placeholder="Search by SOP code, title or owner…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <button type="submit" className="btn-secondary">
          Search
        </button>
        {query && (
          <button
            type="button"
            className="btn-ghost"
            onClick={() => {
              setSearch('');
              setQuery('');
            }}
          >
            Clear
          </button>
        )}
      </form>

      {loading && <Spinner label="Loading SOP library" />}
      {Boolean(error) && <ErrorBanner error={error} onRetry={reload} />}

      {data && data.items.length === 0 && (
        <EmptyState
          title="No SOP documents yet"
          description="Upload an approved SOP to start building the library, or run the seed script to load the synthetic dataset."
        />
      )}

      {data && data.items.length > 0 && (
        <div className="card overflow-hidden">
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-slate-200 dark:divide-slate-800">
              <caption className="sr-only">SOP documents with revision counts and conflict status</caption>
              <thead className="bg-slate-50 dark:bg-slate-900/60">
                <tr>
                  <th scope="col" className="th">SOP</th>
                  <th scope="col" className="th">Owner</th>
                  <th scope="col" className="th">Process area</th>
                  <th scope="col" className="th">Revisions</th>
                  <th scope="col" className="th">Governing</th>
                  <th scope="col" className="th">Status</th>
                  <th scope="col" className="th">Added</th>
                  <th scope="col" className="th"><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800/70">
                {data.items.map((doc) => (
                  <tr key={doc.id} className="hover:bg-slate-50 dark:hover:bg-slate-800/40">
                    <td className="td">
                      <Link to={`/sops/${doc.id}`} className="font-semibold text-brand-700 hover:underline dark:text-brand-300">
                        {doc.sop_code}
                      </Link>
                      <span className="block max-w-xs truncate text-xs text-slate-500 dark:text-slate-400">
                        {doc.title}
                      </span>
                    </td>
                    <td className="td">{doc.owner || '—'}</td>
                    <td className="td">{doc.process_area ? humanize(doc.process_area) : '—'}</td>
                    <td className="td tabular-nums">{doc.revision_count}</td>
                    <td className="td">
                      {doc.approved_revision_code ? (
                        <Badge className={STATUS_CLASSES.approved}>Rev {doc.approved_revision_code}</Badge>
                      ) : (
                        <Badge className={STATUS_CLASSES.draft}>No approved revision</Badge>
                      )}
                    </td>
                    <td className="td">
                      {doc.has_open_conflicts ? (
                        <Badge className={STATUS_CLASSES.conflicting} title="This SOP has an unresolved conflict that blocks conclusions">
                          Conflict open
                        </Badge>
                      ) : (
                        <Badge className={STATUS_CLASSES.approved}>Clear</Badge>
                      )}
                    </td>
                    <td className="td whitespace-nowrap text-xs">{formatDate(doc.created_at)}</td>
                    <td className="td text-right">
                      <Link to={`/sops/${doc.id}`} className="btn-ghost">
                        Open
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="border-t border-slate-200 px-3 py-2 text-xs text-slate-500 dark:border-slate-800 dark:text-slate-400">
            {data.total} document{data.total === 1 ? '' : 's'}
          </p>
        </div>
      )}

      <UploadDialog open={uploadOpen} onClose={() => setUploadOpen(false)} onDone={reload} />
    </div>
  );
}
