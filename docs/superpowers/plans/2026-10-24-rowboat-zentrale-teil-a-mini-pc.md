# Rowboat-Zentrale Teil A – Server auf dem Mini-PC, Telegram, Umzug, App als Ansicht

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rowboat läuft rund um die Uhr ohne Oberfläche auf dem Mini-PC (VM `offload-vm`), antwortet per eigenem Telegram-Bot, das Wissen ist dorthin umgezogen, und die Desktop-App am PC ist nur noch Ansicht im Fern-Modus.

**Architecture:** Der Rowboat-Fork baut `rowboat-server.cjs` (upstream `apps/server`, `npm run build:headless`); er läuft auf der VM in einem Container mit `network_mode: host` (die Host-Prüfung in `server.ts:82-97` lehnt Docker-Bridge und `tailscale serve` mit 403 ab) auf `http://100.67.177.45:3220`, Arbeitsordner `/home/debian/rowboat-data`. Modelle über eine zweite Shim-Instanz mit Claude-CLI auf der VM (loopback `:8121`, Budget-Hülle). Die PC-Programme, die heute in `~/.rowboat/knowledge` schreiben (Brain-Kurator, Marketing, MCP `knowledge`, Discourse), schreiben weiter lokal; ein **Wissens-Spiegel** gleicht PC-Kopie und VM alle 5 min nach einer festen Besitzer-Tabelle ab.

**Tech Stack:** Node 22 (Container `node:22-bookworm`), Docker Compose auf der VM, Python 3.11 (Spiegel, Umzug, Start-Skript, pytest), PowerShell 7 (Aufgaben), ssh `offload-vm`.

**Spec:** `docs/superpowers/specs/2026-10-24-rowboat-geschaeftsdaten-zentrale-design.md` (Teil A = Ziele 1–3, Anti-Ziele, Abschnitt 7). Technik-Erkundung: `C:/Users/User/AppData/Local/Temp/claude/c--Users-User-Desktop-Vibemind-V1/09346339-4318-4647-a968-36a3579400b7/scratchpad/rowboat-headless-survey.md`.

## Global Constraints

