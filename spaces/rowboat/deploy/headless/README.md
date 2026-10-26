# Rowboat-Server ohne Oberflaeche (offload-vm)

Rowboats Kern (`rowboat-server.cjs`, upstream `apps/server`, `npm run build:headless`) laeuft auf dem
Mini-PC in einem Container. Erreichbar im Tailnet unter `http://100.67.177.45:3220`.

Die Umstellung vom PC auf den Mini-PC (Reihenfolge, Abnahme, Rueckweg) steht in
[`UMSTELLUNG.md`](UMSTELLUNG.md); diese Datei ist dafuer die verbindliche Schrittliste.

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

**Pflicht: dauerhaft ueber Neustarts** ueber die eigene Einheit `rowboat-firewall.service`
(laedt nur die Tabelle `inet rowboat`, vor Docker). NICHT `nftables.service` einschalten: Debians
`/etc/nftables.conf` beginnt mit `flush ruleset` und raeumt Dockers Regeln ab.

```sh
sudo install -d -m 755 /etc/nftables.d
sudo install -m 644 ~/rowboat-headless/rowboat-firewall.nft /etc/nftables.d/rowboat-firewall.nft
sudo install -m 644 ~/rowboat-headless/rowboat-firewall.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now rowboat-firewall
sudo nft list table inet rowboat          # Tabelle da, Regeln einfach (Neuladen ersetzt sie)
```

Eingerichtet auf offload-vm am 25.10.2026 (Neuladen geprueft, Docker-Tabellen unberuehrt).

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

## Claude-Shim `rowboat-shim` (127.0.0.1:8121)

Rowboats Modell-Anbieter "openai-compatible" zeigt auf `http://127.0.0.1:8121/v1`. Der Dienst ist der
Shim vom PC (`claude_code_openai_shim.py`, `SHIM_TOOL_CALLS=1`) in einem Container mit der Claude-CLI
(gleiche Version wie am PC, im `shim/Dockerfile` gepinnt). Ein Modell laeuft dort nicht, die CLI ruft
das Abo auf. Er bindet nur Loopback, ist also weder im LAN noch im Tailnet erreichbar.

- Budget-Huelle `shim/rowboat_shim_huelle.py` (Muster `Vibemind_V1/scripts/brain_devops_shim.py`):
  vor jedem CLI-Aufruf `claude_budget.pruefen("rowboat-server")`, danach `buchen`. Lehnt der Waechter
  ab oder geht er kaputt, kommt eine Fehlermeldung und kein Aufruf (fail-closed). Grenze: 150 Aufrufe
  am Tag (Klasse `auftrag`, also keine Pause, waehrend der Betreiber am PC arbeitet),
  `Vibemind_V1/config/agent_budget.yaml`.
- Das Konto der VM liegt in `~/rowboat-headless/budget/` (0700, legt `bauen.ps1` an) und ist
  **getrennt** vom Konto am PC. Not-Aus: `touch ~/rowboat-headless/budget/STOP`.
- Shim, Werkzeugmodul, `claude_budget.py` und `agent_budget.yaml` liegen nicht im Repo. `bauen.ps1`
  kopiert sie bei jedem Uebertragen frisch nach `~/rowboat-headless/shim/`. Wer die Grenze aendert,
  uebertraegt neu und baut neu (`docker compose up -d --build rowboat-shim`).
- Anmeldung: `~/rowboat-headless/claude-token` mit genau einer Zeile `CLAUDE_CODE_OAUTH_TOKEN=...`,
  Modus 0600, per `env_file`. Erzeugt am PC mit `claude setup-token`, uebergeben ohne Anzeige:
  `ssh offload-vm 'umask 077; cat > ~/rowboat-headless/claude-token' < <datei>`. Fehlt die Datei,
  verweigert compose den Start.

Probe (auf der VM, nach Betreiber-OK):

```sh
cd ~/rowboat-headless && docker compose up -d --build rowboat-shim
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8121/v1/models   # 200, bucht nichts
tail -n 2 ~/rowboat-headless/budget/$(date +%F).jsonl                       # nach einem Testaufruf: pruefung + buchung
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

## Bedeutungssuche `rowboat-wissen` (127.0.0.1:3240)

Dienst im selben Compose: MCP-Server (Streamable HTTP, `http://127.0.0.1:3240/mcp/`) mit den Werkzeugen
`wissen_suchen(frage, anzahl=8)` und `wissen_stand()`, dazu `GET /gesund`. Bindet nur Loopback, Host-Netz,
kein `ports:`, also keine Firewall-Regel noetig. Quelle: `spaces/rowboat/wissen_suche/`.

- Ein Indexer-Faden gleicht alle 10 min `knowledge/` (nur lesend als `/wissen` eingehaengt) mit dem Index
  `~/rowboat-headless/wissen-index/wissen.sqlite` ab und schickt geaenderte Notizen an Ollama am PC
  (`OLLAMA_URL`, Vorgabe `http://100.84.127.84:11435` = `tailscale serve` am PC, Modell
  `qwen3-embedding:0.6b`, `num_ctx` 2048). Auf der VM laeuft kein Modell.
- Ist der PC aus (Aufbau > 3 s, Gesamtzeit > 15 s, 5xx/404, Verbindung zurueckgesetzt), bleiben Notizen
  `offen` und die Suche antwortet per Stichwort mit festem Hinweis. Noch nicht eingebettete Notizen werden
  auch bei Bedeutungssuche per Stichwort ergaenzt.
- Fehlt mehr als die Haelfte der indexierten Notizen (leerer oder fehlender Mount), loescht der Indexer
  nichts und schreibt eine Warnung ins Log.
- Wechseln `EMBED_MODELL` oder `EMBED_DIM`, baut sich der Index von selbst neu auf.

`bauen.ps1` kopiert `wissen/` (Dockerfile, requirements.txt) und das Paket nach `~/rowboat-headless/wissen/`
und legt `wissen-index/` (0700) an. Start und Probe (auf der VM, nur mit Betreiber-OK):

```sh
cd ~/rowboat-headless && docker compose up -d --build rowboat-wissen   # Rowboat-Server bleibt unberuehrt
curl -s http://127.0.0.1:3240/gesund                                   # {"ok":true}
docker compose logs -f rowboat-wissen                                  # Runden: eingebettet / offen / PC erreichbar
```
