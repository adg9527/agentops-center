import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': { target: process.env.AGENTOPS_API ?? 'http://127.0.0.1:8000', ws: true, changeOrigin: true },
    },
  },
  // manifest.json lets the backend render the HTML shell itself (see backend/app/main.py)
  build: { chunkSizeWarningLimit: 2000, manifest: true },
})
