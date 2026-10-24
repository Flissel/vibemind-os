# Rowboat-Server ohne Oberflaeche (offload-vm)

Rowboats Kern (`rowboat-server.cjs`, upstream `apps/server`, `npm run build:headless`) laeuft auf dem
Mini-PC in einem Container. Erreichbar im Tailnet unter `http://100.67.177.45:3220`.

## Bauen und uebertragen (am PC)

```powershell
pwsh -File spaces/rowboat/deploy/headless/bauen.ps1 -NurBauen   # nur bauen
pwsh -File spaces/rowboat/deploy/headless/bauen.ps1             # bauen + nach offload-vm kopieren
```

Quelle ist die gepinnte Kopie im Betriebs-Checkout (`-Quelle`, Vorgabe
`C:/Users/User/ClaudeWork/vibemind-betrieb/spaces/rowboat/rowboat/apps/x`). Diese Kopie nie von Hand
aendern, Aenderungen nur ueber den Fork und `spaces/rowboat/scripts/sync_desktop.py`.
Das Skript kopiert `dist-headless/` nach `~/rowboat-headless/app/` und `Dockerfile`, `compose.yml`,
`start.sh` nach `~/rowboat-headless/`. Es loescht nichts auf der VM.

## Starten, stoppen, Logs (auf der VM, nur mit Betreiber-OK)

```sh
cd ~/rowboat-headless
docker compose up -d --build     # starten (baut node-pty im Image)
docker compose stop              # stoppen
docker compose logs -f --tail 200
curl -s http://100.67.177.45:3220/health
```

## Wo die Daten liegen

- Arbeitsordner: `/home/debian/rowboat-data` (Besitzer debian, Modus 0700), im Container `/data`.
- Darin: `server-key` (Zugangsschluessel fuer App und Skripte, 0600), `cipher-key`, `config/`,
  `knowledge/`, `storage/`. Beide Schluessel nie kopieren, nie in Logs oder Commits.
- Beim ersten Start legt `start.sh` `config/server.json` mit `lanEnabled: true` an, sonst bindet der
  Server nur 127.0.0.1. Eine vorhandene Datei bleibt unveraendert.

## Warum `network_mode: host`

Der Server lehnt jede Anfrage mit fremdem `Host`-Header ab (403 "unrecognized Host header"). Erlaubt
sind nur `localhost`, der eigene Rechnername und die IPs der eigenen Netzschnittstellen. In einem
Docker-Bridge-Netz kennt der Container die Tailscale-IP nicht, deshalb Host-Netz und kein `ports:`.
Aus demselben Grund geht `tailscale serve` mit `*.ts.net`-Namen nicht; die App verbindet sich direkt
per `http://100.67.177.45:3220`. Mit `lanEnabled` lauscht der Server auf allen Schnittstellen der VM;
ohne gueltigen `server-key` antwortet er nur auf `/health`.
