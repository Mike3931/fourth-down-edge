/**
 * Post-build step: inject the real precache manifest into dist/sw.js.
 *
 * Vite emits content-hashed asset filenames, so the service worker cannot know
 * them ahead of time. This script scans the build output and replaces the
 * placeholders in the hand-written service worker with the actual asset list,
 * the shell document path, and a cache version derived from those filenames —
 * so a new build always invalidates the old cache.
 *
 * Pass --base=/subpath/ to match a non-root Vite `--base` (e.g. GitHub Pages
 * project sites at /repo-name/). Must match the base used for `vite build`,
 * or the precached URLs won't correspond to what the app actually requests.
 */
import { createHash } from 'node:crypto';
import { readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';

const distDir = resolve(import.meta.dirname, '..', 'dist');
const swPath = join(distDir, 'sw.js');

const baseArg = process.argv.find((a) => a.startsWith('--base='));
let base = baseArg ? baseArg.slice('--base='.length) : '/';
if (!base.startsWith('/')) base = `/${base}`;
if (!base.endsWith('/')) base = `${base}/`;

/** Files always needed for the offline shell, beyond the hashed assets. */
const SHELL_FILES = ['index.html', 'manifest.webmanifest', 'icons/icon-192.png', 'icons/icon-512.png'];
const SHELL_URLS = [base, ...SHELL_FILES.map((f) => base + f)];
const SHELL_INDEX = `${base}index.html`;

function walk(dir) {
  const out = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) out.push(...walk(full));
    else out.push(full);
  }
  return out;
}

const assetUrls = walk(join(distDir, 'assets')).map(
  (f) => base + relative(distDir, f).split('\\').join('/'),
);

const precache = [...new Set([...SHELL_URLS, ...assetUrls])].sort();
const version = `fde-${createHash('sha256').update(precache.join('|')).digest('hex').slice(0, 12)}`;

let sw = readFileSync(swPath, 'utf8');
for (const placeholder of ['__FDE_PRECACHE_URLS__', '__FDE_CACHE_VERSION__', '__FDE_SHELL_INDEX__']) {
  if (!sw.includes(placeholder)) {
    throw new Error(`sw.js is missing the ${placeholder} placeholder — did the source change?`);
  }
}
sw = sw
  .replace('__FDE_PRECACHE_URLS__', JSON.stringify(precache))
  .replace('__FDE_CACHE_VERSION__', version)
  .replace('__FDE_SHELL_INDEX__', SHELL_INDEX);
writeFileSync(swPath, sw);

console.log(`sw.js: precached ${precache.length} files under base "${base}" as cache "${version}"`);
