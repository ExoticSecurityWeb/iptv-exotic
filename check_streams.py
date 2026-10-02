#!/usr/bin/env python3
"""
Exotic TV — Stream Checker v5
- Plus de cache : playlist + iptv-org rechargées à chaque run (no-cache + timestamp)
- Remplacements UNIQUES : une même URL n'est jamais proposée deux fois,
  ni si elle est déjà utilisée par une chaîne vivante de la playlist
- Vérifie le vrai contenu des .m3u8 (#EXTM3U), pas juste le code HTTP
- Tests en parallèle + 2e tentative avant de déclarer une chaîne morte
- Rapport stream-report.json
"""

import os
import re
import json
import time
import requests
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

# ─── CONFIG ──────────────────────────────────────────────────────────────────
M3U_URL         = "https://exoticsecurityweb.github.io/iptv-exotic/exotic-tv-playlist.m3u"
IPTV_ORG_FR_URL = "https://iptv-org.github.io/iptv/countries/fr.m3u"
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "")
TIMEOUT         = 10
TIMEOUT_ARCHIVE = 20
WORKERS         = 10     # tests en parallèle
RETRIES         = 2      # tentatives avant "mort"
MAX_ORG_TRIES   = 3      # candidats iptv-org testés par chaîne
REPORT_FILE     = "stream-report.json"
# Codes = le serveur GitHub est refusé (géo/IP/anti-bot) ≠ chaîne morte
BLOCKED_CODES   = (401, 403, 429, 451)

# Vérification "vue d'ailleurs" (optionnel, via variables d'environnement) :
#  CHECK_WORKER     = URL de ton Worker Cloudflare de test (worker.js)
#  CHECK_WORKER_KEY = clé secrète du Worker
#  CHECK_PROXY      = proxy HTTP/SOCKS pour les tests de flux, ex. socks5h://127.0.0.1:1055
# Seuls les TESTS DE FLUX passent par là (pas Discord, ni les playlists).
CHECK_WORKER     = os.environ.get("CHECK_WORKER", "").strip()
CHECK_WORKER_KEY = os.environ.get("CHECK_WORKER_KEY", "").strip()
CHECK_PROXY      = os.environ.get("CHECK_PROXY", "").strip()
PROXIES          = {'http': CHECK_PROXY, 'https': CHECK_PROXY} if CHECK_PROXY else None
_worker_warned   = False

BLACKLIST_REPLACEMENT_HOSTS = [
    "69.64.57.208",
]

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'

