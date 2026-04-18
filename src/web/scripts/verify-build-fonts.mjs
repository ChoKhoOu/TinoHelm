#!/usr/bin/env node
/**
 * Verify Next.js build produced the expected @font-face entries and woff2 files.
 *
 * Strategy (two-layer defense):
 *   - Strong evidence: scan every .css file under the build tree for @font-face
 *     font-family values.
 *       Required: 'Inter', 'JetBrains Mono'
 *       Forbidden: 'Source Serif 4' (explicitly not loaded)
 *   - Sanity: static/media/*.woff2 file count >= 2
 *
 * Build layout handled:
 *   - `next build` (server build)     -> .next/static/{css|chunks|media}
 *   - `next build` with output:export -> out/_next/static/{chunks|media} (CSS lives in chunks/)
 *
 * CSS minification note: Next.js / Turbopack strips quotes around font-family
 * identifiers, so the regex accepts both quoted and unquoted forms.
 */
import { readdirSync, readFileSync, existsSync } from 'node:fs';
import { join, extname } from 'node:path';

function pickRoot() {
  const candidates = ['.next', 'out/_next'];
  for (const r of candidates) {
    const staticDir = join(process.cwd(), r, 'static');
    if (existsSync(staticDir)) return r;
  }
  console.error('[verify-build-fonts] Neither .next nor out/_next found. Did `next build` run?');
  process.exit(1);
}

function walkCssFiles(dir) {
  const out = [];
  const entries = readdirSync(dir, { withFileTypes: true });
  for (const e of entries) {
    const full = join(dir, e.name);
    if (e.isDirectory()) {
      out.push(...walkCssFiles(full));
    } else if (e.isFile() && extname(e.name) === '.css') {
      out.push(full);
    }
  }
  return out;
}

const rootBase = pickRoot();
const staticDir = join(process.cwd(), rootBase, 'static');
const mediaDir = join(staticDir, 'media');

const cssFiles = walkCssFiles(staticDir);
const cssText = cssFiles.map((f) => readFileSync(f, 'utf-8')).join('\n\n');

// Accept quoted or unquoted font-family values (Next.js 16 / Turbopack strips quotes)
const fontFaceFamilies = [
  ...cssText.matchAll(/@font-face\s*\{[^}]*font-family\s*:\s*(?:['"]([^'"]+)['"]|([^;,}]+))/g),
].map((m) => (m[1] ?? m[2] ?? '').trim());

function hasFamily(name) {
  return fontFaceFamilies.some((f) => f.toLowerCase().includes(name.toLowerCase()));
}

const required = ['Inter', 'JetBrains Mono'];
const forbidden = ['Source Serif 4'];

let failed = 0;

console.log(`[verify-build-fonts] scanned ${cssFiles.length} CSS file(s) under ${staticDir}`);
console.log(`[verify-build-fonts] discovered @font-face families: ${[...new Set(fontFaceFamilies)].join(', ') || '(none)'}`);

for (const fam of required) {
  const ok = hasFamily(fam);
  console.log(`[verify-build-fonts] required @font-face { font-family: '${fam}' }: ${ok ? 'OK' : 'MISSING'}`);
  if (!ok) failed++;
}
for (const fam of forbidden) {
  const present = hasFamily(fam);
  console.log(`[verify-build-fonts] forbidden @font-face { font-family: '${fam}' }: ${present ? 'PRESENT (FAIL)' : 'absent OK'}`);
  if (present) failed++;
}

const woff2 = existsSync(mediaDir)
  ? readdirSync(mediaDir).filter((f) => f.endsWith('.woff2'))
  : [];
console.log(`[verify-build-fonts] woff2 count in ${mediaDir}: ${woff2.length}`);
if (woff2.length < 2) {
  console.error(`[verify-build-fonts] expected >= 2 woff2 files, got ${woff2.length}`);
  failed++;
}

if (failed > 0) {
  console.error(`[verify-build-fonts] ${failed} check(s) failed`);
  process.exit(1);
}
console.log('[verify-build-fonts] All checks passed.');
