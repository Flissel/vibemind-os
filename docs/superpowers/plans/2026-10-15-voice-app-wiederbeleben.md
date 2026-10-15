# voice-App (Multiverse) wiederbeleben – Implementierungsplan

> **Fuer agentische Umsetzer:** PFLICHT-SKILL: superpowers:subagent-driven-development (empfohlen) oder superpowers:executing-plans. Schritte als Checkboxen (`- [ ]`).

**Ziel:** Die voice-Electron-App startet ueber den Launcher aus einem eigenen Laufzeit-Checkout ohne Absturz und ohne Nebenwirkungen auf Docker, OpenFang, Brain, n8n/MiroFish (VM) und Rowboat; die Multiverse-Ansicht und die Overlays laden oder zeigen eine Statuskarte.

**Architektur:** Ein Startprofil (`startup-policy.js`, Env `VIBEMIND_STARTUP_ALLOW`) entscheidet ueber neun benannte Nebenwirkungen; jede Stelle, die eine davon ausloest, fragt `policy.isAllowed(name)` und loggt bei „aus“, was sie getan haette. Ein gemeinsamer Overlay-Helfer (`overlay-status.js`) prueft vor jedem `loadURL`/`loadFile` Datei bzw. URL (2 s) und laedt sonst eine Statuskarte. Ein Update-Skript im aeusseren Repo haelt den Laufzeit-Checkout `vibemind-betrieb` auf dem `master`-Pin, der Launcher startet die App von dort.

**Tech-Stack:** Electron 33.3.1 (Node 20 im Main-Prozess), `node --test` (System-Node v24), Python 3.12 (`voice/.venv312`) bzw. pytest 7.4.3 aus `C:\Users\User\Desktop\Vibemind_V1\.venv` (Python 3.11), PowerShell 7 + Pester 6.0.0, git.

**Spec:** `C:\Users\User\Desktop\Vibemind_V1\vibemind-os\.worktrees\setup-agent\docs\superpowers\specs\2026-10-15-voice-app-wiederbeleben-design.md`

---

## Globale Vorgaben

- Neun Nebenwirkungen (exakt diese Namen, diese Reihenfolge): `docker-cleanup`, `media-compose`, `n8n-local`, `mirofish-local`, `openfang-spawn`, `brain-spawn`, `rowboat-autoclean`, `bubble-sync`, `zombie-kill`.
- `VIBEMIND_STARTUP_ALLOW` = Komma-Liste; leer/fehlend = alles aus; unbekannte Namen → Warnung, ignoriert; `VIBEMIND_E2E_ISOLATED_STARTUP=true` schaltet weiter alles aus.
- `VIBEMIND_ENV_FILE` = Pfad der Master-`.env` (Launcher: `C:\Users\User\Desktop\Vibemind_V1\.env`); ohne Wert gilt der alte relative Pfad.
- `VIBEMIND_VOICE_DIR` = voice-Verzeichnis fuer den Launcher; Vorgabe `C:\Users\User\ClaudeWork\vibemind-betrieb\voice`, wenn vorhanden, sonst `<Vibemind_V1>\vibemind-os\voice`.
- `BRAIN_PORT=5000`, `MIROFISH_URL=http://100.67.177.45:3000` (Launcher setzt beide).
- Python-Schalter aus dem Profil: `VIBEMIND_ROWBOAT_AUTOCLEAN`, `VIBEMIND_BUBBLE_SYNC_ENABLED`, `VIBEMIND_CANVAS_REFORMAT_ENABLED` (Profil gewinnt gegen `.env`).
- Laufzeit-Checkout: `C:\Users\User\ClaudeWork\vibemind-betrieb` (vibemind-os-Klon von `https://github.com/Flissel/vibemind-os.git`, `master`-Pin, nur Submodul `voice` initialisiert).
- voice-Arbeit: Branch `feat/startprofil` von `a506ca2` in `Flissel/VibeMind-VoiceDialog`, in einem NEUEN Worktree `C:\Users\User\ClaudeWork\wt-voice-startprofil`; Merge nach `codex/laura-video-interface-embed` nur nach User-OK.
- Aeusseres Repo `C:\Users\User\Desktop\Vibemind_V1`: Branch `codex/vibemind-ops-baseline` (dort Update-Skript, Launcher, WORKBOARD-Claim).
- vibemind-os-Pin-Update auf `master` nur im Worktree `C:\Users\User\Desktop\Vibemind_V1\vibemind-os\.worktrees\setup-agent`, nur nach User-OK.
- NIE schreibend anfassen: Haupt-Checkout `C:\Users\User\Desktop\Vibemind_V1\vibemind-os` und dessen voice-Checkout `...\vibemind-os\voice` (nur lesen; `git worktree add` legt nur Metadaten unter `.git/modules/voice/worktrees` an). Vor jedem Commit `git -C <pfad> rev-parse --show-toplevel` pruefen (Memory: Subagent committete einmal in den Haupt-Checkout).
- Commits: `$env:ENTIRE_TEST_TTY='0'; git commit ...` (PowerShell), Conventional Commits auf Deutsch wie im Repo (`feat(electron): …`, `fix(python): …`, `test(…)`, `chore(voice): …`). Nur eigene Dateien stagen (`git add <pfad>`), nie `git add -A`.
- Kein Force-Push, kein `--no-verify`, nie `docker service update`, keine Branch-/Worktree-Loeschung ohne User.
- Temp-Verzeichnisse fuer Tests auf `C:\` (unter `$HOME\.vbu-tests\…`), nicht `E:\`.
- Git ueber PowerShell (Memory: git-bash crasht).

## Review-Fokus

Die fuenf wahrscheinlichsten Fehlerbilder, die die Spec impliziert, aber nicht ausdruecklich testet – jedes ist in einer Task durch einen Test festgenagelt:

1. **`VIBEMIND_STARTUP_ALLOW` mit Leerzeichen, Grossbuchstaben, Duplikaten, leeren Eintraegen** (`" Docker-Cleanup , brain-spawn,docker-cleanup,,"`) – sonst bleibt eine erlaubte Wirkung aus oder eine doppelte Zeile verfaelscht das Log. → T1, Test `Liste wird getrimmt, kleingeschrieben, dedupliziert und in fester Reihenfolge gefuehrt`. Zusatz: `VIBEMIND_STARTUP_ALLOW` aus einer `.env` darf nichts einschalten → T6, Test `VIBEMIND_STARTUP_ALLOW aus einer .env schaltet nichts ein`.
2. **OpenFang-Health antwortet langsam (>1 s), laeuft aber** – der alte 1-s-Timeout startete dann ein zweites `openfang.exe`. → T5, Test `langsame Health (1,5 s) gilt als laufend – kein zweiter Start, auch wenn openfang-spawn erlaubt ist`.
3. **`BRAIN_PORT` keine Zahl** (`"5000abc"`, `"70000"`, `"0"`, `" 5001 "`, `""`) – sonst `http://localhost:NaN/brain` oder Probe auf Unsinn. → T5, Test `parseBrainPort akzeptiert nur 1..65535 und faellt sonst mit Warnung auf 5000 zurueck`.
4. **`MIROFISH_URL` erreichbar, liefert aber Fehlerseite oder fremde App** (HTTP 502 vom Tailnet-Proxy; Rowboat-Vite auf :5173 beim Dashboard). → T4, Tests `MiroFish: HTTP 502 ergibt Statuskarte statt Fehlerseite`, `MiroFish: fremde App (falscher Titel) ergibt Statuskarte`, `Dashboard: DASHBOARD_DEV_URL mit fremder App (Rowboat) ergibt Statuskarte`.
5. **App wird beendet, waehrend ein Startblock noch laeuft** – danach darf kein weiterer Block (v. a. `Python backend`) mehr starten, sonst verwaister Python-Prozess nach `will-quit`; ein Overlay-Check darf nach Zerstoerung nicht mehr laden. → T1, Tests `nach beginShutdown laufen keine weiteren Startbloecke` und `Beenden waehrend ein Block laeuft: laufender Block endet, Folgeblock startet nicht`; T4, Test `zerstoerte webContents werden nach dem Check nicht mehr geladen`.

## Dateistruktur

voice-Repo (Worktree `C:\Users\User\ClaudeWork\wt-voice-startprofil`):

| Datei | Neu/Geaendert | Verantwortung |
|---|---|---|
| `electron-app/startup-policy.js` | geaendert (T1, T2) | Startprofil: Liste parsen, `isAllowed`, Log-Zeile, „haette“-Log, Fehlerisolation je Block, Beenden-Sperre, Python-Schalter |
| `electron-app/startup-policy.test.js` | geaendert (T1, T2) | Tests Startprofil + Python-Env |
| `electron-app/local-startup.js` | neu (T3) | media-compose, n8n-local (+ Besitzer-Anmeldung), mirofish-local, zombie-kill hinter dem Profil |
| `electron-app/local-startup.test.js` | neu (T3) | Tests mit Attrappen (exec/execSync/execFileSync) |
| `electron-app/port-allocator.js` | geaendert (T3) | `cleanupStaleContainers` nur mit `docker-cleanup` |
| `electron-app/docker-manager.js` | geaendert (T3) | `stopAllContainers` beim Beenden nur mit `docker-cleanup` |
| `electron-app/docker-cleanup.test.js` | neu (T3) | Tests PortAllocator/DockerManager mit Fake-exec |
| `electron-app/overlay-status.js` | neu (T4) | gemeinsame Ziel-Pruefung (Datei/URL, 2 s, Titel), Statuskarte, `loadOverlay`, `resolveElectronParts` |
| `electron-app/overlay-status.test.js` | neu (T4) | Tests Helfer mit echten lokalen HTTP-Servern |
| `electron-app/mirofish-manager.js`, `clawport-manager.js`, `dashboard-manager.js`, `rowboat-manager.js`, `eyeterm-manager.js`, `video-manager.js`, `agentfarm-manager.js`, `flowzen-manager.js` | geaendert (T4) | laden ueber `loadOverlay`; Rowboat-Platzhalter, eyeTerm „entfernt“, Dashboard ohne `:5173`-Vorgabe, Video ohne Laura-Absturz |
| `electron-app/overlay-managers.test.js` | neu (T4) | Manager-Tests mit Fake-BrowserView |
| `electron-app/openfang-manager.js` | geaendert (T5) | nur uebernehmen (3 s × 3 Probe), Start nur mit `openfang-spawn`, kein `taskkill`/Shutdown fremder Prozesse |
| `electron-app/brain-manager.js` | geaendert (T5) | `BRAIN_PORT` parsen, nur uebernehmen, Statuskarte, Start nur mit `brain-spawn` |
| `electron-app/adopt-managers.test.js` | neu (T5) | Tests OpenFang/Brain |
| `electron-app/env-files.js` | neu (T6) | `.env`-Pfade inkl. `VIBEMIND_ENV_FILE`, Startprofil nur aus Prozess-Env |
| `electron-app/env-files.test.js` | neu (T6) | Tests mit Fake-dotenv |
| `electron-app/main.js` | geaendert (T1–T6) | Verdrahtung: Profil-Log, Bloecke einzeln, Gates, Python-Env, Managers mit Abhaengigkeiten, `.env` |
| `electron-app/package.json` | geaendert (T1, T3–T6) | `test:unit` um neue Testdateien ergaenzt |
| `python/electron_backend.py` | geaendert (T2) | `SpaceColors.ROWBOAT` → `ROARBOOT` (Z. 140–141) |
| `python/tests/test_startprofil_backend.py` | neu (T2) | Regression Import/Farben, Autoclean aus, Bubble-Worker aus |

Aeusseres Repo `C:\Users\User\Desktop\Vibemind_V1` (Branch `codex/vibemind-ops-baseline`):

| Datei | Neu/Geaendert | Verantwortung |
|---|---|---|
| `scripts/voice_betrieb_update.ps1` | neu (T7) | Laufzeit-Checkout klonen/aktualisieren, npm ci/pip/Overlay-Builds nur bei Aenderung |
| `scripts/tests/voice-betrieb-update.Tests.ps1` | neu (T7) | Pester: Abbruch bei eigenen Aenderungen, npm ci nur bei geaendertem Lockfile |
| `scripts/lib/vibemind-helpers.ps1` | geaendert (T7) | `Get-VoiceElectronLaunch`, `ConvertTo-CmdSetPrefix`, `Get-MiroFishHealthUrl` |
| `scripts/vibemind-start.ps1` | geaendert (T7) | Phase 5 aus `Get-VoiceElectronLaunch`, ohne `DASHBOARD_DEV_URL`; Phase 6/Status: MiroFish auf der VM |
| `scripts/tests/voice-electron-launch.Tests.ps1` | neu (T7) | Pester fuer die Launcher-Helfer |
| `WORKBOARD.md` | geaendert (T0, T8) | Claim/Abschluss (nur eigene Zeile stagen) |

vibemind-os (Worktree `.worktrees\setup-agent`, `master`): nur der Gitlink `voice` (T8).

---

## Task 0: Setup, Claim, Baseline

**Dateien:** keine Code-Aenderung; `WORKBOARD.md` (aeusseres Repo).

- [ ] **Schritt 0.1 – Lage pruefen (nur lesen).**
  ```powershell
  git -C C:\Users\User\Desktop\Vibemind_V1 status --short --branch
  git -C C:\Users\User\Desktop\Vibemind_V1\vibemind-os\voice status --short --branch
  git -C C:\Users\User\Desktop\Vibemind_V1\vibemind-os\voice worktree list
  git -C C:\Users\User\Desktop\Vibemind_V1\vibemind-os\voice ls-remote origin refs/heads/codex/laura-video-interface-embed refs/heads/feat/startprofil
  Test-Path C:\Users\User\ClaudeWork\wt-voice-startprofil, C:\Users\User\ClaudeWork\vibemind-betrieb
  ```
  Erwartet: aeusseres Repo auf `codex/vibemind-ops-baseline`; `codex/laura-video-interface-embed` zeigt auf `a506ca2e64525e8926dc6282ae73df891d26cac9`; kein `feat/startprofil` auf origin; beide Pfade `False`. Weicht etwas ab → STOP, User fragen.

- [ ] **Schritt 0.2 – WORKBOARD-Claim (aeusseres Repo).** `WORKBOARD.md` lesen; liegt ein aktiver fremder Claim auf `vibemind-os/voice`, Launcher Phase 5/6 oder `ClaudeWork\vibemind-betrieb` → STOP, User fragen. Sonst unter *In Arbeit* direkt nach der Kopfzeile der Tabelle eine Zeile einfuegen:
  ```
  | cc-voice-app-teil1 | 2026-10-15 | **voice-App Teil 1 (Startprofil, Overlays, Laufzeit-Checkout).** voice `feat/startprofil` im Worktree `C:/Users/User/ClaudeWork/wt-voice-startprofil`; aeusseres Repo `scripts/voice_betrieb_update.ps1`, `scripts/vibemind-start.ps1` Phase 5/6, `scripts/lib/vibemind-helpers.ps1`; spaeter `C:/Users/User/ClaudeWork/vibemind-betrieb`. Kein Merge/Pin/Start ohne User-OK. |
  ```
  Nur die eigene Zeile stagen (Memory „Board-Dateien: nur eigene Zeile stagen“):
  ```powershell
  cd C:\Users\User\Desktop\Vibemind_V1
  git diff --stat -- WORKBOARD.md      # muss genau 1 insertion zeigen
  ```
  Zeigt der Diff mehr als die eigene Zeile (fremde ungestagte Aenderung): Blob aus `HEAD` + eigener Zeile bauen und nur diesen stagen:
  ```powershell
  $zeilen = [System.Collections.Generic.List[string]](git show HEAD:WORKBOARD.md)
  $start = $zeilen.IndexOf('## In Arbeit')
  $kopf = $zeilen.IndexOf('|---|---|---|', $start)
  $zeilen.Insert($kopf + 1, '<eigene Zeile von oben>')
  New-Item -ItemType Directory $HOME\.vbu-tests -Force | Out-Null
  [IO.File]::WriteAllText("$HOME\.vbu-tests\workboard.md", (($zeilen -join "`n") + "`n"), [Text.UTF8Encoding]::new($false))
  $blob = git hash-object -w $HOME\.vbu-tests\workboard.md
  git update-index --cacheinfo 100644,$blob,WORKBOARD.md
  ```
  Sonst `git add WORKBOARD.md`. Danach:
  ```powershell
  git diff --cached --name-only        # genau: WORKBOARD.md
  $env:ENTIRE_TEST_TTY='0'; git commit -m "docs: workboard claim cc-voice-app-teil1"
  ```

- [ ] **Schritt 0.3 – voice-Worktree anlegen.**
  ```powershell
  git -C C:\Users\User\Desktop\Vibemind_V1\vibemind-os\voice worktree add -b feat/startprofil C:\Users\User\ClaudeWork\wt-voice-startprofil a506ca2
  git -C C:\Users\User\ClaudeWork\wt-voice-startprofil rev-parse --show-toplevel   # C:/Users/User/ClaudeWork/wt-voice-startprofil
  git -C C:\Users\User\ClaudeWork\wt-voice-startprofil log -1 --format=%H          # a506ca2e64525e8926dc6282ae73df891d26cac9
  ```

- [ ] **Schritt 0.4 – Testlaeufer feststellen.** `electron-app/package.json` Skript `test:unit` = `node --test laura-embed-config.test.js laura-preload.test.js laura-embed-host.test.js video-manager.test.js laura-packaging.test.js laura-live-proof.test.js startup-policy.test.js supabase-config.test.js`. Python: `voice/.venv312` hat **kein** pytest → Unit-Tests mit `C:\Users\User\Desktop\Vibemind_V1\.venv\Scripts\python.exe -m pytest` (pytest 7.4.3, Python 3.11); der Import-Smoke-Test nutzt zusaetzlich `VOICE_PYTHON` (s. T2). Pester: `Import-Module Pester -MinimumVersion 5.0` (6.0.0 vorhanden; 3.4.0 daneben nicht verwenden).

- [ ] **Schritt 0.5 – Baseline Electron.**
  ```powershell
  cd C:\Users\User\ClaudeWork\wt-voice-startprofil\electron-app
  node --test laura-embed-config.test.js laura-preload.test.js laura-embed-host.test.js video-manager.test.js laura-packaging.test.js laura-live-proof.test.js startup-policy.test.js supabase-config.test.js
  ```
  Erwartet: `# fail 0`. Scheitert ein Test nur an `Cannot find module 'electron'`/`'dotenv'` (Worktree ohne `node_modules`), eine Junction auf die vorhandenen Module legen (nur lesend genutzt, `node_modules/` ist in `.gitignore`):
  ```powershell
  New-Item -ItemType Junction -Path .\node_modules -Target C:\Users\User\Desktop\Vibemind_V1\vibemind-os\voice\electron-app\node_modules
  ```
  und erneut laufen lassen. Ergebnis (Anzahl pass/fail) im Task-Bericht notieren; vorbestehende Fehler sind nicht Teil dieses Plans.

- [ ] **Schritt 0.6 – Baseline Python.**
  ```powershell
  cd C:\Users\User\ClaudeWork\wt-voice-startprofil\python
  C:\Users\User\Desktop\Vibemind_V1\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests/test_bindings_registry.py -q
  ```
  Erwartet: gruen (nur Lauffaehigkeit des Laeufers belegen).

**Interfaces:** keine.

---

## Task 1: Startprofil-Kern (`startup-policy.js`) und Blockisolation in `main.js`

**Dateien:** `electron-app/startup-policy.js`, `electron-app/startup-policy.test.js`, `electron-app/main.js`.

- [ ] **Schritt 1.1 – Fehlschlagende Tests schreiben.** In `startup-policy.test.js` den Import (Z. 4–11) ersetzen durch:
  ```js
  const {
    DOCKER_BOOTSTRAP_MARKER,
    ISOLATED_PRE_QUIT_VIDEO_CLEANUP_MARKER,
    STARTUP_SIDE_EFFECTS,
    createStartupAudit,
    createStartupPolicy,
    findForbiddenStartupMarkers,
    formatStartupProfileLine,
    parseStartupAllow,
    stripAnsi,
  } = require('./startup-policy');

  function recordingLogger() {
    const lines = [];
    const rec = (level) => (...parts) => lines.push([level, parts.map(String).join(' ')]);
    return { lines, log: rec('log'), info: rec('info'), warn: rec('warn'), error: rec('error') };
  }
  ```
  und am Dateiende anhaengen:
  ```js
  test('Startprofil kennt genau die neun Nebenwirkungen der Spec', () => {
    assert.deepEqual([...STARTUP_SIDE_EFFECTS], [
      'docker-cleanup', 'media-compose', 'n8n-local', 'mirofish-local',
      'openfang-spawn', 'brain-spawn', 'rowboat-autoclean', 'bubble-sync', 'zombie-kill',
    ]);
  });

  test('Vorgabe: ohne VIBEMIND_STARTUP_ALLOW ist alles aus', () => {
    const policy = createStartupPolicy({}, { logger: recordingLogger() });
    assert.deepEqual([...policy.allowed], []);
    assert.deepEqual([...policy.skipped], [...STARTUP_SIDE_EFFECTS]);
    for (const name of STARTUP_SIDE_EFFECTS) assert.equal(policy.isAllowed(name), false);
    assert.equal(createStartupPolicy({ VIBEMIND_STARTUP_ALLOW: '' }).isAllowed('docker-cleanup'), false);
  });

  test('Liste wird getrimmt, kleingeschrieben, dedupliziert und in fester Reihenfolge gefuehrt', () => {
    const policy = createStartupPolicy(
      { VIBEMIND_STARTUP_ALLOW: ' Docker-Cleanup , brain-spawn,docker-cleanup,, ZOMBIE-KILL ' },
      { logger: recordingLogger() },
    );
    assert.deepEqual([...policy.allowed], ['docker-cleanup', 'brain-spawn', 'zombie-kill']);
    assert.equal(policy.isAllowed('docker-cleanup'), true);
    assert.equal(policy.isAllowed('media-compose'), false);
    assert.deepEqual(parseStartupAllow(undefined), { allowed: [], unknown: [] });
  });

  test('unbekannte Namen werden mit Warnung ignoriert', () => {
    const logger = recordingLogger();
    const policy = createStartupPolicy({ VIBEMIND_STARTUP_ALLOW: 'n8n-local,eyeterm,Eyeterm' }, { logger });
    assert.deepEqual([...policy.allowed], ['n8n-local']);
    assert.deepEqual([...policy.unknown], ['eyeterm']);
    policy.logStartupProfile();
    const warnings = logger.lines.filter(([level]) => level === 'warn').map(([, msg]) => msg);
    assert.deepEqual(warnings, ['[Startprofil] unbekannter Name ignoriert: eyeterm']);
  });

  test('isAllowed wirft bei Tippfehlern im Code', () => {
    const policy = createStartupPolicy({});
    assert.throws(() => policy.isAllowed('docker-clean'), /Unbekannte Startprofil-Nebenwirkung: docker-clean/);
  });

  test('VIBEMIND_E2E_ISOLATED_STARTUP schlaegt die Erlaubnisliste', () => {
    const policy = createStartupPolicy({
      VIBEMIND_E2E_ISOLATED_STARTUP: 'true',
      VIBEMIND_STARTUP_ALLOW: 'docker-cleanup,brain-spawn',
    });
    assert.equal(policy.isAllowed('docker-cleanup'), false);
    assert.deepEqual([...policy.allowed], []);
  });

  test('Log-Zeile hat das Spec-Format', () => {
    const logger = recordingLogger();
    createStartupPolicy({}, { logger }).logStartupProfile();
    assert.deepEqual(logger.lines, [[
      'log',
      '[Startprofil] erlaubt=[] uebersprungen=[docker-cleanup,media-compose,n8n-local,mirofish-local,openfang-spawn,brain-spawn,rowboat-autoclean,bubble-sync,zombie-kill]',
    ]]);
    assert.equal(
      formatStartupProfileLine(['docker-cleanup', 'zombie-kill']),
      '[Startprofil] erlaubt=[docker-cleanup,zombie-kill] uebersprungen=[media-compose,n8n-local,mirofish-local,openfang-spawn,brain-spawn,rowboat-autoclean,bubble-sync]',
    );
  });

  test('logSkipped beschreibt, was getan worden waere', () => {
    const logger = recordingLogger();
    createStartupPolicy({}, { logger }).logSkipped('docker-cleanup', 'docker rm -f generation-a');
    assert.deepEqual(logger.lines, [['log', '[Startprofil] docker-cleanup uebersprungen – haette: docker rm -f generation-a']]);
  });

  test('ein Fehler in einem Block bricht die Folgebloecke nicht ab', async () => {
    const logger = recordingLogger();
    const policy = createStartupPolicy({}, { logger });
    const calls = [];
    const results = [
      await policy.runExternalStartup('eins', async () => calls.push('eins')),
      await policy.runExternalStartup('Laura', async () => { throw new Error('Laura renderer missing'); }),
      await policy.runExternalStartup('Python backend', async () => calls.push('python')),
      await policy.runExternalStartup('kaputt-ohne-Error', async () => { throw 'nur ein String'; }),
    ];
    assert.deepEqual(results, [true, false, true, false]);
    assert.deepEqual(calls, ['eins', 'python']);
    const errors = logger.lines.filter(([level]) => level === 'error').map(([, msg]) => msg);
    assert.deepEqual(errors, [
      '[Startprofil] Startblock "Laura" fehlgeschlagen: Laura renderer missing',
      '[Startprofil] Startblock "kaputt-ohne-Error" fehlgeschlagen: nur ein String',
    ]);
  });

  test('nach beginShutdown laufen keine weiteren Startbloecke', async () => {
    const logger = recordingLogger();
    const policy = createStartupPolicy({}, { logger });
    const calls = [];
    assert.equal(await policy.runExternalStartup('vorher', async () => calls.push('vorher')), true);
    assert.equal(policy.isShuttingDown(), false);
    policy.beginShutdown();
    assert.equal(policy.isShuttingDown(), true);
    assert.equal(await policy.runExternalStartup('Python backend', async () => calls.push('python')), false);
    assert.deepEqual(calls, ['vorher']);
    assert.ok(logger.lines.some(([level, msg]) =>
      level === 'log' && msg === '[Startprofil] Startblock "Python backend" uebersprungen – App wird beendet'));
  });

  test('Beenden waehrend ein Block laeuft: laufender Block endet, Folgeblock startet nicht', async () => {
    const policy = createStartupPolicy({}, { logger: recordingLogger() });
    const calls = [];
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    const running = policy.runExternalStartup('post-window', async () => {
      calls.push('start');
      await gate;
      calls.push('ende');
    });
    policy.beginShutdown();
    release();
    assert.equal(await running, true);
    assert.equal(await policy.runExternalStartup('Python backend', async () => calls.push('python')), false);
    assert.deepEqual(calls, ['start', 'ende']);
  });
  ```

