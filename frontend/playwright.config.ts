import { defineConfig } from '@playwright/test';

const baseURL = 'http://127.0.0.1:4173/app/';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL,
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'desktop-1440',
      use: { viewport: { width: 1440, height: 900 } },
    },
    {
      name: 'tablet-1024',
      use: { viewport: { width: 1024, height: 768 } },
    },
    {
      name: 'tablet-768',
      use: { viewport: { width: 768, height: 1024 } },
    },
    {
      name: 'mobile-390',
      use: { viewport: { width: 390, height: 844 } },
    },
  ],
  webServer: {
    command: 'pnpm preview --host 127.0.0.1 --port 4173',
    url: baseURL,
    reuseExistingServer: !process.env.CI,
  },
});
