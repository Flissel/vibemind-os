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
docker compose up -d --build     # starten (baut node-pty im Image; rowboat-auftraege nur mit --profile auftraege)
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

### Diagnose im Container

Am 2026-10-26 lief eine Ad-hoc-Probe (`docker exec -i rowboat-headless-rowboat-wissen-1 python -`, Skript
ueber stdin) 16 Tage lang mit 100 % CPU weiter. Sie suchte die Index-Datei rekursiv ab `/` und lief dabei
endlos durch die Symlink-Zyklen in `/sys` (`node0 -> memoryN -> node0`). Ihr Aufrufer war laengst
abgebrochen. Seitdem gilt fuer jede Probe in diesem und jedem anderen Container:

- **Nie rekursiv ab `/` suchen.** `glob("**", recursive=True)`, `os.walk(..., followlinks=True)` und
  `find -L` folgen den Zyklen in `/sys` endlos. Pfade stehen in der Konfiguration. Die Index-DB liegt
  unter `WISSEN_INDEX` (`/index/wissen.sqlite`), die Notizen unter `WISSEN_ORDNER` (`/wissen`).
- **`timeout` gehoert in den Container.** Ein `timeout` um den `docker`-Client oder ein abgebrochener
  Tool-Aufruf beendet nur den Client. Der per `docker exec` gestartete Prozess laeuft verwaist weiter.
- **Nach jeder abgebrochenen Probe mit `docker top` nach Resten schauen.** Das Image hat kein `procps`,
  deshalb geht `kill` nur ueber die Shell.

```sh
C=rowboat-headless-rowboat-wissen-1
docker exec -i $C timeout 30 python - <<'PY'
import os, sqlite3
db = sqlite3.connect(f"file:{os.environ['WISSEN_INDEX']}?mode=ro", uri=True)
print(db.execute("select count(*) from sqlite_master").fetchone())
PY
docker top $C                                     # nur der Dienst, kein `python -`
docker exec $C sh -c 'kill <pid>'                 # Rest beenden (PID aus Sicht des Containers)
```

`cpus: "1.0"` in `compose.yml` begrenzt einen solchen Ausreisser zusaetzlich auf einen Kern.

## HTML-Konzepte (Konzept-Flaeche)

Seit Fork `40ba520b` (14.11.2026) schreibt der Rowboat-Agent klickbare Ein-Datei-Seiten nach
`knowledge/Konzepte/*.html` (hoechstens 1 MB). Die App zeigt sie neben dem Chat, sie sind Knoten im
Wissensgraphen, durchsuchbar (`rowboat-wissen` liest den sichtbaren Text) und werden wie Notizen gespiegelt.
Entwurf: `Vibemind_V1/docs/superpowers/specs/2026-11-13-konzept-flaeche-rowboat-design.md`.

- **Geltungsbereich:** alle Schutzregeln gelten nur fuer HTML unter `knowledge/`. HTML an anderen Orten des
  Arbeitsordners (z. B. Berichte von Hintergrundaufgaben) hat sie nicht.
- **Kein Netz:** der Server liefert Wissens-HTML mit einer Kein-Netz-Regel aus (`default-src 'none'`, nur
  Inline-Skript/-Stil, Bilder/Schrift/Medien nur als `data:`/`blob:`, `sandbox allow-scripts`). In der App
  laeuft die Seite in einem abgeschotteten Gast (eigene Sitzung `konzept`, toter Proxy, WebRTC nur ueber den
  Proxy, keine Dateien, keine Fenster, keine Navigation). Kann der tote Proxy beim Start nicht bestaetigt
  werden, zeigt die App statt der Seite "Konzepte lassen sich hier nicht anzeigen.".
- **Links und Notizen** aus einer Seite oeffnen sich nie von selbst: die Seite bittet, die App fragt in der
  Kopfzeile der Flaeche nach ("Link oeffnen? (Adresse unten)").
- **Bekannter Rest, vom Betreiber am 14.11.2026 angenommen:** `<link rel="dns-prefetch">` in einer Seite
  loest eine Namensaufloesung beim Betriebssystem aus. Darueber koennte eine Seite Daten in Rechnernamen
  nach aussen tragen (braucht einen Namensserver des Angreifers). Keine erprobte Einstellung von
  Electron 39 stellt das ab.
