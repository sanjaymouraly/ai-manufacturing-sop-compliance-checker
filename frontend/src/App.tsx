import { Suspense, lazy } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { Layout } from './components/Layout';
import { Spinner } from './components/Spinner';
import { useAuth } from './lib/auth';
import type { Role } from './lib/api';
import LoginPage from './pages/Login';

const Dashboard = lazy(() => import('./pages/Dashboard'));
const SopLibrary = lazy(() => import('./pages/SopLibrary'));
const DocumentViewer = lazy(() => import('./pages/DocumentViewer'));
const RevisionCompare = lazy(() => import('./pages/RevisionCompare'));
const NewAudit = lazy(() => import('./pages/NewAudit'));
const AuditResults = lazy(() => import('./pages/AuditResults'));
const AuditHistory = lazy(() => import('./pages/AuditHistory'));
const ConflictCenter = lazy(() => import('./pages/ConflictCenter'));
const ReviewQueue = lazy(() => import('./pages/ReviewQueue'));
const ExportCenter = lazy(() => import('./pages/ExportCenter'));
const ProviderSettings = lazy(() => import('./pages/ProviderSettings'));
const Settings = lazy(() => import('./pages/Settings'));

function Protected({ children, roles }: { children: JSX.Element; roles?: Role[] }) {
  const { user, loading, can } = useAuth();
  if (loading) return <Spinner label="Checking your session" />;
  if (!user) return <Navigate to="/login" replace />;
  if (roles && !can(...roles)) {
    return (
      <div className="card p-6">
        <h1 className="text-lg font-semibold">Not available for your role</h1>
        <p className="mt-2 text-sm text-slate-600 dark:text-slate-400">
          Your account is a <strong>{user.role}</strong>. This screen is restricted to:{' '}
          {roles.join(', ')}. Ask an administrator if you need access.
        </p>
      </div>
    );
  }
  return children;
}

export default function App() {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner label="Starting" />
      </div>
    );
  }

  return (
    <Routes>
      <Route path="/login" element={user ? <Navigate to="/" replace /> : <LoginPage />} />
      <Route
        path="/*"
        element={
          <Layout>
            <Suspense fallback={<Spinner />}>
              <Routes>
                <Route path="/" element={<Protected><Dashboard /></Protected>} />
                <Route path="/sops" element={<Protected><SopLibrary /></Protected>} />
                <Route path="/sops/:documentId" element={<Protected><DocumentViewer /></Protected>} />
                <Route
                  path="/sops/:documentId/compare"
                  element={<Protected><RevisionCompare /></Protected>}
                />
                <Route
                  path="/audits/new"
                  element={<Protected roles={['auditor']}><NewAudit /></Protected>}
                />
                <Route path="/audits" element={<Protected><AuditHistory /></Protected>} />
                <Route path="/audits/:auditId" element={<Protected><AuditResults /></Protected>} />
                <Route path="/conflicts" element={<Protected><ConflictCenter /></Protected>} />
                <Route path="/review" element={<Protected><ReviewQueue /></Protected>} />
                <Route path="/exports" element={<Protected><ExportCenter /></Protected>} />
                <Route
                  path="/providers"
                  element={<Protected roles={['administrator']}><ProviderSettings /></Protected>}
                />
                <Route path="/settings" element={<Protected><Settings /></Protected>} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
            </Suspense>
          </Layout>
        }
      />
    </Routes>
  );
}
