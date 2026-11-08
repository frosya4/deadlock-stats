import { test, expect } from '@playwright/test';
import { mkdir } from 'node:fs/promises';

test('loads match', async ({ page }) => {
  await page.goto('/m/133162474#players');
  await expect(page.locator('h1')).toContainText('133162474');
  await expect(page.locator('#players tbody tr')).toHaveCount(12);
  await expect(page.locator('#players')).toContainText('Игрок 8');
  await expect(page.locator('#players')).toContainText('1 852');
  await expect(page.locator('#timeline .u-over')).toBeVisible();
  for (const title of ['Получено золота', 'Объекты', 'Карта смертей', 'Урон по героям (кто → кому)', 'Точность и прочее', 'Билды и прокачка', 'Хронология убийств', 'Чат']) {
    const tab = title === 'Получено золота' ? 'gold' : title === 'Чат' ? 'chat' : title === 'Билды и прокачка' ? 'players' : 'fights';
    await page.locator('.tabs a[href="#' + tab + '"]').click();
    await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible();
  }
  await expect(page.locator('meta[name="robots"]')).toHaveAttribute('content', 'noindex');
  await mkdir('../../docs/img', { recursive: true });
  await page.locator('.tabs a[href="#overview"]').click();
  await page.screenshot({ path: '../../docs/img/match-desktop.png', fullPage: true });
});

test('toggle player line', async ({ page }) => {
  await page.goto('/m/133162474#players');
  const checkbox = page.getByRole('checkbox', { name: 'Линия Игрок 8', exact: true });
  await expect(checkbox).toBeChecked();
  await checkbox.uncheck();
  await expect.poll(() => page.evaluate(() => {
    const chart = (document.querySelector('#timeline') as any).plot;
    return chart.series.find((s: any) => s.label === 'Игрок 8').show;
  })).toBe(false);
  await page.getByRole('button', { name: 'Amber', exact: true }).click();
  await expect(page.locator('#player-legend input:checked')).toHaveCount(6);
  await page.getByRole('button', { name: 'Все', exact: true }).click();
  await expect(page.locator('#player-legend input:checked')).toHaveCount(12);
  await page.getByLabel('Метрика').selectOption('gpm');
  await expect.poll(() => page.evaluate(() => (document.querySelector('#timeline') as any).plot.data[1].some(Number.isFinite))).toBe(true);
  await page.getByRole('button', { name: 'Суммы команд', exact: true }).click();
  await expect.poll(() => page.evaluate(() => (document.querySelector('#timeline') as any).plot.series.length)).toBe(3);
});

test('tooltip shows 12 values', async ({ page }) => {
  await page.goto('/m/133162474#players');
  await page.locator('#timeline .u-over').hover({ position: { x: 220, y: 100 } });
  await expect(page.locator('#timeline-tooltip [data-value]')).toHaveCount(12);
  const values = await page.locator('#timeline-tooltip [data-value]').evaluateAll(rows => rows.map(r => Number((r as HTMLElement).dataset.value)));
  expect(values).toEqual([...values].sort((a, b) => b - a));
  await page.getByRole('checkbox', { name: 'Линия Игрок 8', exact: true }).uncheck();
  await page.locator('#timeline .u-over').hover({ position: { x: 220, y: 100 } });
  await expect(page.locator('#timeline-tooltip [data-value]')).toHaveCount(11);
});

test('player card opens', async ({ page }) => {
  await page.goto('/m/133162474#players');
  await page.locator('#players').getByRole('button', { name: 'Игрок 8', exact: true }).click();
  const card = page.getByRole('dialog');
  await expect(card).toBeVisible();
  await expect(card.getByRole('heading', { name: 'Игрок 8 — Виктор' })).toBeVisible();
  for (const text of ['Билд по времени', 'Способности', 'Смерти', 'Урон кому / от кого']) await expect(card.getByRole('heading', { name: text, exact: true })).toBeVisible();
  await expect(card.locator('svg circle')).toHaveCount(1);
  await expect(card.locator('.sold').first()).toBeAttached();
  await page.keyboard.press('Escape');
  await expect(card).not.toBeVisible();
});

test('escapes player names', async ({ page }) => {
  let dialogs = 0;
  page.on('dialog', async d => { dialogs++; await d.dismiss(); });
  await page.goto('/m/133162475#players');
  const name = '<img src=x onerror=alert(1)>';
  await expect(page.locator('#players').getByRole('button', { name, exact: true })).toBeVisible();
  await page.locator('#players').getByRole('button', { name, exact: true }).click();
  await expect(page.getByRole('dialog')).toContainText(name);
  await expect(page.locator('img[onerror], img[src="x"]')).toHaveCount(0);
  await expect(page.locator('#players img').first()).toHaveAttribute('src', /^\/img\/heroes\/\d+\.png$/);
  expect(dialogs).toBe(0);
});

test('upload shows progress then redirects', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('#matches')).toContainText('133162474');
  await page.locator('input[type=file]').setInputFiles({ name: 'test.dem', mimeType: 'application/octet-stream', buffer: Buffer.concat([Buffer.from('PBDEMS2\0'), Buffer.alloc(100000)]) });
  await expect(page.getByRole('progressbar')).toBeVisible();
  await expect(page.getByRole('status')).toContainText(/Загрузка|Очередь|Обработка/);
  await expect(page).toHaveURL(/\/m\/u[0-9a-f]{12}$/, { timeout: 30000 });
  await expect(page.locator('#players tbody tr')).toHaveCount(12);
  await expect(page.getByRole('link', { name: 'Официальная версия: /m/133162474' })).toHaveAttribute('href', '/m/133162474');
  const officialRequest = page.waitForRequest(request => request.url().endsWith('/api/fetch') && request.method() === 'POST');
  await page.getByRole('button', { name: 'Скачать официальную запись у Valve' }).click();
  expect((await officialRequest).postDataJSON()).toEqual({ matchId: '133162474' });
  await expect(page).toHaveURL(/\/m\/133162474$/);
});

test('mobile no horizontal scroll', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/m/133162474#players');
  await expect(page.locator('#timeline .u-over')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.locator('#players').getByRole('button', { name: 'Игрок 8', exact: true }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.keyboard.press('Escape');
  await mkdir('../../docs/img', { recursive: true });
  await page.screenshot({ path: '../../docs/img/match-mobile-390.png', fullPage: true });
});


test('duplicate names retain slot identity', async ({ page }) => {
  await page.goto('/m/133162476#fights');
  const rows = page.locator('#damage tbody tr');
  await expect(rows).toHaveCount(12);
  const hero = await page.locator('#players tbody tr').nth(1).locator('button').nth(1).evaluate(node => Array.from(node.childNodes).filter(n => n.nodeType === Node.TEXT_NODE).map(n => n.textContent).join(''));
  await rows.nth(1).getByRole('button').click();
  await expect(page.getByRole('dialog').locator('h2')).toContainText(hero);
});
