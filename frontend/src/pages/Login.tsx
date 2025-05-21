import { useState } from 'react';
import { useAuth } from '../lib/auth';
import { useTheme } from '../lib/theme';
import { Disclaimer } from '../components/Disclaimer';

const DEMO_ACCOUNTS = [
  { role: 'Administrator', username: 'admin', password: 'Admin#12345', note: 'Full access incl. provider config' },
  { role: 'Auditor', username: 'auditor', password: 'Auditor#12345', note: 'Uploads and runs analyses' },
  { role: 'Reviewer', username: 'reviewer', password: 'Reviewer#12345', note: 'Signs off findings and conflicts' },
];

export default function LoginPage() {
  const { login } = useAuth();
  const { theme, toggle } = useTheme();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username, password);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sign in failed');
    } finally {
      setBusy(false);
    }
  }

  function fill(user: string, pass: string) {
    setUsername(user);
    setPassword(pass);
  }

  return (
    <div className="flex min-h-full items-center justify-center bg-slate-100 p-4 dark:bg-slate-950">
      <div className="w-full max-w-5xl overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-xl dark:border-slate-800 dark:bg-slate-900 lg:grid lg:grid-cols-2">
        <div className="hidden flex-col justify-between bg-brand-700 p-10 text-white lg:flex">
          <div>
            <div className="mb-6 flex items-center gap-3">
              <span className="grid h-10 w-10 place-items-center rounded-xl bg-white/15 text-lg font-bold">✓</span>
              <span className="text-lg font-semibold">SOP Compliance Auditor</span>
            </div>
            <h1 className="text-3xl font-bold leading-tight">
              Every finding carries a quote you can trace back to the page it came from.
            </h1>
            <p className="mt-4 text-sm leading-relaxed text-brand-100">
              Upload approved SOPs and executed process reports. The engine resolves which revision
              actually governed the run, compares every requirement deterministically, verifies each
              citation against stored text, and escalates anything it cannot settle on its own.
            </p>
          </div>
          <ul className="space-y-3 text-sm text-brand-100">
            {[
              'Deterministic rules own every verifiable comparison',
              'Unverifiable quotes are blocked from “confirmed” status',
              'Unresolved SOP conflicts force human review',
              'Works fully offline — no AI provider required',
            ].map((line) => (
              <li key={line} className="flex items-start gap-2">
                <span aria-hidden="true" className="mt-0.5 text-brand-300">
                  ●
                </span>
                <span>{line}</span>
              </li>
            ))}
          </ul>
        </div>

        <div className="p-8 sm:p-10">
          <div className="mb-6 flex items-start justify-between">
            <div>
              <h2 className="text-xl font-semibold text-slate-900 dark:text-slate-100">Sign in</h2>
              <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
                Use a demo account below or your own credentials.
              </p>
            </div>
            <button type="button" onClick={toggle} className="btn-ghost px-2" aria-label="Toggle theme">
              {theme === 'dark' ? '☀' : '☾'}
            </button>
          </div>

          <form onSubmit={submit} className="space-y-4">
            <div>
              <label className="label" htmlFor="username">
                Username
              </label>
              <input
                id="username"
                name="username"
                className="input"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                required
                autoFocus
              />
            </div>
            <div>
              <label className="label" htmlFor="password">
                Password
              </label>
              <input
                id="password"
                name="password"
                type="password"
                className="input"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                required
              />
            </div>

            {Boolean(error) && (
              <p
                role="alert"
                className="rounded-lg border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200"
              >
                {error}
              </p>
            )}

            <button type="submit" className="btn-primary w-full" disabled={busy}>
              {busy ? 'Signing in…' : 'Sign in'}
            </button>
          </form>

          <div className="mt-6">
            <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Demo accounts
            </p>
            <div className="space-y-2">
              {DEMO_ACCOUNTS.map((account) => (
                <button
                  key={account.username}
                  type="button"
                  onClick={() => fill(account.username, account.password)}
                  className="flex w-full items-center justify-between gap-3 rounded-lg border border-slate-200 px-3 py-2 text-left text-sm hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-slate-800"
                >
                  <span>
                    <span className="font-medium text-slate-900 dark:text-slate-100">{account.role}</span>
                    <span className="block text-xs text-slate-500 dark:text-slate-400">{account.note}</span>
                  </span>
                  <code className="shrink-0 rounded bg-slate-100 px-2 py-1 text-xs text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                    {account.username}
                  </code>
                </button>
              ))}
            </div>
          </div>

          <div className="mt-6">
            <Disclaimer compact />
          </div>
        </div>
      </div>
    </div>
  );
}
