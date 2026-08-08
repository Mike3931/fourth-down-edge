/**
 * No secret may reach the shipped bundle.
 *
 * Vite inlines every VITE_-prefixed variable into client JavaScript as a
 * string literal. A `VITE_FDE_API_TOKEN` read in browser code therefore
 * published the engine's bearer token to every visitor — confirmed by
 * building with one set and finding it verbatim in dist/assets.
 *
 * Two checks, because they catch different mistakes:
 *
 *   SOURCE  no VITE_-prefixed variable whose name looks like a credential.
 *           Catches the mistake when it is written, rather than only on a
 *           build where the variable happens to be set.
 *
 *   BUILD   a canary is injected into every credential-shaped VITE_
 *           variable, the app is built, and the output searched for it.
 *           Catches a path the name check does not recognise.
 *
 * Run:  node scripts/audit-bundle-secrets.mjs
 */
import { execSync } from 'node:child_process';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';

const SECRETISH = /VITE_[A-Z0-9_]*(TOKEN|KEY|SECRET|PASSWORD|CREDENTIAL)/g;
const CANARY = 'BUNDLE_SECRET_CANARY_b7f3a91c';
const SRC = 'apps/web/src';
const DIST = 'apps/web/dist';

function walk(dir) {
  const out = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) out.push(...walk(full));
    else out.push(full);
  }
  return out;
}

/**
 * Values that are PUBLIC BY DESIGN and belong in the bundle.
 *
 * An allowlist, not a looser pattern: each entry has to state why the
 * value is safe, so the next person adding one makes the same argument
 * rather than widening a regex until their case slips through.
 */
const PUBLIC_BY_DESIGN = new Map([
  [
    'VITE_SUPABASE_ANON_KEY',
    'Supabase anon keys are meant for browser use; access is governed by ' +
      'row-level security rather than by keeping the key secret. The ' +
      'SERVICE ROLE key is the dangerous one and must never appear here.',
  ],
]);

const problems = [];

// --- source ----------------------------------------------------------- //
for (const file of walk(SRC)) {
  if (!/\.(ts|tsx|js|jsx)$/.test(file)) continue;
  // Block comments are stripped across the whole file BEFORE line
  // comments. Doing it per line missed `*` continuation lines inside a
  // block, so prose explaining this very rule was reported as a breach
  // of it.
  const code = readFileSync(file, 'utf8')
    // Normalise line endings FIRST. On a CRLF checkout every line ends
    // with \r; `.` does not match a line terminator and `$` without the m
    // flag wants end-of-string, so `//.*$` matched nothing and every line
    // comment survived stripping. The audit then reported its own
    // explanatory prose as a violation — on Windows only.
    .replace(/\r\n?/g, '\n')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n')
    .map((line) => line.replace(/\/\/.*$/, ''))
    .join('\n');
  for (const match of code.matchAll(SECRETISH)) {
    if (PUBLIC_BY_DESIGN.has(match[0])) continue;
    problems.push(`${file}: reads ${match[0]} — Vite inlines this into the bundle`);
  }
}

// --- build ------------------------------------------------------------ //
const env = { ...process.env };
for (const name of ['VITE_FDE_API_TOKEN', 'VITE_FDE_ODDS_API_KEY', 'VITE_API_SECRET']) {
  env[name] = CANARY;
}
execSync('npm run build --silent', { env, stdio: 'ignore' });

for (const file of walk(DIST)) {
  if (readFileSync(file, 'utf8').includes(CANARY)) {
    problems.push(`${file}: canary credential reached the shipped bundle`);
  }
}

if (problems.length) {
  console.error('\nBUNDLE SECRET AUDIT FAILED:');
  for (const p of problems) console.error(`  - ${p}`);
  console.error('');
  process.exit(1);
}
console.log('BUNDLE SECRET AUDIT PASSED: no credential reaches client code');
