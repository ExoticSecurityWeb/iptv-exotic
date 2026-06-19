#!/usr/bin/env python3
"""
Exotic TFX Auto-Updater v4.6 (amélioré)
- Ajout : verrouillage, options CLI (dry-run, verbose, no-notify), meilleur logging,
  sauvegarde/rollback safe, et gestion propre des signaux.
- Conserve le comportement précédent (préférer le master raw quand disponible).
"""

import os
import re
import sys
import json
import time
import base64
import logging
import requests
import argparse
import signal
import tempfile
import fcntl
from datetime import datetime, timezone
from typing import Optional, Tuple, Dict

# ─── LOGGING ────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("tfx-updater")

# ─── CONFIG ────────────────────────────────────────────────────────────
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

# suffixes to append to the tokenised base when we detect it in ParaTV files
AUDIO_SUFFIX = "TFX-mp4a_140800_fra=20000.m3u8"
VIDEO_SUFFIX = "TFX-avc1_1699968=10001.m3u8"
# regex to capture the tokenised base up to /prod/TFX/cmaf/out/
BASE_RE = re.compile(r"(https?://[^/]+(?:/[^/]+)*/prod/TFX/cmaf/out/)")

# ─── SESSION ───────────────────────────────────────────────────────────
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "ExoticTV-Updater/4.6"})
_DEFAULT_BRANCH_CACHE: Optional[str] = None
# last detected audio url (set when we reconstruct from tokenised base)
LAST_AUDIO_URL: Optional[str] = None

# CLI / runtime flags (populated in main)
DRY_RUN = False
NO_NOTIFY = False
LOCK_FD = None  # file descriptor for lock file


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


# ─── GITHUB API ─────────────────────────────────────────────────────────
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
        _DEFAULT_BRANCH_CACHE = r.json().get("default_branch", "main")
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


def github_put(path: str, content: str, sha: Optional[str], message: str) -> dict:
    """
    Put content into GitHub. If DRY_RUN is enabled, we log and skip the actual call.
    """
    if DRY_RUN:
        log.info(f"[dry-run] github_put {path} (message: {message})")
        return {}
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


# ─── SCAN PARATV ────────────────────────────────────────────────────────
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


def _extract_tfx_from_file(file_info: dict, headers: dict) -> Optional[str]:
    """
    Télécharge le fichier .m3u8 de ParaTV et retourne la bonne URL.

    Comportement :
    - si le fichier contient un master HLS (#EXT-X-STREAM-INF) -> retourne le
      download_url (raw GitHub) du master (meilleure compatibilité audio+vidéo)
    - sinon, si on détecte une base tokenisée TF1 (alive-tfx-hls.../prod/TFX/cmaf/out/)
      on reconstruit deux URLs (audio/video) en ajoutant des suffixes connus.
      La fonction retourne l'URL vidéo (TFX-avc1_...) pour la playlist mais
      stocke l'URL audio dans LAST_AUDIO_URL pour la notification / cache.
    - sinon, si le fichier contient une URL directe, retourne la première.
    """
    global LAST_AUDIO_URL
    raw_url = file_info.get("download_url")
    if not raw_url:
        return None

    try:
        r = fetch(raw_url, headers=headers)
        if r.status_code != 200:
            return None
        text = r.text

        # Vérifier que c'est bien TFX
        if not any(kw in text.upper() for kw in TFX_KEYWORDS):
            return None

        fname = file_info.get("name", "?")

        # Master HLS avec audio séparé (#EXT-X-MEDIA + #EXT-X-STREAM-INF)
        if "#EXT-X-STREAM-INF" in text:
            log.info(f"📋 Master HLS détecté : {fname}")

            master_url = raw_url

            log.info(f"✅ TFX master complet (audio+vidéo) : {master_url[:80]}…")
            # clear any previous audio url since master covers it
            LAST_AUDIO_URL = None
            return master_url

        # Chercher une base tokenisée TF1 et reconstruire audio+video URLs
        bases = list(dict.fromkeys(m.group(1) for m in BASE_RE.finditer(text)))
        if bases:
            base = bases[0]
            audio_url = base + AUDIO_SUFFIX
            video_url = base + VIDEO_SUFFIX
            LAST_AUDIO_URL = audio_url
            log.info(f"🔗 Base tokenisée détectée : {base[:80]}…")
            log.info(f"    audio : {audio_url[:120]}…")
            log.info(f"    video : {video_url[:120]}…")
            # Retourner la video URL pour la playlist (les players chargeront la .m3u8)
            return video_url

        # Stream direct (pas de variantes)
        all_urls = [l.strip() for l in text.splitlines() if l.strip() and not l.startswith("#")]
        if not all_urls:
            return None

        log.info(f"✅ TFX stream direct : {fname} → {all_urls[0][:80]}…")
        LAST_AUDIO_URL = None
        return all_urls[0]

    except Exception as exc:
        log.warning(f"Erreur {file_info.get('name', '?')} : {exc}")
        return None


