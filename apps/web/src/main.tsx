import React from 'react';
import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import App from './App';
import { AuthProvider } from './lib/auth';
import { StoreProvider } from './lib/store';
import './styles.css';

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
});

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <StoreProvider>
          {/* basename supports deployment under a subpath (e.g. GitHub
              Pages project sites at /repo-name/); import.meta.env.BASE_URL
              is injected by Vite from the --base build flag and defaults
              to '/' for root deployments and local dev. */}
          <BrowserRouter basename={import.meta.env.BASE_URL}>
            <App />
          </BrowserRouter>
        </StoreProvider>
      </AuthProvider>
    </QueryClientProvider>
  </React.StrictMode>,
);

// Register the service worker for installability + offline app shell.
// Market data is never served from cache without a stale-data warning: the
// app renders its own staleness banners from data timestamps, and the SW
// serves cached data only alongside an 'offline' flag (see public/sw.js).
if ('serviceWorker' in navigator && import.meta.env.PROD) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register(`${import.meta.env.BASE_URL}sw.js`).catch(() => {
      // Registration failure is non-fatal; app remains fully usable online.
    });
  });
}
