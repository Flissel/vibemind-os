# Laura-Oberfläche im VibeMind Video Space — Live-Beleg mit Nicht-Claims

**Zeitpunkt:** `2026-08-25T13:10:25.393+02:00`

Der reale Electron-Pfad wurde automatisiert geprüft. Das Gate belegt die eingebettete
Laura-Oberfläche, die authentifizierte Local-API-Verbindung, ein Detach/Reattach derselben
BrowserView und die fail-closed Gegenprobe. Es ist kein vollständiger Medien-Workflow-Beleg:
Der vorgesehene
Workspace war auf diesem Host nicht vorhanden und die externe Laura-`.env` enthielt keinen
nichtleeren `LAURA_TOKEN`. Deshalb wurden keine bestehenden Projektdaten gefunden oder
verändert und keine Ersatzdaten erzeugt.

## Versionen

| Komponente | Commit |
|---|---|
| `vibemind-os` vor diesem Follow-up-Commit | `caa4ab57df63c09cdbabf84046133b5fed5594b0` |
| `spaces/video/laura` | `e5e005cbc025363cd617ae1b5cf6ac9684e8ad03` |
| `voice` | `3224436830c0765299fb33f884508f5d18567ee9` |
| `voice` beim vollständigen API/UI-Live-Runner | `7c6096d766ad17119550eca3446b70044e11a62b` |

`npm --prefix voice/electron-app run video:build` baute den realen Laura-Renderer frisch
mit Vite (`152 modules transformed`, Exitcode 0).

## Positives Gate

Für den Test lief genau ein eigener Laura-Local-API-Prozess auf Loopback mit ephemerem,
nicht protokolliertem Token und isoliertem temporärem Workspace. Der Prozess antwortete mit
Schema-Version 36; `GET /projects` lieferte authentifiziert HTTP 200 und ohne Token HTTP 401.
Die authentifizierte Projektliste war leer.

Der eingecheckte Runner erzeugt den ephemeren Token mit `randomBytes(32)`, legt ausschließlich
einen isolierten System-Temp-Workspace an, startet den deklarierten API-Befehl, wartet begrenzt
auf Readiness und verwaltet API und Electron über die jeweils selbst gestartete Root-PID. Der
vollständig ausführbare Repository-Befehl lautet:

```powershell
npm --prefix voice/electron-app run video:build
npm --prefix voice/electron-app run laura:live-proof
```

Der Runner ruft intern exakt `uv run --directory services/local-api laura-api` auf. Die
API-Ergebnisfelder des erfolgreichen Laufs waren `apiHealthStatus=200`,
`apiAuthorizedProjectsStatus=200`, `apiAuthorizedProjectCount=0` und
`apiUnauthorizedProjectsStatus=401`. Tokenwert und konkrete Workspace-Pfade gehören nicht
zur Ausgabe-Allowlist.

Im echten VibeMind-Electron-Fenster wurde der Video Space über `window.vibemind.showVideo()`
geöffnet. Beobachtet wurden:

- Laura-Header und alle sieben NavRail-Einträge;
- die sichtbare Chat-Eingabe;
- die Projektwahl als vorhandenes, wegen null Projekten deaktiviertes Steuerelement;
- der geöffnete JobCenter mit der Überschrift `Job-Zentrale`;
- keine alte `window.vibemindVideo`-Bridge und keine Meldung `Service offline`;
- authentifizierter API-Zugriff aus dem Laura-Renderer mit HTTP 200;
- nach `window.vibemind.hideVideo()` und anschließendem `showVideo()` derselbe
  BrowserView/WebContents, dieselbe Renderer-Zeitbasis und weiterhin der zuvor gewählte
  NavRail-Stand `Media`.

Der Dateidialog-IPC wurde mit einer instrumentierten `canceled: true`-Antwort durchlaufen;
`window.laura.pickMediaFiles()` gab die erwartete leere Liste zurück. Das belegt Bridge und
Abbruchsemantik, aber nicht das sichtbare Öffnen des nativen Windows-Dialogs.

Der begrenzte Electron/Playwright-Probelauf ist als
`voice/electron-app/scripts/laura-ui-live-proof.js` eingecheckt. Er setzt `LAURA_URL` auf
Loopback, übergibt denselben ephemeren Token nur über die Prozessumgebung und gab im frischen
Lauf ausschließlich folgende secret-freien positiven Ergebnisfelder aus:

