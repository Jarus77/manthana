/*
 * Build-time sync: docs/ (the single source of truth) -> docs-site/src/content/docs/.
 *
 * The markdown in ../docs is authored to be read on GitHub, as plain relative
 * .md links. This step is the ONLY transformation between that source and the
 * published site, so the site can never drift from the repo docs:
 *
 *   1. copies each public page into the Starlight content collection
 *   2. lifts the leading `# H1` into frontmatter `title` (Starlight renders the
 *      page title itself; leaving the H1 in would print it twice)
 *   3. derives a `description` from the first paragraph, for <meta> / search
 *   4. rewrites every relative .md link to its site route, and every link that
 *      points at a repo file (LICENSE, docker-compose.yml, deploy/) to GitHub
 *
 * Anything not listed in PAGES is internal and is never published:
 * docs/aws-infrastructure.md, docs/DEMO.md, docs/report/**, spec/**.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..', '..');
const DOCS = path.join(ROOT, 'docs');
const OUT = path.resolve(HERE, '..', 'src', 'content', 'docs');
const HOME = path.resolve(HERE, '..', 'src', 'home');

const REPO = 'https://github.com/Jarus77/manthana';
const repoUrl = (p, isDir) => `${REPO}/${isDir ? 'tree' : 'blob'}/main/${p.replace(/\/$/, '')}`;

/** Public pages: source path (relative to docs/) -> site route. */
const PAGES = {
  'troubleshooting.md': '/troubleshooting/',
  'founders/index.md': '/founders/',
  'founders/provisioning.md': '/founders/provisioning/',
  'founders/privacy-and-budgets.md': '/founders/privacy-and-budgets/',
  'founders/reading-the-wiki.md': '/founders/reading-the-wiki/',
  'founders/operating.md': '/founders/operating/',
  'engineers/index.md': '/engineers/',
  'engineers/your-data.md': '/engineers/your-data/',
  'engineers/daily.md': '/engineers/daily/',
  'solo/index.md': '/solo/',
  'self-hosting/index.md': '/self-hosting/',
  'self-hosting/web-client.md': '/self-hosting/web-client/',
  'self-hosting/operations.md': '/self-hosting/operations/',
  'reference/architecture.md': '/reference/architecture/',
  'reference/privacy.md': '/reference/privacy/',
  'reference/cli.md': '/reference/cli/',
  'reference/environment.md': '/reference/environment/',
  // Routing stubs. They stay published so the paths printed by install.sh and
  // the server keep resolving, but they get no sidebar entry (see astro.config).
  'deploy.md': '/deploy/',
  'onboarding.md': '/onboarding/',
  // The index becomes the hand-built landing page, not a markdown dump.
  'README.md': '/',
};

/** Descriptions that read better than the first paragraph of the page. */
const DESCRIPTIONS = {
  'founders/index.md':
    'What a founder or admin gets from Manthana, and the order to set it up in: provision an org, invite engineers, set the privacy posture and AI budget.',
  'engineers/index.md':
    'You were sent an invite. What Manthana is, exactly what it does with your data, and the ten minutes of setup.',
  'troubleshooting.md':
    'Symptom to cause to fix, for engineers and for the org server — starting with what `manthana doctor` and `manthana-server doctor` tell you.',
  'reference/environment.md':
    'Every MANTHANA_* and MANTHANA_SERVER_* environment variable, its default, and what it does.',
  'reference/cli.md':
    'Every manthana and manthana-server command and flag, for the local agent and for the org server.',
  'deploy.md':
    'Deploying the Manthana org server: where the pages on deployment, TLS, the web client and operations now live.',
  'onboarding.md':
    'Onboarding, split by audience: provisioning an org, setting up an engineer laptop, running solo, or deploying the server.',
};

const stripFence = (md) => md.replace(/```[\s\S]*?```/g, '').replace(/^ {4}.*$/gm, '');

