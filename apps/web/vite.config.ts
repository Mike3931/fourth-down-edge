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
  },
});
