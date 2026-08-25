import { defineConfig } from '@playwright/test';

const baseURL = 'http://127.0.0.1:4174';

export default defineConfig({
  testDir: './e2e-products',
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: [['list']],
  use: {
    baseURL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { viewport: { width: 1366, height: 768 } } }],
  webServer: {
    command: 'PREVIEW_PORT=4174 python3 ../tests/stage2_preview_server.py',
    url: `${baseURL}/app/products`,
    reuseExistingServer: false,
  },
});
