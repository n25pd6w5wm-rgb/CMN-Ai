# Deployment — GitHub + Render + Supabase + Raspberry Pi

So bringst du cmn-ai live. Die Architektur ist **verteilt**:

```
 Browser ──► Render (FastAPI-App: UI, Routing, Login)
                │
                ├──► Supabase   — Accounts/Login + API-Keys
                └──► Raspberry Pi (Ollama: gratis lokales Gemma)  ← via OLLAMA_HOST
```

- **Render** hostet die App (Chat-UI + Orchestrator + Login).
- **Supabase** ist das Account-Backend (Login/Signup) und speichert die Modell-API-Keys.
- **Raspberry Pi** fährt das gratis lokale Modell (Ollama/Gemma); Render erreicht es über `OLLAMA_HOST`.
- **GitHub** hält den Code; Render deployt von dort.

---

## 1. Code auf GitHub (machst du)

Das lokale Repo ist fertig (Commits, keine Secrets — `.env` ist gitignored). **Lege das
Repo selbst an** (so wählst du Sichtbarkeit/Org):

1. Auf github.com → **New repository** → Name z. B. `cmn-ai` → **Private** → *ohne* README/gitignore (das Repo hat schon welche) → **Create**.
2. Im Terminal (im Projektordner):

```bash
git remote add origin https://github.com/<DEIN-USER>/cmn-ai.git
git push -u origin build/greenfield-mvp        # oder vorher: git branch -M main
```

> Ich (Claude) lege das Repo bewusst **nicht** automatisch an — das Hochladen der ganzen
> Codebase ist eine Aktion, die ein Mensch freigeben soll.

---

## 2. Raspberry Pi erreichbar machen — OHNE Port-Freigabe (machst du)

**Wichtig:** Du musst **keinen Port am Router öffnen** und keine Portweiterleitung
einrichten. Ein **Tunnel** (Cloudflare oder Tailscale) baut eine **ausgehende** Verbindung
vom Pi auf — der Pi „ruft raus", Render erreicht ihn über die Tunnel-URL. Keine offenen
Ports, keine Firewall-Regeln, deutlich sicherer als Port-Forwarding.

Einfach das Setup-Skript ausführen — es installiert Ollama, stellt es lokal bereit und
zeigt am Ende die Tunnel-Befehle:

```bash
./scripts/install-pi.sh          # oder: CMN_AI_MODEL=gemma3:4b ./scripts/install-pi.sh
```

Dann einen Tunnel starten (alle **ohne** Port-Freigabe):

- **Schnell (zum Testen, kein Account):** `cloudflared tunnel --url http://localhost:11434`
  → gibt sofort eine temporäre `https://…trycloudflare.com`-URL.
- **Stabil (Cloudflare-Account + Domain):** benannter Tunnel als Dienst (`cloudflared
  service install`) → feste URL, läuft nach Reboot weiter. Schritte stehen in der
  Skript-Ausgabe.
- **Alternative:** `tailscale funnel 11434`.

Die ausgegebene `https`-URL trägst du als `OLLAMA_HOST` in Render ein.

> Ehrliche Einschränkung: Render → Pi geht übers Heimnetz. Latenz und Verfügbarkeit
> hängen an der Pi-/Internet-Verbindung zu Hause (davon unabhängig vom Tunnel-Verfahren).

---

## 3. Supabase: Auth + Keys (machst du)

1. **Auth aktivieren:** Supabase-Projekt → Authentication → Email aktivieren. Für sofortiges
   Login ohne Bestätigung: „Confirm email" aus (oder an, dann muss man die Mail bestätigen).
2. **Keys:** Du brauchst aus Project Settings → API:
   - `Project URL` → `SUPABASE_URL`
   - `anon`/publishable key → `SUPABASE_ANON_KEY` (öffentlich, für die Login-Seite)
   - `service_role` secret key → `SUPABASE_KEY` (lädt die Modell-API-Keys beim Start)
3. **Modell-Keys** (Anthropic etc.) in die `api_keys`-Tabelle legen — am einfachsten lokal
   mit `uv run cmn-ai setup` (siehe Benutzerhandbuch), oder als Env-Var auf Render.

---

