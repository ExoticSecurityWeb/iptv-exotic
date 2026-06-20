#!/usr/bin/env python3
"""
Exotic Channel Renamer — workflow réutilisable à vie 🧟‍♀️
- Renomme n'importe quelle chaîne de la playlist
- Peut aussi changer son URL si fournie
- Se lance manuellement avec des paramètres (inputs GitHub Actions)
- Ne se supprime JAMAIS — il sert encore et encore, Elena lui redonne vie chaque fois
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
log = logging.getLogger("renamer")

# ─── CONFIG ──────────────────────────────────────────────────────────────────
PLAYLIST_FILE = "exotic-tv-playlist.m3u"
REPO          = "ExoticSecurityWeb/iptv-exotic"
GITHUB_TOKEN  = os.environ.get("GITHUB_TOKEN", "")

# Paramètres passés via les inputs du workflow_dispatch
OLD_NAME = os.environ.get("OLD_NAME", "").strip()
NEW_NAME = os.environ.get("NEW_NAME", "").strip()
NEW_URL  = os.environ.get("NEW_URL", "").strip()  # optionnel

REQUEST_TIMEOUT = 20
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "ExoticTV-Renamer/1.0"})


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
    all_names = []

    while i < len(lines):
        line = lines[i]
        if line.startswith("#EXTINF"):
            m = re.search(r",(.+)$", line)
            if m:
                current_name = m.group(1).strip()
                all_names.append(current_name)
                if current_name.lower() == OLD_NAME.lower():
                    new_line = re.sub(r",[^,]+$", f",{NEW_NAME}", line)
                    new_line = re.sub(r'tvg-name="[^"]*"', f'tvg-name="{NEW_NAME}"', new_line)
                    new_lines.append(new_line)
                    log.info(f"✏️  Renommage : «{OLD_NAME}» → «{NEW_NAME}»")
                    i += 1
                    while i < len(lines) and lines[i].startswith("#"):
                        new_lines.append(lines[i])
                        i += 1
                    if i < len(lines) and not lines[i].startswith("#"):
                        old_url = lines[i]
                        if NEW_URL:
                            log.info(f"   ancien URL : {old_url[:90]}")
                            log.info(f"   nouvel URL : {NEW_URL[:90]}")
                            new_lines.append(NEW_URL)
                        else:
                            log.info(f"   URL conservée : {old_url[:90]}")
                            new_lines.append(old_url)
                        i += 1
                    found = True
                    continue
        new_lines.append(line)
        i += 1

    if not found:
        log.error(f"❌ Aucune chaîne nommée «{OLD_NAME}» trouvée dans la playlist !")
        similar = [n for n in all_names if OLD_NAME.lower() in n.lower() or n.lower() in OLD_NAME.lower()]
        if similar:
            log.info(f"   Noms similaires trouvés : {similar}")
        return False

    new_content = "\n".join(new_lines)
    if trailing_newline:
        new_content += "\n"

    github_put(PLAYLIST_FILE, new_content, sha, f"✏️ Renommage «{OLD_NAME}» → «{NEW_NAME}» — {_now()}")
    log.info("✅ Playlist mise à jour avec succès !")
    return True


def main() -> None:
    log.info("─" * 55)
    log.info(f"🧟‍♀️ Exotic Channel Renamer — {_now()}")
    log.info("    (workflow ressuscité par Elena, encore et encore)")
    log.info("─" * 55)

    if not GITHUB_TOKEN:
        log.error("❌ GITHUB_TOKEN manquant")
        sys.exit(1)

    if not OLD_NAME or not NEW_NAME:
        log.error("❌ OLD_NAME et NEW_NAME sont obligatoires")
        log.error(f"   OLD_NAME reçu : «{OLD_NAME}»")
        log.error(f"   NEW_NAME reçu : «{NEW_NAME}»")
        sys.exit(1)

    log.info(f"🎯 Ancien nom : «{OLD_NAME}»")
    log.info(f"🎯 Nouveau nom : «{NEW_NAME}»")
    if NEW_URL:
        log.info(f"🎯 Nouvelle URL : {NEW_URL[:90]}")
    else:
        log.info("🎯 URL : inchangée")

    success = rename_channel()

    if success:
        log.info("🎉 Mission accomplie ! Ce workflow attend sa prochaine résurrection… 🧟‍♀️")
    else:
        log.error("❌ Renommage échoué")
        sys.exit(1)


if __name__ == "__main__":
    main()
