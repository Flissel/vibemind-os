# Teilprojekt 1: voice-App (Multiverse) wiederbeleben (Design)

Stand: 2026-10-15. Teil 1 von 2. Teil 2 ist die Rowboat-Einbettung per IPC-Durchreichen in den Roarboot-Space. Es braucht eine laufende voice-App und ist ein eigener Spec-und-Plan-Zyklus.

## Ziel (User)

Die voice-Electron-App mit der Multiverse-3D-Ansicht wird wieder die Haupt-UI von VibeMind und startet ueber den Launcher. Mit dem heutigen Aufbau darf sie nicht kollidieren:
- n8n und MiroFish laufen auf der Proxmox-VM.
- Das Brain laeuft als Swarm-Dienst.
- OpenFang ist ein nativer Prozess mit Watchdog.

Erfolg heisst: Die App startet ohne Absturz, die 3D-Ansicht und die Space-Navigation funktionieren, und die Overlays laden, soweit ihre Dienste existieren.

Ausserhalb des Umfangs:
- Voice-Dialog (OpenAI Realtime, kein API-Budget)
- tote Python-Werkzeuge hinter AgentFarm und Flowzen
- Ausraeumen der 207 toten Importe
- Rowboat-Einbettung (Teil 2)

## Ist-Zustand (gemessen 15.10., rein lesend)

Repo: `Flissel/VibeMind-VoiceDialog`, Submodul `vibemind-os/voice`. Pin `a506ca2` auf `codex/laura-video-interface-embed`, 50 Commits vor `main`, 2 dahinter.

**Ursache des Absturzes:** Seit 09.07. bricht das Python-Backend beim Import ab. `voice/python/electron_backend.py:140-141` verwendet `SpaceColors.ROWBOAT`, `swarm/logging/space_logger.py:40` kennt nur noch `ROARBOOT` (Commit `6010f3e`). Die 7 `ipc/*`-Module importieren `electron_backend` erneut, dieselbe Stelle. Von 207 toten Importen liegen etwa 8 auf dem Startpfad, alle abgefangen.

**Fenster unabhaengig vom Backend:** Das Fenster haengt nicht am Python-Backend. `createWindow()` (`main.js:3250`) laeuft vor `startPythonBackend()` (`main.js:3465`). Am 02.09. lief die Multiverse-Ansicht trotz Python-Exit 1. Ohne Backend fehlen Bubbles, Canvas, der Wizard, der Medien-Server `:8977` und alle `sendToPython`-RPCs.

**Nebenwirkungen beim Start, meist ohne Schalter:**
- **Docker-Aufraeumen:** `docker rm -f` auf alle Container mit Praefix `generation-` oder `project-` (`port-allocator.js:193-215`). Beim Beenden zusaetzlich `claude-runner` (`docker-manager.js:786`).
- **Media-Compose:** Startet synchron vor dem Fenster, Timeout 15 s (`main.js:3237-3246`).
- **n8n:** Lokales n8n-Compose mit Besitzer-Anmeldung, fest eingetragene Zugangsdaten gegen `N8N_API_URL`, das inzwischen auf die VM zeigt (`main.js:3361-3421`).
- **MiroFish:** Lokales MiroFish plus Neo4j bauen und starten (`main.js:3427-3447`).
- **OpenFang:** Antwortet `/api/health` nicht innerhalb 1 s, startet die App ein zweites `openfang.exe` und beendet es beim Schliessen per `taskkill /T /F` (`openfang-manager.js:46-162`).
- **Brain:** `show()` startet ein eigenes Brain auf dem fest eingetragenen Port 5000 und ignoriert `SKIP_BRAIN_SPAWN` (`brain-manager.js:63`, `:185-258`, `:300-304`).
- **Zombie-Kill:** Beendet, was auf `:8099` oder `:8009` lauscht (`main.js:440-460`).
- **Bei laufendem Python zusaetzlich:**
  - Hard-Delete von bis zu 200 Rowboat-Mongo-Quellen und 200 Vault-Ordnern (`publishing/__init__.py:223-244`).
  - Bubble-Sync-Worker A/B und Reformat gegen die lokale DB, obwohl die geplante Aufgabe Worker A schon betreibt.
- **Startbloecke:** `runExternalStartup` (`startup-policy.js`) faengt keine Fehler ab. Eine fehlende Laura-Datei wuerde Python, n8n, MiroFish, die Rowboat-Bridge und die Shortcuts still ueberspringen.

**Launcher (`scripts/vibemind-start.ps1:647-714`, aeusseres Repo):**
- Startet `electron.exe --remote-debugging-port=9223` im Haupt-Checkout `vibemind-os/voice/electron-app`.
- Setzt `SKIP_BRAIN_SPAWN=1`, `BRAIN_PORT=5000`, `DASHBOARD_DEV_URL=http://localhost:5173` und `VIBEMIND_ENABLE_CDP=1`.
- Auf `:5173` laeuft inzwischen der Rowboat-Vite-Server.