- **Eingebettet in VibeMind (Voice-App):** dort werden Konzepte nicht ausgefuehrt, es erscheint nur der
  Hinweis. Ansehen geht in Rowboats eigenem Fenster.
- **Versucht etwas, den Gast umzulenken,** wird er beendet: "Dieses Konzept wurde angehalten." mit
  "Neu laden".
- **Rueckweg:** Pin in `spaces/rowboat/rowboat/UPSTREAM.md` auf Fork `2981a0a7` zuruecksetzen
  (`sync_desktop.py`), PC-App und Server neu bauen. Auf der VM liegt der vorige Server-Stand als
  `~/rowboat-headless/app.vor-konzept-20261114`. Vorhandene Dateien unter `knowledge/Konzepte/` bleiben
  liegen; die alte App zeigt sie als gewoehnliche Dateien.

## Werkzeuge `rowboat-werkzeuge` (127.0.0.1:8150)

MCP-Server `vibemind-werkzeuge` (Streamable HTTP, `http://127.0.0.1:8150/mcp`, ohne Schraegstrich:
mcp 1.27 leitet `/mcp/` per 307 um) mit zwoelf nur lesenden Werkzeugen: `github_repos`, `github_issues`,
`github_prs`, `github_commits`, `github_ci`, `github_datei`, `github_ueberblick`, `gitea_repos`,
`gitea_issues`, `gitea_commits`, `gitea_datei`, `pc_suchen`; dazu `GET /health`.
Quelle: `werkzeuge/`, Skill: `skills/zentrale-werkzeuge/`, Eintrag fuer `config/mcp.json`: `mcp-eintrag.json`.

**Der Endpunkt ist ohne Anmeldung.** Das ist vertretbar, weil er nur Loopback bindet (Host-Netz, kein
`ports:`, nur Prozesse auf der VM erreichen ihn) und nur liest. Wer auf der VM Code ausfuehren kann,
kann ihn also benutzen, aber weder schreiben noch die Tokens sehen.

- Geheimnisse nur in `werkzeuge.env` (0600, `env_file` mit `required: false`), Kopie aus dem Tresor am PC
  per `rowboat-zugang speichern|abgleichen <gruppe>` / `rowboat-zugang pc-schluessel` (ssh-stdin in
  `werkzeuge-zugang-eintragen.sh`; behaelt nur die juengste Sicherung `werkzeuge.env.vor-*`).
- GitHub-Token je Besitzer: fein granuliert (`github_pat_…`), 90 Tage, alle Repos des Kontos, nur
  **Read**: Metadata, Contents, Issues, Pull requests, Actions (fuer `github_ci`), Commit statuses.
  Kein Write-Recht – auch nicht Issues oder Actions; Schreiben gehoert nicht in diesen Dienst.
- Gitea (`GITEA_URL`, Vorgabe `http://127.0.0.1:3002`, Container `debian-coding-gitea-1`, spaeter
  Sicherungsziel): ein Token `GITEA_LESEN` des Kontos `vibemind` mit nur `read:repository,read:issue,
  read:user,read:organization`, erzeugt per `rowboat-zugang gitea-token` am PC (Wert nur Tresor -> VM).
  Startprobe: sechs POSTs mit leerem Koerper (`/user/repos`, `/repos/migrate`, `/orgs`, `/admin/users`,
  Repo-`issues`/`labels`); gemessen an Gitea 1.23 am 26.10.: Lese-Token 403 ueberall, Schreib-Token 422.
  Log: `Zugang Gitea: <zustand>`.
- Beim Start prueft der Dienst jeden GitHub-Token an einem privaten Repo mit sechs Schreibproben
  (leerer Koerper, bei `git/refs` gueltig geformt mit Null-SHA, weil GitHub dort zuerst das Schema prueft); nur wenn alle 403 liefern, ist der Besitzer freigegeben. `unklar` wird nach 30 min
  erneut geprueft, `darf_schreiben`/`abgelehnt` bleiben bis zum Neustart gesperrt.
  Log: `Zugang <Besitzer>: <zustand> (Token läuft ab JJJJ-MM-TT)`.
