import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

// Fourth Down Edge web app.
// Structured so the frontend can later be packaged with Tauri without major
// refactoring: no server-side rendering, no Node APIs in the client, all data
// access behind the @fde/api-client abstraction.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    target: 'es2022',
    sourcemap: false,
    rollupOptions: {
      output: {
        // Charts are heavy and only needed on analysis screens; splitting them
        // keeps the app shell (and therefore the offline shell) small.
        manualChunks: {
          charts: ['recharts'],
          vendor: ['react', 'react-dom', 'react-router-dom', '@tanstack/react-query', '@tanstack/react-table'],
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      // Dev convenience: point the analytical-engine URL at "/research-api" in
      // Settings and requests become same-origin, so no CORS round-trip is
      // needed while developing. Production deployments talk to the engine's
      // real URL directly (the client accepts any base URL).
      '/research-api': {
        target: process.env.FDE_RESEARCH_API ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/research-api/, ''),
        // The bearer token is attached HERE, in the dev server, from a
        // variable with no VITE_ prefix. Vite inlines VITE_-prefixed
        // variables into the client bundle as string literals, so reading
        // the token in browser code published it to every visitor.
        configure: (proxy) => {
          proxy.on('proxyReq', (proxyReq) => {
            const token = process.env.FDE_API_TOKEN;
            if (token) proxyReq.setHeader('authorization', `Bearer ${token}`);
          });
        },
      },
    },
  },
});
