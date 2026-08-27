import { defineConfig } from '@playwright/test';

const port = 4173;
const baseURL = `http://127.0.0.1:${port}`;
const python = process.env.ERP_E2E_PYTHON || 'python3';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL,
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'desktop-1920',
      use: { viewport: { width: 1920, height: 1080 } },
    },
    {
      name: 'desktop-1440',
      use: { viewport: { width: 1440, height: 900 } },
    },
    {
      name: 'desktop-1280',
      use: { viewport: { width: 1280, height: 800 } },
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
      name: 'mobile-430',
      use: { viewport: { width: 430, height: 932 } },
    },
    {
      name: 'mobile-390',
      use: { viewport: { width: 390, height: 844 } },
    },
    {
      name: 'mobile-375',
      use: { viewport: { width: 375, height: 812 } },
    },
    {
      name: 'mobile-320',
      use: { viewport: { width: 320, height: 700 } },
    },
  ],
  webServer: {
    command: `PREVIEW_PORT=${port} ${python} ../tests/stage2_preview_server.py`,
    url: `${baseURL}/login`,
    reuseExistingServer: !process.env.CI,
  },
});
