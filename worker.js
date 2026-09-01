/* Field Kit — CORS proxy (Cloudflare Worker)
   Gives the market pages a fast, reliable proxy instead of flaky public relays.
   Deploy: dash.cloudflare.com -> Workers & Pages -> Create Worker -> paste this -> Deploy.
   Then paste the worker's URL into Market Board via the ⚡ button. See PROXY-SETUP.md.

   Locked to a small host allowlist so it can't be abused as an open proxy. */

const ALLOW = new Set([
  'query1.finance.yahoo.com',
  'query2.finance.yahoo.com',
  'raw.githubusercontent.com'
]);

export default {
  async fetch(request) {
    if (request.method === 'OPTIONS') return cors(new Response(null, { status: 204 }));

    const url = new URL(request.url);
    const target = url.searchParams.get('url');
    if (!target) return cors(new Response('Add ?url=<encoded target URL>', { status: 400 }));

    let t;
    try { t = new URL(target); } catch { return cors(new Response('Bad url', { status: 400 })); }
    if (t.protocol !== 'https:' || !ALLOW.has(t.hostname)) {
      return cors(new Response('Host not allowed: ' + t.hostname, { status: 403 }));
    }

    try {
      const upstream = await fetch(t.toString(), {
        method: 'GET',
        headers: { 'User-Agent': 'FieldKit/1.0', 'Accept': 'application/json,text/plain,*/*' },
        cf: { cacheTtl: 15, cacheEverything: true }   // 15s edge cache — plenty for quotes
      });
      const resp = new Response(upstream.body, { status: upstream.status });
      resp.headers.set('Content-Type', upstream.headers.get('content-type') || 'application/json');
      return cors(resp);
    } catch (e) {
      return cors(new Response('Upstream error: ' + e.message, { status: 502 }));
    }
  }
};

function cors(r) {
  r.headers.set('Access-Control-Allow-Origin', '*');
  r.headers.set('Access-Control-Allow-Methods', 'GET,OPTIONS');
  r.headers.set('Access-Control-Allow-Headers', '*');
  return r;
}
