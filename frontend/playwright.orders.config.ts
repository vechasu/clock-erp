import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './e2e', testMatch: 'orders.spec.ts', workers: 1,
  use: { baseURL: 'http://127.0.0.1:4188', viewport: { width: 1440, height: 900 } },
  webServer: {
    command: 'ERP_TEST_MODE=1 ERP_AUTH_ENABLED=0 PREVIEW_PORT=4188 python3 ../tests/orders_progressive_preview_server.py',
    url: 'http://127.0.0.1:4188/app/orders', reuseExistingServer: !process.env.CI,
  },
});
