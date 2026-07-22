/*
 * Post-build link check over dist/.
 *
 * Every internal href in the built HTML must resolve to a real file in dist/,
 * and every in-page #anchor must correspond to an `id` on the target page. The
 * docs were authored as relative .md links for GitHub; sync-docs.mjs rewrites
 * them, and this is the proof that the rewrite is complete. External links are
 * listed but not fetched.
 *
 * Exits non-zero on the first broken link.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const DIST = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'dist');

function walk(dir, out = []) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p, out);
    else out.push(p);
  }
  return out;
}

if (!fs.existsSync(DIST)) {
  console.error('check-links: dist/ not found — run `npm run build` first');
  process.exit(1);
}

const files = walk(DIST);
const htmlFiles = files.filter((f) => f.endsWith('.html'));
const idsByPage = new Map();

for (const f of htmlFiles) {
  const html = fs.readFileSync(f, 'utf8');
  const ids = new Set();
  for (const m of html.matchAll(/\sid="([^"]+)"/g)) ids.add(m[1]);
  idsByPage.set(f, ids);
}

const exists = (p) => fs.existsSync(p);
const broken = [];
let internal = 0;
let external = 0;

for (const f of htmlFiles) {
  const html = fs.readFileSync(f, 'utf8');
  const pageUrl = '/' + path.relative(DIST, f).replace(/index\.html$/, '').replace(/\\/g, '/');

  for (const m of html.matchAll(/<a\b[^>]*\shref="([^"]+)"/g)) {
    const href = m[1];
    if (/^(https?:|mailto:|tel:|data:)/.test(href)) {
      external += 1;
      continue;
    }
    internal += 1;

    const [rawPath, hash] = href.split('#');
    let targetFile = f;

    if (rawPath) {
      const abs = rawPath.startsWith('/')
        ? path.join(DIST, rawPath)
        : path.resolve(path.dirname(f), rawPath);
      const candidates = [abs, path.join(abs, 'index.html'), abs + '.html'];
      const hit = candidates.find((c) => exists(c) && fs.statSync(c).isFile());
      if (!hit) {
        broken.push(`${pageUrl} -> ${href}  (no file for ${rawPath})`);
        continue;
      }
      targetFile = hit;
    }

    if (hash && targetFile.endsWith('.html')) {
      const ids = idsByPage.get(targetFile);
      if (ids && !ids.has(hash) && hash !== '_top') {
        broken.push(`${pageUrl} -> ${href}  (no #${hash} on target page)`);
      }
    }
  }
}

console.log(
  `check-links: ${htmlFiles.length} pages, ${internal} internal links, ${external} external links`,
);
if (broken.length) {
  console.error(`\n${broken.length} broken link(s):`);
  for (const b of broken) console.error('  ' + b);
  process.exit(1);
}
console.log('check-links: no broken internal links');
