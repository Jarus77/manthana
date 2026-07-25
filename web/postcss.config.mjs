/**
 * Tailwind v4 is a PostCSS plugin and nothing else — no tailwind.config.js, no
 * content globs. The theme lives in `@theme inline` inside app/globals.css, and
 * class scanning is automatic.
 */
const config = {
  plugins: {
    '@tailwindcss/postcss': {},
  },
}

export default config
