# Teilprojekt 2: Rowboat in der Multiverse-Ansicht einbetten (Design)

Stand: 2026-10-17. Setzt Teilprojekt 1 voraus (voice-App laeuft aus `C:\Users\User\ClaudeWork\vibemind-betrieb`, Spec `2026-10-15-voice-app-wiederbeleben-design.md`). Dort zeigt der Rowboat-Space (Roarboot) die Statuskarte "kommt mit Teil 2".

Repos:
- **Fork:** `Flissel/rowboat`, Branch `vibemind`. Die Rowboat-Aenderungen entstehen hier und kommen per `spaces/rowboat/scripts/sync_desktop.py` in die gepinnte Kopie `vibemind-os/spaces/rowboat/rowboat/apps/x`. Die Kopie wird nie von Hand geaendert.
- **voice:** `Flissel/VibeMind-VoiceDialog`, Pin-Branch `codex/laura-video-interface-embed`.
- **Aeusseres Repo:** Launcher und Update-Skript.

## Ziel (User)

Rowboat erscheint **echt eingebettet** im Multiverse-Fenster, wenn der Rowboat-Space geoeffnet wird, wie die anderen Overlays. Es soll die volle, aktuelle Rowboat-Oberflaeche sein, ohne nachgebaute Logik in der voice-App. Ein Rowboat-Update soll in der Multiverse-Ansicht ohne Nacharbeit ankommen.

Gewaehlter Weg: Rowboats eigener Hauptprozess beantwortet alle Kanaele, die voice-App reicht nur durch. Verworfen wurden:
- Rowboats Fenster per `SetParent` einhaengen (Fokus, DPI, Tastatur unter Windows wackelig);
- Rowboats Hauptlogik in die voice-App laden (Kollisionen bei Protokollen, Menues, Single-Instance).

## Ist-Zustand (gemessen 17.10., rein lesend)

**Rowboat (`apps/x`):**
- **IPC-Vertrag:** `packages/shared/src/ipc.ts` definiert 485 Kanaele; 54 davon sind Push-Kanaele (`res: z.null()`).
- **Eine Stelle fuer alle Handler:** `registerIpcHandlers()` (`apps/main/src/ipc.ts:394-415`, ein `ipcMain.handle` bei :404). Hinzu kommen `integrationsHandlers`, `browserIpcHandlers` und `spacesIpcHandlers`.
- **Vorhandener lokaler Server:** `rowboat-server` auf `127.0.0.1:3220` mit Bearer-Token (`~/.rowboat/server-key`). Er bedient nur 300 Kanaele. Sein WS-Hub akzeptiert das Token auch in der URL (`?token=`).
- **Push:** `broadcastToWindows` (`ipc.ts:589`) sendet an alle `BrowserWindow`. Etwa 30 Push-Kanaele werden nur aus dem Hauptprozess gesendet. Textdeltas, Fortschritt und TTS gehen absenderbezogen an `event.sender` (`ipc.ts:707-746`, 1523, 1760, 2345, 2413/2424, 2687, 1055).
- **Fensterbindung:**
  - Dateidialoge holen ihr Elternfenster aus `BrowserWindow.fromWebContents(event.sender)` (7 Stellen).
  - Der Browser-Bereich haengt per `addChildView` an Rowboats Fenster.
  - Zoom, Menue, Quick-Ask, Push-to-Talk, Deeplinks und das Meeting-Popup zielen auf `mainWindow`.
- **Protokoll `app://`:** privilegiert, mit Stream. Es liefert die Oberflaeche (`app://-/index.html`) und die Inhalte workspace, file-preview, space-blob, space-document (`main.ts:225-344`). Der Renderer nutzt `app://` an 79 Stellen.
- **Renderer:**
  - Kein Node, `sandbox:true`.
  - Preload-Globale: `window.ipc` (invoke/send/on), `window.featureFlags.spaces`, `window.electronUtils.getPathForFile` und `getZoomFactor`.
  - Die Nutzdaten sind JSON, Binaerdaten kommen als Base64.
- **Start:**
  - `--hidden` erzeugt das Fenster versteckt.
  - Die Single-Instance-Sperre gilt nur in gebauten Versionen.
  - `start_desktop.py` startet heute den Entwicklungsmodus (vite :5173).

**voice:**
- **Alte Einbettung:** `rowboat-manager.js` (BrowserView plus Bridge), `rowboat-preload.js`, `rowboat-core-bridge.mjs` sowie Stubs in `main.js:1955-2122`. Sie deckt 39 Kanaele ab. Seit Teil 1 ist sie stillgelegt (Statuskarte).

## Design

### 1. Einbettungszugang im Fork (`apps/main/src/embed-bridge.ts`)

**Aktivierung:** Nur mit `ROWBOAT_EMBED=1` oder `--embed`. Ohne den Schalter aendert sich am Verhalten nichts.

