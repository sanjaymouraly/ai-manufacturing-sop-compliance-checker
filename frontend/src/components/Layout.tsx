import { useState } from 'react';
import type { ReactNode } from 'react';
import { NavLink, useNavigate } from 'react-router-dom';
import { useAuth } from '../lib/auth';
import { useTheme } from '../lib/theme';
import { humanize } from '../lib/format';

interface NavItem {
  to: string;
  label: string;
  icon: ReactNode;
  roles?: string[];
  end?: boolean;
}

const icon = (path: string) => (
  <svg
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.8"
    strokeLinecap="round"
    strokeLinejoin="round"
    className="h-[18px] w-[18px] shrink-0"
    aria-hidden="true"
  >
    <path d={path} />
  </svg>
);

const NAV: NavItem[] = [
  { to: '/', label: 'Dashboard', end: true, icon: icon('M3 13h8V3H3v10zm0 8h8v-6H3v6zm10 0h8V11h-8v10zm0-18v6h8V3h-8z') },
  { to: '/sops', label: 'SOP Library', icon: icon('M4 19.5A2.5 2.5 0 016.5 17H20M4 19.5A2.5 2.5 0 016.5 22H20V2H6.5A2.5 2.5 0 004 4.5v15z') },
  { to: '/audits/new', label: 'New Audit', roles: ['administrator', 'auditor'], icon: icon('M12 5v14M5 12h14') },
  { to: '/audits', label: 'Audit History', icon: icon('M3 3v5h5M3.05 13A9 9 0 106 5.3L3 8M12 7v5l4 2') },
  { to: '/conflicts', label: 'SOP Conflicts', icon: icon('M10.3 3.9L1.8 18a2 2 0 001.7 3h17a2 2 0 001.7-3L14.7 3.9a2 2 0 00-3.4 0zM12 9v4M12 17h.01') },
  { to: '/review', label: 'Review Queue', icon: icon('M9 11l3 3L22 4M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11') },
  { to: '/exports', label: 'Export Center', icon: icon('M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3') },
  { to: '/providers', label: 'AI Provider', roles: ['administrator'], icon: icon('M12 2a3 3 0 013 3v1.2a6 6 0 013 5.2V17l2 2H4l2-2v-5.6a6 6 0 013-5.2V5a3 3 0 013-3z') },
  { to: '/settings', label: 'Settings', icon: icon('M12 15a3 3 0 100-6 3 3 0 000 6zM19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 11-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 11-4 0v-.09A1.65 1.65 0 008 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 11-2.83-2.83l.06-.06A1.65 1.65 0 004.6 15a1.65 1.65 0 00-1.51-1H3a2 2 0 110-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 112.83-2.83l.06.06A1.65 1.65 0 009 4.6a1.65 1.65 0 001-1.51V3a2 2 0 114 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 112.83 2.83l-.06.06A1.65 1.65 0 0019.4 9c.14.63.67 1.1 1.32 1.1H21a2 2 0 110 4h-.09a1.65 1.65 0 00-1.51 1z') },
];

export function Layout({ children }: { children: ReactNode }) {
  const { user, logout, can } = useAuth();
  const { theme, toggle } = useTheme();
  const navigate = useNavigate();
  const [mobileOpen, setMobileOpen] = useState(false);

  const visible = NAV.filter((item) => !item.roles || can(...(item.roles as never[])));

  const navLinks = (
    <nav className="flex flex-col gap-1" aria-label="Main navigation">
      {visible.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.end}
          onClick={() => setMobileOpen(false)}
          className={({ isActive }) =>
            `flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
              isActive
                ? 'bg-brand-600 text-white'
                : 'text-slate-700 hover:bg-slate-200/70 dark:text-slate-300 dark:hover:bg-slate-800'
            }`
          }
        >
          {item.icon}
          <span>{item.label}</span>
        </NavLink>
      ))}
    </nav>
  );

  return (
    <div className="flex min-h-screen flex-col">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-50 focus:rounded-lg focus:bg-brand-600 focus:px-4 focus:py-2 focus:text-white"
      >
        Skip to main content
      </a>

      <header className="sticky top-0 z-30 border-b border-slate-200 bg-white/90 backdrop-blur dark:border-slate-800 dark:bg-slate-900/90">
        <div className="flex h-14 items-center gap-3 px-4">
          <button
            type="button"
            className="btn-ghost px-2 lg:hidden"
            onClick={() => setMobileOpen((v) => !v)}
            aria-expanded={mobileOpen}
            aria-label="Toggle navigation menu"
          >
            <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
              <path d="M3 6h18M3 12h18M3 18h18" strokeLinecap="round" />
            </svg>
          </button>

          <div className="flex items-center gap-2.5">
            <span
              aria-hidden="true"
              className="grid h-8 w-8 place-items-center rounded-lg bg-brand-600 text-sm font-bold text-white"
            >
              ✓
            </span>
            <div className="leading-tight">
              <p className="text-sm font-semibold text-slate-900 dark:text-slate-100">SOP Compliance Auditor</p>
              <p className="hidden text-[11px] text-slate-500 dark:text-slate-400 sm:block">
                Evidence-first decision support — not a certification
              </p>
            </div>
          </div>

          <div className="ml-auto flex items-center gap-2">
            <button
              type="button"
              onClick={toggle}
              className="btn-ghost px-2"
              aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
              title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
            >
              {theme === 'dark' ? (
                <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
                  <circle cx="12" cy="12" r="4" />
                  <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" strokeLinecap="round" />
                </svg>
              ) : (
                <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
                  <path d="M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z" strokeLinejoin="round" />
                </svg>
              )}
            </button>

            {user && (
              <div className="flex items-center gap-3 border-l border-slate-200 pl-3 dark:border-slate-800">
                <div className="hidden text-right leading-tight sm:block">
                  <p className="text-sm font-medium text-slate-900 dark:text-slate-100">
                    {user.full_name || user.username}
                  </p>
                  <p className="text-[11px] text-slate-500 dark:text-slate-400">{humanize(user.role)}</p>
                </div>
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => {
                    logout();
                    navigate('/login');
                  }}
                >
                  Sign out
                </button>
              </div>
            )}
          </div>
        </div>
      </header>

      <div className="flex flex-1">
        {user && (
          <>
            <div className="hidden shrink-0 self-stretch border-r border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900 lg:block">
              <div className="sticky top-14 max-h-[calc(100vh-3.5rem)] w-60 overflow-y-auto p-3">{navLinks}</div>
            </div>
            {mobileOpen && (
              <div className="fixed inset-0 z-20 lg:hidden">
                <div className="absolute inset-0 bg-slate-900/50" onClick={() => setMobileOpen(false)} aria-hidden="true" />
                <aside className="relative h-full w-64 border-r border-slate-200 bg-white p-3 dark:border-slate-800 dark:bg-slate-900">
                  {navLinks}
                </aside>
              </div>
            )}
          </>
        )}

        <main id="main" className="min-w-0 flex-1 p-4 sm:p-6">
          {children}
        </main>
      </div>
    </div>
  );
}