- Auf dem Mini-PC läuft **kein Modell**. Claude-CLI ist erlaubt (Betreiber-OK 24.10.).
- Nur **ein** Rowboat-Kern auf dem Wissen: nach der Umstellung startet der PC keinen eigenen Kern mehr (Fern-Modus).
- Telegram: **neuer** Bot-Schlüssel (nicht der Marketing-Bot), `allowFrom` = nur die Chat-ID des Betreibers; Telegram-Läufe ohne Werkzeuge mit Außenwirkung (Spec 7).
- Rowboats Gmail-/Kalender-Abgleich zieht **nicht** mit; `gmail_sync/`, `calendar_sync/` am PC bleiben unangetastet; auf der VM `config/knowledge_sources.json` Quelle `gmail` `enabled:false`, kein Google verbinden.
- Nie mitkopieren: `server-key`, `cipher-key`, `server.lock`, `embed.json`, `.electron-data/`, `engines/`, `config/client.json`, `config/oauth.json`, `config/oauth-clients.json`, `config/gmail-credentials.json`, `config/gdrive-credentials.json`, `config/composio.json`, `config/slack.json`. OAuth wird einmalig neu angemeldet.
- Geheimnisse (Bot-Schlüssel, `server-key`, Claude-Token) nie in argv, Logs oder Commits; Ablage am PC unter `%LOCALAPPDATA%\VibeMind\` bzw. Windows-Anmeldeinformationen, auf der VM Dateien mit Modus 0600.
- Nichts am PC löschen. Der alte Stand von `~/.rowboat` bleibt bis zur Abnahme vollständig erhalten (Rückweg).
- Deploy-Schritte auf der VM und am PC (Container starten, Aufgaben anlegen, Umschalten) nur nach Betreiber-OK; Code-Tasks davor ohne.
- Fork-Änderungen nur im Fork `Flissel/rowboat` Branch `vibemind`, zurück per `spaces/rowboat/scripts/sync_desktop.py`.

## Besitzer-Tabelle des Wissens-Spiegels (Pfade relativ zu `knowledge/`)

| Besitzer | Ordner/Dateien | Grund |
|---|---|---|
| **PC** | `Projects/`, `Agents/`, `Areas/`, `Topics/`, `Bubbles/`, `Domains/`, `Hubs/`, `Coding-Projects/`, `Workflows/`, `System/`, `Videos/`, `vibemind-discourse/`, `companys/*/` außer den drei VM-Ordnern | Brain-Kurator, Discourse, Marketing, PC-Skripte schreiben dort |
| **VM** | alles andere, u. a. `Notes/`, `People/`, `Organizations/`, `Meetings/`, `Diary/`, `Agent Notes/`, `Voice Memos/`, `Templates/`, `Bewerbung/`, `Investor Programs/`, `Company.md`, `Welcome.md`, `companys/*/Kontakte/`, `companys/*/Newsletter/`, `companys/*/Wochenbild/` | Rowboat (Telegram, App) und Teil B schreiben dort |

Regeln: Der Besitzer gewinnt bei Abweichung; Löschen wirkt nur vom Besitzer aus. Eine **neue** Datei auf der Nicht-Besitzer-Seite wird zum Besitzer kopiert (nicht verworfen). Eine Nicht-Besitzer-Änderung an einer bestehenden Datei wird überschrieben, vorher aber nach `knowledge/_konflikte/<JJJJ-MM-TT>/<pfad>` auf der VM gesichert. `todo.md`, `todo/`, `storage/` liegen außerhalb von `knowledge/` und gehören nur der VM (kein Spiegel).

## Review Focus

1. Zwei Seiten ändern dieselbe Datei zwischen zwei Läufen → Besitzer gewinnt, die andere Fassung landet in `_konflikte/`, nichts geht still verloren.
2. VM nicht erreichbar (Tailscale aus, ssh-Timeout) → Spiegellauf bricht ohne Änderung ab, Zustand bleibt, nächster Lauf holt nach; kein halber Abgleich.
3. Leerer oder fehlender Ordner auf einer Seite (z. B. VM frisch, PC-Ordner umbenannt) → kein Massenlöschen: Löschungen über 20 Dateien oder 10 % je Lauf brechen ab und melden.
4. Dateinamen mit Leerzeichen, Umlauten, Klammern (`Agent Notes/`, `2026-10-23 split-vorlage-vibemind.md`) → werden korrekt übertragen und verglichen.
5. Telegram-Nachricht aus fremdem Chat → keine Antwort, kein Lauf (allowFrom); Telegram-Lauf versucht GitHub-Push → Werkzeug nicht verfügbar.

---

### Task 1: Wissens-Spiegel – Abgleichsplan (reine Logik)

**Files:**
- Create: `spaces/rowboat/scripts/wissens_spiegel.py`
- Test: `spaces/rowboat/tests/test_wissens_spiegel.py`

**Interfaces:**
- Produces:
  - `Eintrag = NamedTuple(sha256: str, mtime: float)`; `Bestand = dict[str, Eintrag]` (Schlüssel: POSIX-Pfad relativ zu `knowledge/`, NFC-normalisiert)
  - `besitzer(pfad: str) -> Literal["pc", "vm"]` nach der Besitzer-Tabelle oben
  - `Aktion = NamedTuple(art: Literal["nach_vm", "nach_pc", "loeschen_vm", "loeschen_pc", "konflikt_sichern"], pfad: str)`
  - `abgleich_planen(pc: Bestand, vm: Bestand, zuletzt: Bestand) -> list[Aktion]` (`zuletzt` = gemeinsamer Stand nach dem letzten erfolgreichen Lauf)
  - `class ZuVieleLoeschungen(Exception)`; `loeschgrenze_pruefen(aktionen, gesamt: int, max_anzahl: int = 20, max_anteil: float = 0.10) -> None`

- [ ] **Step 1: Tests schreiben**

```python
def test_besitzer():
    assert wissens_spiegel.besitzer("Projects/x.md") == "pc"
    assert wissens_spiegel.besitzer("companys/VibeMind/Agent-Notizen/a.md") == "pc"
    assert wissens_spiegel.besitzer("companys/VibeMind/Kontakte/bav/Max.md") == "vm"
    assert wissens_spiegel.besitzer("Notes/Einkauf.md") == "vm"
    assert wissens_spiegel.besitzer("Company.md") == "vm"

def test_besitzer_aenderung_gewinnt():          # PC-Datei auf beiden Seiten geändert
    # zuletzt=A; pc=B; vm=C für "Projects/p.md" → [konflikt_sichern, nach_vm]

def test_neue_datei_beim_nichtbesitzer_wandert_zum_besitzer():
    # nur pc hat "Notes/neu.md" (vm-Besitz), zuletzt leer → [nach_vm]

