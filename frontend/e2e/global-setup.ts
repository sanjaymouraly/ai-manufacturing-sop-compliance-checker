/**
 * Guarantees the preconditions the E2E suite needs, before any test runs.
 *
 * Why this exists
 * ---------------
 * `scripts/seed.py` deliberately creates no audits: a freshly seeded instance
 * should look like a real one on day one. But several specs need analyses that
 * already exist — one with findings, one escalated for review.
 *
 * Without this file those specs only passed when an earlier spec in the same
 * run happened to create suitable data, which made the suite order-dependent
 * and made a clean CI database fail where a developer's reused database
 * passed. Creating the fixtures up front removes that coupling: every spec can
 * assume the data it needs exists, and no spec depends on another's residue.
 *
 * This runs against the real API, so it exercises the same code path a user
 * would. It is idempotent — re-running it simply adds further analyses.
 */
import type { FullConfig } from '@playwright/test';

const API = process.env.E2E_API_URL ?? 'http://127.0.0.1:8000';

/** Number of reports to analyse. Enough to guarantee a varied outcome mix. */
const REPORTS_TO_ANALYSE = 12;

async function login(username: string, password: string): Promise<string> {
  const response = await fetch(`${API}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  if (!response.ok) {
    throw new Error(`E2E setup: login as ${username} failed with ${response.status}`);
  }
  const body = (await response.json()) as { access_token: string };
  return body.access_token;
}

export default async function globalSetup(_config: FullConfig): Promise<void> {
  // Fail loudly and early if the API is not up; a confusing cascade of
  // selector timeouts is a much worse diagnostic than one clear message.
  const health = await fetch(`${API}/api/health`).catch(() => null);
  if (!health || !health.ok) {
    throw new Error(
      `E2E setup: the API at ${API} is not reachable. Start it before running the suite.`,
    );
  }

  const token = await login('auditor', 'Auditor#12345');
  const auth = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' };

  const existing = await fetch(`${API}/api/audits?page_size=1`, { headers: auth });
  const existingBody = (await existing.json()) as { total?: number; items?: unknown[] };
  if ((existingBody.total ?? existingBody.items?.length ?? 0) > 0) {
    return; // Fixtures already present.
  }

  const reportsResponse = await fetch(`${API}/api/reports?page_size=${REPORTS_TO_ANALYSE}`, {
    headers: auth,
  });
  const reports = (await reportsResponse.json()) as { items: Array<{ id: number }> };

  let created = 0;
  for (const report of reports.items) {
    const response = await fetch(`${API}/api/audits`, {
      method: 'POST',
      headers: auth,
      body: JSON.stringify({ report_id: report.id, mode: 'deterministic' }),
    });
    if (response.ok) created += 1;
  }

  if (created === 0) {
    throw new Error('E2E setup: could not create any analyses; the suite cannot run.');
  }
  // eslint-disable-next-line no-console
  console.log(`E2E setup: created ${created} analyses as test fixtures.`);
}
