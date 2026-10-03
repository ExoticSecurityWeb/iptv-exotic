// Exotic TV — Worker de vérification de flux (iptv-cheaker)
// Secret à créer dans Cloudflare : KEY (même valeur que CHECK_WORKER_KEY côté GitHub)
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36';

const CORS = {
  'access-control-allow-origin': '*',
  'access-control-allow-headers': 'content-type',
  'access-control-allow-methods': 'GET, OPTIONS',
};

const json = (o, status = 200) =>
  new Response(JSON.stringify(o), {
    status,
    headers: { 'content-type': 'application/json', ...CORS },
  });

export default {
  async fetch(req, env) {
    if (req.method === 'OPTIONS') return new Response(null, { status: 204, headers: CORS });

    const u = new URL(req.url);
    if (!env.KEY || u.searchParams.get('key') !== env.KEY) return json({ error: 'forbidden' }, 403);

    // ?info=1 → où le Worker S'EXÉCUTE vraiment (colo = code aéroport : CDG/MRS = France)
    // edge = datacenter d'entrée de la requête ; avec un placement actif, colo ≠ edge.
    if (u.searchParams.get('info') === '1') {
      const edge = req.cf && req.cf.colo;
      const placement = req.headers.get('cf-placement') || null;
      const out = { edge, placement, colo: null, country: null, ip: null };
      try {
        const t = await (await fetch('https://www.cloudflare.com/cdn-cgi/trace', { headers: { 'User-Agent': UA } })).text();
        const kv = Object.fromEntries(t.trim().split('\n').map((l) => l.split('=')));
        out.colo = kv.colo || null;
        out.country = kv.loc || null;
        out.ip = kv.ip || null;
      } catch (e) {}
      if (!out.country) {
        try {
          const i = await (await fetch('https://ipinfo.io/json', { headers: { 'User-Agent': UA } })).json();
          out.country = i.country || null;
          out.ip = out.ip || i.ip || null;
        } catch (e) {}
      }
      if (!out.colo) out.colo = edge;
      return json(out);
    }

    const target = u.searchParams.get('url') || '';
    if (!/^https?:\/\//i.test(target)) return json({ error: 'URL invalide' }, 400);

    const headers = { 'User-Agent': UA, Accept: '*/*', 'Cache-Control': 'no-cache' };
    if (u.searchParams.get('ref') !== '0') {
      headers.Origin = 'https://exoticsecurityweb.github.io';
      headers.Referer = 'https://exoticsecurityweb.github.io/';
    }

    try {
      const r = await fetch(target, { headers, redirect: 'follow', signal: AbortSignal.timeout(9000) });
      let hls = null;
      if (/\.m3u8?(\?|$)/i.test(target) && r.status < 400 && r.body) {
        const reader = r.body.getReader();
        const { value } = await reader.read();
        hls = new TextDecoder().decode(value || new Uint8Array()).includes('#EXTM3U');
        await reader.cancel();
      } else if (r.body) {
        await r.body.cancel();
      }
      return json({ status: r.status, hls });
    } catch (e) {
      const msg = String(e && e.name) === 'TimeoutError' ? 'Timeout' : 'Connexion impossible';
      return json({ error: msg });
    }
  },
};
