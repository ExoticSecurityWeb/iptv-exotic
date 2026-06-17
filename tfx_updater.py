#!/usr/bin/env python3
"""
Exotic TFX Auto-Updater v4.1
- Nom exact confirmé : "TFX (1080p)" dans la playlist
- Scan dynamique ParaTV
- Discord fonctionnel
- Logs détaillés pour GitHub Actions
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
log = logging.getLogger("tfx-updater")

# ─── CONFIG ──────────────────────────────────────────────────────────────────
PARATV_REPO         = "Paradise-91/ParaTV"
PARATV_STREAMS_DIR  = "streams"
PLAYLIST_FILE       = "exotic-tv-playlist.m3u"
CACHE_FILE          = "tfx_cache.json"
REPO                = "ExoticSecurityWeb/iptv-exotic"
GITHUB_TOKEN        = os.environ.get("GITHUB_TOKEN", "")
DISCORD_WEBHOOK_TFX = os.environ.get("DISCORD_WEBHOOK_TFX", "")

# Nom exact tel qu'il apparaît dans la playlist (après la virgule du #EXTINF)
# + variantes au cas où
TFX_EXACT_NAMES = {
    "TFX (1080p)",
    "TFX (1080p) [Geo-Blocked]",
    "TFX",
    "TFX HD",
    "TFX (720p)",
}

# Mots-clés pour détecter TFX dans les fichiers .m3u8 de ParaTV
TFX_KEYWORDS = {"TFX", "NT1"}

# HTTP
MAX_RETRIES     = 3
RETRY_BACKOFF   = 2
REQUEST_TIMEOUT = 20

# ─── SESSION ─────────────────────────────────────────────────────────────────
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "ExoticTV-Updater/4.1"})


def fetch(url: str, headers: dict = None, retries: int = MAX_RETRIES) -> requests.Response:
    h = headers or {}
    delay = RETRY_BACKOFF
    last_exc = None
    for attempt in range(1, retries + 2):
        try:
            r = SESSION.get(url, headers=h, timeout=REQUEST_TIMEOUT)
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", delay))
                log.warning(f"Rate limit GitHub — attente {wait}s")
                time.sleep(wait)
                continue
            return r
        except requests.RequestException as exc:
            last_exc = exc
            if attempt > retries:
                break
            log.warning(f"Réseau KO ({exc}) — retry {attempt}/{retries} dans {delay}s")
            time.sleep(delay)
            delay *= 2
    raise RuntimeError(f"Impossible de joindre {url} : {last_exc}")

# ─── GITHUB API ──────────────────────────────────────────────────────────────
def gh_headers() -> dict:
    h = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        h["Authorization"] = f"token {GITHUB_TOKEN}"
    return h


def github_get(path: str) -> dict:
    r = fetch(f"https://api.github.com/repos/{REPO}/contents/{path}", headers=gh_headers())
    if r.status_code == 404:
        raise FileNotFoundError(f"Introuvable : {path}")
    r.raise_for_status()
    return r.json()


def github_put(path: str, content: str, sha: str | None, message: str) -> dict:
    payload = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
    }
    if sha:
        payload["sha"] = sha
    r = SESSION.put(
        f"https://api.github.com/repos/{REPO}/contents/{path}",
        headers=gh_headers(),
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )
    if r.status_code == 409:
        raise RuntimeError("Conflit GitHub 409 — SHA obsolète, relance")
    r.raise_for_status()
    return r.json()


def decode_content(data: dict) -> str:
    raw = data["content"].replace("\n", "").replace(" ", "")
    return base64.b64decode(raw).decode("utf-8", errors="replace")

# ─── SCAN PARATV ─────────────────────────────────────────────────────────────
def find_tfx_url() -> str:
    log.info("🔍 Scan du repo ParaTV…")
    h = gh_headers()

    r = fetch(
        f"https://api.github.com/repos/{PARATV_REPO}/contents/{PARATV_STREAMS_DIR}",
        headers=h,
    )
    if r.status_code == 404:
        raise RuntimeError(f"Dossier {PARATV_STREAMS_DIR}/ introuvable dans {PARATV_REPO}")
    r.raise_for_status()

    candidates = []
    for item in r.json():
        if item["type"] == "file" and item["name"].lower().endswith(".m3u8"):
            candidates.append(item)
        elif item["type"] == "dir":
            try:
                r2 = fetch(item["url"], headers=h)
                r2.raise_for_status()
                for f in r2.json():
                    if f["type"] == "file" and f["name"].lower().endswith(".m3u8"):
                        candidates.append(f)
            except Exception as exc:
                log.warning(f"Impossible de lister {item['name']}/ : {exc}")

    log.info(f"🔎 {len(candidates)} fichier(s) .m3u8 à analyser")

    for c in candidates:
        url = _extract_tfx_from_file(c, h)
        if url:
            return url

    raise RuntimeError("Aucun stream TFX trouvé dans ParaTV")


def _extract_tfx_from_file(file_info: dict, headers: dict) -> str | None:
    raw_url = file_info.get("download_url")
    if not raw_url:
        return None
    try:
        r = fetch(raw_url, headers=headers)
        if r.status_code != 200:
            return None
        text = r.text
        if not any(kw in text.upper() for kw in TFX_KEYWORDS):
            return None
        stream_urls = [
            l.strip() for l in text.splitlines()
            if l.strip() and not l.startswith("#")
        ]
        if not stream_urls:
            return None
        log.info(f"✅ TFX trouvé : {file_info.get('name')} → {stream_urls[0][:70]}…")
        return stream_urls[0]
    except Exception as exc:
        log.warning(f"Erreur {file_info.get('name', '?')} : {exc}")
        return None

# ─── PLAYLIST ────────────────────────────────────────────────────────────────
def update_playlist(new_url: str) -> int:
    """
    Remplace l'URL de toutes les occurrences TFX dans la playlist.
    Affiche les noms trouvés pour debug si aucun match.
    """
    log.info(f"📝 Lecture de {PLAYLIST_FILE}…")
    data = github_get(PLAYLIST_FILE)
    content = decode_content(data)
    sha = data["sha"]

    lines = content.splitlines()
    new_lines = []
    i = 0
    count = 0
    trailing_newline = content.endswith("\n")

    # Debug : collecter tous les noms de chaînes pour diagnostic
    all_names = []

    while i < len(lines):
        line = lines[i]
        if line.startswith("#EXTINF"):
            m = re.search(r",(.+)$", line)
            if m:
                name = m.group(1).strip()
                all_names.append(name)
                # Match exact (insensible à la casse)
                if name.lower() in {n.lower() for n in TFX_EXACT_NAMES}:
                    new_lines.append(line)
                    i += 1
                    # Sauter les commentaires intermédiaires éventuels
                    while i < len(lines) and lines[i].startswith("#"):
                        new_lines.append(lines[i])
                        i += 1
                    # Remplacer l'ancienne URL
                    if i < len(lines) and not lines[i].startswith("#"):
                        old_url = lines[i]
                        log.info(f"  ↳ «{name}»")
                        log.info(f"    ancien : {old_url[:90]}")
                        log.info(f"    nouveau: {new_url[:90]}")
                        i += 1
                        count += 1
                    new_lines.append(new_url)
                    continue
        new_lines.append(line)
        i += 1

    if count == 0:
        log.error("❌ Aucune chaîne TFX trouvée dans la playlist !")
        log.error(f"   Noms cherchés : {sorted(TFX_EXACT_NAMES)}")
        log.error("   Chaînes présentes dans la playlist :")
        for n in all_names:
            if "tf" in n.lower() or "tfx" in n.lower():
                log.error(f"   → (possible TFX) «{n}»")
        # Afficher les 30 premiers noms pour debug
        log.info("   30 premiers noms de la playlist :")
        for n in all_names[:30]:
            log.info(f"   · {n}")
        return 0

    new_content = "\n".join(new_lines)
    if trailing_newline:
        new_content += "\n"

    github_put(
        PLAYLIST_FILE, new_content, sha,
        f"📺 TFX auto-updated ({count} occurrence(s)) — {_now()}"
    )
    log.info(f"✅ Playlist mise à jour — {count} remplacement(s)")
    return count

# ─── CACHE ───────────────────────────────────────────────────────────────────
def load_cache() -> tuple[dict, str | None]:
    try:
        data = github_get(CACHE_FILE)
        return json.loads(decode_content(data)), data["sha"]
    except FileNotFoundError:
        log.info("Cache absent — sera créé après la première mise à jour")
        return {"last_url": "", "last_updated": "", "update_count": 0}, None
    except Exception as exc:
        log.warning(f"Cache illisible ({exc}) — réinitialisation")
        return {"last_url": "", "last_updated": "", "update_count": 0}, None


def save_cache(cache: dict, sha: str | None) -> None:
    try:
        github_put(
            CACHE_FILE,
            json.dumps(cache, indent=2, ensure_ascii=False),
            sha,
            f"🔄 Cache TFX — {_now()}",
        )
        log.info("💾 Cache sauvegardé")
    except Exception as exc:
        log.warning(f"Cache non sauvegardé : {exc}")

# ─── DISCORD ─────────────────────────────────────────────────────────────────
def notify_discord(old_url: str, new_url: str, count: int, total: int) -> None:
    if not DISCORD_WEBHOOK_TFX:
        log.warning("⚠️ DISCORD_WEBHOOK_TFX absent — notification ignorée")
        return

    def trunc(u: str, n: int = 100) -> str:
        return u[:n] + "…" if len(u) > n else u

    payload = {
        "embeds": [{
            "title": "📺 TFX — URL mise à jour automatiquement",
            "description": f"**{count}** occurrence(s) mise(s) à jour dans `{PLAYLIST_FILE}`",
            "color": 0xf472b6,
            "fields": [
                {
                    "name": "✅ Nouvelle URL",
                    "value": f"```{trunc(new_url)}```",
                    "inline": False,
                },
                {
                    "name": "💀 Ancienne URL",
                    "value": f"```{trunc(old_url) if old_url else 'N/A — première détection'}```",
                    "inline": False,
                },
                {
                    "name": "🔢 Mise à jour n°",
                    "value": str(total),
                    "inline": True,
                },
                {
                    "name": "🕐 Heure",
                    "value": _now(),
                    "inline": True,
                },
            ],
            "footer": {"text": "Exotic TFX Auto-Updater v4.1 • 🌴 Pink Paradise"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }]
    }

    try:
        r = SESSION.post(DISCORD_WEBHOOK_TFX, json=payload, timeout=10)
        # Discord renvoie 204 No Content = succès
        if r.status_code in (200, 204):
            log.info("✅ Discord notifié")
        else:
            log.warning(f"Discord réponse inattendue : HTTP {r.status_code} — {r.text[:200]}")
    except Exception as exc:
        log.warning(f"❌ Discord KO : {exc}")

# ─── UTILS ───────────────────────────────────────────────────────────────────
def _now() -> str:
    return datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")


def _check_config() -> None:
    if not GITHUB_TOKEN:
        log.error("❌ GITHUB_TOKEN manquant")
        sys.exit(1)
    if not DISCORD_WEBHOOK_TFX:
        log.warning("⚠️ DISCORD_WEBHOOK_TFX non défini — pas de notification")

# ─── MAIN ────────────────────────────────────────────────────────────────────
def main() -> None:
    log.info("─" * 55)
    log.info(f"📺 Exotic TFX Auto-Updater v4.1 — {_now()}")
    log.info("─" * 55)

    _check_config()

    # 1. Scanner ParaTV pour trouver la nouvelle URL
    try:
        new_url = find_tfx_url()
        log.info(f"🎯 URL TFX : {new_url[:80]}…")
    except Exception as exc:
        log.error(f"❌ Scan ParaTV échoué : {exc}")
        sys.exit(1)

    # 2. Charger le cache
    cache, cache_sha = load_cache()
    last_url = cache.get("last_url", "")

    # 3. Même URL → rien à faire
    if new_url == last_url:
        log.info("📌 URL identique — aucune mise à jour nécessaire")
        return

    log.info("🔄 Nouvelle URL détectée — mise à jour en cours…")

    # 4. Mettre à jour la playlist
    try:
        count = update_playlist(new_url)
    except Exception as exc:
        log.error(f"❌ Mise à jour échouée : {exc}")
        sys.exit(1)

    if count == 0:
        log.error("❌ Aucun remplacement effectué")
        sys.exit(1)

    # 5. Sauvegarder le cache
    cache["last_url"] = new_url
    cache["update_count"] = cache.get("update_count", 0) + 1
    save_cache(cache, cache_sha)

    # 6. Discord
    notify_discord(last_url, new_url, count, cache["update_count"])

    log.info("🎉 Terminé avec succès !")


if __name__ == "__main__":
    main()
