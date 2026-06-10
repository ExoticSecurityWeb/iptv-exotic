#!/usr/bin/env python3
"""
Exotic TV — Stream Checker
Vérifie chaque chaîne du palmi.m3u toutes les 30 minutes.
Si une URL est morte, cherche automatiquement une URL de remplacement
dans la base REPLACEMENT_DB et notifie le salon Discord du clan.
"""

import os
import re
import time
import requests
from datetime import datetime

# ─── CONFIG ──────────────────────────────────────────────────────────────────
M3U_URL    = "https://exoticsecurityweb.github.io/iptv-exotic/palmi.m3u"
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "")
TIMEOUT    = 10   # secondes max par test d'URL
SLEEP_BTW  = 0.3  # pause entre chaque chaîne pour pas flood les serveurs

# ─── BASE DE REMPLACEMENT ────────────────────────────────────────────────────
# Clé   = tvg-id exact dans le M3U  (ex: "M6.fr")
# Valeur = liste d'URLs alternatives testées dans l'ordre, la 1ère qui répond est proposée
REPLACEMENT_DB = {
    # ── TNT ──────────────────────────────────────────────────────────────────
    "TF1.fr": [
        "https://viamotionhsi.netplus.ch/live/eds/tf1hd/browser-HLS8/tf1hd.m3u8",
        "https://raw.githubusercontent.com/Paradise-91/ParaTV/main/streams/tf1/tf1-hd.m3u8",
    ],
    "France2.fr": [
        "https://raw.githubusercontent.com/schumijo/iptv/main/playlists/francetv/france2.m3u8",
        "https://simulcast-p.ftven.fr/simulcast/France_2/hls_fr2/France_2.m3u8",
    ],
    "France3.fr": [
        "https://raw.githubusercontent.com/schumijo/iptv/main/playlists/francetv/france3.m3u8",
    ],
    "France4.fr": [
        "https://raw.githubusercontent.com/schumijo/iptv/main/playlists/francetv/france4.m3u8",
    ],
    "France5.fr": [
        "https://raw.githubusercontent.com/schumijo/iptv/main/playlists/francetv/france5.m3u8",
    ],
    "M6.fr": [
        "https://lbcdn.6cloud.fr/resource/m6web/l/m6_hls_sd_short_q2hyb21h.m3u8?groups[]=m6web-live-m6_ext",
        "https://shls-m6-france-prod-dub.shahid.net/out/v1/c8a9f6e000cd4ebaa4d2fc7d18c15988/index.m3u8",
        "https://viamotionhsi.netplus.ch/live/eds/m6hd/browser-HLS8/m6hd.m3u8",
    ],
    "Arte.fr": [
        "https://raw.githubusercontent.com/schumijo/iptv/main/playlists/francetv/arte.m3u8",
    ],
    "LaChaineParlementaire.fr": [
        "https://raw.githubusercontent.com/schumijo/iptv/main/playlists/francetv/lcpps.m3u8",
        "https://raw.githubusercontent.com/ipstreet312/freeiptv/master/ressources/dmotion/py/lcpan/lcp1.m3u8",
    ],
    "W9.fr": [
        "https://lbcdn.6cloud.fr/resource/m6web/l/w9_hls_sd_short_q2hyb21h.m3u8?groups[]=m6web-live-w9_ext",
        "https://viamotionhsi.netplus.ch/live/eds/w9/browser-HLS8/w9.m3u8",
    ],
    "TMC.fr": [
        "https://viamotionhsi.netplus.ch/live/eds/tmc/browser-HLS8/tmc.m3u8",
    ],
    "NT1.fr": [
        "https://viamotionhsi.netplus.ch/live/eds/nt1/browser-HLS8/nt1.m3u8",
    ],
    "Gulli.fr": [
        "https://lbcdn.6cloud.fr/resource/m6web/l/gulli_hls_sd_short_q2hyb21h.m3u8?groups[]=m6web-live-gulli_ext",
    ],
    "BFMTV.fr": [
        "https://ncdn-live-bfm.pfd.sfr.net/shls/LIVE$BFM_TV/index.m3u8?start=LIVE&end=END",
    ],
    "CNews.fr": [
        "https://raw.githubusercontent.com/schumijo/iptv/main/playlists/canalplus/cnews.m3u8",
    ],
    "LCI.fr": [
        "https://raw.githubusercontent.com/pinkisso/mored/refs/heads/main/res/26-1/lci1.m3u8",
    ],
    "FranceInfo.fr": [
        "https://raw.githubusercontent.com/schumijo/iptv/main/playlists/francetv/franceinfo.m3u8",
    ],
    "CStar.fr": [
        "https://raw.githubusercontent.com/schumijo/iptv/main/playlists/canalplus/cstar.m3u8",
        "https://viamotionhsi.netplus.ch/live/eds/d17/browser-HLS8/d17.m3u8",
    ],
    "T18.fr": [
        "https://raw.githubusercontent.com/schumijo/iptv/main/playlists/dailymotion/t18.m3u8",
    ],
    "NOVO19.fr": [
        "https://viamotionhsi.netplus.ch/live/eds/novo19/browser-HLS8/novo19.m3u8",
    ],
    "TF1SeriesFilms.fr": [
        "https://viamotionhsi.netplus.ch/live/eds/hd1/browser-HLS8/hd1.m3u8",
    ],
    "6ter.fr": [
        "https://lbcdn.6cloud.fr/resource/m6web/l/6ter_hls_sd_short_q2hyb21h.m3u8?groups[]=m6web-live-6ter_ext",
        "https://viamotionhsi.netplus.ch/live/eds/6ter/browser-HLS8/6ter.m3u8",
    ],
    "Numero23.fr": [
        "https://d15aro46bnpfm8.cloudfront.net/v1/master/3722c60a815c199d9c0ef36c5b73da68a62b09d1/cc-fqkqiax1078up/RMC_Story_FR.m3u8",
    ],
    "RMCDecouverte.fr": [
        "https://d16zzycxcd0m0r.cloudfront.net/v1/master/3722c60a815c199d9c0ef36c5b73da68a62b09d1/cc-hixvx5kymecr9/RMC_Decouverte_FR.m3u8",
    ],
    "Cherie25.fr": [
        "https://d3dcdjv6dx07iz.cloudfront.net/v1/master/3722c60a815c199d9c0ef36c5b73da68a62b09d1/cc-eaaww2dyp3iih/RMC_Life_FR.m3u8",
    ],
    "CanalPlus.fr": [
        "https://raw.githubusercontent.com/Paradise-91/ParaTV/main/streams/canalplus/canalplusclair-hd.m3u8",
    ],
    # ── INFO ──────────────────────────────────────────────────────────────────
    "BFM2.fr": [
        "https://ncdn-live-bfm.pfd.sfr.net/shls/LIVE$BFM2/index.m3u8?start=LIVE&end=END",
    ],
    "BFMBusiness.fr": [
        "https://ncdn-live-bfm.pfd.sfr.net/shls/LIVE$BFM_BUSINESS/index.m3u8?start=LIVE&end=END",
    ],
    "BFMGrandsReportages.fr": [
        "https://ncdn-live-bfm.pfd.sfr.net/shls/LIVE$BFM_GRANDSREPORTAGES/index.m3u8?start=LIVE&end=END",
    ],
    "BSmartTV.fr": [
        "https://raw.githubusercontent.com/Sibprod/streams/main/ressources/dm/py/hls/bsmart.m3u8",
    ],
    "France24.fr": [
        "https://raw.githubusercontent.com/schumijo/iptv/main/playlists/francetv/france24.m3u8",
    ],
    "LCP100.fr": [
        "https://raw.githubusercontent.com/ipstreet312/freeiptv/master/ressources/dmotion/py/lcpan/lcp1.m3u8",
    ],
    "PublicSenat2424.fr": [
        "https://raw.githubusercontent.com/Paradise-91/ParaTV/main/streams/publicsenat/publicsenat-dm.m3u8",
    ],
    "RMCTalkInfo.fr": [
        "https://d75bm5ggq4k1o.cloudfront.net/v1/master/3722c60a815c199d9c0ef36c5b73da68a62b09d1/cc-gui89fv6hprsj/RMC_TALK_INFO_FR.m3u8",
    ],
    # ── SPORT ─────────────────────────────────────────────────────────────────
    "InfosportPlus.fr": [
        "http://212.102.60.80/Infosport/index.m3u8",
    ],
    "Equidia.fr": [
        "https://raw.githubusercontent.com/schumijo/iptv/main/playlists/equidia/equidia-live2.m3u8",
    ],
    "TRACESportStars.fr": [
        "https://lightning-tracesport-samsungau.amagi.tv/playlist.m3u8",
    ],
    "SportEnFrance.fr": [
        "https://raw.githubusercontent.com/schumijo/iptv/main/playlists/dailymotion/sportenfrance.m3u8",
    ],
    # ── SERIES & DIVERTISSEMENT ───────────────────────────────────────────────
    "TV5Monde.fr": [
        "https://ott.tv5monde.com/Content/HLS/Live/channel(fbs)/index.m3u8",
    ],
    "FranceTVSeries.fr": [
        "https://raw.githubusercontent.com/schumijo/iptv/main/playlists/francetv/series.m3u8",
    ],
    "TIJI.fr": [
        "https://shls-tiji-tv-prod-dub.shahid.net/out/v1/98f46736bd8c4404b67e4b7a38cc8976/index.m3u8",
    ],
    # ── INFO SUPPLEMENTAIRES ──────────────────────────────────────────────────
    "BFMTalkInfo.fr": [
        "https://ncdn-live-bfm.pfd.sfr.net/shls/LIVE$BFM_TV/index.m3u8?start=LIVE&end=END",
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
            name_m  = re.search(r',(.+)$', line)
            logo_m  = re.search(r'tvg-logo="([^"]*)"', line)
            group_m = re.search(r'group-title="([^"]*)"', line)
            id_m    = re.search(r'tvg-id="([^"]*)"', line)
            current = {
                'name':    name_m.group(1).strip() if name_m else 'Sans nom',
                'logo':    logo_m.group(1) if logo_m else '',
                'group':   group_m.group(1) if group_m else '',
                'tvg_id':  id_m.group(1) if id_m else '',
                'extinf':  line,
                'url':     ''
            }
        elif line and not line.startswith('#') and current:
            current['url'] = line
            channels.append(current)
            current = None
    return channels

# ─── VÉRIFICATION D'UNE URL ──────────────────────────────────────────────────
def check_url(url):
    """
    Teste si une URL répond correctement.
    Retourne (ok: bool, code: int, erreur: str|None)
    Essaie d'abord HEAD (rapide), puis GET si HEAD échoue.
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': '*/*',
        'Origin': 'https://exoticsecurityweb.github.io',
        'Referer': 'https://exoticsecurityweb.github.io/',
    }
    try:
        r = requests.head(url, timeout=TIMEOUT, headers=headers, allow_redirects=True)
        if r.status_code < 400:
            return True, r.status_code, None
        # HEAD a échoué → on tente GET (certains serveurs n'acceptent pas HEAD)
        r = requests.get(url, timeout=TIMEOUT, headers=headers,
                         allow_redirects=True, stream=True)
        r.close()
        if r.status_code < 400:
            return True, r.status_code, None
        return False, r.status_code, f"HTTP {r.status_code}"
    except requests.exceptions.Timeout:
        return False, 0, "Timeout (serveur trop lent)"
    except requests.exceptions.ConnectionError:
        return False, 0, "Connexion impossible (DNS mort ou IP bloquée)"
    except Exception as e:
        return False, 0, str(e)[:100]

# ─── RECHERCHE DE REMPLACEMENT ────────────────────────────────────────────────
def find_replacement(channel):
    """
    Cherche dans REPLACEMENT_DB une URL alternative qui fonctionne.
    Retourne l'URL si trouvée, None sinon.
    """
    tvg_id = channel.get('tvg_id', '')
    candidates = REPLACEMENT_DB.get(tvg_id, [])
    dead_url = channel['url']
    for candidate in candidates:
        if candidate.strip() == dead_url.strip():
            continue  # pas la peine de retester la même URL
        ok, _, _ = check_url(candidate)
        if ok:
            return candidate
        time.sleep(0.5)
    return None

# ─── ENVOI DISCORD ────────────────────────────────────────────────────────────
def send_discord(embeds, content=None):
    """Envoie jusqu'à 10 embeds dans un message Discord via webhook."""
    if not DISCORD_WEBHOOK:
        print("⚠️  DISCORD_WEBHOOK non configuré — pas de notif envoyée")
        return
    payload = {"embeds": embeds[:10]}
    if content:
        payload["content"] = content
    try:
        r = requests.post(DISCORD_WEBHOOK, json=payload, timeout=10)
        r.raise_for_status()
        time.sleep(1.2)  # Respect du rate-limit Discord (5 msg/5s)
    except Exception as e:
        print(f"❌ Erreur envoi Discord : {e}")

def build_dead_embed(channel, error, replacement=None):
    """Construit l'embed Discord pour une chaîne morte."""
    fields = [
        {"name": "📂 Groupe",    "value": channel['group'] or "—", "inline": True},
        {"name": "🔴 Erreur",    "value": error or "Inconnue",     "inline": True},
        {"name": "🔗 URL morte", "value": f"```{channel['url'][:120]}```", "inline": False},
    ]
    if replacement:
        fields += [
            {"name": "✅ Nouvelle URL trouvée",
             "value": f"```{replacement}```", "inline": False},
            {"name": "💡 Comment l'appliquer",
             "value": (
                 "1. Ouvre **[l'éditeur M3U](https://exoticsecurityweb.github.io/iptv-exotic/)**\n"
                 "2. Clique sur la chaîne dans la liste\n"
                 "3. Remplace l'URL du stream\n"
                 "4. Clique **Exporter M3U**"
             ), "inline": False},
        ]
        color = 0x4ade80   # vert = remplacement dispo
        icon  = "🔄"
    else:
        fields.append({
            "name": "😓 Aucun remplacement trouvé",
            "value": "Toutes les URLs alternatives sont aussi mortes. À corriger manuellement.",
            "inline": False
        })
        color = 0xf87171   # rouge = pas de solution
        icon  = "💀"

    return {
        "title":  f"{icon} {channel['name']}",
        "color":  color,
        "fields": fields,
        "footer": {"text": "Exotic TV Stream Checker • Pink Paradise 🌴"},
        "timestamp": datetime.utcnow().isoformat()
    }

# ─── PROGRAMME PRINCIPAL ──────────────────────────────────────────────────────
def main():
    now = datetime.utcnow().strftime('%d/%m/%Y à %H:%M UTC')
    print(f"\n🌴 Exotic TV Stream Checker — {now}")
    print("─" * 60)

    # 1. Charger le M3U depuis GitHub Pages
    print(f"📥 Chargement de palmi.m3u…")
    try:
        r = requests.get(M3U_URL, timeout=15)
        r.raise_for_status()
        channels = parse_m3u(r.text)
        print(f"✅ {len(channels)} chaînes trouvées\n")
    except Exception as e:
        msg = f"Impossible de charger palmi.m3u : {e}"
        print(f"❌ {msg}")
        send_discord([{
            "title": "❌ palmi.m3u inaccessible",
            "description": msg,
            "color": 0xf87171,
            "footer": {"text": "Exotic TV Stream Checker • Pink Paradise 🌴"}
        }])
        return

    # 2. Tester chaque chaîne
    alive = []
    dead  = []

    for i, ch in enumerate(channels):
        label = f"[{i+1:3}/{len(channels)}] {ch['name']:<38}"
        print(label, end='', flush=True)

        ok, code, err = check_url(ch['url'])

        if ok:
            print(f"✅  {code}")
            alive.append(ch)
        else:
            print(f"❌  {err}")
            replacement = find_replacement(ch)
            if replacement:
                print(f"         ↳ 🔄 Remplacement trouvé : {replacement[:80]}")
            else:
                print(f"         ↳ 😓 Aucun remplacement disponible")
            dead.append({**ch, 'error': err, 'replacement': replacement})

        time.sleep(SLEEP_BTW)

    # 3. Résumé console
    print(f"\n{'─'*60}")
    print(f"✅ Vivantes        : {len(alive)}")
    print(f"❌ Mortes          : {len(dead)}")
    with_repl = [d for d in dead if d['replacement']]
    print(f"🔄 Avec remplacement : {len(with_repl)}")
    print(f"😓 Sans solution   : {len(dead) - len(with_repl)}")

    # 4. Notifier Discord
    if not dead:
        send_discord([{
            "title": "✅ Exotic TV — Tout fonctionne !",
            "description": (
                f"**{len(alive)}/{len(channels)}** chaînes opérationnelles\n"
                f"Vérification du {now}"
            ),
            "color": 0x4ade80,
            "footer": {"text": "Exotic TV Stream Checker • Pink Paradise 🌴"},
            "timestamp": datetime.utcnow().isoformat()
        }])
        print("\nDiscord notifié : tout OK ✅")
        return

    # Résumé global
    send_discord([{
        "title": "📺 Exotic TV — Rapport de veille",
        "description": (
            f"🕐 {now}\n\n"
            f"✅ Chaînes OK : **{len(alive)}**\n"
            f"❌ Chaînes mortes : **{len(dead)}**\n"
            f"🔄 Avec remplacement dispo : **{len(with_repl)}**\n"
            f"😓 Sans solution : **{len(dead) - len(with_repl)}**"
        ),
        "color": 0xf472b6,
        "footer": {"text": "Exotic TV Stream Checker • Pink Paradise 🌴"},
        "timestamp": datetime.utcnow().isoformat()
    }])

    # Détail chaîne par chaîne (max 4 embeds par message pour lisibilité)
    for i in range(0, len(dead), 4):
        batch = dead[i:i+4]
        embeds = [build_dead_embed(ch, ch['error'], ch['replacement']) for ch in batch]
        send_discord(embeds)

    print(f"\nDiscord notifié — {len(dead)} chaîne(s) morte(s) signalée(s) ✅")

if __name__ == '__main__':
    main()