def test_loeschen_nur_vom_besitzer():
    # zuletzt+pc haben "Projects/alt.md", vm fehlt → [nach_vm] (VM darf PC-Besitz nicht löschen)
    # zuletzt+vm haben "Projects/alt.md", pc fehlt → [loeschen_vm]

def test_unveraendert_keine_aktion():
    # gleiche sha auf beiden Seiten → []

def test_loeschgrenze():
    # 21 Löschungen bei 1000 Dateien → ZuVieleLoeschungen; 5 Löschungen bei 20 Dateien (25 %) → ZuVieleLoeschungen

def test_pfade_mit_umlauten_und_leerzeichen():
    # "Agent Notes/Über (neu).md" in NFD auf dem PC und NFC auf der VM gilt als dieselbe Datei
```

- [ ] **Step 2: Laufen lassen, rot** – `python -m pytest spaces/rowboat/tests/test_wissens_spiegel.py -q --basetemp C:/Temp/pt-rb` → FAIL (Modul fehlt)
- [ ] **Step 3: `besitzer`, `abgleich_planen`, `loeschgrenze_pruefen` in `wissens_spiegel.py` umsetzen** – reine Funktionen ohne Ein-/Ausgabe; Vergleich über `sha256`, `mtime` nur zur Anzeige.
- [ ] **Step 4: grün** – gleicher Befehl → alle PASS
- [ ] **Step 5: Commit** `feat(rowboat): Wissens-Spiegel plant den Abgleich nach Besitzer`

### Task 2: Wissens-Spiegel – Übertragung, Zustand, Aufgabe

**Files:**
- Modify: `spaces/rowboat/scripts/wissens_spiegel.py`
- Create: `spaces/rowboat/scripts/wissens_spiegel_aufgabe.ps1`
- Test: `spaces/rowboat/tests/test_wissens_spiegel.py`

**Interfaces:**
- Consumes: Task 1.
- Produces:
  - `bestand_lokal(wurzel: Path) -> Bestand`
  - `bestand_vm(ssh_ziel: str, vm_wurzel: str, laeufer=subprocess.run) -> Bestand` (ein `ssh … python3 -` mit Skript über stdin, liefert JSON)
  - `ausfuehren(aktionen, pc_wurzel: Path, ssh_ziel: str, vm_wurzel: str, laeufer=subprocess.run) -> None` (Übertragung als `tar` über ssh-stdin/stdout, keine Pfade in argv außer den Wurzeln)
  - `lauf(pc_wurzel: Path, ssh_ziel: str, vm_wurzel: str, zustand: Path, trocken: bool = False) -> int` (Exit 0 ok, 2 VM nicht erreichbar, 3 Löschgrenze)
  - CLI: `python -m spaces.rowboat.scripts.wissens_spiegel [--trocken]`; Vorgaben: PC `~/.rowboat/knowledge`, `offload-vm`, VM `/home/debian/rowboat-data/knowledge`, Zustand `%LOCALAPPDATA%\VibeMind\wissens_spiegel\zuletzt.json`, Log `logs\rowboat\wissens_spiegel.log` (äußeres Repo-Root über Env `VIBEMIND_ROOT`).
  - Aufgabe „VibeMind Wissens-Spiegel“ alle 5 min (`pythonw`, `stdin=DEVNULL`), `-Install`/`-Remove` wie `scripts/rowboat_waechter.ps1`.

- [ ] **Step 1: Tests** (mit Fake-`laeufer`, kein echtes ssh):
  - `test_vm_nicht_erreichbar_aendert_nichts` – Fake wirft `TimeoutExpired` → `lauf` gibt 2, Zustandsdatei unverändert, kein Schreiben lokal.
  - `test_zustand_nur_nach_vollem_erfolg` – Fehler bei der 2. von 3 Aktionen → Zustand bleibt alt.
  - `test_trockenlauf_schreibt_nichts` – `trocken=True` → keine `ausfuehren`-Aufrufe, Aktionsliste im Log.
  - `test_tar_uebertraegt_sonderzeichen(tmp_path)` – echte lokale Übertragung zwischen zwei tmp-Ordnern über den Fake, der `tar` lokal ausführt; Datei `Agent Notes/Über (neu).md` kommt bytegleich an.
  - `test_konflikt_wird_gesichert(tmp_path)` – Aktion `konflikt_sichern` legt die Nicht-Besitzer-Fassung unter `_konflikte/<datum>/<pfad>` ab.
- [ ] **Step 2: rot**, **Step 3: umsetzen**, **Step 4: grün** (Befehl wie Task 1)
- [ ] **Step 5: Commit** `feat(rowboat): Wissens-Spiegel uebertraegt per ssh und merkt sich den Stand`

### Task 3: Rowboat-Server auf der VM (Bauen, Container, Start)

**Files:**
- Create: `spaces/rowboat/deploy/headless/Dockerfile`, `spaces/rowboat/deploy/headless/compose.yml`, `spaces/rowboat/deploy/headless/bauen.ps1`, `spaces/rowboat/deploy/headless/README.md`
- Test: `spaces/rowboat/tests/test_headless_deploy.py`

**Interfaces:**
- Produces:
  - `bauen.ps1`: im Betriebs-Checkout `spaces/rowboat/rowboat/apps/x` → `npm run deps`, dann in `apps/server` `npm run build:headless`; überträgt `dist-headless/` per `scp` nach `offload-vm:~/rowboat-headless/app/`.
  - `Dockerfile` (`FROM node:22-bookworm`): `npm install --omit=dev` in `/app` (baut `node-pty`), `CMD ["node","rowboat-server.cjs"]`.
  - `compose.yml`: Dienst `rowboat-server`, `network_mode: host`, `restart: unless-stopped`, Volume `/home/debian/rowboat-data:/data`, Env `ROWBOAT_WORKDIR=/data`, `ROWBOAT_SERVER_PORT=3220`, Healthcheck `GET http://127.0.0.1:3220/health`, `mem_limit: 3g`.
  - VM-Ordner: `/home/debian/rowboat-data` (Besitzer debian, 0700).