- [ ] **Schritt 1.2 – Tests rot sehen.**
  ```powershell
  cd C:\Users\User\ClaudeWork\wt-voice-startprofil\electron-app
  node --test startup-policy.test.js
  ```
  Erwartet: Abbruch mit `TypeError: Cannot read properties of undefined (reading 'filter')` bzw. `STARTUP_SIDE_EFFECTS is not iterable` – die neuen Exporte fehlen.

- [ ] **Schritt 1.3 – Implementierung.** `startup-policy.js` vollstaendig ersetzen durch:
  ```js
  const { stripVTControlCharacters } = require('node:util');

  const DOCKER_BOOTSTRAP_MARKER =
    '[Main] NORMAL_STARTUP Docker bootstrap entered: stale-container cleanup and media Docker';
  const ISOLATED_PRE_QUIT_VIDEO_CLEANUP_MARKER =
    '[Main] VIBEMIND_E2E_ISOLATED_STARTUP active — pre-quit video BrowserView destroyed';

  // Spec 2026-10-15 Abschnitt 1: benannte Nebenwirkungen, Vorgabe aus.
  const STARTUP_SIDE_EFFECTS = Object.freeze([
    'docker-cleanup',
    'media-compose',
    'n8n-local',
    'mirofish-local',
    'openfang-spawn',
    'brain-spawn',
    'rowboat-autoclean',
    'bubble-sync',
    'zombie-kill',
  ]);

  function parseStartupAllow(raw) {
    const allowed = [];
    const unknown = [];
    if (typeof raw !== 'string') return { allowed, unknown };
    for (const part of raw.split(',')) {
      const name = part.trim().toLowerCase();
      if (!name) continue;
      if (STARTUP_SIDE_EFFECTS.includes(name)) {
        if (!allowed.includes(name)) allowed.push(name);
      } else if (!unknown.includes(name)) {
        unknown.push(name);
      }
    }
    allowed.sort((a, b) => STARTUP_SIDE_EFFECTS.indexOf(a) - STARTUP_SIDE_EFFECTS.indexOf(b));
    return { allowed, unknown };
  }

  function formatStartupProfileLine(allowed) {
    const skipped = STARTUP_SIDE_EFFECTS.filter((name) => !allowed.includes(name));
    return `[Startprofil] erlaubt=[${allowed.join(',')}] uebersprungen=[${skipped.join(',')}]`;
  }

  function errorText(err) {
    return err && typeof err.message === 'string' ? err.message : String(err);
  }

  function createStartupPolicy(environment, options = {}) {
    const logger = options.logger || console;
    const isIsolatedStartup = environment.VIBEMIND_E2E_ISOLATED_STARTUP === 'true';
    const parsed = parseStartupAllow(environment.VIBEMIND_STARTUP_ALLOW);
    const allowed = Object.freeze(isIsolatedStartup ? [] : parsed.allowed);
    const unknown = Object.freeze(parsed.unknown);
    const skipped = Object.freeze(STARTUP_SIDE_EFFECTS.filter((name) => !allowed.includes(name)));
    let isolatedPreQuitCleanupDone = false;
    let shuttingDown = false;

    function assertKnown(name) {
      if (!STARTUP_SIDE_EFFECTS.includes(name)) {
        throw new TypeError(`Unbekannte Startprofil-Nebenwirkung: ${name}`);
      }
    }

    return Object.freeze({
      isIsolatedStartup,
      allowed,
      skipped,
      unknown,
      isAllowed(name) {
        assertKnown(name);
        return allowed.includes(name);
      },
      isShuttingDown() {
        return shuttingDown;
      },
      beginShutdown() {
        shuttingDown = true;
      },
      startupLogLine() {
        return formatStartupProfileLine(allowed);
      },
      logStartupProfile() {
        for (const name of unknown) logger.warn(`[Startprofil] unbekannter Name ignoriert: ${name}`);
        logger.log(formatStartupProfileLine(allowed));
      },
      logSkipped(name, wouldHaveDone) {
        assertKnown(name);
        logger.log(`[Startprofil] ${name} uebersprungen – haette: ${wouldHaveDone}`);
      },
      async runExternalStartup(name, start) {
        if (isIsolatedStartup) return false;
        if (shuttingDown) {
          logger.log(`[Startprofil] Startblock "${name}" uebersprungen – App wird beendet`);
          return false;
        }
        try {
          await start();
          return true;
        } catch (err) {
          logger.error(`[Startprofil] Startblock "${name}" fehlgeschlagen: ${errorText(err)}`);
          return false;
        }
      },
      runIsolatedPreQuitCleanup(cleanup) {
        if (!isIsolatedStartup || isolatedPreQuitCleanupDone) return false;
        isolatedPreQuitCleanupDone = true;
        cleanup();
        return true;
      },
    });
  }

  function stripAnsi(value) {
    return stripVTControlCharacters(String(value));
  }

  function findForbiddenStartupMarkers(output, markers) {
    const normalized = stripAnsi(output);
    return markers.filter((marker) => normalized.includes(marker));
  }

  function createStartupAudit() {
    let dockerBootstrapStarted = false;
    return Object.freeze({
      markDockerBootstrapStarted() {
        dockerBootstrapStarted = true;
      },
      publicMarkers() {
        return dockerBootstrapStarted ? [DOCKER_BOOTSTRAP_MARKER] : [];
      },
    });
  }

  module.exports = {
    DOCKER_BOOTSTRAP_MARKER,
    ISOLATED_PRE_QUIT_VIDEO_CLEANUP_MARKER,
    STARTUP_SIDE_EFFECTS,
    createStartupAudit,
    createStartupPolicy,
    findForbiddenStartupMarkers,
    formatStartupProfileLine,
    parseStartupAllow,
    stripAnsi,
  };
  ```

- [ ] **Schritt 1.4 – `main.js`: Profil-Zeile, Beenden-Sperre, Bloecke einzeln, Laura ohne Absturz.**
  a) In `app.whenReady().then(async () => {` (a506ca2: Z. 3187) als **erste** Zeile im Callback einfuegen:
  ```js
      startupPolicy.logStartupProfile();
  ```
  b) Den Block `await startupPolicy.runExternalStartup('auxiliary managers and services', async () => { … });` (Z. 3252–3319) vollstaendig ersetzen durch:
  ```js
      await startupPolicy.runExternalStartup('Dashboard manager', async () => {
          dashboardManager = new DashboardManager(mainWindow);
          // Forward engine log lines and structured progress to the dashboard renderer
          if (dockerManager && dashboardManager) {
              dockerManager.setLogCallback((logLine) => {
                  dashboardManager.sendMessage('engine:log', logLine);
              });
              dockerManager.setProgressCallback((progressData) => {
                  dashboardManager.sendMessage('engine:progress', progressData);
              });
          }
      });
      await startupPolicy.runExternalStartup('Rowboat manager', async () => {
          rowboatManager = new RowboatManager(mainWindow);
      });
      await startupPolicy.runExternalStartup('SWE Design manager', async () => {
          sweDesignManager = new SweDesignManager(mainWindow);
      });
      await startupPolicy.runExternalStartup('ClawPort manager', async () => {
          clawportManager = new ClawPortManager(mainWindow);
      });
      await startupPolicy.runExternalStartup('Brain manager', async () => {
          // Phase A: SKIP_BRAIN_SPAWN=1 verhindert ein eigenes Brain (Brain kommt aus dem Swarm-Stack).
          brainManager = new BrainManager(mainWindow);
          if (process.env.SKIP_BRAIN_SPAWN === '1' || process.env.SKIP_BRAIN_SPAWN === 'true') {
              console.log('[Main] SKIP_BRAIN_SPAWN set — not spawning embedded Brain (using external/container Brain on :' + (process.env.BRAIN_PORT || '5000') + ')');
          } else {
              brainManager.startHeadless().catch(err =>
                  console.warn('[Main] Brain headless start failed:', err.message)
              );
          }
      });
      await startupPolicy.runExternalStartup('OpenFang', async () => {
          openfangManager = new OpenFangManager();
          openfangManager.start().catch(err =>
              console.warn('[Main] OpenFang start failed:', err.message)
          );
      });
      await startupPolicy.runExternalStartup('Supabase realtime', async () => {
          // Subscribes to ideas, projects, canvas_nodes etc. (live DB -> UI)
          realtimeChannels = initRealtimeSubscriptions(mainWindow);
      });
      await startupPolicy.runExternalStartup('Brain bridge', async () => {
          // Phase 11.G — Brain Space-Event Bridge (SSE -> renderer IPC)
          initBrainEventBridge(mainWindow);
      });
      await startupPolicy.runExternalStartup('AgentFarm manager', async () => {
          agentfarmManager = new AgentFarmManager(mainWindow);
      });
      await startupPolicy.runExternalStartup('MiroFish manager', async () => {
          mirofishManager = new MiroFishManager(mainWindow);
      });
      await startupPolicy.runExternalStartup('Flowzen manager', async () => {
          flowzenManager = new FlowzenManager(mainWindow, sendToPython);
      });
  ```
  c) Den Laura-/Video-Abschnitt (Z. 3321–3345, von `const lauraRendererUrl = …` bis `console.log('[Main] Laura host and VideoManager initialized');`) ersetzen durch:
  ```js
      // Fehlt der Laura-Renderer (z. B. Laufzeit-Checkout ohne spaces/video/laura),
      // darf das NICHT den Rest des Starts (Python, Shortcuts) abbrechen.
      let lauraRendererUrl = null;
      try {
          lauraRendererUrl = pathToFileURL(resolveLauraRendererPath({
              dirname: __dirname,
              resourcesPath: process.resourcesPath,
              existsSync: fs.existsSync,
          })).href;
      } catch (err) {
          console.warn('[Main] Laura-Renderer fehlt – Video-Overlay zeigt Statuskarte:', err.message);
      }
      try {
          lauraEmbedHost = createLauraEmbedHost({
              app,
              dialog,
              env: process.env,
              ipcMain,
              isAllowedSender: (event) => isAllowedLauraIpcEvent(
                  event,
                  videoManager?.videoView?.webContents,
                  lauraRendererUrl,
              ),
              logger: console,
              net,
              protocol: session.fromPartition(LAURA_SESSION_PARTITION).protocol,
              shell,
          });
          lauraEmbedHost.install();
      } catch (err) {
          console.error('[Main] Laura-Host nicht installiert:', err.message);
      }
      try {
          videoManager = new VideoManager(mainWindow);
          console.log('[Main] Laura host and VideoManager initialized');
      } catch (err) {
          console.error('[Main] VideoManager nicht verfuegbar:', err.message);
      }
  ```
  d) In `app.on('before-quit', (event) => {` (Z. 3501) als erste Zeile im Handler:
  ```js
      startupPolicy.beginShutdown();
  ```

- [ ] **Schritt 1.5 – Tests gruen.**
  ```powershell
  node --test startup-policy.test.js
  node --check main.js
  ```
  Erwartet: `# fail 0` (alte 8 + neue 11 Tests); `node --check` ohne Ausgabe.

- [ ] **Schritt 1.6 – Commit.**
  ```powershell
  cd C:\Users\User\ClaudeWork\wt-voice-startprofil
  git rev-parse --show-toplevel        # C:/Users/User/ClaudeWork/wt-voice-startprofil
  git add electron-app/startup-policy.js electron-app/startup-policy.test.js electron-app/main.js
  $env:ENTIRE_TEST_TTY='0'; git commit -m "feat(electron): Startprofil mit benannten Nebenwirkungen und Fehlerisolation je Startblock"
  ```

**Interfaces (fuer T2–T6):**
- `STARTUP_SIDE_EFFECTS: readonly string[]`
- `parseStartupAllow(raw: string|undefined) -> { allowed: string[], unknown: string[] }`
- `formatStartupProfileLine(allowed: string[]) -> string`
- `createStartupPolicy(env, { logger }?)` → `{ isIsolatedStartup, allowed, skipped, unknown, isAllowed(name) -> boolean (wirft TypeError bei unbekanntem Namen), isShuttingDown() -> boolean, beginShutdown(), startupLogLine() -> string, logStartupProfile(), logSkipped(name, wouldHaveDone: string), runExternalStartup(name, async fn) -> Promise<boolean>, runIsolatedPreQuitCleanup(fn) -> boolean }`

---

## Task 2: Python-Absturz (ROARBOOT) und Python-Schalter aus dem Profil

**Dateien:** `python/electron_backend.py`, `python/tests/test_startprofil_backend.py` (neu), `electron-app/startup-policy.js`, `electron-app/startup-policy.test.js`, `electron-app/main.js`.

- [ ] **Schritt 2.1 – Fehlschlagende Python-Tests.** Neue Datei `python/tests/test_startprofil_backend.py`:
  ```python
  """Startprofil-Regressionen fuer das Python-Backend (voice-App Teil 1, Spec 2026-10-15).

  1. electron_backend/ipc duerfen nur SpaceColors-Namen nutzen, die es gibt
     (seit 6010f3e heisst ROWBOAT -> ROARBOOT; der Import brach seit 09.07. ab).
  2. Profil "aus": Start-Sync loescht keine Rowboat-Quellen/Vault-Ordner.
  3. Profil "aus": Bubble-Sync-Worker A/B sind per Kill-Switch aus.
  """
  from __future__ import annotations

  import ast
  import importlib
  import os
  import subprocess
  import sys
  import types
  from pathlib import Path

  import pytest

  PY_ROOT = Path(__file__).resolve().parents[1]


  def _space_color_refs(path: Path) -> set[str]:
      tree = ast.parse(path.read_text(encoding="utf-8"))
      return {
          node.attr
          for node in ast.walk(tree)
          if isinstance(node, ast.Attribute)
          and isinstance(node.value, ast.Name)
          and node.value.id == "SpaceColors"
      }


  def test_backend_und_ipc_nutzen_nur_vorhandene_space_colors() -> None:
      from swarm.logging.space_logger import SpaceColors

      dateien = [PY_ROOT / "electron_backend.py", *sorted((PY_ROOT / "ipc").glob("*.py"))]
      fehlend = sorted(
          f"{datei.name}:{name}"
          for datei in dateien
          for name in _space_color_refs(datei)
          if not hasattr(SpaceColors, name)
      )
      assert fehlend == []


  @pytest.mark.skipif(not os.environ.get("VOICE_PYTHON"), reason="VOICE_PYTHON (Python 3.12 der voice-venv) nicht gesetzt")
  def test_electron_backend_importiert_mit_der_voice_venv() -> None:
      env = dict(os.environ)
      env.update({
          "VIBEMIND_ROWBOAT_AUTOCLEAN": "0",
          "VIBEMIND_BUBBLE_SYNC_ENABLED": "0",
          "VIBEMIND_CANVAS_REFORMAT_ENABLED": "0",
          "PYTHONIOENCODING": "utf-8",
      })
      ergebnis = subprocess.run(
          [os.environ["VOICE_PYTHON"], "-c", "import electron_backend; print('IMPORT_OK')"],
          cwd=PY_ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=240,
      )
      assert ergebnis.returncode == 0, ergebnis.stderr[-2000:]
      assert "IMPORT_OK" in ergebnis.stdout


  def _fake_cleanup_module(monkeypatch: pytest.MonkeyPatch, aufrufe: list[str]) -> None:
      fake_data = types.ModuleType("data")

      class _Ideas:
          def list_top_level(self, limit: int):  # noqa: ARG002
              aufrufe.append("ideas")
              return []

      fake_data.IdeasRepository = _Ideas
      monkeypatch.setitem(sys.modules, "data", fake_data)

      fake_mongo = types.ModuleType("publishing.rowboat_mongo_publisher")

      class _Mongo:
          def cleanup_orphaned_sources(self, *args, **kwargs):  # noqa: ARG002
              aufrufe.append("mongo")
              return {"deleted": 0}

      fake_mongo.RowboatMongoPublisher = _Mongo
      monkeypatch.setitem(sys.modules, "publishing.rowboat_mongo_publisher", fake_mongo)

      fake_vault = types.ModuleType("publishing.ideas_publisher")

      class _Vault:
          def cleanup_orphaned_project_dirs(self, *args, **kwargs):  # noqa: ARG002
              aufrufe.append("vault")
              return {"deleted": 0}

      fake_vault.IdeasPublisher = _Vault
      monkeypatch.setitem(sys.modules, "publishing.ideas_publisher", fake_vault)


  def _publishing_ohne_spiegel(monkeypatch: pytest.MonkeyPatch):
      import publishing

      for name in ("get_openfang_publisher", "get_n8n_publisher", "get_blue_rose_publisher", "get_mirofish_publisher"):
          monkeypatch.setattr(publishing, name, lambda: object())
      return publishing


  def test_autoclean_aus_loescht_weder_mongo_noch_vault(monkeypatch: pytest.MonkeyPatch) -> None:
      monkeypatch.setenv("ROWBOAT_PUBLISH_ENABLED", "1")
      monkeypatch.setenv("VIBEMIND_ROWBOAT_AUTOCLEAN", "0")
      aufrufe: list[str] = []
      _fake_cleanup_module(monkeypatch, aufrufe)
      publishing = _publishing_ohne_spiegel(monkeypatch)

      ergebnis = publishing.sync_all_spaces()

      assert "rowboat-cleanup" not in ergebnis
      assert "rowboat-cleanup-vault" not in ergebnis
      assert aufrufe == []


  def test_autoclean_an_ruft_beide_aufraeumer(monkeypatch: pytest.MonkeyPatch) -> None:
      monkeypatch.setenv("ROWBOAT_PUBLISH_ENABLED", "1")
      monkeypatch.setenv("VIBEMIND_ROWBOAT_AUTOCLEAN", "1")
      aufrufe: list[str] = []
      _fake_cleanup_module(monkeypatch, aufrufe)
      publishing = _publishing_ohne_spiegel(monkeypatch)

      publishing.sync_all_spaces()

      assert aufrufe == ["ideas", "mongo", "vault"]


  @pytest.mark.parametrize("modul", [
      "publishing.bubble_sync.worker_db_to_fs",
      "publishing.bubble_sync.worker_fs_to_db",
  ])
  def test_bubble_worker_sind_bei_profil_aus_abgeschaltet(monkeypatch: pytest.MonkeyPatch, modul: str) -> None:
      monkeypatch.setenv("VIBEMIND_BUBBLE_SYNC_ENABLED", "0")
      geladen = importlib.reload(importlib.import_module(modul))
      assert geladen.ENABLED is False
  ```

- [ ] **Schritt 2.2 – Rot sehen.**
  ```powershell
  cd C:\Users\User\ClaudeWork\wt-voice-startprofil\python
  C:\Users\User\Desktop\Vibemind_V1\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests/test_startprofil_backend.py -q
  ```
  Erwartet: `test_backend_und_ipc_nutzen_nur_vorhandene_space_colors` FAILED mit `assert ['electron_backend.py:ROWBOAT'] == []`; Autoclean-/Worker-Tests gruen (Python respektiert die Schalter bereits); Import-Test `SKIPPED`.

- [ ] **Schritt 2.3 – Fix.** In `python/electron_backend.py` Z. 140–141 ersetzen:
  ```python
      "[GraphBuilder]":  SpaceColors.ROARBOOT,   # Blue (ABSORB-8: ROWBOAT -> ROARBOOT)
      "[Rowboat]":       SpaceColors.ROARBOOT,   # Blue
  ```

- [ ] **Schritt 2.4 – Gruen, inklusive Import mit der voice-venv (nur lesend genutzt).**
  ```powershell
  C:\Users\User\Desktop\Vibemind_V1\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests/test_startprofil_backend.py -q
  $env:VOICE_PYTHON='C:\Users\User\Desktop\Vibemind_V1\vibemind-os\voice\.venv312\Scripts\python.exe'
  C:\Users\User\Desktop\Vibemind_V1\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests/test_startprofil_backend.py -q -k importiert
  Remove-Item Env:VOICE_PYTHON
  ```
  Erwartet: `5 passed, 1 skipped`, dann `1 passed`. Scheitert der Import-Smoke-Test an einem **anderen** Fehler als `ROWBOAT` (der Worktree hat kein vibemind-os drumherum, `brain`/`spaces` fehlen), Fehler im Bericht notieren und den Nachweis in T8 Schritt 8.8 (`python_ready`) fuehren – nicht hier weiter reparieren (Spec-Risiko „Weitere Fehler zur Laufzeit“).

- [ ] **Schritt 2.5 – Fehlschlagende JS-Tests fuer die Python-Umgebung.** In `startup-policy.test.js` den Import um `buildPythonBackendEnv` ergaenzen und anhaengen:
  ```js
  test('Profil aus: Python-Schalter sind 0 und schlagen .env-Werte', () => {
    const policy = createStartupPolicy({}, { logger: recordingLogger() });
    const env = buildPythonBackendEnv({
      baseEnv: {
        VIBEMIND_ROWBOAT_AUTOCLEAN: '1',
        VIBEMIND_BUBBLE_SYNC_ENABLED: '1',
        VIBEMIND_CANVAS_REFORMAT_ENABLED: 'true',
        PYTHONPATH: 'C:\\alt',
        BEHALTEN: 'x',
      },
      policy,
      vibemindRoot: 'C:\\vos',
      delimiter: ';',
    });
    assert.equal(env.VIBEMIND_ROWBOAT_AUTOCLEAN, '0');
    assert.equal(env.VIBEMIND_BUBBLE_SYNC_ENABLED, '0');
    assert.equal(env.VIBEMIND_CANVAS_REFORMAT_ENABLED, '0');
    assert.equal(env.PYTHONPATH, 'C:\\vos;C:\\alt');
    assert.equal(env.BEHALTEN, 'x');
  });

  test('Profil erlaubt rowboat-autoclean und bubble-sync: Schalter sind 1', () => {
    const policy = createStartupPolicy({ VIBEMIND_STARTUP_ALLOW: 'rowboat-autoclean,bubble-sync' });
    assert.deepEqual(policy.pythonEnv(), {
      VIBEMIND_ROWBOAT_AUTOCLEAN: '1',
      VIBEMIND_BUBBLE_SYNC_ENABLED: '1',
      VIBEMIND_CANVAS_REFORMAT_ENABLED: '1',
    });
    const env = buildPythonBackendEnv({ baseEnv: {}, policy, vibemindRoot: 'C:\\vos', delimiter: ';' });
    assert.equal(env.PYTHONPATH, 'C:\\vos');
  });
  ```
  `node --test startup-policy.test.js` → rot mit `TypeError: buildPythonBackendEnv is not a function`.

