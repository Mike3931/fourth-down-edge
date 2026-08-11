import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 60_000,
  use: {
    baseURL: 'http://localhost:4173',
    viewport: { width: 1440, height: 900 },
  },
  webServer: {
    // BUILD, then serve. `vite preview` serves whatever is already in
    // dist/ and never rebuilds, and `reuseExistingServer` keeps a preview
    // from an earlier build alive across runs — so `npx playwright test`
    // on its own could pass against a bundle whose source no longer
    // exists. It did: a copy fix verified in the dev server on 5173 was
    // still reported as absent here, because 4173 was serving output from
    // before the edit.
    //
    // CI was never wrong (its job runs `npm run build` first), which is
    // exactly why this survived — the failure only ever appeared on a
    // developer machine, as a test result that looked like a real one.
    command: 'npm run build -w @fde/web && npm run preview -w @fde/web -- --port 4173 --strictPort',
    port: 4173,
    reuseExistingServer: false,
    timeout: 180_000,
  },
});