**Voraussetzungen:** `node_modules` vorhanden (Electron 33.3.1), `voice/.venv312` vorhanden (Python 3.12.0). `.env`-Dateien vorhanden.

## Design

### 1. Startprofil (`electron-app/startup-policy.js`)

Die bestehende Policy wird um benannte Nebenwirkungen erweitert. `VIBEMIND_STARTUP_ALLOW` ist eine Komma-Liste. Ohne Eintrag ist eine Nebenwirkung aus. Unbekannte Namen werden mit Warnung ignoriert. `VIBEMIND_E2E_ISOLATED_STARTUP=true` bleibt und schaltet weiter alles aus.

| Name | Wirkung, wenn erlaubt | Verhalten, wenn aus (Vorgabe) |
|---|---|---|
| `docker-cleanup` | `docker rm -f` auf `generation-*`/`project-*`; `claude-runner` beim Beenden | nichts loeschen, loggen, was geloescht worden waere |
| `media-compose` | Media-Compose vor dem Fenster | ueberspringen |
| `n8n-local` | lokales n8n-Compose plus Besitzer-Anmeldung | ueberspringen, keine Anmeldung |
| `mirofish-local` | lokales MiroFish plus Neo4j | ueberspringen; das Overlay nutzt `MIROFISH_URL` |
| `openfang-spawn` | OpenFang starten und beim Beenden toeten | nur uebernehmen; fehlt es, Statuskarte, kein Start, kein `taskkill` |
| `brain-spawn` | eigenes Brain starten | nur uebernehmen auf `BRAIN_PORT`; fehlt es, Statuskarte, keine Neustart-Schleife |
| `rowboat-autoclean` | Start-Sync loescht Mongo-Quellen und Vault-Ordner | Python bekommt `VIBEMIND_ROWBOAT_AUTOCLEAN=0` |
| `bubble-sync` | Worker A/B und Reformat im Backend | Python bekommt `VIBEMIND_BUBBLE_SYNC_ENABLED=0` und `VIBEMIND_CANVAS_REFORMAT_ENABLED=0` |
| `zombie-kill` | Prozesse auf `:8099`/`:8009` beenden | nur loggen |

Die App setzt die Python-Schalter beim Start des Backends aus dem Profil. Das Profil gewinnt gegen `.env`.

`runExternalStartup` faengt Fehler je Block ab, loggt sie und setzt mit dem naechsten Block fort.

Beim Start schreibt die App eine Zeile `[Startprofil] erlaubt=[…] uebersprungen=[…]` ins Log.

`voice/python/electron_backend.py:140-141` wird von `ROWBOAT` auf `ROARBOOT` umgestellt.

### 2. Betriebs-Checkout und Launcher

**Laufzeit-Checkout:**
- Ort: `C:\Users\User\ClaudeWork\vibemind-betrieb`, ein vibemind-os-Klon auf dem `master`-Pin, nur das Submodul `voice` initialisiert.
- `spaces/rowboat/rowboat` liegt so neben `voice`, wie es die Rowboat-Pfade fuer Teil 2 erwarten.
- Der Haupt-Checkout `vibemind-os` (detached, fremde Aenderungen) und `vos-runtime` (MCP `knowledge`) bleiben unberuehrt.

**Herkunft der Aenderungen:**
- voice: Branch `feat/startprofil` in `Flissel/VibeMind-VoiceDialog`, abgezweigt von `a506ca2`, gemergt nach `codex/laura-video-interface-embed` (Pin-Branch).
- Danach ein Pin-Update in vibemind-os `master`.

**Update-Skript `scripts/voice_betrieb_update.ps1` (aeusseres Repo):**
1. Bricht ab, wenn der Laufzeit-Checkout eigene Aenderungen hat.
2. `git fetch`, Checkout des `origin/master`-Pins, `git submodule update --init voice`.
3. `npm ci` in `voice/electron-app`, nur wenn sich `package-lock.json` geaendert hat.
4. `pip install -r` in `voice/.venv312`, nur wenn sich die Requirements geaendert haben. Fehlt die venv, wird sie mit Python 3.12 angelegt.
5. ClawPort bauen, wenn die Quellen vorhanden sind.
6. Ausgabe: alter und neuer Stand, was neu gebaut wurde.

**`.env`:** `main.js` liest `VIBEMIND_ENV_FILE`, falls gesetzt. Sonst gilt der bisherige relative Pfad. Secrets werden weder kopiert noch verschoben.

**Launcher Phase 5 (`scripts/vibemind-start.ps1`):**
- `VIBEMIND_VOICE_DIR`: Vorgabe ist `vibemind-betrieb\voice`, wenn vorhanden, sonst der alte Pfad.
- Setzt `VIBEMIND_ENV_FILE=<Vibemind_V1>\.env`, `VIBEMIND_STARTUP_ALLOW=` (leer), `BRAIN_PORT=5000` und `MIROFISH_URL=http://100.67.177.45:3000`.
- `DASHBOARD_DEV_URL` wird nicht mehr gesetzt.
- CDP `:9223` bleibt.