- [ ] **Schritt 2.6 – Implementierung.** In `startup-policy.js` im Objekt von `createStartupPolicy` nach `logSkipped` einfuegen:
  ```js
        pythonEnv() {
          const flag = (name) => (allowed.includes(name) ? '1' : '0');
          return {
            VIBEMIND_ROWBOAT_AUTOCLEAN: flag('rowboat-autoclean'),
            VIBEMIND_BUBBLE_SYNC_ENABLED: flag('bubble-sync'),
            VIBEMIND_CANVAS_REFORMAT_ENABLED: flag('bubble-sync'),
          };
        },
  ```
  vor `function stripAnsi` einfuegen:
  ```js
  function buildPythonBackendEnv({ baseEnv, policy, vibemindRoot, delimiter }) {
    const existing = baseEnv.PYTHONPATH || '';
    return {
      ...baseEnv,
      PYTHONPATH: existing ? `${vibemindRoot}${delimiter}${existing}` : vibemindRoot,
      // Profil gewinnt gegen .env (Spec Abschnitt 1)
      ...policy.pythonEnv(),
    };
  }
  ```
  und `buildPythonBackendEnv,` in `module.exports` aufnehmen.

  In `main.js` Z. 37–41 den Import ergaenzen:
  ```js
  const {
      ISOLATED_PRE_QUIT_VIDEO_CLEANUP_MARKER,
      buildPythonBackendEnv,
      createStartupAudit,
      createStartupPolicy,
  } = require('./startup-policy');
  ```
  und in `startPythonBackend()` (Z. 492–503) ersetzen:
  ```js
      // PYTHONPATH: vibemind-os root, damit 'brain' und 'spaces' importierbar sind;
      // Python-Schalter (Autoclean, Bubble-Sync, Reformat) kommen aus dem Startprofil.
      const vibemindRoot = path.join(__dirname, '..', '..');
      const pythonEnv = buildPythonBackendEnv({
          baseEnv: process.env,
          policy: startupPolicy,
          vibemindRoot,
          delimiter: path.delimiter,
      });
      console.log('[Main] Python-Schalter aus Startprofil:',
          `VIBEMIND_ROWBOAT_AUTOCLEAN=${pythonEnv.VIBEMIND_ROWBOAT_AUTOCLEAN}`,
          `VIBEMIND_BUBBLE_SYNC_ENABLED=${pythonEnv.VIBEMIND_BUBBLE_SYNC_ENABLED}`,
          `VIBEMIND_CANVAS_REFORMAT_ENABLED=${pythonEnv.VIBEMIND_CANVAS_REFORMAT_ENABLED}`);

      pythonProcess = spawn(pythonPath, [backendPath], {
          stdio: ['pipe', 'pipe', 'pipe'],
          cwd: path.join(__dirname, '..', 'python'),
          env: pythonEnv,
      });
  ```

- [ ] **Schritt 2.7 – Gruen.** `node --test startup-policy.test.js` → `# fail 0`; `node --check main.js`.

- [ ] **Schritt 2.8 – Commits (zwei).**
  ```powershell
  cd C:\Users\User\ClaudeWork\wt-voice-startprofil
  git add python/electron_backend.py python/tests/test_startprofil_backend.py
  $env:ENTIRE_TEST_TTY='0'; git commit -m "fix(python): electron_backend nutzt SpaceColors.ROARBOOT statt ROWBOAT (Importabsturz seit 09.07.)"
  git add electron-app/startup-policy.js electron-app/startup-policy.test.js electron-app/main.js
  $env:ENTIRE_TEST_TTY='0'; git commit -m "feat(electron): Python-Backend bekommt Autoclean/Bubble-Sync/Reformat aus dem Startprofil"
  ```

**Interfaces:** `policy.pythonEnv() -> { VIBEMIND_ROWBOAT_AUTOCLEAN, VIBEMIND_BUBBLE_SYNC_ENABLED, VIBEMIND_CANVAS_REFORMAT_ENABLED }` ('0'|'1'); `buildPythonBackendEnv({ baseEnv, policy, vibemindRoot, delimiter }) -> env`.

---

## Task 3: Gefaehrliche Nebenwirkungen hinter das Profil (Docker, Compose, n8n, MiroFish, Zombie-Kill)

**Dateien:** `electron-app/port-allocator.js`, `electron-app/docker-manager.js`, `electron-app/local-startup.js` (neu), `electron-app/local-startup.test.js` (neu), `electron-app/docker-cleanup.test.js` (neu), `electron-app/main.js`, `electron-app/package.json`.

- [ ] **Schritt 3.1 – Fehlschlagende Tests Docker.** Neue Datei `docker-cleanup.test.js`:
  ```js
  const assert = require('node:assert/strict');
  const test = require('node:test');

  const DockerManager = require('./docker-manager');
  const PortAllocator = require('./port-allocator');
  const { createStartupPolicy } = require('./startup-policy');

  function recordingLogger() {
    const lines = [];
    const rec = (level) => (...parts) => lines.push(`${level} ${parts.map(String).join(' ')}`);
    return { lines, log: rec('log'), info: rec('info'), warn: rec('warn'), error: rec('error') };
  }

  function fakeExec(psOutput) {
    const calls = [];
    const exec = async (command) => {
      calls.push(command);
      return { stdout: command.startsWith('docker ps') ? psOutput : '', stderr: '' };
    };
    exec.calls = calls;
    return exec;
  }

  const PS = 'generation-abc\r\nproject-x\r\nvibemind_brain-core.1.q2w3e4\r\nclaude-runner\r\n';

  test('docker-cleanup aus: kein docker rm, Log nennt was geloescht worden waere', async () => {
    const logger = recordingLogger();
    const policy = createStartupPolicy({}, { logger });
    const exec = fakeExec(PS);
    const allocator = new PortAllocator({ policy, execAsync: exec, logger });

    const result = await allocator.cleanupStaleContainers();

    assert.deepEqual(exec.calls, ['docker ps -a --format "{{.Names}}"']);
    assert.deepEqual(result, { removed: [], skipped: ['generation-abc', 'project-x'] });
    assert.ok(logger.lines.includes(
      'log [Startprofil] docker-cleanup uebersprungen – haette: docker rm -f generation-abc project-x'));
  });

  test('docker-cleanup erlaubt: entfernt nur generation-/project-Container', async () => {
    const logger = recordingLogger();
    const policy = createStartupPolicy({ VIBEMIND_STARTUP_ALLOW: 'docker-cleanup' }, { logger });
    const exec = fakeExec(PS);
    const allocator = new PortAllocator({ policy, execAsync: exec, logger });

    const result = await allocator.cleanupStaleContainers();

    assert.deepEqual(exec.calls, [
      'docker ps -a --format "{{.Names}}"',
      'docker rm -f generation-abc',
      'docker rm -f project-x',
    ]);
    assert.deepEqual(result, { removed: ['generation-abc', 'project-x'], skipped: [] });
  });

  test('ohne Startprofil raeumt PortAllocator nichts ab (fail-closed)', async () => {
    const logger = recordingLogger();
    const exec = fakeExec(PS);
    const result = await new PortAllocator({ execAsync: exec, logger }).cleanupStaleContainers();
    assert.deepEqual(exec.calls, ['docker ps -a --format "{{.Names}}"']);
    assert.deepEqual(result.removed, []);
  });

  test('DockerManager beim Beenden: docker-cleanup aus -> kein docker rm, auch nicht claude-runner', async () => {
    const logger = recordingLogger();
    const policy = createStartupPolicy({}, { logger });
    const exec = fakeExec('');
    const manager = new DockerManager({ policy, execAsync: exec, logger, pythonPath: 'python' });

    const result = await manager.stopAllContainers();

    assert.deepEqual(exec.calls, []);
    assert.deepEqual(result, { stopped: [], skipped: ['claude-runner'] });
    assert.ok(logger.lines.includes(
      'log [Startprofil] docker-cleanup uebersprungen – haette: beim Beenden docker rm -f claude-runner'));
  });

  test('DockerManager beim Beenden: docker-cleanup erlaubt -> claude-runner wird entfernt', async () => {
    const policy = createStartupPolicy({ VIBEMIND_STARTUP_ALLOW: 'docker-cleanup' }, { logger: recordingLogger() });
    const exec = fakeExec('');
    const manager = new DockerManager({ policy, execAsync: exec, logger: recordingLogger(), pythonPath: 'python' });

    const result = await manager.stopAllContainers();

    assert.deepEqual(exec.calls, ['docker rm -f claude-runner']);
    assert.deepEqual(result, { stopped: ['claude-runner'], skipped: [] });
  });
  ```

- [ ] **Schritt 3.2 – Fehlschlagende Tests lokaler Start.** Neue Datei `local-startup.test.js`:
  ```js
  const assert = require('node:assert/strict');
  const test = require('node:test');

  const {
    findZombieListeners,
    killZombiePythonProcesses,
    startMediaCompose,
    startMiroFishLocal,
    startN8nLocal,
  } = require('./local-startup');
  const { createStartupPolicy } = require('./startup-policy');

  function recordingLogger() {
    const lines = [];
    const rec = (level) => (...parts) => lines.push(`${level} ${parts.map(String).join(' ')}`);
    return { lines, log: rec('log'), info: rec('info'), warn: rec('warn'), error: rec('error') };
  }

  const AUS = () => createStartupPolicy({}, { logger: recordingLogger() });
  const ERLAUBT = (list) => createStartupPolicy({ VIBEMIND_STARTUP_ALLOW: list }, { logger: recordingLogger() });

  test('media-compose aus: kein docker compose', () => {
    const calls = [];
    const result = startMediaCompose({
      policy: AUS(), composePath: 'C:\\v\\docker-compose.media.yml',
      existsSync: () => true, execSync: (c) => calls.push(c), logger: recordingLogger(),
    });
    assert.equal(result, 'skipped');
    assert.deepEqual(calls, []);
  });

  test('media-compose erlaubt: startet media mit 15 s Timeout', () => {
    const calls = [];
    const result = startMediaCompose({
      policy: ERLAUBT('media-compose'), composePath: 'C:\\v\\docker-compose.media.yml',
      existsSync: () => true, execSync: (c, o) => calls.push([c, o.timeout]), logger: recordingLogger(),
    });
    assert.equal(result, 'started');
    assert.deepEqual(calls, [['docker compose -f "C:\\v\\docker-compose.media.yml" up -d media', 15000]]);
  });

  test('n8n-local aus: kein Compose-Aufruf, keine Besitzer-Anmeldung', () => {
    const logger = recordingLogger();
    const policy = createStartupPolicy({}, { logger });
    const execCalls = [];
    const provisioned = [];
    const result = startN8nLocal({
      policy,
      env: { N8N_ENABLED: 'true', N8N_API_URL: 'http://100.67.177.45:5678' },
      composePath: 'C:\\v\\docker-compose.n8n.yml', cwd: 'C:\\v',
      existsSync: () => true,
      exec: (...args) => execCalls.push(args),
      provisionOwner: (url) => provisioned.push(url),
      logger,
    });
    assert.equal(result, 'skipped');
    assert.deepEqual(execCalls, []);
    assert.deepEqual(provisioned, []);
    assert.ok(logger.lines.some((l) => l.includes('n8n-local uebersprungen') && l.includes('http://100.67.177.45:5678/rest/owner/setup')));
  });

  test('n8n-local erlaubt: Compose und erst danach Besitzer-Anmeldung', () => {
    const provisioned = [];
    const result = startN8nLocal({
      policy: ERLAUBT('n8n-local'),
      env: { N8N_ENABLED: 'true' },
      composePath: 'C:\\v\\docker-compose.n8n.yml', cwd: 'C:\\v',
      existsSync: () => true,
      exec: (command, options, callback) => {
        assert.equal(command, 'docker compose -f "C:\\v\\docker-compose.n8n.yml" up -d');
        assert.equal(options.cwd, 'C:\\v');
        callback(null, 'ok', '');
      },
      provisionOwner: (url) => provisioned.push(url),
      logger: recordingLogger(),
    });
    assert.equal(result, 'started');
    assert.deepEqual(provisioned, ['http://localhost:15678']);
  });

  test('n8n ohne N8N_ENABLED=true bleibt aus, auch wenn erlaubt', () => {
    const execCalls = [];
    const result = startN8nLocal({
      policy: ERLAUBT('n8n-local'), env: {}, composePath: 'x', cwd: 'y',
      existsSync: () => true, exec: (...a) => execCalls.push(a), provisionOwner: () => {}, logger: recordingLogger(),
    });
    assert.equal(result, 'disabled');
    assert.deepEqual(execCalls, []);
  });

  test('mirofish-local aus: kein Compose-Aufruf', () => {
    const execCalls = [];
    const result = startMiroFishLocal({
      policy: AUS(), env: { MIROFISH_ENABLED: 'true' },
      composePath: 'C:\\v\\docker-compose.mirofish.yml', cwd: 'C:\\v',
      existsSync: () => true, exec: (...a) => execCalls.push(a), logger: recordingLogger(),
    });
    assert.equal(result, 'skipped');
    assert.deepEqual(execCalls, []);
  });

  const NETSTAT = [
    '  Proto  Lokale Adresse         Remoteadresse          Status           PID',
    '  TCP    0.0.0.0:8099           0.0.0.0:0              ABHÖREN          4711',
    '  TCP    127.0.0.1:8009         0.0.0.0:0              LISTENING        999',
    '  TCP    0.0.0.0:80990          0.0.0.0:0              LISTENING        5555',
    '  TCP    127.0.0.1:52000        127.0.0.1:8099         ESTABLISHED      6666',
    '  TCP    [::]:8009              [::]:0                 LISTENING        7777',
  ].join('\r\n');

  test('findZombieListeners: nur lauschende fremde PIDs auf 8099/8009, auch deutsches netstat', () => {
    assert.deepEqual(findZombieListeners(NETSTAT, { ownPid: 999 }), [
      { port: 8099, pid: 4711 },
      { port: 8009, pid: 7777 },
    ]);
  });

  test('zombie-kill aus: netstat ja, taskkill nie, PIDs werden geloggt', () => {
    const logger = recordingLogger();
    const policy = createStartupPolicy({}, { logger });
    const calls = [];
    const found = killZombiePythonProcesses({
      policy, platform: 'win32', ownPid: 999, logger,
      execFileSync: (exe, args) => { calls.push(exe); return exe === 'netstat' ? NETSTAT : ''; },
    });
    assert.deepEqual(calls, ['netstat']);
    assert.equal(found.length, 2);
    assert.ok(logger.lines.includes(
      'log [Startprofil] zombie-kill uebersprungen – haette: taskkill PID 4711 (:8099), taskkill PID 7777 (:8009)'));
  });

  test('zombie-kill erlaubt: taskkill fuer jede gefundene PID', () => {
    const calls = [];
    killZombiePythonProcesses({
      policy: ERLAUBT('zombie-kill'), platform: 'win32', ownPid: 999, logger: recordingLogger(),
      execFileSync: (exe, args) => { calls.push([exe, ...args]); return exe === 'netstat' ? NETSTAT : ''; },
    });
    assert.deepEqual(calls.slice(1), [
      ['taskkill', '/PID', '4711', '/T', '/F'],
      ['taskkill', '/PID', '7777', '/T', '/F'],
    ]);
  });

  test('zombie-kill ausserhalb von Windows tut nichts', () => {
    const calls = [];
    assert.deepEqual(killZombiePythonProcesses({
      policy: ERLAUBT('zombie-kill'), platform: 'linux', execFileSync: (e) => calls.push(e), logger: recordingLogger(),
    }), []);
    assert.deepEqual(calls, []);
  });
  ```
  Rot sehen: `node --test docker-cleanup.test.js local-startup.test.js` → `Cannot find module './local-startup'` bzw. `assert.deepEqual` mit `docker rm -f generation-abc\r` in den Docker-Tests.

- [ ] **Schritt 3.3 – `port-allocator.js`.** Konstruktor (Z. 18–28) ersetzen:
  ```js
    constructor(options = {}) {
      // VNC ports start at 6200 to avoid Windows/Hyper-V TCP exclusion range
      // (Hyper-V often reserves 5940-6139 on Windows 11 + Docker Desktop)
      this.vncBasePort = 6200;
      this.appBasePort = 3001;
      this.maxPorts = 20;

      this.allocations = new Map();
      this.usedVncPorts = new Set();
      this.usedAppPorts = new Set();

      // Startprofil (Spec 2026-10-15): Aufraeumen nur mit "docker-cleanup".
      this._policy = options.policy || null;
      this._exec = options.execAsync || execAsync;
      this._logger = options.logger || console;
    }
  ```
  `cleanupStaleContainers()` (Z. 189–215) ersetzen:
  ```js
    /**
     * Entfernt generation-/project-Container frueherer Sitzungen – nur mit
     * Startprofil "docker-cleanup". Sonst wird nur geloggt, was entfernt worden waere.
     */
    async cleanupStaleContainers() {
      let names;
      try {
        const { stdout } = await this._exec('docker ps -a --format "{{.Names}}"');
        names = String(stdout)
          .split(/\r?\n/)
          .map((name) => name.trim())
          .filter((name) => name.startsWith('generation-') || name.startsWith('project-'));
      } catch {
        this._logger.log('[PortAllocator] No stale containers to clean');
        return { removed: [], skipped: [] };
      }

      if (!this._policy || !this._policy.isAllowed('docker-cleanup')) {
        const would = names.length > 0
          ? `docker rm -f ${names.join(' ')}`
          : 'keine generation-/project-Container vorhanden';
        if (this._policy) this._policy.logSkipped('docker-cleanup', would);
        else this._logger.warn(`[PortAllocator] kein Startprofil – nichts entfernt (${would})`);
        return { removed: [], skipped: names };
      }

      for (const name of names) {
        this._logger.log(`[PortAllocator] Removing stale container: ${name}`);
        try {
          await this._exec(`docker rm -f ${name}`);
        } catch { /* already gone */ }
      }
      this.allocations.clear();
      this.usedVncPorts.clear();
      this.usedAppPorts.clear();
      this._logger.log(`[PortAllocator] Cleaned up ${names.length} stale containers`);
      return { removed: names, skipped: [] };
    }
  ```

- [ ] **Schritt 3.4 – `docker-manager.js`.** Konstruktor Z. 15 `constructor() {` → `constructor(options = {}) {`; Z. 37–38 (`this.pythonPath = this._findPython();`) ersetzen durch:
  ```js
      // Startprofil (Spec 2026-10-15): Container beim Beenden nur mit "docker-cleanup" entfernen.
      this._policy = options.policy || null;
      this._exec = options.execAsync || execAsync;
      this._logger = options.logger || console;

      // Resolve Python executable path (Electron may not inherit shell PATH)
      this.pythonPath = options.pythonPath || this._findPython();
  ```
  In `stopClaudeRunner()` (Z. 786) `await execAsync('docker rm -f claude-runner');` → `await this._exec('docker rm -f claude-runner');`.
  `stopAllContainers()` (Z. 819–828) ersetzen:
  ```js
    /**
     * Beim Beenden: Container nur mit Startprofil "docker-cleanup" entfernen.
     */
    async stopAllContainers() {
      const ownProjects = Array.from(this.containers.keys());
      const targets = [...ownProjects.map((id) => `project-${id}`), 'claude-runner'];
      if (!this._policy || !this._policy.isAllowed('docker-cleanup')) {
        const would = `beim Beenden docker rm -f ${targets.join(' ')}`;
        if (this._policy) this._policy.logSkipped('docker-cleanup', would);
        else this._logger.warn(`[DockerManager] kein Startprofil – nichts entfernt (${would})`);
        return { stopped: [], skipped: targets };
      }
      await Promise.all([
        ...ownProjects.map((id) => this.stopProjectContainer(id)),
        this.stopClaudeRunner(),
      ]);
      return { stopped: targets, skipped: [] };
    }
  ```

- [ ] **Schritt 3.5 – `local-startup.js` (neu).**
  ```js
  /**
   * Lokale Nebenwirkungen beim App-Start hinter dem Startprofil
   * (Spec 2026-10-15 Abschnitt 1): media-compose, n8n-local, mirofish-local, zombie-kill.
   * Bei "aus" wird nichts ausgefuehrt, nur geloggt, was getan worden waere.
   */
  const http = require('node:http');

  const ZOMBIE_PORTS = Object.freeze([8099, 8009]);

  function startMediaCompose({ policy, composePath, existsSync, execSync, logger = console }) {
    const command = `docker compose -f "${composePath}" up -d media`;
    if (!policy.isAllowed('media-compose')) {
      policy.logSkipped('media-compose', command);
      return 'skipped';
    }
    if (!existsSync(composePath)) {
      logger.warn(`[Main] Media-Compose fehlt: ${composePath}`);
      return 'missing';
    }
    execSync(command, { timeout: 15000, stdio: 'ignore' });
    logger.log('[Main] Media server container started (port 9877)');
    return 'started';
  }

  function startLocalCompose({ sideEffect, label, enabledFlag, policy, composePath, cwd, existsSync, exec, onStarted, extraNote, logger }) {
    if (enabledFlag !== 'true') return 'disabled';
    const command = `docker compose -f "${composePath}" up -d`;
    if (!policy.isAllowed(sideEffect)) {
      policy.logSkipped(sideEffect, extraNote ? `${command} + ${extraNote}` : command);
      return 'skipped';
    }
    if (!existsSync(composePath)) {
      logger.warn(`[Main] ${label}: ${composePath} fehlt – Start uebersprungen`);
      return 'missing';
    }
    logger.log(`[Main] Starting ${label} Docker containers...`);
    exec(command, { cwd }, (err, stdout) => {
      if (err) {
        logger.warn(`[Main] ${label} Docker start failed:`, err.message);
        return;
      }
      logger.log(`[Main] ${label} Docker started successfully`);
      if (stdout && String(stdout).trim()) logger.log(`[Main] ${label}:`, String(stdout).trim());
      if (onStarted) onStarted();
    });
    return 'started';
  }

  function startN8nLocal({ policy, env, composePath, cwd, existsSync, exec, provisionOwner, logger = console }) {
    const n8nUrl = env.N8N_API_URL || 'http://localhost:15678';
    return startLocalCompose({
      sideEffect: 'n8n-local',
      label: 'n8n',
      enabledFlag: env.N8N_ENABLED,
      policy, composePath, cwd, existsSync, exec, logger,
      extraNote: `Besitzer-Anmeldung an ${n8nUrl}/rest/owner/setup`,
      onStarted: () => provisionOwner(n8nUrl),
    });
  }

  function startMiroFishLocal({ policy, env, composePath, cwd, existsSync, exec, logger = console }) {
    return startLocalCompose({
      sideEffect: 'mirofish-local',
      label: 'MiroFish',
      enabledFlag: env.MIROFISH_ENABLED,
      policy, composePath, cwd, existsSync, exec, logger,
    });
  }

  // Unveraendert aus main.js (a506ca2 Z. 3376-3414) uebernommen; laeuft nur nach erlaubtem n8n-local.
  function provisionN8nOwner(n8nUrl, { request = http.request, schedule = setTimeout, logger = console, maxAttempts = 6 } = {}) {
    let attempts = 0;
    const attempt = () => {
      attempts += 1;
      const url = new URL(`${n8nUrl}/rest/owner/setup`);
      const body = JSON.stringify({
        email: 'admin@vibemind.local',
        password: 'Vibemind1',
        firstName: 'VibeMind',
        lastName: 'Admin',
      });
      const req = request({
        hostname: url.hostname,
        port: url.port,
        path: url.pathname,
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) },
      }, (res) => {
        if (res.statusCode === 200) {
          logger.log('[Main] n8n owner account provisioned');
        } else {
          logger.log('[Main] n8n owner already exists or setup skipped (status ' + res.statusCode + ')');
        }
        res.resume();
      });
      req.on('error', () => {
        if (attempts < maxAttempts) {
          logger.log(`[Main] n8n not ready yet, retry ${attempts}/${maxAttempts}...`);
          schedule(attempt, 5000);
        } else {
          logger.warn(`[Main] n8n owner provision gave up after ${maxAttempts} attempts`);
        }
      });
      req.write(body);
      req.end();
    };
    schedule(attempt, 5000);
  }

  function findZombieListeners(netstatText, { ports = ZOMBIE_PORTS, ownPid = process.pid } = {}) {
    const found = [];
    for (const raw of String(netstatText).split(/\r?\n/)) {
      const parts = raw.trim().split(/\s+/);
      if (parts.length < 5 || parts[0].toUpperCase() !== 'TCP') continue;
      if (!/^(LISTEN|ABH)/i.test(parts[3])) continue; // LISTENING / ABHÖREN
      const pid = Number.parseInt(parts[4], 10);
      if (!(pid > 0) || pid === ownPid) continue;
      for (const port of ports) {
        if (parts[1].endsWith(`:${port}`) && !found.some((f) => f.pid === pid && f.port === port)) {
          found.push({ port, pid });
        }
      }
    }
    return found;
  }

  function killZombiePythonProcesses({ policy, execFileSync, platform = process.platform, ownPid = process.pid, logger = console }) {
    if (platform !== 'win32') return [];
    let netstat;
    try {
      netstat = execFileSync('netstat', ['-ano'], { timeout: 3000, encoding: 'utf8' });
    } catch (_) {
      return [];
    }
    const found = findZombieListeners(netstat, { ownPid });
    if (!policy.isAllowed('zombie-kill')) {
      if (found.length > 0) {
        policy.logSkipped('zombie-kill', found.map((f) => `taskkill PID ${f.pid} (:${f.port})`).join(', '));
      }
      return found;
    }
    for (const { port, pid } of found) {
      logger.log(`[Main] Killing zombie process on port ${port} (PID ${pid})`);
      try {
        execFileSync('taskkill', ['/PID', String(pid), '/T', '/F'], { timeout: 3000 });
      } catch (_) { /* may already be dead */ }
    }
    return found;
  }

  module.exports = {
    ZOMBIE_PORTS,
    findZombieListeners,
    killZombiePythonProcesses,
    provisionN8nOwner,
    startMediaCompose,
    startMiroFishLocal,
    startN8nLocal,
  };
  ```

