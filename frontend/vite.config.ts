/// <reference types="vitest" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The dev server runs inside a sandbox and is reached through a proxied preview host,
// so it must bind 0.0.0.0, accept that host, and proxy /api to the backend itself -
// the browser is not inside the sandbox and cannot reach 127.0.0.1:8000 directly.
// API_PROXY_TARGET lets the start scripts keep this in sync when the API port is
// changed; without it, `start.sh` with a custom port silently proxied to :8000.
const apiProxyTarget = process.env.API_PROXY_TARGET ?? 'http://127.0.0.1:8000';

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: Number(process.env.WEB_PORT ?? 5173),
    strictPort: true,
    allowedHosts: true,
    proxy: {
      '/api': {
        target: apiProxyTarget,
        changeOrigin: true,
      },
    },
  },
  preview: {
    host: '0.0.0.0',
    port: 4173,
    strictPort: true,
    allowedHosts: true,
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
    // Playwright owns e2e/; Vitest must not try to collect those specs.
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    exclude: ['node_modules', 'dist', 'e2e'],
  },
});
