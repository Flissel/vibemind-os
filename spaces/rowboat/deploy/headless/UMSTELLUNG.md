# Umstellung Rowboat auf den Mini-PC (Teil A): Ablauf und Rueckweg

Verbindliche Schrittliste fuer Betreiber und Leitstand. Plan:
`docs/superpowers/plans/2026-10-24-rowboat-zentrale-teil-a-mini-pc.md` (Task 7). Jeder Schritt auf der
VM, jeder Dienststart und jede Aufgabe braucht das OK des Betreibers. Die Reihenfolge ist Pflicht. Ist
eine Abnahme nicht erfuellt, geht es nicht weiter. Dann wird der Fehler behoben oder der Rueckweg
(Schritt 9) genommen.

Kurzformen: **PC** = Windows-Rechner mit `~/.rowboat`, **VM** = `offload-vm` (Mini-PC, Tailnet
`100.67.177.45`), **Repo** = vibemind-os, **aeusseres Repo** = Vibemind_V1, **Betrieb** =
`C:/Users/User/ClaudeWork/vibemind-betrieb`.

## 0. Vorbedingungen

- [ ] vibemind-os `master` mit den Teil-A-Commits ist gepusht.
- [ ] Betrieb ist aktualisiert: im aeusseren Repo `pwsh -File scripts/voice_betrieb_update.ps1`.
  Pruefung:
  `Select-String fern_ziel_laden C:\Users\User\ClaudeWork\vibemind-betrieb\spaces\rowboat\scripts\start_desktop.py`
  findet Treffer.
- [ ] Der vibemind-os-Haupt-Checkout steht auf dem Stand mit diesen Commits, denn die Spiegel-Aufgabe
  laeuft von dort.
- [ ] `bauen.ps1` ist voll gelaufen (ohne `-NurBauen`). Das legt `~/rowboat-data` (0700) und
  `~/rowboat-headless/budget/` an und kopiert `rowboat-firewall.nft`.
- [ ] `~/rowboat-headless/claude-token` liegt auf der VM (Modus 0600). Die Shim-Probe ist gruen:
  `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8121/v1/models` liefert 200 (siehe
  README).
- [ ] nft ist geladen **und dauerhaft eingerichtet (Pflicht)**, siehe README, Abschnitt Firewall.
  Nach einem VM-Neustart wurde geprueft: `sudo nft list table inet rowboat` zeigt die Tabelle, und
  die LAN-Probe laeuft in den Timeout.

## 1. Sicherung

- [ ] Die Aufgabe „VibeMind Rowboat-Wissen sichern“ einmal von Hand laufen lassen. Sie bleibt danach
  **aktiv**, denn der Spiegel ersetzt keine Sicherung.
- [ ] Zusaetzlich ein naechtliches `tar` von `~/rowboat-data` auf der VM, **ohne** `server-key` und
  `cipher-key`. Ist es noch nicht gebaut, wird es als Folgeaufgabe vermerkt (WORKBOARD/Ledger).

## 2. Lokales Rowboat stoppen

- [ ] `Stop-RowboatEmbed` ausfuehren (aus `scripts/lib/vibemind-helpers.ps1`).
- [ ] Pruefen: Es laeuft kein Rowboat-Prozess mehr. `~/.rowboat/server.lock` und `~/.rowboat/embed.json`
  zeigen auf keine lebende PID.

## 3. Umzug

- [ ] `python spaces/rowboat/scripts/rowboat_umzug.py --trocken`: Vorbedingungen erfuellt, Anzahl und
  Groessen plausibel.
- [ ] `python spaces/rowboat/scripts/rowboat_umzug.py`: Exit 0. Im Log steht
  `Pruefsummen: ... Abweichungen 0`.
- [ ] Direkt danach ein Spiegel-Trockenlauf mit
  `pwsh -File spaces\rowboat\scripts\wissens_spiegel_aufgabe.ps1 -DryRun` (oder
  `python spaces/rowboat/scripts/wissens_spiegel.py --trocken`). Erwartet werden etwa 0 Aktionen.
  `knowledge/.git` reist einmal mit, der Spiegel ignoriert es danach.
- Wiederholung nach einem Fehlschlag: Der Umzug ueberschreibt nichts. Der VM-Zielordner
  (`~/rowboat-data`, ohne `server-key`/`cipher-key`) wird **nur nach OK des Betreibers** geleert.

## 4. Server und Shim starten

- [ ] Auf der VM: `cd ~/rowboat-headless && docker compose up -d`.
- [ ] `curl -s -o /dev/null -w '%{http_code}\n' http://100.67.177.45:3220/health` liefert 200.
- [ ] Ein Test-Chat per `/rpc` mit Token (`server-key`) bekommt eine Antwort. Der Token wird dabei nie
  angezeigt und nie ins Log geschrieben.

## 5. Desktop-App im Fern-Modus

- [ ] Fern-Datei `%LOCALAPPDATA%\VibeMind\rowboat-fern.json` per Skript schreiben. Der Key wird dabei
  nicht angezeigt, z. B.:

  ```powershell
  ssh offload-vm 'cat ~/rowboat-data/server-key' | python -c "import json,os,sys; t=sys.stdin.read().strip(); p=os.path.join(os.environ['LOCALAPPDATA'],'VibeMind','rowboat-fern.json'); os.makedirs(os.path.dirname(p),exist_ok=True); open(p,'w',encoding='utf-8').write(json.dumps({'url':'http://100.67.177.45:3220','token':t}))"
  ```

