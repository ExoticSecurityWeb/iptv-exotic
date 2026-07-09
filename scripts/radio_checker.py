#!/usr/bin/env python3
"""
Exotic Radio Stream Checker
Vérifie l'état des flux dans exotic-radio-playlist.m3u et notifie sur Discord
en cas de flux mort (#exotic-radio_notify).

Secret requis (GitHub Actions) : DISCORD_RADIO_WEBHOOK
"""

import os
import re
import sys
import requests

PLAYLIST_PATH = "tv/exotic-radio-playlist.m3u"
DISCORD_WEBHOOK = os.environ.get("DISCORD_RADIO_WEBHOOK")
TIMEOUT = 10  # secondes


def parse_m3u(path):
    """Extrait les couples (nom_chaine, url) depuis le M3U."""
    entries = []
    with open(path, "r", encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip()]

    current_name = None
    for line in lines:
        if line.startswith("#EXTINF"):
            match = re.search(r",(.+)$", line)
            current_name = match.group(1).strip() if match else "Inconnu"
        elif not line.startswith("#") and current_name:
            entries.append((current_name, line))
            current_name = None

    return entries


def check_stream(url):
    """Retourne (ok: bool, detail: str)."""
    try:
        # HEAD d'abord (plus léger), fallback GET stream si HEAD pas supporté
        r = requests.head(url, timeout=TIMEOUT, allow_redirects=True)
        if r.status_code >= 400:
            r = requests.get(url, timeout=TIMEOUT, stream=True)
        r.close()
        if r.status_code < 400:
            return True, f"HTTP {r.status_code}"
        return False, f"HTTP {r.status_code}"
    except requests.exceptions.RequestException as e:
        return False, str(e)


def clean_error(detail):
    """Simplifie les erreurs Python brutes en message lisible."""
    if "ConnectTimeoutError" in detail or "timed out" in detail:
        return "Timeout"
    if "NameResolutionError" in detail or "Failed to resolve" in detail:
        return "DNS introuvable"
    if "ConnectionRefusedError" in detail or "Connection refused" in detail:
        return "Connexion refusée"
    if "HTTP" in detail:
        return detail
    return "Erreur de connexion"


def notify_discord(dead_streams):
    if not DISCORD_WEBHOOK:
        print("⚠️  DISCORD_RADIO_WEBHOOK non défini, notification skip.")
        return

    for name, url, detail in dead_streams:
        payload = {
            "username": "Exotic Radio Checker",
            "embeds": [
                {
                    "title": f"💀 {name} [Down]",
                    "color": 0xE74C3C,
                    "fields": [
                        {"name": "🔴 Erreur", "value": clean_error(detail), "inline": False},
                        {"name": "🔗 URL morte", "value": f"```{url}```", "inline": False},
                    ],
                    "footer": {"text": "Exotic Radio Checker • Pink Paradise 🌴"},
                }
            ],
        }
        resp = requests.post(DISCORD_WEBHOOK, json=payload, timeout=TIMEOUT)
        resp.raise_for_status()


def main():
    if not os.path.exists(PLAYLIST_PATH):
        print(f"❌ Playlist introuvable : {PLAYLIST_PATH}")
        sys.exit(1)

    entries = parse_m3u(PLAYLIST_PATH)
    print(f"🔍 {len(entries)} flux radio à vérifier...")

    dead = []
    for name, url in entries:
        ok, detail = check_stream(url)
        status = "✅" if ok else "❌"
        print(f"{status} {name} — {detail}")
        if not ok:
            dead.append((name, url, detail))

    if dead:
        print(f"\n⚠️  {len(dead)} flux morts détectés, notification Discord...")
        notify_discord(dead)
    else:
        print("\n✅ Tous les flux radio sont OK.")


if __name__ == "__main__":
    main()
