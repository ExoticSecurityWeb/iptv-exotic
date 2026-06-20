#!/usr/bin/env python3
"""
Exotic CStar Auto-Updater v3.0
- Lit streams/canalplus/cstar-dm.m3u8 sur ParaTV (chemin fixe)
- Parse les variantes #EXT-X-STREAM-INF (chacune a déjà audio+vidéo: CODECS="avc1...,mp4a...")
- Choisit la meilleure résolution disponible (1080 > 720 > 480 > 380 > 240)
- Met à jour exotic-tv-playlist.m3u si l'URL a changé
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
PARATV_REPO           = "Paradise-91/ParaTV"
PARATV_FILE_PATH      = "streams/canalplus/cstar-dm.m3u8"
PLAYLIST_FILE         = "exotic-tv-playlist.m3u"
CACHE_FILE            = "cstar_cache.json"
REPO                  = "ExoticSecurityWeb/iptv-exotic"
GITHUB_TOKEN          = os.environ.get("GITHUB_TOKEN", "")
DISCORD_WEBHOOK_CSTAR = os.environ.get("DISCORD_WEBHOOK_CSTAR", "")

CSTAR_EXACT_NAMES = {
    "cstar",
    "cstar (720p)",
    "cstar (720p) [geo-blocked]",
    "cstar hd",
    "cstar (1080p)",
    "c star (720p) [geo-blocked]",
    "c star",
}

MAX_RETRIES     = 3
RETRY_BACKOFF   = 2
REQUEST_TIMEOUT = 20

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "ExoticTV-Updater/3.0"})
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

# ─── TROUVER L'URL CSTAR ─────────────────────────────────────────────────────
def find_cstar_url() -> str:
    """
    Fetch streams/canalplus/cstar-dm.m3u8 et parse les variantes
    #EXT-X-STREAM-INF pour choisir la meilleure résolution.
    Chaque variante a déjà CODECS="avc1...,mp4a..." → audio+vidéo combinés,
    donc on prend juste l'URL de la meilleure résolution, point.
    """
    raw_url = f"https://raw.githubusercontent.com/{PARATV_REPO}/main/{PARATV_FILE_PATH}"
    log.info(f"🔍 Fetch : {PARATV_FILE_PATH}")

    r = fetch(raw_url)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} sur {PARATV_FILE_PATH}")

    text  = r.text
    lines = text.splitlines()

    log.info(f"📄 Fichier reçu ({len(lines)} lignes) :")
    for l in lines:
        log.info(f"   {l[:120]}")

    variants = []  # (height, bandwidth, url)

    for i, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF"):
            res_match = re.search(r'RESOLUTION=(\d+)x(\d+)', line)
            bw_match  = re.search(r'BANDWIDTH=(\d+)', line)
            height = int(res_match.group(2)) if res_match else 0
            bw     = int(bw_match.group(1)) if bw_match else 0

            # Chercher l'URL sur la/les lignes suivantes (sauter les # éventuels)
            j = i + 1
            while j < len(lines) and lines[j].startswith("#"):
                j += 1
            if j < len(lines) and lines[j].strip():
                url = lines[j].strip()
                variants.append((height, bw, url))
                log.info(f"   ↳ variante détectée : {height}p, {bw}bps")

    if not variants:
        log.error("❌ Aucune variante #EXT-X-STREAM-INF trouvée dans le fichier")
        raise RuntimeError("Pas de variante HLS trouvée — structure de fichier inattendue")

    # Trier par résolution puis bandwidth, décroissant → meilleure qualité en premier
    variants.sort(key=lambda v: (v[0], v[1]), reverse=True)

    log.info(f"📊 {len(variants)} variante(s) trouvée(s), triées : {[f'{h}p' for h,_,_ in variants]}")

    best_height, best_bw, best_url = variants[0]
    log.info(f"✅ Meilleure qualité retenue : {best_height}p ({best_bw} bps)")
    log.info(f"   URL : {best_url}")

    return best_url

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
                if name.lower() in CSTAR_EXACT_NAMES:
                    new_lines.append(line)
                    i += 1
                    while i < len(lines) and lines[i].startswith("#"):
                        new_lines.append(lines[i])
                        i += 1
                    if i < len(lines) and not lines[i].startswith("#"):
                        old_url = lines[i]
                        log.info(f"  ↳ «{name}»")
                        log.info(f"    ancien : {old_url[:100]}")
                        log.info(f"    nouveau: {new_url[:100]}")
                        i += 1
                        count += 1
                    new_lines.append(new_url)
                    continue
        new_lines.append(line)
        i += 1

    if count == 0:
        log.error("❌ Aucune chaîne CStar trouvée dans ta playlist !")
        log.error(f"   Noms cherchés : {sorted(CSTAR_EXACT_NAMES)}")
        cstar_names = [n for n in all_names if "star" in n.lower()]
        if cstar_names:
            log.info(f"   Noms contenant 'star' dans ta playlist : {cstar_names}")
        else:
            log.info("   Aucune chaîne contenant 'star' trouvée du tout.")
        return 0

    new_content = "\n".join(new_lines)
    if trailing_newline:
        new_content += "\n"

    github_put(
        PLAYLIST_FILE, new_content, sha,
        f"📺 CStar auto-updated ({count} occurrence(s)) — {_now()}"
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
        github_put(CACHE_FILE, json.dumps(cache, indent=2, ensure_ascii=False), sha, f"🔄 Cache CStar — {_now()}")
        log.info("💾 Cache sauvegardé")
    except Exception as exc:
        log.warning(f"Cache non sauvegardé : {exc}")

# ─── DISCORD ─────────────────────────────────────────────────────────────────
def notify_discord(old_url: str, new_url: str, count: int, total: int) -> None:
    if not DISCORD_WEBHOOK_CSTAR:
        log.warning("⚠️ DISCORD_WEBHOOK_CSTAR absent")
        return

    def trunc(u, n=100):
        return u[:n] + "…" if len(u) > n else u

    payload = {"embeds": [{
        "title": "📺 CStar — URL mise à jour automatiquement",
        "description": f"**{count}** occurrence(s) dans `{PLAYLIST_FILE}`",
        "color": 0x22d3ee,
        "fields": [
            {"name": "✅ Nouvelle URL", "value": f"```{trunc(new_url)}```", "inline": False},
            {"name": "💀 Ancienne URL", "value": f"```{trunc(old_url) if old_url else 'N/A'}```", "inline": False},
            {"name": "🔢 Mise à jour n°", "value": str(total), "inline": True},
            {"name": "🕐 Heure", "value": _now(), "inline": True},
        ],
        "footer": {"text": "Exotic CStar Auto-Updater v3.0 • 🌴 Pink Paradise"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }]}

    try:
        r = SESSION.post(DISCORD_WEBHOOK_CSTAR, json=payload, timeout=10)
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
    if not DISCORD_WEBHOOK_CSTAR:
        log.warning("⚠️ DISCORD_WEBHOOK_CSTAR non défini")

# ─── MAIN ────────────────────────────────────────────────────────────────────
def main() -> None:
    log.info("─" * 55)
    log.info(f"📺 Exotic CStar Auto-Updater v3.0 — {_now()}")
    log.info("─" * 55)

    _check_config()

    try:
        new_url = find_cstar_url()
        log.info(f"🎯 URL finale retenue : {new_url[:90]}…")
    except Exception as exc:
        log.error(f"❌ Recherche CStar échouée : {exc}")
        sys.exit(1)

    cache, cache_sha = load_cache()
    last_url = cache.get("last_url", "")

    if new_url == last_url:
        log.info("📌 URL identique — aucune mise à jour nécessaire")
        return

    log.info("🔄 Nouvelle URL détectée — mise à jour…")

    try:
        count = update_playlist(new_url)
    except Exception as exc:
        log.error(f"❌ Mise à jour échouée : {exc}")
        sys.exit(1)

    if count == 0:
        log.error("❌ Aucun remplacement effectué — arrêt")
        sys.exit(1)

    cache["last_url"] = new_url
    cache["update_count"] = cache.get("update_count", 0) + 1
    save_cache(cache, cache_sha)
    notify_discord(last_url, new_url, count, cache["update_count"])
    log.info("🎉 Terminé avec succès !")

if __name__ == "__main__":
    main()
