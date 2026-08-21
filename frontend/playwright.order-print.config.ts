import { defineConfig } from '@playwright/test';

const baseURL = 'http://127.0.0.1:4174';

export default defineConfig({
  testDir: './e2e-print',
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  reporter: 'list',
  use: {
    baseURL,
    viewport: { width: 1280, height: 900 },
    trace: 'on-first-retry',
  },
  projects: [{ name: 'chrome-a4', use: { channel: 'chrome' } }],
  webServer: {
    command: 'cd .. && python3 -m tests.order_print_preview_server',
    url: `${baseURL}/app/orders/18593/print`,
    reuseExistingServer: false,
  },
});
