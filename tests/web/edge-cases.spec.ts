import { test, expect } from '@playwright/test';

test('gold uses cumulative snapshots and follows the selected players', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/m/133162474');
  await expect(page.locator('#gold-chart .u-over')).toBeVisible();
  for (const checkbox of await page.locator('#player-legend input').all()) await checkbox.uncheck();
  await expect(page.locator('#gold-legend')).toContainText('Выберите хотя бы одного игрока');
  await page.getByRole('checkbox', { name: 'Линия Игрок 1', exact: true }).check();
  const heroGold = () => page.evaluate(() => {
    const chart = (document.querySelector('#gold-chart') as any).plot;
    return chart.data[chart.series.findIndex((s: any) => s.label === 'Герои')].at(-1);
  });
  await expect.poll(heroGold).toBe(5956);
  for (const metric of ['kills', 'heroDamage', 'level', 'gpm', 'netWorth']) {
    await page.getByLabel('Метрика').selectOption(metric);
    await expect(page.locator('#timeline canvas')).toBeVisible();
  }
  await expect.poll(heroGold).toBe(5956);
  expect(errors).toEqual([]);
});

test('upload displays queue position and API error then permits a retry', async ({ page }) => {
  // Stub only scheduling here to deterministically observe a queued job/error.
  // The required upload test exercises the real worker and fake dldump.
  await page.route('**/api/upload', route => route.fulfill({ status: 202, json: { job: 'queue-test' } }));
  let polls = 0;
  await page.route('**/api/jobs/queue-test', route => route.fulfill({ json: ++polls === 1 ? { state: 'queued', position: 3 } : { state: 'error', error: 'Не удалось разобрать демку (возможно, вышел патч игры)' } }));
  await page.goto('/');
  await page.locator('#file').setInputFiles({ name: 'test.dem', mimeType: 'application/octet-stream', buffer: Buffer.from('PBDEMS2\0abcdefgh') });
  await expect(page.getByRole('status')).toContainText('Очередь: позиция 3');
  await expect(page.getByRole('status')).toContainText('Не удалось разобрать демку (возможно, вышел патч игры)');
  await expect(page.locator('#file')).toBeEnabled();
  await page.locator('#file').setInputFiles({ name: 'bad.dem', mimeType: 'application/octet-stream', buffer: Buffer.from('not demo') });
  await expect(page.getByRole('status')).toHaveText('Это не демка Deadlock');
});

test('drag and drop handles duplicate result and server upload rejection', async ({ page }) => {
  await page.goto('/');
  await page.route('**/api/upload', route => route.fulfill({ status: 413, json: { error: 'Файл слишком большой' } }));
  const drop = async () => {
    const transfer = await page.evaluateHandle(() => {
      const data = new DataTransfer(); data.items.add(new File(['PBDEMS2\0abcdefgh'], 'drop.dem')); return data;
    });
    await page.locator('#drop-zone').dispatchEvent('drop', { dataTransfer: transfer });
    await transfer.dispose();
  };
  await drop();
  await expect(page.getByRole('status')).toHaveText('Файл слишком большой');
  await page.unroute('**/api/upload');
  await page.route('**/api/upload', route => route.fulfill({ json: { matchId: '133162474', duplicate: true } }));
  await drop();
  await expect(page).toHaveURL(/\/m\/133162474$/);
});

test('dark mobile layout fits after resizing and a missing match shows API error', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.goto('/m/133162474');
  await expect(page.locator('#timeline .u-over')).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
  await expect.poll(() => page.evaluate(() => {
    const host = document.querySelector('#timeline') as any;
    return Math.abs(host.plot.width - host.clientWidth);
  })).toBeLessThanOrEqual(1);
  await page.goto('/m/999999999');
  await expect(page.getByRole('status')).toHaveText('Файл не найден');
  await page.goto('/');
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
});

test('network failures use Russian recovery messages', async ({ page }) => {
  await page.route('**/api/matches', route => route.abort('failed'));
  await page.goto('/');
  await expect(page.locator('#list-status')).toHaveText('Не удалось загрузить список матчей. Проверьте подключение и обновите страницу.');
  await page.route('**/api/matches/133162474', route => route.abort('failed'));
  await page.goto('/m/133162474');
  await expect(page.getByRole('status')).toHaveText('Не удалось загрузить матч. Проверьте подключение и обновите страницу.');
});