# ─── BASE DE REMPLACEMENT MANUELLE ───────────────────────────────────────────
# "Nom exact dans le M3U": ["url1", "url2", ...]
# Une URL déjà utilisée ailleurs (playlist ou autre remplacement) est ignorée.
REPLACEMENT_DB = {
    "TF1 (720p)": [
        "https://raw.githubusercontent.com/Paradise-91/ParaTV/main/streams/tf1/tf1-hd.m3u8",
    ],
    "France 2 (1080p)": [
        "https://raw.githubusercontent.com/schumijo/iptv/main/playlists/francetv/france2.m3u8",
    ],
    "France 3": [
        "https://raw.githubusercontent.com/schumijo/iptv/main/playlists/francetv/france3.m3u8",
    ],
    "France 4 (1080p)": [
        "https://raw.githubusercontent.com/schumijo/iptv/main/playlists/francetv/france4.m3u8",
    ],
    "France 5 (1080p)": [
        "https://raw.githubusercontent.com/schumijo/iptv/main/playlists/francetv/france5.m3u8",
    ],
    "M6 (720p) [Geo-blocked] [Geo-Blocked]": [
        "https://origin-m6web.live.6cloud.fr/out/v1/6play/6play-m6/cmaf_q2hyb21h/hls-short-sd.m3u8",
        "https://lbcdn.6cloud.fr/resource/m6web/l/m6_hls_sd_short_q2hyb21h.m3u8?groups[]=m6web-live-m6_ext",
    ],
    "Arte (720p) [Geo-blocked]": [
        "https://raw.githubusercontent.com/schumijo/iptv/main/playlists/francetv/arte.m3u8",
    ],
    "Arte HD (1080p)": [
        "https://raw.githubusercontent.com/schumijo/iptv/main/playlists/francetv/arte.m3u8",
    ],
    "W9 (720p) [Geo-blocked] [Geo-Blocked]": [
        "https://origin-m6web.live.6cloud.fr/out/v1/6play/6play-w9/cmaf_q2hyb21h/hls-short-sd.m3u8",
        "https://lbcdn.6cloud.fr/resource/m6web/l/w9_hls_sd_short_q2hyb21h.m3u8?groups[]=m6web-live-w9_ext",
    ],
    "C Star (720p) [Geo-Blocked]": [
        "https://raw.githubusercontent.com/schumijo/iptv/main/playlists/canalplus/cstar.m3u8",
    ],
    "Gulli (720p) [Geo-Blocked]": [
        "https://origin-m6web.live.6cloud.fr/out/v1/6play/6play-gulli/cmaf_q2hyb21h/hls-short-sd.m3u8",
    ],
    "CNews (1080p) [Geo-Blocked]": [
        "https://raw.githubusercontent.com/schumijo/iptv/main/playlists/canalplus/cnews.m3u8",
    ],
    "Canal+ en clair (720p) [Geo-blocked] [Geo-Blocked]": [
        "https://raw.githubusercontent.com/Paradise-91/ParaTV/main/streams/canalplus/canalplusclair-hd.m3u8",
    ],
    "TF1 HD (720p) [Geo-Blocked]": [
        "https://raw.githubusercontent.com/Paradise-91/ParaTV/main/streams/tf1/tf1-hd.m3u8",
    ],
    "TF1 Series Films (1080p) [Geo-Blocked]": [
        "https://viamotionhsi.netplus.ch/live/eds/hd1/browser-HLS8/hd1.m3u8",
    ],
    "TFX (1080p) [Geo-Blocked]": [
        "https://viamotionhsi.netplus.ch/live/eds/nt1/browser-HLS8/nt1.m3u8",
    ],
    "RMC Decouverte (1080p) [Geo-Blocked]": [
        "https://d16zzycxcd0m0r.cloudfront.net/v1/master/3722c60a815c199d9c0ef36c5b73da68a62b09d1/cc-hixvx5kymecr9/RMC_Decouverte_FR.m3u8",
    ],
    "RMC Life (720p) [Geo-Blocked]": [
        "https://d3dcdjv6dx07iz.cloudfront.net/v1/master/3722c60a815c199d9c0ef36c5b73da68a62b09d1/cc-eaaww2dyp3iih/RMC_Life_FR.m3u8",
    ],
    "RMC Story (1080p) [Geo-Blocked]": [
        "https://d15aro46bnpfm8.cloudfront.net/v1/master/3722c60a815c199d9c0ef36c5b73da68a62b09d1/cc-fqkqiax1078up/RMC_Story_FR.m3u8",
    ],
    "LCI HD (720p) [Geo-Blocked]": [
        "https://raw.githubusercontent.com/pinkisso/mored/refs/heads/main/res/26-1/lci1.m3u8",
    ],
    "Franceinfo (720p) [Geo-Blocked]": [
        "https://raw.githubusercontent.com/schumijo/iptv/main/playlists/francetv/franceinfo.m3u8",
    ],
    "France 2 HD (720p) [Geo-Blocked]": [
        "https://raw.githubusercontent.com/schumijo/iptv/main/playlists/francetv/france2.m3u8",
    ],
    "France 4 HD (720p) [Geo-Blocked]": [
        "https://raw.githubusercontent.com/schumijo/iptv/main/playlists/francetv/france4.m3u8",
    ],
    "France 5 HD (720p) [Geo-Blocked]": [
        "https://raw.githubusercontent.com/schumijo/iptv/main/playlists/francetv/france5.m3u8",
    ],
    "LCP (720p) [Geo-Blocked]": [
        "https://raw.githubusercontent.com/ipstreet312/freeiptv/master/ressources/dmotion/py/lcpan/lcp1.m3u8",
    ],
    "NOVO19 (720p) [Geo-Blocked]": [
        "https://viamotionhsi.netplus.ch/live/eds/novo19/browser-HLS8/novo19.m3u8",
    ],
    "Canal J HD (720p) [Geo-Blocked]": [
        "https://viamotionhsi.netplus.ch/live/eds/canalj/browser-HLS8/canalj.m3u8",
    ],
    "Euronews French HD (720p) [Geo-Blocked]": [
        "https://euronews-live-fre-fr.fast.rakuten.tv/v1/master/0547f18649bd788bec7b67b746e47670f558b6b2/production-LiveChannel-6564/bitok/e/26032/euronews-fr.m3u8",
    ],
    "L'Equipe (1080p)": [
        "https://dq37unyetkpcz.cloudfront.net/v1/master/3722c60a815c199d9c0ef36c5b73da68a62b09d1/cc-m04j89j7k5gtp/LEquipe_FR.m3u8",
    ],
    "Public Senat 24/24": [
        "https://raw.githubusercontent.com/Paradise-91/ParaTV/main/streams/publicsenat/publicsenat-dm.m3u8",
    ],
    "BFM2 (1080p)": [
        "https://ncdn-live-bfm.pfd.sfr.net/shls/LIVE$BFM2/index.m3u8?start=LIVE&end=END",
    ],
    "TV5Monde France Belgique Suisse Monaco (1080p) [Geo-blocked]": [
        "https://ott.tv5monde.com/Content/HLS/Live/channel(fbs)/index.m3u8",
    ],
    "TV5Monde France Belgium Switzerland Monaco HD (720p) [Geo-Blocked]": [
        "https://ott.tv5monde.com/Content/HLS/Live/channel(fbs)/index.m3u8",
    ],
    "TiVi5 Monde [Geo-blocked]": [
        "https://ott.tv5monde.com/Content/HLS/Live/channel(tivi5)/index.m3u8",
    ],
    "TV5Monde Info (1080p) [Geo-blocked]": [
        "https://ott.tv5monde.com/Content/HLS/Live/channel(info)/index.m3u8",
    ],
}

