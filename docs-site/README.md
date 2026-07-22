# docs-site

The public documentation site for Manthana — [Astro](https://astro.build) +
[Starlight](https://starlight.astro.build), built to a plain directory of static
files (`dist/`) that is served from S3 + CloudFront. **There is no Node server at
runtime**: every page is prerendered HTML and search is a client-side Pagefind
index built at deploy time.

This is a separate project from `web/` (the org wiki client, a Next.js app that
talks to a running server). They share a look, nothing else — different purpose,
different deploy, different lifecycle.

## The content lives in `../docs`, not here

`docs/` at the repo root is the single source of truth. `npm run build` runs
`scripts/sync-docs.mjs` first, which copies each **public** page into
`src/content/docs/` and, on the way through:

- lifts the leading `# H1` into the frontmatter `title` (Starlight renders the
  title itself),
- derives a `description` for `<meta>`, Open Graph, and the search index,
- rewrites every relative `.md` link to its site route, and every link that
  points at a repo file (`LICENSE`, `docker-compose.yml`, `deploy/…`) to GitHub.

`src/content/docs/` is generated and gitignored. **Never edit it** — edit
`../docs` and rebuild. If you add a page to `docs/`, add it to `PAGES` in
`scripts/sync-docs.mjs` and to the `sidebar` in `astro.config.mjs`.

Pages deliberately **not** published: `docs/aws-infrastructure.md`,
`docs/DEMO.md`, `docs/report/**`, and everything in `spec/`. They are internal.
`docs/deploy.md` and `docs/onboarding.md` are routing stubs whose paths are
printed by `install.sh` and the server, so they are published (at `/deploy/` and
`/onboarding/`) but get no sidebar entry.

## Run it locally

```bash
cd docs-site
npm ci
npm run dev            # http://localhost:4321 — sync + hot reload
```

## Build

```bash
npm ci
npm run build          # -> docs-site/dist/
npm run check-links    # verifies every internal link and #anchor in dist/
```

Serve the built output the way production does, with a dumb static file server:

```bash
npx serve dist         # or: cd dist && python3 -m http.server 8099
```

## Where it deploys

`docs-site/dist/` is the artefact. Upload its contents to the docs bucket and
invalidate CloudFront:

```bash
aws s3 sync docs-site/dist/ s3://<docs-bucket>/ --delete
aws cloudfront create-invalidation --distribution-id <id> --paths '/*'
```

Two settings the distribution needs, because the site uses directory-style URLs
(`/reference/environment/` → `reference/environment/index.html`):

- **Default root object** `index.html`.
- A **CloudFront Function / origin-request rewrite** appending `index.html` to
  any request path ending in `/`, or use an S3 *website* origin, which does that
  itself. Without one of the two, deep links 404.
- `dist/404.html` is the custom error page for 404s.

The canonical hostname is set in `astro.config.mjs` (`site:`) and feeds the
sitemap and canonical tags — change it there if the domain changes.

## Theme

`src/styles/codex.css` restyles Starlight down to the Wikimedia Codex tokens the
org wiki uses (`../web/app/globals.css` documents why they were chosen), so the
docs read as a sibling of the wiki: serif headings with a hairline rule, 14px
sans body, 960px column, `#36c` links.

**The site is light only, deliberately.** Starlight's theme picker and its
`prefers-color-scheme`-reading ThemeProvider are replaced by
`src/components/Empty.astro` and `src/components/LightThemeProvider.astro`, and
every colour token is declared on plain `:root` as well as on both `data-theme`
values — so there is no state, and no JS-disabled path, in which a dark page can
appear. Do not add a dark mode or a `prefers-color-scheme` block.

Search is Pagefind, bundled by Starlight at build time. It is deliberately not
an AI/LLM search: no hosted service, no API key, no network dependency.
