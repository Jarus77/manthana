// @ts-check
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';

/*
 * Manthana docs site.
 *
 * Astro + Starlight, built to a plain directory of static files (`dist/`) that
 * is served from S3 + CloudFront. There is no Node server at runtime: search is
 * a client-side Pagefind index generated at build time, and every page is
 * prerendered HTML.
 *
 * The theme is overridden down to the Wikimedia Codex tokens the org wiki uses
 * (see ../web/app/globals.css) so this reads as a sibling of the wiki. It is
 * LIGHT ONLY — ThemeProvider and ThemeSelect are replaced with light-only stubs
 * so there is no dark mode to accidentally engage.
 */
export default defineConfig({
  // Feeds canonical tags and the sitemap, so it must be the hostname the site is
  // actually served from — CloudFront in front of a private S3 bucket, alongside
  // api.latentspaces.in on the same Route53 zone.
  site: 'https://docs.latentspaces.in',
  trailingSlash: 'always',
  build: { format: 'directory' },
  integrations: [
    starlight({
      title: 'Manthana docs',
      description:
        'Manthana turns the AI coding sessions your team is already having into a shared, searchable org wiki — without anyone writing documentation, and without anyone being surveilled.',
      tagline: 'The wiki your team never had to write',
      customCss: ['./src/styles/codex.css'],
      // Expressive Code ships a light AND a dark syntax theme by default and
      // picks between them with a `prefers-color-scheme` media query — which
      // would put dark code blocks on this light page for anyone on a dark-mode
      // machine. One theme, no media query, no selector switching.
      expressiveCode: {
        themes: ['github-light'],
        useDarkModeMediaQuery: false,
        themeCssSelector: false,
        // No macOS-style terminal chrome or editor tabs around code blocks —
        // these docs are full of ASCII pipeline diagrams and shell one-liners,
        // and a fake window frame around them is noise the wiki doesn't have.
        defaultProps: { frame: 'none' },
        styleOverrides: {
          borderRadius: '0',
          borderColor: 'var(--border-subtle)',
          codeBackground: 'var(--neutral)',
          frames: { shadowColor: 'transparent' },
        },
      },
      components: {
        // Light only, deliberately: no toggle, no prefers-color-scheme.
        ThemeProvider: './src/components/LightThemeProvider.astro',
        ThemeSelect: './src/components/Empty.astro',
      },
      social: [
        {
          icon: 'github',
          label: 'GitHub',
          href: 'https://github.com/Jarus77/manthana',
        },
      ],
      editLink: {
        baseUrl: 'https://github.com/Jarus77/manthana/edit/main/docs/',
      },
      pagination: true,
      lastUpdated: false,
      credits: false,
      sidebar: [
        {
          label: 'Founders & admins',
          items: [
            { label: 'Overview', link: '/founders/' },
            { label: 'Provisioning an org', link: '/founders/provisioning/' },
            { label: 'Privacy & budgets', link: '/founders/privacy-and-budgets/' },
            { label: 'Reading the wiki', link: '/founders/reading-the-wiki/' },
            { label: 'Operating it', link: '/founders/operating/' },
          ],
        },
        {
          label: 'Engineers',
          items: [
            { label: 'Overview', link: '/engineers/' },
            { label: 'What happens to your data', link: '/engineers/your-data/' },
            { label: 'Day to day', link: '/engineers/daily/' },
          ],
        },
        {
          label: 'Solo & independent',
          items: [{ label: 'Running Manthana solo', link: '/solo/' }],
        },
        {
          label: 'Self-hosting',
          items: [
            { label: 'Deploying the server', link: '/self-hosting/' },
            { label: 'The web client', link: '/self-hosting/web-client/' },
            { label: 'Operations', link: '/self-hosting/operations/' },
          ],
        },
        {
          label: 'Reference',
          items: [
            { label: 'How Manthana works', link: '/reference/architecture/' },
            { label: 'Privacy & security model', link: '/reference/privacy/' },
            { label: 'CLI reference', link: '/reference/cli/' },
            { label: 'Environment variables', link: '/reference/environment/' },
            { label: 'Troubleshooting', link: '/troubleshooting/' },
          ],
        },
      ],
    }),
  ],
});
