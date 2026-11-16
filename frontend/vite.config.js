import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
//
// Two sites from this one code base (16/11/2026, admin site split):
//   customer site  `vite` / `vite build`               -> port 5176, dist/
//   admin site     `vite --mode admin` / `vite build --mode admin`
//                                                       -> port 5177, dist-admin/
// App.jsx reads import.meta.env.VITE_SITE ('public' | 'admin'): the customer
// site has no admin pages at all; the admin site has only the staff pages.
export default defineConfig(({ mode }) => {
  const admin = mode === 'admin'
  return {
    plugins: [react()],
    define: {
      'import.meta.env.VITE_SITE': JSON.stringify(admin ? 'admin' : 'public'),
    },
    build: { outDir: admin ? 'dist-admin' : 'dist' },
    server: {
      host: true,
      // Tick and Book's ports: website 5176 + backend 8005, admin site 5177 +
      // admin backend 8006 (the same as the server's docker-compose.yml) —
      // other live systems on the server use 5173/8000. strictPort: if the
      // port is taken, stop with an error instead of quietly moving.
      port: admin ? 5177 : 5176,
      strictPort: true,
      // The site calls its backend at '/api' (same origin). In development
      // Vite passes those calls on; on the server nginx does it.
      proxy: {
        '/api': { target: admin ? 'http://127.0.0.1:8006' : 'http://127.0.0.1:8005', changeOrigin: true },
        // Uploaded images (package posters) are served by the backend at /uploads/.
        '/uploads': { target: admin ? 'http://127.0.0.1:8006' : 'http://127.0.0.1:8005', changeOrigin: true },
      },
    },
  }
})
