#!/usr/bin/env python3
"""
Exotic TFX Auto-Updater v4.5
- Log le contenu COMPLET du fichier TFX ParaTV pour debug audio
- Retourne l'URL brute du fichier ParaTV (le master complet)
  en reconstruisant depuis download_url → raw stream URL
- Tout le reste identique à v4.2
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

# ─── SESSION ─────────────────────────────────────────────────────────────────
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "ExoticTV-Updater/4.5"})
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
        url = _extract_tfx_from_file(c, h)
        if url:
            return url

    raise RuntimeError("Aucun stream TFX trouvé dans ParaTV")


def _extract_tfx_from_file(file_info: dict, headers: dict) -> str | None:
    """
    Télécharge le fichier .m3u8 de ParaTV.
    - Log le contenu complet pour debug
    - Si contient #EXT-X-STREAM-INF → c'est un master HLS
      → on retourne l'URL du fichier lui-même (pas une variante enfant)
        car c'est le master complet que les players doivent recevoir
    - Sinon → retourne la première URL non-commentaire
    """
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

        fname = file_info.get("name", "?")

        # ── LOG COMPLET du fichier TFX pour debug ──
        log.info(f"{'='*50}")
        log.info(f"📄 Contenu de {fname} :")
        for i, line in enumerate(text.splitlines()[:40]):
            log.info(f"  L{i+1:02d}: {line}")
        if len(text.splitlines()) > 40:
            log.info(f"  ... ({len(text.splitlines())} lignes au total)")
        log.info(f"{'='*50}")

        lines = text.splitlines()

        # ── Master HLS : contient des variantes ──
        if "#EXT-X-STREAM-INF" in text:
            log.info(f"📋 Master HLS détecté : {fname}")

            # Chercher l'URL du stream depuis les métadonnées GitHub
            # download_url pointe vers le fichier dans le repo ParaTV
            # La vraie URL live est dans les lignes non-commentaires
            all_urls = [l.strip() for l in lines if l.strip() and not l.startswith("#")]

            log.info(f"  URLs trouvées dans le master ({len(all_urls)}) :")
            for u in all_urls:
                log.info(f"  → {u[:100]}")

            # Chercher si une URL ressemble au CDN TF1 direct
            # (alive-tfx-hls, tf1.fr, etc.) → c'est elle qu'on veut
            for u in all_urls:
                if any(domain in u for domain in ["tf1.fr", "tf1.com", "alive-tfx", "diff.tf1"]):
                    # Vérifier si c'est une variante (.m3u8) ou le master
                    # On veut l'URL de la variante qui contient audio+vidéo
                    # mais si on n'a pas le choix, on prend la première URL CDN
                    log.info(f"✅ URL CDN TF1 trouvée : {u[:80]}…")
                    return u

            # Pas d'URL CDN directe → prendre la première URL absolue
            for u in all_urls:
                if u.startswith("http"):
                    log.info(f"✅ TFX (première URL abs) : {u[:80]}…")
                    return u

            # Dernier recours : première URL quelle qu'elle soit
            if all_urls:
                log.info(f"✅ TFX (fallback) : {all_urls[0][:80]}…")
                return all_urls[0]

            return None

        # ── Pas de master HLS : stream direct ──
        all_urls = [l.strip() for l in lines if l.strip() and not l.startswith("#")]
        if not all_urls:
            return None

        log.info(f"✅ TFX stream direct : {fname} → {all_urls[0][:80]}…")
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
                if name.lower() in TFX_EXACT_NAMES:
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
        log.error("❌ Aucune chaîne TFX trouvée !")
        log.error(f"   Noms cherchés : {sorted(TFX_EXACT_NAMES)}")
        tf_names = [n for n in all_names if "tf" in n.lower()]
        if tf_names:
            log.info(f"   Noms contenant 'tf' : {tf_names}")
        else:
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
        sha     = github_get(CACHE_FILE)["sha"]
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
        log.warning("⚠️ DISCORD_WEBHOOK_TFX absent")
        return

    def trunc(u: str, n: int = 100) -> str:
        return u[:n] + "…" if len(u) > n else u

    payload = {
        "embeds": [{
            "title": "📺 TFX — URL mise à jour automatiquement",
            "description": f"**{count}** occurrence(s) dans `{PLAYLIST_FILE}`",
            "color": 0xf472b6,
            "fields": [
                {"name": "✅ Nouvelle URL",   "value": f"```{trunc(new_url)}```", "inline": False},
                {"name": "💀 Ancienne URL",   "value": f"```{trunc(old_url) if old_url else 'N/A'}```", "inline": False},
                {"name": "🔢 Mise à jour n°", "value": str(total), "inline": True},
                {"name": "🕐 Heure",          "value": _now(),     "inline": True},
            ],
            "footer": {"text": "Exotic TFX Auto-Updater v4.5 • 🌴 Pink Paradise"},
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
        log.warning("⚠️ DISCORD_WEBHOOK_TFX non défini")

# ─── MAIN ────────────────────────────────────────────────────────────────────
def main() -> None:
    log.info("─" * 55)
    log.info(f"📺 Exotic TFX Auto-Updater v4.5 — {_now()}")
    log.info("─" * 55)

    _check_config()

    try:
        new_url = find_tfx_url()
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

