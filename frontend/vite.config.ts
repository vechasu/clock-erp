import { defineConfig } from 'vitest/config';

export default defineConfig({
  base: '/app/',
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:5050',
    },
  },
  build: {
    outDir: '../app/static/react',
    emptyOutDir: true,
    sourcemap: false,
  },
  test: {
    environment: 'node',
    globals: true,
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
  },
});
