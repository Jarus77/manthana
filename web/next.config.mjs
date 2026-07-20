/**
 * The wiki client is served same-origin with the API — no CORS anywhere.
 *
 * In dev the browser talks to :3000 and these rewrites proxy the API calls
 * through to the FastAPI server, so the `manthana_admin` cookie (httponly,
 * path=/ui) is set and sent on the SAME origin the pages came from. Pointing
 * the client at http://127.0.0.1:8000 directly instead would make every call
 * cross-origin and the cookie would be dropped.
 *
 * In prod there is no rewrite: Caddy routes /ui/*, /v1/*, /docs and /healthz to
 * the server and everything else to this app (see deploy/Caddyfile).
 */
const API_ORIGIN = process.env.MANTHANA_API_ORIGIN ?? 'http://127.0.0.1:8000'

/** @type {import('next').NextConfig} */
const nextConfig = {
  // Traced standalone bundle — see web/Dockerfile.
  output: 'standalone',
  async rewrites() {
    return process.env.NODE_ENV === 'production'
      ? []
      : [{ source: '/ui/:path*', destination: `${API_ORIGIN}/ui/:path*` }]
  },
}

export default nextConfig
