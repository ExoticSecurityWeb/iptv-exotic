#!/usr/bin/env python3
"""
Exotic TV — Stream Checker v3
- Matching iptv-org strict (nom exact uniquement, blacklist IP louches)
- Timeout plus long pour Archive.org
- Notifie Discord uniquement pour les vraies URLs mortes
"""

import os
import re
import time
import requests
from datetime import datetime

# ─── CONFIG ──────────────────────────────────────────────────────────────────
M3U_URL         = "https://exoticsecurityweb.github.io/iptv-exotic/exotic-tv-playlist.m3u"
IPTV_ORG_FR_URL = "https://iptv-org.github.io/iptv/countries/fr.m3u"
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "")
TIMEOUT         = 10
TIMEOUT_ARCHIVE = 20   # Archive.org est plus lent
SLEEP_BTW       = 0.3

# IPs blacklistées UNIQUEMENT pour les remplacements auto iptv-org
# (ces serveurs proposent France 2 pour toutes les chaînes — inutile)
BLACKLIST_REPLACEMENT_HOSTS = [
    "69.64.57.208",
]

# ─── BASE DE REMPLACEMENT MANUELLE ───────────────────────────────────────────
#
# FORMAT :
#   "Nom exact de la chaîne dans le M3U": ["url1", "url2", ...]
#
# AJOUTER une chaîne :
#   "Ma Chaîne": ["https://stream.url/playlist.m3u8"],
#
# SUPPRIMER une chaîne :
#   Efface juste la ligne correspondante
#
# AJOUTER une URL alternative :
#   "Ma Chaîne": ["url_principale", "url_backup"],
#
REPLACEMENT_DB = {
    # ── TNT France ────────────────────────────────────────────────────────────
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

# ─── PARSE M3U ────────────────────────────────────────────────────────────────
def parse_m3u(text):
    channels = []
    lines = text.strip().split('\n')
    current = None
    for line in lines:
        line = line.strip()
        if line.startswith('#EXTINF'):
            name_m  = re.search(r',([^,]+)$', line)
            logo_m  = re.search(r'tvg-logo="([^"]*)"', line)
            group_m = re.search(r'group-title="([^"]*)"', line)
            id_m    = re.search(r'tvg-id="([^"]*)"', line)
            raw_name = name_m.group(1).strip() if name_m else ''
            # Ignorer les lignes avec nom cassé (User-Agent mélangé)
            if 'Safari/' in raw_name or 'Chrome/' in raw_name or len(raw_name) > 80:
                current = None
                continue
            current = {
                'name':   raw_name or 'Sans nom',
                'logo':   logo_m.group(1) if logo_m else '',
                'group':  group_m.group(1) if group_m else '',
                'tvg_id': id_m.group(1) if id_m else '',
                'url':    ''
            }
        elif line and not line.startswith('#') and current:
            current['url'] = line
            channels.append(current)
            current = None
    return channels

# ─── CHARGER IPTV-ORG FR ─────────────────────────────────────────────────────
def load_iptv_org_fr():
    """Charge iptv-org/fr.m3u et retourne un dict nom_exact -> url"""
    print("📡 Chargement iptv-org/fr.m3u…")
    try:
        r = requests.get(IPTV_ORG_FR_URL, timeout=20)
        r.raise_for_status()
        channels = parse_m3u(r.text)
        db = {}
        for ch in channels:
            # Index par nom exact (lowercase) uniquement
            key = ch['name'].lower().strip()
            if not is_blacklisted(ch['url']):
                db[key] = ch['url']
        print(f"✅ {len(db)} chaînes FR chargées depuis iptv-org\n")
        return db
    except Exception as e:
        print(f"⚠️ Impossible de charger iptv-org : {e}\n")
        return {}

def is_blacklisted(url):
    """Vérifie si une URL provient d'un hôte blacklisté pour les remplacements auto"""
    for host in BLACKLIST_REPLACEMENT_HOSTS:
        if host in url:
            return True
    return False

# ─── CHECK URL ────────────────────────────────────────────────────────────────
def check_url(url, timeout=None):
    if timeout is None:
        timeout = TIMEOUT_ARCHIVE if 'archive.org' in url else TIMEOUT
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': '*/*',
        'Origin': 'https://exoticsecurityweb.github.io',
        'Referer': 'https://exoticsecurityweb.github.io/',
    }
    try:
        r = requests.head(url, timeout=timeout, headers=headers, allow_redirects=True)
        if r.status_code < 400:
            return True, r.status_code, None
        r = requests.get(url, timeout=timeout, headers=headers, allow_redirects=True, stream=True)
        r.close()
        if r.status_code < 400:
            return True, r.status_code, None
        return False, r.status_code, f"HTTP {r.status_code}"
    except requests.exceptions.Timeout:
        return False, 0, "Timeout"
    except requests.exceptions.ConnectionError:
        return False, 0, "DNS mort / Connexion impossible"
    except Exception as e:
        return False, 0, str(e)[:80]

# ─── TROUVER UN REMPLACEMENT ──────────────────────────────────────────────────
def find_replacement(channel, iptv_org_db):
    dead_url = channel['url']
    name = channel['name']

    # 1. REPLACEMENT_DB manuelle par nom exact
    candidates = REPLACEMENT_DB.get(name, [])
    for url in candidates:
        if url.strip() == dead_url.strip():
            continue
        ok, _, _ = check_url(url)
        if ok:
            return url, "DB manuelle"
        time.sleep(0.3)

    # 2. iptv-org par nom EXACT uniquement (pas de matching approximatif)
    # Enlève juste les suffixes de qualité pour la comparaison
    clean_name = re.sub(r'\s*[\(\[].*?[\)\]]\s*', '', name).strip().lower()
    
    for org_key, org_url in iptv_org_db.items():
        org_clean = re.sub(r'\s*[\(\[].*?[\)\]]\s*', '', org_key).strip()
        if clean_name == org_clean and not is_blacklisted(org_url):
            if org_url.strip() == dead_url.strip():
                continue
            ok, _, _ = check_url(org_url)
            if ok:
                return org_url, "iptv-org"
            time.sleep(0.3)
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

def build_embed(channel, error, replacement=None, source=None):
    fields = [
        {"name": "📂 Groupe", "value": channel['group'] or "—", "inline": True},
        {"name": "🔴 Erreur", "value": error, "inline": True},
        {"name": "🔗 URL morte", "value": f"```{channel['url'][:120]}```", "inline": False},
    ]
    if replacement:
        fields += [
            {"name": f"✅ Nouvelle URL ({source})",
             "value": f"```{replacement}```", "inline": False},
            {"name": "💡 Comment l'appliquer",
             "value": "[Ouvre l'éditeur M3U](https://exoticsecurityweb.github.io/iptv-exotic/) → clique la chaîne → remplace l'URL → Exporter M3U",
             "inline": False},
        ]
        color = 0x4ade80
        icon  = "🔄"
    else:
        fields.append({
            "name": "😓 Aucun remplacement trouvé",
            "value": "Ni dans la DB manuelle, ni sur iptv-org/fr.",
            "inline": False
        })
        color = 0xf87171
        icon  = "💀"

    return {
        "title": f"{icon} {channel['name']}",
        "color": color,
        "fields": fields,
        "footer": {"text": "Exotic TV Stream Checker • Pink Paradise 🌴"},
        "timestamp": datetime.utcnow().isoformat()
    }

# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    now = datetime.utcnow().strftime('%d/%m/%Y à %H:%M UTC')
    print(f"\n🌴 Exotic TV Stream Checker v3 — {now}")
    print("─" * 60)

    # Charger le M3U
    print(f"📥 Chargement de la playlist…")
    try:
        r = requests.get(M3U_URL, timeout=15)
        r.raise_for_status()
        channels = parse_m3u(r.text)
        print(f"✅ {len(channels)} chaînes trouvées\n")
    except Exception as e:
        print(f"❌ {e}")
        send_discord([{"title": "❌ Playlist inaccessible", "description": str(e),
                       "color": 0xf87171, "footer": {"text": "Exotic TV • Pink Paradise 🌴"}}])
        return

    # Charger iptv-org/fr
    iptv_org_db = load_iptv_org_fr()

    # Tester chaque chaîne
    alive = []
    dead  = []

    for i, ch in enumerate(channels):
        print(f"[{i+1:3}/{len(channels)}] {ch['name']:<42} ", end='', flush=True)
        ok, code, err = check_url(ch['url'])

        if ok:
            print(f"✅  {code}")
            alive.append(ch)
        else:
            print(f"❌  {err}")
            replacement, source = find_replacement(ch, iptv_org_db)
            if replacement:
                print(f"         ↳ 🔄 [{source}] {replacement[:70]}")
            else:
                print(f"         ↳ 😓 Aucun remplacement")
            dead.append({**ch, 'error': err, 'replacement': replacement, 'source': source})

        time.sleep(SLEEP_BTW)

    # Résumé
    print(f"\n{'─'*60}")
    with_repl = [d for d in dead if d['replacement']]
    print(f"✅ Vivantes           : {len(alive)}")
    print(f"❌ Mortes             : {len(dead)}")
    print(f"🔄 Avec remplacement  : {len(with_repl)}")
    print(f"😓 Sans solution      : {len(dead) - len(with_repl)}")

    # Discord
    if not dead:
        send_discord([{
            "title": "✅ Exotic TV — Tout fonctionne !",
            "description": f"**{len(alive)}/{len(channels)}** chaînes OK\n{now}",
            "color": 0x4ade80,
            "footer": {"text": "Exotic TV Stream Checker • Pink Paradise 🌴"},
            "timestamp": datetime.utcnow().isoformat()
        }])
        return

    # Résumé global Discord
    send_discord([{
        "title": "📺 Exotic TV — Rapport de veille",
        "description": (
            f"🕐 {now}\n\n"
            f"✅ OK : **{len(alive)}** | ❌ Mortes : **{len(dead)}**\n"
            f"🔄 Avec remplacement : **{len(with_repl)}** | 😓 Sans solution : **{len(dead)-len(with_repl)}**"
        ),
        "color": 0xf472b6,
        "footer": {"text": "Exotic TV Stream Checker • Pink Paradise 🌴"},
        "timestamp": datetime.utcnow().isoformat()
    }])

    # Détail par chaîne morte (4 par message)
    for i in range(0, len(dead), 4):
        batch = dead[i:i+4]
        send_discord([build_embed(ch, ch['error'], ch['replacement'], ch['source']) for ch in batch])

    print(f"\nDiscord notifié ✅")

if __name__ == '__main__':
    main()
