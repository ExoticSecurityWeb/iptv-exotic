#!/usr/bin/env python3
"""
Exotic TFX Auto-Updater v4.2
- Fix décodage base64 playlist GitHub API
- Détection TFX robuste
- Discord fonctionnel
- Détection automatique de la branche par défaut
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

TFX_EXACT_NAMES = {
    "tfx (1080p)",
    "tfx (1080p) [geo-blocked]",
    "tfx",
    "tfx hd",
    "tfx (720p)",
}

TFX_KEYWORDS    = {"TFX", "NT1"}
MAX_RETRIES     = 3
RETRY_BACKOFF   = 2
REQUEST_TIMEOUT = 20

# ─── SESSION + CACHE BRANCHE ──────────────────────────────────────────────────
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "ExoticTV-Updater/4.2"})
_DEFAULT_BRANCH_CACHE = None


def fetch(url: str, headers: dict = None, retries: int = MAX_RETRIES) -> requests.Response:
    h = headers or {}
    delay = RETRY_BACKOFF
    last_exc = None
    for attempt in range(1, retries + 2):
        try:
            r = SESSION.get(url, headers=h, timeout=REQUEST_TIMEOUT)
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", delay))
                log.warning(f"Rate limit — attente {wait}s")
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


def get_default_branch() -> str:
    """Récupère la branche par défaut du repo via l'API GitHub."""
    global _DEFAULT_BRANCH_CACHE
    if _DEFAULT_BRANCH_CACHE:
        return _DEFAULT_BRANCH_CACHE
    
    try:
        r = fetch(
            f"https://api.github.com/repos/{REPO}",
            headers=gh_headers()
        )
        r.raise_for_status()
        _DEFAULT_BRANCH_CACHE = r.json()["default_branch"]
        log.info(f"🔍 Branche par défaut détectée : {_DEFAULT_BRANCH_CACHE}")
        return _DEFAULT_BRANCH_CACHE
    except Exception as exc:
        log.error(f"❌ Impossible de récupérer la branche par défaut : {exc}")
        log.warning("⚠️ Utilisation de 'main' par défaut")
        _DEFAULT_BRANCH_CACHE = "main"
        return _DEFAULT_BRANCH_CACHE


def github_get(path: str) -> dict:
    r = fetch(
        f"https://api.github.com/repos/{REPO}/contents/{path}",
        headers=gh_headers()
    )
    if r.status_code == 404:
        raise FileNotFoundError(f"Introuvable : {path}")
    r.raise_for_status()
    return r.json()


def github_get_raw(path: str) -> str:
    """
    Télécharge le fichier via l'URL raw GitHub (pas l'API JSON).
    Evite complètement le décodage base64 — on récupère le texte brut directement.
    """
    default_branch = get_default_branch()
    
    # Essai 1 : raw.githubusercontent.com
    url = f"https://raw.githubusercontent.com/{REPO}/{default_branch}/{path}"
    log.debug(f"📥 Tentative raw.githubusercontent : {url}")
    
    try:
        r = fetch(url)
        if r.ok:
            r.encoding = "utf-8"
            log.debug(f"✅ Fichier récupéré via raw.githubusercontent")
            return r.text
    except Exception as exc:
        log.warning(f"⚠️ Échec raw.githubusercontent ({exc}) — tentative API GitHub")

    # Essai 2 : API GitHub avec décodage manuel
    try:
        data = github_get(path)
        raw_b64 = data["content"]
        cleaned = raw_b64.replace("\n", "")
        log.debug(f"✅ Fichier récupéré via API GitHub")
        return base64.b64decode(cleaned).decode("utf-8", errors="replace")
    except Exception as exc:
        log.error(f"❌ Impossible de récupérer {path} : {exc}")
        raise


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
    log.info(f"📝 Lecture de {PLAYLIST_FILE} via raw URL…")

    # Lire via raw pour éviter tout problème de décodage base64
    content = github_get_raw(PLAYLIST_FILE)

    # On a besoin du SHA pour le PUT — on le récupère via l'API
    sha = github_get(PLAYLIST_FILE)["sha"]

    lines = content.splitlines()
    new_lines = []
    i = 0
    count = 0
    trailing_newline = content.endswith("\n")

    # Debug : collecter tous les noms
    all_names = []

    while i < len(lines):
        line = lines[i]
        if line.startswith("#EXTINF"):
            m = re.search(r",(.+)$", line)
            if m:
                name = m.group(1).strip()
                all_names.append(name)
                if name.lower() in TFX_EXACT_NAMES:
                    new_lines.append(line)
                    i += 1
                    # Sauter les commentaires intermédiaires
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
        log.error(f"   Noms cherchés (insensible casse) : {sorted(TFX_EXACT_NAMES)}")
        log.info(f"   Total chaînes dans la playlist : {len(all_names)}")
        # Afficher tous les noms contenant "tf" pour aider
        tf_names = [n for n in all_names if "tf" in n.lower()]
        if tf_names:
            log.info(f"   Noms contenant 'tf' : {tf_names}")
        else:
            log.info("   Aucun nom contenant 'tf' — 30 premiers noms :")
            for n in all_names[:30]:
                log.info(f"   · «{n}»")
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
        content = github_get_raw(CACHE_FILE)
        sha = github_get(CACHE_FILE)["sha"]
        return json.loads(content), sha
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
            "footer": {"text": "Exotic TFX Auto-Updater v4.2 • 🌴 Pink Paradise"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }]
    }

    try:
        r = SESSION.post(DISCORD_WEBHOOK_TFX, json=payload, timeout=10)
        if r.status_code in (200, 204):
            log.info("✅ Discord notifié")
        else:
            log.warning(f"Discord HTTP {r.status_code} : {r.text[:200]}")
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
    log.info(f"📺 Exotic TFX Auto-Updater v4.2 — {_now()}")
    log.info("─" * 55)

    _check_config()

    # 1. Scanner ParaTV
    try:
        new_url = find_tfx_url()
        log.info(f"🎯 URL TFX : {new_url[:80]}…")
    except Exception as exc:
        log.error(f"❌ Scan ParaTV échoué : {exc}")
        sys.exit(1)

    # 2. Cache
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
        log.error("❌ Aucun remplacement — arrêt")
        sys.exit(1)

    # 5. Cache
    cache["last_url"] = new_url
    cache["update_count"] = cache.get("update_count", 0) + 1
    save_cache(cache, cache_sha)

    # 6. Discord
    notify_discord(last_url, new_url, count, cache["update_count"])

    log.info("🎉 Terminé avec succès !")


if __name__ == "__main__":
    main()