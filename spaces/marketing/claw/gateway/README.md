# marketing-claw Gateway

Die Saat `config/openclaw.json` ist KOMMENTARFREI, weil dieser openclaw-Build
(2026.7.1-slim) unbekannte Root-Schluessel als `<root>: Invalid input` ablehnt
(gemessen 02.09.2026 — die sales-claw-Saat traegt Root-Kommentare, ihr Build
toleriert sie; nicht kopieren). Was dort stuende:

Saat marketing-claw (02.09.2026). Modell-Provider = Claude-Shim (Subscription, kein API-Budget): im WSL-Mirrored-Modus erreicht der Container den loopback-gebundenen Shim DIREKT ueber host.docker.internal:8114 (gemessen: HTTP 200) — kein Portproxy, kein LAN-Expose. apiKey ist ein Platzhalter, der Shim prueft keinen. Zwei MCP-Server: marketing (Host-Sidecar :8130, Entwuerfe->Staging+Schaufenster, nie senden) und rowboat (VM :3100, toolFilter NUR die drei Lesewerkzeuge — Schreibwerkzeuge bleiben Sync-Workern vorbehalten, Spec E2). Der Rowboat-Bearer-Schluessel steht NICHT hier (keine Env-Referenz in mcp.servers.*.headers) — anbinden.sh setzt ihn ins Laufzeit-Volume. KEINE channels: dieser Agent kann strukturell nichts versenden. Kommentar-Schluessel stehen bewusst NICHT in mcp.servers — dort waere jeder Schluessel ein Servername.

Saat -> Laufzeit: `docker compose cp config/openclaw.json marketing-claw:/home/node/.openclaw/openclaw.json`
(wirkt logisch erst nach restart), Workspace ebenso nach
`/home/node/.openclaw/workspace/`. Rowboat-Schluessel: `anbinden.sh`.
