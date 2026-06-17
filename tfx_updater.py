#!/usr/bin/env python3
"""
Exotic TFX Auto-Updater v2
- Scanne dynamiquement le repo ParaTV pour trouver le fichier TFX
- Même si le chemin change, il retrouve toujours la bonne URL
- Met à jour exotic-tv-playlist.m3u automatiquement
- Notifie Discord quand l'URL change
"""

import os
import re
import json
import base64
import requests
from datetime import datetime

# ─── CONFIG ──────────────────────────────────────────────────────────────────
PARATV_REPO        = "Paradise-91/ParaTV"
PARATV_STREAMS_DIR = "streams"
PLAYLIST_FILE      = "exotic-tv-playlist.m3u"
CACHE_FILE         = "tfx_cache.json"
REPO               = "ExoticSecurityWeb/iptv-exotic"
GITHUB_TOKEN       = os.environ.get("GITHUB_TOKEN", "")
DISCORD_WEBHOOK_TFX = os.environ.get("DISCORD_WEBHOOK_TFX", "")

# Noms TFX dans ta playlist
TFX_NAMES = ["TFX", "TFX HD", "TFX (1080p) [Geo-Blocked]", "TFX (1080p)"]

# ─── SCAN PARATV DYNAMIQUEMENT ───────────────────────────────────────────────
def find_tfx_url():
    """Scanne le repo ParaTV pour trouver le fichier TFX peu importe le chemin."""
    print("🔍 Scan du repo ParaTV…")
    headers = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"

    # Lister les sous-dossiers de streams/
    r = requests.get(
        f"https://api.github.com/repos/{PARATV_REPO}/contents/{PARATV_STREAMS_DIR}",
        headers=headers, timeout=15
    )
    if r.status_code == 404:
        raise Exception("Dossier streams/ introuvable dans ParaTV")
    r.raise_for_status()
    
    folders = [item for item in r.json() if item['type'] == 'dir']
    print(f"📁 {len(folders)} dossier(s) trouvé(s) dans streams/")

    # Chercher dans chaque sous-dossier un fichier .m3u8
    for folder in folders:
        r2 = requests.get(folder['url'], headers=headers, timeout=15)
        r2.raise_for_status()
        files = [f for f in r2.json() if f['name'].endswith('.m3u8')]
        
        for f in files:
            # Fetch le contenu du fichier
            raw_url = f['download_url']
            try:
                r3 = requests.get(raw_url, timeout=15, headers={'User-Agent': 'Mozilla/5.0'})
                if r3.status_code != 200:
                    continue
                # Extraire la première URL de stream
                for line in r3.text.strip().split('\n'):
                    line = line.strip()
                    if line and not line.startswith('#'):
                        # Vérifier si c'est TFX (cherche TFX dans l'URL ou les tags)
                        content_upper = r3.text.upper()
                        if 'TFX' in content_upper or 'NT1' in content_upper:
                            print(f"✅ Fichier TFX trouvé : {folder['name']}/{f['name']}")
                            return line
            except:
                continue

    raise Exception("Aucun fichier TFX trouvé dans ParaTV")

# ─── GITHUB API ──────────────────────────────────────────────────────────────
def github_get(path):
    r = requests.get(
        f"https://api.github.com/repos/{REPO}/contents/{path}",
        headers={"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"},
        timeout=15
    )
    r.raise_for_status()
    return r.json()

def github_put(path, content, sha, message):
    payload = {
        "message": message,
        "content": base64.b64encode(content.encode('utf-8')).decode('utf-8'),
    }
    if sha:
        payload["sha"] = sha
    r = requests.put(
        f"https://api.github.com/repos/{REPO}/contents/{path}",
        headers={"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"},
        json=payload, timeout=15
    )
    r.raise_for_status()
    return r.json()