function deriveTitle(md, file) {
  const m = md.match(/^#\s+(.+?)\s*$/m);
  if (!m) throw new Error(`no H1 in docs/${file}`);
  // Starlight renders the title as plain text, so markdown code ticks in an H1
  // (`The wiki client (\`web/\`)`) would show up literally.
  return { title: m[1].replace(/`/g, ''), body: md.replace(/^#\s+.+?\s*$\n*/m, '') };
}

function deriveDescription(md, file) {
  if (DESCRIPTIONS[file]) return DESCRIPTIONS[file];
  const para = stripFence(md)
    .split(/\n\s*\n/)
    .map((p) => p.trim())
    .find((p) => p && !p.startsWith('#') && !p.startsWith('|') && !p.startsWith('>'));
  if (!para) return 'Manthana documentation.';
  let text = para
    .replace(/\s+/g, ' ')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/`/g, '')
    .replace(/\*\*?/g, '');
  if (text.length > 158) text = text.slice(0, 155).replace(/[\s,;:]+\S*$/, '') + '…';
  return text;
}

/** Resolve one markdown link target, relative to the doc it appears in. */
function resolveLink(target, fromFile) {
  if (/^(https?:|mailto:|#)/.test(target)) return target;

  const [rawPath, hash] = target.split('#');
  const abs = path.normalize(path.join(path.dirname(fromFile), rawPath));
  const suffix = hash ? `#${hash}` : '';

  // A link to another published doc page -> its site route.
  if (PAGES[abs]) return PAGES[abs] + suffix;
  // A doc directory link (e.g. `self-hosting/`) -> that section's index.
  const asIndex = path.join(abs.replace(/\/$/, ''), 'index.md');
  if (PAGES[asIndex]) return PAGES[asIndex] + suffix;

  const isDir = rawPath.endsWith('/');
  // Anything that escapes docs/ is a repo file: send it to GitHub.
  if (abs.startsWith('..')) {
    const repoPath = path.normalize(path.join('docs', abs)).replace(/^\/+/, '');
    return repoUrl(repoPath, isDir) + suffix;
  }
  // A docs/ file we deliberately do not publish (internal) -> GitHub.
  if (rawPath) return repoUrl(`docs/${abs}`, isDir) + suffix;
  return target;
}

function rewriteLinks(md, file) {
  const seen = [];
  const out = md.replace(/\]\(([^)\s]+)\)/g, (whole, target) => {
    const next = resolveLink(target, file);
    seen.push([target, next]);
    return `](${next})`;
  });
  return { out, seen };
}

const yaml = (s) => `"${String(s).replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`;

function main() {
  fs.rmSync(OUT, { recursive: true, force: true });
  fs.mkdirSync(OUT, { recursive: true });

  let count = 0;
  const linkReport = [];

  for (const [file, route] of Object.entries(PAGES)) {
    if (route === '/') continue; // the landing page is built from src/home
    const src = path.join(DOCS, file);
    const raw = fs.readFileSync(src, 'utf8');
    const { title, body } = deriveTitle(raw, file);
    const description = deriveDescription(body, file);
    const { out, seen } = rewriteLinks(body, file);
    linkReport.push(...seen.map(([from, to]) => ({ file, from, to })));

    const dest = path.join(OUT, file);
    fs.mkdirSync(path.dirname(dest), { recursive: true });
    fs.writeFileSync(
      dest,
      `---\ntitle: ${yaml(title)}\ndescription: ${yaml(description)}\n---\n\n${out.trimStart()}`,
    );
    count += 1;
  }

  // Hand-built landing page + any other authored pages.
  for (const entry of fs.readdirSync(HOME)) {
    fs.copyFileSync(path.join(HOME, entry), path.join(OUT, entry));
    count += 1;
  }

  fs.writeFileSync(
    path.resolve(HERE, '..', '.link-map.json'),
    JSON.stringify(linkReport, null, 2),
  );
  console.log(`sync-docs: ${count} pages, ${linkReport.length} links rewritten`);
}

main();
export { PAGES, resolveLink };
