# cmn·ai — Modell-Benchmark & Routing-Übersicht

Welche KI wofür, was sie kostet, und wie der Dirigent entscheidet. Preise sind
EUR pro **Million Token** (Ein-/Ausgabe), Stand der Preistabelle in
`src/cmn_ai/budget/pricing.py` — dort ist die einzige Quelle der Wahrheit.

## Die Riege

| Agent | Provider · Modell | € in / out je 1M | Rolle im System | Stärke |
|---|---|---|---|---|
| **local** | Ollama · Gemma (Pi) | **gratis** | trägt das Alltags­volumen | kostenlos, privat, aber schwächer |
| **gemini** | Google · gemini-3.5-flash | 0,28 / 2,30 | günstiger Generalist | sehr schnell & billig, macht mehr Fehler |
| **openai** | OpenAI · gpt-5.4-mini | 0,69 / 4,14 | zweiter günstiger Generalist | solide, guter Preis |
| **anthropic** | Anthropic · claude-sonnet-5 | 2,80 / 13,80 | starker Denker | beste Qualität bei komplexen Fragen |
| **coding** | Anthropic · claude-sonnet-5 → opus-4-8 | 2,80 / 13,80 (Eskalation teurer) | Programmier-Spezialist | Code, eskaliert bei harten Aufgaben |
| **perplexity** | Perplexity · sonar-pro | 2,80 / 13,80 | Web-Recherche | aktuelle Fakten **mit Quellen** |

> Faustregel: gratis lokal trägt die Masse, bezahlte Experten werden gezielt
> engagiert — nie abonniert. Unter jeder Antwort steht, wer geantwortet hat und
> was es gekostet hat.

## Wer bekommt was (Routing-Policy)

Der Dirigent klassifiziert jede Frage und wählt danach:

| Frageart | Geht an | Warum |
|---|---|---|
| Triviale Alltagsfrage (CHAT, einfach) | lokal (gratis) → sonst günstigster (Gemini/GPT, verteilt) | Volumen billig halten |
| Komplexe Frage (CHAT, schwer) | **Claude Sonnet** → GPT-mini | Qualität, wo es zählt |
| Recherche / aktuelle Fakten | **Perplexity** | echte Websuche statt Raten |
| Code / Debugging | **Coding-Agent** (Claude, eskaliert zu Opus) | spezialisiert |
| Bilder / Anhänge | **Gemini** (Vision) → Claude | multimodal |
| **Team (mehrere KIs)** | **Claude + Perplexity + GPT** parallel, Claude führt zusammen | stärkstes, vielfältiges Panel |

Ist der Pi (lokales Modell) offline, wird er per Health-Check übersprungen —
das bezahlte Panel arbeitet ohne Unterbruch weiter, ohne irreführendes
„Fallback"-Label.

## Team-Modus (Council)

Bei „Team (mehrere KIs)" entwerfen bis zu drei **starke, vielfältige** KIs
(Claude, Perplexity, GPT — nicht die drei billigsten) parallel eine Antwort; die
stärkste führt die Entwürfe zu einer besten Antwort zusammen. Robust: fällt eine
KI aus, arbeiten die übrigen weiter; ist nur eine verfügbar, antwortet sie allein.
Jeder Beitrag wird genannt und verbucht.

## Kostenkontrolle

Ein hartes Wochenbudget (rollierend, aus dem Monatsbudget abgeleitet), getrennt
nach `general` und `coding`. Ist ein Topf leer, wird lokal geantwortet oder
ehrlich blockiert — nie stillschweigend weiter abgerechnet.

## Gratis-Tier-Realität (Google Gemini, Stand 2026)

- **Gemini 2.5 Pro** ist im kostenlosen Tier enthalten, aber stark limitiert
  (~50–100 Anfragen/Tag, ~5/Minute) → nur für Prototyping/persönliche Nutzung,
  nicht als Volumen-Default.
- **Flash / Flash-Lite** haben viel höhere Gratis-Limits (250–1.000/Tag) und
  tragen deshalb das Volumen.
- Achtung: Gratis-Tier-Eingaben können von Google zum Modelltraining genutzt
  werden — für sensible Daten ungeeignet.

Quellen: [Gemini API Rate limits](https://ai.google.dev/gemini-api/docs/rate-limits) ·
[Free-Tier-Guide 2026](https://www.aifreeapi.com/en/posts/gemini-api-free-tier-complete-guide)

## Geschwindigkeit selbst messen

Einstellungen → **„Speed-Test starten"** schickt dieselbe kurze Frage parallel an
alle aktiven Modelle und zeigt Antwortzeit und Kosten je Modell (bezahlte Modelle
kosten dabei einen Mini-Betrag, der verbucht wird). So siehst du das aktuelle
Ranking für deinen Standort und deine Keys.
