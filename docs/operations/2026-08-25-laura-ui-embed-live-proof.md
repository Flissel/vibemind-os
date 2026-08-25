# Laura-Oberfläche im VibeMind Video Space — Live-Beleg mit Nicht-Claims

**Zeitpunkt:** `2026-08-25T12:28:09.987+02:00`

Der reale Electron-Pfad wurde automatisiert geprüft. Das Gate belegt die eingebettete
Laura-Oberfläche, die authentifizierte Local-API-Verbindung, den Space-Wechsel und die
fail-closed Gegenprobe. Es ist kein vollständiger Medien-Workflow-Beleg: Der vorgesehene
Workspace war auf diesem Host nicht vorhanden und die externe Laura-`.env` enthielt keinen
nichtleeren `LAURA_TOKEN`. Deshalb wurden keine bestehenden Projektdaten gefunden oder
verändert und keine Ersatzdaten erzeugt.

## Versionen

| Komponente | Commit |
|---|---|
| `vibemind-os` vor diesem Follow-up-Commit | `555c45aa295b47aa592c61450b3e685d44f74bd5` |
| `spaces/video/laura` | `e5e005cbc025363cd617ae1b5cf6ac9684e8ad03` |
| `voice` | `9478cb9375dd63b5c5730eac7a16810a2d940e2d` |

`npm --prefix voice/electron-app run video:build` baute den realen Laura-Renderer frisch
mit Vite (`152 modules transformed`, Exitcode 0).

## Positives Gate

Für den Test lief genau ein eigener Laura-Local-API-Prozess auf Loopback mit ephemerem,
nicht protokolliertem Token und isoliertem temporärem Workspace. Der Prozess antwortete mit
Schema-Version 36; `GET /projects` lieferte authentifiziert HTTP 200 und ohne Token HTTP 401.
Die authentifizierte Projektliste war leer.

Der API-Start und die HTTP-Proben wurden in einer PowerShell-Session mit folgenden
redigierten Befehlen ausgeführt; `<TEMP_WORKSPACE>` war ein pro Lauf neu angelegtes und beim
Cleanup entferntes Verzeichnis:

```powershell
$env:LAURA_TOKEN = [Convert]::ToBase64String(<32 zufällige Bytes>)
$env:LAURA_WORKSPACE = '<TEMP_WORKSPACE>'
$api = Start-Process -FilePath uv -ArgumentList @(
  'run', '--directory', 'services/local-api', 'laura-api'
) -WorkingDirectory 'spaces/video/laura' -WindowStyle Hidden -PassThru
Invoke-WebRequest http://127.0.0.1:8765/healthz
Invoke-WebRequest http://127.0.0.1:8765/projects `
  -Headers @{ 'X-Laura-Token' = $env:LAURA_TOKEN }
Invoke-WebRequest http://127.0.0.1:8765/projects
```

Ergebnisfelder des erfolgreichen Laufs: `HealthStatus=200`,
`AuthorizedProjectsStatus=200`, `AuthorizedProjectCount=0`,
`AuthorizedResponseIsArray=true`, `UnauthorizedProjectsStatus=401` und
`WorkspaceKind=isolated-temporary`. Der Tokenwert und der konkrete temporäre Pfad wurden
nicht ausgegeben.

Im echten VibeMind-Electron-Fenster wurde der Video Space über `window.vibemind.showVideo()`
geöffnet. Beobachtet wurden:

- Laura-Header und alle sieben NavRail-Einträge;
- die sichtbare Chat-Eingabe;
- die Projektwahl als vorhandenes, wegen null Projekten deaktiviertes Steuerelement;
- der geöffnete JobCenter mit der Überschrift `Job-Zentrale`;
- keine alte `window.vibemindVideo`-Bridge und keine Meldung `Service offline`;
- authentifizierter API-Zugriff aus dem Laura-Renderer mit HTTP 200;
- nach Wechsel aus dem Video Space und zurück derselbe BrowserView/WebContents, dieselbe
  Renderer-Zeitbasis und weiterhin der zuvor gewählte NavRail-Stand `Media`.

Der Dateidialog-IPC wurde mit einer instrumentierten `canceled: true`-Antwort durchlaufen;
`window.laura.pickMediaFiles()` gab die erwartete leere Liste zurück. Das belegt Bridge und
Abbruchsemantik, aber nicht das sichtbare Öffnen des nativen Windows-Dialogs.

Der begrenzte Electron/Playwright-Probelauf wurde aus `voice/electron-app` mit
`node <temporärer-laura-ui-probe>.js` gestartet. Der Probe-Runner setzte `LAURA_URL` auf
Loopback, übergab denselben ephemeren Token nur über die Prozessumgebung und gab ausschließlich
folgende secret-freien Ergebnisfelder aus:

```text
header=Laura; navRailEntries=7; chatInput=true; projectSelector=true;
projectSelectorDisabled=true; jobCenter=Job-Zentrale; legacyBridge=false;
rendererApiStatus=200; dialogCanceled=true; pickedFileCount=0;
browserViewReused=true; rendererTimeOriginPreserved=true; navStateAfterReturn=Media
```

Die BrowserView-/State-Gegenprobe bestand aus `window.vibemind.showVideo()`, Wechsel in
einen anderen Space und erneutem `window.vibemind.showVideo()`; verglichen wurden die
WebContents-ID, `performance.timeOrigin` und der aktive NavRail-Eintrag. Für den Dialog
wurde ausschließlich die Electron-`dialog.showOpenDialog`-Antwort
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

Die reproduzierbare, im Repository liegende Negativprobe lautet:

```powershell
npm --prefix voice/electron-app run test:e2e -- --grep "video space embeds"
```

Die Fixture setzt `LAURA_TOKEN=''` und `LAURA_URL='http://127.0.0.1:0'`. Die Assertion-Felder
waren `attached=true`, `title='Laura'`, `titleVisible=true`,
`hasLauraGetServiceInfo=true`, `serviceInfoUnavailable=true` und
`hasLegacyVideoApi=false`. Der Lauf endete nach dem Lifecycle-Fix regulär mit Exitcode 0 und
`1 passed (12.0s)`.

## Nicht-Claims

- Sora ist durch dieses Gate nicht in Laura integriert.
- Capture und FaceSwap sind durch dieses Gate nicht in Laura integriert.
- Die alte Video-UI ist noch nicht entfernt.

Zusätzlich ist dieses Dokument kein Beleg für eine erfolgreiche Projektwahl, eine gerenderte
Timeline, Proxy-Playback/Seek, einen nativen Dateidialog oder HTTP 200/206 über
`laura-media://`.

## Verifikation und Blocker

- PASS: `npm --prefix voice/electron-app run video:build` (Vite-Build, 152 Module).
- PASS: `npm --prefix voice/electron-app run test:unit` (47/47 Tests).
- PASS: `pnpm --dir spaces/video/laura/apps/desktop typecheck` (Exitcode 0).
- PASS: `npm --prefix voice/electron-app run test:e2e -- --grep "video space embeds"`
  (Exitcode 0, `1 passed (12.0s)`). Beim früheren Lauf hatten die Assertions bereits
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