# ─── UTILS ────────────────────────────────────────────────────────────────────
def now_utc():
    return datetime.now(timezone.utc)

def fresh_url(url):
    """Ajoute un paramètre unique pour casser le cache GitHub Pages / raw / CDN"""
    return url + ('&' if '?' in url else '?') + f"_={int(time.time())}"

def fresh_get(url, timeout=20):
    """GET sans cache"""
    return requests.get(
        fresh_url(url),
        timeout=timeout,
        headers={'User-Agent': UA, 'Cache-Control': 'no-cache', 'Pragma': 'no-cache'},
    )

def norm(url):
    """Forme normalisée d'une URL pour détecter les doublons"""
    return url.strip().split('#')[0].rstrip('/').lower()

def clean_name(name):
    """Nom sans suffixes de qualité/geo : 'Arte (720p) [Geo-blocked]' -> 'arte'"""
    return re.sub(r'\s*[\(\[].*?[\)\]]\s*', ' ', name).strip().lower()

def is_blacklisted(url):
    return any(host in url for host in BLACKLIST_REPLACEMENT_HOSTS)

# ─── PARSE M3U ────────────────────────────────────────────────────────────────
def parse_m3u(text):
    channels = []
    current = None
    for line in text.strip().split('\n'):
        line = line.strip()
        if line.startswith('#EXTINF'):
            name_m  = re.search(r',([^,]+)$', line)
            logo_m  = re.search(r'tvg-logo="([^"]*)"', line)
            group_m = re.search(r'group-title="([^"]*)"', line)
            id_m    = re.search(r'tvg-id="([^"]*)"', line)
            raw_name = name_m.group(1).strip() if name_m else ''
            if 'Safari/' in raw_name or 'Chrome/' in raw_name or len(raw_name) > 80:
                current = None
                continue
            current = {
                'name':   raw_name or 'Sans nom',
                'logo':   logo_m.group(1) if logo_m else '',
                'group':  group_m.group(1) if group_m else '',
                'tvg_id': id_m.group(1) if id_m else '',
                'url':    '',
            }
        elif line and not line.startswith('#') and current:
            current['url'] = line
            channels.append(current)
            current = None
    return channels