- [ ] **Schritt 3.6 – `main.js` verdrahten.**
  a) Nach dem `startup-policy`-Import:
  ```js
  const {
      killZombiePythonProcesses,
      provisionN8nOwner,
      startMediaCompose,
      startMiroFishLocal,
      startN8nLocal,
  } = require('./local-startup');
  ```
  b) Die Funktion `function killZombiePythonProcesses() { … }` (a506ca2 Z. 440–460) loeschen. In `startPythonBackend()` die Zeilen `// Kill leftover Python processes from previous sessions` + `killZombiePythonProcesses();` ersetzen durch:
  ```js
      // Reste frueherer Sitzungen auf :8099/:8009 – beendet nur mit Startprofil "zombie-kill"
      killZombiePythonProcesses({
          policy: startupPolicy,
          execFileSync: require('child_process').execFileSync,
      });
  ```
  c) Den Block `await startupPolicy.runExternalStartup('Docker bootstrap', async () => { … });` (Z. 3219–3247) ersetzen:
  ```js
      await startupPolicy.runExternalStartup('Docker bootstrap', async () => {
          startupAudit.markDockerBootstrapStarted();
          // Coding-Engine-Manager; Aufraeumen nur mit Startprofil "docker-cleanup"
          dockerManager = new DockerManager({ policy: startupPolicy });
          portAllocator = new PortAllocator({ policy: startupPolicy });
          try {
              await portAllocator.cleanupStaleContainers();
          } catch (e) {
              console.warn('[Main] Container cleanup failed (Docker may not be running):', e.message);
          }
      });
      await startupPolicy.runExternalStartup('media-compose', async () => {
          startMediaCompose({
              policy: startupPolicy,
              composePath: path.join(__dirname, '..', 'python', 'spaces', 'video', 'docker-compose.media.yml'),
              existsSync: fs.existsSync,
              execSync: require('child_process').execSync,
          });
      });
  ```
  d) Den Block `await startupPolicy.runExternalStartup('post-window external services', async () => { … });` (Z. 3353–3466) vollstaendig ersetzen:
  ```js
      await startupPolicy.runExternalStartup('eyeTerm manager', async () => {
          eyetermManager = new EyeTermManager(mainWindow);
      });
      await startupPolicy.runExternalStartup('n8n-local', async () => {
          startN8nLocal({
              policy: startupPolicy,
              env: process.env,
              composePath: path.join(__dirname, '..', 'docker-compose.n8n.yml'),
              cwd: path.join(__dirname, '..'),
              existsSync: fs.existsSync,
              exec: require('child_process').exec,
              provisionOwner: (n8nUrl) => provisionN8nOwner(n8nUrl),
          });
      });
      await startupPolicy.runExternalStartup('mirofish-local', async () => {
          startMiroFishLocal({
              policy: startupPolicy,
              env: process.env,
              composePath: path.join(__dirname, '..', 'docker-compose.mirofish.yml'),
              cwd: path.join(__dirname, '..'),
              existsSync: fs.existsSync,
              exec: require('child_process').exec,
          });
      });
      // Rowboat-Core-Bridge und autoConfigureRowboatModels() (schreibt ~/.rowboat/config/models.json)
      // starten in Teil 1 bewusst NICHT – Rowboat-Einbettung ist Teil 2 der Spec.
      console.log('[Main] Rowboat-Bridge nicht gestartet – kommt mit Teil 2');
      await startupPolicy.runExternalStartup('Python backend', async () => {
          startPythonBackend();
      });
  ```
  e) `package.json` `test:unit` ersetzen durch:
  ```json
  "test:unit": "node --test laura-embed-config.test.js laura-preload.test.js laura-embed-host.test.js video-manager.test.js laura-packaging.test.js laura-live-proof.test.js startup-policy.test.js supabase-config.test.js local-startup.test.js docker-cleanup.test.js",
  ```

- [ ] **Schritt 3.7 – Gruen.**
  ```powershell
  cd C:\Users\User\ClaudeWork\wt-voice-startprofil\electron-app
  node --test docker-cleanup.test.js local-startup.test.js startup-policy.test.js
  node --check main.js
  Select-String -Path main.js -Pattern "startBridge\(\)|autoConfigureRowboatModels\(\)|rest/owner/setup|docker rm -f"
  ```
  Erwartet: `# fail 0`; `Select-String` findet nur noch die Funktionsdefinition `async function autoConfigureRowboatModels()` (Z. ~163), keine Aufrufe, kein `rest/owner/setup`, kein `docker rm -f`.

- [ ] **Schritt 3.8 – Commit.**
  ```powershell
  cd C:\Users\User\ClaudeWork\wt-voice-startprofil
  git add electron-app/port-allocator.js electron-app/docker-manager.js electron-app/local-startup.js electron-app/local-startup.test.js electron-app/docker-cleanup.test.js electron-app/main.js electron-app/package.json
  $env:ENTIRE_TEST_TTY='0'; git commit -m "feat(electron): Docker-Aufraeumen, Compose, n8n, MiroFish und Zombie-Kill nur mit Startprofil"
  ```

**Interfaces:**
- `new PortAllocator({ policy, execAsync?, logger? })`, `cleanupStaleContainers() -> Promise<{ removed: string[], skipped: string[] }>`
- `new DockerManager({ policy, execAsync?, logger?, pythonPath? })`, `stopAllContainers() -> Promise<{ stopped: string[], skipped: string[] }>`
- `startMediaCompose({ policy, composePath, existsSync, execSync, logger? }) -> 'skipped'|'missing'|'started'`
- `startN8nLocal({ policy, env, composePath, cwd, existsSync, exec, provisionOwner, logger? }) -> 'disabled'|'skipped'|'missing'|'started'`
- `startMiroFishLocal({ policy, env, composePath, cwd, existsSync, exec, logger? })` (gleiche Rueckgaben)
- `provisionN8nOwner(n8nUrl, { request?, schedule?, logger?, maxAttempts? })`
- `findZombieListeners(netstatText, { ports?, ownPid? }) -> { port, pid }[]`; `killZombiePythonProcesses({ policy, execFileSync, platform?, ownPid?, logger? }) -> { port, pid }[]`

---

## Task 4: Overlay-Pruefung und Statuskarte

**Dateien:** `electron-app/overlay-status.js` (neu), `electron-app/overlay-status.test.js` (neu), `electron-app/overlay-managers.test.js` (neu), `mirofish-manager.js`, `clawport-manager.js`, `dashboard-manager.js`, `rowboat-manager.js`, `eyeterm-manager.js`, `video-manager.js`, `agentfarm-manager.js`, `flowzen-manager.js`, `main.js`, `package.json`.

- [ ] **Schritt 4.1 – Fehlschlagende Helfer-Tests.** Neue Datei `overlay-status.test.js`:
  ```js
  const assert = require('node:assert/strict');
  const http = require('node:http');
  const test = require('node:test');

  const { checkOverlayTarget, extractTitle, loadOverlay, statusCardHtml } = require('./overlay-status');

  const quiet = { log() {}, info() {}, warn() {}, error() {} };

  function startServer(handler) {
    return new Promise((resolve) => {
      const server = http.createServer(handler);
      server.listen(0, '127.0.0.1', () => resolve({ server, url: `http://127.0.0.1:${server.address().port}/` }));
    });
  }
  function stop(server) {
    server.closeAllConnections();
    return new Promise((resolve) => server.close(resolve));
  }
  function html(title) {
    return (req, res) => { res.writeHead(200, { 'Content-Type': 'text/html' }); res.end(`<html><head><title>${title}</title></head></html>`); };
  }
  function fakeWebContents({ destroyed = false } = {}) {
    const calls = [];
    return {
      calls,
      isDestroyed: () => destroyed,
      loadURL: async (url) => { calls.push(['url', url]); },
      loadFile: async (file) => { calls.push(['file', file]); },
    };
  }
  function cardOf(call) {
    assert.equal(call[0], 'url');
    const prefix = 'data:text/html;charset=utf-8,';
    assert.ok(call[1].startsWith(prefix), call[1]);
    return decodeURIComponent(call[1].slice(prefix.length));
  }

  test('vorhandene Datei wird geladen, fehlende Datei ergibt Statuskarte', async () => {
    const wc = fakeWebContents();
    const ok = await loadOverlay(wc, { space: 'ClawPort', targets: [{ kind: 'file', path: 'C:\\da\\index.html' }] },
      { existsSync: () => true, logger: quiet });
    assert.equal(ok.ok, true);
    assert.deepEqual(wc.calls, [['file', 'C:\\da\\index.html']]);

    const wc2 = fakeWebContents();
    const fehlt = await loadOverlay(wc2, { space: 'ClawPort', targets: [{ kind: 'file', path: 'C:\\weg\\index.html' }] },
      { existsSync: () => false, logger: quiet });
    assert.equal(fehlt.ok, false);
    const card = cardOf(wc2.calls[0]);
    assert.match(card, /ClawPort nicht verfuegbar – Datei fehlt/);
    assert.match(card, /C:\\weg\\index\.html/);
  });

  test('nicht erreichbare URL ergibt Statuskarte mit Grund', async () => {
    const { server, url } = await startServer(html('x'));
    await stop(server);
    const result = await checkOverlayTarget({ kind: 'url', url }, { logger: quiet });
    assert.equal(result.ok, false);
    assert.match(result.reason, /^nicht erreichbar \(ECONNREFUSED\)$/);
    assert.equal(result.target, url);
  });

  test('langsame URL: Statuskarte nach dem Timeout', async () => {
    const { server, url } = await startServer((req, res) => setTimeout(() => res.end('spaet'), 500));
    try {
      const result = await checkOverlayTarget({ kind: 'url', url, timeoutMs: 100 });
      assert.deepEqual(result, { ok: false, reason: 'keine Antwort in 100 ms', target: url });
    } finally {
      await stop(server);
    }
  });

  test('HTTP-Fehlerseite ergibt Statuskarte', async () => {
    const { server, url } = await startServer((req, res) => { res.writeHead(502); res.end('Bad Gateway'); });
    try {
      assert.deepEqual(await checkOverlayTarget({ kind: 'url', url }), { ok: false, reason: 'HTTP 502', target: url });
    } finally {
      await stop(server);
    }
  });

  test('fremde App (falscher Titel) ergibt Statuskarte, richtige App laedt', async () => {
    const fremd = await startServer(html('Rowboat'));
    const echt = await startServer(html('MiroFish Offline - Predict Everything'));
    try {
      const a = await checkOverlayTarget({ kind: 'url', url: fremd.url, expectTitle: /MiroFish/i });
      assert.deepEqual(a, { ok: false, reason: 'fremde App (Titel „Rowboat“)', target: fremd.url });
      const wc = fakeWebContents();
      const b = await loadOverlay(wc, { space: 'MiroFish', targets: [{ kind: 'url', url: echt.url, expectTitle: /MiroFish/i }] }, { logger: quiet });
      assert.equal(b.ok, true);
      assert.deepEqual(wc.calls, [['url', echt.url]]);
    } finally {
      await stop(fremd.server);
      await stop(echt.server);
    }
  });

  test('erstes verfuegbares Ziel gewinnt; sonst alle Gruende auf der Karte', async () => {
    const { server, url } = await startServer(html('x'));
    await stop(server);
    const wc = fakeWebContents();
    await loadOverlay(wc, { space: 'Dashboard', targets: [{ kind: 'url', url }, { kind: 'file', path: 'C:\\d\\index.html' }] },
      { existsSync: () => true, logger: quiet });
    assert.deepEqual(wc.calls, [['file', 'C:\\d\\index.html']]);

    const wc2 = fakeWebContents();
    const result = await loadOverlay(wc2, { space: 'Dashboard', targets: [{ kind: 'url', url }, { kind: 'file', path: 'C:\\d\\index.html' }] },
      { existsSync: () => false, logger: quiet });
    assert.equal(result.reason, 'nicht erreichbar (ECONNREFUSED); Datei fehlt');
    assert.equal(result.target, `${url} | C:\\d\\index.html`);
    assert.match(cardOf(wc2.calls[0]), /Dashboard nicht verfuegbar – nicht erreichbar \(ECONNREFUSED\); Datei fehlt/);
  });

  test('ungueltige und nicht-HTTP-URLs werden nicht geladen', async () => {
    assert.equal((await checkOverlayTarget({ kind: 'url', url: 'kein-url' })).reason, 'ungueltige URL');
    assert.equal((await checkOverlayTarget({ kind: 'url', url: 'file:///C:/x.html' })).reason, 'ungueltige URL');
    assert.equal((await checkOverlayTarget({ kind: 'url', url: '' })).reason, 'ungueltige URL');
  });

  test('unavailable-Ziel zeigt die Karte mit festem Grund', async () => {
    const wc = fakeWebContents();
    await loadOverlay(wc, { space: 'eyeTerm', targets: [{ kind: 'unavailable', reason: 'entfernt', target: 'http://127.0.0.1:8099' }] }, { logger: quiet });
    const card = cardOf(wc.calls[0]);
    assert.match(card, /eyeTerm nicht verfuegbar – entfernt/);
    assert.match(card, /http:\/\/127\.0\.0\.1:8099/);
  });

  test('zerstoerte webContents werden nach dem Check nicht mehr geladen', async () => {
    const wc = fakeWebContents({ destroyed: true });
    await loadOverlay(wc, { space: 'ClawPort', targets: [{ kind: 'file', path: 'C:\\x' }] }, { existsSync: () => true, logger: quiet });
    assert.deepEqual(wc.calls, []);
  });

  test('Statuskarte escaped HTML', () => {
    const out = statusCardHtml({ space: '<b>X</b>', reason: '"a" & b', target: '<script>' });
    assert.doesNotMatch(out, /<b>X<\/b>|<script>/);
    assert.match(out, /&lt;b&gt;X&lt;\/b&gt; nicht verfuegbar – &quot;a&quot; &amp; b/);
    assert.equal(extractTitle('<TITLE> Hallo </TITLE>'), 'Hallo');
  });
  ```
  `node --test overlay-status.test.js` → rot: `Cannot find module './overlay-status'`.

- [ ] **Schritt 4.2 – `overlay-status.js` (neu).**
  ```js
  /**
   * Gemeinsame Pruefung vor jedem Overlay-loadURL/loadFile (Spec 2026-10-15 Abschnitt 3).
   * Datei vorhanden bzw. URL erreichbar (Timeout 2 s, optional erwarteter <title>);
   * sonst Statuskarte "<Space> nicht verfuegbar – <Grund>" mit dem geprueften Ziel.
   */
  const fs = require('node:fs');

  const DEFAULT_TIMEOUT_MS = 2000;
  const CARD_PREFIX = 'data:text/html;charset=utf-8,';

  function resolveElectronParts(dependencies, names) {
    const missing = names.filter((name) => !dependencies[name]);
    const electron = missing.length > 0 ? require('electron') : {};
    return Object.fromEntries(names.map((name) => [name, dependencies[name] || electron[name]]));
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  function statusCardHtml({ space, reason, target }) {
    return '<!doctype html><html lang="de"><head><meta charset="utf-8">'
      + `<title>${escapeHtml(space)} nicht verfuegbar</title>`
      + '<style>html,body{margin:0;height:100%;background:#050510;color:#e6e6f0;font-family:system-ui,sans-serif}'
      + 'main{display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;gap:12px;padding:24px;box-sizing:border-box;text-align:center}'
      + 'h1{font-size:20px;margin:0}p{margin:0;color:#a0a0b8}code{color:#7fd4ff;word-break:break-all}</style></head>'
      + `<body><main><h1 data-status-card="1">${escapeHtml(space)} nicht verfuegbar – ${escapeHtml(reason)}</h1>`
      + `<p>Geprueftes Ziel: <code>${escapeHtml(target || '–')}</code></p></main></body></html>`;
  }

  function statusCardUrl(card) {
    return `${CARD_PREFIX}${encodeURIComponent(statusCardHtml(card))}`;
  }

  function extractTitle(html) {
    const match = /<title[^>]*>([^<]*)<\/title>/i.exec(String(html));
    return match ? match[1].trim() : '';
  }

  async function cancelBody(response) {
    try { await response.body?.cancel(); } catch { /* egal */ }
  }

  async function checkOverlayTarget(target, deps = {}) {
    const existsSync = deps.existsSync || fs.existsSync;
    const fetchImpl = deps.fetchImpl || globalThis.fetch;
    if (!target || target.kind === 'unavailable') {
      return { ok: false, reason: (target && target.reason) || 'kein Ziel konfiguriert', target: (target && target.target) || '' };
    }
    if (target.kind === 'file') {
      return existsSync(target.path)
        ? { ok: true, target: target.path }
        : { ok: false, reason: 'Datei fehlt', target: target.path };
    }
    if (target.kind !== 'url') throw new TypeError(`Unbekannte Zielart: ${target.kind}`);

    let parsed;
    try {
      parsed = new URL(target.url);
    } catch {
      return { ok: false, reason: 'ungueltige URL', target: String(target.url) };
    }
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
      return { ok: false, reason: 'ungueltige URL', target: target.url };
    }

    const timeoutMs = target.timeoutMs || DEFAULT_TIMEOUT_MS;
    let response;
    try {
      response = await fetchImpl(target.url, { signal: AbortSignal.timeout(timeoutMs), redirect: 'follow' });
    } catch (err) {
      if (err && (err.name === 'TimeoutError' || err.name === 'AbortError')) {
        return { ok: false, reason: `keine Antwort in ${timeoutMs} ms`, target: target.url };
      }
      const code = (err && err.cause && err.cause.code) || (err && err.message) || String(err);
      return { ok: false, reason: `nicht erreichbar (${code})`, target: target.url };
    }
    if (!response.ok) {
      await cancelBody(response);
      return { ok: false, reason: `HTTP ${response.status}`, target: target.url };
    }
    if (target.expectTitle) {
      let body;
      try {
        body = await response.text();
      } catch (err) {
        return { ok: false, reason: `Antwort unvollstaendig (${err && err.name === 'TimeoutError' ? `keine Antwort in ${timeoutMs} ms` : err.message})`, target: target.url };
      }
      const title = extractTitle(body);
      if (!target.expectTitle.test(title)) {
        return { ok: false, reason: `fremde App (Titel „${title || 'ohne Titel'}“)`, target: target.url };
      }
    } else {
      await cancelBody(response);
    }
    return { ok: true, target: target.url };
  }

  async function loadOverlay(webContents, { space, targets }, deps = {}) {
    const logger = deps.logger || console;
    const failures = [];
    let chosen = null;
    for (const target of targets) {
      const result = await checkOverlayTarget(target, deps);
      if (result.ok) {
        chosen = { target, result };
        break;
      }
      failures.push(result);
    }
    if (webContents.isDestroyed && webContents.isDestroyed()) {
      return chosen ? chosen.result : { ok: false, reason: 'Ansicht geschlossen', target: '' };
    }
    try {
      if (chosen) {
        if (chosen.target.kind === 'file') await webContents.loadFile(chosen.target.path);
        else await webContents.loadURL(chosen.target.url);
        return chosen.result;
      }
      const reason = failures.map((f) => f.reason).join('; ');
      const target = failures.map((f) => f.target).filter(Boolean).join(' | ');
      logger.warn(`[Overlay] ${space} nicht verfuegbar – ${reason} (${target})`);
      await webContents.loadURL(statusCardUrl({ space, reason, target }));
      return { ok: false, reason, target };
    } catch (err) {
      logger.warn(`[Overlay] ${space}: Laden fehlgeschlagen – ${err.message}`);
      return chosen ? chosen.result : { ok: false, reason: err.message, target: '' };
    }
  }

  module.exports = {
    CARD_PREFIX,
    DEFAULT_TIMEOUT_MS,
    checkOverlayTarget,
    extractTitle,
    loadOverlay,
    resolveElectronParts,
    statusCardHtml,
    statusCardUrl,
  };
  ```
  `node --test overlay-status.test.js` → `# fail 0`.

