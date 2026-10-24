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
Das Skript kopiert `dist-headless/` nach `~/rowboat-headless/app/` und `Dockerfile`, `.dockerignore`,
`compose.yml`, `start.sh`, `rowboat-firewall.nft` nach `~/rowboat-headless/`. Es legt
`~/rowboat-data` (0700) an, falls er fehlt, und loescht nichts auf der VM.

## Firewall (vor dem ersten Start)

Der Server lauscht wegen `lanEnabled` auf allen Schnittstellen. `rowboat-firewall.nft` laesst die
Ports 3220-3229 (3220 plus die Ausweichports des Servers) nur ueber `lo` und `tailscale0` zu. Es ist
eine eigene Tabelle `inet rowboat`, Dockers Regeln bleiben unberuehrt.

```sh
sudo nft -f ~/rowboat-headless/rowboat-firewall.nft    # sofort aktiv
sudo nft list table inet rowboat                       # pruefen
```

Dauerhaft ueber Neustarts: die Datei nach `/etc/nftables.d/` kopieren, in `/etc/nftables.conf` die
Zeile `include "/etc/nftables.d/rowboat-firewall.nft"` ergaenzen und `sudo systemctl enable nftables`.
Debians Vorgabe-`/etc/nftables.conf` beginnt mit `flush ruleset`: beim Booten laeuft das vor Docker
und schadet nicht, ein spaeteres `systemctl restart nftables` raeumt aber Dockers Regeln ab (danach
`sudo systemctl restart docker`). Zum Nachladen nur die Rowboat-Tabelle neu einlesen; ein zweites
`nft -f` haengt die Regeln doppelt an (harmlos), sauber geht es mit vorherigem
`sudo nft delete table inet rowboat`.

Probe:

- von einem anderen Rechner im LAN: `curl --max-time 5 http://<LAN-IP>:3220/health` muss in den
  Timeout laufen;
- aus dem Tailnet: `curl -s http://100.67.177.45:3220/health` liefert 200.

## Starten, stoppen, Logs (auf der VM, nur mit Betreiber-OK)

`~/rowboat-data` muss vorher existieren (Besitzer debian, 0700); compose legt ihn bewusst nicht an
(`create_host_path: false`), sonst gehoerte er root und der Server (uid 1000) koennte nicht schreiben.

```sh
cd ~/rowboat-headless
docker compose up -d --build     # starten (baut node-pty im Image)
docker compose stop              # stoppen
docker compose logs -f --tail 200
curl -s http://100.67.177.45:3220/health
```

Steht der Container auf `unhealthy`: `docker compose logs` nach "port 3220 is taken" durchsuchen. Dann
ist 3220 belegt und der Server auf 3221 ff. ausgewichen; der Healthcheck prueft nur 3220.

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
