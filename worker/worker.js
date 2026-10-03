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

async function probe(target, headers, ms) {
  const r = await fetch(target, { headers, redirect: 'follow', signal: AbortSignal.timeout(ms) });
  let hls = null;
  if (/\.m3u8?(\?|$)/i.test(target) && r.status < 400 && r.body) {
    const reader = r.body.getReader();
    const { value } = await reader.read();
    hls = new TextDecoder().decode(value || new Uint8Array()).includes('#EXTM3U');
    await reader.cancel();
  } else if (r.body) {
    await r.body.cancel();
  }
  return { status: r.status, hls };
}

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

    // 1) navigateur (avec ou sans Origin/Referer), 2) si 401/403/429/451 : on réessaie
    //    avec des User-Agent de vrais lecteurs (VLC, Kodi, okhttp) — certains serveurs n'acceptent que ça.
    const base = { Accept: '*/*', 'Cache-Control': 'no-cache' };
    const first = { ...base, 'User-Agent': UA };
    if (u.searchParams.get('ref') !== '0') {
      first.Origin = 'https://exoticsecurityweb.github.io';
      first.Referer = 'https://exoticsecurityweb.github.io/';
    }
    const tries = [
      ['navigateur', first],
      ['vlc', { ...base, 'User-Agent': 'VLC/3.0.20 LibVLC/3.0.20' }],
      ['kodi', { ...base, 'User-Agent': 'Kodi/21.0 (Linux; Android 13) ExoPlayerLib/2.18.7' }],
      ['okhttp', { ...base, 'User-Agent': 'okhttp/4.12.0' }],
    ];

    try {
      let last = null;
      let used = 0;
      for (let i = 0; i < tries.length; i++) {
        try {
          last = await probe(target, tries[i][1], i === 0 ? 8000 : 4000);
          used = i;
        } catch (e) {
          if (i === 0) throw e;
          break;
        }
        if (![401, 403, 429, 451].includes(last.status)) break;
      }
      return json({ ...last, via: last.status < 400 && used > 0 ? tries[used][0] : null });
    } catch (e) {
      const msg = String(e && e.name) === 'TimeoutError' ? 'Timeout' : 'Connexion impossible';
      return json({ error: msg });
    }
  },
};