**Phase 6:** Prueft MiroFish auf der VM statt auf `localhost:5101`.

### 3. Overlays

Eine gemeinsame Pruefung vor jedem `loadURL`/`loadFile`: Datei vorhanden bzw. URL erreichbar (Timeout 2 s). Sonst zeigt das Overlay eine Statuskarte: `<Space> nicht verfuegbar – <Grund>` mit dem geprueften Ziel. Kein Overlay bleibt leer oder zeigt eine fremde App.

| Overlay | Ziel | Teil 1 |
|---|---|---|
| Brain | `http://localhost:${BRAIN_PORT}/brain` | nur uebernehmen (Abschnitt 1) |
| MiroFish | `MIROFISH_URL` (VM-Oberflaeche `:3000`, Tailnet) | Erreichbarkeit pruefen |
| AgentFarm | `agentfarm/dist` | unveraendert |
| Flowzen | lokale HTML-Datei | unveraendert |
| Video/Laura | Laura-`dist` | unveraendert, Statuskarte statt stillem Abbruch |
| SWE Design | `start_dashboard.py` → `:8086` bei Bedarf | unveraendert (Start auf Anfrage) |
| ClawPort | `dashboard/dist` | Update-Skript baut es; sonst Statuskarte |
| Dashboard (Coding Engine) | `DASHBOARD_DEV_URL` oder gebauter Stand | ohne beides Statuskarte |
| eyeTerm | `:8099` | Statuskarte „entfernt“ |
| Rowboat (Roarboot) | – | Statuskarte „kommt mit Teil 2“; Bridge und Renderer werden nicht gestartet |

### 4. Tests

TDD. Fuer Electron `node --test` wie die vorhandenen `*.test.js`, fuer Python pytest.

- **`startup-policy`:**
  - Vorgabe alles aus.
  - Erlaubt nur, was in der Liste steht; unbekannte Namen loesen eine Warnung aus.
  - Ein Fehler in einem Block bricht die Folgebloecke nicht ab.
  - Die Log-Zeile stimmt.
- **Manager mit Attrappen:**
  - Docker-Aufraeumen: bei „aus“ kein `docker rm`.
  - OpenFang: kein Start und kein `taskkill`, wenn es fehlt.
  - Brain: nur uebernehmen auf `BRAIN_PORT`, keine Schleife.
  - n8n und MiroFish: kein Compose-Aufruf, keine Anmeldung.
- **Statuskarte:** bei fehlender Datei, nicht erreichbarer URL und fremder App.
- **Python:**
  - `electron_backend` importiert fehlerfrei (`ROARBOOT`).
  - Bei Profil „aus“ starten Autoclean und Bubble-Sync nicht.
- **Update-Skript:**
  - Abbruch bei eigenen Aenderungen.
  - `npm ci` nur bei geaendertem Lockfile.

## Abnahme (echt, mit dem User)

1. Der Launcher startet die App aus `vibemind-betrieb`; das Fenster kommt ohne Absturz.
2. Das Start-Log zeigt alle Nebenwirkungen als uebersprungen.
3. Gegenprobe:
   - `docker ps -a` ist vorher und nachher gleich.
   - Die OpenFang-PID ist unveraendert.
   - n8n auf der VM: keine neue Besitzer-Anmeldung.
4. Die Multiverse-3D-Ansicht ist da; alle Spaces lassen sich anklicken.
5. Diese Overlays laden: Brain, MiroFish (VM), AgentFarm, Flowzen, Video/Laura und SWE Design. Rowboat, eyeTerm und das ungebaute Dashboard zeigen ihre Statuskarte. ClawPort zeigt den gebauten Stand oder die Statuskarte.
6. Das Python-Backend meldet `python_ready`, ohne Rowboat-Loeschungen und ohne Bubble-Worker.
7. CDP `:9223` antwortet.
8. Beim Beenden bleiben Container und OpenFang unberuehrt.

## Risiken

- **Weitere Fehler zur Laufzeit:** Ist der Import repariert, laufen mehr Pfade im Python-Backend. Statisch wurde nur der Startpfad geprueft; Netz- und DB-Verhalten in den Repository-Konstruktoren und Thread-Importe sind ungeprueft.
- **Repo-Entwicklung:** Der voice-Pin-Branch liegt 50 Commits vor `main`. Die Aenderungen gehen auf den Pin-Branch; das Zusammenfuehren mit `main` ist nicht Teil dieses Projekts.
- **Speicher:** Die App braucht neben Stack und Brain zusaetzlichen Speicher auf dem Windows-Host (Docker-VM 9 GB). Vor der Abnahme wird der freie Speicher gemessen.
