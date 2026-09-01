/// <reference types="vitest" />
import path from 'node:path'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  server: {
    port: 5173,
    // The API runs on 8000 in docker-compose. Proxying keeps the browser on a
    // single origin in dev, so there is no CORS configuration to maintain and
    // the app's fetch calls use plain relative paths in every environment.
    //
    // ONE rule, under a prefix the SPA never routes on. Proxying bare paths
    // (`/devices`, `/readings`) was a bug: a hard refresh at /devices is a
    // document request that matched the proxy and returned the API's 401 JSON
    // instead of index.html. `/api` cannot collide, because no client route
    // starts with it. The rewrite strips the prefix so the backend keeps
    // serving the paths SPEC documents.
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
})
