/**
 * Post-build step: inject the real precache manifest into dist/sw.js.
 *
 * Vite emits content-hashed asset filenames, so the service worker cannot know
 * them ahead of time. This script scans the build output and replaces the two
 * placeholders in the hand-written service worker with the actual asset list
 * and a cache version derived from those filenames — so a new build always
 * invalidates the old cache.
 */
import { createHash } from 'node:crypto';
import { readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';

const distDir = resolve(import.meta.dirname, '..', 'dist');
const swPath = join(distDir, 'sw.js');

/** Files always needed for the offline shell, beyond the hashed assets. */
const SHELL_URLS = ['/', '/index.html', '/manifest.webmanifest', '/icons/icon-192.png', '/icons/icon-512.png'];

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
  (f) => `/${relative(distDir, f).split('\\').join('/')}`,
);

const precache = [...new Set([...SHELL_URLS, ...assetUrls])].sort();
const version = `fde-${createHash('sha256').update(precache.join('|')).digest('hex').slice(0, 12)}`;

let sw = readFileSync(swPath, 'utf8');
if (!sw.includes('__FDE_PRECACHE_URLS__') || !sw.includes('__FDE_CACHE_VERSION__')) {
  throw new Error('sw.js is missing its precache placeholders — did the source change?');
}
sw = sw
  .replace('__FDE_PRECACHE_URLS__', JSON.stringify(precache))
  .replace('__FDE_CACHE_VERSION__', version);
writeFileSync(swPath, sw);

console.log(`sw.js: precached ${precache.length} files as cache "${version}"`);
