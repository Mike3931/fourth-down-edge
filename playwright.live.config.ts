import { defineConfig } from '@playwright/test';

/** Run only the tests that REQUIRE a functioning local analytical engine. */
const port = process.env.FDE_E2E_PORT ?? '5173';
export default defineConfig({
  testDir: './tests/engine-e2e',
  testMatch: 'live-engine-required.spec.ts',
  timeout: 60_000,
  retries: 0,
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    viewport: { width: 1440, height: 900 },
  },
  webServer: {
    command: `npm run dev -w @fde/web -- --host 127.0.0.1 --port ${port} --strictPort`,
    url: `http://127.0.0.1:${port}`,
    reuseExistingServer: false,
    timeout: 180_000,
    env: {
      FDE_RESEARCH_API: process.env.FDE_RESEARCH_API ?? 'http://127.0.0.1:8000',
    },
  },
});