# ─── LOAD/SAVE CACHE ─────────────────────────────────────────────────────────
def load_cache():
    try:
        data = github_get(CACHE_FILE)
        content = base64.b64decode(data['content']).decode('utf-8')
        return json.loads(content), data['sha']
    except:
        return {"last_url": "", "last_updated": ""}, None

def save_cache(cache, sha):
    content = json.dumps(cache, indent=2)
    now = datetime.utcnow().strftime('%d/%m/%Y %H:%M UTC')
    github_put(CACHE_FILE, content, sha, f"🔄 TFX cache — {now}")

# ─── UPDATE PLAYLIST ─────────────────────────────────────────────────────────
def update_playlist(new_url):
    print(f"📝 Mise à jour de {PLAYLIST_FILE}…")
    data = github_get(PLAYLIST_FILE)
    content = base64.b64decode(data['content']).decode('utf-8')
    sha = data['sha']

    lines = content.split('\n')
    new_lines = []
    i = 0
    updated = False

    while i < len(lines):
        line = lines[i]
        if line.startswith('#EXTINF'):
            name_match = re.search(r',(.+)$', line)
            if name_match and name_match.group(1).strip() in TFX_NAMES:
                new_lines.append(line)
                i += 1
                if i < len(lines):
                    new_lines.append(new_url)
                    i += 1
                    updated = True
                    print(f"✅ URL TFX remplacée !")
                    continue
        new_lines.append(line)
        i += 1

    if not updated:
        print(f"⚠️ Chaîne TFX pas trouvée dans la playlist")
        return False

    now = datetime.utcnow().strftime('%d/%m/%Y %H:%M UTC')
    github_put(PLAYLIST_FILE, '\n'.join(new_lines), sha, f"📺 TFX auto-updated — {now}")
    print(f"✅ Playlist mise à jour !")
    return True

# ─── DISCORD ─────────────────────────────────────────────────────────────────
def notify_discord(old_url, new_url):
    if not DISCORD_WEBHOOK_TFX:
        return
    now = datetime.utcnow().strftime('%d/%m/%Y à %H:%M UTC')
    payload = {"embeds": [{
        "title": "📺 TFX — URL mise à jour automatiquement",
        "color": 0xf472b6,
        "fields": [
            {"name": "✅ Nouvelle URL", "value": f"```{new_url}```", "inline": False},
            {"name": "💀 Ancienne URL (expirée)", "value": f"```{old_url[:120] if old_url else 'N/A'}```", "inline": False},
        ],
        "footer": {"text": f"Exotic TFX Auto-Updater • Pink Paradise 🌴 • {now}"}
    }]}
    try:
        r = requests.post(DISCORD_WEBHOOK_TFX, json=payload, timeout=10)
        r.raise_for_status()
        print("✅ Discord notifié !")
    except Exception as e:
        print(f"❌ Erreur Discord : {e}")

# ─── MAIN ────────────────────────────────────────────────────────────────────
def main():
    now = datetime.utcnow().strftime('%d/%m/%Y à %H:%M UTC')
    print(f"\n📺 Exotic TFX Auto-Updater v2 — {now}")
    print("─" * 50)

    # 1. Scanner ParaTV dynamiquement
    try:
        new_url = find_tfx_url()
        print(f"🎯 URL TFX : {new_url[:80]}…")
    except Exception as e:
        print(f"❌ Erreur scan ParaTV : {e}")
        return

    # 2. Charger le cache
    cache, cache_sha = load_cache()
    last_url = cache.get("last_url", "")

    # 3. Comparer
    if new_url == last_url:
        print(f"📌 URL identique — pas de mise à jour")
        return

    # 4. URL changée → mettre à jour
    print(f"🔄 URL changée ! Mise à jour…")
    if update_playlist(new_url):
        cache["last_url"] = new_url
        cache["last_updated"] = now
        save_cache(cache, cache_sha)
        notify_discord(last_url, new_url)

if __name__ == '__main__':
    main()
