#!/usr/bin/env python3
"""
Exotic TF1 Auto-Updater v1.0
- Scanne dynamiquement le repo ParaTV pour trouver le fichier TF1
- Retourne le master HLS complet (audio+vidéo) via raw.githubusercontent
- Met à jour exotic-tv-playlist.m3u automatiquement
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
log = logging.getLogger("tf1-updater")

# ─── CONFIG ──────────────────────────────────────────────────────────────────
PARATV_REPO        = "Paradise-91/ParaTV"
PARATV_STREAMS_DIR = "streams"
PLAYLIST_FILE      = "exotic-tv-playlist.m3u"
CACHE_FILE         = "tf1_cache.json"
REPO               = "ExoticSecurityWeb/iptv-exotic"
GITHUB_TOKEN       = os.environ.get("GITHUB_TOKEN", "")
DISCORD_WEBHOOK_TF1 = os.environ.get("DISCORD_WEBHOOK_TF1", "")

TF1_EXACT_NAMES = {
    "tf1",
    "tf1 (720p)",
    "tf1 (1080p)",
    "tf1 hd",
    "tf1 (720p) [geo-blocked]",
    "tf1 hd (720p) [geo-blocked]",
}

# Mots-clés pour identifier le bon fichier — ATTENTION à ne pas matcher
# TF1 Series Films / TFX qui contiennent aussi "TF1" ou "NT1" dans certains noms
TF1_KEYWORDS_POSITIVE = {"TF1"}
TF1_KEYWORDS_EXCLUDE  = {"TFX", "NT1", "SERIES", "SERIE", "FILMS", "LCI"}

MAX_RETRIES     = 3
RETRY_BACKOFF   = 2
REQUEST_TIMEOUT = 20

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "ExoticTV-Updater/1.0"})
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
            log.warning(f"Réseau KO ({exc}) — retry {attempt}/{retries}")
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
    global _DEFAULT_BRANCH_CACHE
    if _DEFAULT_BRANCH_CACHE:
        return _DEFAULT_BRANCH_CACHE
    try:
        r = fetch(f"https://api.github.com/repos/{REPO}", headers=gh_headers())
        r.raise_for_status()
        _DEFAULT_BRANCH_CACHE = r.json()["default_branch"]
        log.info(f"🔍 Branche : {_DEFAULT_BRANCH_CACHE}")
        return _DEFAULT_BRANCH_CACHE
    except Exception as exc:
        log.warning(f"Branche inconnue ({exc}) — fallback 'main'")
        _DEFAULT_BRANCH_CACHE = "main"
        return _DEFAULT_BRANCH_CACHE

def github_get(path: str) -> dict:
    r = fetch(f"https://api.github.com/repos/{REPO}/contents/{path}", headers=gh_headers())
    if r.status_code == 404:
        raise FileNotFoundError(f"Introuvable : {path}")
    r.raise_for_status()
    return r.json()

def github_get_raw(path: str) -> str:
    branch = get_default_branch()
    url = f"https://raw.githubusercontent.com/{REPO}/{branch}/{path}"
    try:
        r = fetch(url)
        if r.ok:
            r.encoding = "utf-8"
            return r.text
    except Exception as exc:
        log.warning(f"raw.githubusercontent KO ({exc}) — fallback API")
    data = github_get(path)
    cleaned = data["content"].replace("\n", "")
    return base64.b64decode(cleaned).decode("utf-8", errors="replace")

def github_put(path: str, content: str, sha, message: str) -> dict:
    payload = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
    }
    if sha:
        payload["sha"] = sha
    r = SESSION.put(
        f"https://api.github.com/repos/{REPO}/contents/{path}",
        headers=gh_headers(), json=payload, timeout=REQUEST_TIMEOUT,
    )
    if r.status_code == 409:
        raise RuntimeError("Conflit GitHub 409 — SHA obsolète, relance")
    r.raise_for_status()
    return r.json()

# ─── SCAN PARATV ─────────────────────────────────────────────────────────────
def find_tf1_url() -> str:
    log.info("🔍 Scan du repo ParaTV pour TF1…")
    h = gh_headers()

    r = fetch(
        f"https://api.github.com/repos/{PARATV_REPO}/contents/{PARATV_STREAMS_DIR}",
        headers=h,
    )
    if r.status_code == 404:
        raise RuntimeError(f"Dossier {PARATV_STREAMS_DIR}/ introuvable")
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
        url = _extract_tf1_from_file(c, h)
        if url:
            return url

    raise RuntimeError("Aucun stream TF1 trouvé dans ParaTV")


def _extract_tf1_from_file(file_info: dict, headers: dict) -> str | None:
    """
    Cherche le fichier TF1 (pas TFX, pas TF1 Series Films, pas LCI).
    Retourne le master HLS complet (URL raw ParaTV) pour garder audio+vidéo.
    """
    raw_url = file_info.get("download_url")
    if not raw_url:
        return None

    try:
        r = fetch(raw_url, headers=headers)
        if r.status_code != 200:
            return None
        text = r.text
        text_upper = text.upper()

        # Doit contenir TF1 mais pas les mots-clés d'exclusion
        if not any(kw in text_upper for kw in TF1_KEYWORDS_POSITIVE):
            return None
        if any(kw in text_upper for kw in TF1_KEYWORDS_EXCLUDE):
            return None

        fname = file_info.get("name", "?")

        # Master HLS avec variantes (#EXT-X-STREAM-INF) → garder l'URL raw du
        # fichier ParaTV lui-même pour préserver audio+vidéo (groupes #EXT-X-MEDIA)
        if "#EXT-X-STREAM-INF" in text:
            log.info(f"📋 Master HLS détecté : {fname}")
            log.info(f"✅ TF1 master complet (audio+vidéo) : {raw_url[:80]}…")
            return raw_url

        # Stream direct (pas de variantes)
        all_urls = [l.strip() for l in text.splitlines() if l.strip() and not l.startswith("#")]
        if not all_urls:
            return None
        log.info(f"✅ TF1 stream direct : {fname} → {all_urls[0][:80]}…")
        return all_urls[0]

    except Exception as exc:
        log.warning(f"Erreur {file_info.get('name', '?')} : {exc}")
        return None

# ─── PLAYLIST ────────────────────────────────────────────────────────────────
def update_playlist(new_url: str) -> int:
    log.info(f"📝 Lecture de {PLAYLIST_FILE}…")
    content = github_get_raw(PLAYLIST_FILE)
    sha     = github_get(PLAYLIST_FILE)["sha"]

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
                if name.lower() in TF1_EXACT_NAMES:
                    new_lines.append(line)
                    i += 1
                    while i < len(lines) and lines[i].startswith("#"):
                        new_lines.append(lines[i])
                        i += 1
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
        log.error("❌ Aucune chaîne TF1 trouvée !")
        log.error(f"   Noms cherchés : {sorted(TF1_EXACT_NAMES)}")
        tf_names = [n for n in all_names if "tf1" in n.lower()]
        if tf_names:
            log.info(f"   Noms contenant 'tf1' : {tf_names}")
        return 0

    new_content = "\n".join(new_lines)
    if trailing_newline:
        new_content += "\n"

    github_put(
        PLAYLIST_FILE, new_content, sha,
        f"📺 TF1 auto-updated ({count} occurrence(s)) — {_now()}"
    )
    log.info(f"✅ Playlist mise à jour — {count} remplacement(s)")
    return count

# ─── CACHE ───────────────────────────────────────────────────────────────────
def load_cache() -> tuple[dict, str | None]:
    try:
        content = github_get_raw(CACHE_FILE)
        sha     = github_get(CACHE_FILE)["sha"]
        return json.loads(content), sha
    except FileNotFoundError:
        log.info("Cache absent — sera créé après la première mise à jour")
        return {"last_url": "", "last_updated": "", "update_count": 0}, None
    except Exception as exc:
        log.warning(f"Cache illisible ({exc}) — réinitialisation")
        return {"last_url": "", "last_updated": "", "update_count": 0}, None

def save_cache(cache: dict, sha) -> None:
    try:
        github_put(CACHE_FILE, json.dumps(cache, indent=2, ensure_ascii=False), sha, f"🔄 Cache TF1 — {_now()}")
        log.info("💾 Cache sauvegardé")
    except Exception as exc:
        log.warning(f"Cache non sauvegardé : {exc}")

# ─── DISCORD ─────────────────────────────────────────────────────────────────
def notify_discord(old_url: str, new_url: str, count: int, total: int) -> None:
    if not DISCORD_WEBHOOK_TF1:
        log.warning("⚠️ DISCORD_WEBHOOK_TF1 absent")
        return

    def trunc(u, n=100):
        return u[:n] + "…" if len(u) > n else u

    payload = {"embeds": [{
        "title": "📺 TF1 — URL mise à jour automatiquement",
        "description": f"**{count}** occurrence(s) dans `{PLAYLIST_FILE}`",
        "color": 0x0033a0,
        "fields": [
            {"name": "✅ Nouvelle URL", "value": f"```{trunc(new_url)}```", "inline": False},
            {"name": "💀 Ancienne URL", "value": f"```{trunc(old_url) if old_url else 'N/A'}```", "inline": False},
            {"name": "🔢 Mise à jour n°", "value": str(total), "inline": True},
            {"name": "🕐 Heure", "value": _now(), "inline": True},
        ],
        "footer": {"text": "Exotic TF1 Auto-Updater v1.0 • 🌴 Pink Paradise"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }]}

    try:
        r = SESSION.post(DISCORD_WEBHOOK_TF1, json=payload, timeout=10)
        if r.status_code in (200, 204):
            log.info("✅ Discord notifié")
        else:
            log.warning(f"Discord HTTP {r.status_code}")
    except Exception as exc:
        log.warning(f"❌ Discord KO : {exc}")

# ─── UTILS ───────────────────────────────────────────────────────────────────
def _now() -> str:
    return datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")

def _check_config() -> None:
    if not GITHUB_TOKEN:
        log.error("❌ GITHUB_TOKEN manquant")
        sys.exit(1)
    if not DISCORD_WEBHOOK_TF1:
        log.warning("⚠️ DISCORD_WEBHOOK_TF1 non défini")

# ─── MAIN ────────────────────────────────────────────────────────────────────
def main() -> None:
    log.info("─" * 55)
    log.info(f"📺 Exotic TF1 Auto-Updater v1.0 — {_now()}")
    log.info("─" * 55)

    _check_config()

    try:
        new_url = find_tf1_url()
        log.info(f"🎯 URL finale : {new_url[:80]}…")
    except Exception as exc:
        log.error(f"❌ Scan ParaTV échoué : {exc}")
        sys.exit(1)

    cache, cache_sha = load_cache()
    last_url = cache.get("last_url", "")

    if new_url == last_url:
        log.info("📌 URL identique — aucune mise à jour")
        return

    log.info("🔄 Nouvelle URL — mise à jour…")

    try:
        count = update_playlist(new_url)
    except Exception as exc:
        log.error(f"❌ Mise à jour échouée : {exc}")
        sys.exit(1)

    if count == 0:
        log.error("❌ Aucun remplacement — arrêt")
        sys.exit(1)

    cache["last_url"] = new_url
    cache["update_count"] = cache.get("update_count", 0) + 1
    save_cache(cache, cache_sha)
    notify_discord(last_url, new_url, count, cache["update_count"])
    log.info("🎉 Terminé avec succès !")

if __name__ == "__main__":
    main()
