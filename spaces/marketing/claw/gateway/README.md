# marketing-claw Gateway

Die Saat `config/openclaw.json` ist KOMMENTARFREI, weil dieser openclaw-Build
(2026.7.1-slim) unbekannte Root-Schluessel als `<root>: Invalid input` ablehnt
(gemessen 02.09.2026 — die sales-claw-Saat traegt Root-Kommentare, ihr Build
toleriert sie; nicht kopieren). Was dort stuende:

Saat marketing-claw (02.09.2026). Modell-Provider = Claude-Shim (Subscription, kein API-Budget): im WSL-Mirrored-Modus erreicht der Container den loopback-gebundenen Shim DIREKT ueber host.docker.internal:8117, der eigenen Marketing-Shim-Instanz (damals als :8114 gemessen: HTTP 200; die Saat zeigt heute auf :8117, `claw/shim/README.md`) — kein Portproxy, kein LAN-Expose. apiKey ist ein Platzhalter, der Shim prueft keinen. Heute EIN MCP-Server: marketing (Host-Sidecar :8130, nie senden). Der Rowboat-Lesezugriff liegt als Passthrough im Sidecar (claw/werkzeuge.py, _rowboat), weil Container im Mirrored-Modus kein LAN erreichen; der Rowboat-Schluessel erreicht das Gateway-Volume nie. anbinden.sh spielt nur noch die Saat ein und prueft Werkzeuge (kein Sende-, kein Schreibwerkzeug). KEINE channels: dieser Agent kann strukturell nichts versenden. Kommentar-Schluessel stehen bewusst NICHT in mcp.servers — dort waere jeder Schluessel ein Servername.

Saat -> Laufzeit: `docker compose cp config/openclaw.json marketing-claw:/home/node/.openclaw/openclaw.json`
(wirkt logisch erst nach restart), Workspace ebenso nach
`/home/node/.openclaw/workspace/`. Rowboat-Schluessel: `anbinden.sh`.

## Stand (2026-10-07)

- Gateway-Container `marketing-claw` (`docker-compose.yml`), Gateway bindet loopback :18895.
- Modell: `http://host.docker.internal:8117/v1` (eigene Marketing-Shim-Instanz, nicht die gemeinsame :8114).
- MCP-Server `marketing` auf `http://host.docker.internal:8130/mcp` (Host-Sidecar mit 27 Werkzeugen, siehe `claw/server.py`).
- Persona und Skills unter `config/workspace/` (`AGENTS.md`, `skills/`); sie sagt dem Agenten "Du versendest NICHTS". Versand ist nur als Versandauftrag an sales-claw moeglich (`versand_beauftragen`).
- `anbinden.sh` ist die wiederholbare Abnahme (Saat einspielen, Werkzeuge sichtbar, kein Sende-/Schreibwerkzeug).