- Telegram/WhatsApp duerfen nur MCP-Werkzeuge aus einer Freigabeliste aufrufen (Fork:
  `DEFAULT_CHANNEL_READ_ONLY_MCP_TOOLS` in `packages/shared/src/channels.ts`). Seit 26.10. setzt die VM
  sie selbst: `~/rowboat-data/config/channels.json` -> `readOnlyMcpTools` (16 Eintraege, inkl.
  `github_ci` und `gitea_*`; wird je Nachricht gelesen, kein Neustart). Dieser Schluessel ERSETZT die
  Vorgabe des Forks - ein neues Werkzeug muss dort mit eingetragen werden. Die Fork-Vorgabe ist seit
  Fork `99943af5` gleich; `tests/test_zentrale_skill.py` meldet rot, wenn ein Werkzeug aus `server.py` darin
  fehlt.
- `pc_suchen` leitet an den PC weiter (`100.84.127.84:8149`): keine Verbindung = "PC ist aus",
  Verbindung ohne Antwort binnen 20 s = "PC antwortet nicht".

```sh
cd ~/rowboat-headless && docker compose up -d --build rowboat-werkzeuge   # nur mit Betreiber-OK
curl -s http://127.0.0.1:8150/health                                       # {"status":"ok"}
docker compose logs rowboat-werkzeuge | grep Zugang                        # Ergebnis der Schreibprobe
```

## Aufträge `rowboat-auftraege` (127.0.0.1:8156)

MCP-Server `rowboat-auftraege` (Streamable HTTP, `http://127.0.0.1:8156/mcp`, dazu `GET /health`) mit genau einem
Werkzeug, `video_auftrag_aus_konzept(pfad)`: Er liest ein Videokonzept `knowledge/Konzepte/**/*.html`
(höchstens 1 MB, nie unter `knowledge/Kanal/`) selbst aus dem nur lesend eingehängten Wissensordner, prüft den
Datenblock wie die App (`auftraege/konzept_lesen.py`, Nachbau von `readKonzept`/`checkVideoKonzept`, gemessen an
den 236 Fällen in `tests/konzept_vektoren.json`) und legt in Sales eine **vorbereitete Übergabe** an
(`POST /api/auftraege/video`, `SALES_UI_URL`, Vorgabe `http://127.0.0.1:8791`). Quelle: `auftraege/`, Konzept-Fläche
Teil B (`Vibemind_V1/docs/superpowers/plans/2026-11-14-konzept-flaeche-teil-b.md`, Aufgaben 5 und 10).

- **Das Ja sitzt in Sales** (Spec Abschnitt 14): Der Dienst erzeugt keinen Auftrag. Sales antwortet
  `{"status": "vorbereitet", "vorgang": "<8 hex>", "link": "<URL>"}`; der Agent nennt den Link. Den Auftrag legt
  Sales erst an, wenn Felix die Übergabe auf der Werkbank-Seite hinter seinem Login mit „Ja, übergeben" bestätigt.
  Übergeben wird der Stand, den Sales beim Vorbereiten gespeichert hat. Vorbereitete Übergaben verfallen nach 24 h,
  je Laden sind höchstens 5 offen (429), dasselbe Konzept nicht zweimal (409).
- **Was zum Modell zurückgeht, ist geprüft:** nur `status`, `vorgang` (genau 8 Kleinbuchstaben-Hex) und `link`
  (http(s), höchstens 300 Zeichen, nur druckbares ASCII, ohne Zugangsdaten; ist `SALES_UI_ANZEIGE_URL` gesetzt, muss
  der Link genau auf deren Ursprung zeigen). Sätze von Sales höchstens 300 Zeichen, ohne Steuer- und Formatzeichen.
  Alles andere: „Die Annahme hat unerwartet geantwortet. Bitte auf der Werkbank-Seite nachsehen."
