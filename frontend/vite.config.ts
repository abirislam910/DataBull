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
    proxy: {
      '/auth': 'http://localhost:8000',
      '/devices': 'http://localhost:8000',
      '/readings': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
})
