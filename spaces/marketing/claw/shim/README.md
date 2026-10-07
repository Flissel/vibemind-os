# marketing-claw: eigene Shim-Instanz auf :8117

## Warum eine zweite Instanz

Der gemeinsame Claude-Shim (`claude_code_openai_shim.py` im Benutzer-`.local/bin`, Port :8114) spricht
OpenAI-Protokoll und leitet an die Claude-Code-CLI weiter — die Subscription
statt API-Budget. Er reicht die `tools` des Aufrufers aber **nicht als
`tool_calls` zurück**, er protokolliert sie nur (gemessen 02.09.2026: openclaw
bot 45 Werkzeuge an, der Agent bekam keinen einzigen Aufruf zustande und sagte
das selbst). Eine Agentenschleife, die auf `tool_calls` wartet, bleibt an diesem
Shim also werkzeuglos.

Der Ausweg ohne Umbau der Antwortform: die Werkzeuge **in die CLI-Schleife**
hineinreichen. Die CLI kann MCP-Server per `--mcp-config` bedienen; der Shim
baut diesen Aufruf ohnehin schon (für Captains Artefakt-Server). Er hat dafür
jetzt ein generisches, env-gesteuertes Tor:

    SHIM_EXTRA_MCP_CONFIG=<pfad zu dieser marketing-mcp.json>

Ohne die Variable ändert sich für Hermes und Captain nichts — deshalb eine
**zweite Instanz auf :8117** statt einer Änderung an der gemeinsamen :8114.

## Was das für die Rollen bedeutet

- **openclaw** ist Gesprächs-Frontend, Sitzungsgedächtnis und Arbeitsraum
  (`AGENTS.md`) — und bleibt die Instanz, die keinen Sendeweg kennt.
- **Die CLI im Shim** ist die Agentenschleife, die wirklich Werkzeuge ruft
  (`mcp__marketing__*` gegen den Sidecar auf :8130).
- Der Sidecar bleibt die einzige Stelle, an der Schlüssel liegen.

## Start

Gestartet wird die Instanz von `claw/scripts/marketing-dienste-starten.ps1`
(Dienst `marketing_claw_shim`) mit `SHIM_EXTRA_MCP_CONFIG` auf `marketing-mcp.json`,
`SHIM_NEUTRALIZE_DOUBLE_BRACKETS=1` und `VIBEMIND_AGENT=marketing-chat`
(Budget-Wächter). Sie führt die Datei **`marketing_shim.py` aus diesem
Ordner** aus, eine Kopie des gemeinsamen Shims vom 02.10.2026 (zusätzlich:
Body-Flag `marketing_stream` für echtes Streaming). Von Hand, mit gesetzten
Variablen:

    python spaces/marketing/claw/shim/marketing_shim.py --host 127.0.0.1 --port 8117

Der Vorgabewert von `--port` in `marketing_shim.py` ist weiterhin 8114 (und
`claw/llm.py` nutzt als Vorgabe `http://127.0.0.1:8114/v1`); die Instanz ist nur
dann :8117, wenn der Port ausdrücklich übergeben wird, wie es das Startskript
tut. Prüfen:

    curl -s http://127.0.0.1:8117/v1/models

## Der irreführende „out of extra usage"-Abbruch (gemessen 03.09.2026)

Der Agentenlauf durchs Gateway brach mit `API Error: 400 You're out of extra
usage` ab — bei vollem Kontingent: eine Direktprobe gegen denselben Shim mit
demselben Konto lief Sekunden vorher. Bisektion eines 1:1 nachgespielten
openclaw-Aufrufs (Request-Dump per `SHIM_DUMP_DIR`): nicht Größe (27.000
Zeichen laufen), nicht Streaming, nicht die 44 Werkzeuge, kein Emoji — sondern
zwei Zeilen aus openclaws „Assistant Output Directives" **zusammen**:

    - Native quote/reply: first token `[[reply_to_current]]`; use `[[reply_to:<id>]]` ...
    - Supported directives are stripped before rendering; channel config still decides delivery.

Jede Zeile allein und jeder Marker allein läuft durch. `[[` → `[ [` heilt es
deterministisch. Warum die CLI dafür eine Kontingent-Meldung ausgibt, ist
nicht geklärt — die Meldung ist jedenfalls **kein** Kontingent-Befund.
Darum trägt diese Instanz `SHIM_NEUTRALIZE_DOUBLE_BRACKETS=1`; openclaw kennt
laut Schema keinen Schalter, die Direktiven wegzulassen. Für den
Marketing-Agenten ist die Entschärfung folgenlos: er hat keine Kanäle, also
nichts, was ein `[[reply_to_current]]` je rendern würde.

## Grenze

Die CLI-Schleife läuft als der angemeldete Benutzer auf dem Host — sie kann
nur die Werkzeuge aus der `allowedTools`-Liste von `marketing-mcp.json` rufen
(`--strict-mcp-config` schließt alles andere aus). Stand dieser Datei: 51
Einträge, 25 `mcp__marketing__*` und 26 `mcp__laura__*`. Kein Marketing-Werkzeug
versendet etwas; Versand läuft nur über `versand_beauftragen` als Auftrag an
sales-claw.

**Bekannte Lücke:** Der Sidecar (`claw/server.py`) registriert 27 Werkzeuge, die
Allow-List führt nur 25 davon. `newsletter_bildplaetze` und
`newsletter_bild_beauftragen` fehlen, die CLI kann sie über diese Instanz also
nicht rufen. Die Laura-Einträge umfassen auch schreibende Werkzeuge
(z. B. `import_media`, `edit_timeline`, `render_timeline`). Die Liste ist hier
nur dokumentiert, nicht geändert.