**Verbindung:**
- WebSocket nur auf `127.0.0.1`, freier Port.
- Beim Start schreibt Rowboat `~/.rowboat/embed.json` `{port, token, pid}`, nur fuer den eigenen Benutzer lesbar.
- Das Token sind 32 Zufallsbytes, pro Start neu.

**Anmeldung:**
- Die erste Nachricht muss `{type:"hello", token}` sein, sonst wird die Verbindung geschlossen.
- Das Token steht nie in einer URL.
- Verbindungen mit Browser-`Origin`-Header werden abgewiesen.

**Aufruf:**
- `{id, type:"invoke", channel, args}` wird wie bei `ipcMain.handle` verarbeitet: dieselbe Handler-Tabelle (aus `registerIpcHandlers()` herausgezogen), dieselbe Zod-Pruefung, dieselben Handler.
- Antwort: `{id, ok:true, result}` oder `{id, ok:false, error}`, in derselben Fehlerform wie der Electron-Weg.

**Absender:** Jeder Aufruf bekommt ein nachgebautes `event`, dessen `sender` die Verbindung ist. Er bietet `send(channel, payload)` (Push an genau diese Verbindung), `id`, `isDestroyed()` und das Ereignis `destroyed`.

**Kein neuer Umfang:** Nur die Kanaele aus `ipc.ts`. Handler, die ein eigenes Fenster brauchen und keinen Ersatz haben, antworten mit `embed_unsupported`.

### 2. Push und Absender

`broadcastToWindows` sendet zusaetzlich an alle angemeldeten Einbettungs-Verbindungen.

Absenderbezogene Streams funktionieren ueber den nachgebauten Absender ohne Sonderfall: turns-Deltas, `codeMode:engineProgress`, `apps:progress`, Dateivorschau, TTS und `theme:set`. Schliesst die Verbindung, gilt der Absender als zerstoert, und die Abos werden aufgeraeumt.

Fensterbindung, eingebettet:

| Bereich | Verhalten |
|---|---|
| Dateidialoge (7) | ohne Elternfenster (modelos) |
| `getZoomFactor` | liefert die voice-Seite |
| App-Menue, Quick-Ask, Push-to-Talk, Deeplinks, Meeting-Popup | bleiben an Rowboats Fenster; eingebettet nicht verfuegbar (`embed_unsupported`) |
| Browser-Bereich | Hinweis "In Rowboat oeffnen" (holt Rowboats Fenster) |

### 3. Medien ueber `app://`

**Fork:**
- Der `app://`-Handler (`main.ts:225-344`) wird zu einer eigenstaendigen Funktion herausgezogen.
- Der Einbettungszugang bietet ihn zusaetzlich ueber HTTP an, auf demselben Port: `GET /app/<pfad>`. Verlangt werden `Authorization: Bearer <token>` und Host `127.0.0.1`. Range-Anfragen und Streaming bleiben erhalten.

**voice:**
- Die Rowboat-Ansicht laeuft in der Sitzung `partition: persist:rowboat-embed`.
- Die voice-App registriert `app` als privilegiertes Schema (standard, secure, stream; vor `app.whenReady`) und darin `protocol.handle('app', …)`. Das reicht jede Anfrage mit dem Token an `http://127.0.0.1:<port>/app/<pfad>` weiter.
- Die Ansicht laedt `app://-/index.html`. Der Renderer bleibt unveraendert, es gibt keinen Vite-Server.

**Betrieb:** Rowboat laeuft fuer die Einbettung als gebaute Version. `start_desktop.py --embed` baut Renderer und Hauptprozess und startet Rowboat mit `--hidden --embed`.

### 4. voice-Seite

**`rowboat-preload.js` (neu, generisch):**
- Stellt bereit: `window.ipc.invoke/send/on`, `window.featureFlags.spaces`, `window.electronUtils.getPathForFile` (aus dem eigenen Preload) und `getZoomFactor`.
- Laedt in der Sandbox keine lokalen Module.
- Spricht nur per IPC mit dem voice-Hauptprozess; den Token sieht er nie.

**`rowboat-embed-client.js` (neu, Hauptprozess):**
- Liest `embed.json`, prueft die PID und haelt die WebSocket-Verbindung.
- Reicht `invoke` weiter und verteilt Push-Meldungen an die Ansicht.
- Verbindet sich bei Abbruch neu (mit Backoff) und registriert das `app`-Protokoll.

**`rowboat-manager.js`:**
- **Bleibt:** Ansicht, `show`/`hide`/`updateBounds`, Statuskarte und Re-Check (Teil 1).
- **Pruefung vor dem Laden:** Ist der Client verbunden? Sonst Statuskarte "Rowboat laeuft nicht (start_desktop.py --embed)".
- **Faellt weg:** Bridge (`:236-460`), `relayEvent`.

**`main.js`:**
- Faellt weg: Rowboat-Stubs (`:1955-2122`), `rowboat-core-bridge.mjs`, `autoConfigureRowboatModels`.
- Der Teil-1-Platzhalter ("kommt mit Teil 2") wird ersetzt.

