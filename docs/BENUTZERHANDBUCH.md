# cmn-ai — Benutzerhandbuch

Praktische Anleitung: starten, nutzen, konfigurieren — und was du wissen musst.
Für Entwickler-Details siehe die `README.md`, für den Raspberry Pi `docs/router-on-pi.md`.

---

## 1. Was ist das?

cmn-ai ist eine **lokale Chat-Oberfläche**, hinter der ein **Dirigent** sitzt: Er
klassifiziert jede Anfrage und schickt sie an die jeweils passende KI — unter einem
**harten Budget-Limit**.

- **Ein gratis lokales Modell** (Gemma über Ollama) erledigt den Großteil → kostet 0 €.
- **Bezahl-APIs** (Claude, OpenAI, Gemini, Perplexity) werden nur gezielt zugeschaltet,
  wenn eine Aufgabe sie wirklich braucht *und* das Budget es erlaubt.
- Jede Anfrage wird transparent angezeigt: welche KI, warum, wie teuer.

Standardmäßig läuft alles **lokal und kostenlos** — Bezahl-KIs musst du bewusst
aktivieren (siehe Abschnitt 6).

---

## 2. Voraussetzungen

| Was | Wofür | Pflicht? |
|-----|-------|----------|
| **Ollama** läuft lokal + ein Gemma-Modell ist geladen | das gratis lokale Modell | **Ja** |
| **uv** (Python-Paketmanager) | Projekt bauen & starten | **Ja** |
| API-Keys (Anthropic/OpenAI/…) | Bezahl-KIs | nein (nur wenn gewünscht) |
| Supabase-Projekt | Keys zentral speichern statt in `.env` | nein (Komfort) |

Ollama vorbereiten — das Modell muss zu deiner Config passen (Standard auf dem Mac:
`gemma4:31b` für den lokalen Chat, `gemma4:latest` für Optimierung/Routing-Fallback):

```bash
ollama serve            # läuft meist schon im Hintergrund
ollama pull gemma4      # bzw. den Modellnamen, den du in der Config nutzt
```

> Heißt dein installiertes Modell anders (z. B. `gemma2`, `llama3`), trag den Namen in
> `config/mac.yaml` bei `agents.local.model` ein — sonst findet cmn-ai das Modell nicht.

---

## 3. Installation

```bash
cd ~/cmn-ai
uv sync                 # installiert alle Abhängigkeiten
```

(Nur auf Apple-Silicon und nur fürs Trainieren des Router-Modells zusätzlich:
`uv sync --extra train`.)

---

## 4. Starten

Drei Wege — such dir einen aus:

```bash
# 1) Normal über die CLI
uv run cmn-ai serve              # dann http://127.0.0.1:8000 öffnen

# 2) Doppelklick (macOS): start.command im Finder doppelklicken
#    → startet den Server und öffnet den Browser automatisch

# 3) Nur Status anzeigen (ohne Server)
uv run cmn-ai                    # zeigt Profil, Router-Strategie, Budget, aktive Agenten
```

Beenden: `Ctrl-C` im Terminal.

---

## 5. Die Oberfläche nutzen

