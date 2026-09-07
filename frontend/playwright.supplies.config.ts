import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './e2e-supplies',
  workers: 1,
  retries: 0,
  use: { baseURL: 'http://127.0.0.1:4184', timezoneId: 'Europe/Moscow', trace: 'retain-on-failure' },
  webServer: {
    command: 'PREVIEW_PORT=4184 python3 ../tests/supplies_preview_server.py',
    url: 'http://127.0.0.1:4184/login',
    reuseExistingServer: false,
  },
});
