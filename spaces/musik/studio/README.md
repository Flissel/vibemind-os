# Musik-Studio (djset)

`djset` baut aus einem Genre-Profil und einer Bibliothek eigener Tracks ein
komplettes DJ-Set: Profil, Auswahl, Reihenfolge, Uebergaenge und Mix-Render.
Das Paket kam mit seiner Git-Historie aus dem lokalen Projekt `Musik` in den
Space (`git subtree add`, ohne Squash). Der Kommandozeilenbefehl heisst `dj-set`.

Die Umgebung baut man im Verzeichnis `spaces/musik/studio` (Python 3.11 oder
neuer, ffmpeg mit rubberband im PATH):

    python -m venv .venv
    .venv/Scripts/python -m pip install -e ".[dev]"

Die Studio-Tests laufen nur mit dieser Umgebung, denn sie brauchen librosa und
Co. pytest laeuft mit `filterwarnings = error`, die Ausgabe muss sauber sein:

    PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python -m pytest -q --basetemp C:/Users/User/AppData/Local/Temp/djset-pytest

Die uebrigen Tests des Space (`agents/`, `api/`) laufen im Wurzelverzeichnis mit
dem System-Python (`python -m pytest -p no:cacheprovider spaces/musik -q`);
`../conftest.py` blendet `studio/` dort aus. Entwurf und Plan stehen in
`docs/superpowers/specs/2026-11-23-musik-studio-design.md` und
`docs/superpowers/plans/2026-11-23-musik-studio-plan-1-erstes-set.md`
(im Wurzelverzeichnis des Repos).

## Musik-Dienst und Musik-Claw

Drei Dienste laufen am PC (nur `127.0.0.1`):

| Dienst | Port | Aufgabe |
| --- | --- | --- |
| `musik_dienst` (`musik-dienst`) | 8148 | MCP (`/mcp`) und JSON-Routen; arbeitet Bestellungen im Hintergrund ab (Set, Videomusik, Aufnehmen, Laura-Render) und legt Videomusik in Laura. Der Datei-Link fuer Laura laeuft getrennt auf 8146. |
| `musik_shim` | 8152 | eigene Instanz der Marketing-Shim (`spaces/marketing/claw/shim/marketing_shim.py`): Claude Code als Modell der Musik-Claw, Budget-Name `musik-claw`, Werkzeugliste aus `spaces/musik/claw/musik-mcp.json`. |
| `musik_telegram` (`musik-telegram`) | 8142 | Telegram-Bot: nur der eigene Chat; schickt `spaces/musik/claw/AGENTS.md` als Systemprompt mit jeder Anfrage an die Shim und liefert fertige Auftraege mit Datei zurueck. |

Start (idempotent, ein Dienst mit antwortendem `/health` bleibt unberuehrt):

    powershell -NoProfile -File spaces/musik/studio/scripts/musik-dienste-starten.ps1
    powershell -NoProfile -File spaces/musik/studio/scripts/musik-dienste-starten.ps1 -Nur musik_dienst,musik_shim
    powershell -NoProfile -File spaces/musik/studio/scripts/musik-dienste-starten.ps1 -Liste

`-Liste` gibt Name, Port und Health-Pfad als JSON aus und startet nichts. Nach dem Start
wartet das Skript bis zu 20 s auf `/health` und meldet je Dienst `OK` oder
`NICHT ERREICHBAR`. Die Skriptpfade stammen aus dem Ort des Skripts; das Budget-Modul
(`scripts/claude_budget.py` im aeusseren Repo) wird nur eingetragen, wenn es dort liegt.

Logs: `studio/logs/musik/<dienst>.log` (Ausgabe) und `<dienst>.err` (Fehler), jeweils mit
`.prev` vom Lauf davor. Der Ordner `logs/` ist git-ignoriert.

Umgebung (nur Namen; Werte stehen in der `.env` des aeusseren Repos, die Dienste laden
sie selbst, das Skript liest und druckt keine Geheimnisse):

- Musik-Dienst: `MUSIK_DATEN` (Datenordner; das Skript nimmt den vorhandenen Wert, sonst
  `VibemindDaten\musik` im Benutzerordner), `MUSIK_STUDIO_PORT`, `VIBEMIND_AGENT`
  (`musik-kette`), `VIBEMIND_BUDGET_MODUL`, `MUSIK_LINK_HOST`, `MUSIK_LINK_PORT`,
  `MUSIK_LINK_SCHLUESSEL`, `LAURA_API_URL`, `LAURA_TOKEN`.
- Musik-Shim: `VIBEMIND_AGENT` (`musik-claw`), `VIBEMIND_BUDGET_MODUL`,
  `SHIM_EXTRA_MCP_CONFIG`, `MCP_TOOL_TIMEOUT`.
- Musik-Telegram: `MUSIK_TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`, `MUSIK_TELEGRAM_PORT`,
  `MUSIK_TELEGRAM_LLM_URL`, `MUSIK_STUDIO_URL`, `MUSIK_TELEGRAM_ZUSTAND` (optional).

Die Umgebung gilt nur fuer den jeweiligen Kindprozess; die Sitzung des Aufrufers bleibt
unveraendert. Der Weg der Musik nach Laura steht in `../docs/LAURA-VERTRAG.md`.