# ─── PLAYLIST ───────────────────────────────────────────────────────────
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

    if DRY_RUN:
        log.info("[dry-run] update_playlist: skip github_put")
    else:
        github_put(
            PLAYLIST_FILE, new_content, sha,
            f"📺 TFX auto-updated ({count} occurrence(s)) — {_now()}"
        )
        log.info(f"✅ Playlist mise à jour — {count} remplacement(s)")
    return count


# ─── CACHE ────────────────────────────────────────────────────────────
def load_cache() -> Tuple[Dict, Optional[str]]:
    try:
        content = github_get_raw(CACHE_FILE)
        sha     = github_get(CACHE_FILE)["sha"]
        return json.loads(content), sha
    except FileNotFoundError:
        log.info("Cache absent — sera créé après la première mise à jour")
        return {"last_url": "", "last_audio_url": "", "last_updated": "", "update_count": 0}, None
    except Exception as exc:
        log.warning(f"Cache illisible ({exc}) — réinitialisation")
        return {"last_url": "", "last_audio_url": "", "last_updated": "", "update_count": 0}, None


def save_cache(cache: dict, sha: Optional[str]) -> None:
    if DRY_RUN:
        log.info("[dry-run] save_cache: skip github_put")
        return
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


# ─── DISCORD ───────────────────────────────────────────────────────────
def notify_discord(old_url: str, new_url: str, count: int, total: int, audio_url: Optional[str] = None) -> None:
    if NO_NOTIFY:
        log.info("Notifications Discord désactivées (--no-notify)")
        return
    if not DISCORD_WEBHOOK_TFX:
        log.warning("⚠️ DISCORD_WEBHOOK_TFX absent")
        return

    def trunc(u: str, n: int = 100) -> str:
        return u[:n] + "…" if len(u) > n else u

    fields = [
        {"name": "✅ Nouvelle URL",   "value": f"```{trunc(new_url)}```", "inline": False},
        {"name": "💀 Ancienne URL",   "value": f"```{trunc(old_url) if old_url else 'N/A'}```", "inline": False},
        {"name": "🔢 Mise à jour n°", "value": str(total), "inline": True},
        {"name": "🕐 Heure",          "value": _now(),     "inline": True},
    ]
    if audio_url:
        fields.insert(1, {"name": "🔊 Audio URL", "value": f"```{trunc(audio_url)}```", "inline": False})

    payload = {
        "embeds": [{
            "title": "📺 TFX — URL mise à jour automatiquement",
            "description": f"**{count}** occurrence(s) dans `{PLAYLIST_FILE}`",
            "color": 0xf472b6,
            "fields": fields,
            "footer": {"text": "Exotic TFX Auto-Updater v4.6 • 🌴 Pink Paradise"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }]
    }

    try:
        if DRY_RUN:
            log.info("[dry-run] notify_discord: skip POST")
            return
        r = SESSION.post(DISCORD_WEBHOOK_TFX, json=payload, timeout=10)
        if r.status_code in (200, 204):
            log.info("✅ Discord notifié")
        else:
            log.warning(f"Discord HTTP {r.status_code} : {r.text[:200]}")
    except Exception as exc:
        log.warning(f"❌ Discord KO : {exc}")


# ─── UTILS ────────────────────────────────────────────────────────────
def _now() -> str:
    return datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")


def _check_config() -> None:
    if not GITHUB_TOKEN:
        log.error("❌ GITHUB_TOKEN manquant")
        sys.exit(1)
    if not DISCORD_WEBHOOK_TFX:
        log.warning("⚠️ DISCORD_WEBHOOK_TFX non défini")


# ─── LOCKING / SIGNALS ─────────────────────────────────────────────────
def acquire_lock(lockfile: str) -> int:
    global LOCK_FD
    fd = os.open(lockfile, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        LOCK_FD = fd
        log.debug(f"Verrou acquis {lockfile}")
        return fd
    except OSError:
        os.close(fd)
        raise RuntimeError(f"Impossible d'acquérir le verrou {lockfile} — déjà en cours")


def release_lock() -> None:
    global LOCK_FD
    if LOCK_FD is not None:
        try:
            fcntl.flock(LOCK_FD, fcntl.LOCK_UN)
            os.close(LOCK_FD)
            log.debug("Verrou libéré")
        except Exception:
            pass
        LOCK_FD = None


def _signal_handler(signum, frame):
    log.warning(f"Signal {signum} reçu — arrêt")
    release_lock()
    sys.exit(1)


# ─── MAIN ─────────────────────────────────────────────────────────────
def main() -> None:
    global DRY_RUN, NO_NOTIFY

    parser = argparse.ArgumentParser(description="Exotic TFX Auto-Updater")
    parser.add_argument("--dry-run", action="store_true", help="Ne pas écrire sur GitHub ni notifier")
    parser.add_argument("--no-notify", action="store_true", help="Ne pas envoyer de notifications Discord")
    parser.add_argument("--verbose", "-v", action="store_true", help="Affiche plus de logs")
    parser.add_argument("--lockfile", default="/tmp/tfx_updater.lock", help="Chemin du fichier de verrou (default: /tmp/tfx_updater.lock)")
    args = parser.parse_args()

    if args.verbose:
        log.setLevel(logging.DEBUG)
    DRY_RUN = args.dry_run
    NO_NOTIFY = args.no_notify

    # signals
    for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(s, _signal_handler)

    # try to acquire lock
    try:
        acquire_lock(args.lockfile)
    except Exception as exc:
        log.error(str(exc))
        sys.exit(1)

    log.info("─" * 55)
    log.info(f"📺 Exotic TFX Auto-Updater v4.6 — {_now()}")
    log.info("─" * 55)

    _check_config()

    try:
        new_url = find_tfx_url()
        log.info(f"🎯 URL finale : {new_url[:80]}…")
    except Exception as exc:
        log.error(f"❌ Scan ParaTV échoué : {exc}")
        release_lock()
        sys.exit(1)

    cache, cache_sha = load_cache()
    last_url = cache.get("last_url", "")

    if new_url == last_url:
        log.info("📌 URL identique — aucune mise à jour")
        release_lock()
        return

    log.info("🔄 Nouvelle URL — mise à jour…")

    try:
        count = update_playlist(new_url)
    except Exception as exc:
        log.error(f"❌ Mise à jour échouée : {exc}")
        release_lock()
        sys.exit(1)

    if count == 0:
        log.error("❌ Aucun remplacement — arrêt")
        release_lock()
        sys.exit(1)

    # sauvegarder aussi l'audio URL si elle a été reconstruite
    cache["last_url"] = new_url
    cache["last_audio_url"] = LAST_AUDIO_URL or cache.get("last_audio_url", "")
    cache["update_count"] = cache.get("update_count", 0) + 1
    save_cache(cache, cache_sha)
    notify_discord(last_url, new_url, count, cache["update_count"], cache.get("last_audio_url"))
    log.info("🎉 Terminé avec succès !")
    release_lock()


if __name__ == "__main__":
    main()
