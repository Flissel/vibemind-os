# marketing-claw: eigene Shim-Instanz auf :8117

## Warum eine zweite Instanz

Der Claude-Shim (`C:\Users\User\.local\bin\claude_code_openai_shim.py`) spricht
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

Der Launcher fährt die Instanz als Sidecar `marketing_claw_shim` mit gesetztem
`SHIM_EXTRA_MCP_CONFIG`. Von Hand:

    python "C:\Users\User\.local\bin\claude_code_openai_shim.py" --host 127.0.0.1 --port 8117

mit `SHIM_EXTRA_MCP_CONFIG` auf diese Datei. Prüfen:

    curl -s http://127.0.0.1:8117/v1/models

## Grenze

Die CLI-Schleife läuft als der angemeldete Benutzer auf dem Host — sie kann
genau die zehn Werkzeuge aus der `allowedTools`-Liste rufen
(`--strict-mcp-config` schließt alles andere aus), und keines davon versendet
etwas.
