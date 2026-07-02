#!/usr/bin/env python3
"""Upload an Obsidian vault (all .md files) to the cmn-ai vault service on the Pi.

Usage:
    python3 scripts/upload-vault.py <vault-path> <vault-tunnel-url>

Example:
    python3 scripts/upload-vault.py ~/Documents/MeinVault \
        https://xxx-yyy-zzz.trycloudflare.com
"""

import json
import sys
import urllib.request
from pathlib import Path

BATCH = 50  # files per request (keep payloads manageable)


def collect_notes(vault: Path) -> list[dict[str, str]]:
    skip = {".git", ".obsidian", ".trash", "node_modules"}
    notes = []
    for p in vault.rglob("*.md"):
        if any(part in skip for part in p.parts):
            continue
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        notes.append({"path": str(p.relative_to(vault)), "content": content})
    return notes


def upload_batch(url: str, notes: list[dict[str, str]]) -> dict:  # type: ignore[type-arg]
    payload = json.dumps({"notes": notes}).encode()
    req = urllib.request.Request(
        f"{url.rstrip('/')}/upload",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())  # type: ignore[no-any-return]


def main() -> None:
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)

    vault_path = Path(sys.argv[1]).expanduser().resolve()
    tunnel_url = sys.argv[2].rstrip("/")

    if not vault_path.is_dir():
        print(f"Fehler: '{vault_path}' ist kein Ordner.")
        sys.exit(1)

    print(f"Scanne Vault: {vault_path}")
    notes = collect_notes(vault_path)
    print(f"Gefunden: {len(notes)} Markdown-Dateien")

    saved = errors = 0
    for i in range(0, len(notes), BATCH):
        batch = notes[i : i + BATCH]
        end = min(i + BATCH, len(notes))
        print(f"  Lade hoch {i + 1}-{end} von {len(notes)}…", end=" ", flush=True)
        try:
            result = upload_batch(tunnel_url, batch)
            saved += result.get("saved", 0)
            errors += len(result.get("errors", []))
            print(f"ok ({result.get('saved', 0)} gespeichert)")
        except Exception as exc:
            print(f"Fehler: {exc}")
            errors += len(batch)

    print(f"\nFertig: {saved} gespeichert, {errors} Fehler")
    if errors:
        print(
            "Tipp: Prüfe ob der Vault-Tunnel noch läuft (cloudflared tunnel --url http://localhost:11435)"
        )


if __name__ == "__main__":
    main()
