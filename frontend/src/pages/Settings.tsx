import { useState } from 'react';
import { api } from '../lib/api';
import { useAsync } from '../lib/useAsync';
import { useAuth } from '../lib/auth';
import { useTheme } from '../lib/theme';
import { Spinner } from '../components/Spinner';
import { ErrorBanner } from '../components/ErrorBanner';
import { Badge } from '../components/Badge';
import { Modal } from '../components/Modal';
import { useToast } from '../components/Toast';
import { formatDate, humanize } from '../lib/format';

const ROLE_HELP: Record<string, string> = {
  administrator: 'Everything, including user management and AI provider configuration.',
  auditor: 'Uploads documents and reports, runs analyses and generates exports. Cannot sign off their own findings.',
  reviewer: 'Reviews findings, resolves SOP conflicts and records decisions. Cannot change system configuration.',
  viewer: 'Read-only access to the library, analyses and exports.',
};

function UserManagement() {
  const { push } = useToast();
  const { user: me } = useAuth();
  const { data: users, error, loading, reload } = useAsync(() => api.users(), []);

  const [open, setOpen] = useState(false);
  const [username, setUsername] = useState('');
  const [fullName, setFullName] = useState('');
  const [email, setEmail] = useState('');
  const [role, setRole] = useState('auditor');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<unknown>(null);

  async function create() {
    setBusy(true);
    setFormError(null);
    try {
      await api.createUser({ username, full_name: fullName || null, email: email || null, role, password });
      push('success', `Created ${username}.`);
      setOpen(false);
      setUsername('');
      setFullName('');
      setEmail('');
      setPassword('');
      reload();
    } catch (err) {
      setFormError(err);
    } finally {
      setBusy(false);
    }
  }

  async function toggleActive(id: number, isActive: boolean, name: string) {
    try {
      await api.updateUser(id, { is_active: !isActive });
      push('success', `${name} ${isActive ? 'deactivated' : 'reactivated'}.`);
      reload();
    } catch (err) {
      push('error', err instanceof Error ? err.message : 'Update failed');
    }
  }

  async function changeRole(id: number, newRole: string, name: string) {
    try {
      await api.updateUser(id, { role: newRole });
      push('success', `${name} is now ${humanize(newRole)}.`);
      reload();
    } catch (err) {
      push('error', err instanceof Error ? err.message : 'Update failed');
    }
  }

  return (
    <section className="card p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">Users &amp; roles</h2>
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
            Separation of duties is enforced in the API, not just in this screen.
          </p>
        </div>
        <button type="button" className="btn-primary" onClick={() => setOpen(true)}>
          Add user
        </button>
      </div>

      {loading && <Spinner label="Loading users" />}
      {Boolean(error) && <div className="mt-3"><ErrorBanner error={error} onRetry={reload} /></div>}

      {users && (
        <div className="mt-4 overflow-x-auto">
          <table className="min-w-full divide-y divide-slate-200 dark:divide-slate-800">
            <caption className="sr-only">User accounts and their roles</caption>
            <thead>
              <tr>
                <th scope="col" className="th">User</th>
                <th scope="col" className="th">Role</th>
                <th scope="col" className="th">Status</th>
                <th scope="col" className="th">Last sign-in</th>
                <th scope="col" className="th"><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800/70">
              {users.map((account) => (
                <tr key={account.id}>
                  <td className="td">
                    <span className="font-medium text-slate-900 dark:text-slate-100">{account.username}</span>
                    {account.full_name && (
                      <span className="block text-xs text-slate-500 dark:text-slate-400">{account.full_name}</span>
                    )}
                  </td>
                  <td className="td">
                    <select
                      className="input max-w-[170px] py-1 text-xs"
                      value={account.role}
                      aria-label={`Role for ${account.username}`}
                      onChange={(e) => changeRole(account.id, e.target.value, account.username)}
                      disabled={account.id === me?.id}
                    >
                      {['administrator', 'auditor', 'reviewer', 'viewer'].map((value) => (
                        <option key={value} value={value}>{humanize(value)}</option>
                      ))}
                    </select>
                  </td>
                  <td className="td">
                    {account.is_active ? (
                      <Badge className="bg-emerald-100 text-emerald-800 ring-emerald-600/20 dark:bg-emerald-950 dark:text-emerald-200 dark:ring-emerald-400/30">
                        active
                      </Badge>
                    ) : (
                      <Badge>inactive</Badge>
                    )}
                  </td>
                  <td className="td whitespace-nowrap text-xs">{formatDate(account.last_login_at)}</td>
                  <td className="td text-right">
                    <button
                      type="button"
                      className="btn-ghost text-xs"
                      disabled={account.id === me?.id}
                      onClick={() => toggleActive(account.id, account.is_active, account.username)}
                    >
                      {account.is_active ? 'Deactivate' : 'Reactivate'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <dl className="mt-5 space-y-2 border-t border-slate-200 pt-4 text-xs dark:border-slate-800">
        {Object.entries(ROLE_HELP).map(([key, help]) => (
          <div key={key} className="flex gap-2">
            <dt className="w-28 shrink-0 font-semibold text-slate-700 dark:text-slate-300">{humanize(key)}</dt>
            <dd className="text-slate-500 dark:text-slate-400">{help}</dd>
          </div>
        ))}
      </dl>

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="Add a user"
        footer={
          <>
            <button type="button" className="btn-secondary" onClick={() => setOpen(false)}>Cancel</button>
            <button
              type="button"
              className="btn-primary"
              onClick={create}
              disabled={busy || username.length < 3 || password.length < 8}
            >
              {busy ? 'Creating…' : 'Create user'}
            </button>
          </>
        }
      >
        <div>
          <label className="label" htmlFor="u-name">Username</label>
          <input id="u-name" className="input" value={username} onChange={(e) => setUsername(e.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="u-full">Full name</label>
          <input id="u-full" className="input" value={fullName} onChange={(e) => setFullName(e.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="u-email">Email</label>
          <input id="u-email" type="email" className="input" value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="u-role">Role</label>
          <select id="u-role" className="input" value={role} onChange={(e) => setRole(e.target.value)}>
            {['administrator', 'auditor', 'reviewer', 'viewer'].map((value) => (
              <option key={value} value={value}>{humanize(value)}</option>
            ))}
          </select>
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{ROLE_HELP[role]}</p>
        </div>
        <div>
          <label className="label" htmlFor="u-pass">Initial password (min 8 characters)</label>
          <input id="u-pass" type="password" className="input" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" />
        </div>
        {Boolean(formError) && <ErrorBanner error={formError} />}
      </Modal>
    </section>
  );
}

function ChangePassword() {
  const { push } = useToast();
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (next !== confirm) {
      setError(new Error('The new passwords do not match.'));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await api.changePassword(current, next);
      push('success', result.message);
      setCurrent('');
      setNext('');
      setConfirm('');
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card p-5">
      <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">Change your password</h2>
      <form onSubmit={submit} className="mt-4 max-w-sm space-y-3">
        <div>
          <label className="label" htmlFor="pw-current">Current password</label>
          <input id="pw-current" type="password" className="input" value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" required />
        </div>
        <div>
          <label className="label" htmlFor="pw-new">New password</label>
          <input id="pw-new" type="password" className="input" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" minLength={8} required />
        </div>
        <div>
          <label className="label" htmlFor="pw-confirm">Confirm new password</label>
          <input id="pw-confirm" type="password" className="input" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" minLength={8} required />
        </div>
        {Boolean(error) && <ErrorBanner error={error} />}
        <button type="submit" className="btn-primary" disabled={busy}>
          {busy ? 'Updating…' : 'Update password'}
        </button>
      </form>
    </section>
  );
}

export default function Settings() {
  const { user, can } = useAuth();
  const { theme, toggle } = useTheme();
  const { data: settings } = useAsync(() => api.settings(), []);
  const { data: health } = useAsync(() => api.health(), []);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">Settings</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Your account, appearance, and the runtime configuration this instance is using.
        </p>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <section className="card p-5">
          <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">Your account</h2>
          <dl className="mt-3 space-y-2 text-sm">
            {[
              ['Username', user?.username],
              ['Full name', user?.full_name || '—'],
              ['Email', user?.email || '—'],
              ['Role', humanize(user?.role)],
              ['Last sign-in', formatDate(user?.last_login_at)],
            ].map(([label, value]) => (
              <div key={String(label)} className="flex justify-between gap-3">
                <dt className="text-slate-500 dark:text-slate-400">{label}</dt>
                <dd className="font-medium text-slate-900 dark:text-slate-100">{value}</dd>
              </div>
            ))}
          </dl>
          <p className="mt-3 rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-600 dark:bg-slate-800/60 dark:text-slate-400">
            {ROLE_HELP[user?.role ?? 'viewer']}
          </p>
        </section>

        <section className="card p-5">
          <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">Appearance</h2>
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
            Your choice is remembered in this browser.
          </p>
          <button type="button" className="btn-secondary mt-3" onClick={toggle}>
            Switch to {theme === 'dark' ? 'light' : 'dark'} theme
          </button>
        </section>
      </div>

      <ChangePassword />

      {can('administrator') && <UserManagement />}

      <section className="card p-5">
        <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">Runtime configuration</h2>
        <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
          Read-only. Secrets are never exposed through this endpoint.
        </p>
        <div className="mt-3 grid gap-4 sm:grid-cols-2">
          <dl className="space-y-1.5 text-sm">
            {settings &&
              Object.entries(settings).map(([key, value]) => (
                <div key={key} className="flex justify-between gap-3">
                  <dt className="text-slate-500 dark:text-slate-400">{humanize(key)}</dt>
                  <dd className="truncate font-medium text-slate-900 dark:text-slate-100">
                    {typeof value === 'boolean' ? (value ? 'yes' : 'no') : String(value)}
                  </dd>
                </div>
              ))}
          </dl>
          <dl className="space-y-1.5 text-sm">
            {health &&
              Object.entries(health).map(([key, value]) => (
                <div key={key} className="flex justify-between gap-3">
                  <dt className="text-slate-500 dark:text-slate-400">{humanize(key)}</dt>
                  <dd className="truncate font-medium text-slate-900 dark:text-slate-100">
                    {typeof value === 'object' ? JSON.stringify(value) : String(value)}
                  </dd>
                </div>
              ))}
          </dl>
        </div>
      </section>
    </div>
  );
}
