import { expect, test } from '@playwright/test';
import { fetchConflictCounts, fetchDashboardStats, login, nav, trackPageErrors } from './helpers';

test.describe('Authentication and shell', () => {
  test('rejects bad credentials and accepts good ones', async ({ page }) => {
    const errors = trackPageErrors(page);
    await page.goto('/login');

    await page.getByLabel('Username').fill('auditor');
    await page.getByLabel('Password').fill('wrong-password');
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page.getByRole('alert')).toContainText(/invalid|incorrect|credential/i);

    await page.getByLabel('Password').fill('Auditor#12345');
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible();
    expect(errors).toEqual([]);
  });

  test('a demo account button fills the form', async ({ page }) => {
    await page.goto('/login');
    await page.getByRole('button', { name: /Reviewer/ }).click();
    await expect(page.getByLabel('Username')).toHaveValue('reviewer');
  });

  test('theme toggle switches to dark mode and persists', async ({ page }) => {
    await login(page, 'auditor');
    const html = page.locator('html');
    const wasDark = await html.evaluate((el) => el.classList.contains('dark'));
    await page.getByRole('button', { name: /Switch to (light|dark) theme/ }).first().click();
    await expect(html).toHaveClass(wasDark ? /^(?!.*dark).*$/ : /dark/);
    await page.reload();
    await expect(html).toHaveClass(wasDark ? /^(?!.*dark).*$/ : /dark/);
  });

  test('signing out returns to the login screen', async ({ page }) => {
    await login(page, 'auditor');
    await page.getByRole('button', { name: 'Sign out' }).click();
    await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
  });
});