- [ ] **Step 1: Test** `test_compose_vertrag` – liest `compose.yml` (yaml) und prüft: `network_mode == "host"`, `ROWBOAT_WORKDIR == "/data"`, Port 3220, `restart == "unless-stopped"`, Healthcheck auf `/health`, kein `ports:`-Abschnitt, kein Geheimnis in `environment`. `test_dockerfile_node22` – erste Zeile `FROM node:22-bookworm`.
- [ ] **Step 2: rot**, **Step 3: Dateien anlegen**, **Step 4: grün**
- [ ] **Step 5: Bauprobe am PC** – `pwsh -File spaces/rowboat/deploy/headless/bauen.ps1 -NurBauen` → `dist-headless/rowboat-server.cjs` existiert.
- [ ] **Step 6: Commit** `feat(rowboat): Rowboat-Server ohne Oberflaeche fuer den Mini-PC`
- [ ] **Step 7 (Betreiber-OK): Start auf leerem Arbeitsordner** – `bauen.ps1`, dann `ssh offload-vm 'cd ~/rowboat-headless && docker compose up -d --build'`. Prüfen: `curl -s http://100.67.177.45:3220/health` → 200; `POST /rpc/sessions:list` ohne Token → 401. Danach `docker compose stop` (Umzug folgt in Task 5).

### Task 4: Shim mit Claude-CLI auf der VM (Budget-Hülle)

**Files:**
- Create: `spaces/rowboat/deploy/headless/shim/Dockerfile`, `spaces/rowboat/deploy/headless/shim/start.sh`
- Modify: `spaces/rowboat/deploy/headless/compose.yml` (Dienst `rowboat-shim`)
- Modify (äußeres Repo, Branch `codex/vibemind-ops-baseline`): `config/agent_budget.yaml` – Eintrag `rowboat-server: {bereich: wissen, klasse: auftrag, modell: sonnet, tagesgrenze: 150, wrapper: shim}`
- Test: `spaces/rowboat/tests/test_headless_deploy.py`

