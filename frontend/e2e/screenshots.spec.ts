/**
 * Captures the screenshots used in README.md and docs/.
 *
 *   npx playwright test e2e/screenshots.spec.ts
 *
 * These are real screenshots of the running application against the seeded
 * synthetic dataset - nothing here is mocked or hand-drawn.
 */
import { expect, test } from '@playwright/test';
import { login, nav } from './helpers';

const DIR = '../docs/images';

test.describe.configure({ mode: 'serial' });

async function shoot(page: import('@playwright/test').Page, name: string) {
  await page.waitForTimeout(400); // let charts and lazy chunks settle
  // A fullPage capture stitches viewport-sized tiles together, and a
  // position:sticky header is painted wherever the page is currently
  // scrolled - which lands it in the middle of the image. Reset first.
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(150);
  await page.screenshot({ path: `${DIR}/${name}.png`, fullPage: true });
}

test('01 login', async ({ page }) => {
  await page.goto('/login');
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
  await shoot(page, '01-login');
});

test('02 dashboard', async ({ page }) => {
  await login(page, 'admin');
  await expect(page.getByRole('heading', { name: 'Analysis outcomes' })).toBeVisible();
  await shoot(page, '02-dashboard');
});

test('03 dashboard dark', async ({ page }) => {
  await login(page, 'admin');
  await page.getByRole('button', { name: /Switch to dark theme/ }).click();
  await expect(page.locator('html')).toHaveClass(/dark/);
  await shoot(page, '03-dashboard-dark');
});

test('04 sop library', async ({ page }) => {
  await login(page, 'auditor');
  await nav(page).getByRole('link', { name: 'SOP Library' }).click();
  await expect(page.getByRole('heading', { name: 'SOP Library' })).toBeVisible();
  await shoot(page, '04-sop-library');
});

test('05 document viewer with highlighting', async ({ page }) => {
  await login(page, 'auditor');
  await page.goto('/sops');
  await page.getByRole('link', { name: 'SOP-ASSY-101' }).click();
  await page.locator('ul li button').filter({ hasText: /SOP-ASSY-101:/ }).first().click();
  await expect(page.locator('[data-testid^="highlight-"]').first()).toBeVisible();
  await shoot(page, '05-document-viewer');
});

test('06 revision comparison', async ({ page }) => {
  await login(page, 'auditor');
  await page.goto('/sops');
  await page.getByRole('link', { name: 'SOP-ASSY-101' }).click();
  await page.getByRole('link', { name: 'Compare revisions' }).click();
  await expect(page.getByText(/compared to/)).toBeVisible();
  await shoot(page, '06-revision-comparison');
});

test('07 new audit workflow', async ({ page }) => {
  await login(page, 'auditor');
  await nav(page).getByRole('link', { name: 'New Audit' }).click();
  await page.locator('ul li button').filter({ hasText: /PR-/ }).first().click();
  await page.getByRole('button', { name: 'Continue' }).click();
  await expect(page.getByRole('heading', { name: 'Analysis mode' })).toBeVisible();
  await shoot(page, '07-new-audit');
});

test('08 audit results with evidence', async ({ page }) => {
  await login(page, 'auditor');
  await page.goto('/audits');
  // Choose an analysis that actually produced findings.
  const row = page
    .locator('tbody tr')
    .filter({ hasNot: page.locator('td:nth-child(5):text-is("0")') })
    .first();
  await row.locator('a').first().click();
  await expect(page.getByText(/Which SOP revision governed this run/)).toBeVisible();
  await page.locator('article').first().locator('button').first().click();
  await expect(page.getByText(/Evidence \(/).first()).toBeVisible();
  await shoot(page, '08-audit-results');
});

test('09 evidence in context', async ({ page }) => {
  await login(page, 'auditor');
  await page.goto('/audits');
  const row = page
    .locator('tbody tr')
    .filter({ hasNot: page.locator('td:nth-child(5):text-is("0")') })
    .first();
  await row.locator('a').first().click();
  const finding = page.locator('article').first();
  await finding.locator('button').first().click();
  await finding.getByRole('button', { name: 'Show in source' }).first().click();
  const dialog = page.getByRole('dialog', { name: 'Evidence in context' });
  await expect(dialog.locator('[data-testid^="highlight-"]').first()).toBeVisible();
  await page.screenshot({ path: `${DIR}/09-evidence-in-context.png` });
});

test('10 conflict center', async ({ page }) => {
  await login(page, 'reviewer');
  await nav(page).getByRole('link', { name: 'SOP Conflicts' }).click();
  await page.locator('li').filter({ hasText: /CONF-/ }).first().locator('button').first().click();
  await expect(page.getByText(/Source A/).first()).toBeVisible();
  await shoot(page, '10-conflict-center');
});

test('11 review queue', async ({ page }) => {
  await login(page, 'reviewer');
  await page.goto('/review');
  await expect(page.getByRole('heading', { name: /Review .* escalation queue/ })).toBeVisible();
  await page.locator('article').first().locator('button').first().click();
  await shoot(page, '11-review-queue');
});

test('12 export center', async ({ page }) => {
  await login(page, 'auditor');
  await nav(page).getByRole('link', { name: 'Export Center' }).click();
  await expect(page.getByRole('heading', { name: 'Export center' })).toBeVisible();
  await shoot(page, '12-export-center');
});

test('13 audit history', async ({ page }) => {
  await login(page, 'auditor');
  await page.goto('/audits');
  await expect(page.getByRole('heading', { name: 'Audit history' })).toBeVisible();
  await shoot(page, '13-audit-history');
});

test('14 ai provider settings', async ({ page }) => {
  await login(page, 'admin');
  await nav(page).getByRole('link', { name: 'AI Provider' }).click();
  await expect(page.getByRole('heading', { name: 'AI provider settings' })).toBeVisible();
  // Show the spend controls populated rather than at their defaults; the
  // token ceiling is the control that still works when a provider publishes
  // no pricing, so it is the one worth illustrating.
  const mock = process.env.MOCK_LLM_URL;
  await page.locator('#p-type').selectOption('custom');
  await page.locator('#p-name').fill(mock ? 'Local OpenAI-compatible endpoint' : 'Gateway');
  await page.locator('#p-url').fill(mock ? `${mock}/v1` : 'https://api.example-gateway.test/v1');
  await page.locator('#p-token-budget').fill('60000');

  if (mock) {
    // Discovering a real catalogue that carries no prices is what triggers
    // the warning, so capture the page in that state rather than empty.
    await page.locator('#p-key').fill('sk-not-a-real-key');
    await page.getByRole('button', { name: 'List', exact: true }).click();
    await expect(page.getByText(/publishes no pricing/i)).toBeVisible({ timeout: 20_000 });
    await page.waitForTimeout(5500); // let the toast clear the form
  }

  await shoot(page, '14-provider-settings');
});

test('15 settings and user management', async ({ page }) => {
  await login(page, 'admin');
  await page.goto('/settings');
  await expect(page.getByRole('heading', { name: /Users .* roles/ })).toBeVisible();
  await shoot(page, '15-settings-users');
});
