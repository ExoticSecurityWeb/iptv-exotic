#!/usr/bin/env python3
"""
Exotic France3 → Renommage ONE-SHOT
- Renomme la chaîne "France 3" en "France 3 Nouvelle Aquitaine" dans la playlist
- Met à jour son URL avec le flux ParaTV Aquitaine
- Se supprime lui-même du repo après exécution (script à usage unique)
"""

import os
import re
import sys
import base64
import logging
import requests
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("f3-rename")

# ─── CONFIG ──────────────────────────────────────────────────────────────────
PLAYLIST_FILE    = "exotic-tv-playlist.m3u"
REPO             = "ExoticSecurityWeb/iptv-exotic"
GITHUB_TOKEN     = os.environ.get("GITHUB_TOKEN", "")
SELF_PATH        = "scripts/france3_rename.py"  # chemin de CE fichier dans le repo

OLD_NAME         = "France 3"
NEW_NAME         = "France 3 Nouvelle Aquitaine"
PARATV_URL       = "https://raw.githubusercontent.com/Paradise-91/ParaTV/main/streams/francetv/france-3-aquitaine.m3u8"

REQUEST_TIMEOUT  = 20
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "ExoticTV-Rename/1.0"})


def gh_headers() -> dict:
    return {"Accept": "application/vnd.github.v3+json", "Authorization": f"token {GITHUB_TOKEN}"}


def get_default_branch() -> str:
    r = SESSION.get(f"https://api.github.com/repos/{REPO}", headers=gh_headers(), timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()["default_branch"]


def github_get(path: str) -> dict:
    r = SESSION.get(f"https://api.github.com/repos/{REPO}/contents/{path}", headers=gh_headers(), timeout=REQUEST_TIMEOUT)
    if r.status_code == 404:
        raise FileNotFoundError(f"Introuvable : {path}")
    r.raise_for_status()
    return r.json()


def github_get_raw(path: str) -> str:
    branch = get_default_branch()
    url = f"https://raw.githubusercontent.com/{REPO}/{branch}/{path}"
    r = SESSION.get(url, timeout=REQUEST_TIMEOUT)
    if r.ok:
        r.encoding = "utf-8"
        return r.text
    data = github_get(path)
    return base64.b64decode(data["content"].replace("\n", "")).decode("utf-8", errors="replace")


def github_put(path: str, content: str, sha, message: str) -> None:
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
    r.raise_for_status()


def github_delete(path: str, sha: str, message: str) -> None:
    r = SESSION.delete(
        f"https://api.github.com/repos/{REPO}/contents/{path}",
        headers=gh_headers(),
        json={"message": message, "sha": sha},
        timeout=REQUEST_TIMEOUT,
    )
    r.raise_for_status()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")


def rename_channel() -> bool:
    log.info(f"📝 Lecture de {PLAYLIST_FILE}…")
    content = github_get_raw(PLAYLIST_FILE)
    sha     = github_get(PLAYLIST_FILE)["sha"]

    lines     = content.splitlines()
    new_lines = []
    i         = 0
    found     = False
    trailing_newline = content.endswith("\n")

    while i < len(lines):
        line = lines[i]
        if line.startswith("#EXTINF"):
            m = re.search(r",(.+)$", line)
            if m and m.group(1).strip().lower() == OLD_NAME.lower():
                # Renommer dans la ligne #EXTINF (remplace le nom après la dernière virgule
                # et aussi dans tvg-name="..." si présent)
                new_line = re.sub(r",[^,]+$", f",{NEW_NAME}", line)
                new_line = re.sub(r'tvg-name="[^"]*"', f'tvg-name="{NEW_NAME}"', new_line)
                new_lines.append(new_line)
                log.info(f"✏️  Renommage : «{OLD_NAME}» → «{NEW_NAME}»")
                i += 1
                # Sauter les éventuelles lignes #EXTVLCOPT
                while i < len(lines) and lines[i].startswith("#"):
                    new_lines.append(lines[i])
                    i += 1
                # Remplacer l'URL
                if i < len(lines) and not lines[i].startswith("#"):
                    old_url = lines[i]
                    log.info(f"   ancien URL : {old_url[:90]}")
                    log.info(f"   nouvel URL : {PARATV_URL[:90]}")
                    i += 1
                new_lines.append(PARATV_URL)
                found = True
                continue
        new_lines.append(line)
        i += 1

    if not found:
        log.error(f"❌ Aucune chaîne nommée «{OLD_NAME}» trouvée dans la playlist !")
        return False

    new_content = "\n".join(new_lines)
    if trailing_newline:
        new_content += "\n"

    github_put(PLAYLIST_FILE, new_content, sha, f"✏️ Renommage France 3 → {NEW_NAME} — {_now()}")
    log.info("✅ Playlist mise à jour avec succès !")
    return True


def self_delete() -> None:
    """Supprime ce script lui-même du repo après exécution réussie."""
    log.info(f"🗑️  Auto-suppression de {SELF_PATH}…")
    try:
        data = github_get(SELF_PATH)
        github_delete(SELF_PATH, data["sha"], f"🗑️ Suppression auto du script one-shot — {_now()}")
        log.info("✅ Script supprimé du repo")
    except Exception as exc:
        log.warning(f"⚠️ Impossible de s'auto-supprimer : {exc}")
        log.warning("   Tu peux le supprimer manuellement depuis GitHub")


def main() -> None:
    log.info("─" * 55)
    log.info(f"✏️  Exotic France3 Rename (one-shot) — {_now()}")
    log.info("─" * 55)

    if not GITHUB_TOKEN:
        log.error("❌ GITHUB_TOKEN manquant")
        sys.exit(1)

    success = rename_channel()

    if success:
        self_delete()
        log.info("🎉 Terminé ! Ce script ne se relancera plus (supprimé).")
    else:
        log.error("❌ Renommage échoué — le script reste en place pour debug")
        sys.exit(1)


if __name__ == "__main__":
    main()
