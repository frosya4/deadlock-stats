import { test, expect } from '@playwright/test';

test('tabs support direct links, history, and persistent explicit theme', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'light' });
  await page.goto('/m/133162474');
  await expect(page.locator('#panel-overview')).toBeVisible();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await expect(page.locator('#ranking .rank-row')).toHaveCount(12);
  await expect(page.locator('#mvp-card')).toBeVisible();
  await page.locator('.tabs a[href="#gold"]').click();
  await expect(page).toHaveURL(/#gold$/);
  await expect(page.locator('#panel-players')).toBeHidden();
  await page.reload();
  await expect(page.locator('#panel-gold')).toBeVisible();
  await page.locator('#theme-toggle').click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await page.locator('.tabs a[href="#chat"]').click();
  await page.goBack();
  await expect(page.locator('#panel-gold')).toBeVisible();
});

test('mobile source comparison and enriched player card use backend values', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/m/133162474#gold');
  const data = await (await page.request.get('/api/matches/133162474')).json();
  await expect(page.locator('#gold-chart .u-over')).toBeVisible();
  await page.locator('#compare').check();
  await expect(page.locator('#gold-compare-chart .u-over')).toBeVisible();
  await page.locator('#source-player-0').selectOption(String(data.players[2].slot));
  const last = data.players[2].sourceTimeline.at(-1);
  const total = Object.entries(last).filter(([k]) => k !== 't').reduce((n, [,v]) => n + Number(v), 0);
  await expect.poll(() => page.locator('#gold-chart').evaluate((host: any) => host.plot.data[1].at(-1))).toBe(total);
  for (const tab of ['overview','players','gold','fights','chat']) {
    await page.locator('.tabs a[href="#' + tab + '"]').click();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
  }
  await page.locator('.tabs a[href="#overview"]').click();
  await page.locator('#ranking .fantasy').first().hover();
  await expect(page.locator('#ranking .fantasy-parts').first()).toBeVisible();
  await page.locator('#ranking .rank-player').first().click();
  await expect(page.locator('.hs-chart .u-over')).toBeVisible();
  await expect(page.locator('#player-content progress')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
  await page.screenshot({ path: 'test-results/v2-player-mobile.png', fullPage: true });
  await page.keyboard.press('Escape');
  await page.screenshot({ path: 'test-results/v2-overview-mobile.png', fullPage: true });
  expect(errors).toEqual([]);
});

test('old matches hide missing enrichment blocks', async ({ page }) => {
  await page.route('**/api/matches/133162474', async route => {
    const response = await route.fetch(); const data = await response.json();
    for (const p of data.players) {
      p.lane = p.lane?.id ?? 1;
      for (const key of ['sourceTimeline','hsTimeline','fantasy','grade','rank','mvp']) delete p[key];
    }
    delete data.match.mvpSlot; delete data.match.hsAverage;
    await route.fulfill({ json: data });
  });
  await page.goto('/m/133162474');
  await expect(page.locator('#ranking .rank-row')).toHaveCount(12);
  await expect(page.locator('#mvp-card, .fantasy, .grade, .lane')).toHaveCount(0);
  await page.locator('#ranking .rank-player').first().click();
  await expect(page.locator('#player-card')).toBeVisible();
  await expect(page.locator('.hs-chart')).toHaveCount(0);
});
