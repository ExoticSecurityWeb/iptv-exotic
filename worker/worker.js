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

    // ?info=1 → d'où sort le Worker (datacenter = code aéroport : CDG/MRS = France, IAD = Washington)
    if (u.searchParams.get('info') === '1') {
      const colo = req.cf && req.cf.colo;
      const placement = req.headers.get('cf-placement') || null;
      try {
        const i = await (await fetch('https://ipinfo.io/json', { headers: { 'User-Agent': UA } })).json();
        return json({ country: i.country, ip: i.ip, org: i.org, colo, placement });
      } catch (e) {
        return json({ error: 'info indisponible', colo, placement });
      }
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