- [ ] **Schritt 4.3 – Fehlschlagende Manager-Tests.** Neue Datei `overlay-managers.test.js`:
  ```js
  const assert = require('node:assert/strict');
  const http = require('node:http');
  const test = require('node:test');

  const ClawPortManager = require('./clawport-manager');
  const DashboardManager = require('./dashboard-manager');
  const EyeTermManager = require('./eyeterm-manager');
  const MiroFishManager = require('./mirofish-manager');
  const RowboatManager = require('./rowboat-manager');
  const VideoManager = require('./video-manager');
  const { CARD_PREFIX } = require('./overlay-status');

  const quiet = { log() {}, info() {}, warn() {}, error() {} };

  function harness() {
    const views = [];
    class FakeBrowserView {
      constructor(options) {
        this.options = options;
        this.loads = [];
        this.handlers = new Map();
        this.webContents = {
          loadURL: async (url) => { this.loads.push(['url', url]); },
          loadFile: async (file) => { this.loads.push(['file', file]); },
          on: (event, handler) => this.handlers.set(event, handler),
          setWindowOpenHandler: () => {},
          openDevTools: () => {},
          insertCSS: () => {},
          isDestroyed: () => false,
          send: () => {},
          close: () => {},
          destroy: () => {},
          session: { webRequest: { onHeadersReceived() {} }, clearCache: async () => {} },
        };
        views.push(this);
      }
      setBounds() {}
    }
    const mainWindow = {
      on() {}, setBrowserView() {}, addBrowserView() {}, removeBrowserView() {}, getBrowserView: () => null,
      getContentBounds: () => ({ x: 0, y: 0, width: 1200, height: 800 }),
      isDestroyed: () => false, webContents: { send() {} },
    };
    const shell = { openExternal: async () => {} };
    return { views, FakeBrowserView, mainWindow, shell };
  }
  function card(load) {
    assert.equal(load[0], 'url');
    assert.ok(load[1].startsWith(CARD_PREFIX), load[1]);
    return decodeURIComponent(load[1].slice(CARD_PREFIX.length));
  }
  function startServer(handler) {
    return new Promise((resolve) => {
      const server = http.createServer(handler);
      server.listen(0, '127.0.0.1', () => resolve({ server, url: `http://127.0.0.1:${server.address().port}/` }));
    });
  }
  function stop(server) { server.closeAllConnections(); return new Promise((r) => server.close(r)); }
  const titled = (title) => (req, res) => { res.writeHead(200, { 'Content-Type': 'text/html' }); res.end(`<title>${title}</title>`); };

  test('MiroFish: nicht erreichbare MIROFISH_URL ergibt Statuskarte', async () => {
    const { server, url } = await startServer(titled('x'));
    await stop(server);
    const h = harness();
    const m = new MiroFishManager(h.mainWindow, { BrowserView: h.FakeBrowserView, shell: h.shell, env: { MIROFISH_URL: url }, overlayDeps: { logger: quiet } });
    m.show();
    await m.overlayReady;
    assert.match(card(h.views[0].loads[0]), /MiroFish nicht verfuegbar – nicht erreichbar/);
  });

  test('MiroFish: HTTP 502 ergibt Statuskarte statt Fehlerseite', async () => {
    const { server, url } = await startServer((req, res) => { res.writeHead(502); res.end('Bad Gateway'); });
    try {
      const h = harness();
      const m = new MiroFishManager(h.mainWindow, { BrowserView: h.FakeBrowserView, shell: h.shell, env: { MIROFISH_URL: url }, overlayDeps: { logger: quiet } });
      m.show();
      await m.overlayReady;
      assert.match(card(h.views[0].loads[0]), /MiroFish nicht verfuegbar – HTTP 502/);
    } finally { await stop(server); }
  });

  test('MiroFish: fremde App (falscher Titel) ergibt Statuskarte, echte App laedt', async () => {
    const fremd = await startServer(titled('Rowboat'));
    const echt = await startServer(titled('MiroFish Offline - Predict Everything'));
    try {
      const h = harness();
      const a = new MiroFishManager(h.mainWindow, { BrowserView: h.FakeBrowserView, shell: h.shell, env: { MIROFISH_URL: fremd.url }, overlayDeps: { logger: quiet } });
      a.show();
      await a.overlayReady;
      assert.match(card(h.views[0].loads[0]), /fremde App \(Titel „Rowboat“\)/);
      const b = new MiroFishManager(h.mainWindow, { BrowserView: h.FakeBrowserView, shell: h.shell, env: { MIROFISH_URL: echt.url }, overlayDeps: { logger: quiet } });
      b.show();
      await b.overlayReady;
      assert.deepEqual(h.views[1].loads, [['url', echt.url]]);
    } finally { await stop(fremd.server); await stop(echt.server); }
  });

  test('ClawPort: fehlender Build ergibt Statuskarte, vorhandener Build laedt', async () => {
    const h = harness();
    const fehlt = new ClawPortManager(h.mainWindow, { BrowserView: h.FakeBrowserView, shell: h.shell, env: {}, rendererPath: 'C:\\nirgends\\index.html', overlayDeps: { existsSync: () => false, logger: quiet } });
    fehlt.show();
    await fehlt.overlayReady;
    assert.match(card(h.views[0].loads[0]), /ClawPort nicht verfuegbar – Datei fehlt/);
    const da = new ClawPortManager(h.mainWindow, { BrowserView: h.FakeBrowserView, shell: h.shell, env: {}, rendererPath: 'C:\\da\\index.html', overlayDeps: { existsSync: () => true, logger: quiet } });
    da.show();
    await da.overlayReady;
    assert.deepEqual(h.views[1].loads, [['file', 'C:\\da\\index.html']]);
  });

  test('Dashboard: ohne DASHBOARD_DEV_URL und ohne Build Statuskarte, kein :5173', async () => {
    const h = harness();
    const d = new DashboardManager(h.mainWindow, { BrowserView: h.FakeBrowserView, env: {}, dashboardPath: 'C:\\nirgends\\index.html', overlayDeps: { existsSync: () => false, logger: quiet } });
    d.show();
    await d.overlayReady;
    assert.equal(h.views[0].loads.length, 1);
    assert.match(card(h.views[0].loads[0]), /Dashboard \(Coding Engine\) nicht verfuegbar – Datei fehlt/);
    assert.doesNotMatch(card(h.views[0].loads[0]), /5173/);
  });

  test('Dashboard: DASHBOARD_DEV_URL mit fremder App (Rowboat) ergibt Statuskarte', async () => {
    const fremd = await startServer(titled('Rowboat'));
    try {
      const h = harness();
      const d = new DashboardManager(h.mainWindow, { BrowserView: h.FakeBrowserView, env: { DASHBOARD_DEV_URL: fremd.url }, dashboardPath: 'C:\\nirgends\\index.html', overlayDeps: { existsSync: () => false, logger: quiet } });
      d.show();
      await d.overlayReady;
      assert.match(card(h.views[0].loads[0]), /fremde App \(Titel „Rowboat“\); Datei fehlt/);
    } finally { await stop(fremd.server); }
  });

  test('Rowboat: Platzhalter „kommt mit Teil 2“, Bridge startet nicht', async () => {
    const h = harness();
    const r = new RowboatManager(h.mainWindow, { BrowserView: h.FakeBrowserView, shell: h.shell, placeholderReason: 'kommt mit Teil 2', overlayDeps: { logger: quiet } });
    r.show();
    await r.overlayReady;
    assert.match(card(h.views[0].loads[0]), /Rowboat nicht verfuegbar – kommt mit Teil 2/);
    assert.equal(await r.startBridge(), false);
    assert.equal(r.bridgeProcess, null);
  });

  test('eyeTerm: Statuskarte „entfernt“', async () => {
    const h = harness();
    const e = new EyeTermManager(h.mainWindow, { BrowserView: h.FakeBrowserView, env: {}, overlayDeps: { logger: quiet } });
    e.show();
    await e.overlayReady;
    assert.match(card(h.views[0].loads[0]), /eyeTerm nicht verfuegbar – entfernt/);
  });

  test('Video/Laura: fehlender Renderer ergibt Statuskarte statt Absturz', async () => {
    const h = harness();
    const logger = Object.assign(() => {}, quiet);
    const v = new VideoManager(h.mainWindow, {
      BrowserView: h.FakeBrowserView, shell: h.shell, logger,
      resolveRendererPath: () => { throw new Error('Laura renderer missing; run pnpm laura:build (checked C:\\x\\index.html)'); },
    });
    v.show();
    await v.overlayReady;
    const text = card(h.views[0].loads[0]);
    assert.match(text, /Video\/Laura nicht verfuegbar – Renderer fehlt \(pnpm laura:build\)/);
    assert.match(text, /checked C:\\x\\index\.html/);
  });
  ```
  `node --test overlay-managers.test.js` → rot (z. B. `TypeError: Cannot read properties of undefined (reading 'BrowserView')` bzw. `overlayReady` ist `undefined`).

- [ ] **Schritt 4.4 – MiroFish (`mirofish-manager.js`).** Z. 10 `const { BrowserView } = require('electron');` ersetzen durch `const { loadOverlay, resolveElectronParts } = require('./overlay-status');`. Konstruktor Z. 17 `constructor(mainWindow) {` → `constructor(mainWindow, dependencies = {}) {` und direkt danach einfuegen:
  ```js
      const { BrowserView, shell } = resolveElectronParts(dependencies, ['BrowserView', 'shell']);
      this.BrowserView = BrowserView;
      this.shell = shell;
      this._env = dependencies.env || process.env;
      this._overlayDeps = dependencies.overlayDeps || {};
      this.overlayReady = null;
  ```
  Z. 26 `this.mirofishUrl = process.env.MIROFISH_URL || 'http://localhost:3001';` → `this.mirofishUrl = this._env.MIROFISH_URL || 'http://localhost:3001';`. In `createView()` `new BrowserView(` → `new this.BrowserView(`; Z. 58–60 ersetzen:
  ```js
      // Pruefung vor dem Laden: erreichbar (2 s) und wirklich MiroFish, sonst Statuskarte
      _mfLog('Pruefe und lade:', this.mirofishUrl);
      this.overlayReady = loadOverlay(this.mirofishView.webContents, {
        space: 'MiroFish',
        targets: [{ kind: 'url', url: this.mirofishUrl, expectTitle: /MiroFish/i }],
      }, this._overlayDeps);
  ```
  Z. 69 `require('electron').shell.openExternal(url);` → `this.shell.openExternal(url);`.

- [ ] **Schritt 4.5 – ClawPort (`clawport-manager.js`).** Z. 10 → `const { loadOverlay, resolveElectronParts } = require('./overlay-status');`. Konstruktor `constructor(mainWindow) {` → `constructor(mainWindow, dependencies = {}) {`, danach einfuegen:
  ```js
      const { BrowserView, shell } = resolveElectronParts(dependencies, ['BrowserView', 'shell']);
      this.BrowserView = BrowserView;
      this.shell = shell;
      this._env = dependencies.env || process.env;
      this._rendererPathOverride = dependencies.rendererPath || null;
      this._overlayDeps = dependencies.overlayDeps || {};
      this.overlayReady = null;
  ```
  Z. 27 `this.isDev = !!process.env.CLAWPORT_DEV_URL;` → `this.isDev = !!this._env.CLAWPORT_DEV_URL;`. In `createView()` `new BrowserView(` → `new this.BrowserView(`; den Lade-Block (`// Load renderer` bis zum schliessenden `}` des else, Z. 68–76) ersetzen:
  ```js
      // Pruefung vor dem Laden: Build vorhanden bzw. Dev-URL erreichbar, sonst Statuskarte
      const targets = this.isDev
        ? [{ kind: 'url', url: this._env.CLAWPORT_DEV_URL }]
        : [{ kind: 'file', path: this._rendererPathOverride || this._resolveRendererPath() }];
      _cpLog('Pruefe und lade:', targets.map((t) => t.url || t.path).join(' | '));
      this.overlayReady = loadOverlay(this.clawportView.webContents, { space: 'ClawPort', targets }, this._overlayDeps);
  ```
  `if (process.env.NODE_ENV === 'development' || this.isDev)` bleibt; `require('electron').shell.openExternal(url);` → `this.shell.openExternal(url);`.

- [ ] **Schritt 4.6 – Dashboard (`dashboard-manager.js`).** Z. 8 → `const { loadOverlay, resolveElectronParts } = require('./overlay-status');`. Den Kommentar + `const STACK_FRONTEND_URL = 'http://localhost:5173';` (Z. 13–19) loeschen und durch diesen Kommentar ersetzen:
  ```js
  // Quelle: DASHBOARD_DEV_URL (muss die Coding-Engine-App "DaveLovable" sein) oder der
  // gebaute Stand im Coding_engine-Submodul. Ohne beides: Statuskarte. Die fruehere
  // Vorgabe http://localhost:5173 entfaellt – dort laeuft inzwischen der Rowboat-Vite-Server.
  ```
  Konstruktor `constructor(mainWindow) {` → `constructor(mainWindow, dependencies = {}) {`; Z. 23–40 (von `this.mainWindow = mainWindow;` bis `this.isDev = !!this.dashboardUrl;`) ersetzen:
  ```js
      const { BrowserView } = resolveElectronParts(dependencies, ['BrowserView']);
      this.BrowserView = BrowserView;
      this._env = dependencies.env || process.env;
      this._overlayDeps = dependencies.overlayDeps || {};
      this.overlayReady = null;
      this.mainWindow = mainWindow;
      this.dashboardView = null;
      this.isVisible = false;
      this.dashboardUrl = this._env.DASHBOARD_DEV_URL || '';
      this.dashboardPath = dependencies.dashboardPath || path.join(__dirname, '..', 'python', 'spaces',
        'coding', 'Coding_engine', 'web-app', 'front', 'dist', 'index.html');
      this.isDev = !!this.dashboardUrl;
  ```
  In `createView()` `new BrowserView(` → `new this.BrowserView(` und den Lade-Block `// Load the dashboard — URL first …` bis zum Ende des `else`-Zweigs (Z. 72–88) ersetzen:
  ```js
      const targets = [];
      if (this.dashboardUrl) targets.push({ kind: 'url', url: this.dashboardUrl, expectTitle: /DaveLovable/i });
      targets.push({ kind: 'file', path: this.dashboardPath });
      _dmLog('Pruefe und lade:', targets.map((t) => t.url || t.path).join(' | '));
      this.overlayReady = loadOverlay(this.dashboardView.webContents, { space: 'Dashboard (Coding Engine)', targets }, this._overlayDeps);
  ```

- [ ] **Schritt 4.7 – Rowboat-Platzhalter (`rowboat-manager.js`).** Z. 13 `const { BrowserView } = require('electron');` → `const { loadOverlay, resolveElectronParts } = require('./overlay-status');`. Konstruktor `constructor(mainWindow) {` → `constructor(mainWindow, dependencies = {}) {`, danach:
  ```js
      const { BrowserView, shell } = resolveElectronParts(dependencies, ['BrowserView', 'shell']);
      this.BrowserView = BrowserView;
      this.shell = shell;
      // Teil 1: Rowboat ist nur Platzhalter ("kommt mit Teil 2"), Bridge und Renderer starten nicht.
      this.placeholderReason = dependencies.placeholderReason || '';
      this._overlayDeps = dependencies.overlayDeps || {};
      this.overlayReady = null;
  ```
  In `createView()` nach dem `new this.BrowserView({...});` (vorher `new BrowserView(`) einfuegen:
  ```js
      if (this.placeholderReason) {
        this.overlayReady = loadOverlay(this.rowboatView.webContents, {
          space: 'Rowboat',
          targets: [{ kind: 'unavailable', reason: this.placeholderReason, target: 'Roarboot-Einbettung' }],
        }, this._overlayDeps);
        return this.rowboatView;
      }
  ```
  den Block `// Load renderer` (Z. 86–94) ersetzen:
  ```js
      const targets = this.isDev
        ? [{ kind: 'url', url: process.env.ROWBOAT_DEV_URL }]
        : [{ kind: 'file', path: this._resolveRendererPath() }];
      this.overlayReady = loadOverlay(this.rowboatView.webContents, { space: 'Rowboat', targets }, this._overlayDeps);
  ```
  `require('electron').shell.openExternal(url);` → `this.shell.openExternal(url);`. In `startBridge()` als erste Zeilen:
  ```js
      if (this.placeholderReason) {
        _rbLog('Bridge nicht gestartet –', this.placeholderReason);
        return Promise.resolve(false);
      }
  ```
  In `main.js`, Block `'Rowboat manager'` (aus T1): `rowboatManager = new RowboatManager(mainWindow);` → `rowboatManager = new RowboatManager(mainWindow, { placeholderReason: 'kommt mit Teil 2' });`.

- [ ] **Schritt 4.8 – eyeTerm (`eyeterm-manager.js`).** Z. 10 → `const { loadOverlay, resolveElectronParts } = require('./overlay-status');`. Konstruktor `constructor(mainWindow) {` → `constructor(mainWindow, dependencies = {}) {`, danach:
  ```js
      const { BrowserView } = resolveElectronParts(dependencies, ['BrowserView']);
      this.BrowserView = BrowserView;
      this._env = dependencies.env || process.env;
      this._overlayDeps = dependencies.overlayDeps || {};
      this.overlayReady = null;
  ```
  `parseInt(process.env.EYETERM_STREAM_PORT || '8099', 10)` → `parseInt(this._env.EYETERM_STREAM_PORT || '8099', 10)`. In `createView()` `new BrowserView(` → `new this.BrowserView(`; Z. 56–58 (`// Load simple HTML page with MJPEG stream` + `const html = …` + `loadURL(html)`) ersetzen:
  ```js
      // eyeTerm ist geloescht (16.06., ec98a0c6): Statuskarte statt MJPEG-Stream.
      this.overlayReady = loadOverlay(this.eyetermView.webContents, {
        space: 'eyeTerm',
        targets: [{ kind: 'unavailable', reason: 'entfernt', target: this.streamUrl }],
      }, this._overlayDeps);
  ```

- [ ] **Schritt 4.9 – Video/Laura (`video-manager.js`).** Import Z. 13–16 ergaenzen: nach dem `laura-embed-config`-Import `const { loadOverlay } = require('./overlay-status');`. Im Konstruktor Z. 29–33 (`this.rendererPath = dependencies.rendererPath || resolveLauraRendererPath({ … });`) ersetzen:
  ```js
      this.rendererPath = null;
      this.rendererMissing = '';
      if (dependencies.rendererPath) {
        this.rendererPath = dependencies.rendererPath;
      } else {
        const resolveRendererPath = dependencies.resolveRendererPath || (() => resolveLauraRendererPath({
          dirname: __dirname,
          resourcesPath: process.resourcesPath || '',
          existsSync: fs.existsSync,
        }));
        try {
          this.rendererPath = resolveRendererPath();
        } catch (err) {
          this.rendererMissing = err.message;
          this.logger.warn('[VideoManager] Laura-Renderer fehlt – Statuskarte statt Abbruch');
        }
      }
      this._overlayDeps = dependencies.overlayDeps || { logger: this.logger };
      this.overlayReady = null;
  ```
  In `createView()` direkt nach dem `new this.BrowserView({ … });`-Block und VOR `this.logger.info('[VideoManager] Loading Laura renderer');` einfuegen:
  ```js
      if (!this.rendererPath) {
        this.overlayReady = loadOverlay(this.videoView.webContents, {
          space: 'Video/Laura',
          targets: [{ kind: 'unavailable', reason: 'Renderer fehlt (pnpm laura:build)', target: this.rendererMissing }],
        }, this._overlayDeps);
        return this.videoView;
      }
  ```

- [ ] **Schritt 4.10 – AgentFarm und Flowzen (Ziele unveraendert, nur Pruefung davor).** `agentfarm-manager.js`: nach Z. 12 `const { loadOverlay } = require('./overlay-status');`; Lade-Block Z. 68–76 ersetzen:
  ```js
      const targets = this.isDev
        ? [{ kind: 'url', url: process.env.AGENTFARM_DEV_URL }]
        : [{ kind: 'file', path: this._resolveRendererPath() }];
      _afLog('Pruefe und lade:', targets.map((t) => t.url || t.path).join(' | '));
      this.overlayReady = loadOverlay(this.agentfarmView.webContents, { space: 'AgentFarm', targets });
  ```
  `flowzen-manager.js`: nach Z. 12 `const { loadOverlay } = require('./overlay-status');`; Z. 54–55 ersetzen:
  ```js
      _fzLog('Pruefe und lade:', diaryPath);
      this.overlayReady = loadOverlay(this.flowzenView.webContents, { space: 'Flowzen', targets: [{ kind: 'file', path: diaryPath }] });
  ```
  (SWE Design bleibt laut Spec-Tabelle unveraendert: Server startet auf Anfrage, Fehler meldet `swedesign_view_status`.)

- [ ] **Schritt 4.11 – `package.json`** `test:unit`:
  ```json
  "test:unit": "node --test laura-embed-config.test.js laura-preload.test.js laura-embed-host.test.js video-manager.test.js laura-packaging.test.js laura-live-proof.test.js startup-policy.test.js supabase-config.test.js local-startup.test.js docker-cleanup.test.js overlay-status.test.js overlay-managers.test.js",
  ```

- [ ] **Schritt 4.12 – Gruen.**
  ```powershell
  cd C:\Users\User\ClaudeWork\wt-voice-startprofil\electron-app
  npm run test:unit
  node --check main.js; node --check agentfarm-manager.js; node --check flowzen-manager.js
  Select-String -Path dashboard-manager.js -Pattern "5173"     # keine Treffer ausser im Kommentar
  ```
  Erwartet: `# fail 0` (inkl. vorhandener `video-manager.test.js`).

- [ ] **Schritt 4.13 – Commit.**
  ```powershell
  cd C:\Users\User\ClaudeWork\wt-voice-startprofil
  git add electron-app/overlay-status.js electron-app/overlay-status.test.js electron-app/overlay-managers.test.js electron-app/mirofish-manager.js electron-app/clawport-manager.js electron-app/dashboard-manager.js electron-app/rowboat-manager.js electron-app/eyeterm-manager.js electron-app/video-manager.js electron-app/agentfarm-manager.js electron-app/flowzen-manager.js electron-app/main.js electron-app/package.json
  $env:ENTIRE_TEST_TTY='0'; git commit -m "feat(electron): Overlays pruefen Ziel vor dem Laden und zeigen sonst eine Statuskarte"
  ```

**Interfaces (fuer T5):**
- `resolveElectronParts(dependencies, names: string[]) -> object`
- `statusCardHtml({ space, reason, target }) -> string`; `statusCardUrl(card) -> string` (`CARD_PREFIX` + HTML)
- `checkOverlayTarget(target, { existsSync?, fetchImpl? }) -> Promise<{ ok, reason?, target }>`; Ziele: `{ kind: 'file', path }` | `{ kind: 'url', url, expectTitle?: RegExp, timeoutMs? }` | `{ kind: 'unavailable', reason, target }`
- `loadOverlay(webContents, { space, targets }, { existsSync?, fetchImpl?, logger? }) -> Promise<{ ok, reason?, target }>`
- Manager setzen `this.overlayReady` (Promise) beim Erzeugen der Ansicht.

---

## Task 5: OpenFang und Brain nur uebernehmen

**Dateien:** `electron-app/openfang-manager.js`, `electron-app/brain-manager.js`, `electron-app/adopt-managers.test.js` (neu), `electron-app/main.js`, `electron-app/package.json`.

- [ ] **Schritt 5.1 – Fehlschlagende Tests.** Neue Datei `adopt-managers.test.js`:
  ```js
  const assert = require('node:assert/strict');
  const { EventEmitter } = require('node:events');
  const http = require('node:http');
  const test = require('node:test');

  const BrainManager = require('./brain-manager');
  const OpenFangManager = require('./openfang-manager');
  const { CARD_PREFIX } = require('./overlay-status');
  const { createStartupPolicy } = require('./startup-policy');

  function recordingLogger() {
    const lines = [];
    const rec = (level) => (...parts) => lines.push(`${level} ${parts.map(String).join(' ')}`);
    return { lines, log: rec('log'), info: rec('info'), warn: rec('warn'), error: rec('error') };
  }
  function startServer(handler) {
    return new Promise((resolve) => {
      const server = http.createServer(handler);
      server.listen(0, '127.0.0.1', () => resolve({ server, port: server.address().port }));
    });
  }
  function stop(server) { server.closeAllConnections(); return new Promise((r) => server.close(r)); }
  function fakeSpawn(readyLine = 'API server listening on http://127.0.0.1:4200') {
    const calls = [];
    const spawn = (cmd, args) => {
      calls.push([cmd, ...args]);
      const proc = new EventEmitter();
      proc.stdout = new EventEmitter();
      proc.stderr = new EventEmitter();
      proc.pid = 4242;
      proc.kill = () => {};
      setImmediate(() => proc.stdout.emit('data', Buffer.from(readyLine)));
      return proc;
    };
    spawn.calls = calls;
    return spawn;
  }
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));

  // ---------- OpenFang ----------

  test('OpenFang fehlt und openfang-spawn aus: kein Start, kein taskkill, Status "fehlt"', async () => {
    const logger = recordingLogger();
    const policy = createStartupPolicy({}, { logger });
    const spawn = fakeSpawn();
    const killCalls = [];
    const m = new OpenFangManager({ policy, spawn, execFileSync: (...a) => killCalls.push(a), probeHealth: async () => false, sleep: async () => {}, logger });
    await m.start();
    m.destroy();
    await m.stop();
    assert.deepEqual(spawn.calls, []);
    assert.deepEqual(killCalls, []);
    assert.equal(m.status.state, 'fehlt');
    assert.match(m.status.reason, /openfang-spawn aus/);
    assert.ok(logger.lines.some((l) => l.includes('openfang-spawn uebersprungen')));
  });

  test('langsame Health (1,5 s) gilt als laufend – kein zweiter Start, auch wenn openfang-spawn erlaubt ist', async () => {
    const { server, port } = await startServer((req, res) => setTimeout(() => { res.writeHead(200); res.end('ok'); }, 1500));
    try {
      const policy = createStartupPolicy({ VIBEMIND_STARTUP_ALLOW: 'openfang-spawn' }, { logger: recordingLogger() });
      const spawn = fakeSpawn();
      const m = new OpenFangManager({ policy, spawn, healthUrl: `http://127.0.0.1:${port}/api/health`, logger: recordingLogger() });
      await m.start();
      assert.deepEqual(spawn.calls, []);
      assert.equal(m.isReady(), true);
      assert.equal(m.getPort(), port);
      assert.equal(m.status.state, 'uebernommen');
    } finally { await stop(server); }
  });

  test('uebernommenes OpenFang wird beim Beenden weder heruntergefahren noch getoetet', async () => {
    let shutdowns = 0;
    const { server, port } = await startServer((req, res) => {
      if (req.url === '/api/shutdown') shutdowns += 1;
      res.writeHead(200); res.end('ok');
    });
    try {
      const killCalls = [];
      const m = new OpenFangManager({ policy: createStartupPolicy({}), execFileSync: (...a) => killCalls.push(a), healthUrl: `http://127.0.0.1:${port}/api/health`, logger: recordingLogger() });
      await m.start();
      await m.stop();
      m.destroy();
      assert.equal(shutdowns, 0);
      assert.deepEqual(killCalls, []);
    } finally { await stop(server); }
  });

  test('openfang-spawn erlaubt und nichts laeuft: genau ein Start', async () => {
    const policy = createStartupPolicy({ VIBEMIND_STARTUP_ALLOW: 'openfang-spawn' }, { logger: recordingLogger() });
    const spawn = fakeSpawn();
    const m = new OpenFangManager({ policy, spawn, probeHealth: async () => false, sleep: async () => {}, logger: recordingLogger() });
    await m.start();
    assert.equal(spawn.calls.length, 1);
    assert.equal(spawn.calls[0][1], 'start');
  });

  // ---------- Brain ----------

  test('parseBrainPort akzeptiert nur 1..65535 und faellt sonst mit Warnung auf 5000 zurueck', () => {
    const logger = recordingLogger();
    assert.equal(BrainManager.parseBrainPort('5001', logger), 5001);
    assert.equal(BrainManager.parseBrainPort(' 5002 ', logger), 5002);
    assert.equal(BrainManager.parseBrainPort(undefined, logger), 5000);
    assert.equal(BrainManager.parseBrainPort('', logger), 5000);
    assert.equal(logger.lines.length, 0);
    for (const bad of ['5000abc', '70000', '0', '-1', '50.5', 'abc']) {
      assert.equal(BrainManager.parseBrainPort(bad, logger), 5000, bad);
    }
    assert.equal(logger.lines.filter((l) => l.startsWith('warn') && l.includes('BRAIN_PORT')).length, 6);
  });

  function brainHarness({ env, policy, probe }) {
    const views = [];
    class FakeBrowserView {
      constructor(options) {
        this.options = options;
        this.loads = [];
        this.webContents = {
          loadURL: async (url) => { this.loads.push(url); },
          loadFile: async () => {},
          on: () => {}, setWindowOpenHandler: () => {}, isDestroyed: () => false, send: () => {}, close: () => {}, reload: () => {},
        };
        views.push(this);
      }
      setBounds() {}
    }
    const mainWindow = {
      on() {}, setBrowserView() {}, getContentBounds: () => ({ width: 1200, height: 800 }),
      isDestroyed: () => false, webContents: { send() {} },
    };
    const probes = [];
    const spawn = fakeSpawn('Uvicorn running on http://127.0.0.1:5000');
    const logger = recordingLogger();
    const m = new BrainManager(mainWindow, {
      BrowserView: FakeBrowserView, shell: { openExternal: async () => {} },
      env, policy, spawn, logger,
      probeHealth: async (port) => { probes.push(port); return probe; },
      overlayDeps: { logger },
    });
    return { m, views, probes, spawn, logger };
  }

  test('Brain fehlt, brain-spawn aus: Statuskarte, kein Start, keine Schleife', async () => {
    const h = brainHarness({ env: { BRAIN_PORT: '5000' }, policy: createStartupPolicy({}), probe: false });
    await h.m.startHeadless();
    await h.m.show();
    await h.m.overlayReady;
    await wait(100);
    assert.deepEqual(h.spawn.calls, []);
    assert.deepEqual(h.probes, [5000, 5000]);
    const url = h.views[0].loads[0];
    assert.ok(url.startsWith(CARD_PREFIX));
    assert.match(decodeURIComponent(url.slice(CARD_PREFIX.length)), /Brain nicht verfuegbar – kein Brain auf :5000 \(brain-spawn aus\)/);
  });

  test('Brain laeuft auf BRAIN_PORT: wird uebernommen und geladen, kein Start', async () => {
    const h = brainHarness({ env: { BRAIN_PORT: '5002', SKIP_BRAIN_SPAWN: '1' }, policy: createStartupPolicy({ VIBEMIND_STARTUP_ALLOW: 'brain-spawn' }), probe: true });
    await h.m.show();
    assert.deepEqual(h.spawn.calls, []);
    assert.deepEqual(h.probes, [5002]);
    assert.deepEqual(h.views[0].loads, ['http://localhost:5002/brain']);
  });

  test('SKIP_BRAIN_SPAWN schlaegt ein erlaubtes brain-spawn', async () => {
    const h = brainHarness({ env: { SKIP_BRAIN_SPAWN: 'true' }, policy: createStartupPolicy({ VIBEMIND_STARTUP_ALLOW: 'brain-spawn' }), probe: false });
    await h.m.startHeadless();
    assert.deepEqual(h.spawn.calls, []);
  });

  test('brain-spawn erlaubt und kein SKIP: eigenes Brain wird gestartet', async () => {
    const h = brainHarness({ env: {}, policy: createStartupPolicy({ VIBEMIND_STARTUP_ALLOW: 'brain-spawn' }), probe: false });
    await h.m.startHeadless();
    assert.equal(h.spawn.calls.length, 1);
    assert.deepEqual(h.spawn.calls[0].slice(1), ['-m', 'web.brain_server', '--port', '5000']);
  });
  ```
  `node --test adopt-managers.test.js` → rot (`OpenFangManager` ignoriert die Abhaengigkeiten → echter `spawn`/`openfang`; `BrainManager.parseBrainPort is not a function`). Falls der erste Rotlauf echte Prozesse starten koennte: die Tests ERST nach Schritt 5.2/5.3 laufen lassen (Rot nur mit `node --test --test-name-pattern parseBrainPort adopt-managers.test.js` belegen).

- [ ] **Schritt 5.2 – `openfang-manager.js`.** Z. 13–41 (Requires bis Ende des Konstruktors) ersetzen:
  ```js
  const path = require('path');
  const fs = require('fs');
  const http = require('http');
  const { spawn, execFileSync } = require('child_process');

  const FANG_COLOR = '\x1b[35m';  // Magenta
  const RST = '\x1b[0m';

  // Spec 2026-10-15: nur uebernehmen. Grosszuegige Probe (3 s × 3), damit ein langsam
  // antwortendes, laufendes OpenFang nie ein zweites openfang.exe ausloest.
  const DEFAULT_HEALTH_URL = 'http://127.0.0.1:4200/api/health';
  const HEALTH_TIMEOUT_MS = 3000;
  const HEALTH_ATTEMPTS = 3;
  const HEALTH_RETRY_DELAY_MS = 500;

  function probeHttpOk(url, timeoutMs) {
    return new Promise((resolve) => {
      let settled = false;
      const done = (value) => { if (!settled) { settled = true; resolve(value); } };
      const req = http.get(url, { timeout: timeoutMs }, (res) => {
        res.resume();
        done(res.statusCode === 200);
      });
      req.on('error', () => done(false));
      req.on('timeout', () => { req.destroy(); done(false); });
    });
  }

  class OpenFangManager {
    constructor(dependencies = {}) {
      this._process = null;
      this._ready = false;
      this._starting = false;
      this._adopted = false;
      this.port = null;
      this.status = { state: 'unbekannt', reason: '' };

      this._policy = dependencies.policy || null;
      this._spawn = dependencies.spawn || spawn;
      this._execFileSync = dependencies.execFileSync || execFileSync;
      this._probe = dependencies.probeHealth || probeHttpOk;
      this._healthUrl = dependencies.healthUrl || DEFAULT_HEALTH_URL;
      this._logger = dependencies.logger || console;
      this._sleep = dependencies.sleep || ((ms) => new Promise((r) => setTimeout(r, ms)));

      // Find binary: release → debug → PATH
      const projectRoot = path.resolve(__dirname, '..', '..'); // vibemind-os/
      const releaseBin = path.join(projectRoot, 'openfang', 'target', 'release', 'openfang.exe');
      const debugBin = path.join(projectRoot, 'openfang', 'target', 'debug', 'openfang.exe');

      if (fs.existsSync(releaseBin)) {
        this._binary = releaseBin;
      } else if (fs.existsSync(debugBin)) {
        this._binary = debugBin;
      } else {
        this._binary = 'openfang'; // hope it's in PATH
      }

      this._cwd = path.join(projectRoot, 'openfang');
    }

    async _isRunningExternally() {
      for (let attempt = 0; attempt < HEALTH_ATTEMPTS; attempt += 1) {
        if (await this._probe(this._healthUrl, HEALTH_TIMEOUT_MS)) return true;
        if (attempt < HEALTH_ATTEMPTS - 1) await this._sleep(HEALTH_RETRY_DELAY_MS);
      }
      return false;
    }

    _spawnAllowed() {
      return !!this._policy && this._policy.isAllowed('openfang-spawn');
    }
  ```
  In `start()` den Block `// Check if already running externally` bis einschliesslich `} catch { /* continue to spawn */ }` (Z. 49–66) ersetzen:
  ```js
      if (await this._isRunningExternally()) {
        this.port = Number(new URL(this._healthUrl).port) || 4200;
        this._ready = true;
        this._adopted = true;
        this.status = { state: 'uebernommen', reason: '' };
        this._logger.log(`${FANG_COLOR}[OpenFang]${RST} Laeuft bereits auf Port ${this.port} – uebernommen`);
        return;
      }

      if (!this._spawnAllowed()) {
        this.status = { state: 'fehlt', reason: `keine Antwort von ${this._healthUrl} (openfang-spawn aus)` };
        if (this._policy) this._policy.logSkipped('openfang-spawn', `${this._binary} start`);
        this._logger.warn(`${FANG_COLOR}[OpenFang]${RST} nicht verfuegbar – ${this.status.reason}`);
        return;
      }
  ```
  `const proc = spawn(this._binary, …` → `const proc = this._spawn(this._binary, …`; nach `this._process = proc;` einfuegen `this.status = { state: 'gestartet', reason: '' };`. In `stop()` die erste Zeile `if (!this._process && !this._ready) return;` ersetzen:
  ```js
      // Nie einen uebernommenen (fremden) Daemon herunterfahren – nur den eigenen.
      if (!this._process) return;
  ```
  In `_forceKill()` `execFileSync('taskkill', …` → `this._execFileSync('taskkill', …`. Am Dateiende:
  ```js
  module.exports = OpenFangManager;
  module.exports.probeHttpOk = probeHttpOk;
  ```

- [ ] **Schritt 5.3 – `brain-manager.js`.** Z. 13 `const { BrowserView } = require('electron');` ersetzen durch `const { loadOverlay, resolveElectronParts } = require('./overlay-status');`. Vor `class BrainManager {` einfuegen:
  ```js
  function parseBrainPort(value, logger = console) {
    if (value === undefined || value === null || String(value).trim() === '') return 5000;
    const text = String(value).trim();
    if (/^\d+$/.test(text)) {
      const port = Number(text);
      if (port >= 1 && port <= 65535) return port;
    }
    logger.warn(`[BrainManager] BRAIN_PORT="${text}" ungueltig – nutze 5000`);
    return 5000;
  }
  ```
  Konstruktor `constructor(mainWindow) {` → `constructor(mainWindow, dependencies = {}) {`, direkt danach:
  ```js
      const { BrowserView, shell } = resolveElectronParts(dependencies, ['BrowserView', 'shell']);
      this.BrowserView = BrowserView;
      this.shell = shell;
      this._env = dependencies.env || process.env;
      this._policy = dependencies.policy || null;
      this._spawn = dependencies.spawn || spawn;
      this._logger = dependencies.logger || console;
      this._probe = dependencies.probeHealth || ((port) => this._probeHealth(port));
      this._overlayDeps = dependencies.overlayDeps || { logger: this._logger };
      this._adopted = false;
      this._statusCardView = false;
      this.overlayReady = null;
  ```
  Z. 63 `this.port = 5000;` → `this.port = parseBrainPort(this._env.BRAIN_PORT, this._logger);`. In `_tryStartOnPort` `const proc = spawn(this.pythonPath, [` → `const proc = this._spawn(this.pythonPath, [`. Vor `async startHeadless()` einfuegen:
  ```js
    _spawnAllowed() {
      const skip = this._env.SKIP_BRAIN_SPAWN === '1' || this._env.SKIP_BRAIN_SPAWN === 'true';
      return !skip && !!this._policy && this._policy.isAllowed('brain-spawn');
    }

    /**
     * Spec 2026-10-15: Brain nur uebernehmen (BRAIN_PORT), eigener Start nur mit
     * Startprofil "brain-spawn" und ohne SKIP_BRAIN_SPAWN. Keine Neustart-Schleife.
     */
    async _ensureServer() {
      if (this._serverReady) return { ok: true };
      if (this._spawnAllowed()) {
        this.port = await this._startServer();
        return { ok: true };
      }
      if (await this._probe(this.port)) {
        this._serverReady = true;
        this._adopted = true;
        this._logger.log(`${BRAIN_MGR_COLOR}[BrainManager]${RST} Brain auf :${this.port} uebernommen`);
        return { ok: true };
      }
      if (this._policy) this._policy.logSkipped('brain-spawn', `python -m web.brain_server --port ${this.port}`);
      return { ok: false, reason: `kein Brain auf :${this.port} (brain-spawn aus)` };
    }

    _showStatusCard(reason) {
      if (!this.view) {
        this.view = new this.BrowserView({ webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true } });
        this._statusCardView = true;
      }
      this.mainWindow.setBrowserView(this.view);
      this._updateBounds();
      this.isVisible = true;
      this.overlayReady = loadOverlay(this.view.webContents, {
        space: 'Brain',
        targets: [{ kind: 'unavailable', reason, target: `http://localhost:${this.port}/brain` }],
      }, this._overlayDeps);
    }
  ```
  `startHeadless()` (Z. 268–280) ersetzen:
  ```js
    async startHeadless() {
      if (this._serverReady || this._starting) return;
      this._starting = true;
      try {
        const result = await this._ensureServer();
        if (result.ok) {
          this._logger.log(`${BRAIN_MGR_COLOR}[BrainManager]${RST} Brain bereit auf Port ${this.port} (${this._adopted ? 'uebernommen' : 'headless'})`);
        } else {
          this._logger.warn(`${BRAIN_MGR_COLOR}[BrainManager]${RST} Brain nicht verfuegbar – ${result.reason}`);
        }
      } catch (err) {
        this._logger.warn(`${BRAIN_MGR_COLOR}[BrainManager]${RST} Headless start failed: ${err.message}`);
      } finally {
        this._starting = false;
      }
    }
  ```
  In `show()` den Block `// Start server if not running` … `this._starting = false;\n      }` (Z. 299–305) ersetzen:
  ```js
        if (!this._serverReady) {
          this._starting = true;
          let result;
          try {
            result = await this._ensureServer();
          } finally {
            this._starting = false;
          }
          if (!result.ok) {
            this._showStatusCard(result.reason);
            return;
          }
        }
        if (this._statusCardView) {
          try { this.view.webContents.close(); } catch (_) { /* schon zu */ }
          this.view = null;
          this._statusCardView = false;
        }
  ```
  `this.view = new BrowserView({` → `this.view = new this.BrowserView({`; `require('electron').shell.openExternal(url);` → `this.shell.openExternal(url);`. Dateiende:
  ```js
  module.exports = BrainManager;
  module.exports.parseBrainPort = parseBrainPort;
  ```

- [ ] **Schritt 5.4 – `main.js`.** Block `'Brain manager'` (aus T1) ersetzen:
  ```js
      await startupPolicy.runExternalStartup('Brain manager', async () => {
          // Nur uebernehmen auf BRAIN_PORT; eigener Start nur mit "brain-spawn" und ohne SKIP_BRAIN_SPAWN.
          brainManager = new BrainManager(mainWindow, { policy: startupPolicy });
          brainManager.startHeadless().catch(err =>
              console.warn('[Main] Brain headless start failed:', err.message)
          );
      });
  ```
  Block `'OpenFang'` ersetzen:
  ```js
      await startupPolicy.runExternalStartup('OpenFang', async () => {
          // Nur uebernehmen; Start/taskkill nur mit Startprofil "openfang-spawn".
          openfangManager = new OpenFangManager({ policy: startupPolicy });
          openfangManager.start()
              .then(() => {
                  if (openfangManager.status.state === 'fehlt' && mainWindow && !mainWindow.isDestroyed()) {
                      mainWindow.webContents.send('python-message', {
                          type: 'openfang_status',
                          status: 'unavailable',
                          message: `OpenFang nicht verfuegbar – ${openfangManager.status.reason}`,
                      });
                  }
              })
              .catch(err => console.warn('[Main] OpenFang start failed:', err.message));
      });
  ```
  `package.json` `test:unit` um ` adopt-managers.test.js` am Ende ergaenzen.

- [ ] **Schritt 5.5 – Gruen.** `npm run test:unit` → `# fail 0`; `node --check main.js`. Der Test `langsame Health (1,5 s)` dauert ~1,5 s.

- [ ] **Schritt 5.6 – Commit.**
  ```powershell
  cd C:\Users\User\ClaudeWork\wt-voice-startprofil
  git add electron-app/openfang-manager.js electron-app/brain-manager.js electron-app/adopt-managers.test.js electron-app/main.js electron-app/package.json
  $env:ENTIRE_TEST_TTY='0'; git commit -m "feat(electron): OpenFang und Brain nur uebernehmen, Start nur mit Startprofil"
  ```

**Interfaces:**
- `new OpenFangManager({ policy, spawn?, execFileSync?, probeHealth?(url, timeoutMs), healthUrl?, logger?, sleep? })`; `.status: { state: 'unbekannt'|'uebernommen'|'fehlt'|'gestartet', reason }`; `start()`, `stop()`, `destroy()`, `isReady()`, `getPort()`; `OpenFangManager.probeHttpOk(url, timeoutMs) -> Promise<boolean>`
- `new BrainManager(mainWindow, { BrowserView?, shell?, env?, policy, spawn?, logger?, probeHealth?(port), overlayDeps? })`; `BrainManager.parseBrainPort(value, logger?) -> number`

---

## Task 6: `VIBEMIND_ENV_FILE` und Startprofil nur aus dem Prozess

**Dateien:** `electron-app/env-files.js` (neu), `electron-app/env-files.test.js` (neu), `electron-app/main.js`, `electron-app/package.json`.

- [ ] **Schritt 6.1 – Fehlschlagende Tests.** Neue Datei `env-files.test.js`:
  ```js
  const assert = require('node:assert/strict');
  const path = require('node:path');
  const test = require('node:test');

  const { LAUNCH_ONLY_KEYS, loadEnvFiles, resolveEnvFiles } = require('./env-files');

  const DIR = 'C:\\betrieb\\voice\\electron-app';
  const quiet = { log() {}, warn() {}, info() {}, error() {} };

  // Verhaelt sich wie dotenv.config ohne override: setzt nur fehlende Schluessel.
  function fakeDotenv(files) {
    const calls = [];
    return {
      calls,
      config({ path: file, processEnv, override }) {
        calls.push(file);
        for (const [key, value] of Object.entries(files[file] || {})) {
          if (override || processEnv[key] === undefined) processEnv[key] = value;
        }
        return { parsed: files[file] || {} };
      },
    };
  }

  test('ohne VIBEMIND_ENV_FILE gilt der bisherige relative Pfad', () => {
    assert.deepEqual(resolveEnvFiles({ dirname: DIR, env: {} }), {
      rootEnv: path.join(DIR, '..', '..', '..', '.env'),
      voiceEnv: path.join(DIR, '..', '.env'),
    });
    assert.equal(resolveEnvFiles({ dirname: DIR, env: { VIBEMIND_ENV_FILE: '   ' } }).rootEnv, path.join(DIR, '..', '..', '..', '.env'));
  });

  test('VIBEMIND_ENV_FILE ersetzt die Master-.env', () => {
    const files = resolveEnvFiles({ dirname: DIR, env: { VIBEMIND_ENV_FILE: 'C:\\Users\\User\\Desktop\\Vibemind_V1\\.env' } });
    assert.equal(files.rootEnv, 'C:\\Users\\User\\Desktop\\Vibemind_V1\\.env');
    assert.equal(files.voiceEnv, path.join(DIR, '..', '.env'));
  });

  test('Master zuerst, voice ergaenzt nur fehlende Schluessel', () => {
    const env = { VIBEMIND_ENV_FILE: 'C:\\V1\\.env' };
    const dotenv = fakeDotenv({
      'C:\\V1\\.env': { OPENAI_API_KEY: 'master', N8N_API_URL: 'http://100.67.177.45:5678' },
      [path.join(DIR, '..', '.env')]: { OPENAI_API_KEY: 'voice', NUR_VOICE: '1' },
    });
    loadEnvFiles({ dirname: DIR, env, dotenv, existsSync: () => true, logger: quiet });
    assert.deepEqual(dotenv.calls, ['C:\\V1\\.env', path.join(DIR, '..', '.env')]);
    assert.equal(env.OPENAI_API_KEY, 'master');
    assert.equal(env.NUR_VOICE, '1');
  });

  test('VIBEMIND_STARTUP_ALLOW aus einer .env schaltet nichts ein', () => {
    assert.deepEqual([...LAUNCH_ONLY_KEYS], ['VIBEMIND_STARTUP_ALLOW']);
    const env = { VIBEMIND_ENV_FILE: 'C:\\V1\\.env' };
    const dotenv = fakeDotenv({ 'C:\\V1\\.env': { VIBEMIND_STARTUP_ALLOW: 'docker-cleanup,zombie-kill' } });
    loadEnvFiles({ dirname: DIR, env, dotenv, existsSync: () => true, logger: quiet });
    assert.equal(Object.prototype.hasOwnProperty.call(env, 'VIBEMIND_STARTUP_ALLOW'), false);
  });

  test('VIBEMIND_STARTUP_ALLOW aus dem Prozess bleibt erhalten', () => {
    const env = { VIBEMIND_STARTUP_ALLOW: 'brain-spawn' };
    const dotenv = fakeDotenv({ [path.join(DIR, '..', '..', '..', '.env')]: { VIBEMIND_STARTUP_ALLOW: 'docker-cleanup' } });
    loadEnvFiles({ dirname: DIR, env, dotenv, existsSync: () => true, logger: quiet });
    assert.equal(env.VIBEMIND_STARTUP_ALLOW, 'brain-spawn');
  });

  test('fehlende VIBEMIND_ENV_FILE wird gewarnt, nicht verschwiegen', () => {
    const warnings = [];
    loadEnvFiles({
      dirname: DIR, env: { VIBEMIND_ENV_FILE: 'C:\\weg\\.env' }, dotenv: fakeDotenv({}),
      existsSync: () => false, logger: { ...quiet, warn: (m) => warnings.push(m) },
    });
    assert.deepEqual(warnings, ['[Main] VIBEMIND_ENV_FILE zeigt auf eine fehlende Datei: C:\\weg\\.env']);
  });
  ```
  `node --test env-files.test.js` → rot: `Cannot find module './env-files'`.

- [ ] **Schritt 6.2 – `env-files.js` (neu).**
  ```js
  /**
   * .env-Laden fuer den Electron-Main-Prozess (Spec 2026-10-15 Abschnitt 2).
   * VIBEMIND_ENV_FILE ersetzt die Master-.env (Laufzeit-Checkout liegt nicht unter Vibemind_V1);
   * ohne Wert gilt der bisherige relative Pfad. Secrets werden nur gelesen, nie kopiert.
   * Startprofil-Schluessel kommen nur aus dem Prozess (Launcher), nie aus einer .env.
   */
  const path = require('node:path');

  const LAUNCH_ONLY_KEYS = Object.freeze(['VIBEMIND_STARTUP_ALLOW']);

  function resolveEnvFiles({ dirname, env }) {
    const explicit = typeof env.VIBEMIND_ENV_FILE === 'string' ? env.VIBEMIND_ENV_FILE.trim() : '';
    return {
      rootEnv: explicit ? path.resolve(explicit) : path.join(dirname, '..', '..', '..', '.env'),
      voiceEnv: path.join(dirname, '..', '.env'),
    };
  }

  function loadEnvFiles({ dirname, env, dotenv, existsSync, logger = console }) {
    const launchValues = Object.fromEntries(LAUNCH_ONLY_KEYS.map((key) => [key, env[key]]));
    const files = resolveEnvFiles({ dirname, env });
    if (typeof env.VIBEMIND_ENV_FILE === 'string' && env.VIBEMIND_ENV_FILE.trim() && !existsSync(files.rootEnv)) {
      logger.warn(`[Main] VIBEMIND_ENV_FILE zeigt auf eine fehlende Datei: ${files.rootEnv}`);
    }
    dotenv.config({ path: files.rootEnv, processEnv: env });                   // master first
    dotenv.config({ path: files.voiceEnv, override: false, processEnv: env }); // voice-only keys added
    for (const key of LAUNCH_ONLY_KEYS) {
      if (launchValues[key] === undefined) delete env[key];
      else env[key] = launchValues[key];
    }
    return files;
  }

  module.exports = { LAUNCH_ONLY_KEYS, loadEnvFiles, resolveEnvFiles };
  ```

- [ ] **Schritt 6.3 – `main.js`.** Z. 31–36 (Kommentar + `_rootEnv`/`_voiceEnv` + zwei `dotenv.config`) ersetzen:
  ```js
  // .env: VIBEMIND_ENV_FILE (Launcher: <Vibemind_V1>\.env) oder Vibemind_V1/.env relativ,
  // danach vibemind-os/voice/.env (nur fehlende Schluessel). Startprofil nur aus dem Prozess.
  const { loadEnvFiles } = require('./env-files');
  const _envFiles = loadEnvFiles({
      dirname: __dirname,
      env: process.env,
      dotenv: require('dotenv'),
      existsSync: fs.existsSync,
  });
  console.log('[Main] .env-Dateien:', _envFiles.rootEnv, '+', _envFiles.voiceEnv);
  ```
  `package.json` `test:unit` um ` env-files.test.js` ergaenzen.

- [ ] **Schritt 6.4 – Gruen.** `npm run test:unit` → `# fail 0`; `node --check main.js`. Pruefen, dass `fs` vor Z. 31 deklariert ist (Z. 16 `const fs = require('fs');` – ja).

- [ ] **Schritt 6.5 – Commit.**
  ```powershell
  git add electron-app/env-files.js electron-app/env-files.test.js electron-app/main.js electron-app/package.json
  $env:ENTIRE_TEST_TTY='0'; git commit -m "feat(electron): VIBEMIND_ENV_FILE fuer die Master-.env, Startprofil nur aus der Prozessumgebung"
  ```

- [ ] **Schritt 6.6 – Gesamtpruefung voice-Branch.**
  ```powershell
  cd C:\Users\User\ClaudeWork\wt-voice-startprofil\electron-app; npm run test:unit
  cd ..\python; C:\Users\User\Desktop\Vibemind_V1\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests/test_startprofil_backend.py -q
  git -C C:\Users\User\ClaudeWork\wt-voice-startprofil log --oneline a506ca2..HEAD
  ```
  Erwartet: alles gruen; 7 Commits auf `feat/startprofil`. Danach Code-Review (superpowers:requesting-code-review) gegen den Review-Fokus oben.

**Interfaces:** `LAUNCH_ONLY_KEYS`; `resolveEnvFiles({ dirname, env }) -> { rootEnv, voiceEnv }`; `loadEnvFiles({ dirname, env, dotenv, existsSync, logger? }) -> { rootEnv, voiceEnv }`.

---

## Task 7: Aeusseres Repo – Update-Skript und Launcher Phase 5/6

**Dateien (Repo `C:\Users\User\Desktop\Vibemind_V1`, Branch `codex/vibemind-ops-baseline`):** `scripts/voice_betrieb_update.ps1` (neu), `scripts/tests/voice-betrieb-update.Tests.ps1` (neu), `scripts/lib/vibemind-helpers.ps1`, `scripts/vibemind-start.ps1`, `scripts/tests/voice-electron-launch.Tests.ps1` (neu).

- [ ] **Schritt 7.0 – Vorpruefung.** `git -C C:\Users\User\Desktop\Vibemind_V1 status --short --branch` → `codex/vibemind-ops-baseline`, keine fremden Aenderungen an den vier Zieldateien. Sonst STOP.

- [ ] **Schritt 7.1 – Fehlschlagender Pester-Test Update-Skript.** Neue Datei `scripts/tests/voice-betrieb-update.Tests.ps1`:
  ```powershell
  #Requires -Version 7
  BeforeAll {
      $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
      $script:UpdateScript = Join-Path $repoRoot 'scripts\voice_betrieb_update.ps1'
      $script:Base = Join-Path $HOME ('.vbu-tests\update-' + [guid]::NewGuid().ToString('N'))
      New-Item -ItemType Directory -Path $script:Base -Force | Out-Null
      # Lokale Submodul-URLs erlauben (nur fuer diesen Testprozess)
      $env:GIT_CONFIG_COUNT = '1'
      $env:GIT_CONFIG_KEY_0 = 'protocol.file.allow'
      $env:GIT_CONFIG_VALUE_0 = 'always'

      function Invoke-T {
          param([string]$Dir, [string[]]$GitArgs)
          $o = & git -C $Dir -c user.name=t -c user.email=t@t @GitArgs 2>&1
          if ($LASTEXITCODE -ne 0) { throw "git $($GitArgs -join ' '): $o" }
      }

      $script:VoiceSrc = Join-Path $script:Base 'voice-src'
      $script:OsSrc = Join-Path $script:Base 'os-src'
      & git init -q -b main $script:VoiceSrc
      New-Item -ItemType Directory -Path (Join-Path $script:VoiceSrc 'electron-app') -Force | Out-Null
      Set-Content -LiteralPath (Join-Path $script:VoiceSrc 'electron-app\package-lock.json') -Value '{"lockfileVersion":3,"v":1}'
      Set-Content -LiteralPath (Join-Path $script:VoiceSrc 'requirements.txt') -Value 'python-dotenv'
      Invoke-T $script:VoiceSrc @('add', '-A')
      Invoke-T $script:VoiceSrc @('commit', '-q', '-m', 'init')
      & git init -q -b master $script:OsSrc
      Invoke-T $script:OsSrc @('submodule', 'add', '-q', $script:VoiceSrc, 'voice')
      Invoke-T $script:OsSrc @('commit', '-q', '-m', 'pin voice')

      $script:Runtime = Join-Path $script:Base 'betrieb'
      $script:NpmLog = Join-Path $script:Base 'npm.log'
      $script:FakeNpm = Join-Path $script:Base 'fake-npm.cmd'
      Set-Content -LiteralPath $script:FakeNpm -Encoding ascii -Value (
          "@echo off`r`nif not exist node_modules mkdir node_modules`r`necho %CD% %*>>`"$script:NpmLog`"`r`nexit /b 0`r`n")

      function Invoke-Update {
          & $script:UpdateScript -RuntimeDir $script:Runtime -RepoUrl $script:OsSrc -Npm $script:FakeNpm -SkipPython -SkipOverlayBuilds *>&1 | Out-String
      }
      function Get-NpmCalls {
          if (Test-Path -LiteralPath $script:NpmLog) { return @(Get-Content -LiteralPath $script:NpmLog) }
          return @()
      }
      function Push-VoiceChange {
          param([string]$RelPath, [string]$Content)
          Set-Content -LiteralPath (Join-Path $script:VoiceSrc $RelPath) -Value $Content
          Invoke-T $script:VoiceSrc @('add', '-A')
          Invoke-T $script:VoiceSrc @('commit', '-q', '-m', "aendere $RelPath")
          $sub = Join-Path $script:OsSrc 'voice'
          Invoke-T $sub @('fetch', '-q', 'origin')
          Invoke-T $sub @('checkout', '-q', 'origin/main')
          Invoke-T $script:OsSrc @('add', 'voice')
          Invoke-T $script:OsSrc @('commit', '-q', '-m', 'pin bump')
      }
  }

  AfterAll {
      Remove-Item -LiteralPath $script:Base -Recurse -Force -ErrorAction SilentlyContinue
      Remove-Item Env:GIT_CONFIG_COUNT, Env:GIT_CONFIG_KEY_0, Env:GIT_CONFIG_VALUE_0 -ErrorAction SilentlyContinue
  }

  Describe 'voice_betrieb_update.ps1' {
      It 'klont beim ersten Lauf und fuehrt npm ci genau einmal aus' {
          $out = Invoke-Update
          @(Get-NpmCalls).Count | Should -Be 1
          (Get-NpmCalls)[0] | Should -Match 'electron-app ci\s*$'
          $out | Should -Match 'Neu gebaut: npm ci \(electron-app\)'
          Test-Path -LiteralPath (Join-Path $script:Runtime 'voice\electron-app\package-lock.json') | Should -BeTrue
      }
      It 'laesst npm ci weg, wenn sich nichts geaendert hat' {
          $out = Invoke-Update
          @(Get-NpmCalls).Count | Should -Be 1
          $out | Should -Match 'Neu gebaut: nichts'
      }
      It 'laesst npm ci weg, wenn sich in voice nur andere Dateien aendern' {
          Push-VoiceChange -RelPath 'README.md' -Content 'neu'
          $out = Invoke-Update
          @(Get-NpmCalls).Count | Should -Be 1
          $out | Should -Match 'Neu gebaut: nichts'
          Test-Path -LiteralPath (Join-Path $script:Runtime 'voice\README.md') | Should -BeTrue
      }
      It 'fuehrt npm ci erneut aus, wenn sich das Lockfile geaendert hat' {
          Push-VoiceChange -RelPath 'electron-app\package-lock.json' -Content '{"lockfileVersion":3,"v":2}'
          $out = Invoke-Update
          @(Get-NpmCalls).Count | Should -Be 2
          $out | Should -Match 'Neuer Stand: vibemind-os [0-9a-f]+ / voice [0-9a-f]+'
      }
      It 'bricht bei eigenen Aenderungen im Laufzeit-Checkout ab und baut nichts' {
          Add-Content -LiteralPath (Join-Path $script:Runtime 'voice\electron-app\package-lock.json') -Value ' '
          Push-VoiceChange -RelPath 'electron-app\package-lock.json' -Content '{"lockfileVersion":3,"v":3}'
          { & $script:UpdateScript -RuntimeDir $script:Runtime -RepoUrl $script:OsSrc -Npm $script:FakeNpm -SkipPython -SkipOverlayBuilds } |
              Should -Throw '*eigene Aenderungen*'
          @(Get-NpmCalls).Count | Should -Be 2
      }
  }
  ```
  Rot sehen:
  ```powershell
  cd C:\Users\User\Desktop\Vibemind_V1
  Import-Module Pester -MinimumVersion 5.0
  Invoke-Pester -Path scripts\tests\voice-betrieb-update.Tests.ps1 -Output Detailed
  ```
  Erwartet: alle 5 `It` FAILED (`The term '...voice_betrieb_update.ps1' is not recognized`).

- [ ] **Schritt 7.2 – `scripts/voice_betrieb_update.ps1` (neu).**
  ```powershell
  #Requires -Version 7
  <#
  .SYNOPSIS
      Zieht den Laufzeit-Checkout der voice-App (vibemind-betrieb) auf den origin/master-Pin nach.
  .DESCRIPTION
      Spec: vibemind-os/docs/superpowers/specs/2026-10-15-voice-app-wiederbeleben-design.md, Abschnitt 2.
        1. Abbruch, wenn der Laufzeit-Checkout eigene Aenderungen hat (aeusserer Klon oder voice).
        2. git fetch, Checkout des origin/<Branch>-Pins, git submodule update --init voice.
        3. npm ci in voice/electron-app nur, wenn sich package-lock.json geaendert hat.
        4. pip install -r requirements.txt in voice/.venv312 nur bei geaenderten Requirements;
           fehlt die venv, wird sie mit Python 3.12 angelegt.
        5. Overlay-Builds ClawPort (electron-app/dashboard) und AgentFarm (electron-app/agentfarm),
           wenn die Quellen vorhanden sind; ein Build-Fehler ist nicht fatal (Overlay zeigt Statuskarte).
        6. Ausgabe: alter und neuer Stand, was neu gebaut wurde.
      Secrets werden weder kopiert noch verschoben (die App liest VIBEMIND_ENV_FILE).
  .EXAMPLE
      pwsh -File scripts\voice_betrieb_update.ps1
  #>
  [CmdletBinding()]
  param(
      [string]$RuntimeDir = 'C:\Users\User\ClaudeWork\vibemind-betrieb',
      [string]$RepoUrl = 'https://github.com/Flissel/vibemind-os.git',
      [string]$Branch = 'master',
      [string]$Npm = 'npm',
      [string]$PythonLauncher = 'py',
      [switch]$SkipPython,
      [switch]$SkipOverlayBuilds
  )

  $ErrorActionPreference = 'Stop'
  Set-StrictMode -Version Latest

  function Invoke-Git {
      param([Parameter(Mandatory)][string]$Dir, [Parameter(Mandatory)][string[]]$GitArgs)
      $out = & git -c core.longpaths=true -C $Dir @GitArgs 2>&1
      if ($LASTEXITCODE -ne 0) { throw "git $($GitArgs -join ' ') fehlgeschlagen in ${Dir}: $($out -join ' ')" }
      return @($out | ForEach-Object { "$_" })
  }

  function Get-ShortHead {
      param([string]$Dir)
      if (-not (Test-Path -LiteralPath (Join-Path $Dir '.git'))) { return '(fehlt)' }
      return (Invoke-Git -Dir $Dir -GitArgs @('rev-parse', '--short', 'HEAD'))[0].Trim()
  }

  function Get-BlobSha {
      param([string]$Dir, [string]$RelPath)
      $sha = & git -C $Dir rev-parse --verify --quiet "HEAD:$RelPath" 2>$null
      if ($LASTEXITCODE -ne 0 -or -not $sha) { return '' }
      return "$sha".Trim()
  }

  function Read-Marker {
      param([string]$Path)
      if (Test-Path -LiteralPath $Path) { return (Get-Content -LiteralPath $Path -Raw).Trim() }
      return ''
  }

  function Write-Marker {
      param([string]$Path, [string]$Value)
      Set-Content -LiteralPath $Path -Value $Value -Encoding ascii -NoNewline
  }

  function Invoke-Tool {
      param([string]$Exe, [string[]]$Arguments, [string]$WorkDir)
      Push-Location -LiteralPath $WorkDir
      try {
          & $Exe @Arguments | Out-Host
          if ($LASTEXITCODE -ne 0) { throw "$Exe $($Arguments -join ' ') fehlgeschlagen (Exit $LASTEXITCODE)" }
      } finally {
          Pop-Location
      }
  }

  $voiceDir = Join-Path $RuntimeDir 'voice'
  $electronDir = Join-Path $voiceDir 'electron-app'
  $neu = [System.Collections.Generic.List[string]]::new()

  # 1. Eigene Aenderungen? (nur getrackte Dateien; node_modules/.venv312/dist sind ignoriert)
  if (Test-Path -LiteralPath (Join-Path $RuntimeDir '.git')) {
      $dirty = @(Invoke-Git -Dir $RuntimeDir -GitArgs @('status', '--porcelain', '--untracked-files=no'))
      if (Test-Path -LiteralPath (Join-Path $voiceDir '.git')) {
          $dirty += @(Invoke-Git -Dir $voiceDir -GitArgs @('status', '--porcelain', '--untracked-files=no'))
      }
      $dirty = @($dirty | Where-Object { $_.Trim() })
      if ($dirty.Count -gt 0) {
          throw "Laufzeit-Checkout $RuntimeDir hat eigene Aenderungen - Abbruch, nichts veraendert:`n$($dirty -join "`n")"
      }
      $altOs = Get-ShortHead $RuntimeDir
      $altVoice = Get-ShortHead $voiceDir
  } else {
      if ((Test-Path -LiteralPath $RuntimeDir) -and @(Get-ChildItem -LiteralPath $RuntimeDir -Force).Count -gt 0) {
          throw "$RuntimeDir existiert und ist kein Git-Checkout - Abbruch."
      }
      $cloneOut = & git -c core.longpaths=true clone --quiet --no-checkout $RepoUrl $RuntimeDir 2>&1
      if ($LASTEXITCODE -ne 0) { throw "git clone fehlgeschlagen: $($cloneOut -join ' ')" }
      $altOs = '(neu)'
      $altVoice = '(neu)'
  }

  # 2. Pin holen und auschecken, nur Submodul voice
  $null = Invoke-Git -Dir $RuntimeDir -GitArgs @('fetch', '--quiet', 'origin', $Branch)
  $pin = (Invoke-Git -Dir $RuntimeDir -GitArgs @('rev-parse', "origin/$Branch"))[0].Trim()
  $null = Invoke-Git -Dir $RuntimeDir -GitArgs @('checkout', '--quiet', '--detach', $pin)
  $null = Invoke-Git -Dir $RuntimeDir -GitArgs @('submodule', 'update', '--init', '--quiet', 'voice')

  # 3. npm ci nur bei geaendertem Lockfile (Marker = installierter Lockfile-Blob)
  $lockSha = Get-BlobSha $voiceDir 'electron-app/package-lock.json'
  $nodeModules = Join-Path $electronDir 'node_modules'
  $lockMarker = Join-Path $nodeModules '.vbu-lock-sha'
  if ($lockSha -and ((-not (Test-Path -LiteralPath $nodeModules)) -or (Read-Marker $lockMarker) -ne $lockSha)) {
      Invoke-Tool -Exe $Npm -Arguments @('ci') -WorkDir $electronDir
      Write-Marker $lockMarker $lockSha
      $neu.Add('npm ci (electron-app)')
  }

  # 4. Python-venv und Requirements
  if (-not $SkipPython) {
      $venvDir = Join-Path $voiceDir '.venv312'
      $venvPy = Join-Path $venvDir 'Scripts\python.exe'
      if (-not (Test-Path -LiteralPath $venvPy)) {
          Invoke-Tool -Exe $PythonLauncher -Arguments @('-3.12', '-m', 'venv', '.venv312') -WorkDir $voiceDir
          $neu.Add('venv .venv312 angelegt (Python 3.12)')
      }
      $reqSha = Get-BlobSha $voiceDir 'requirements.txt'
      $reqMarker = Join-Path $venvDir '.vbu-req-sha'
      if ($reqSha -and (Read-Marker $reqMarker) -ne $reqSha) {
          Invoke-Tool -Exe $venvPy -Arguments @('-m', 'pip', 'install', '-r', 'requirements.txt') -WorkDir $voiceDir
          Write-Marker $reqMarker $reqSha
          $neu.Add('pip install -r requirements.txt')
      }
  }

  # 5. Overlay-Builds (ClawPort laut Spec; AgentFarm, weil agentfarm/dist gitignored ist)
  if (-not $SkipOverlayBuilds) {
      $overlays = @(
          [pscustomobject]@{ Name = 'ClawPort'; Rel = 'dashboard' },
          [pscustomobject]@{ Name = 'AgentFarm'; Rel = 'agentfarm' }
      )
      foreach ($ov in $overlays) {
          $src = Join-Path $electronDir $ov.Rel
          if (-not (Test-Path -LiteralPath (Join-Path $src 'package.json'))) {
              Write-Output "$($ov.Name): Quellen fehlen - uebersprungen (Overlay zeigt Statuskarte)"
              continue
          }
          $treeSha = Get-BlobSha $voiceDir "electron-app/$($ov.Rel)"
          $distIndex = Join-Path $src 'dist\index.html'
          $marker = Join-Path $src 'dist\.vbu-src-sha'
          if ((Test-Path -LiteralPath $distIndex) -and (Read-Marker $marker) -eq $treeSha) { continue }
          try {
              $installArgs = if (Test-Path -LiteralPath (Join-Path $src 'package-lock.json')) { @('ci') } else { @('install', '--no-audit', '--no-fund') }
              Invoke-Tool -Exe $Npm -Arguments $installArgs -WorkDir $src
              Invoke-Tool -Exe $Npm -Arguments @('run', 'build') -WorkDir $src
              Write-Marker $marker $treeSha
              $neu.Add("$($ov.Name) gebaut")
          } catch {
              Write-Warning "$($ov.Name): Build fehlgeschlagen - Overlay zeigt Statuskarte ($($_.Exception.Message))"
              $neu.Add("$($ov.Name) Build fehlgeschlagen")
          }
      }
  }

  # 6. Ausgabe
  Write-Output "Alter Stand: vibemind-os $altOs / voice $altVoice"
  Write-Output "Neuer Stand: vibemind-os $(Get-ShortHead $RuntimeDir) / voice $(Get-ShortHead $voiceDir)"
  Write-Output ('Neu gebaut: ' + $(if ($neu.Count -gt 0) { $neu -join ', ' } else { 'nichts' }))
  ```
  `Invoke-Pester -Path scripts\tests\voice-betrieb-update.Tests.ps1 -Output Detailed` → `Tests Passed: 5, Failed: 0`.

- [ ] **Schritt 7.3 – Fehlschlagender Pester-Test Launcher.** Neue Datei `scripts/tests/voice-electron-launch.Tests.ps1`:
  ```powershell
  BeforeAll {
      $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
      . (Join-Path $repoRoot 'scripts\lib\vibemind-helpers.ps1')
      $script:StartScript = Join-Path $repoRoot 'scripts\vibemind-start.ps1'
      $script:Base = Join-Path $HOME ('.vbu-tests\launch-' + [guid]::NewGuid().ToString('N'))
      New-Item -ItemType Directory -Path $script:Base -Force | Out-Null
  }
  AfterAll { Remove-Item -LiteralPath $script:Base -Recurse -Force -ErrorAction SilentlyContinue }

  Describe 'Get-VoiceElectronLaunch' {
      It 'nimmt vibemind-betrieb\voice, wenn dort electron-app\main.js liegt' {
          $betrieb = Join-Path $script:Base 'betrieb'
          New-Item -ItemType Directory -Path (Join-Path $betrieb 'voice\electron-app') -Force | Out-Null
          Set-Content -LiteralPath (Join-Path $betrieb 'voice\electron-app\main.js') -Value '//'
          $l = Get-VoiceElectronLaunch -Root 'C:\V1' -BetriebDir $betrieb -VoiceDirOverride ''
          $l.VoiceDir | Should -Be (Join-Path $betrieb 'voice')
          $l.ElectronDir | Should -Be (Join-Path $betrieb 'voice\electron-app')
      }
      It 'faellt auf den alten Pfad zurueck, wenn vibemind-betrieb fehlt' {
          $l = Get-VoiceElectronLaunch -Root 'C:\V1' -BetriebDir (Join-Path $script:Base 'gibtsnicht') -VoiceDirOverride ''
          $l.VoiceDir | Should -Be 'C:\V1\vibemind-os\voice'
      }
      It 'VIBEMIND_VOICE_DIR gewinnt' {
          (Get-VoiceElectronLaunch -Root 'C:\V1' -VoiceDirOverride 'D:\x\voice').VoiceDir | Should -Be 'D:\x\voice'
      }
      It 'setzt genau die Spec-Umgebung und kein DASHBOARD_DEV_URL' {
          $e = (Get-VoiceElectronLaunch -Root 'C:\V1' -BetriebDir (Join-Path $script:Base 'gibtsnicht') -VoiceDirOverride '').Env
          (@($e.Keys) -join ',') | Should -Be 'SKIP_BRAIN_SPAWN,BRAIN_PORT,VIBEMIND_ENABLE_CDP,VIBEMIND_ENV_FILE,VIBEMIND_STARTUP_ALLOW,MIROFISH_URL'
          $e['SKIP_BRAIN_SPAWN'] | Should -Be '1'
          $e['BRAIN_PORT'] | Should -Be '5000'
          $e['VIBEMIND_ENABLE_CDP'] | Should -Be '1'
          $e['VIBEMIND_ENV_FILE'] | Should -Be 'C:\V1\.env'
          $e['VIBEMIND_STARTUP_ALLOW'] | Should -Be ''
          $e['MIROFISH_URL'] | Should -Be 'http://100.67.177.45:3000'
      }
  }
  Describe 'ConvertTo-CmdSetPrefix' {
      It 'quotet Werte und leert leere Variablen' {
          ConvertTo-CmdSetPrefix -Variables ([ordered]@{ A = '1'; B = ''; C = 'C:\x y\.env' }) |
              Should -Be 'set "A=1"& set "B="& set "C=C:\x y\.env"&'
      }
  }
  Describe 'Get-MiroFishHealthUrl' {
      It 'zeigt auf die MiroFish-Oberflaeche der VM' { Get-MiroFishHealthUrl | Should -Be 'http://100.67.177.45:3000/' }
  }
  Describe 'vibemind-start.ps1' {
      It 'nutzt die Helfer, setzt kein DASHBOARD_DEV_URL und prueft MiroFish nicht mehr lokal' {
          $text = Get-Content -LiteralPath $script:StartScript -Raw
          $text | Should -Not -Match 'DASHBOARD_DEV_URL='
          $text | Should -Not -Match 'localhost:5101'
          $text | Should -Match 'Get-VoiceElectronLaunch'
          $text | Should -Match 'ConvertTo-CmdSetPrefix'
          $text | Should -Match 'Get-MiroFishHealthUrl'
          $text | Should -Match '--remote-debugging-port=9223'
      }
  }
  ```
  `Invoke-Pester -Path scripts\tests\voice-electron-launch.Tests.ps1 -Output Detailed` → rot (`Get-VoiceElectronLaunch` unbekannt; `DASHBOARD_DEV_URL=` noch im Skript).

- [ ] **Schritt 7.4 – Helfer in `scripts/lib/vibemind-helpers.ps1`** (am Dateiende anhaengen):
  ```powershell
  # --- voice-App (Spec 2026-10-15 voice-app-wiederbeleben, Abschnitt 2) -----------

  function Get-VoiceElectronLaunch {
      <#
      .SYNOPSIS
          voice-Verzeichnis und Umgebung fuer den Electron-Start (Launcher Phase 5).
          Vorgabe: vibemind-betrieb\voice, wenn vorhanden, sonst <Root>\vibemind-os\voice;
          VIBEMIND_VOICE_DIR gewinnt.
      #>
      param(
          [Parameter(Mandatory)][string]$Root,
          [string]$BetriebDir = 'C:\Users\User\ClaudeWork\vibemind-betrieb',
          [string]$VoiceDirOverride = $env:VIBEMIND_VOICE_DIR
      )
      $voiceDir = if ($VoiceDirOverride) {
          $VoiceDirOverride
      } elseif (Test-Path -LiteralPath (Join-Path $BetriebDir 'voice\electron-app\main.js')) {
          Join-Path $BetriebDir 'voice'
      } else {
          Join-Path $Root 'vibemind-os\voice'
      }
      $launchEnv = [ordered]@{
          SKIP_BRAIN_SPAWN       = '1'
          BRAIN_PORT             = '5000'
          VIBEMIND_ENABLE_CDP    = '1'
          VIBEMIND_ENV_FILE      = (Join-Path $Root '.env')
          VIBEMIND_STARTUP_ALLOW = ''
          MIROFISH_URL           = 'http://100.67.177.45:3000'
      }
      return [pscustomobject]@{
          VoiceDir    = $voiceDir
          ElectronDir = (Join-Path $voiceDir 'electron-app')
          Env         = $launchEnv
      }
  }

  function ConvertTo-CmdSetPrefix {
      <# cmd.exe-Praefix: set "K=V"& … ; leerer Wert entfernt die Variable im Kindprozess. #>
      param([Parameter(Mandatory)][System.Collections.IDictionary]$Variables)
      return (@($Variables.Keys | ForEach-Object { 'set "{0}={1}"&' -f $_, $Variables[$_] }) -join ' ')
  }

  function Get-MiroFishHealthUrl {
      <# MiroFish laeuft auf der Proxmox-VM (Tailnet), nicht mehr lokal auf :5101. #>
      return 'http://100.67.177.45:3000/'
  }
  ```

- [ ] **Schritt 7.5 – `scripts/vibemind-start.ps1`.**
  a) In `Invoke-Phase5-Electron` die Zeilen
  ```powershell
      $voiceDir    = Join-Path $script:ROOT 'vibemind-os\voice'
      $electronDir = Join-Path $voiceDir 'electron-app'
  ```
  ersetzen durch:
  ```powershell
      # voice aus dem Laufzeit-Checkout (scripts\voice_betrieb_update.ps1), sonst Haupt-Checkout
      $launch      = Get-VoiceElectronLaunch -Root $script:ROOT
      $voiceDir    = $launch.VoiceDir
      $electronDir = $launch.ElectronDir
  ```
  und die Warnung `Write-Warn "Electron not installed (run: cd vibemind-os/voice/electron-app; npm install) — skipping"` ersetzen durch:
  ```powershell
          Write-Warn "Electron not installed in $electronDir (run: pwsh -File scripts\voice_betrieb_update.ps1) — skipping"
  ```
  b) Den Kommentarblock ab `# CRITICAL: clear ELECTRON_RUN_AS_NODE` bis einschliesslich der Zeile `$cmdLine = "set SKIP_BRAIN_SPAWN=1& …"` ersetzen durch:
  ```powershell
      # CRITICAL: clear ELECTRON_RUN_AS_NODE (set by Claude Code IDE; turns
      # Electron into plain Node, which then rejects --remote-debugging-port
      # as "bad option"). cmd /c with explicit `set VAR=&` guarantees the
      # clear reaches the child env.
      # Umgebung aus Get-VoiceElectronLaunch (Spec 2026-10-15): SKIP_BRAIN_SPAWN=1,
      # BRAIN_PORT=5000, VIBEMIND_ENABLE_CDP=1 (CDP nur Loopback-Origins, s. main.js),
      # VIBEMIND_ENV_FILE=<ROOT>\.env, VIBEMIND_STARTUP_ALLOW leer (alle Nebenwirkungen aus),
      # MIROFISH_URL auf die VM. DASHBOARD_DEV_URL entfaellt: auf :5173 laeuft der
      # Rowboat-Vite-Server, das Dashboard zeigt ohne eigenes Ziel eine Statuskarte.
      $envPrefix = ConvertTo-CmdSetPrefix -Variables $launch.Env
      $cmdLine = "$envPrefix set ELECTRON_RUN_AS_NODE=& `"$electronExe`" --remote-debugging-port=9223 --enable-logging `"$electronDir`""
  ```
  c) In `Wait-StackHealthy` `$stackEndpoints += @{ Name = "mirofish";        Url = "http://localhost:5101/health" }` → `$stackEndpoints += @{ Name = "mirofish";        Url = (Get-MiroFishHealthUrl) }`.
  d) In `Show-Status` (a506-unabhaengig, Z. ~845) `if ($activeSpaces -contains "mirofish") { $eps += @{N="mirofish"; U="http://localhost:5101/health"} }` → `if ($activeSpaces -contains "mirofish") { $eps += @{N="mirofish"; U=(Get-MiroFishHealthUrl)} }`.

- [ ] **Schritt 7.6 – Gruen + Trockenlauf.**
  ```powershell
  cd C:\Users\User\Desktop\Vibemind_V1
  Import-Module Pester -MinimumVersion 5.0
  Invoke-Pester -Path scripts\tests\voice-electron-launch.Tests.ps1, scripts\tests\voice-betrieb-update.Tests.ps1, scripts\tests\vibemind-stack-profiles.Tests.ps1 -Output Detailed
  pwsh -NoProfile -File scripts\vibemind-start.ps1 -DryRun 2>&1 | Select-String "Would launch: cmd /c"
  ```
  Erwartet: Pester `Failed: 0`; die DryRun-Zeile enthaelt `set "VIBEMIND_STARTUP_ALLOW="&`, `set "VIBEMIND_ENV_FILE=C:\Users\User\Desktop\Vibemind_V1\.env"&`, `set "MIROFISH_URL=http://100.67.177.45:3000"&`, kein `DASHBOARD_DEV_URL`. (Startet der `-DryRun` andere Phasen real, ist das ein Befund – dann STOP und nur die Pester-Tests werten.)

- [ ] **Schritt 7.7 – Commit (aeusseres Repo).**
  ```powershell
  cd C:\Users\User\Desktop\Vibemind_V1
  git status --short
  git add scripts/voice_betrieb_update.ps1 scripts/tests/voice-betrieb-update.Tests.ps1 scripts/tests/voice-electron-launch.Tests.ps1 scripts/lib/vibemind-helpers.ps1 scripts/vibemind-start.ps1
  git diff --cached --name-only     # genau diese fuenf
  $env:ENTIRE_TEST_TTY='0'; git commit -m "feat(launcher): voice-App aus vibemind-betrieb starten, Update-Skript und MiroFish-Pruefung auf der VM"
  ```

**Interfaces:**
- `scripts/voice_betrieb_update.ps1 [-RuntimeDir] [-RepoUrl] [-Branch] [-Npm] [-PythonLauncher] [-SkipPython] [-SkipOverlayBuilds]` → Ausgabe `Alter Stand: …`, `Neuer Stand: …`, `Neu gebaut: …`; wirft bei eigenen Aenderungen.
- `Get-VoiceElectronLaunch -Root <string> [-BetriebDir] [-VoiceDirOverride] -> { VoiceDir, ElectronDir, Env (ordered) }`
- `ConvertTo-CmdSetPrefix -Variables <IDictionary> -> string`
- `Get-MiroFishHealthUrl -> 'http://100.67.177.45:3000/'`

---

## Task 8: Integration, Laufzeit-Checkout, Abnahme mit dem User

**Dateien:** keine neuen; Git-Operationen, `WORKBOARD.md`.

- [ ] **Schritt 8.1 – Review-Abschluss.** Alle Tests aus 6.6 und 7.6 erneut gruen; Code-Review-Befunde behoben (je eigener Commit).

- [ ] **Schritt 8.2 – voice-Branch pushen.**
  ```powershell
  gh auth status     # Memory: zwei Accounts (Vibemind-LAB/Flissel) – Push braucht Flissel
  git -C C:\Users\User\ClaudeWork\wt-voice-startprofil push -u origin feat/startprofil
  ```

- [ ] **Schritt 8.3 – HALT: User-OK fuer den Merge nach `codex/laura-video-interface-embed`.** Dem User zeigen: `git log --oneline a506ca2..feat/startprofil`, Testergebnisse, offene Punkte. Ohne ausdrueckliches OK nicht weiter.

- [ ] **Schritt 8.4 – Merge (nur nach OK).** Der Pin-Branch ist im Haupt-Checkout ausgecheckt → nicht dort mergen, sondern im Worktree detached:
  ```powershell
  cd C:\Users\User\ClaudeWork\wt-voice-startprofil
  git status --short                                  # leer
  git fetch origin
  git rev-parse origin/codex/laura-video-interface-embed   # muss a506ca2e… sein, sonst STOP + User
  git switch --detach origin/codex/laura-video-interface-embed
  $env:ENTIRE_TEST_TTY='0'; git merge --no-ff feat/startprofil -m "merge: Startprofil fuer die voice-App (Teil 1) nach codex/laura-video-interface-embed"
  cd electron-app; npm run test:unit; cd ..
  git push origin HEAD:codex/laura-video-interface-embed   # Fast-Forward, kein Force
  $neuerPin = git rev-parse HEAD
  git switch feat/startprofil
  ```

- [ ] **Schritt 8.5 – HALT: User-OK fuer das Pin-Update in vibemind-os `master`.**

- [ ] **Schritt 8.6 – Pin-Update (nur nach OK) im Worktree `setup-agent`.** Der Worktree hat fremde ungestagte Aenderungen (u. a. `openfang`, `spaces/marketing/...`) – nur den Gitlink stagen, nichts stashen (Memory: `--cacheinfo` prueft den SHA nicht; `git commit -- <pfad>` verwirft gestagte Gitlinks):
  ```powershell
  $wt = 'C:\Users\User\Desktop\Vibemind_V1\vibemind-os\.worktrees\setup-agent'
  git -C $wt rev-parse --show-toplevel           # .../.worktrees/setup-agent
  git -C $wt status --short --branch             # auf master; fremde Aenderungen notieren, NICHT anfassen
  git -C $wt fetch origin
  git -C $wt rev-list --count HEAD..origin/master   # muss 0 sein; sonst STOP + User (kein pull mit fremdem Dirty-State)
  git -C C:\Users\User\ClaudeWork\wt-voice-startprofil cat-file -t $neuerPin      # commit
  git -C C:\Users\User\ClaudeWork\wt-voice-startprofil ls-remote origin refs/heads/codex/laura-video-interface-embed   # == $neuerPin
  git -C $wt diff --cached --name-only           # muss leer sein, sonst STOP
  git -C $wt update-index --cacheinfo "160000,$neuerPin,voice"
  git -C $wt diff --cached --name-only           # genau: voice
  $env:ENTIRE_TEST_TTY='0'; git -C $wt commit -m "chore(voice): Pin auf Startprofil der voice-App (Teil 1)"
  git -C $wt ls-tree HEAD voice                  # 160000 commit <$neuerPin>	voice
  git -C $wt push origin HEAD:master
  ```

- [ ] **Schritt 8.7 – Laufzeit-Checkout anlegen.**
  ```powershell
  pwsh -NoProfile -File C:\Users\User\Desktop\Vibemind_V1\scripts\voice_betrieb_update.ps1
  git -C C:\Users\User\ClaudeWork\vibemind-betrieb\voice log -1 --format=%H   # == $neuerPin
  Test-Path C:\Users\User\ClaudeWork\vibemind-betrieb\spaces\rowboat\rowboat   # True (Teil 2)
  Test-Path C:\Users\User\ClaudeWork\vibemind-betrieb\voice\electron-app\node_modules\electron\dist\electron.exe   # True
  ```
  Ausgabe (`Alter Stand`/`Neuer Stand`/`Neu gebaut`) im Bericht festhalten. Schlaegt ein Overlay-Build fehl: kein Abbruch – das Overlay zeigt die Statuskarte; in der Abnahme so erwartet.

- [ ] **Schritt 8.8 – Speicher messen (Spec-Risiko).**
  ```powershell
  $os = Get-CimInstance Win32_OperatingSystem
  '{0:N1} GB RAM frei von {1:N1} GB' -f ($os.FreePhysicalMemory/1MB), ($os.TotalVisibleMemorySize/1MB)
  '{0:N1} GB Commit frei' -f ($os.FreeVirtualMemory/1MB)
  ```
  Weniger als 4 GB RAM frei oder weniger als 4 GB Commit frei → HALT, User fragen (Memory: Host am Commit-Limit → OOM/Orphans).

- [ ] **Schritt 8.9 – HALT: Abnahme gemeinsam mit dem User (Spec „Abnahme“, 8 Schritte).** Vorher (Gegenprobe) festhalten:
  ```powershell
  $proben = "$HOME\.vbu-tests\abnahme"; New-Item -ItemType Directory $proben -Force | Out-Null
  docker ps -a --format "{{.Names}} {{.Status}}" | Sort-Object | Set-Content "$proben\docker-vorher.txt"
  (Get-NetTCPConnection -LocalPort 4200 -State Listen | Select-Object -First 1).OwningProcess | Set-Content "$proben\openfang-pid-vorher.txt"
  ```
  1. User startet den Launcher wie gewohnt; Log `C:\Users\User\Desktop\Vibemind_V1\logs\voice\electron.log` zeigt `Launching Electron (cwd=C:\Users\User\ClaudeWork\vibemind-betrieb\voice)`; Fenster ohne Absturz.
  2. `Select-String logs\voice\electron.log -Pattern '\[Startprofil\]'` → `[Startprofil] erlaubt=[] uebersprungen=[docker-cleanup,media-compose,n8n-local,mirofish-local,openfang-spawn,brain-spawn,rowboat-autoclean,bubble-sync,zombie-kill]`.
  3. Gegenprobe: `docker ps -a …` erneut → identisch zu `docker-vorher.txt` (`Compare-Object`); OpenFang-PID unveraendert; `Select-String logs\voice\electron.log -Pattern 'owner/setup|owner account provisioned'` → keine Treffer.
  4. User klickt in der Multiverse-3D-Ansicht jeden Space an.
  5. Overlays: Brain, MiroFish (VM), AgentFarm, Flowzen, Video/Laura, SWE Design laden; Rowboat („kommt mit Teil 2“), eyeTerm („entfernt“) und Dashboard ohne Build zeigen die Statuskarte; ClawPort gebaut oder Statuskarte. (Video/Laura: siehe offene Frage unten.)
  6. `Select-String logs\voice\electron.log, $env:TEMP\vibemind-renderer-console.log -Pattern 'python_ready'` → Treffer; `Python-Schalter aus Startprofil: VIBEMIND_ROWBOAT_AUTOCLEAN=0 VIBEMIND_BUBBLE_SYNC_ENABLED=0 VIBEMIND_CANVAS_REFORMAT_ENABLED=0`; kein `rowboat-cleanup deleted`, `bubble_sync workers OFF`.
  7. `Invoke-RestMethod http://localhost:9223/json/version` antwortet.
  8. User beendet die App; danach Schritt-3-Gegenprobe wiederholen (Container und OpenFang-PID unveraendert).

- [ ] **Schritt 8.10 – Abschluss.** WORKBOARD-Zeile `cc-voice-app-teil1` nach *Erledigt* bzw. auf „AUSGELIEFERT … Claim geschlossen“ setzen (nur eigene Zeile stagen, wie 0.2), Commit `docs: workboard cc-voice-app-teil1 ausgeliefert`. Worktree `wt-voice-startprofil` und Branch `feat/startprofil` bleiben bis zum User-OK bestehen (keine Loeschung ohne User). `/distill` anbieten.

---

## Selbstpruefung

### Spec-Abdeckung

| Spec-Abschnitt | Anforderung | Task |
|---|---|---|
| 1 Startprofil | `VIBEMIND_STARTUP_ALLOW`, Vorgabe aus, unbekannt → Warnung, isolated bleibt | T1 |
| 1 Tabelle | `docker-cleanup` (Start + `claude-runner` beim Beenden) | T3 |
| 1 Tabelle | `media-compose`, `n8n-local` (+ keine Anmeldung), `mirofish-local`, `zombie-kill` | T3 |
| 1 Tabelle | `openfang-spawn`, `brain-spawn` (nur uebernehmen, Statuskarte/Status, keine Schleife) | T5 |
| 1 Tabelle | `rowboat-autoclean`, `bubble-sync` → Python-Schalter, Profil gewinnt gegen `.env` | T2 |
| 1 | `runExternalStartup` faengt Fehler je Block ab | T1 (+ Bloecke einzeln in T1/T3) |
| 1 | Log-Zeile `[Startprofil] erlaubt=[…] uebersprungen=[…]` | T1 |
| 1 | `electron_backend.py:140-141` → `ROARBOOT` | T2 |
| 2 | Laufzeit-Checkout `vibemind-betrieb`, nur `voice` initialisiert | T7 (Skript), T8.7 |
| 2 | `feat/startprofil` von `a506ca2`, Merge in Pin-Branch, Pin-Update `master` | T0.3, T8.4, T8.6 |
| 2 | Update-Skript Punkte 1–6 | T7.2 |
| 2 | `VIBEMIND_ENV_FILE` | T6 |
| 2 | Launcher Phase 5 (`VIBEMIND_VOICE_DIR`, Env, kein `DASHBOARD_DEV_URL`, CDP bleibt) | T7.4/7.5 |
| 2 | Phase 6: MiroFish auf der VM | T7.5c/d |
| 3 | Gemeinsame Pruefung (Datei/URL 2 s), Statuskarte mit Ziel | T4.2 |
| 3 Tabelle | Brain | T5 · MiroFish T4.4 · AgentFarm/Flowzen T4.10 · Video/Laura T4.9 (+ T1.4c) · SWE Design bewusst unveraendert (Tabelle) · ClawPort T4.5 (+ Build T7) · Dashboard T4.6 · eyeTerm T4.8 · Rowboat T4.7 + T3.6d |
| 4 Tests | startup-policy (4 Punkte) | T1.1 |
| 4 Tests | Manager mit Attrappen (Docker, OpenFang, Brain, n8n/MiroFish) | T3.1/3.2, T5.1 |
| 4 Tests | Statuskarte: fehlende Datei, nicht erreichbare URL, fremde App | T4.1/4.3 |
| 4 Tests | Python: Import (`ROARBOOT`), Autoclean/Bubble-Sync aus | T2.1 |
| 4 Tests | Update-Skript: Abbruch bei eigenen Aenderungen, `npm ci` nur bei Lockfile-Aenderung | T7.1 |
| Abnahme 1–8 | mit dem User | T8.9 |
| Risiken | Speicher messen | T8.8 |

### Platzhalter-Scan
Durchgesehen: kein „TBD“, kein „aehnlich wie“, kein „Fehlerbehandlung ergaenzen“; jeder Code-Schritt enthaelt den Code. Zeilenangaben beziehen sich auf `a506ca2`; nach T1 verschieben sich die `main.js`-Zeilen – deshalb sind alle `main.js`-Ersetzungen zusaetzlich ueber den Code-Anker (Blockname/erste Zeile) benannt.

### Signatur-Konsistenz
- `createStartupPolicy(env, { logger })` – T1 definiert; T2 (`pythonEnv`), T3 (`isAllowed`, `logSkipped`), T5 (`isAllowed`, `logSkipped`), T6 (Env-Bereinigung vor der Erzeugung in `main.js`) nutzen genau diese Namen.
- `logSkipped(name, text)` erzeugt `[Startprofil] <name> uebersprungen – haette: <text>`; Tests in T1/T3/T5 pruefen exakt dieses Format.
- `loadOverlay(webContents, { space, targets }, deps)` – T4 definiert, T5 (`_showStatusCard`) nutzt dieselbe Form; `CARD_PREFIX` aus T4 in T5-Tests.
- Manager-Konstruktoren: `(mainWindow, dependencies = {})` (Brain, MiroFish, ClawPort, Dashboard, Rowboat, eyeTerm, Video); `OpenFangManager(dependencies = {})`; `PortAllocator(options = {})`, `DockerManager(options = {})` – `main.js`-Aufrufe in T3/T4/T5 passen dazu.
- Launcher: `Get-VoiceElectronLaunch` liefert `.Env` (ordered), das `ConvertTo-CmdSetPrefix -Variables` direkt nimmt.

### Abweichungen/Ergaenzungen gegenueber der Spec (bewusst)
- n8n/MiroFish lokal: zusaetzlich zum Profil bleibt die vorhandene Bedingung `N8N_ENABLED`/`MIROFISH_ENABLED === 'true'` (`main.js:3361`, `:3427`).
- `VIBEMIND_STARTUP_ALLOW` wird nur aus der Prozessumgebung gelesen (eine `.env` koennte sonst Nebenwirkungen einschalten, `main.js:35-42` laedt die `.env` vor dem Profil).
- Rowboat: neben der Bridge entfaellt auch `autoConfigureRowboatModels()` (`main.js:3458`, schreibt `~/.rowboat/config/models.json`).
- OpenFang hat kein eigenes Overlay: „Statuskarte“ = Status `fehlt` + `python-message` `openfang_status` an den Renderer + Log.
- Update-Skript baut zusaetzlich AgentFarm (`electron-app/agentfarm/dist` ist per `voice/.gitignore:16` ignoriert; ohne Build kein AgentFarm-Overlay in `vibemind-betrieb`, Abnahme 5 verlangt es).
- `docker-cleanup` aus: beim Beenden werden auch selbst gestartete `project-*`-Container nicht entfernt (Spec: „nichts loeschen“).

### Offene Fragen an den User
1. **Video/Laura im Laufzeit-Checkout:** `spaces/video/laura` ist ein eigenes Submodul (`vibemind-os/.gitmodules`), die Spec initialisiert nur `voice` → in `vibemind-betrieb` fehlt `spaces/video/laura/apps/desktop/dist`; das Overlay zeigt dann die Statuskarte statt „laedt“ (Abnahme 5). Soll das Update-Skript `spaces/video/laura` zusaetzlich initialisieren und `pnpm … build:embed` ausfuehren?
2. **Selbst gestartete Coding-Engine-Container beim Beenden:** bei `docker-cleanup` aus bleiben auch `project-*`-Container dieser Sitzung stehen – so gewollt?
3. **Pin-Update auf `master`:** der Worktree `setup-agent` traegt fremde ungestagte Aenderungen; liegt `origin/master` vor `HEAD`, stoppt T8.6 (kein Pull ueber fremden Dirty-State) – wer zieht den Worktree dann nach?
