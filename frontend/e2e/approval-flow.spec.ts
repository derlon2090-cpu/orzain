import { expect, test } from '@playwright/test';

const entry = { kind: 'price_cross', indicator: 'PRICE', operator: 'crosses_above', value: '65000', timeframe: '5m', symbol: 'BTCUSDT' };
const exit = { kind: 'price_cross', indicator: 'PRICE', operator: 'crosses_below', value: '64000', timeframe: '5m', symbol: 'BTCUSDT' };

test('user reviews parsed rules and risk limits before explicitly approving a paper revision', async ({ page }) => {
  let savedAgent: any = null;
  let approvalCount = 0;
  await page.route('**/api/v1/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();
    if (path.endsWith('/auth/me')) return route.fulfill({ json: { id: 'user-1', email: 'trader@example.test' } });
    if (path.endsWith('/agents') && method === 'GET') return route.fulfill({ json: savedAgent ? [savedAgent] : [] });
    if (path.endsWith('/exchange-connections')) return route.fulfill({ json: [{ id: 'sandbox-1', exchange: 'binance', label: 'Testnet', environment: 'sandbox' }] });
    if (path.endsWith('/ai/interpret-strategy')) return route.fulfill({ json: { status: 'ready', entry_conditions: [entry], exit_conditions: [exit], missing_information: [], ambiguities: [], unsupported_instructions: [], explanation: 'Two explicit price-cross rules.' } });
    if (path.endsWith('/agents') && method === 'POST') {
      const body = request.postDataJSON();
      savedAgent = { id: 'agent-1', name: body.name, state: 'DRAFT', strategy_version: 1, approval_status: 'DRAFT', strategy: body.strategy };
      return route.fulfill({ status: 201, json: savedAgent });
    }
    if (path.endsWith('/transition')) {
      const body = request.postDataJSON();
      savedAgent.state = body.target;
      return route.fulfill({ json: savedAgent });
    }
    if (path.endsWith('/approve')) {
      expect(request.postDataJSON()).toEqual({ explicit_approval: true });
      approvalCount += 1;
      savedAgent = { ...savedAgent, state: 'PAPER_TRADING', approval_status: 'APPROVED', strategy_version: 2 };
      return route.fulfill({ json: savedAgent });
    }
    return route.fulfill({ status: 404, json: { detail: `Unexpected API request: ${method} ${path}` } });
  });

  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Your agents', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'New strategy' }).first().click();
  await page.getByLabel('Describe explicit entry and exit rules').fill('BTCUSDT crosses above 65000 on 5m; exit crosses below 64000 on 5m');
  await page.getByRole('button', { name: 'Interpret rules' }).click();
  await expect(page.getByText('Structured rules ready for your review')).toBeVisible();
  await page.getByLabel('Agent name').fill('BTC breakout');
  await page.getByLabel('Paper allocation (USDT)').fill('250');
  await page.getByLabel('Maximum trade risk (%)').fill('1');
  await page.getByLabel('Maximum daily loss (%)').fill('3');
  await page.getByLabel('Stop loss (%)').fill('2');
  await page.getByRole('button', { name: 'Save draft for review' }).click();
  await expect(page.getByText('Paper allocation (USDT): 250 USDT')).toBeVisible();
  await expect(page.getByText('Maximum trade risk (%): 1%')).toBeVisible();
  await expect(page.getByText('PRICE crosses above 65000')).toBeVisible();
  await page.getByRole('button', { name: 'Approve paper strategy' }).click();
  await expect.poll(() => approvalCount).toBe(1);
  await expect(page.getByText('BTC breakout')).toBeVisible();

  await page.getByRole('button', { name: 'العربية' }).click();
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByRole('heading', { name: 'وكلاؤك', exact: true })).toBeVisible();
});

test('ambiguous interpretation does not offer draft creation', async ({ page }) => {
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/auth/me')) return route.fulfill({ json: { id: 'user-1', email: 'trader@example.test' } });
    if (path.endsWith('/agents')) return route.fulfill({ json: [] });
    if (path.endsWith('/exchange-connections')) return route.fulfill({ json: [{ id: 'sandbox-1', exchange: 'binance', label: 'Testnet', environment: 'sandbox' }] });
    if (path.endsWith('/ai/interpret-strategy')) return route.fulfill({ json: { status: 'needs_clarification', entry_conditions: [], exit_conditions: [], missing_information: ['numeric threshold'], ambiguities: ['vague condition'], unsupported_instructions: [], explanation: 'Please specify a threshold.' } });
    return route.fulfill({ status: 404, json: { detail: 'Unexpected request' } });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'New strategy' }).first().click();
  await page.getByLabel('Describe explicit entry and exit rules').fill('buy when it rises a little');
  await page.getByRole('button', { name: 'Interpret rules' }).click();
  await expect(page.getByText('Clarification needed: Please specify a threshold.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Save draft for review' })).toHaveCount(0);
});