## 4. Render deployen (machst du)

1. github → das Repo pushen (Schritt 1).
2. Render → **New + → Blueprint** → dein Repo wählen. `render.yaml` wird erkannt.
3. Die `sync:false`-Variablen ausfüllen:
   - `OLLAMA_HOST` = die Pi-URL aus Schritt 2 (z. B. `https://pi-xyz.trycloudflare.com`)
   - `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_KEY` aus Schritt 3
   - `ANTHROPIC_API_KEY` (optional, falls nicht über Supabase)
4. Deploy. Health-Check ist `/healthz`. Beim ersten Aufruf erscheint die **Login-Seite**.

> **Chat-Verlauf:** Wenn `SUPABASE_URL` + `SUPABASE_KEY` gesetzt sind, speichert die App
> Konversationen + Nachrichten **direkt in Supabase** (Tabellen `cmn_conversations` /
> `cmn_messages`, pro Nutzer) — kein Disk nötig, echt mehrbenutzerfähig und persistent über
> Render-Neustarts. Der Render-Disk unter `/data` hält dann nur noch das lokale
> Decision-Log/Ledger (SQLite); ohne Supabase fällt auch der Verlauf auf SQLite/Disk zurück.
> (Spend/Budget pro Nutzer über `cmn_spend` / `cmn_user_settings` ist der nächste Schritt.)

---

## 4b. Obsidian-Vault (bleibt auf dem Pi)

Deine Notizen liegen **auf dem Pi**, nicht in der Cloud. Der Installer startet dafür einen
kleinen Vault-Dienst (`cmn-ai vault-serve`, Port `11435`, Notizen in `~/.cmn-ai/vault`).

- Exponiere `:11435` über einen **zweiten** ausgehenden Tunnel (wie bei Ollama, kein
  Port-Forwarding) und trag die URL als **`VAULT_HOST`** auf Render ein.
- In der App unter **Einstellungen → Obsidian Vault** deinen Vault-Ordner hochladen
  (nur `.md`). Der Browser liest die Dateien und schickt sie an den Pi.
- Bei passenden Fragen zieht die KI relevante Notizen automatisch als Kontext.

## 5. Als Web-App „installieren" (machst du / Nutzer)

Die App ist eine **PWA**. Im Browser auf der Render-URL:
- **Safari (macOS):** Menü „Ablage → Zum Dock hinzufügen".
- **Chrome:** Install-Icon in der Adressleiste.
- **Handy:** „Zum Home-Bildschirm".

Dann startet cmn-ai wie eine eigenständige App im eigenen Fenster.

---

## Was lokal vs. gehostet gilt

| | Lokal (Mac/Pi) | Render (gehostet) |
|---|---|---|
| Login | aus (offener Modus), wenn kein Supabase gesetzt | an (Supabase) |
| Lokales Modell | direkt (`localhost:11434`) | über `OLLAMA_HOST` → Pi |
| Profil | mac/pi (auto) | `CMN_AI_PROFILE=render` |
| DB | `~/.cmn-ai/cmn.db` | `/data/cmn.db` (Disk) |

Ohne Supabase-Env läuft die App weiter im **offenen Einzelnutzer-Modus** (keine Login-Wand) —
ideal für lokale Entwicklung auf dem Mac.


## Pi-Tunnel: empfohlener Weg (Stand Juli 2026)

`scripts/start-pi.sh` wählt automatisch den besten Modus:

1. **Tailscale Funnel** (empfohlen, keine Domain nötig): Ist der Pi im Tailnet,
   bekommt Ollama `https://<pi>.<tailnet>.ts.net` (Port 443) und der Vault `:8443` —
   dauerhafte URLs, einmal in Render eintragen. Voraussetzung auf dem Pi:
   `curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up`.
2. **Cloudflare Named Tunnel** (nur mit eigener Domain im Cloudflare-Konto):
   Tokens nach `~/.config/cmn-ai/cf-tunnels.env` (CF_TOKEN_OLLAMA / CF_TOKEN_VAULT)
   oder einmal `cloudflared tunnel login`.
3. **Quick Tunnels** (Fallback): wechselnde trycloudflare-URLs, nach jedem Start neu
   in Render eintragen.