# ─── IPTV-ORG FR ─────────────────────────────────────────────────────────────
def load_iptv_org_fr():
    """Retourne dict nom_nettoyé -> [urls] (plusieurs candidats par chaîne)"""
    print("📡 Chargement iptv-org/fr.m3u (sans cache)…")
    try:
        r = fresh_get(IPTV_ORG_FR_URL, timeout=20)
        r.raise_for_status()
        db = {}
        for ch in parse_m3u(r.text):
            if not ch['url'] or is_blacklisted(ch['url']):
                continue
            db.setdefault(clean_name(ch['name']), []).append(ch['url'])
        print(f"✅ {len(db)} chaînes FR chargées depuis iptv-org\n")
        return db
    except Exception as e:
        print(f"⚠️ Impossible de charger iptv-org : {e}\n")
        return {}

# ─── CHECK URL ────────────────────────────────────────────────────────────────
_check_cache = {}   # évite de retester 2x la même URL dans un run

def _check_via_worker(url, timeout, browserlike):
    r = requests.get(
        CHECK_WORKER,
        params={'url': url, 'key': CHECK_WORKER_KEY, 'ref': '1' if browserlike else '0'},
        timeout=timeout + 5,
    )
    r.raise_for_status()
    d = r.json()
    if d.get('error'):
        err = str(d['error'])[:80]
        return False, 0, err
    st = int(d.get('status', 0))
    if st >= 400:
        return False, st, f"HTTP {st}"
    if d.get('hls') is False:
        return False, st, "Pas une vraie playlist HLS"
    return True, st, None

def _check_once(url, timeout, browserlike=True):
    global _worker_warned
    if CHECK_WORKER:
        try:
            return _check_via_worker(url, timeout, browserlike)
        except Exception as e:
            if not _worker_warned:
                print(f"⚠️ Worker de test injoignable ({str(e)[:60]}) → test direct")
                _worker_warned = True
    headers = {
        'User-Agent': UA,
        'Accept': '*/*',
        'Cache-Control': 'no-cache',
    }
    if browserlike:
        headers['Origin'] = 'https://exoticsecurityweb.github.io'
        headers['Referer'] = 'https://exoticsecurityweb.github.io/'

    is_hls = '.m3u8' in url.lower() or '.m3u' in url.lower()
    try:
        if not is_hls:
            r = requests.head(url, timeout=timeout, headers=headers, allow_redirects=True, proxies=PROXIES)
            if r.status_code < 400:
                return True, r.status_code, None
        r = requests.get(url, timeout=timeout, headers=headers, allow_redirects=True, stream=True, proxies=PROXIES)
        try:
            if r.status_code >= 400:
                return False, r.status_code, f"HTTP {r.status_code}"
            if is_hls:
                head = next(r.iter_content(2048), b'')
                if b'#EXTM3U' not in head:
                    return False, r.status_code, "Pas une vraie playlist HLS"
            return True, r.status_code, None
        finally:
            r.close()
    except requests.exceptions.Timeout:
        return False, 0, "Timeout"
    except requests.exceptions.ConnectionError:
        return False, 0, "DNS mort / Connexion impossible"
    except Exception as e:
        return False, 0, str(e)[:80]

