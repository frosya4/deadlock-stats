import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: '.', testMatch: '*.spec.ts', workers: 1, timeout: 30000,
  use: { baseURL: 'http://127.0.0.1:8765', viewport: { width: 1440, height: 1000 }, screenshot: 'only-on-failure' },
  webServer: { command: 'python serve.py', url: 'http://127.0.0.1:8765/api/matches', timeout: 120000, reuseExistingServer: false },
});