- **Chat-Feld**: Frage eintippen, Enter. Die Antwort streamt live.
- **Route-Indikator** (pro Antwort): zeigt *welche* KI geantwortet hat, *warum*
  (z. B. „free local model for low-complexity task") und die geschätzten Kosten.
- **Budget-Meter / Buckets**: aktueller Verbrauch je Topf (general / coding) gegen das
  Wochenlimit. Wird ein Topf voll, erscheint ein „Budget reached"-Hinweis.
- **Modell-Roster** (Seitenleiste): alle Agenten, grüner Punkt = aktiv (Key vorhanden).
  Beim Coding-Agent steht ein Badge **„tools: read-only"** bzw. **„read/write"**, wenn
  der Tool-Loop (Abschnitt 8) konfiguriert ist.
- **Aktivität/Analytics**: wie viele Anfragen an welche KI gingen und Gesamtkosten.

---

## 6. Bezahl-KIs aktivieren (optional)

Zwei Schritte: **Key hinterlegen** + **Agent einschalten**.

### Key hinterlegen — der bequeme Weg

```bash
uv run cmn-ai setup
```

Der Assistent fragt nach deiner Supabase-Projekt-URL + **Service-Role-Key**, legt bei
Bedarf die `api_keys`-Tabelle an und lädt deine Modell-Keys hoch. Beim Start zieht
cmn-ai die Keys automatisch aus Supabase und **aktiviert passende Agenten von selbst**.

> ⚠️ Es muss der **Service-Role-Key** sein, nicht der „publishable"-Key
> (`sb_publishable_…`). Mit dem öffentlichen Key bleibt die Key-Tabelle leer und die App
> läuft local-only — cmn-ai warnt dich beim Start, falls das passiert.

Manueller Weg ohne Supabase: Keys als Umgebungsvariablen setzen, z. B.
`export ANTHROPIC_API_KEY=sk-ant-…` (siehe `.env.example`).

### Agent einschalten

In `config/default.yaml` (oder deinem Profil) das gewünschte `enabled: false` auf
`true` setzen. Beispiel Claude + Coding-Agent (beide nutzen `ANTHROPIC_API_KEY`):

```yaml
agents:
  anthropic:
    enabled: true
  coding:
    enabled: true
```

Ohne Key wird der Agent zwar angezeigt, bleibt aber inaktiv (grauer Punkt) — er kann
also nichts kosten.

---

## 7. Budget verstehen

- **Monatsbudget** (Standard **35 €**) wird auf ein **rollierendes 7-Tage-Fenster**
  umgerechnet und in zwei isolierte **Töpfe** geteilt: **general 60 % / coding 40 %**.
  So kann Programmier-Nutzung nie dein ganzes Budget auffressen.
- **Gratis/lokale Anfragen laufen immer** (kosten 0 €). Nur **bezahlte** Calls werden
  gegen den Topf geprüft.
- Ist ein Topf voll → **harte Sperre** dieser Kategorie bis nächste Woche (Standard).
  Der andere Topf läuft weiter.
- **Limit erhöhen**: in der UI das Monatsbudget hochsetzen (`POST /api/budget/raise`) —
  die Wochenlimits skalieren sofort mit. (Das ist der spätere „Credits kaufen"-Hebel.)

Einstellbar in der Config unter `budget:` (`monthly_budget_eur`, `bucket_split`,
`on_limit: block | fallback_free | ask`).

---

## 8. Der Coding-Agent mit Werkzeugen (Tool-Loop)

Der Claude-Coding-Agent kann nicht nur antworten, sondern ein **Projekt selbst
durchsuchen und bearbeiten** — in einer **abgegrenzten Arbeitsmappe** (Sandbox), **ohne
Shell**. Das ist standardmäßig **aus** und wird pro Coding-Agent aktiviert:

```yaml
agents:
  coding:
    enabled: true
    workspace_root: ~/code/mein-projekt   # schaltet die (Lese-)Werkzeuge über diesen Ordner frei
    workspace_writable: false             # true = darf Dateien schreiben/ändern (write_file, edit_file)
```

- **Nur lesen** (`workspace_writable: false`): `read_file`, `list_dir`, `search`.
  Sicher — gut für „erklär mir diesen Code" / Analyse.
- **Lesen + schreiben** (`true`): zusätzlich Dateien anlegen/ändern.
- **Kein Bash**, keine beliebigen Befehle. Jeder Pfad ist auf `workspace_root`
  eingegrenzt — kein Ausbruch über `..` oder Symlinks, weder beim Lesen noch Schreiben.
- Der Loop ist doppelt gedeckelt: harte Runden-Obergrenze **und** ein Budget-Wächter, der
  abbricht, sobald der Coding-Topf erschöpft wäre.

> ⚠️ **Wenn du Schreiben aktivierst**, kann das Modell Dateien in `workspace_root`
> überschreiben. Richte es auf eine **Arbeitskopie** (am besten mit Git), nicht auf
> Originale ohne Backup.

---

## 9. Der Dirigent (Routing-Strategie)

Über `router.strategy` in der Config:

| Strategie | Bedeutung |
|-----------|-----------|
| `rule` | Heuristik (Schlüsselwörter/Länge). Läuft überall, braucht kein Modell. |
| `mlx` | Trainiertes Mini-Modell über Apple MLX (**nur Apple Silicon**). **Standard.** |
| `ollama` | Dasselbe trainierte Modell über Ollama — für den Raspberry Pi (`docs/router-on-pi.md`). |

**Wichtig:** `mlx` (Standard) braucht einen trainierten Adapter unter
`~/.cmn-ai/router-adapter-gemma3-1b`. Fehlt er, **schaltet cmn-ai automatisch auf
`rule` zurück** — die App läuft normal weiter, nur eben heuristisch. Willst du das
trainierte Modell sicher umgehen, setze `router.strategy: rule`.

Optional: `router.optimize: true` lässt das lokale Gemma längere Prompts vor der
Beantwortung umformulieren (klarer/präziser). Sicher gebaut: triviale Prompts werden
übersprungen, bei jedem Fehler wird der Original-Prompt verwendet. Standard: aus.

---

## 10. CLI-Befehle

| Befehl | Was er tut |
|--------|------------|
| `uv run cmn-ai` | Status (Profil, Strategie, Budget, aktive Agenten) |
| `uv run cmn-ai serve [--host --port]` | Web-UI starten (Standard 127.0.0.1:8000) |
| `uv run cmn-ai setup` | Supabase + API-Key-Assistent |
| `uv run cmn-ai train [--iters N]` | Router-Modell lokal trainieren (Apple Silicon) |
| `uv run cmn-ai eval` | Trainiertes Modell vs. Regel-Baseline vergleichen |

---

## 11. Was du wissen musst (ehrlich)

- **Standard = lokal & gratis.** Es entstehen keine Kosten, solange du keine Bezahl-KI
  aktivierst und mit Key versiehst.
- **Deine Daten** liegen lokal unter `~/.cmn-ai/` (SQLite-DB `cmn.db` mit Chats,
  Ausgaben-Ledger, Routing-Entscheidungen; ggf. trainierte Adapter). Nichts geht ohne
  deine aktiven Keys nach außen.
- **Kosten sind Schätzungen** aus einer Preistabelle (Tokens × Preis). Sie sind der
  Maßstab für das Budget, aber nicht die zentgenaue Provider-Rechnung.
- **Modellnamen müssen passen.** `gemma4:…` ist nur der konfigurierte Name — es muss in
  Ollama unter genau diesem Namen vorhanden sein, sonst schlägt der lokale Chat fehl.
- **Coding-Tool-Loop**: bisher mit nachgestellten (gemockten) API-Antworten getestet,
  ein echter End-to-End-Lauf braucht einen Anthropic-Key und wurde noch nicht gemacht.
  Schreibzugriff nur auf eine Arbeitskopie richten (siehe Abschnitt 8).
- **Noch nicht enthalten:** Bash-Werkzeug für den Coding-Agenten, Mehrbenutzer/Login,
  Bezahl-/Abrechnungs-Anbindung. Das sind die nächsten möglichen Ausbaustufen.

---

## 12. Problemlösung

| Symptom | Ursache / Lösung |
|---------|------------------|
| Keine Antwort / Fehler beim Chat | Ollama läuft nicht oder Modell fehlt → `ollama serve`, `ollama pull <modell>`. Modellname in `config/*.yaml` prüfen. |
| Beim Start „SUPABASE_KEY looks like a publishable key" | Du hast den öffentlichen Key statt des Service-Role-Keys hinterlegt → in `.env`/Supabase korrigieren. |
| Bezahl-KI bleibt grau/inaktiv | Kein Key vorhanden, oder Agent in der Config nicht `enabled: true`. |
| „router strategy=mlx but no adapter" im Log | Normal ohne trainiertes Modell — läuft mit Regel-Router weiter. Adapter mit `cmn-ai train` erzeugen oder `strategy: rule` setzen. |
| Budget blockt eine Anfrage | Wochenlimit des Topfes erreicht → in der UI Monatsbudget erhöhen oder bis nächste Woche warten. |
| Coding-Agent ändert nichts an Dateien | `workspace_root` gesetzt? Für Schreiben zusätzlich `workspace_writable: true`. |


## Neu seit Juli 2026

- **Konto & gespeicherte Chats:** Registrieren geht sofort (keine Bestätigungs-Mail).
  „Passwort vergessen?" auf der Anmeldeseite schickt einen Reset-Link.
- **Jede Datei rein:** Dateien einfach ins Chat-Fenster **ziehen** (Drag & Drop),
  Screenshots direkt **einfügen** (Cmd+V) oder über die Büroklammer wählen — PDF,
  Word (docx), Excel (xlsx), PowerPoint (pptx), Bilder, Text und Code (max. 8 Stück,
  15 MB). Die KI liest den Inhalt; Bilder gehen an ein Vision-Modell.
- **Dateien raus:** Sag einfach „… und gib es mir als PDF" (oder docx/pptx) — die KI
  erstellt das komplette Dokument und unter der Antwort erscheint eine
  **Download-Karte**. Zusätzlich lässt sich jede Antwort unten manuell als
  **md / pdf / docx / pptx** speichern (PDF mit Tabellen, Codeblöcken und vollem
  Unicode; PowerPoint: Überschriften werden Folien).
- **Schöne Antworten:** Codeblöcke (mit Kopieren-Button), Listen und Tabellen werden
  formatiert dargestellt; Antworten sind jetzt ausführlich statt einzeilig.
- **Hell/Dunkel:** Einstellungen → Darstellung (System/Dunkel/Hell).
- **Speed-Test:** Einstellungen → „Speed-Test starten" misst, welches Modell gerade am
  schnellsten antwortet (bezahlte Modelle kosten dabei einen Mini-Betrag).
- **Kluges Routing:** Einfache Fragen beantwortet das günstigste Modell, komplexe
  Fragen und Dokument-Analysen gehen automatisch an ein starkes Modell (Claude/GPT),
  Recherche an Perplexity, Bilder an Gemini. Ist das lokale Pi-Modell nicht
  erreichbar, wird es per Health-Check übersprungen — ohne Fehlversuch und ohne
  irreführendes „Fallback"-Label.
- **Landing Page:** Die Hauptdomain zeigt Besuchern jetzt die Produktseite mit
  „App downloaden" (PWA) und „oder im Browser öffnen".