```text
header=Laura; navRailEntries=7; chatInput=true; projectSelector=true;
projectSelectorDisabled=true; projectOptionCount=1; jobCenter=Job-Zentrale;
legacyBridge=false; rendererApiStatus=200; dialogCanceled=true;
dialogCallCount=1; pickedFileCount=0;
browserViewReused=true; rendererTimeOriginPreserved=true; navStateAfterReturn=Media
```

Die BrowserView-/State-Gegenprobe bestand ausschließlich aus
`window.vibemind.showVideo()`, `window.vibemind.hideVideo()` und erneutem `showVideo()`;
verglichen wurden die WebContents-ID, `performance.timeOrigin` und der aktive
NavRail-Eintrag. Sie belegt Detach/Reattach, keinen Wechsel in einen anderen VibeMind Space.
Für den Dialog wurde ausschließlich die Electron-`dialog.showOpenDialog`-Antwort
`{ canceled: true, filePaths: [] }` instrumentiert.

Nicht belegt wurden Projektwahl, Timeline, Proxy-Playback/Seek und ein
`laura-media://`-Range-Request mit HTTP 200/206. Es gab im geplanten Workspace kein
bestehendes Projekt oder Proxy-Medium; ersatzweise Projektdaten zu erzeugen war für dieses
Gate ausdrücklich ausgeschlossen.

## Negative Gegenprobe

Ein zweiter echter VibeMind-Electron-Start lief mit leerem `LAURA_TOKEN`, während die eigene
Local API weiterhin auf derselben Loopback-URL erreichbar war. Beobachtet wurden:

- der Laura-Renderer und Header luden;
- `window.laura.getServiceInfo()` lieferte `null`;
- die Oberfläche zeigte `Service offline`;
- es erschienen weder Projektwahl/Projektoptionen noch Medien-Bin oder Assets;
- die alte `window.vibemindVideo`-Bridge blieb abwesend.

Damit schlug die UI trotz erreichbarer API ohne Token fail-closed um; ein stiller Token- oder
Backend-Fallback wurde nicht beobachtet.

Die Negativprobe ist Teil desselben Repository-Befehls; die Local API bleibt dabei auf der
gleichen Loopback-URL erreichbar, während der zweite Electron-Start explizit
`LAURA_TOKEN=''` erhält. Die Ergebnisfelder des frischen Laufs waren:

```text
header=Laura; serviceInfoUnavailable=true; serviceOffline=true;
projectControlsAbsent=true; mediaAssetsAbsent=true; legacyBridge=false
```

Der Gesamt-Runner endete regulär mit Exitcode 0 und `port8765Free=true`. Die zusätzliche
eingecheckte Dead-Port-Gegenprobe
`npm --prefix voice/electron-app run test:e2e -- --grep "video space embeds"` blieb ebenfalls
grün (`1 passed (12.0s)`).

## FAST_STARTUP-Gate

Der aktuelle Voice-Commit definiert `FAST_STARTUP=true` als explizite Startup-Policy. Sie
erhält Hauptfenster, Laura-Host, Laura-Protokoll und VideoManager, überspringt aber die drei
gekapselten externen Bootphasen. Damit liefen in diesem Modus weder stale-container/media
Docker noch Brain-Spawn, OpenFang, Supabase Realtime, Brain-Bridge, n8n/MiroFish Docker,
Rowboat-Bridge oder das Python-Backend an. Außerhalb des exakt gesetzten Werts `true` führt
dieselbe Policy die bisherigen Callbacks weiterhin in Reihenfolge aus und wartet auf sie.

Fixture und Live-Runner setzen zusätzlich `N8N_ENABLED=false`, `MIROFISH_ENABLED=false` und
`SKIP_BRAIN_SPAWN=true` explizit, damit geerbte `.env`-Werte nicht als stiller Fallback dienen.
Der instrumentierte E2E erfasste Main-stdout/stderr begrenzt im Speicher. Der kontrollierte
Lauf ohne Retry

```powershell
npm --prefix voice/electron-app run test:e2e -- --grep "video space embeds" --retries=0
```

