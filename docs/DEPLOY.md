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

## 2. Raspberry Pi erreichbar machen (machst du)

Render muss das Ollama des Pi erreichen. Ollama lauscht lokal auf `:11434` — gib es
sicher nach außen, z. B. mit **Tailscale Funnel** oder **Cloudflare Tunnel** (kein offenes
Port-Forwarding!). Du bekommst eine URL wie `https://pi-xyz.trycloudflare.com`.

Auf dem Pi muss Ollama auf allen Interfaces lauschen:

```bash
OLLAMA_HOST=0.0.0.0:11434 ollama serve
ollama pull gemma4        # bzw. dein Modellname
```

> Ehrliche Einschränkung: Render → Pi geht übers Heimnetz. Latenz und Verfügbarkeit
> hängen an der Pi-/Internet-Verbindung zu Hause.

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
