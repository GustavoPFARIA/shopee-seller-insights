import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Local development only (npm run dev): /api is proxied to FastAPI on :8000.
// In Docker the static build is served by nginx (see nginx.conf).
const apiTarget = process.env.API_PROXY_TARGET ?? 'http://localhost:8000'
const proxy = { '/api': { target: apiTarget, changeOrigin: false } }

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy },
})
