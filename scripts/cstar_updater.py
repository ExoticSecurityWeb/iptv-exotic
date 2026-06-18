#!/usr/bin/env python3
"""
Exotic CStar Auto-Updater v1.0
- Fetch l'URL HLS CStar depuis l'API Dailymotion (video x5gv5v0)
- Met à jour exotic-tv-playlist.m3u automatiquement si l'URL change
- Notifie Discord
"""

import os
import re
import sys
import json
import time
import base64
import logging
import requests
from datetime import datetime, timezone

# ─── LOGGING ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("cstar-updater")

# ─── CONFIG ──────────────────────────────────────────────────────────────────
DM_VIDEO_ID         = "x5gv5v0"   # ID Dailymotion de CStar
PLAYLIST_FILE       = "exotic-tv-playlist.m3u"
CACHE_FILE          = "cstar_cache.json"
REPO                = "ExoticSecurityWeb/iptv-exotic"
GITHUB_TOKEN        = os.environ.get("GITHUB_TOKEN", "")
DISCORD_WEBHOOK     = os.environ.get("DISCORD_WEBHOOK_CSTAR", "")

# Noms CStar dans ta playlist (insensible à la casse)
CSTAR_EXACT_NAMES = {
    "cstar",
    "cstar (720p)",
    "cstar (720p) [geo-blocked]",
    "cstar hd",
    "c star (720p) [geo-blocked]",
    "c star",
}

REQUEST_TIMEOUT = 20
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0 ExoticTV-Updater/1.0"})
_DEFAULT_BRANCH_CACHE = None