def check_url(url, timeout=None):
    if url in _check_cache:
        return _check_cache[url]
    if timeout is None:
        timeout = TIMEOUT_ARCHIVE if 'archive.org' in url else TIMEOUT
    result = (False, 0, "?")
    for attempt in range(RETRIES):
        result = _check_once(url, timeout)
        if result[0]:
            break
        if result[1] in BLOCKED_CODES:
            break   # inutile de réessayer pareil
        if attempt < RETRIES - 1:
            time.sleep(1)
    # 403 & co : on retente SANS Origin/Referer (certains serveurs les refusent)
    if not result[0] and result[1] in BLOCKED_CODES:
        retry = _check_once(url, timeout, browserlike=False)
        if retry[0]:
            result = retry
        else:
            # Toujours refusé : le serveur répond, donc l'URL existe.
            # On ne peut pas conclure "morte" depuis un datacenter (géo/IP).
            result = (None, result[1], f"HTTP {result[1]} — refusé au serveur (géo/IP), non vérifiable")
    _check_cache[url] = result
    return result

# ─── REMPLACEMENT UNIQUE ─────────────────────────────────────────────────────
def find_replacement(channel, iptv_org_db, used):
    """
    `used` = set d'URLs normalisées déjà prises (playlist vivante + remplacements
    déjà proposés). Un candidat présent dans `used` est ignoré.
    """
    own = norm(channel['url'])

    def usable(url):
        n = norm(url)
        return n != own and n not in used

    # 1. DB manuelle
    for url in REPLACEMENT_DB.get(channel['name'], []):
        if not usable(url):
            continue
        ok, _, _ = check_url(url)
        if ok:
            return url, "DB manuelle"

    # 2. iptv-org : plusieurs candidats, nom exact (suffixes retirés)
    tried = 0
    for url in iptv_org_db.get(clean_name(channel['name']), []):
        if not usable(url):
            continue
        ok, _, _ = check_url(url)
        tried += 1
        if ok:
            return url, "iptv-org"
        if tried >= MAX_ORG_TRIES:
            break

    return None, None

# ─── DISCORD ─────────────────────────────────────────────────────────────────
def send_discord(embeds):
    if not DISCORD_WEBHOOK:
        print("⚠️  DISCORD_WEBHOOK non configuré")
        return
    try:
        r = requests.post(DISCORD_WEBHOOK, json={"embeds": embeds[:10]}, timeout=10)
        r.raise_for_status()
        time.sleep(1.2)
    except Exception as e:
        print(f"❌ Erreur Discord : {e}")

def cut(s, n):
    s = str(s)
    return s if len(s) <= n else s[:n - 1] + "…"

def build_embed(channel, error, replacement=None, source=None, duplicate_of=None):
    fields = [
        {"name": "📂 Groupe", "value": cut(channel['group'] or "—", 1000), "inline": True},
        {"name": "🔴 Erreur", "value": cut(error, 1000), "inline": True},
        {"name": "🔗 URL morte", "value": f"```{cut(channel['url'], 900)}```", "inline": False},
    ]
    if duplicate_of:
        fields.append({
            "name": "♻️ Doublon dans la playlist",
            "value": cut(f"Une autre entrée « {duplicate_of} » fonctionne déjà. "
                         "Supprime cette ligne, ou renomme-la si son URL correspond à une autre chaîne.", 1000),
            "inline": False,
        })
        color, icon = 0x94a3b8, "♻️"
    elif replacement:
        fields += [
            {"name": f"✅ Nouvelle URL ({source})",
             "value": f"```{cut(replacement, 900)}```", "inline": False},
            {"name": "💡 Comment l'appliquer",
             "value": "[Ouvre l'éditeur M3U](https://exoticsecurityweb.github.io/iptv-exotic/) → clique la chaîne → remplace l'URL → Exporter M3U",
             "inline": False},
        ]
        color, icon = 0x4ade80, "🔄"
    else:
        fields.append({
            "name": "😓 Aucun remplacement trouvé",
            "value": "Aucune URL libre et fonctionnelle (DB manuelle / iptv-org/fr).",
            "inline": False,
        })
        color, icon = 0xf87171, "💀"

    return {
        "title": cut(f"{icon} {channel['name']}", 250),
        "color": color,
        "fields": fields,
        "footer": {"text": "Exotic TV Stream Checker • Pink Paradise 🌴"},
        "timestamp": now_utc().isoformat(),
    }

# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    now = now_utc().strftime('%d/%m/%Y à %H:%M UTC')
    print(f"\n🌴 Exotic TV Stream Checker v5 — {now}")
    print("─" * 60)
    mode = "Worker" if CHECK_WORKER else ("Proxy" if CHECK_PROXY else "direct (IP GitHub)")
    print(f"🛰️  Mode de test des flux : {mode}")
    net_line = f"🛰️ Test via : {mode}"          # affiché aussi dans le message Discord
    if CHECK_WORKER:
        try:
            d = requests.get(CHECK_WORKER, params={'key': CHECK_WORKER_KEY, 'info': '1'}, timeout=15).json()
            if d.get('error') == 'forbidden':
                net_line = "🛰️ Worker : clé refusée (KEY du Worker ≠ CHECK_WORKER_KEY) → test direct"
                print(f"   ↳ ⚠️ {net_line}")
            else:
                print(f"   ↳ Worker : pays {d.get('country', '?')} · IP {d.get('ip', '?')} · "
                      f"datacenter {d.get('colo', '?')} · {d.get('org', '?')}")
                net_line = f"🛰️ Test via : Worker · pays {d.get('country', '?')} · datacenter {d.get('colo', '?')}"
        except Exception as e:
            net_line = "🛰️ Worker injoignable → test direct (IP GitHub)"
            print(f"   ↳ ⚠️ Worker injoignable : {str(e)[:60]}")

    print("📥 Chargement de la playlist (sans cache)…")
    try:
        r = fresh_get(M3U_URL, timeout=15)
        r.raise_for_status()
        channels = parse_m3u(r.text)
        print(f"✅ {len(channels)} chaînes trouvées\n")
    except Exception as e:
        print(f"❌ {e}")
        send_discord([{"title": "❌ Playlist inaccessible", "description": cut(e, 1500),
                       "color": 0xf87171, "footer": {"text": "Exotic TV • Pink Paradise 🌴"}}])
        return

    iptv_org_db = load_iptv_org_fr()

    # 1) Test de toutes les chaînes en parallèle
    print(f"🔎 Test en parallèle ({WORKERS} threads)…\n")
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        results = list(ex.map(lambda c: check_url(c['url']), channels))

    alive, dead, unknown = [], [], []
    for i, (ch, (ok, code, err)) in enumerate(zip(channels, results)):
        if ok:
            print(f"[{i+1:3}/{len(channels)}] {ch['name']:<42} ✅  {code}")
            alive.append(ch)
        elif ok is None:
            print(f"[{i+1:3}/{len(channels)}] {ch['name']:<42} ❓  {err}")
            unknown.append({**ch, 'error': err})
        else:
            print(f"[{i+1:3}/{len(channels)}] {ch['name']:<42} ❌  {err}")
            dead.append({**ch, 'error': err, 'replacement': None, 'source': None, 'duplicate_of': None})

    # 2) Remplacements uniques : on réserve d'abord toutes les URLs vivantes
    # les "non vérifiables" restent en place (pas de remplacement) et sont réservées
    used = {norm(c['url']) for c in alive} | {norm(c['url']) for c in unknown}
    print(f"\n🔄 Recherche de remplacements uniques pour {len(dead)} chaînes…")
    # Doublon : une entrée du même nom fonctionne déjà → pas de r