- [ ] Die Aufgabe „VibeMind Rowboat (Einbettung)“ starten.
- [ ] Abnahme:
  - Logzeile `Fern-Modus: http://100.67.177.45:3220`.
  - `python scripts/rowboat_embed_probe.py` (aeusseres Repo) endet mit Exit 0.
  - In `~/.rowboat` gibt es kein `server.lock` mit lebender PID, es laeuft also kein lokaler Kern.

## 6. Wissens-Spiegel einschalten

- [ ] `pwsh -File spaces\rowboat\scripts\wissens_spiegel_aufgabe.ps1 -Install` (aus dem Haupt-Checkout).
- [ ] Abnahme nach den ersten Laeufen (`logs/rowboat/wissens_spiegel.log`):
  - **0** `loeschen_*`.
  - Jedes `konflikt_sichern` laesst sich erklaeren.
  - `nach_vm` fuer PC-Ordner kommt auf der VM an.

## 7. Telegram

- [ ] Der Betreiber legt den Bot bei @BotFather an.
- [ ] Bot-Token und `allowFrom` eintragen, auf einem von zwei Wegen:
  - in der App unter Settings → Mobile, **oder**
  - als Datei `~/rowboat-data/config/channels.json` (Modus 0600), danach
    `docker compose restart rowboat-server`.
- [ ] Die eigene Chat-ID steht in der Ablehnungsantwort des Bots auf die erste Nachricht. Sie kommt in
  `allowFrom`.

## 8. Abnahme

- [ ] Das PC-Rowboat ist aus. Eine Todo-Frage und eine Notiz per Telegram werden beantwortet bzw.
  angelegt.
- [ ] Nachricht aus einem fremden Chat: Es kommt nur die Ablehnungszeile, es gibt keinen Lauf und keine
  Budget-Buchung.
- [ ] Auf der VM laeuft kein Modellprozess, die Shim ruft nur das Abo auf.
- [ ] Das Budget bucht: `~/rowboat-headless/budget/$(date +%F).jsonl` zeigt `pruefung` und `buchung`.

## 9. Rueckweg (Reihenfolge Pflicht)

1. Auf der VM die Container per `docker stop` anhalten (`docker stop $(docker ps -q --filter name=rowboat)`
   oder die beiden Namen einzeln). `docker compose` scheitert ohne `claude-token`.
2. Ein letzter Spiegel-Lauf endet mit Exit 0
   (`pwsh -File spaces\rowboat\scripts\wissens_spiegel_aufgabe.ps1`).
3. Spiegel-Aufgabe entfernen: `pwsh -File spaces\rowboat\scripts\wissens_spiegel_aufgabe.ps1 -Remove`.
4. `todo.md`, `todo/` und `storage/` von der VM in einen **getrennten** PC-Ordner kopieren, z. B.
   `~/rowboat-rueckweg-<datum>/`, **nicht** ueber `~/.rowboat`. Danach `knowledge/_konflikte/` auf der VM
   sichten und von Hand uebernehmen.
5. `~/.rowboat/config/client.json` pruefen: Steht dort `remoteServer`, wird es entfernt.
6. Die Fern-Datei `%LOCALAPPDATA%\VibeMind\rowboat-fern.json` umbenennen (z. B. `.aus`).
7. Die Aufgabe „VibeMind Rowboat (Einbettung)“ starten. Der lokale Kern laeuft wieder.

## Betreiberhinweise

- **PC-eigene Notizen** sind `Projects/`, `Topics/`, `Agents/`, `companys/*` (ausser `Kontakte/`,
  `Newsletter/`, `Wochenbild/`) und die weiteren PC-Ordner der Besitzer-Tabelle. Wer sie in der App,
  per rowboat-ui oder per Telegram aendert, verliert die Aenderung: Der Spiegel ueberschreibt sie, die
  geaenderte Fassung landet in `knowledge/_konflikte/<datum>/` auf der VM.
- Der **OpenAI-Key** reist in `config/models.json` mit auf die VM (0600).
- **Chats** aus `storage/` enthalten Windows-Pfade. Auf der VM fuehren diese Pfade ins Leere.
- **Nicht umgezogen:** `knowledge_graph_state.json`, `note_tagging_state.json`,
  `agent_notes_state.json`, `vibemind/`, `vibemind_sync/`, `mcp-servers/`, `scripts/`, `pre-built/`,
  `runs/`.
- In der `mcp.json`-Kopie auf der VM fehlen `desktop-commander`, `playwright` und `filesystem` (kein
  Shell-/Dateizugriff auf `/data` fuer Telegram-Laeufe). Das PC-Original bleibt unveraendert.
- `vault-map.json` ist im Fern-Modus eingefroren.
- Die **150 Aufrufe am Tag** (`rowboat-server`) teilen sich Telegram und Rowboats Hintergrund-Agenten.
- Der Telegram-Kanal antwortet fremden Chats mit einer Ablehnungszeile.