# ─── FETCH CSTAR URL ─────────────────────────────────────────────────────────
def fetch_cstar_url() -> str:
    """Récupère l'URL HLS CStar via l'API embed Dailymotion."""
    log.info(f"🔍 Fetch URL CStar depuis Dailymotion (id: {DM_VIDEO_ID})…")

    # API embed Dailymotion — retourne les qualités disponibles
    url = f"https://www.dailymotion.com/player/metadata/video/{DM_VIDEO_ID}?embedder=https://www.dailymotion.com&locale=fr&dmV1st=1"
    r = SESSION.get(url, timeout=REQUEST_TIMEOUT)

    if r.status_code != 200:
        raise RuntimeError(f"Dailymotion API HTTP {r.status_code}")

    data = r.json()

    # Cherche une URL HLS dans les qualités
    qualities = data.get("qualities", {})
    for quality_name in ["auto", "1080", "720", "480", "380", "240"]:
        items = qualities.get(quality_name, [])
        for item in items:
            if isinstance(item, dict) and item.get("type") == "application/x-mpegURL":
                hls_url = item.get("url", "")
                if hls_url:
                    log.info(f"✅ URL HLS trouvée (qualité {quality_name})")
                    return hls_url

    # Fallback : cherche dans stream_hls_url
    hls = data.get("stream_hls_url", "")
    if hls:
        log.info("✅ URL HLS trouvée via stream_hls_url")
        return hls

    # Fallback 2 : URL cdndirector directe
    dm_url = f"https://cdndirector.dailymotion.com/cdn/live/video/{DM_VIDEO_ID}.m3u8"
    r2 = SESSION.head(dm_url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
    if r2.status_code < 400:
        log.info("✅ URL cdndirector validée")
        return dm_url

    raise RuntimeError("Impossible de trouver une URL HLS CStar valide")

# ─── GITHUB API ──────────────────────────────────────────────────────────────
def gh_headers() -> dict:
    h = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        h["Authorization"] = f"token {GITHUB_TOKEN}"
    return h

def get_default_branch() -> str:
    global _DEFAULT_BRANCH_CACHE
    if _DEFAULT_BRANCH_CACHE:
        return _DEFAULT_BRANCH_CACHE
    r = SESSION.get(f"https://api.github.com/repos/{REPO}", headers=gh_headers(), timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    _DEFAULT_BRANCH_CACHE = r.json()["default_branch"]
    return _DEFAULT_BRANCH_CACHE

def github_get_raw(path: str) -> str:
    branch = get_default_branch()
    url = f"https://raw.githubusercontent.com/{REPO}/{branch}/{path}"
    r = SESSION.get(url, timeout=REQUEST_TIMEOUT)
    if r.ok:
        r.encoding = "utf-8"
        return r.text
    # Fallback API
    r2 = SESSION.get(f"https://api.github.com/repos/{REPO}/contents/{path}", headers=gh_headers(), timeout=REQUEST_TIMEOUT)
    if r2.status_code == 404:
        raise FileNotFoundError(f"Introuvable : {path}")
    r2.raise_for_status()
    raw_b64 = r2.json()["content"].replace("\n", "")
    return base64.b64decode(raw_b64).decode("utf-8", errors="replace")

def github_get_sha(path: str) -> str:
    r = SESSION.get(f"https://api.github.com/repos/{REPO}/contents/{path}", headers=gh_headers(), timeout=REQUEST_TIMEOUT)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()["sha"]

def github_put(path: str, content: str, sha: str | None, message: str) -> None:
    payload = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
    }
    if sha:
        payload["sha"] = sha
    r = SESSION.put(
        f"https://api.github.com/repos/{REPO}/contents/{path}",
        headers=gh_headers(), json=payload, timeout=REQUEST_TIMEOUT
    )
    r.raise_for_status()

# ─── CACHE ───────────────────────────────────────────────────────────────────
def load_cache() -> tuple[dict, str | None]:
    try:
        content = github_get_raw(CACHE_FILE)
        sha = github_get_sha(CACHE_FILE)
        return json.loads(content), sha
    except FileNotFoundError:
        log.info("Cache absent — sera créé après la première mise à jour")
        return {"last_url": "", "last_updated": "", "update_count": 0}, None
    except Exception as exc:
        log.warning(f"Cache illisible ({exc}) — réinitialisation")
        return {"last_url": "", "last_updated": "", "update_count": 0}, None

def save_cache(cache: dict, sha: str | None) -> None:
    try:
        github_put(CACHE_FILE, json.dumps(cache, indent=2), sha, f"🔄 Cache CStar — {_now()}")
        log.info("💾 Cache sauvegardé")
    except Exception as exc:
        log.warning(f"Cache non sauvegardé : {exc}")

# ─── UPDATE PLAYLIST ─────────────────────────────────────────────────────────
def update_playlist(new_url: str) -> int:
    log.info(f"📝 Lecture de {PLAYLIST_FILE}…")
    content = github_get_raw(PLAYLIST_FILE)
    sha = github_get_sha(PLAYLIST_FILE)

    lines = content.splitlines()
    new_lines = []
    i = 0
    count = 0
    trailing_newline = content.endswith("\n")
    all_names = []

    while i < len(lines):
        line = lines[i]
        if line.startswith("#EXTINF"):
            m = re.search(r",(.+)$", line)
            if m:
                name = m.group(1).strip()
                all_names.append(name)
                if name.lower() in CSTAR_EXACT_NAMES:
                    new_lines.append(line)
                    i += 1
                    while i < len(lines) and lines[i].startswith("#"):
                        new_lines.append(lines[i])
                        i += 1
                    if i < len(lines) and not lines[i].startswith("#"):
                        log.info(f"  ↳ «{name}»")
                        log.info(f"    ancien : {lines[i][:90]}")
                        log.info(f"    nouveau: {new_url[:90]}")
                        i += 1
                        count += 1
                    new_lines.append(new_url)
                    continue
        new_lines.append(line)
        i += 1

    if count == 0:
        log.error("❌ Aucune chaîne CStar trouvée dans la playlist !")
        cstar_names = [n for n in all_names if "star" in n.lower() or "cstar" in n.lower()]
        if cstar_names:
            log.info(f"   Noms contenant 'star' : {cstar_names}")
        return 0

    new_content = "\n".join(new_lines)
    if trailing_newline:
        new_content += "\n"

    github_put(PLAYLIST_FILE, new_content, sha, f"📺 CStar auto-updated ({count}) — {_now()}")
    log.info(f"✅ Playlist mise à jour — {count} remplacement(s)")
    return count

# ─── DISCORD ─────────────────────────────────────────────────────────────────
def notify_discord(old_url: str, new_url: str, count: int, total: int) -> None:
    if not DISCORD_WEBHOOK:
        log.warning("⚠️ DISCORD_WEBHOOK_CSTAR absent — notification ignorée")
        return
    payload = {"embeds": [{
        "title": "📺 CStar — URL mise à jour automatiquement",
        "description": f"**{count}** occurrence(s) mise(s) à jour dans `{PLAYLIST_FILE}`",
        "color": 0x22d3ee,
        "fields": [
            {"name": "✅ Nouvelle URL", "value": f"```{new_url[:100]}```", "inline": False},
            {"name": "💀 Ancienne URL", "value": f"```{old_url[:100] if old_url else 'N/A'}```", "inline": False},
            {"name": "🔢 Mise à jour n°", "value": str(total), "inline": True},
            {"name": "🕐 Heure", "value": _now(), "inline": True},
        ],
        "footer": {"text": "Exotic CStar Auto-Updater v1.0 • 🌴 Pink Paradise"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }]}
    try:
        r = SESSION.post(DISCORD_WEBHOOK, json=payload, timeout=10)
        if r.status_code in (200, 204):
            log.info("✅ Discord notifié")
        else:
            log.warning(f"Discord HTTP {r.status_code}")
    except Exception as exc:
        log.warning(f"❌ Discord KO : {exc}")

# ─── UTILS ───────────────────────────────────────────────────────────────────
def _now() -> str:
    return datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")

# ─── MAIN ────────────────────────────────────────────────────────────────────
def main() -> None:
    log.info("─" * 55)
    log.info(f"📺 Exotic CStar Auto-Updater v1.0 — {_now()}")
    log.info("─" * 55)

    if not GITHUB_TOKEN:
        log.error("❌ GITHUB_TOKEN manquant")
        sys.exit(1)

    # 1. Fetch URL CStar
    try:
        new_url = fetch_cstar_url()
        log.info(f"🎯 URL CStar : {new_url[:80]}…")
    except Exception as exc:
        log.error(f"❌ Fetch CStar échoué : {exc}")
        sys.exit(1)

    # 2. Cache
    cache, cache_sha = load_cache()
    last_url = cache.get("last_url", "")

    # 3. Même URL → rien
    if new_url == last_url:
        log.info("📌 URL identique — aucune mise à jour nécessaire")
        return

    log.info("🔄 Nouvelle URL détectée — mise à jour…")

    # 4. Update playlist
    try:
        count = update_playlist(new_url)
    except Exception as exc:
        log.error(f"❌ Mise à jour échouée : {exc}")
        sys.exit(1)

    if count == 0:
        log.error("❌ Aucun remplacement — arrêt")
        sys.exit(1)

    # 5. Cache
    cache["last_url"] = new_url
    cache["last_updated"] = _now()
    cache["update_count"] = cache.get("update_count", 0) + 1
    save_cache(cache, cache_sha)

    # 6. Discord
    notify_discord(last_url, new_url, count, cache["update_count"])
    log.info("🎉 Terminé !")

if __name__ == "__main__":
    main()

