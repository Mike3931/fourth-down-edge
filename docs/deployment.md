# Deployment

## Static frontend (v1)

```
npm run build            # -> apps/web/dist
```

Deploy `apps/web/dist` to any HTTPS static host. Examples:

**Vercel:** project root `apps/web`, build command `npm run build -w @fde/web` (run from repo root via
`cd ../.. && npm run build -w @fde/web`), output `apps/web/dist`. Add a SPA rewrite: all routes →
`/index.html`.

**Netlify:** publish dir `apps/web/dist`, redirect rule `/* /index.html 200`.

Required headers (most hosts set sensible defaults; verify):

- The CSP is delivered via `<meta http-equiv>` in `index.html`; a host-level header may mirror it.
- `Cache-Control: no-cache` for `index.html` and `sw.js`; long-lived immutable caching for
  `/assets/*` (hashed filenames).

HTTPS is mandatory — service workers and PWA install do not function on plain HTTP (localhost is the
only exception).

## Supabase

1. Create separate dev and prod projects.
2. Apply `supabase/migrations/0001..0003` in order; run `supabase/seed/seed.sql`.
3. Auth → Email OTP enabled; disable anonymous sign-ups if not wanted.
4. API → CORS: allowlist only your deployed origin.
5. Never expose the `service_role` key to the frontend or commit it anywhere.
6. Set `VITE_SUPABASE_URL` / `VITE_SUPABASE_ANON_KEY` as build-time env vars in the host.

## PWA install verification checklist

- Open the HTTPS URL in Chrome/Edge → DevTools → Application → Manifest: no warnings, installability
  "installable".
- Install; confirm standalone window, dark theme-color, icons at 192/512 + maskable.
- Toggle offline in DevTools → reload: app shell loads, screens render with their designed
  stale/disconnected states, and no market data is presented as current without a warning banner.