endete mit Exitcode 0 und `1 passed (28.6s)`. Die Logs enthielten die Marker
`FAST_STARTUP active — external startup side effects disabled`,
`Laura host and VideoManager initialized`, `Loading Laura renderer`, `Laura renderer loaded`
und `Video shown`. Sie enthielten keinen der geprüften Startmarker für Python, Brain,
OpenFang, Supabase, Brain-Bridge, n8n, MiroFish oder Rowboat. Der Prozess beendete sich ohne
firstWindow- oder Teardown-Timeout.

Die Beweisquellen sind getrennt: Der Unit-Test injiziert Callbacks in die Policy und belegt,
dass FAST keinen davon ausführt, während Normalmodus sie geordnet ausführt. Ein separater
Audit-Counter markiert nur den tatsächlichen Eintritt in die Normalmodus-Dockerphase und
emittiert nach Laura-Initialisierung den secret-freien Marker
`NORMAL_STARTUP Docker bootstrap entered: stale-container cleanup and media Docker`. Der
echte FAST-E2E belegt dessen Abwesenheit im sicher erfassten späten Logfenster. Vor sämtlichen
Forbidden-Vergleichen entfernt die gemeinsame Prüfung ANSI/VT-Sequenzen; der Regressionstest
erkennt ausdrücklich auch `[OpenFang]\x1b[0m Starting daemon` als verbotenen Startmarker.

## Nicht-Claims

- Sora ist durch dieses Gate nicht in Laura integriert.
- Capture und FaceSwap sind durch dieses Gate nicht in Laura integriert.
- Die alte Video-UI ist noch nicht entfernt.

Zusätzlich ist dieses Dokument kein Beleg für eine erfolgreiche Projektwahl, eine gerenderte
Timeline, Proxy-Playback/Seek, einen nativen Dateidialog oder HTTP 200/206 über
`laura-media://`. Ein echter Wechsel vom Video Space in einen anderen VibeMind Space und
zurück ist ebenfalls nicht belegt.

## Verifikation und Blocker

- PASS: `npm --prefix voice/electron-app run video:build` (Vite-Build, 152 Module).
- PASS am Voice-Commit `7c6096d`: `npm --prefix voice/electron-app run laura:live-proof`
  (Exitcode 0; API,
  positive UI/Detach/Reattach/Dialog-Probe, negativer Umschlag und Cleanup in einem
  begrenzten Lauf). Der Runner verwendet für diese State-Probe direkt `hideVideo()` und
  `showVideo()`; daraus wird kein echter Space-Wechsel abgeleitet.
- PASS am aktuellen Voice-Commit: `npm --prefix voice/electron-app run test:unit`
  (55/55 Tests).
- PASS: `pnpm --dir spaces/video/laura/apps/desktop typecheck` (Exitcode 0).
- PASS am aktuellen Voice-Commit:
  `npm --prefix voice/electron-app run test:e2e -- --grep "video space embeds" --retries=0`
  (Exitcode 0, `1 passed (28.6s)`). Beim früheren Teardown-Befund hatten die Assertions bereits
  bestanden; der gemeldete Testfehler war ausschließlich der Teardown-Timeout. Die
  Lifecycle-Instrumentierung lokalisierte ihn auf `VideoManager.destroy()`:
  `BrowserView.webContents.close()` blockierte während `will-quit`. Der eng begrenzte Fix
  verwendet `webContents.destroy()`; ein Regressionstest fordert `destroy()` und verbietet
  `close()`.
- PASS: `uv run --directory services/local-api pytest tests/test_runtime_dependencies.py`
  (`1 passed`). RED davor: Der Basissatz deklarierten Dependencies scheiterte beim Import
  mit `ModuleNotFoundError: No module named 'numpy'`. Nach Deklaration und Lockfile-Update
  startete `uv run --directory services/local-api laura-api` frisch und bestand die oben
  dokumentierten HTTP-200/200/401-Proben.

## Prozess-Eigentum und Aufräumen

Der fremde Laura-MCP-Prozessbaum mit PID 40336 blieb unangetastet und lief nach dem Gate
weiter. Nach dem Lifecycle-Fix schloss die gezielte Electron-E2E-Instanz regulär über
`app.close()`; danach waren keine dem Lauf zugehörigen Electron- oder Python-Prozesse übrig.
Der eigene Local-API-Prozessbaum wurde über seinen vor dem Start erfassten Root-PID gestoppt,
der temporäre Workspace entfernt, und Port 8765 war danach wieder frei. Es wurden keine
Projekt- oder Mediendaten erzeugt.