**Interfaces:**
- Consumes: Shim-Skript wie am PC (`:8121`, `SHIM_TOOL_CALLS=1`, reines Python), Budget-Hülle nach Muster `scripts/brain_devops_shim.py` (`VIBEMIND_AGENT=rowboat-server`) samt `scripts/claude_budget.py` und `config/agent_budget.yaml` (in das Image kopiert).
- Produces: Dienst `rowboat-shim` mit `network_mode: host`, lauscht **nur** auf `127.0.0.1:8121`; Claude-Anmeldung über Env `CLAUDE_CODE_OAUTH_TOKEN` aus Datei `/home/debian/rowboat-headless/claude-token` (0600, per `env_file`), Budget-Zähldatei im Volume `/home/debian/rowboat-headless/budget/`.
- [ ] **Step 1: Tests** `test_shim_nur_loopback` (compose: Befehl/Env bindet `127.0.0.1`), `test_shim_token_nur_aus_datei` (kein Token-Wert in compose/Dockerfile, `env_file` zeigt auf `claude-token`), `test_budget_eintrag_rowboat_server` (yaml enthält `rowboat-server` mit `tagesgrenze: 150`).
- [ ] **Step 2: rot**, **Step 3: umsetzen**, **Step 4: grün**
- [ ] **Step 5: Commit** (vibemind-os) `feat(rowboat): Claude-Shim mit Budget-Huelle auf dem Mini-PC`; (äußeres Repo) `chore(budget): rowboat-server 150/Tag`
- [ ] **Step 6 (Betreiber-Schritt):** Betreiber erzeugt am PC mit `claude setup-token` einen Abo-Token; Übergabe ohne Anzeige per `ssh offload-vm 'umask 077; cat > ~/rowboat-headless/claude-token'` aus der Zwischenablage-Datei. Probe: `docker compose up -d rowboat-shim`, `curl -s 127.0.0.1:8121/v1/models` auf der VM → 200; ein Testaufruf → Antwort, Budget bucht 1.

### Task 5: Umzug des Arbeitsordners

**Files:**
- Create: `spaces/rowboat/scripts/rowboat_umzug.py`
- Test: `spaces/rowboat/tests/test_rowboat_umzug.py`

**Interfaces:**
- Consumes: Task 2 (`bestand_lokal`, Übertragung per tar über ssh).
- Produces:
  - `MITNEHMEN = ("knowledge", "todo", "todo.md", "storage", "bg-tasks", "agents", "skills", "projects", "config")`; `NIE` = Liste aus Global Constraints
  - `auswahl(wurzel: Path) -> list[Path]`
  - `config_anpassen(config_ordner: Path) -> None` – `models.json`: Anbieter mit `baseURL` `http://127.0.0.1:8121/v1` → bleibt (VM-Shim lauscht dort); `knowledge_sources.json`: `gmail` `enabled:false`; `channels.json`: `telegram.enabled=false`, `whatsapp.enabled=false`; `server.json`: `{"lanEnabled": true, "port": 3220}`
  - CLI `python -m spaces.rowboat.scripts.rowboat_umzug [--trocken]` → überträgt nach `offload-vm:/home/debian/rowboat-data/`, schreibt danach Zustand des Wissens-Spiegels (`zuletzt.json`) = übertragener `knowledge`-Bestand, und vergleicht Prüfsummen PC↔VM (Ausgabe: Anzahl, Abweichungen = 0)
- [ ] **Step 1: Tests**: `test_nie_wird_nicht_mitgenommen(tmp_path)` (alle NIE-Einträge fehlen in `auswahl`), `test_gmail_sync_bleibt_draussen`, `test_config_anpassen(tmp_path)` (drei Dateien wie oben, andere Schlüssel unverändert), `test_trocken_ueberträgt_nichts`, `test_pruefsummen_abweichung_bricht_ab` (Fake meldet 1 Abweichung → Exit 4, Spiegel-Zustand nicht geschrieben).
- [ ] **Step 2: rot**, **Step 3: umsetzen**, **Step 4: grün**
- [ ] **Step 5: Commit** `feat(rowboat): Umzug des Arbeitsordners auf den Mini-PC`

### Task 6: Desktop-App im Fern-Modus

**Files:**
- Modify: `spaces/rowboat/scripts/start_desktop.py`
- Test: `spaces/rowboat/tests/test_start_desktop.py`

**Interfaces:**
- Produces:
  - `fern_ziel_laden(datei: Path) -> tuple[str, str] | None` – liest `%LOCALAPPDATA%\VibeMind\rowboat-fern.json` `{"url": "...", "token": "..."}`; `None`, wenn die Datei fehlt (dann bleibt alles wie heute, lokaler Kern).
  - In `start()`: ist ein Fern-Ziel da, bekommt **nur der Rowboat-Prozess** `ROWBOAT_REMOTE_SERVER` und `ROWBOAT_REMOTE_TOKEN` in die Umgebung (nie argv, nie Log); der Vault-Map-Lauf entfällt im Fern-Modus.
