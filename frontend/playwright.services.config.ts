import { defineConfig } from '@playwright/test';

const port = 5057;
const python = process.env.ERP_E2E_PYTHON || 'python3';

export default defineConfig({
  testDir: './e2e',
  testMatch: 'services.spec.ts',
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    viewport: { width: 1440, height: 900 },
    permissions: ['clipboard-read', 'clipboard-write'],
    trace: 'retain-on-failure',
  },
  webServer: {
    command: `ERP_PREVIEW_OWNER=1 PREVIEW_PORT=${port} ${python} ../tests/stage2_preview_server.py`,
    url: `http://127.0.0.1:${port}/app/services`,
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