- **Pfadregel** (gleich der Sales-Grenze): genau `knowledge/Konzepte/…/<Name>.html`, höchstens 500 Zeichen, `/` als
  Trenner, keine leeren, `.`- oder `..`-Segmente, kein `\`, kein `:`, keine Steuerzeichen, kein einzelnes Surrogat,
  kein Segment mit Punkt oder Leerzeichen am Ende. Kein Segment ab `Konzepte` (auch `Konzepte` selbst) darf ein
  Symlink oder eine Junction sein; gelesen wird nur eine reguläre Datei, einmal geöffnet (`O_NOFOLLOW|O_NONBLOCK`)
  und am offenen Griff geprüft (dieselbe Datei wie beim Prüfen, Größe). Der Rumpf an Sales darf 64 KiB nicht
  überschreiten („Das Konzept ist zu groß für die Übergabe.", es geht nichts hinaus).
- **Mit Anmeldung:** außer `GET /health` braucht jede Anfrage `Authorization: Bearer <Dienstschlüssel>`, sonst 401.
  Der Agent erreicht den Dienst (der Schlüssel steht in Rowboats `config/mcp.json`) und kann Übergaben ungefragt
  vorbereiten; ohne den Klick von Felix in Sales bleiben sie folgenlos.
- **Nie in einer Kanal-Freigabeliste:** `video_auftrag_aus_konzept` gehört weder in die Fork-Vorgabe
  `DEFAULT_CHANNEL_READ_ONLY_MCP_TOOLS` noch in `~/rowboat-data/config/channels.json` -> `readOnlyMcpTools`
  (aus Telegram/WhatsApp gibt es keine Übergabe). `tests/test_zentrale_skill.py` prüft die Vorgabe.
- **Kein Wiederholen:** keine Verbindung -> „Die Annahme ist gerade nicht erreichbar. Es wurde nichts vorbereitet.";
  jeder Fehler vor dem Senden sagt ebenfalls „Es wurde nichts vorbereitet." Nur wenn die Anfrage gesendet sein
  könnte (20 s ohne Antwortbeginn, Abbruch, Antwortkörper nicht binnen 5 s vollständig) -> „… ist unklar – bitte auf
  der Werkbank-Seite nachsehen …". Eine vollständig empfangene Antwort wird nie verworfen. 400/409/413/429 geben
  den Satz von Sales weiter, 401 sagt, dass Sales den Schlüssel nicht angenommen hat.
- **Protokoll** `~/rowboat-headless/auftraege-log/auftraege.log` (dreht bei 1 MB, drei Vorgänger): je Aufruf Zeit,
  Pfad und Ergebnis (`vorbereitet <vorgang>` oder `fehler <art>`); nie ein Schlüssel, nie Konzeptinhalt, nie der Link.
- **Container gehärtet:** nur lesbares Wurzeldateisystem (`read_only`, `/tmp` als tmpfs, schreibbar nur
  `auftraege-log/`), `cap_drop: [ALL]`, `no-new-privileges`, uid 1000, `pids_limit` 64.
- **Eigenes Compose-Profil `auftraege`:** `docker compose up -d` für den ganzen Stapel lässt den Dienst aus und
  scheitert so nicht an fehlenden Schlüsseldateien. Gestartet wird er nur mit `--profile auftraege` (s. u.).
- Port 8156 ist ein Port des Mini-PC (nur Loopback). Die Port-Liste `vibemind.config.json` im äußeren Repo führt
  die Ports des PC.

Dateien auf der VM unter `~/rowboat-headless/` (alle 0600, keine im Repo, keine im Image; `bauen.ps1` legt nur
`auftraege-log/` an, nie eine Schlüsseldatei):

| Datei | Inhalt | Woher |
|---|---|---|
| `auftraege-zugang` | Dienstschlüssel (Bearer am MCP-Endpunkt) | auf der VM erzeugt, s. u. |
| `sales-schluessel` | Schlüssel der Herkunft `rowboat` der Sales-Annahme | Sales: `python -m freigabe_schluessel anlegen rowboat` (sales-mcp) |
| `auftraege.env` | `AUFTRAEGE_LADEN_ANZEIGE=<Laden>` (Pflicht, steht in der Werkzeugbeschreibung) und `SALES_UI_ANZEIGE_URL=<Adresse der Sales-Oberfläche>` (optional, prüft die Links); kein Geheimnis | Betreiber |

Beide Schlüssel liest der Dienst beim Start: ein neuer Schlüssel braucht einen Neustart des Dienstes. Fehlt eine
Datei, legt compose den Container nicht an; ist sie leer oder fehlt `AUFTRAEGE_LADEN_ANZEIGE`, startet der Dienst
nicht und nennt nur den Variablennamen.

Schlüssel anlegen (gibt nichts aus; der Sales-Schlüssel wird verdeckt eingefügt, dann Enter). Läuft in jeder
POSIX-Shell (`sh`/`dash`, nicht nur `bash`): Die Eingabe wird mit `stty -echo` verdeckt, die Anzeige wird auch bei
Abbruch (Strg+C) wieder eingeschaltet. Jede Datei entsteht erst als `.neu` mit 0600 und ersetzt das Ziel nur,
wenn sie vollständig und nicht leer ist; ein Rest `.neu` aus einem abgebrochenen Lauf wird vorher gelöscht, damit
nie eine Datei mit alten Rechten weiterlebt. Kein Befehl gibt einen Schlüssel aus.

```sh
cd ~/rowboat-headless
rm -f auftraege-zugang.neu sales-schluessel.neu
( umask 077; python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > auftraege-zugang.neu \
    && mv -f auftraege-zugang.neu auftraege-zugang )
