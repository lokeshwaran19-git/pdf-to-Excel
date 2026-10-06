/**
 * PDF-to-Excel Cloudflare Worker
 *
 * Responsibilities:
 *  1. Serve all static frontend assets (HTML/JS/CSS) from Workers Assets (KV)
 *  2. Proxy every /api/* request to the Render backend, transparently streaming
 *     the body in both directions.
 *  3. Inject CORS headers on EVERY response — including Render 502/503/504 —
 *     so the browser never sees a CORS policy error.
 *  4. Automatically retry up to 3× (with 2s back-off) when Render returns a
 *     5xx gateway error, silently absorbing cold-start latency.
 *  5. Respond to OPTIONS pre-flight requests immediately without hitting Render.
 *
 * Deploy: wrangler deploy
 */

// ── Configuration ─────────────────────────────────────────────────────────────
const RENDER_BACKEND = 'https://pdf-to-excel-n2ho.onrender.com';
const MAX_RETRIES    = 3;          // retry on 502/503/504
const RETRY_DELAY_MS = 2000;       // wait between retries
const CORS_HEADERS   = {
  'Access-Control-Allow-Origin':  '*',
  'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
  'Access-Control-Allow-Headers': '*',
  'Access-Control-Max-Age':       '86400',
};

// ── Helpers ───────────────────────────────────────────────────────────────────
function withCors(response) {
  const r = new Response(response.body, response);
  Object.entries(CORS_HEADERS).forEach(([k, v]) => r.headers.set(k, v));
  return r;
}

function corsError(status, message) {
  return new Response(JSON.stringify({ detail: message }), {
    status,
    headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
  });
}

function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

// ── Main fetch handler ────────────────────────────────────────────────────────
export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // 1. Preflight — answer immediately, never proxy to Render
    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: CORS_HEADERS });
    }

    // 2. API proxy — forward to Render backend
    if (url.pathname.startsWith('/api/')) {
      const targetURL = RENDER_BACKEND + url.pathname + url.search;

      // Clone the request body once so we can re-send on retries
      const reqBody = (request.method !== 'GET' && request.method !== 'HEAD')
        ? await request.arrayBuffer()
        : null;

      let lastResponse = null;
      for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
        try {
          const proxyReq = new Request(targetURL, {
            method:  request.method,
            headers: request.headers,
            body:    reqBody ? reqBody.slice(0) : undefined,
            redirect: 'follow',
          });

          const resp = await fetch(proxyReq);

          // On 502/503/504 — retry (Render cold-start / restart)
          if ((resp.status === 502 || resp.status === 503 || resp.status === 504)
              && attempt < MAX_RETRIES) {
            lastResponse = resp;
            await sleep(RETRY_DELAY_MS * attempt); // exponential-ish back-off
            continue;
          }

          // Success (or non-retryable error) — return with CORS headers
          return withCors(resp);

        } catch (err) {
          // Network-level failure
          if (attempt === MAX_RETRIES) {
            return corsError(502, `Backend unreachable after ${MAX_RETRIES} attempts: ${err.message}`);
          }
          await sleep(RETRY_DELAY_MS * attempt);
        }
      }

      // All retries exhausted — return last Render response with CORS headers
      if (lastResponse) return withCors(lastResponse);
      return corsError(502, 'Backend unavailable. Please try again in a moment.');
    }

    // 3. Static assets — served by Workers Assets (configured in wrangler.toml)
    //    Fall through to the asset binding if present.
    if (env.ASSETS) {
      return env.ASSETS.fetch(request);
    }

    // Fallback if no asset binding configured
    return new Response('Not found', { status: 404 });
  },
};
