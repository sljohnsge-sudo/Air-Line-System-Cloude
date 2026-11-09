import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    // Tick and Book's ports: website 5176, backend 8005 (the same as the
    // server's docker-compose.yml) — other live systems on the server use
    // 5173/8000. strictPort: if 5176 is taken, stop with an error instead of
    // quietly moving to another port.
    port: 5176,
    strictPort: true,
    // The site calls the backend at '/api' (same origin). In development
    // Vite passes those calls on to the backend on port 8005; on the server
    // the web server does it.
    proxy: {
      '/api': { target: 'http://127.0.0.1:8005', changeOrigin: true },
    },
  },
})
