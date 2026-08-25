import { defineConfig } from 'vite';

export default defineConfig({
  base: '/app/',
  server: {
    fs: {
      allow: ['..'],
    },
    proxy: {
      '/api': 'http://127.0.0.1:5050',
    },
  },
  build: {
    outDir: '../app/static/react',
    emptyOutDir: true,
    sourcemap: false,
  },
});
