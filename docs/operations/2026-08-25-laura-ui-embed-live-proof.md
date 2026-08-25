# Laura-Oberfläche im VibeMind Video Space — Teilbeleg

**Zeitpunkt:** `2026-08-25T12:10:55.566+02:00`

Der reale Electron-Pfad wurde automatisiert geprüft. Das Gate belegt die eingebettete
Laura-Oberfläche, die authentifizierte Local-API-Verbindung, den Space-Wechsel und die
fail-closed Gegenprobe. Es ist kein vollständiger Medien-Workflow-Beleg: Der vorgesehene
Workspace war auf diesem Host nicht vorhanden und die externe Laura-`.env` enthielt keinen
nichtleeren `LAURA_TOKEN`. Deshalb wurden keine bestehenden Projektdaten gefunden oder
verändert und keine Ersatzdaten erzeugt.

## Versionen

| Komponente | Commit |
|---|---|
| `vibemind-os` vor diesem Evidenz-Commit | `e4c711ea4047630d7bcd305bdd7652ebd6a19f30` |
| `spaces/video/laura` | `0d9217b9049e99aa87747f232758608d9a7888d5` |
| `voice` | `0427b14851bcab7edb561e8c112b600c97435eb6` |

`npm --prefix voice/electron-app run video:build` baute den realen Laura-Renderer frisch
mit Vite (`152 modules transformed`, Exitcode 0).

## Positives Gate

Für den Test lief genau ein eigener Laura-Local-API-Prozess auf Loopback mit ephemerem,
nicht protokolliertem Token und isoliertem temporärem Workspace. Der Prozess antwortete mit
Schema-Version 36; `GET /projects` lieferte authentifiziert HTTP 200 und ohne Token HTTP 401.
Die authentifizierte Projektliste war leer.

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
- KEIN PASS: `npm --prefix voice/electron-app run test:e2e -- --grep "video space embeds"`
  erreichte nach etwa 1,1 Minuten den fehlgeschlagenen Teststatus und hing danach im
  Electron-Teardown. Der eindeutig diesem Lauf zugehörige Prozessbaum wurde beendet; es
  liegt für diesen frischen Lauf kein grünes Playwright-Ergebnis vor.
- Der geplante `uv run --directory ... laura-api`-Start war mit den deklarierten
  Basisdependencies nicht möglich: `laura.analysis.quality` importiert `numpy`, das dort
  nicht installiert wurde. Für den UI-Teilbeleg wurde deshalb der bereits vorhandene
  externe Laura-Interpreter nur als Dependency-Umgebung verwendet; `PYTHONPATH` zeigte
  nachweislich auf den Local-API-Source des oben genannten Submodul-Commits. Das ist ein
  Dependency-Blocker und kein Beleg, dass der Basis-Startbefehl funktioniert.

## Prozess-Eigentum und Aufräumen

Der fremde Laura-MCP-Prozessbaum mit PID 40336 blieb unangetastet und lief nach dem Gate
weiter. Die beiden eigenen Electron-Instanzen reagierten nicht innerhalb von fünf Sekunden
auf `app.close()`; nur deren eindeutig erfasste Prozesse und zwei verwaiste eigene
Electron-Kinder wurden beendet. Der eigene Local-API-Prozessbaum wurde gestoppt, der
temporäre Workspace entfernt, und Port 8765 war danach wieder frei. Es wurden keine
Projekt- oder Mediendaten erzeugt.