### 5. Lebenszyklus und Sicherheit

**Start:**
- Die voice-App startet Rowboat nicht, sie uebernimmt nur (wie OpenFang und Brain in Teil 1).
- Der Launcher startet Rowboat im Einbettungsmodus als eigenen Schritt vor Phase 5, uebernimmt eine laufende Instanz und startet nichts doppelt.
- Ohne Launcher: Aufgabe "VibeMind Rowboat (Einbettung)" bei der Anmeldung.

**Single-Instance:**
- Im Einbettungsmodus gilt die Sperre immer, es gibt nie zwei Kerne auf `~/.rowboat`.
- Ein zweiter Start oder "In Rowboat oeffnen" holt das versteckte Fenster der laufenden Instanz nach vorn.

**Sicherheit:**
- Nur `127.0.0.1`, nur mit Schalter.
- Token pro Start neu, in `embed.json` nur fuer den Benutzer lesbar, nie in einer URL, nie im Renderer.
- Abweisen bei Browser-`Origin`, ohne `hello` oder mit falschem Token.
- Nachrichten maximal 64 MB.
- Der HTTP-Medienweg verlangt dasselbe Token und liefert nur, was `app://` der echten App liefert.

**Bekannte Grenze:** Die eingebettete Ansicht hat eigenen Browser-Speicher. Was Rowboat dort ablegt (z. B. das Theme), kann vom eigenen Fenster abweichen. Vault und Kern-Zustand sind geteilt.

### 6. Tests

**Fork (Vitest):**
- **Anmeldung:** falsches Token, fehlendes `hello`, Browser-`Origin` → geschlossen; ohne Schalter kein Server.
- **Gleichheit:** Stichprobe je Bereich (workspace, spaces, runs, integrations), gleiches Ergebnis wie der Electron-Weg, inklusive Zod-Fehler.
- **Absender und Push:**
  - turns-Deltas nur an die abonnierende Verbindung; Abos nach dem Schliessen weg.
  - Broadcasts kommen an.
  - Abgleichstest der 54 Push-Kanaele.
- **Medien:** `GET /app/...` mit und ohne Token, Range fuer Video, unbekannter Pfad → 404.
- **Grenzen:** zu grosse Nachricht → abgewiesen; fensterbezogener Kanal → `embed_unsupported`.

**voice (`node --test`, Attrappen):**
- Preload stellt die API ohne lokales `require` bereit.
- Client: Anmeldung, Weiterleitung, Push, Neuverbindung, Statuskarte.
- `app`-Protokoll reicht mit Token weiter, Token nie im Renderer.

**Aeusseres Repo (Pester):**
- Launcher-Schritt startet Rowboat-Einbettung nur, wenn keine Instanz laeuft.
- Update-Skript baut Rowboat (`--embed`-Build).

## Abnahme (echt, mit dem User)

1. Der Launcher startet Rowboat im Einbettungsmodus (versteckt), dann die Multiverse-Ansicht.
2. Ein Klick auf den Rowboat-Space zeigt die volle Rowboat-Oberflaeche im Multiverse-Fenster.
3. Notizen oeffnen und bearbeiten; ein Chat streamt live.
4. Der 3D-Graph (VibeMind im Zentrum) und der Plugins-Tab sind da; ein Bild und ein Video aus dem Vault werden angezeigt.
5. Ein Dateidialog oeffnet sich, Drag & Drop einer Datei funktioniert.
6. "In Rowboat oeffnen" holt Rowboats Fenster; es entsteht kein zweiter Kern.
7. Wird Rowboat beendet, zeigt die Ansicht die Statuskarte. Nach dem Neustart verbindet sie sich ohne App-Neustart wieder.
8. Gegenprobe: Verbindungsversuche mit falschem Token und aus dem Browser werden abgewiesen.

## Nicht in Teil 2

- Browser-Bereich, Quick-Ask, Meeting-Popup und Push-to-Talk eingebettet
- gemeinsamer Browser-Speicher beider Oberflaechen
- Rowboat auf der VM

## Risiken

- **Fork-Weiterentwicklung:** Der Upstream aendert `ipc.ts` und `main.ts` oft. Der Einbettungszugang setzt deshalb an der zentralen Handler-Tabelle und am herausgezogenen `app://`-Handler an, damit Sync-Konflikte klein bleiben. Der Abgleichstest der Push-Kanaele schlaegt bei neuen Kanaelen an.
- **Gleichzeitige Arbeit:** Eine andere Session arbeitet im Fork-Worktree `wt-rowboat-vibemind` an der Integrationen-Seite (`dfb4f38a`). Die Umsetzung bekommt einen eigenen Worktree und stimmt Branch und Merge ab.
- **Speicher:** Rowboat laeuft zusaetzlich versteckt neben Stack und voice-App (Docker-VM 9 GB). Vor der Abnahme wird gemessen.
