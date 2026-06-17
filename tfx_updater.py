#!/usr/bin/env python3
"""
Exotic TFX Auto-Updater
- Fetch le fichier M3U source de ParaTV toutes les 5 min
- Extrait l'URL TFX
- Si elle a changé depuis la dernière fois → met à jour exotic-tv-playlist.m3u
- Si identique → attend le prochain run (toutes les 5 min)
- Toutes les 2h si pas de changement → force quand même une vérif
"""

import os
import re
import json
import base64
import requests
from datetime import datetime

# ─── CONFIG ──────────────────────────────────────────────────────────────────
SOURCE_URL    = "https://raw.githubusercontent.com/Paradise-91/ParaTV/main/streams/E3j2IrI26T1/XYWDSJ3rF32wWzP.m3u8"
PLAYLIST_FILE = "exotic-tv-playlist.m3u"
CACHE_FILE    = "tfx_cache.json"
REPO          = "ExoticSecurityWeb/iptv-exotic"
GITHUB_TOKEN  = os.environ.get("GITHUB_TOKEN", "")

# Nom exact de la chaîne TFX dans ta playlist
TFX_NAMES = ["TFX", "TFX HD", "TFX (1080p) [Geo-Blocked]", "TFX (1080p)"]

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
    r = requests.put(
        f"https://api.github.com/repos/{REPO}/contents/{path}",
        headers={"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"},
        json={
            "message": message,
            "content": base64.b64encode(content.encode('utf-8')).decode('utf-8'),
            "sha": sha
        },
        timeout=15
    )
    r.raise_for_status()
    return r.json()

# ─── FETCH URL TFX ───────────────────────────────────────────────────────────
def fetch_tfx_url():
    """Fetch le M3U source et extrait la première URL de stream."""
    r = requests.get(SOURCE_URL, timeout=15)
    r.raise_for_status()
    lines = r.text.strip().split('\n')
    for line in lines:
        line = line.strip()
        if line and not line.startswith('#'):
            return line
    return None

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
    msg = f"🔄 TFX cache update — {datetime.utcnow().strftime('%d/%m/%Y %H:%M UTC')}"
    try:
        if sha:
            github_put(CACHE_FILE, content, sha, msg)
        else:
            # Fichier n'existe pas encore
            requests.put(
                f"https://api.github.com/repos/{REPO}/contents/{CACHE_FILE}",
                headers={"Authorization": f"token {GITHUB_TOKEN}"},
                json={"message": msg, "content": base64.b64encode(content.encode()).decode()},
                timeout=15
            )
    except Exception as e:
        print(f"⚠️ Cache save error: {e}")

# ─── UPDATE PLAYLIST ─────────────────────────────────────────────────────────
def update_playlist(new_url):
    """Remplace l'URL TFX dans la playlist et push sur GitHub."""
    print(f"📝 Mise à jour de {PLAYLIST_FILE}…")

    # Charger la playlist
    data = github_get(PLAYLIST_FILE)
    content = base64.b64decode(data['content']).decode('utf-8')
    sha = data['sha']

    lines = content.split('\n')
    new_lines = []
    i = 0
    updated = False

    while i < len(lines):
        line = lines[i]
        # Cherche une ligne #EXTINF qui correspond à TFX
        if line.startswith('#EXTINF'):
            name_match = re.search(r',(.+)$', line)
            if name_match:
                name = name_match.group(1).strip()
                if name in TFX_NAMES:
                    new_lines.append(line)
                    i += 1
                    # La ligne suivante est l'URL — on la remplace
                    if i < len(lines):
                        new_lines.append(new_url)
                        i += 1
                        updated = True
                        print(f"✅ URL TFX remplacée pour '{name}'")
                        continue
        new_lines.append(line)
        i += 1

    if not updated:
        print(f"⚠️ Chaîne TFX pas trouvée dans la playlist (noms cherchés: {TFX_NAMES})")
        return False

    new_content = '\n'.join(new_lines)
    now = datetime.utcnow().strftime('%d/%m/%Y %H:%M UTC')
    github_put(PLAYLIST_FILE, new_content, sha, f"📺 TFX URL auto-updated — {now}")
    print(f"✅ Playlist mise à jour sur GitHub !")
    return True

# ─── MAIN ────────────────────────────────────────────────────────────────────
def main():
    now = datetime.utcnow().strftime('%d/%m/%Y à %H:%M UTC')
    print(f"\n📺 Exotic TFX Auto-Updater — {now}")
    print("─" * 50)

    # 1. Fetch la nouvelle URL TFX
    print(f"🔍 Fetch de l'URL TFX depuis ParaTV…")
    try:
        new_url = fetch_tfx_url()
        if not new_url:
            print("❌ Aucune URL trouvée dans le fichier source")
            return
        print(f"✅ URL trouvée : {new_url[:80]}…")
    except Exception as e:
        print(f"❌ Erreur fetch : {e}")
        return

    # 2. Charger le cache
    cache, cache_sha = load_cache()
    last_url = cache.get("last_url", "")

    # 3. Comparer
    if new_url == last_url:
        print(f"📌 URL identique — pas de mise à jour nécessaire")
        return

    # 4. URL différente → mettre à jour la playlist
    print(f"🔄 URL changée ! Mise à jour de la playlist…")
    print(f"   Ancienne : {last_url[:60]}…" if last_url else "   (première fois)")
    print(f"   Nouvelle : {new_url[:60]}…")

    if update_playlist(new_url):
        # 5. Sauvegarder le cache
        cache["last_url"] = new_url
        cache["last_updated"] = now
        save_cache(cache, cache_sha)
        print(f"✅ Cache mis à jour")
    else:
        print(f"❌ Mise à jour playlist échouée")

if __name__ == '__main__':
    main()
