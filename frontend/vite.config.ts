import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The browser only talks to this origin; /api is proxied to the FastAPI service,
// so the API never needs to be exposed with permissive CORS.
const apiTarget = process.env.API_PROXY_TARGET ?? 'http://localhost:8000'
const proxy = { '/api': { target: apiTarget, changeOrigin: false } }

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy },
  preview: { port: 5173, host: true, proxy },
})