( umask 077; trap 'stty echo' EXIT; trap 'exit 1' INT TERM; stty -echo; IFS= read -r k; stty echo; echo
  [ -n "$k" ] && printf '%s\n' "$k" > sales-schluessel.neu && mv -f sales-schluessel.neu sales-schluessel )
( umask 077; printf 'AUFTRAEGE_LADEN_ANZEIGE=%s\n' 'Felix (fin2gether)' > auftraege.env )
ls -l auftraege-zugang sales-schluessel auftraege.env       # alle -rw------- ; Inhalt nie anzeigen
```

Eintrag in Rowboats `config/mcp.json` (Vorlage `mcp-eintrag.json`, Platzhalter `<AUS-DATEI auftraege-zugang>`):
das Skript setzt den Wert aus der Datei ein, schreibt `mcp.json` mit 0600 und gibt den Schlüssel nie aus. Ein Rest
`.mcp.json.neu` wird erst gelöscht, dann neu mit `O_EXCL` und 0600 angelegt (nie eine alte Datei mit anderen Rechten).

```sh
python3 - <<'PY'
import json, os, pathlib
h = pathlib.Path.home()
eintrag = json.loads((h / "rowboat-headless/mcp-eintrag.json").read_text())["rowboat-auftraege"]
eintrag["headers"]["Authorization"] = "Bearer " + (h / "rowboat-headless/auftraege-zugang").read_text().strip()
ziel = h / "rowboat-data/config/mcp.json"
cfg = json.loads(ziel.read_text()) if ziel.exists() else {}
cfg.setdefault("mcpServers", {})["rowboat-auftraege"] = eintrag
neu = ziel.with_name(".mcp.json.neu")
neu.unlink(missing_ok=True)
with os.fdopen(os.open(neu, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as f:
    json.dump(cfg, f, indent=2)
os.replace(neu, ziel)
print("rowboat-auftraege in mcp.json eingetragen")
PY
```

Start und Probe (nur mit Betreiber-OK). Der Dienst liegt im Profil `auftraege`; ohne `--profile auftraege` startet
compose ihn nicht:

```sh
pwsh -File spaces/rowboat/deploy/headless/bauen.ps1 -NurAuftraege            # am PC: nur dieser Kontext
cd ~/rowboat-headless && docker compose --profile auftraege up -d --build rowboat-auftraege   # auf der VM
curl -s http://127.0.0.1:8156/health                                         # {"status":"ok"}
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8156/mcp   # 401 ohne Schlüssel
tail -n 5 ~/rowboat-headless/auftraege-log/auftraege.log                     # je Aufruf eine Zeile
```

**Rückweg:** `docker compose --profile auftraege stop rowboat-auftraege && docker compose --profile auftraege rm -f
rowboat-auftraege`, den Eintrag `rowboat-auftraege` aus `~/rowboat-data/config/mcp.json` nehmen und in Sales den
`rowboat`-Schlüssel sperren. Offene vorbereitete Übergaben verfallen in Sales nach 24 h (oder dort mit „Nein"
verwerfen). Bestehende Aufträge und Konzepte bleiben unberührt; `auftraege-zugang`, `sales-schluessel` und das
Protokoll bleiben liegen, bis der Betreiber sie löscht. Ein späteres `docker compose up -d` ohne Profil ist davon
nicht betroffen.
