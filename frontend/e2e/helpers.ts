import type { Page } from '@playwright/test';
import { expect } from '@playwright/test';

export const CREDENTIALS = {
  admin: { username: 'admin', password: 'Admin#12345' },
  auditor: { username: 'auditor', password: 'Auditor#12345' },
  reviewer: { username: 'reviewer', password: 'Reviewer#12345' },
};

/** Backend base URL for direct API assertions (must match the running server). */
const API = process.env.E2E_API_URL ?? 'http://127.0.0.1:8000';

/** The sidebar, so nav links never collide with same-named shortcuts in page content. */
export function nav(page: Page) {
  return page.getByRole('navigation', { name: 'Main navigation' });
}

export async function login(page: Page, role: keyof typeof CREDENTIALS) {
  const { username, password } = CREDENTIALS[role];
  await page.goto('/login');
  await page.getByLabel('Username').fill(username);
  await page.getByLabel('Password').fill(password);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible();
}

/**
 * Fail a test if the page logged a console error or threw. Catches the class of bug
 * that renders "something" but is actually broken.
 */
export function trackPageErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on('console', (message) => {
    if (message.type() !== 'error') return;
    const text = message.text();
    // "Failed to load resource" is the browser narrating an HTTP status the app
    // handles deliberately (401 on a bad password, 403 on a role check). Only
    // genuine application-level errors should fail a test.
    if (text.includes('Failed to load resource')) return;
    errors.push(`console.error: ${text}`);
  });
  page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`));
  return errors;
}

/**
 * Read the dashboard totals straight from the API.
 *
 * Specs assert that the UI reflects real data, so the expected values must come
 * from the same source the UI reads. Literals would instead encode a snapshot
 * of whatever state the database happened to be in when the spec was written.
 */
export async function fetchDashboardStats(page: Page): Promise<{
  sopDocuments: number;
  openConflicts: number;
  audits: number;
}> {
  const token = await page.evaluate(() => localStorage.getItem('sop-auditor-token'));
  const response = await page.request.get(`${API}/api/analytics/dashboard`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!response.ok()) {
    throw new Error(`dashboard stats request failed with ${response.status()}`);
  }
  const body = (await response.json()) as {
    totals: { sop_documents: number; open_conflicts: number; audits: number };
  };
  return {
    sopDocuments: body.totals.sop_documents,
    openConflicts: body.totals.open_conflicts,
    audits: body.totals.audits,
  };
}

/** Conflict counts by status, read from the API. */
export async function fetchConflictCounts(page: Page): Promise<Record<string, number>> {
  const token = await page.evaluate(() => localStorage.getItem('sop-auditor-token'));
  const response = await page.request.get(`${API}/api/conflicts?page_size=200`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!response.ok()) {
    throw new Error(`conflicts request failed with ${response.status()}`);
  }
  const body = (await response.json()) as { items: Array<{ status: string }> };
  return body.items.reduce<Record<string, number>>((acc, conflict) => {
    acc[conflict.status] = (acc[conflict.status] ?? 0) + 1;
    return acc;
  }, {});
}