test.describe('Dashboard', () => {
  test('shows real counts and the decision-support disclaimer', async ({ page }) => {
    const errors = trackPageErrors(page);
    await login(page, 'auditor');

    await expect(page.getByText(/does not certify compliance/i).first()).toBeVisible();

    // Assert against what the API actually reports rather than a literal. A
    // hard-coded count silently encodes whatever another spec left in the
    // database, and then fails on a freshly seeded one.
    const stats = await fetchDashboardStats(page);
    const sopCard = page.getByRole('link', { name: /SOP documents/ });
    await expect(sopCard).toContainText(String(stats.sopDocuments));
    await expect(page.getByRole('link', { name: /Open SOP conflicts/ })).toContainText(
      String(stats.openConflicts),
    );
    await expect(page.getByRole('heading', { name: 'Analysis outcomes' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Findings by classification' })).toBeVisible();
    expect(errors).toEqual([]);
  });
});

test.describe('SOP library and document viewer', () => {
  test('lists SOPs, flags conflicts, and searches', async ({ page }) => {
    const errors = trackPageErrors(page);
    await login(page, 'auditor');
    await nav(page).getByRole('link', { name: 'SOP Library' }).click();

    await expect(page.getByRole('heading', { name: 'SOP Library' })).toBeVisible();
    await expect(page.getByRole('cell', { name: 'SOP-ASSY-101', exact: false }).first()).toBeVisible();
    await expect(page.getByText('Conflict open').first()).toBeVisible();

    await page.getByLabel('Search SOPs').fill('PKG-220');
    await page.getByRole('button', { name: 'Search', exact: true }).click();
    await expect(page.getByRole('link', { name: 'SOP-PKG-220' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'SOP-ASSY-101' })).toHaveCount(0);
    expect(errors).toEqual([]);
  });

  test('document viewer highlights extracted requirements in the source text', async ({ page }) => {
    const errors = trackPageErrors(page);
    await login(page, 'auditor');
    await page.goto('/sops');
    await page.getByRole('link', { name: 'SOP-ASSY-101' }).click();

    await expect(page.getByRole('heading', { name: 'SOP-ASSY-101' })).toBeVisible();
    await expect(page.getByRole('heading', { name: /Revision history/ })).toBeVisible();

    // Clicking a requirement scrolls to and marks its exact stored character span.
    const firstRequirement = page.locator('ul li button', { hasText: /SOP-ASSY-101:/ }).first();
    await firstRequirement.click();
    await expect(page.locator('[data-testid^="highlight-"]').first()).toBeVisible();
    expect(errors).toEqual([]);
  });

  test('revision comparison reports real requirement-level deltas', async ({ page }) => {
    const errors = trackPageErrors(page);
    await login(page, 'auditor');
    await page.goto('/sops');
    await page.getByRole('link', { name: 'SOP-ASSY-101' }).click();
    await page.getByRole('link', { name: 'Compare revisions' }).click();

    await expect(page.getByRole('heading', { name: 'Compare revisions' })).toBeVisible();
    await expect(page.getByText(/compared to/)).toBeVisible();
    // At least one change badge must be present between two different revisions.
    await expect(
      page.locator('article, li').filter({ hasText: /Added|Removed|Limit changed|Modified|Reordered/ }).first(),
    ).toBeVisible();
    expect(errors).toEqual([]);
  });
});

test.describe('Audit workflow', () => {
  test('an auditor runs an analysis and sees evidence-backed findings', async ({ page }) => {
    const errors = trackPageErrors(page);
    await login(page, 'auditor');
    await nav(page).getByRole('link', { name: 'New Audit' }).click();

    await expect(page.getByRole('heading', { name: 'New audit' })).toBeVisible();
    await page.locator('ul li button').filter({ hasText: /PR-/ }).first().click();
    await page.getByRole('button', { name: 'Continue' }).click();

    await expect(page.getByRole('heading', { name: 'Selected report' })).toBeVisible();
    await page.getByRole('button', { name: 'Continue' }).click();

    await expect(page.getByRole('heading', { name: 'Ready to run' })).toBeVisible();
    await page.getByRole('button', { name: 'Run analysis' }).click();

    await expect(page.getByRole('heading', { name: /^AUD-/ })).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText(/Which SOP revision governed this run/)).toBeVisible();
    await expect(page.getByText(/does not certify compliance/i).first()).toBeVisible();
    expect(errors).toEqual([]);
  });

  test('a finding exposes its verified quote and opens the source in context', async ({ page }) => {
    const errors = trackPageErrors(page);
    await login(page, 'auditor');
    await page.goto('/audits');

    // Pick an analysis that actually produced findings.
    const row = page.locator('tbody tr').filter({ hasNot: page.locator('td:nth-child(5):text-is("0")') }).first();
    await row.locator('a').first().click();
    await expect(page.getByRole('heading', { name: /^AUD-/ })).toBeVisible();

    const finding = page.locator('article').first();
    await finding.locator('button').first().click();
    await expect(finding.getByText(/Evidence \(/)).toBeVisible();
    await expect(finding.getByText('verified against source').first()).toBeVisible();

    await finding.getByRole('button', { name: 'Show in source' }).first().click();
    const dialog = page.getByRole('dialog', { name: 'Evidence in context' });
    await expect(dialog).toBeVisible();
    await expect(dialog.locator('[data-testid^="highlight-"]').first()).toBeVisible();
    await dialog.getByRole('button', { name: 'Close', exact: true }).click();
    expect(errors).toEqual([]);
  });

  test('audit history filters by outcome', async ({ page }) => {
    const errors = trackPageErrors(page);
    await login(page, 'auditor');
    await page.goto('/audits');
    await expect(page.getByRole('heading', { name: 'Audit history' })).toBeVisible();

    await page.getByLabel('Outcome').selectOption('human_review_required');
    await page.getByRole('button', { name: 'Apply' }).click();
    const badges = page.locator('tbody tr td:nth-child(4)');
    const count = await badges.count();
    for (let i = 0; i < count; i += 1) {
      await expect(badges.nth(i)).toContainText('Human Review Required');
    }
    expect(errors).toEqual([]);
  });
});

test.describe('Conflict Center', () => {
  test('shows unresolved conflicts with both sides of the evidence', async ({ page }) => {
    const errors = trackPageErrors(page);
    await login(page, 'reviewer');
    await nav(page).getByRole('link', { name: 'SOP Conflicts' }).click();

    await expect(page.getByRole('heading', { name: 'SOP Conflict Center' })).toBeVisible();
    const conflict = page.locator('li').filter({ hasText: /CONF-/ }).first();
    await conflict.locator('button').first().click();

    await expect(page.getByText(/Source A/).first()).toBeVisible();
    await expect(page.getByText(/Source B/).first()).toBeVisible();
    await expect(page.getByRole('button', { name: 'Resolve this conflict' }).first()).toBeVisible();
    expect(errors).toEqual([]);
  });

  test('the status filter really filters', async ({ page }) => {
    await login(page, 'reviewer');
    await page.goto('/conflicts');

    // Expected counts come from the API, not from literals: how many conflicts
    // are resolved depends on what reviewers have done, and a fixed number
    // would only describe one particular database.
    const counts = await fetchConflictCounts(page);
    const resolved = counts['resolved'] ?? 0;
    const needsAttention = (counts['escalated'] ?? 0) + (counts['open'] ?? 0);

    await page.getByRole('button', { name: 'Resolved' }).click();
    await expect(page.locator('li').filter({ hasText: /CONF-/ })).toHaveCount(resolved);

    await page.getByRole('button', { name: 'Needs attention' }).click();
    await expect(page.locator('li').filter({ hasText: /CONF-/ })).toHaveCount(needsAttention);

    // The two filters must partition the set, not overlap or lose rows.
    await page.getByRole('button', { name: 'All' }).click();
    await expect(page.locator('li').filter({ hasText: /CONF-/ })).toHaveCount(
      resolved + needsAttention,
    );
  });

  test('resolution requires a documented rationale', async ({ page }) => {
    await login(page, 'reviewer');
    await page.goto('/conflicts');
    const conflict = page.locator('li').filter({ hasText: /CONF-/ }).first();
    await conflict.locator('button').first().click();
    await page.getByRole('button', { name: 'Resolve this conflict' }).first().click();

    const dialog = page.getByRole('dialog');
    const submit = dialog.getByRole('button', { name: 'Record resolution' });
    await expect(submit).toBeDisabled();
    await dialog.getByLabel(/Documented rationale/).fill('Checked with document control.');
    await expect(submit).toBeEnabled();
  });
});

test.describe('AI provider spend controls', () => {
  test('warns that a USD cap cannot bound a provider with no published prices', async ({
    page,
  }) => {
    // Runs against scripts/mock_llm_server.py, which serves an
    // OpenAI-compatible catalogue with no pricing fields - the same shape real
    // gateways and self-hosted endpoints return. No credentials, no cost.
    const mock = process.env.MOCK_LLM_URL;
    test.skip(!mock, 'set MOCK_LLM_URL to run this (scripts/mock_llm_server.py)');

    await login(page, 'admin');
    await page.goto('/providers');
    await expect(page.locator('#p-type')).toBeVisible();

    // Nothing has been discovered yet, so no claim should be made either way.
    await expect(page.getByText(/publishes no pricing/i)).toHaveCount(0);

    await page.locator('#p-type').selectOption('custom');
    await page.locator('#p-url').fill(`${mock}/v1`);
    await page.locator('#p-key').fill('sk-not-a-real-key');
    await page.getByRole('button', { name: 'List', exact: true }).click();

    await expect(page.getByText(/publishes no pricing/i)).toBeVisible({ timeout: 20_000 });

    // The ceiling that still works must be present and editable.
    const ceiling = page.locator('#p-token-budget');
    await expect(ceiling).toBeVisible();
    await ceiling.fill('25000');
    await expect(ceiling).toHaveValue('25000');
  });
});

test.describe('Role-based access control', () => {
  test('an auditor cannot reach AI provider settings', async ({ page }) => {
    await login(page, 'auditor');
    await expect(nav(page).getByRole('link', { name: 'AI Provider' })).toHaveCount(0);
    await page.goto('/providers');
    await expect(page.getByRole('heading', { name: 'Not available for your role' })).toBeVisible();
  });

  test('an auditor cannot record a review decision on a finding', async ({ page }) => {
    await login(page, 'auditor');
    await page.goto('/review');
    await expect(page.getByRole('heading', { name: /Review .* escalation queue/ })).toBeVisible();
    await expect(page.getByText(/cannot sign off their own findings/).first()).toBeVisible();
    await expect(page.getByRole('button', { name: 'Record decision' })).toHaveCount(0);
  });

  test('a reviewer can record a decision, and it is appended to the trail', async ({ page }) => {
    const errors = trackPageErrors(page);
    await login(page, 'reviewer');
    await page.goto('/review');

    const finding = page.locator('article').first();
    const code = await finding.locator('code').first().innerText();
    await finding.locator('button').first().click(); // expand to reveal the evidence
    await finding.getByRole('button', { name: 'Record decision' }).click();

    const dialog = page.getByRole('dialog');
    await dialog.getByLabel(/Rationale/).fill('Evidence reviewed against the governing revision; accepted.');
    await dialog.getByRole('button', { name: 'Record decision' }).click();
    await expect(page.getByRole('status')).toContainText(code);
    expect(errors).toEqual([]);
  });

  test('an administrator can open provider settings and sees no secret values', async ({ page }) => {
    const errors = trackPageErrors(page);
    await login(page, 'admin');
    await nav(page).getByRole('link', { name: 'AI Provider' }).click();

    await expect(page.getByRole('heading', { name: 'AI provider settings' })).toBeVisible();
    await expect(page.getByText('Keys are write-only.')).toBeVisible();
    await expect(page.getByLabel(/API key/)).toHaveValue('');
    await expect(page.getByLabel(/API key/)).toHaveAttribute('type', 'password');
    expect(errors).toEqual([]);
  });

  test('an administrator can manage users; a non-admin cannot', async ({ page }) => {
    await login(page, 'admin');
    await page.goto('/settings');
    await expect(page.getByRole('heading', { name: /Users .* roles/ })).toBeVisible();

    await page.getByRole('button', { name: 'Sign out' }).click();
    await login(page, 'auditor');
    await page.goto('/settings');
    await expect(page.getByRole('heading', { name: /Users .* roles/ })).toHaveCount(0);
  });
});

test.describe('Export center', () => {
  test('generates a JSON export and lists it with a hash', async ({ page }) => {
    const errors = trackPageErrors(page);
    await login(page, 'auditor');
    await nav(page).getByRole('link', { name: 'Export Center' }).click();

    await expect(page.getByRole('heading', { name: 'Export center' })).toBeVisible();
    await page.getByLabel('Analysis to export').selectOption({ index: 1 });
    await page.getByRole('button', { name: 'Generate JSON' }).click();

    await expect(page.getByRole('status')).toContainText(/Created .*\.json/);
    await expect(page.getByRole('heading', { name: 'Generated exports' })).toBeVisible();
    await expect(page.locator('tbody tr').first()).toContainText('JSON');
    expect(errors).toEqual([]);
  });
});

test.describe('Accessibility basics', () => {
  test('every page exposes one h1, a skip link and a labelled nav', async ({ page }) => {
    await login(page, 'admin');
    for (const path of ['/', '/sops', '/audits', '/conflicts', '/review', '/exports', '/providers', '/settings']) {
      await page.goto(path);
      await expect(page.locator('h1')).toHaveCount(1);
      await expect(page.getByRole('navigation', { name: 'Main navigation' })).toBeAttached();
      await expect(page.getByRole('link', { name: 'Skip to main content' })).toBeAttached();
    }
  });

  test('the app is keyboard navigable from the skip link', async ({ page }) => {
    await login(page, 'auditor');
    await page.keyboard.press('Tab');
    await expect(page.getByRole('link', { name: 'Skip to main content' })).toBeFocused();
  });
});