- [ ] **Step 1: Tests**: `test_fern_ziel_setzt_nur_rowboat_umgebung` (Fake-Popen: Rowboat-Env hat beide Variablen, andere Prozesse nicht; Token nicht in cmd), `test_ohne_fern_datei_wie_bisher`, `test_kaputte_fern_datei_bricht_mit_hinweis_ab` (ungültiges JSON → Exit 2, Meldung ohne Token-Wert).
- [ ] **Step 2: rot**, **Step 3: umsetzen**, **Step 4: grün** (`python -m pytest spaces/rowboat/tests -q --basetemp C:/Temp/pt-rb`)
- [ ] **Step 5: Commit** `feat(rowboat): Desktop-Start im Fern-Modus zum Mini-PC`

### Task 7: Umstellung, Telegram, Abnahme (Betreiber-OK, live)

**Files:**
- Create: `spaces/rowboat/deploy/headless/UMSTELLUNG.md` (Ablauf + Rückweg)
- Modify (äußeres Repo): `WORKBOARD.md` (eigene Zeile), Memory `project_rowboat_ui_cdp.md`

- [ ] **Step 1: Vorher-Sicherung** – `rowboat_knowledge_sichern.ps1` einmal laufen lassen; Aufgabe „VibeMind Rowboat-Wissen sichern“ danach deaktivieren (Spiegel ersetzt sie).
- [ ] **Step 2: Lokale Einbettung stoppen** – `Stop-RowboatEmbed` (Wächter wertet „läuft nicht“ als bewusst).
- [ ] **Step 3: Umzug** – `python -m spaces.rowboat.scripts.rowboat_umzug --trocken`, dann echt; erwartet „Abweichungen 0“.
- [ ] **Step 4: Server + Shim starten** – `docker compose up -d`; `/health` 200; Test-Chat per `POST /rpc` mit Token beantwortet (Modell über VM-Shim).
- [ ] **Step 5: Fern-Datei anlegen** – `server-key` per `ssh offload-vm 'cat ~/rowboat-data/server-key'` direkt in `%LOCALAPPDATA%\VibeMind\rowboat-fern.json` schreiben (Skript, keine Anzeige), URL `http://100.67.177.45:3220`. Aufgabe „VibeMind Rowboat (Einbettung)“ starten; `scripts/rowboat_embed_probe.py` → 0; Multiverse/Roarboot zeigt Wissen und Todo.
- [ ] **Step 6: Wissens-Spiegel** – `--trocken` (erwartet 0 Aktionen), dann Aufgabe installieren.
- [ ] **Step 7: Telegram (Betreiber-Schritt)** – Betreiber legt bei @BotFather einen Bot an und schickt ihm „/start“; Bot-Schlüssel per Datei auf die VM in `config/channels.json` (`telegram.enabled=true`, `allowFrom=[<Chat-ID des Betreibers>]`), Werkzeuge mit Außenwirkung für Telegram-Läufe aus (Spec 7; Umsetzung je nach Rowboat-Einstellung, sonst Fork-Patch als eigene Folgeaufgabe festhalten).
- [ ] **Step 8: Abnahme** (Spec 9, Teil A):
  - PC-Rowboat aus (`Stop-RowboatEmbed`): Telegram „Was ist heute noch auf der Todo?“ → Antwort aus `todo.md`; „Speicher die Notiz: Test Zentrale“ → Notiz unter `Notes/`; nach 5 min am PC in `~/.rowboat/knowledge/Notes/` (Spiegel).
  - Nachricht aus einem zweiten, nicht freigegebenen Chat → keine Antwort.
  - `ssh offload-vm 'ps aux | grep -i -E "ollama|llama|vllm|comfy" | grep -v grep'` → leer (kein Modell).
  - Budget-Zähldatei auf der VM bucht die Telegram-Läufe.
- [ ] **Step 9: Rückweg dokumentiert** (`UMSTELLUNG.md`): Fern-Datei umbenennen → PC startet wieder lokalen Kern auf dem unveränderten `~/.rowboat`; VM-Container stoppen.
- [ ] **Step 10: Abschluss** – WORKBOARD-Zeile schließen, Memory ergänzen, Commit `docs(rowboat): Umstellung auf den Mini-PC`.

## Folgepläne (nicht Teil A)

- Teil B: nächtlicher Geschäftsdaten-Abgleich (Steckbriefe, Newsletter-Archiv, Wochenbild) in die VM-Ordner `companys/*/Kontakte|Newsletter|Wochenbild`.
- Teil C: Bedeutungssuche (Index auf der VM, Embeddings über Ollama am PC, Rückfall Stichwort).
- Teil D: Werkzeuge (GitHub, Docs, Dashboard, PC-Suche über den verbundenen PC), Freigaben per Telegram.
