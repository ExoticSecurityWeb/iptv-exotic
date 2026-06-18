#!/usr/bin/env python3
"""
Exotic CStar Auto-Updater v1.0
- Fetch l'URL HLS CStar depuis raw.githubusercontent.com/schumijo/iptv
- Même logique que TFX : retourne l'URL raw du master complet
- Met à jour exotic-tv-playlist.m3u si l'URL change
- Notifie Discord
"""

import os
import re
import sys
import json
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
CSTAR_SOURCE        = "https://raw.githubusercontent.com/schumijo/iptv/main/playlists/canalplus/cstar.m3u8"
PLAYLIST_FILE       = "exotic-tv-playlist.m3u"
CACHE_FILE          = "cstar_cache.json"
REPO                = "ExoticSecurityWeb/iptv-exotic"
GITHUB_TOKEN        = os.environ.get("GITHUB_TOKEN", "")
DISCORD_WEBHOOK     = os.environ.get("DISCORD_WEBHOOK_CSTAR", "")

# Noms CStar dans ta playlist (insensible à la casse)
# Lance le script une fois pour voir le nom exact si ça match pas
CSTAR_EXACT_NAMES = {
    "cstar",
    "cstar hd",
    "cstar (720p)",
    "cstar (1080p)",
    "cstar (720p) [geo-blocked]",
    "cstar (1080p) [geo-blocked]",
    "c star",
    "c star hd",
}

REQUEST_TIMEOUT       = 20
_DEFAULT_BRANCH_CACHE = None

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "ExoticTV-CStar-Updater/1.0"})

# ─── FETCH CSTAR URL ─────────────────────────────────────────────────────────
def fetch_cstar_url() -> str:
    """
    Télécharge le fichier cstar.m3u8 depuis le repo schumijo.
    - Si c'est un master HLS → retourne l'URL du fichier lui-même
      (même logique que TFX : le player gère audio+vidéo depuis le master)
    - Si c'est un stream direct → retourne la première URL non-commentaire
    """
    log.info(f"🔍 Fetch CStar depuis schumijo/iptv…")
    r = SESSION.get(CSTAR_SOURCE, timeout=REQUEST_TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} sur {CSTAR_SOURCE}")

    text = r.text
    r.encoding = "utf-8"

    # Log les premières lignes pour debug
    lines = text.splitlines()
    log.info(f"📄 Contenu ({len(lines)} lignes) :")
    for i, line in enumerate(lines[:20]):
        log.info(f"  L{i+1:02d}: {line}")

    # Master HLS → retourner l'URL source directement
    if "#EXT-X-STREAM-INF" in text:
        log.info("📋 Master HLS détecté → URL source retournée")
        log.info(f"✅ CStar master : {CSTAR_SOURCE[:80]}…")
        return CSTAR_SOURCE

    # Stream direct → extraire la première URL
    stream_urls = [l.strip() for l in lines if l.strip() and not l.startswith("#")]
    if stream_urls:
        log.info(f"✅ CStar stream direct : {stream_urls[0][:80]}…")
        return stream_urls[0]

    raise RuntimeError("Aucune URL valide trouvée dans le fichier CStar")

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
    log.info(f"🔍 Branche : {_DEFAULT_BRANCH_CACHE}")
    return _DEFAULT_BRANCH_CACHE


def github_get_raw(path: str) -> str:
    branch = get_default_branch()
    url = f"https://raw.githubusercontent.com/{REPO}/{branch}/{path}"
    r = SESSION.get(url, timeout=REQUEST_TIMEOUT)
    if r.ok:
        r.encoding = "utf-8"
        return r.text
    # Fallback API
    r2 = SESSION.get(
        f"https://api.github.com/repos/{REPO}/contents/{path}",
        headers=gh_headers(), timeout=REQUEST_TIMEOUT
    )
    if r2.status_code == 404:
        raise FileNotFoundError(f"Introuvable : {path}")
    r2.raise_for_status()
    return base64.b64decode(r2.json()["content"].replace("\n", "")).decode("utf-8", errors="replace")


def github_get_sha(path: str) -> str | None:
    r = SESSION.get(
        f"https://api.github.com/repos/{REPO}/contents/{path}",
        headers=gh_headers(), timeout=REQUEST_TIMEOUT
    )
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
    if r.status_code == 409:
        raise RuntimeError("Conflit GitHub 409 — SHA obsolète")
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
        github_put(CACHE_FILE, json.dumps(cache, indent=2, ensure_ascii=False), sha, f"🔄 Cache CStar — {_now()}")
        log.info("💾 Cache sauvegardé")
    except Exception as exc:
        log.warning(f"Cache non sauvegardé : {exc}")

# ─── UPDATE PLAYLIST ─────────────────────────────────────────────────────────
def update_playlist(new_url: str) -> int:
    log.info(f"📝 Lecture de {PLAYLIST_FILE}…")
    content = github_get_raw(PLAYLIST_FILE)
    sha     = github_get_sha(PLAYLIST_FILE)

    lines            = content.splitlines()
    new_lines        = []
    i                = 0
    count            = 0
    trailing_newline = content.endswith("\n")
    all_names        = []

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
        log.error(f"   Noms cherchés : {sorted(CSTAR_EXACT_NAMES)}")
        cstar_names = [n for n in all_names if "star" in n.lower() or "cstar" in n.lower()]
        if cstar_names:
            log.info(f"   Noms contenant 'star' : {cstar_names}")
        else:
            log.info("   30 premiers noms :")
            for n in all_names[:30]:
                log.info(f"   · «{n}»")
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

    def trunc(u: str, n: int = 100) -> str:
        return u[:n] + "…" if len(u) > n else u

    payload = {"embeds": [{
        "title": "📺 CStar — URL mise à jour automatiquement",
        "description": f"**{count}** occurrence(s) dans `{PLAYLIST_FILE}`",
        "color": 0x22d3ee,
        "fields": [
            {"name": "✅ Nouvelle URL",   "value": f"```{trunc(new_url)}```", "inline": False},
            {"name": "💀 Ancienne URL",   "value": f"```{trunc(old_url) if old_url else 'N/A'}```", "inline": False},
            {"name": "🔢 Mise à jour n°", "value": str(total), "inline": True},
            {"name": "🕐 Heure",          "value": _now(),     "inline": True},
        ],
        "footer": {"text": "Exotic CStar Auto-Updater v1.0 • 🌴 Pink Paradise"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }]}
    try:
        r = SESSION.post(DISCORD_WEBHOOK, json=payload, timeout=10)
        if r.status_code in (200, 204):
            log.info("✅ Discord notifié")
        else:
            log.warning(f"Discord HTTP {r.status_code} : {r.text[:200]}")
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
    if not DISCORD_WEBHOOK:
        log.warning("⚠️ DISCORD_WEBHOOK_CSTAR non défini — pas de notification")

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

