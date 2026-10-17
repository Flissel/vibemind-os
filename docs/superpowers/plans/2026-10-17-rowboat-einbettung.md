# Rowboat in der Multiverse-Ansicht einbetten – Umsetzungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Die volle Rowboat-Oberflaeche laeuft im Roarboot-Space der Multiverse-Ansicht. Rowboats eigener Hauptprozess beantwortet dabei alle Kanaele, die voice-App reicht nur durch.

**Architecture:**
- **Fork:** Rowboat bekommt im Einbettungsmodus (`--embed`) einen Zugang auf `127.0.0.1` mit Token pro Start. Er besteht aus einem WebSocket fuer `invoke` und Push und einem HTTP-Weg fuer `app://`.
  - Der elektronfreie Kern liegt in `@x/server` und ist dort mit Vitest testbar.
  - Die Anbindung an Electron liegt in `apps/main`.
- **voice:** Die voice-App laedt Rowboats eigenen, gebauten Preload in eine BrowserView. Sie beantwortet dessen `ipcRenderer.invoke` pro Ansicht (`webContents.ipc.handle`) ueber den WebSocket und leitet `app://` mit dem Token im Hauptprozess weiter.
- **Launcher:** Der Launcher startet Rowboat vor Phase 5 versteckt, das Update-Skript baut es.

**Tech Stack:** Electron 39 (Rowboat), Electron 33 (voice), TypeScript strict + Zod + Vitest + ws (Fork), Node `node:test` (voice), Python/pytest (`start_desktop.py`), PowerShell 7 + Pester 5 (Launcher).

**Spec:** `vibemind-os/docs/superpowers/specs/2026-10-17-rowboat-einbettung-design.md`

## Repos, Worktrees, Branches

| Repo | Worktree (neu, in Vorbereitung angelegt) | Branch | Basis |
|---|---|---|---|
| Fork `Flissel/rowboat` | `C:\Users\User\ClaudeWork\wt-rowboat-embed` | `feat/embed-bridge` | `origin/vibemind` |
| voice `Flissel/VibeMind-VoiceDialog` | `C:\Users\User\ClaudeWork\wt-voice-rowboat` | `feat/rowboat-einbettung` | `origin/codex/laura-video-interface-embed` |
| vibemind-os | `C:\Users\User\Desktop\Vibemind_V1\vibemind-os\.worktrees\setup-agent` (besteht) | `master` | – |
| aeusseres Repo Vibemind_V1 | `C:\Users\User\Desktop\Vibemind_V1` (besteht) | `codex/vibemind-ops-baseline` | – |

Pfadkuerzel im Plan: `FORK = C:\Users\User\ClaudeWork\wt-rowboat-embed\apps\x`, `VOICE = C:\Users\User\ClaudeWork\wt-voice-rowboat\electron-app`, `OS = …\vibemind-os\.worktrees\setup-agent`, `V1 = C:\Users\User\Desktop\Vibemind_V1`.

## Global Constraints

- Aktivierung nur mit `--embed` oder `ROWBOAT_EMBED=1`; ohne Schalter aendert sich am Rowboat-Verhalten nichts (kein Server, keine `embed.json`).
- Zugang nur auf `127.0.0.1`, freier Port; `embed.json` = `{v:1, port, token, pid, preloadPath}` in `WorkDir` (`ROWBOAT_WORKDIR` bzw. `~/.rowboat`), Modus `0o600`.
- Token: 32 Zufallsbytes (`base64url`), pro Start neu, nie in einer URL, nie im Renderer, nie im Log.
- Erste WS-Nachricht muss `{type:"hello", v:1, token}` sein, sonst Close `4401`; Browser-`Origin` und falscher `Host` → `403`; Nachrichten maximal 64 MB (`64 * 1024 * 1024`).
- Gleiche Handler-Tabelle, gleiche Zod-Pruefung wie `ipcMain.handle` (eine Funktion `invokeIpcChannel`).
- Fensterbezogene Kanaele ohne Ersatz antworten mit Fehlercode `embed_unsupported`.
- Die voice-App startet Rowboat nicht, sie verbindet sich nur. Start macht der Launcher (bzw. die Anmelde-Aufgabe).
- Im Einbettungsmodus gilt die Single-Instance-Sperre immer; nie zwei Kerne auf demselben `WorkDir`.
- Rowboat-Kopie `vibemind-os/spaces/rowboat/rowboat` wird nie von Hand geaendert, nur per `sync_desktop.py` (Task 10). `apps/harbor` und `spaces/rowboat/schema` werden nicht angefasst.
- Commits im Fork mit `ENTIRE_TEST_TTY=0 git commit`; Conventional Commits; kein Force-Push; keine Branch-Loeschung; kein Merge und kein Push auf `vibemind`, `codex/laura-video-interface-embed` oder vibemind-os `master` vor dem User-OK in Task 10. Neue Feature-Branches duerfen als Sicherung gepusht werden.
- Fremde Aenderungen nie verwerfen, stashen oder mitcommitten; vor jedem checkout/merge `git status`. Im Fork-Worktree `wt-rowboat-vibemind` arbeitet eine andere Session – dort nichts anfassen.
- Kein `console.log` in neuem voice-Code ausser ueber den uebergebenen `logger`; im Fork folgt neuer Code dem vorhandenen Stil (`console.log`/`console.error` mit `[embed]`-Praefix ist dort ueblich).
- TypeScript: kein `any`; `unknown` und eingrenzen.

## Bewusste Abweichungen von der Spec (Rulings)

1. **Preload:** Die voice-App nimmt Rowboats **eigenen gebauten Preload** (`preloadPath` aus `embed.json`) statt eines nachgebauten `rowboat-preload.js`.
   - Warum: `window.ipc`, `featureFlags` und `electronUtils` bleiben damit exakt Rowboats Version, ein Rowboat-Update braucht keine Nacharbeit (Spec-Ziel).
   - Die Spec-Anforderungen bleiben erfuellt: Sandbox, kein lokales `require` (Bundle), Token nie im Renderer. Der Preload laeuft mit `sandbox:true` und braucht nur `require('electron')`.
   - Kosten bei Irrtum: ein generischer Preload wie in Spec 4, etwa 40 Zeilen.
2. **Ort des Kerns:** Der Kern liegt in `apps/server/src/embed-bridge.ts`, die Electron-Anbindung in `apps/main/src/embed-host.ts`. In der Spec stand alles in `apps/main/src/embed-bridge.ts`.
   - Warum: `apps/main` hat keinen Testlauf. `@x/server` hat `ws`, Vitest und `tokenMatches`.
3. **Push-Weg:** `broadcastToWindows` plus die zwei Stellen, die an ihm vorbei an alle Fenster senden (`spaces:events`, `power:caffeinateChanged`), rufen `mirrorToEmbed`. Gespiegelt wird nur, was in `EMBED_MIRRORED_PUSH` steht.
   - Die Klassifizierung aller 54 Push-Kanaele steht in `@x/shared` und wird dort gegen `ipcSchemas` getestet (Spec 6: Abgleichstest).
4. **Browser-Bereich:** `browser:*`- und `video:*`-Aufrufe antworten mit `embed_unsupported`. Beim ersten `browser:setVisible {visible:true}` zeigt die voice-App einen Dialog „Der Browser-Bereich laeuft nur im Rowboat-Fenster. [In Rowboat öffnen] [Abbrechen]“. Die Spec nennt nur einen Hinweis „In Rowboat oeffnen“.
5. **Start ohne `npm`:** Im Einbettungsmodus startet `start_desktop.py` die gebaute App direkt ueber `apps/main/node_modules/.bin/electron(.cmd) . --hidden --embed`, nicht ueber `npm run start`.
   - Warum: Dessen `env -u` braucht Git-`usr/bin` im PATH, das der Launcher nicht garantiert.
   - `ELECTRON_RUN_AS_NODE` entfernt das Skript selbst aus der Umgebung.

## Review Focus

1. **Veraltete `embed.json`:** Rowboat ist abgestuerzt, und die PID gehoert inzwischen einem anderen Prozess; der Port ist zu. Erwartet: Statuskarte, Wiederholung mit Backoff, kein Haengen. Test in Task 6 („lebende PID, toter Port“).
2. **Zu grosse Antwort:** Eine Antwort ueber 64 MB (z. B. eine grosse Datei als Base64) darf die Verbindung nicht kappen, sonst laedt die ganze Ansicht neu. Erwartet: Fehler `embed_too_large` nur fuer diesen Aufruf, die Verbindung bleibt. Test in Task 2.
3. **Aufruf ohne Verbindung:** Waehrend der Neuverbindung muss ein `invoke` sofort mit „Rowboat nicht verbunden“ scheitern, nicht haengen. Ein Aufruf, der beim Abbruch noch offen ist, wird abgelehnt. Tests in Task 6.
4. **Kodierte Pfade:** `app://workspace/Meine%20Notizen/b%C3%A4r.png` muss genau einmal kodiert ankommen, auf beiden Strecken (voice-Proxy und Bridge). Tests in Task 3 und Task 7.
5. **Browser-Hinweis:** Wiederholtes `browser:setVisible` zeigt den Dialog nur einmal pro Sitzung. Test in Task 8.

---

## Vorbereitung (vor Task 1, keine eigene Review)

- [ ] **V1: Live-Lock setzen.** In `V1\WORKBOARD.md` eine Zeile `cc-rowboat-einbettung · 2026-10-17 · **IN ARBEIT.** Fork feat/embed-bridge, voice feat/rowboat-einbettung, vibemind-os start_desktop.py, Launcher Phase 4.5. Spec/Plan 2026-10-17-rowboat-einbettung.` unter „In Arbeit“ eintragen. Nur die eigene Zeile stagen (`git add -p WORKBOARD.md`, s. Memory „Board-Dateien: nur eigene Zeile stagen“) und mit `docs: workboard claim cc-rowboat-einbettung` committen.
- [ ] **V2: Fork-Worktree anlegen.**

```powershell
git -C C:\Users\User\Desktop\rowboat fetch origin vibemind
git -C C:\Users\User\Desktop\rowboat worktree add C:\Users\User\ClaudeWork\wt-rowboat-embed -b feat/embed-bridge origin/vibemind
cd C:\Users\User\ClaudeWork\wt-rowboat-embed\apps\harbor; pnpm install --frozen-lockfile; pnpm -r build
cd C:\Users\User\ClaudeWork\wt-rowboat-embed\apps\x; pnpm install --frozen-lockfile; npm run deps
```
Erwartet: `npm run deps` endet mit Exit 0.

- [ ] **V3: voice-Worktree anlegen.**

```powershell
git -C C:\Users\User\ClaudeWork\wt-voice-startprofil fetch origin
git -C C:\Users\User\ClaudeWork\wt-voice-startprofil worktree add C:\Users\User\ClaudeWork\wt-voice-rowboat -b feat/rowboat-einbettung origin/codex/laura-video-interface-embed
cd C:\Users\User\ClaudeWork\wt-voice-rowboat\electron-app; npm install --no-audit --no-fund; npm run test:unit
```
Erwartet: `test:unit` gruen (Ausgangslage festhalten).

- [ ] **V4: Ausgangslagen festhalten.**
  - `cd OS; python -m pytest spaces/rowboat/tests -q` (gruen).
  - `cd V1; Invoke-Pester scripts/tests/voice-electron-launch.Tests.ps1, scripts/tests/voice-betrieb-update.Tests.ps1` (gruen).

---

### Task 1: Push-Kanaele fuer die Einbettung einordnen (Fork, `@x/shared`)

**Files:**
- Create: `FORK\packages\shared\src\embed-channels.ts`
- Create: `FORK\packages\shared\src\embed-channels.test.ts`
- Modify: `FORK\packages\shared\src\index.ts` (Export ergaenzen)

**Interfaces:**
- Produces:
  - `EMBED_MIRRORED_PUSH: readonly string[]` (21)
  - `EMBED_SENDER_PUSH: readonly string[]` (3)
  - `EMBED_WINDOW_PUSH: readonly string[]` (30)
  - `EMBED_UNSUPPORTED_INVOKE_PREFIXES = ['browser:', 'video:']`
  - `isEmbedMirrored(channel: string): boolean`
  - `isEmbedUnsupportedInvoke(channel: string): boolean`
  - Import im Hauptprozess: `@x/shared/dist/embed-channels.js`.

- [ ] **Step 1: Failing test schreiben** – `embed-channels.test.ts`:

```ts
import { describe, it, expect } from 'vitest'
import { ipcSchemas, isPushChannel } from './ipc.js'
import { PUSH_CHANNELS } from './push-channels.js'
import {
  EMBED_MIRRORED_PUSH, EMBED_SENDER_PUSH, EMBED_WINDOW_PUSH,
  isEmbedMirrored, isEmbedUnsupportedInvoke,
} from './embed-channels.js'

const allPush = Object.keys(ipcSchemas)
  .filter((c) => isPushChannel(c as keyof typeof ipcSchemas))
  .sort()

describe('Einbettung: Push-Kanaele', () => {
  it('jeder Push-Kanal steht in genau einer Gruppe (neuer Kanal = Test rot, bitte einordnen)', () => {
    const all = [...EMBED_MIRRORED_PUSH, ...EMBED_SENDER_PUSH, ...EMBED_WINDOW_PUSH]
    expect(new Set(all).size).toBe(all.length)
    expect([...all].sort()).toEqual(allPush)
  })
  it('alle Broadcast-Kanaele des rowboat-server erreichen die Einbettung', () => {
    for (const c of PUSH_CHANNELS) {
      expect([...EMBED_MIRRORED_PUSH, ...EMBED_SENDER_PUSH]).toContain(c)
    }
  })
  it('isEmbedMirrored nur fuer gespiegelte Kanaele', () => {
    expect(isEmbedMirrored('runs:events')).toBe(true)
    expect(isEmbedMirrored('spaces:events')).toBe(true)
    expect(isEmbedMirrored('voice:tts-chunk')).toBe(false)
    expect(isEmbedMirrored('quick-ask:submit')).toBe(false)
    expect(isEmbedMirrored('gibt-es-nicht')).toBe(false)
  })
  it('Browser-Bereich und Video-Popout sind eingebettet nicht verfuegbar', () => {
    expect(isEmbedUnsupportedInvoke('browser:setVisible')).toBe(true)
    expect(isEmbedUnsupportedInvoke('video:setPopout')).toBe(true)
    expect(isEmbedUnsupportedInvoke('workspace:readFile')).toBe(false)
    expect(isEmbedUnsupportedInvoke('app:getVersions')).toBe(false)
  })
})
```

- [ ] **Step 2: Test laufen lassen, er muss scheitern**

Run: `cd FORK\packages\shared; npx vitest run src/embed-channels.test.ts`
Expected: FAIL („Failed to load url ./embed-channels.js“ o. ae.)

- [ ] **Step 3: Implementieren** – `embed-channels.ts`:

```ts
// VibeMind-Einbettung (Spec vibemind-os 2026-10-17-rowboat-einbettung): wohin jeder Push-Kanal
// im Einbettungsmodus geht. Ein neuer Push-Kanal in ipc.ts laesst embed-channels.test.ts scheitern,
// bis er hier eingeordnet ist.
//
// mirrored – an alle Fenster gesendet (broadcastToWindows, spaces:events, power:caffeinateChanged);
//            geht zusaetzlich an jede Einbettungs-Verbindung. turns:events: durable Ereignisse hier,
//            die Text-Deltas kommen absenderbezogen ueber turns:subscribe.
// sender   – absenderbezogen (event.sender.send); erreicht die Einbettung ueber ihren eigenen Absender.
// window   – gehoert zu Rowboats eigenem Fenster (Menue, Quick-Ask, PTT, Deeplinks, Meeting-Popup,
//            Updater, Browser-Bereich, Video-Popout) und geht nie an die Einbettung.

export const EMBED_MIRRORED_PUSH = [
  'workspace:didChange', 'runs:events', 'codeRun:events', 'sessions:events', 'turns:events',
  'services:events', 'live-note-agent:events', 'bg-task-agent:events', 'todo:events',
  'home:threadsChanged', 'oauth:didConnect', 'chatgpt:statusChanged', 'codeSession:status',
  'terminal:data', 'terminal:exit', 'power:caffeinateChanged', 'channels:status',
  'composio:didConnect', 'knowledge:didCommit', 'credits:didActivate', 'spaces:events',
] as const

export const EMBED_SENDER_PUSH = ['codeMode:engineProgress', 'apps:progress', 'voice:tts-chunk'] as const

export const EMBED_WINDOW_PUSH = [
  'app:openUrl', 'app:takeMeetingNotes', 'app:toggleMeetingNotes', 'updater:status',
  'menu:command', 'menu:toggleSidebar', 'shortcuts:switcherKey', 'meeting:externalCallEnded',
  'voice:ptt-key', 'quick-ask:submit', 'quick-ask:mode', 'quick-ask:tuck', 'quick-ask:cursor',
  'quick-ask:dragging', 'quick-ask:open-chat', 'quick-ask:chat-context', 'quick-ask:select-chat',
  'quick-ask:new-chat', 'quick-ask:shortcut-changed', 'theme:changed', 'meetingDetect:payload',
  'video:popout-state', 'video:popout-levels', 'video:popout-action', 'screen-pointer:state',
  'browser:didUpdateState', 'browser:httpAuthRequest', 'browser:httpAuthResolved',
  'browser:displayMediaRequest', 'browser:displayMediaResolved',
] as const

/** Aufrufe, die ein eigenes Rowboat-Fenster brauchen und eingebettet `embed_unsupported` liefern. */
export const EMBED_UNSUPPORTED_INVOKE_PREFIXES = ['browser:', 'video:'] as const

const mirrored: ReadonlySet<string> = new Set(EMBED_MIRRORED_PUSH)

export function isEmbedMirrored(channel: string): boolean {
  return mirrored.has(channel)
}

export function isEmbedUnsupportedInvoke(channel: string): boolean {
  return EMBED_UNSUPPORTED_INVOKE_PREFIXES.some((prefix) => channel.startsWith(prefix))
}
```

In `index.ts` hinter der Zeile `export * as integrations from './integrations.js';` ergaenzen:

```ts
export * as embedChannels from './embed-channels.js';
```

- [ ] **Step 4: Tests laufen lassen, sie muessen bestehen**

Run: `cd FORK\packages\shared; npx vitest run src/embed-channels.test.ts; npm run typecheck; npm run build`
Expected: 4 Tests PASS, typecheck und build Exit 0. Falls der erste Test eine Abweichung zeigt (Upstream hat seit 17.10. einen Push-Kanal ergaenzt), den neuen Kanal nach seinem Sender in `ipc.ts` einordnen (`grep -n "'<kanal>'" apps/main/src`) und das im Report nennen.

- [ ] **Step 5: Commit**

```powershell
cd C:\Users\User\ClaudeWork\wt-rowboat-embed
git add apps/x/packages/shared/src/embed-channels.ts apps/x/packages/shared/src/embed-channels.test.ts apps/x/packages/shared/src/index.ts
$env:ENTIRE_TEST_TTY='0'; git commit -m "feat(shared): Push-Kanaele fuer die VibeMind-Einbettung einordnen"
```

---

### Task 2: Einbettungszugang – WebSocket-Kern (Fork, `@x/server`)

**Files:**
- Create: `FORK\apps\server\src\embed-bridge.ts`
- Create: `FORK\apps\server\src\embed-bridge.test.ts`
- Modify: `FORK\apps\server\src\index.ts` (Exporte)

**Interfaces:**
- Consumes: `tokenMatches(presented, expected)` und `extractBearer(header, query?)` aus `./auth.js`.
- Produces (Export aus `@x/server`):
  - Typen: `EmbedSender`, `EmbedBridge`, `EmbedBridgeOptions`.
  - Funktionen:
    - `startEmbedBridge(opts: EmbedBridgeOptions): Promise<EmbedBridge>`
    - `generateEmbedToken(): string`
    - `isEmbedSender(x: unknown): x is EmbedSender`
  - Konstanten: `EMBED_CLOSE_UNAUTHORIZED = 4401`, `EMBED_UNSUPPORTED = 'embed_unsupported'`, `EMBED_TOO_LARGE = 'embed_too_large'`, `EMBED_MAX_MESSAGE_BYTES`.
  - `EmbedBridge` = `{ port: number; broadcast(channel, payload): void; connectionCount(): number; close(): Promise<void> }`.
- Protokoll (vom Client zu Rowboat):
  - `{type:'hello', v:1, token}`
  - `{type:'invoke', id:number, channel, args}`
  - `{type:'show-app'}`
- Protokoll (von Rowboat zum Client):
  - `{type:'welcome', v:1, invokeChannels:string[]}`
  - `{type:'result', id, ok:true, result}` bzw. `{type:'result', id, ok:false, error:{message, code?}}`
  - `{type:'push', channel, payload}`

- [ ] **Step 1: Failing tests schreiben** – `embed-bridge.test.ts`:

```ts
import { afterEach, describe, expect, it } from 'vitest'
import { WebSocket } from 'ws'
import { ipc } from '@x/shared'
import {
  EMBED_CLOSE_UNAUTHORIZED, EMBED_TOO_LARGE, EMBED_UNSUPPORTED,
  isEmbedSender, startEmbedBridge, type EmbedBridge, type EmbedBridgeOptions, type EmbedSender,
} from './embed-bridge.js'

type Msg = Record<string, unknown>
const TOKEN = 'geheim-geheim-geheim'
const open: EmbedBridge[] = []
afterEach(async () => { while (open.length) await open.pop()!.close() })

async function bridge(over: Partial<EmbedBridgeOptions> = {}) {
  const calls: Array<{ channel: string; args: unknown; sender: EmbedSender }> = []
  const shown: number[] = []
  const b = await startEmbedBridge({
    token: TOKEN,
    invokeChannels: () => ['workspace:exists', 'turns:subscribe'],
    isUnsupported: (c) => c.startsWith('browser:'),
    invoke: async (channel, sender, args) => { calls.push({ channel, args, sender }); return { echo: args } },
    serveApp: async () => new Response('x'),
    onShowApp: () => { shown.push(1) },
    helloTimeoutMs: 300,
    ...over,
  })
  open.push(b)
  return { b, calls, shown }
}

function client(b: EmbedBridge, headers: Record<string, string> = {}) {
  const ws = new WebSocket(`ws://127.0.0.1:${b.port}/embed`, { headers })
  const inbox: Msg[] = []
  const waiters: Array<(m: Msg) => void> = []
  ws.on('message', (d) => {
    const m = JSON.parse(String(d)) as Msg
    const w = waiters.shift()
    if (w) w(m); else inbox.push(m)
  })
  const closed = new Promise<number>((r) => ws.on('close', (code) => r(code)))
  const opened = new Promise<void>((resolve, reject) => {
    ws.on('open', () => resolve())
    ws.on('unexpected-response', (_req, res) => reject(new Error(`HTTP ${res.statusCode}`)))
    ws.on('error', reject)
  })
  const next = () => inbox.length ? Promise.resolve(inbox.shift()!) : new Promise<Msg>((r) => waiters.push(r))
  const send = (m: Msg) => ws.send(JSON.stringify(m))
  return { ws, opened, closed, next, send }
}

async function loggedIn(b: EmbedBridge) {
  const c = client(b)
  await c.opened
  c.send({ type: 'hello', v: 1, token: TOKEN })
  const welcome = await c.next()
  return { ...c, welcome }
}

describe('Einbettungszugang: Anmeldung', () => {
  it('falsches Token schliesst mit 4401, nichts wird aufgerufen', async () => {
    const { b, calls } = await bridge()
    const c = client(b); await c.opened
    c.send({ type: 'hello', v: 1, token: 'falsch' })
    expect(await c.closed).toBe(EMBED_CLOSE_UNAUTHORIZED)
    expect(calls).toEqual([])
  })
  it('erste Nachricht ist kein hello: 4401', async () => {
    const { b, calls } = await bridge()
    const c = client(b); await c.opened
    c.send({ type: 'invoke', id: 1, channel: 'workspace:exists', args: { path: 'a' } })
    expect(await c.closed).toBe(EMBED_CLOSE_UNAUTHORIZED)
    expect(calls).toEqual([])
  })
  it('kein hello innerhalb der Frist: 4401', async () => {
    const { b } = await bridge()
    const c = client(b); await c.opened
    expect(await c.closed).toBe(EMBED_CLOSE_UNAUTHORIZED)
  })
  it('Browser-Origin wird beim Upgrade abgewiesen', async () => {
    const { b } = await bridge()
    await expect(client(b, { origin: 'https://evil.example' }).opened).rejects.toThrow('HTTP 403')
  })
  it('fremder Host-Header wird abgewiesen (DNS-Rebinding)', async () => {
    const { b } = await bridge()
    await expect(client(b, { host: `localhost:${b.port}` }).opened).rejects.toThrow('HTTP 403')
  })
  it('welcome nennt die Aufrufkanaele', async () => {
    const { b } = await bridge()
    const c = await loggedIn(b)
    expect(c.welcome).toEqual({ type: 'welcome', v: 1, invokeChannels: ['workspace:exists', 'turns:subscribe'] })
  })
})

describe('Einbettungszugang: Aufrufe', () => {
  it('Ergebnis kommt mit derselben id zurueck', async () => {
    const { b, calls } = await bridge()
    const c = await loggedIn(b)
    c.send({ type: 'invoke', id: 7, channel: 'workspace:exists', args: { path: 'a.md' } })
    expect(await c.next()).toEqual({ type: 'result', id: 7, ok: true, result: { echo: { path: 'a.md' } } })
    expect(calls[0]?.channel).toBe('workspace:exists')
  })
  it('Fehler des Handlers kommt als ok:false mit Meldung', async () => {
    const { b } = await bridge({ invoke: async () => { throw new Error('kaputt') } })
    const c = await loggedIn(b)
    c.send({ type: 'invoke', id: 1, channel: 'workspace:exists', args: null })
    expect(await c.next()).toEqual({ type: 'result', id: 1, ok: false, error: { message: 'kaputt' } })
  })
  it('Zod-Fehler gleicht dem Electron-Weg (gleiche validateRequest-Meldung)', async () => {
    const { b } = await bridge({
      invoke: async (channel, _s, args) => ipc.validateRequest(channel as 'workspace:exists', args),
    })
    let expected = ''
    try { ipc.validateRequest('workspace:exists', { path: 42 }) } catch (e) { expected = (e as Error).message }
    const c = await loggedIn(b)
    c.send({ type: 'invoke', id: 2, channel: 'workspace:exists', args: { path: 42 } })
    const r = await c.next()
    expect(r.ok).toBe(false)
    expect((r.error as Msg).message).toBe(expected)
  })
  it('fensterbezogener Kanal: embed_unsupported, Handler wird nicht aufgerufen', async () => {
    const { b, calls } = await bridge()
    const c = await loggedIn(b)
    c.send({ type: 'invoke', id: 3, channel: 'browser:setVisible', args: { visible: true } })
    const r = await c.next()
    expect(r.ok).toBe(false)
    expect((r.error as Msg).code).toBe(EMBED_UNSUPPORTED)
    expect(calls).toEqual([])
  })
  it('zu grosse Antwort: embed_too_large, Verbindung bleibt offen', async () => {
    const { b } = await bridge({ maxMessageBytes: 1024, invoke: async () => 'x'.repeat(4096) })
    const c = await loggedIn(b)
    c.send({ type: 'invoke', id: 4, channel: 'workspace:exists', args: null })
    const r = await c.next()
    expect(r).toMatchObject({ type: 'result', id: 4, ok: false, error: { code: EMBED_TOO_LARGE } })
    c.send({ type: 'invoke', id: 5, channel: 'workspace:exists', args: null })
    expect((await c.next()).id).toBe(5)
  })
  it('zu grosse eingehende Nachricht schliesst mit 1009', async () => {
    const { b } = await bridge({ maxMessageBytes: 1024 })
    const c = await loggedIn(b)
    c.send({ type: 'invoke', id: 6, channel: 'workspace:exists', args: 'x'.repeat(4096) })
    expect(await c.closed).toBe(1009)
  })
  it('show-app ruft onShowApp', async () => {
    const { b, shown } = await bridge()
    const c = await loggedIn(b)
    c.send({ type: 'show-app' })
    await new Promise((r) => setTimeout(r, 50))
    expect(shown).toEqual([1])
  })
})

describe('Einbettungszugang: Absender und Push', () => {
  it('sender.send erreicht nur die aufrufende Verbindung', async () => {
    const { b } = await bridge({
      invoke: async (_c, sender, args) => { sender.send('voice:tts-chunk', { by: args }); return { ok: true } },
    })
    const a = await loggedIn(b)
    const other = await loggedIn(b)
    a.send({ type: 'invoke', id: 1, channel: 'workspace:exists', args: 'a' })
    expect(await a.next()).toEqual({ type: 'push', channel: 'voice:tts-chunk', payload: { by: 'a' } })
    expect((await a.next()).type).toBe('result')
    other.send({ type: 'invoke', id: 9, channel: 'workspace:exists', args: 'b' })
    // Waere der Push von a durchgesickert, kaeme hier zuerst { by: 'a' }.
    expect(await other.next()).toEqual({ type: 'push', channel: 'voice:tts-chunk', payload: { by: 'b' } })
  })
  it('Absender ist als Einbettung erkennbar, hat eigene id und wird beim Schliessen zerstoert', async () => {
    const seen: EmbedSender[] = []
    const { b } = await bridge({ invoke: async (_c, s) => { seen.push(s); return null } })
    const c = await loggedIn(b)
    c.send({ type: 'invoke', id: 1, channel: 'workspace:exists', args: null })
    await c.next()
    const sender = seen[0]!
    expect(isEmbedSender(sender)).toBe(true)
    expect(isEmbedSender({ id: 1, send() {} })).toBe(false)
    expect(sender.id).toBeGreaterThanOrEqual(1_000_000_000)
    let destroyedEvent = false
    sender.once('destroyed', () => { destroyedEvent = true })
    c.ws.close()
    await c.closed
    await new Promise((r) => setTimeout(r, 50))
    expect(sender.isDestroyed()).toBe(true)
    expect(destroyedEvent).toBe(true)
    expect(b.connectionCount()).toBe(0)
  })
  it('broadcast erreicht alle angemeldeten Verbindungen, keine unangemeldeten', async () => {
    const { b } = await bridge({ helloTimeoutMs: 2000 })
    const a = await loggedIn(b)
    const anon = client(b); await anon.opened
    b.broadcast('runs:events', { x: 1 })
    expect(await a.next()).toEqual({ type: 'push', channel: 'runs:events', payload: { x: 1 } })
    anon.send({ type: 'hello', v: 1, token: TOKEN })
    expect((await anon.next()).type).toBe('welcome') // kein Push vor der Anmeldung
  })
})
```

- [ ] **Step 2: Tests laufen lassen, sie muessen scheitern**

Run: `cd FORK\apps\server; npx vitest run src/embed-bridge.test.ts`
Expected: FAIL (Modul `./embed-bridge.js` fehlt)

- [ ] **Step 3: Implementieren** – `embed-bridge.ts`:

```ts
import crypto from 'node:crypto';
import { EventEmitter } from 'node:events';
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';
import type { AddressInfo } from 'node:net';
import type { Duplex } from 'node:stream';
import { WebSocket, WebSocketServer, type RawData } from 'ws';
import { z } from 'zod';
import { tokenMatches } from './auth.js';

// VibeMind-Einbettung (Spec vibemind-os 2026-10-17-rowboat-einbettung): ein lokaler Zugang, ueber
// den eine fremde Electron-Ansicht (die Multiverse-Ansicht der voice-App) Rowboats eigene
// IPC-Handler aufruft. Nur 127.0.0.1, Token pro Start, erste Nachricht = hello. Der Kern hier
// kennt kein Electron; apps/main/src/embed-host.ts verdrahtet ihn mit der Handler-Tabelle.

export const EMBED_PROTOCOL_VERSION = 1;
export const EMBED_MAX_MESSAGE_BYTES = 64 * 1024 * 1024;
export const EMBED_HELLO_TIMEOUT_MS = 5000;
export const EMBED_CLOSE_UNAUTHORIZED = 4401;
export const EMBED_CLOSE_BAD_MESSAGE = 4400;
export const EMBED_UNSUPPORTED = 'embed_unsupported';
export const EMBED_TOO_LARGE = 'embed_too_large';

const EMBED_SENDER = Symbol.for('rowboat.embedSender');
// Weit weg von echten webContents-IDs (file-previews.ts schluesselt nach sender.id).
let nextSenderId = 1_000_000_000;

/** Nachgebauter `event.sender` einer Einbettungs-Verbindung (das, was Handler davon benutzen). */
export interface EmbedSender {
  readonly id: number;
  send(channel: string, payload: unknown): void;
  isDestroyed(): boolean;
  on(event: 'destroyed', listener: () => void): this;
  once(event: 'destroyed', listener: () => void): this;
  removeListener(event: 'destroyed', listener: () => void): this;
}

class EmbedSenderImpl extends EventEmitter implements EmbedSender {
  readonly [EMBED_SENDER] = true;
  readonly id = nextSenderId++;
  private destroyed = false;

  constructor(private readonly deliver: (channel: string, payload: unknown) => void) {
    super();
  }

  send(channel: string, payload: unknown): void {
    if (!this.destroyed) this.deliver(channel, payload);
  }

  isDestroyed(): boolean {
    return this.destroyed;
  }

  destroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;
    this.emit('destroyed');
    this.removeAllListeners();
  }
}

export function isEmbedSender(x: unknown): x is EmbedSender {
  return typeof x === 'object' && x !== null && (x as Record<symbol, unknown>)[EMBED_SENDER] === true;
}

export function generateEmbedToken(): string {
  return crypto.randomBytes(32).toString('base64url');
}

export interface EmbedBridgeOptions {
  token: string;
  /** Validiert, ruft den Handler (oder den rowboat-server-Zwilling) und validiert die Antwort. */
  invoke(channel: string, sender: EmbedSender, args: unknown): Promise<unknown>;
  invokeChannels(): readonly string[];
  isUnsupported(channel: string): boolean;
  /** Der app://-Handler der App (Task 3). */
  serveApp(request: Request): Promise<Response>;
  onShowApp(): void;
  port?: number;
  helloTimeoutMs?: number;
  maxMessageBytes?: number;
  log?(message: string): void;
}

export interface EmbedBridge {
  readonly port: number;
  broadcast(channel: string, payload: unknown): void;
  connectionCount(): number;
  close(): Promise<void>;
}

const ClientMessage = z.discriminatedUnion('type', [
  z.object({ type: z.literal('hello'), v: z.literal(EMBED_PROTOCOL_VERSION), token: z.string() }),
  z.object({ type: z.literal('invoke'), id: z.number().int().nonnegative(), channel: z.string().min(1), args: z.unknown() }),
  z.object({ type: z.literal('show-app') }),
]);

interface Connection {
  socket: WebSocket;
  sender: EmbedSenderImpl;
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

export async function startEmbedBridge(opts: EmbedBridgeOptions): Promise<EmbedBridge> {
  const log = opts.log ?? (() => {});
  const helloTimeoutMs = opts.helloTimeoutMs ?? EMBED_HELLO_TIMEOUT_MS;
  const maxBytes = opts.maxMessageBytes ?? EMBED_MAX_MESSAGE_BYTES;
  const connections = new Set<Connection>();
  let port = 0;

  const fromBrowser = (req: IncomingMessage) => req.headers.origin !== undefined;
  const hostOk = (req: IncomingMessage) => req.headers.host === `127.0.0.1:${port}`;

  const http = createServer((req, res) => {
    void handleHttp(req, res);
  });
  const wss = new WebSocketServer({ noServer: true, maxPayload: maxBytes });

  http.on('upgrade', (req: IncomingMessage, socket: Duplex, head: Buffer) => {
    const pathname = new URL(req.url ?? '/', 'http://127.0.0.1').pathname;
    if (pathname !== '/embed' || !hostOk(req) || fromBrowser(req)) {
      socket.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n');
      return;
    }
    wss.handleUpgrade(req, socket, head, (ws) => attach(ws));
  });

  function attach(socket: WebSocket): void {
    let sender: EmbedSenderImpl | null = null;
    const helloTimer = setTimeout(() => socket.close(EMBED_CLOSE_UNAUTHORIZED, 'hello timeout'), helloTimeoutMs);
    const sendJson = (msg: unknown): void => {
      if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(msg));
    };

    socket.on('message', (data: RawData) => {
      let msg: z.infer<typeof ClientMessage>;
      try {
        msg = ClientMessage.parse(JSON.parse(data.toString()));
      } catch {
        socket.close(sender ? EMBED_CLOSE_BAD_MESSAGE : EMBED_CLOSE_UNAUTHORIZED, 'bad message');
        return;
      }
      if (!sender) {
        if (msg.type !== 'hello' || !tokenMatches(msg.token, opts.token)) {
          socket.close(EMBED_CLOSE_UNAUTHORIZED, 'unauthorized');
          return;
        }
        clearTimeout(helloTimer);
        const own = new EmbedSenderImpl((channel, payload) => sendJson({ type: 'push', channel, payload }));
        sender = own;
        const entry: Connection = { socket, sender: own };
        connections.add(entry);
        socket.once('close', () => connections.delete(entry));
        sendJson({ type: 'welcome', v: EMBED_PROTOCOL_VERSION, invokeChannels: [...opts.invokeChannels()] });
        return;
      }
      if (msg.type === 'hello') return;
      if (msg.type === 'show-app') {
        opts.onShowApp();
        return;
      }
      const { id, channel, args } = msg;
      if (opts.isUnsupported(channel)) {
        sendJson({ type: 'result', id, ok: false, error: { code: EMBED_UNSUPPORTED, message: `${EMBED_UNSUPPORTED}: ${channel} gibt es nur im Rowboat-Fenster` } });
        return;
      }
      opts.invoke(channel, sender, args).then(
        (result) => {
          const text = JSON.stringify({ type: 'result', id, ok: true, result });
          if (Buffer.byteLength(text) > maxBytes) {
            sendJson({ type: 'result', id, ok: false, error: { code: EMBED_TOO_LARGE, message: `${EMBED_TOO_LARGE}: Antwort auf ${channel} ueber ${maxBytes} Bytes` } });
            return;
          }
          if (socket.readyState === WebSocket.OPEN) socket.send(text);
        },
        (err: unknown) => sendJson({ type: 'result', id, ok: false, error: { message: errorMessage(err) } }),
      );
    });
    socket.on('close', () => {
      clearTimeout(helloTimer);
      sender?.destroy();
    });
    socket.on('error', (err) => log(`[embed] socket error: ${err.message}`));
  }

  // Task 3 ersetzt das durch den app://-Medienweg.
  async function handleHttp(_req: IncomingMessage, res: ServerResponse): Promise<void> {
    res.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' });
    res.end('Not Found');
  }

  await new Promise<void>((resolve, reject) => {
    http.once('error', reject);
    http.listen(opts.port ?? 0, '127.0.0.1', () => {
      http.off('error', reject);
      resolve();
    });
  });
  port = (http.address() as AddressInfo).port;

  return {
    get port() {
      return port;
    },
    broadcast(channel, payload) {
      for (const c of connections) c.sender.send(channel, payload);
    },
    connectionCount: () => connections.size,
    async close() {
      for (const c of connections) c.socket.terminate();
      wss.close();
      http.closeAllConnections();
      await new Promise<void>((resolve) => http.close(() => resolve()));
    },
  };
}
```

In `index.ts` ergaenzen:

```ts
export {
  startEmbedBridge, generateEmbedToken, isEmbedSender,
  EMBED_CLOSE_UNAUTHORIZED, EMBED_UNSUPPORTED, EMBED_TOO_LARGE, EMBED_MAX_MESSAGE_BYTES,
  type EmbedBridge, type EmbedBridgeOptions, type EmbedSender,
} from './embed-bridge.js';
```

- [ ] **Step 4: Tests laufen lassen, sie muessen bestehen**

Run: `cd FORK\apps\server; npx vitest run src/embed-bridge.test.ts; npm run typecheck; npx vitest run`
Expected: alle Tests in `embed-bridge.test.ts` PASS; typecheck Exit 0; die volle Server-Suite ist so gruen wie vor der Aenderung (dazu die Suite vor Step 3 einmal laufen lassen und die Zahl der bestandenen und gescheiterten Tests notieren).

- [ ] **Step 5: Commit**

```powershell
cd C:\Users\User\ClaudeWork\wt-rowboat-embed
git add apps/x/apps/server/src/embed-bridge.ts apps/x/apps/server/src/embed-bridge.test.ts apps/x/apps/server/src/index.ts
$env:ENTIRE_TEST_TTY='0'; git commit -m "feat(server): Einbettungszugang fuer die VibeMind-Multiverse-Ansicht (WebSocket)"
```

---

### Task 3: Einbettungszugang – `app://` ueber HTTP (Fork, `@x/server`)

**Files:**
- Modify: `FORK\apps\server\src\embed-bridge.ts` (Funktion `handleHttp` ersetzen, Import `Readable`, `extractBearer`)
- Modify: `FORK\apps\server\src\embed-bridge.test.ts` (neuer `describe`-Block)

**Interfaces:**
- Consumes: `EmbedBridgeOptions.serveApp(request: Request): Promise<Response>` aus Task 2.
- Produces: Die HTTP-Route `GET|HEAD /app/<host>/<pfad>[?query]` wird als `Request('app://<host>/<pfad>[?query]')` an `serveApp` gereicht.
  - Verlangt wird `Authorization: Bearer <token>` und Host genau `127.0.0.1:<port>`. Ein `Origin`-Header fuehrt zu 403.
  - Der `range`-Header geht durch. Status, Header und Body kommen gestreamt zurueck.

- [ ] **Step 1: Failing tests anhaengen.** Den Import `import { request as httpRequest } from 'node:http'` oben zu den anderen Importen der Testdatei setzen. Am Dateiende anhaengen:

```ts
function get(b: EmbedBridge, path: string, headers: Record<string, string> = {}, method = 'GET') {
  return new Promise<{ status: number; headers: Record<string, string | string[] | undefined>; body: string }>((resolve, reject) => {
    const req = httpRequest({ host: '127.0.0.1', port: b.port, path, method, headers }, (res) => {
      let body = ''
      res.setEncoding('utf8')
      res.on('data', (c: string) => { body += c })
      res.on('end', () => resolve({ status: res.statusCode ?? 0, headers: res.headers, body }))
    })
    req.on('error', reject)
    req.end()
  })
}
const auth = { authorization: `Bearer ${TOKEN}` }

describe('Einbettungszugang: app:// ueber HTTP', () => {
  it('reicht die Anfrage als app://-Request an serveApp und liefert die Antwort', async () => {
    const seen: string[] = []
    const { b } = await bridge({ serveApp: async (r) => { seen.push(r.url); return new Response('<html>ok</html>', { headers: { 'content-type': 'text/html' } }) } })
    const res = await get(b, '/app/-/index.html', auth)
    expect(res.status).toBe(200)
    expect(res.body).toBe('<html>ok</html>')
    expect(res.headers['content-type']).toBe('text/html')
    expect(seen).toEqual(['app://-/index.html'])
  })
  it('ohne oder mit falschem Token: 401, serveApp wird nicht gerufen', async () => {
    const seen: string[] = []
    const { b } = await bridge({ serveApp: async (r) => { seen.push(r.url); return new Response('x') } })
    expect((await get(b, '/app/-/index.html')).status).toBe(401)
    expect((await get(b, '/app/-/index.html', { authorization: 'Bearer falsch' })).status).toBe(401)
    expect(seen).toEqual([])
  })
  it('Range geht durch, 206 und content-range kommen zurueck', async () => {
    let range: string | null = null
    const { b } = await bridge({
      serveApp: async (r) => { range = r.headers.get('range'); return new Response('abc', { status: 206, headers: { 'content-range': 'bytes 0-2/10' } }) },
    })
    const res = await get(b, '/app/workspace/video.mp4', { ...auth, range: 'bytes=0-2' })
    expect(range).toBe('bytes=0-2')
    expect(res.status).toBe(206)
    expect(res.headers['content-range']).toBe('bytes 0-2/10')
    expect(res.body).toBe('abc')
  })
  it('Query und kodierte Pfade kommen genau einmal kodiert an', async () => {
    const seen: string[] = []
    const { b } = await bridge({ serveApp: async (r) => { seen.push(r.url); return new Response('x') } })
    await get(b, '/app/space-blob/o1/s1/h1?thumb=64', auth)
    await get(b, '/app/workspace/Meine%20Notizen/b%C3%A4r.png', auth)
    expect(seen).toEqual(['app://space-blob/o1/s1/h1?thumb=64', 'app://workspace/Meine%20Notizen/b%C3%A4r.png'])
  })
  it('Status von serveApp (404) geht durch', async () => {
    const { b } = await bridge({ serveApp: async () => new Response('Not Found', { status: 404 }) })
    expect((await get(b, '/app/workspace/fehlt.png', auth)).status).toBe(404)
  })
  it('HEAD liefert Header ohne Body', async () => {
    const { b } = await bridge({ serveApp: async () => new Response('abc', { headers: { 'content-type': 'text/plain' } }) })
    const res = await get(b, '/app/-/index.html', auth, 'HEAD')
    expect(res.status).toBe(200)
    expect(res.body).toBe('')
  })
  it('andere Pfade 404, andere Methoden 405, Origin und fremder Host 403', async () => {
    const { b } = await bridge()
    expect((await get(b, '/sonstwas', auth)).status).toBe(404)
    expect((await get(b, '/app/-/index.html', auth, 'POST')).status).toBe(405)
    expect((await get(b, '/app/-/index.html', { ...auth, origin: 'https://evil.example' })).status).toBe(403)
    expect((await get(b, '/app/-/index.html', { ...auth, host: 'localhost' })).status).toBe(403)
  })
  it('Fehler in serveApp: 500, Zugang laeuft weiter', async () => {
    const { b } = await bridge({ serveApp: async () => { throw new Error('kaputt') } })
    expect((await get(b, '/app/-/index.html', auth)).status).toBe(500)
    const c = await loggedIn(b)
    expect(c.welcome.type).toBe('welcome')
  })
})
```

- [ ] **Step 2: Tests laufen lassen, sie muessen scheitern**

Run: `cd FORK\apps\server; npx vitest run src/embed-bridge.test.ts -t "app://"`
Expected: FAIL (404 statt 200 usw.)

- [ ] **Step 3: Implementieren** – Import oben ergaenzen:

```ts
import { Readable } from 'node:stream';
import type { ReadableStream as WebReadableStream } from 'node:stream/web';
import { extractBearer, tokenMatches } from './auth.js';
```

und `handleHttp` ersetzen:

```ts
  function endText(res: ServerResponse, status: number, text: string): void {
    res.writeHead(status, { 'content-type': 'text/plain; charset=utf-8' });
    res.end(text);
  }

  // app:// ueber HTTP: dieselben Inhalte, die die App ueber ihr Protokoll liefert (Oberflaeche,
  // Workspace-Dateien, Vorschauen, Space-Blobs/-Dokumente), fuer die eingebettete Ansicht.
  async function handleHttp(req: IncomingMessage, res: ServerResponse): Promise<void> {
    try {
      if (!hostOk(req) || fromBrowser(req)) return endText(res, 403, 'Forbidden');
      const url = new URL(req.url ?? '/', `http://127.0.0.1:${port}`);
      if (!url.pathname.startsWith('/app/')) return endText(res, 404, 'Not Found');
      if (req.method !== 'GET' && req.method !== 'HEAD') return endText(res, 405, 'Method Not Allowed');
      const presented = extractBearer(req.headers.authorization);
      if (!presented || !tokenMatches(presented, opts.token)) return endText(res, 401, 'Unauthorized');
      const rest = url.pathname.slice('/app/'.length);
      const slash = rest.indexOf('/');
      const host = slash === -1 ? rest : rest.slice(0, slash);
      const pathPart = slash === -1 ? '/' : rest.slice(slash);
      if (!host) return endText(res, 404, 'Not Found');
      const headers = new Headers();
      if (typeof req.headers.range === 'string') headers.set('range', req.headers.range);
      const response = await opts.serveApp(new Request(`app://${host}${pathPart}${url.search}`, { method: req.method, headers }));
      res.writeHead(response.status, Object.fromEntries(response.headers));
      if (req.method === 'HEAD' || !response.body) {
        await response.body?.cancel();
        res.end();
        return;
      }
      Readable.fromWeb(response.body as WebReadableStream<Uint8Array>).pipe(res);
    } catch (err) {
      log(`[embed] app request failed: ${errorMessage(err)}`);
      if (!res.headersSent) endText(res, 500, 'Internal Error');
      else res.destroy();
    }
  }
```

- [ ] **Step 4: Tests laufen lassen, sie muessen bestehen**

Run: `cd FORK\apps\server; npx vitest run src/embed-bridge.test.ts; npm run typecheck`
Expected: alle PASS, typecheck Exit 0.

- [ ] **Step 5: Commit**

```powershell
cd C:\Users\User\ClaudeWork\wt-rowboat-embed
git add apps/x/apps/server/src/embed-bridge.ts apps/x/apps/server/src/embed-bridge.test.ts
$env:ENTIRE_TEST_TTY='0'; git commit -m "feat(server): app://-Inhalte ueber den Einbettungszugang (Token, Range, Streaming)"
```

---

### Task 4: Rowboat-Hauptprozess an den Einbettungszugang anschliessen (Fork, `apps/main`)

`apps/main` hat keinen Unit-Testlauf. Geprueft wird deshalb ueber Typecheck, Build, eine Quellpruefung per Grep und eine echte Rauchprobe mit gestartetem Electron in einem Sandbox-`WorkDir`.

**Files:**
- Create: `FORK\apps\main\src\embed-sink.ts`
- Create: `FORK\apps\main\src\embed-host.ts`
- Create: `FORK\scripts\embed-smoke.mjs`
- Modify: `FORK\apps\main\src\ipc.ts`:
  - `:394-415` registerIpcHandlers
  - `:589` broadcastToWindows
  - Fenster-Lookups `:1055 :1196 :1231 :1538 :1572 :2345 :2489 :2517 :2590`
  - power `:1926-1930`
- Modify: `FORK\apps\main\src\spaces\ipc.ts` (`:149-155` broadcastSpacesEvent, `:338 :348 :364`)
- Modify: `FORK\apps\main\src\main.ts`:
  - `:105` Single-Instance
  - `:151` second-instance
  - `:224-338` app://-Handler
  - `:590` loadURL
  - `:671` nach setupIpcHandlers
  - before-quit

**Interfaces:**
- Consumes:
  - `startEmbedBridge`, `generateEmbedToken`, `isEmbedSender`, `EmbedBridge` aus `@x/server` (Task 2/3).
  - `isEmbedMirrored`, `isEmbedUnsupportedInvoke` aus `@x/shared/dist/embed-channels.js` (Task 1).
- Produces:
  - `invokeIpcChannel(channel: string, event: Electron.IpcMainInvokeEvent, rawArgs: unknown): Promise<unknown>` und `listInvokeChannels(): string[]` (ipc.ts).
  - `mirrorToEmbed(channel, payload)`, `setEmbedSink(fn | null)`, `senderWindow(sender): BrowserWindow | null` (embed-sink.ts).
  - `embedRequested(argv?, env?)`, `startEmbedHost({showApp, preloadPath, serveApp})`, `stopEmbedHost()` (embed-host.ts).
  - `embed.json` = `{v:1, port, token, pid, preloadPath}` in `WorkDir`.

- [ ] **Step 1: `embed-sink.ts` anlegen**

```ts
import { BrowserWindow } from 'electron';
import { isEmbedSender } from '@x/server';
import { isEmbedMirrored } from '@x/shared/dist/embed-channels.js';

// VibeMind-Einbettung (Spec vibemind-os 2026-10-17-rowboat-einbettung). Ohne --embed bleibt der
// sink leer und alles verhaelt sich wie bisher.
let sink: ((channel: string, payload: unknown) => void) | null = null;

export function setEmbedSink(next: ((channel: string, payload: unknown) => void) | null): void {
  sink = next;
}

/** An alle Fenster Gesendetes zusaetzlich an jede Einbettungs-Verbindung (nur EMBED_MIRRORED_PUSH). */
export function mirrorToEmbed(channel: string, payload: unknown): void {
  if (sink && isEmbedMirrored(channel)) sink(channel, payload);
}

/** Fenster des Absenders; null fuer die Einbettung — Dialoge oeffnen dann ohne Elternfenster. */
export function senderWindow(sender: Electron.WebContents): BrowserWindow | null {
  return isEmbedSender(sender) ? null : BrowserWindow.fromWebContents(sender);
}
```

- [ ] **Step 2: Handler-Tabelle in `ipc.ts` herausziehen.** `registerIpcHandlers` (`:394-415`) ersetzen durch:

```ts
// Die eine Handler-Tabelle (VibeMind-Einbettung, Spec 2026-10-17): ipcMain und der
// Einbettungszugang rufen dieselbe Funktion, also dieselbe Zod-Pruefung und dieselben Handler.
const invokeTable = new Map<InvokeChannels, InvokeHandler<InvokeChannels>>();

export function listInvokeChannels(): InvokeChannels[] {
  return [...invokeTable.keys()];
}

export async function invokeIpcChannel(
  channel: string,
  event: Electron.IpcMainInvokeEvent,
  rawArgs: unknown,
): Promise<unknown> {
  const handler = invokeTable.get(channel as InvokeChannels);
  if (!handler) throw new Error(`No handler registered for '${channel}'`);
  const typed = channel as InvokeChannels;
  // Validate request payload
  const args = ipc.validateRequest(typed, rawArgs);
  // Strangler-fig: channels migrated to rowboat-server cross localhost HTTP
  // instead of calling their in-process handler (which stays in the map as
  // the ROWBOAT_FORWARD_MIGRATED=0 kill switch).
  const result = shouldForwardChannel(typed) ? await forwardRpc(typed, args) : await handler(event, args);
  // Validate response payload
  return ipc.validateResponse(typed, result);
}

export function registerIpcHandlers(handlers: InvokeHandlers) {
  for (const [channel, handler] of Object.entries(handlers) as [
    InvokeChannels,
    InvokeHandler<InvokeChannels>
  ][]) {
    invokeTable.set(channel, handler);
    ipcMain.handle(channel, (event, rawArgs) => invokeIpcChannel(channel, event, rawArgs));
  }
}
```

Ganz oben in `ipc.ts` zu den Importen: `import { mirrorToEmbed, senderWindow } from './embed-sink.js';`

- [ ] **Step 3: Push spiegeln.**
  - In `broadcastToWindows` (`:589`) nach der Schleife `mirrorToEmbed(channel, payload);` einfuegen.
  - In `power:setCaffeinate` (`:1926-1930`) nach der Fenster-Schleife `mirrorToEmbed('power:caffeinateChanged', { enabled });` einfuegen.
  - In `spaces/ipc.ts` `broadcastSpacesEvent` (`:149-155`) nach der Schleife `mirrorToEmbed('spaces:events', event);` einfuegen.
  - Import in `spaces/ipc.ts`: `import { mirrorToEmbed, senderWindow } from '../embed-sink.js';`

- [ ] **Step 4: Fenster-Lookups auf `senderWindow` umstellen.**
  - Jede Stelle `BrowserWindow.fromWebContents(event.sender)` bzw. `(_event.sender)` in `ipc.ts` (`:1055 :1196 :1231 :1538 :1572 :2489 :2517 :2590`) und `spaces/ipc.ts` (`:338 :348 :364`) wird zu `senderWindow(event.sender)`.
  - Wo danach `win!` an einen Dialog geht (`:1538 :1572 :2489 :2517 :2590`, bei `:1196` pruefen), auf den Muster-Fall von `:1231` umstellen: Optionen in eine typisierte Konstante, dann `win ? dialog.X(win, options) : dialog.X(options)`. Beispiel fuer `:1538`:

```ts
      const win = senderWindow(event.sender);
      const options: Electron.SaveDialogOptions = {
        defaultPath: `${args.sessionId}.jsonl.log`,
        filters: [
          { name: 'Chat Log', extensions: ['log'] },
          // … die vorhandenen Filter unveraendert uebernehmen
        ],
      };
      const result = win ? await dialog.showSaveDialog(win, options) : await dialog.showSaveDialog(options);
```

  Fuer `showOpenDialog` heisst der Typ `Electron.OpenDialogOptions`.
  - `apps:publish` (`:2345-2348`): Fortschritt geht an den Absender selbst, nicht mehr ueber das Fenster:

```ts
    'apps:publish': async (event, args) => {
      const sender = event.sender;
      const result = await appsPublisher.publishApp(args.folder, (step, detail) => {
        if (!sender.isDestroyed()) sender.send('apps:progress', { folder: args.folder, step, detail });
      });
```

  Fuer echte Fenster ist `event.sender` genau `win.webContents`, das Verhalten bleibt also gleich.

- [ ] **Step 5: `embed-host.ts` anlegen**

```ts
import fsSync from 'node:fs';
import fs from 'node:fs/promises';
import path from 'node:path';
import type { IpcMainInvokeEvent } from 'electron';
import { WorkDir } from '@x/core/dist/config/config.js';
import { generateEmbedToken, startEmbedBridge, type EmbedBridge } from '@x/server';
import { isEmbedUnsupportedInvoke } from '@x/shared/dist/embed-channels.js';
import { invokeIpcChannel, listInvokeChannels } from './ipc.js';
import { setEmbedSink } from './embed-sink.js';

// VibeMind-Einbettung (Spec vibemind-os 2026-10-17-rowboat-einbettung): startet den lokalen
// Zugang und schreibt embed.json, ueber die sich die Multiverse-Ansicht der voice-App verbindet.

export const EMBED_INFO_FILE = 'embed.json';

export function embedRequested(
  argv: readonly string[] = process.argv,
  env: NodeJS.ProcessEnv = process.env,
): boolean {
  return argv.includes('--embed') || env.ROWBOAT_EMBED === '1';
}

let bridge: EmbedBridge | null = null;

export async function startEmbedHost(options: {
  showApp: () => void;
  preloadPath: string;
  serveApp: (request: Request) => Promise<Response>;
}): Promise<void> {
  if (bridge) return;
  const token = generateEmbedToken();
  const live = await startEmbedBridge({
    token,
    invoke: (channel, sender, args) =>
      invokeIpcChannel(channel, { sender } as unknown as IpcMainInvokeEvent, args),
    invokeChannels: listInvokeChannels,
    isUnsupported: isEmbedUnsupportedInvoke,
    serveApp: options.serveApp,
    onShowApp: options.showApp,
    log: (message) => console.warn(message),
  });
  bridge = live;
  setEmbedSink((channel, payload) => live.broadcast(channel, payload));
  const file = path.join(WorkDir, EMBED_INFO_FILE);
  const tmp = `${file}.${process.pid}.tmp`;
  const info = { v: 1, port: live.port, token, pid: process.pid, preloadPath: options.preloadPath };
  await fs.writeFile(tmp, JSON.stringify(info), { mode: 0o600 });
  await fs.rename(tmp, file);
  console.log(`[embed] Einbettungszugang auf 127.0.0.1:${live.port} (${EMBED_INFO_FILE})`);
}

/** Beim Beenden: embed.json nur loeschen, wenn sie von diesem Prozess stammt. */
export function stopEmbedHost(): void {
  if (!bridge) return;
  setEmbedSink(null);
  const file = path.join(WorkDir, EMBED_INFO_FILE);
  try {
    const info = JSON.parse(fsSync.readFileSync(file, 'utf8')) as { pid?: unknown };
    if (info.pid === process.pid) fsSync.rmSync(file, { force: true });
  } catch {
    // weg oder fremd: nichts zu tun
  }
  void bridge.close();
  bridge = null;
}
```

- [ ] **Step 6: `main.ts` anpassen.**
  1. Import zu den anderen: `import { embedRequested, startEmbedHost, stopEmbedHost } from "./embed-host.js";`
  2. **Single-Instance** (`:105`):

```ts
// VibeMind-Einbettung: im Einbettungsmodus (und wenn start_desktop.py es verlangt) gilt die Sperre
// auch ungebaut — nie zwei Kerne auf demselben WorkDir (die Sperre gilt pro userData-Ordner).
const singleInstance = app.isPackaged || embedRequested() || process.env.ROWBOAT_SINGLE_INSTANCE === "1";
if (singleInstance && !app.requestSingleInstanceLock()) {
```

     Den Rest des Blocks unveraendert lassen.
  3. **second-instance** (`:151`):

```ts
app.on("second-instance", (_event, argv) => {
  const url = extractDeepLinkFromArgv(argv);
  if (url) dispatchUrl(url);
  // Einbettung: ein zweiter Start holt das versteckte Fenster der laufenden Instanz nach vorn.
  else if (embedRequested()) showApp();
});
```

  4. **app://-Handler herausziehen** (`:224-338`): Den Rumpf des Callbacks von `protocol.handle("app", async (request) => { … })` unveraendert in eine Funktion verschieben.

```ts
async function handleAppRequest(request: Request): Promise<Response> {
  const url = new URL(request.url);
  // … der bisherige Rumpf, Zeile fuer Zeile unveraendert …
}

function registerAppProtocol() {
  protocol.handle("app", handleAppRequest);
}
```

  5. **loadURL** in `createWindow`: `if (app.isPackaged) {` wird zu `if (app.isPackaged || embedRequested()) {`. Kommentar dazu: `// Einbettung: gebaute Oberflaeche ueber app://, kein Vite-Server.`
  6. Direkt nach `setupBrowserExtensions();` (`:673`):

```ts
  // VibeMind-Einbettung (Spec 2026-10-17): nur mit --embed / ROWBOAT_EMBED=1.
  if (embedRequested()) {
    startEmbedHost({ showApp, preloadPath, serveApp: handleAppRequest }).catch((error) => {
      console.error("[embed] Einbettungszugang startet nicht:", error);
    });
  }
```

  7. In `app.on("before-quit", …)` als erste Zeile: `stopEmbedHost();`

- [ ] **Step 7: Quellpruefung, Typecheck und Build**

Run:

```powershell
cd FORK
rg -n "BrowserWindow\.fromWebContents\(" apps/main/src
rg -n "win!" apps/main/src/ipc.ts apps/main/src/spaces/ipc.ts
npm run deps
cd apps/main; npx tsc --noEmit -p tsconfig.json; npm run build
cd ../renderer; npm run build
```

Erwartet:
- Das erste `rg` findet nur `apps/main/src/embed-sink.ts`.
- Das zweite findet nichts.
- `tsc` Exit 0, beide Builds Exit 0; es entstehen `apps/main/.package/dist/main.cjs` und `apps/renderer/dist/index.html`.
- **Nicht** `npx --prefix … tsc` verwenden (prueft nichts, s. Memory).

- [ ] **Step 8: Rauchprobe-Skript anlegen** – `FORK\scripts\embed-smoke.mjs`:

```js
#!/usr/bin/env node
// Rauchprobe fuer den Einbettungszugang (Spec vibemind-os 2026-10-17-rowboat-einbettung):
//   node scripts/embed-smoke.mjs <workdir>
// Liest <workdir>/embed.json und prueft Anmeldung, Aufruf, Ablehnung und app://. Gibt das Token nie aus.
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(new URL('../apps/server/package.json', import.meta.url));
const { WebSocket } = require('ws');

const workdir = process.argv[2];
if (!workdir) {
  console.error('Aufruf: node scripts/embed-smoke.mjs <workdir>');
  process.exit(2);
}
const info = JSON.parse(fs.readFileSync(path.join(workdir, 'embed.json'), 'utf8'));
const base = `127.0.0.1:${info.port}`;
let failed = 0;
const check = (ok, what) => {
  console.log(`${ok ? 'OK  ' : 'FAIL'} ${what}`);
  if (!ok) failed += 1;
};

function session(token) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(`ws://${base}/embed`);
    const inbox = [];
    const waiters = [];
    const closed = new Promise((r) => ws.on('close', (code) => r(code)));
    ws.on('message', (d) => {
      const m = JSON.parse(String(d));
      const w = waiters.shift();
      if (w) w(m); else inbox.push(m);
    });
    ws.on('error', reject);
    ws.on('open', () => {
      ws.send(JSON.stringify({ type: 'hello', v: 1, token }));
      resolve({ ws, closed, next: () => (inbox.length ? Promise.resolve(inbox.shift()) : new Promise((r) => waiters.push(r))) });
    });
  });
}

const bad = await session('falsch');
check((await bad.closed) === 4401, 'falsches Token wird abgewiesen (4401)');

const s = await session(info.token);
const welcome = await s.next();
check(welcome.type === 'welcome' && welcome.invokeChannels.length > 400, `welcome mit ${welcome.invokeChannels?.length} Kanaelen`);
s.ws.send(JSON.stringify({ type: 'invoke', id: 1, channel: 'app:getVersions', args: null }));
const versions = await s.next();
check(versions.ok === true && typeof versions.result?.electron === 'string', 'app:getVersions antwortet');
s.ws.send(JSON.stringify({ type: 'invoke', id: 2, channel: 'browser:getState', args: null }));
const unsupported = await s.next();
check(unsupported.ok === false && unsupported.error?.code === 'embed_unsupported', 'browser:getState ist embed_unsupported');
s.ws.close();

const page = await fetch(`http://${base}/app/-/index.html`, { headers: { authorization: `Bearer ${info.token}` } });
const html = await page.text();
check(page.status === 200 && html.includes('<html'), 'GET /app/-/index.html liefert die Oberflaeche');
check((await fetch(`http://${base}/app/-/index.html`)).status === 401, 'ohne Token 401');
check(info.pid > 0 && typeof info.preloadPath === 'string' && fs.existsSync(info.preloadPath), 'embed.json nennt PID und vorhandenen Preload');
process.exit(failed ? 1 : 0);
```

- [ ] **Step 9: Rauchprobe gegen ein Sandbox-WorkDir ausfuehren.** Das echte `~/.rowboat` wird nicht angefasst. Das eigene `ROWBOAT_WORKDIR` sorgt fuer einen eigenen userData-Ordner und damit eine eigene Sperre.

```powershell
$wd = Join-Path $env:TEMP ('rowboat-embed-smoke-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $wd | Out-Null
cd FORK\apps\main
$env:ELECTRON_RUN_AS_NODE = $null; $env:ROWBOAT_WORKDIR = $wd; $env:ROWBOAT_EMBED = '1'
$p = Start-Process -FilePath .\node_modules\.bin\electron.cmd -ArgumentList '.', '--hidden', '--embed' -PassThru -WindowStyle Hidden
# auf embed.json warten (max. 90 s)
$deadline = (Get-Date).AddSeconds(90); while (-not (Test-Path "$wd\embed.json") -and (Get-Date) -lt $deadline) { Start-Sleep 2 }
node ..\..\scripts\embed-smoke.mjs $wd
# zweiter Start mit demselben WorkDir muss sofort enden (Single-Instance), embed.json bleibt die erste
$p2 = Start-Process -FilePath .\node_modules\.bin\electron.cmd -ArgumentList '.', '--hidden', '--embed' -PassThru -WindowStyle Hidden -Wait
(Get-Content "$wd\embed.json" | ConvertFrom-Json).pid
```

Erwartet:
- 7 Zeilen `OK`, Exit 0.
- Der zweite Start endet von selbst, die PID in `embed.json` ist unveraendert die erste.
- Danach Rowboat beenden: Die Prozessbaum-PID gezielt ueber `$p.Id` mit `taskkill /PID $($p.Id) /T /F` beenden, **kein** Namens- oder Kommandozeilen-Muster (s. Memory „Kill-Muster trifft eigene Shell“). Dann `$wd` loeschen.
- Ergebnis und Ausgabe in den Report.

Hinweis: Nach `taskkill /F` laeuft `before-quit` nicht, `embed.json` bleibt also liegen. Genau diesen Fall faengt die voice-Seite per PID-Pruefung ab (Review Focus 1).

- [ ] **Step 10: Commit**

```powershell
cd C:\Users\User\ClaudeWork\wt-rowboat-embed
git add apps/x/apps/main/src/embed-sink.ts apps/x/apps/main/src/embed-host.ts apps/x/apps/main/src/ipc.ts apps/x/apps/main/src/spaces/ipc.ts apps/x/apps/main/src/main.ts apps/x/scripts/embed-smoke.mjs
$env:ENTIRE_TEST_TTY='0'; git commit -m "feat(main): Einbettungsmodus --embed fuer die VibeMind-Multiverse-Ansicht"
git push -u origin feat/embed-bridge
```

---

### Task 5: `start_desktop.py` baut und startet die Einbettung (vibemind-os)

**Files:**
- Modify: `OS\spaces\rowboat\scripts\start_desktop.py`
- Modify: `OS\spaces\rowboat\tests\test_start_desktop.py`

**Interfaces:**
- Produces:
  - CLI: `--build-embed` baut und beendet sich; `--embed` startet die gebaute App versteckt.
  - Funktionen: `embed_build_steps(lay)`, `missing_embed(lay, windows)`, `electron_bin(lay, windows)` sowie `jobs(lay, workdir, windows, embed=False)`.
  - Umgebung: In jedem Modus setzt das Skript `ROWBOAT_SINGLE_INSTANCE=1`. Im Einbettungsmodus kommt `ROWBOAT_EMBED=1` dazu, `ELECTRON_RUN_AS_NODE` faellt weg.
- Consumes: Fork-Schalter `--embed` / `ROWBOAT_EMBED` / `ROWBOAT_SINGLE_INSTANCE` (Task 4).

- [ ] **Step 1: Failing tests anhaengen** (am Ende von `test_start_desktop.py`):

```python
from start_desktop import electron_bin, embed_build_steps, missing_embed  # noqa: E402


def _embed_prereqs(tmp_path, windows):
    lay = _prereqs(tmp_path, windows)
    for p in (lay.app / "apps" / "renderer" / "dist" / "index.html",
              lay.app / "apps" / "main" / ".package" / "dist" / "main.cjs",
              lay.app / "apps" / "preload" / "dist" / "preload.js",
              electron_bin(lay, windows)):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("")
    return lay


def test_embed_build_builds_deps_then_renderer_then_main(tmp_path):
    lay = layout(tmp_path)
    assert embed_build_steps(lay) == [
        (["npm", "run", "deps"], lay.app),
        (["npm", "run", "build"], lay.app / "apps" / "renderer"),
        (["npm", "run", "build"], lay.app / "apps" / "main")]


def test_embed_job_starts_the_built_electron_hidden_in_apps_main(tmp_path):
    lay = layout(tmp_path)
    (name, cmd, cwd), (map_name, _, _) = jobs(lay, tmp_path, windows=True, embed=True)
    assert name == "Rowboat" and map_name == "vault map"
    assert cmd == [str(lay.app / "apps" / "main" / "node_modules" / ".bin" / "electron.cmd"), ".", "--hidden", "--embed"]
    assert cwd == lay.app / "apps" / "main"


def test_missing_embed_lists_the_builds_and_electron(tmp_path):
    lay = _prereqs(tmp_path, windows=True)
    assert len(missing_embed(lay, windows=True)) == 4
    assert missing_embed(_embed_prereqs(tmp_path / "b", windows=True), windows=True) == []


def test_embed_start_sets_embed_and_single_instance_and_drops_run_as_node(tmp_path, monkeypatch):
    lay = _embed_prereqs(tmp_path, windows=True)
    seen = {}
    monkeypatch.setattr(start_desktop.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))

    def fake_popen(cmd, cwd=None, env=None, shell=False, **k):
        seen["map" if cwd == lay.schema else "rowboat"] = (cmd, env, shell)
        return FakeProc(exit_code=0)

    monkeypatch.setattr(start_desktop.subprocess, "Popen", fake_popen)
    monkeypatch.setenv("ELECTRON_RUN_AS_NODE", "1")
    assert start_desktop.start(lay, tmp_path, windows=True, embed=True) == 0
    cmd, env, shell = seen["rowboat"]
    assert cmd[-2:] == ["--hidden", "--embed"]
    assert shell is True   # electron.cmd braucht unter Windows die Shell
    assert env["ROWBOAT_EMBED"] == "1" and env["ROWBOAT_SINGLE_INSTANCE"] == "1"
    assert "ELECTRON_RUN_AS_NODE" not in env


def test_embed_start_refuses_without_builds(tmp_path, monkeypatch):
    lay = _prereqs(tmp_path, windows=True)
    started = []
    monkeypatch.setattr(start_desktop.subprocess, "Popen", lambda *a, **k: started.append(a))
    monkeypatch.setattr(start_desktop.subprocess, "run", lambda *a, **k: started.append(a))
    assert start_desktop.start(lay, tmp_path, windows=True, embed=True) == 2
    assert started == []


def test_dev_start_also_asks_for_single_instance(tmp_path, monkeypatch):
    lay = _prereqs(tmp_path, windows=True)
    seen = {}
    monkeypatch.setattr(start_desktop.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))

    def fake_popen(cmd, cwd=None, env=None, **k):
        seen[str(cwd)] = env
        return FakeProc(exit_code=0)

    monkeypatch.setattr(start_desktop.subprocess, "Popen", fake_popen)
    start_desktop.start(lay, tmp_path, windows=True)
    assert seen[str(lay.app)]["ROWBOAT_SINGLE_INSTANCE"] == "1"
    assert "ROWBOAT_EMBED" not in seen[str(lay.app)]


def test_build_embed_runs_the_steps_in_order_and_stops_at_the_first_failure(tmp_path, monkeypatch):
    lay = layout(tmp_path)
    runs = []

    def fake_run(cmd, cwd=None, shell=False, **k):
        runs.append((cmd, cwd))
        return SimpleNamespace(returncode=1 if cwd == lay.app / "apps" / "renderer" else 0)

    monkeypatch.setattr(start_desktop.subprocess, "run", fake_run)
    assert start_desktop.build_embed(lay, windows=True) == 1
    assert [c for c, _ in runs] == [["npm", "run", "deps"], ["npm", "run", "build"]]
```

- [ ] **Step 2: Tests laufen lassen, sie muessen scheitern**

Run: `cd OS; python -m pytest spaces/rowboat/tests/test_start_desktop.py -q`
Expected: FAIL (`ImportError: cannot import name 'electron_bin'`)

- [ ] **Step 3: Implementieren.**
  - Docstring oben um zwei Zeilen ergaenzen:

```
    python spaces/rowboat/scripts/start_desktop.py --build-embed   # Einbettung bauen (VibeMind-Multiverse), nach jedem Sync
    python spaces/rowboat/scripts/start_desktop.py --embed         # gebaute App versteckt im Einbettungsmodus + vault map
```

  - Neue Funktionen nach `missing`:

```python
def electron_bin(lay: Layout, windows: bool = IS_WINDOWS) -> Path:
    return lay.app / "apps" / "main" / "node_modules" / ".bin" / ("electron.cmd" if windows else "electron")


def embed_build_steps(lay: Layout) -> list[tuple[list[str], Path]]:
    """Einbettung (Spec vibemind-os 2026-10-17-rowboat-einbettung): gebaute Oberflaeche statt Vite."""
    return [(["npm", "run", "deps"], lay.app),
            (["npm", "run", "build"], lay.app / "apps" / "renderer"),
            (["npm", "run", "build"], lay.app / "apps" / "main")]


def missing_embed(lay: Layout, windows: bool = IS_WINDOWS) -> list[str]:
    out = missing(lay, windows)
    for path, what in ((lay.app / "apps" / "renderer" / "dist" / "index.html", "Rowboat renderer build"),
                       (lay.app / "apps" / "main" / ".package" / "dist" / "main.cjs", "Rowboat main build"),
                       (lay.app / "apps" / "preload" / "dist" / "preload.js", "Rowboat preload build"),
                       (electron_bin(lay, windows), "Electron")):
        if not path.is_file():
            out.append(f"{path} ({what})")
    return out
```

  - `jobs` ersetzen:

```python
def jobs(lay: Layout, workdir: Path, windows: bool = IS_WINDOWS,
         embed: bool = False) -> list[tuple[str, list[str], Path]]:
    py = venv_python(lay.venv, windows)
    if embed:
        rowboat = ("Rowboat", [str(electron_bin(lay, windows)), ".", "--hidden", "--embed"], lay.app / "apps" / "main")
    else:
        rowboat = ("Rowboat", ["npm", "run", "dev"], lay.app)
    return [rowboat, ("vault map", map_command(py, workdir, MAP_SECONDS), lay.schema)]
```

  - `_shell` ersetzen:

```python
def _shell(cmd: list[str], windows: bool) -> bool:
    return windows and (cmd[0] in ("npm", "pnpm") or cmd[0].lower().endswith(".cmd"))  # .cmd launchers on Windows
```

  - `install` auf einen gemeinsamen Schrittlauf umstellen und `build_embed` ergaenzen:

```python
def _run_steps(steps: list[tuple[list[str], Path]], windows: bool) -> int:
    for cmd, cwd in steps:
        log.info("running in %s: %s", cwd, " ".join(cmd))
        try:
            code = subprocess.run(cmd, cwd=cwd, shell=_shell(cmd, windows)).returncode
        except FileNotFoundError:
            tool, hint = (" ".join(cmd[:2]), " (install Python 3.12 with the py launcher)") if cmd[0] == "py" \
                else (cmd[0], "")
            log.error("install step failed: %s not found%s. Command: %s", tool, hint, " ".join(cmd))
            return 1
        if code != 0:
            log.error("install step failed: %s", " ".join(cmd))
            return 1
    return 0


def install(lay: Layout, windows: bool = IS_WINDOWS) -> int:
    return _run_steps(install_steps(lay, windows), windows)


def build_embed(lay: Layout, windows: bool = IS_WINDOWS) -> int:
    return _run_steps(embed_build_steps(lay), windows)
```

  - In `start`:
    - Signatur: `def start(lay: Layout, workdir: Path, windows: bool = IS_WINDOWS, env_file: Path | None = None, embed: bool = False) -> int:`
    - Pruefung: `gaps = missing_embed(lay, windows) if embed else missing(lay, windows)`. Die Fehlermeldung nennt bei `embed` „run start_desktop.py --install and --build-embed first“.
    - Umgebung:

```python
    # Single-Instance auch ungebaut (Fork, Spec 2026-10-17): ein zweiter Start holt die laufende Instanz nach vorn.
    env = {**base_env, "ROWBOAT_WORKDIR": str(workdir), "ROWBOAT_SINGLE_INSTANCE": "1"}
    if embed:
        env.pop("ELECTRON_RUN_AS_NODE", None)
        env["ROWBOAT_EMBED"] = "1"
```

    - Den `jobs`-Aufruf auf `jobs(lay, workdir, windows, embed=embed)` umstellen.
    - Die Info-Zeile bei `embed`: `"Rowboat laeuft versteckt im Einbettungsmodus; die Multiverse-Ansicht verbindet sich ueber %s. Ctrl+C stoppt alles."` mit `workdir / "embed.json"`.
  - In `main`:

```python
    ap.add_argument("--build-embed", action="store_true", help="Einbettung bauen (gebaute Oberflaeche fuer die Multiverse-Ansicht)")
    ap.add_argument("--embed", action="store_true", help="gebaute App versteckt im Einbettungsmodus starten")
    ...
    if args.install:
        return install(lay)
    if args.build_embed:
        return build_embed(lay)
    return start(lay, args.workdir.expanduser().resolve(), env_file=args.env_file, embed=args.embed)
```

- [ ] **Step 4: Tests laufen lassen, sie muessen bestehen**

Run: `cd OS; python -m pytest spaces/rowboat/tests -q`
Expected: alle PASS (die bisherigen unveraendert, die 7 neuen gruen).

- [ ] **Step 5: Commit (lokal auf master, Push erst in Task 10)**

```powershell
cd OS
git status --short
git add spaces/rowboat/scripts/start_desktop.py spaces/rowboat/tests/test_start_desktop.py
git commit -m "feat(rowboat): start_desktop.py --build-embed und --embed fuer die Multiverse-Einbettung"
```

---

### Task 6: Verbindung der voice-App zu Rowboat (voice, `rowboat-embed-client.js`)

**Files:**
- Create: `VOICE\rowboat-embed-client.js`
- Create: `VOICE\rowboat-embed-client.test.js`
- Modify: `VOICE\package.json`:
  - `dependencies`: `"ws": "^8.20.0"`
  - `test:unit`: `rowboat-embed-client.test.js` anhaengen

**Interfaces:**
- Consumes: Protokoll aus Task 2 und `embed.json` aus Task 4.
- Produces (`module.exports = { RowboatEmbedClient, readEmbedInfo, defaultEmbedFile, NOT_RUNNING }`):
  - `new RowboatEmbedClient({ embedFile?, WebSocket?, readFileSync?, isPidAlive?, backoffMs?, helloTimeoutMs?, logger? })`
  - Methoden: `start()`, `stop()`, `invoke(channel, args): Promise<unknown>`, `showApp(): boolean`
  - Felder: `connected: boolean` (Getter), `info: {port, token, pid, preloadPath} | null`, `invokeChannels: string[]`, `reason: string`
  - Events: `'connected' ({invokeChannels, preloadPath})`, `'disconnected' (reason)`, `'push' (channel, payload)`
  - `NOT_RUNNING = 'Rowboat laeuft nicht (start_desktop.py --embed)'`

- [ ] **Step 1: Failing tests schreiben** – `rowboat-embed-client.test.js`:

```js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const { WebSocketServer } = require('ws');

const { RowboatEmbedClient, NOT_RUNNING } = require('./rowboat-embed-client');

const TOKEN = 'tok-geheim-123';

function tmpEmbedFile() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rb-embed-'));
  return path.join(dir, 'embed.json');
}
function writeInfo(file, port, extra = {}) {
  fs.writeFileSync(file, JSON.stringify({ v: 1, port, token: TOKEN, pid: process.pid, preloadPath: 'C:/rb/preload.js', ...extra }));
}
/** Nachgebauter Rowboat-Zugang: antwortet auf hello mit welcome, auf invoke per handler. */
function fakeRowboat(handler = (m) => ({ type: 'result', id: m.id, ok: true, result: { echo: m.args } })) {
  return new Promise((resolve) => {
    const wss = new WebSocketServer({ host: '127.0.0.1', port: 0 });
    const seen = [];
    const sockets = new Set();
    wss.on('connection', (ws) => {
      sockets.add(ws);
      ws.on('message', (d) => {
        const m = JSON.parse(String(d));
        seen.push(m);
        if (m.type === 'hello') {
          if (m.token !== TOKEN) return ws.close(4401);
          return ws.send(JSON.stringify({ type: 'welcome', v: 1, invokeChannels: ['workspace:exists', 'browser:setVisible'] }));
        }
        if (m.type === 'invoke') {
          const reply = handler(m, ws);
          if (reply) ws.send(JSON.stringify(reply));
        }
      });
    });
    wss.on('listening', () => resolve({
      wss, seen, port: wss.address().port,
      push: (channel, payload) => { for (const s of sockets) s.send(JSON.stringify({ type: 'push', channel, payload })); },
      kill: () => new Promise((r) => { for (const s of sockets) s.terminate(); wss.close(() => r()); }),
    }));
  });
}
function once(emitter, name) { return new Promise((r) => emitter.once(name, (...a) => r(a))); }
function quiet() { const lines = []; const rec = (...a) => lines.push(a.join(' ')); return { lines, info: rec, warn: rec, error: rec, log: rec }; }

test('ohne embed.json: getrennt mit Hinweis auf start_desktop.py --embed, verbindet sich, sobald die Datei da ist', async () => {
  const file = tmpEmbedFile();
  const rb = await fakeRowboat();
  const c = new RowboatEmbedClient({ embedFile: file, backoffMs: [20], logger: quiet() });
  try {
    c.start();
    await new Promise((r) => setTimeout(r, 30));
    assert.equal(c.connected, false);
    assert.equal(c.reason, NOT_RUNNING);
    writeInfo(file, rb.port);
    const [welcome] = await once(c, 'connected');
    assert.deepEqual(welcome, { invokeChannels: ['workspace:exists', 'browser:setVisible'], preloadPath: 'C:/rb/preload.js' });
    assert.equal(c.connected, true);
    assert.deepEqual(rb.seen[0], { type: 'hello', v: 1, token: TOKEN });
  } finally { c.stop(); await rb.kill(); }
});

test('tote PID in embed.json: getrennt, kein Verbindungsversuch', async () => {
  const file = tmpEmbedFile();
  const rb = await fakeRowboat();
  writeInfo(file, rb.port);
  const c = new RowboatEmbedClient({ embedFile: file, backoffMs: [20], isPidAlive: () => false, logger: quiet() });
  try {
    c.start();
    await new Promise((r) => setTimeout(r, 60));
    assert.equal(c.connected, false);
    assert.equal(c.reason, NOT_RUNNING);
    assert.equal(rb.seen.length, 0);
  } finally { c.stop(); await rb.kill(); }
});

test('lebende PID, toter Port: getrennt, versucht es weiter, haengt nicht', async () => {
  const file = tmpEmbedFile();
  const rb = await fakeRowboat();
  const port = rb.port;
  await rb.kill();
  writeInfo(file, port);
  const c = new RowboatEmbedClient({ embedFile: file, backoffMs: [20], logger: quiet() });
  try {
    c.start();
    await new Promise((r) => setTimeout(r, 120));
    assert.equal(c.connected, false);
    await assert.rejects(c.invoke('workspace:exists', { path: 'a' }), /Rowboat nicht verbunden/);
  } finally { c.stop(); }
});

test('invoke: Ergebnis per id, Fehler mit code', async () => {
  const file = tmpEmbedFile();
  const rb = await fakeRowboat((m) => (m.channel === 'browser:setVisible'
    ? { type: 'result', id: m.id, ok: false, error: { code: 'embed_unsupported', message: 'embed_unsupported: nur im Rowboat-Fenster' } }
    : { type: 'result', id: m.id, ok: true, result: { echo: m.args } }));
  writeInfo(file, rb.port);
  const c = new RowboatEmbedClient({ embedFile: file, backoffMs: [20], logger: quiet() });
  try {
    c.start();
    await once(c, 'connected');
    assert.deepEqual(await c.invoke('workspace:exists', { path: 'a.md' }), { echo: { path: 'a.md' } });
    await assert.rejects(c.invoke('browser:setVisible', { visible: true }), (err) => err.code === 'embed_unsupported' && /nur im Rowboat-Fenster/.test(err.message));
  } finally { c.stop(); await rb.kill(); }
});

test('push wird als Ereignis weitergegeben', async () => {
  const file = tmpEmbedFile();
  const rb = await fakeRowboat();
  writeInfo(file, rb.port);
  const c = new RowboatEmbedClient({ embedFile: file, backoffMs: [20], logger: quiet() });
  try {
    c.start();
    await once(c, 'connected');
    const got = once(c, 'push');
    rb.push('runs:events', { x: 1 });
    assert.deepEqual(await got, ['runs:events', { x: 1 }]);
  } finally { c.stop(); await rb.kill(); }
});

test('Abbruch: offener Aufruf wird abgelehnt, disconnected kommt, Neustart auf neuem Port verbindet wieder', async () => {
  const file = tmpEmbedFile();
  const rb = await fakeRowboat(() => null); // antwortet nie
  writeInfo(file, rb.port);
  const c = new RowboatEmbedClient({ embedFile: file, backoffMs: [20], logger: quiet() });
  try {
    c.start();
    await once(c, 'connected');
    const pending = c.invoke('workspace:exists', { path: 'a' });
    const down = once(c, 'disconnected');
    await rb.kill();
    await assert.rejects(pending, /getrennt/);
    await down;
    const rb2 = await fakeRowboat();
    writeInfo(file, rb2.port);
    await once(c, 'connected');
    assert.equal(c.info.port, rb2.port);
    await rb2.kill();
  } finally { c.stop(); }
});

test('showApp schickt show-app nur, wenn verbunden', async () => {
  const file = tmpEmbedFile();
  const rb = await fakeRowboat();
  const c = new RowboatEmbedClient({ embedFile: file, backoffMs: [20], logger: quiet() });
  try {
    assert.equal(c.showApp(), false);
    writeInfo(file, rb.port);
    c.start();
    await once(c, 'connected');
    assert.equal(c.showApp(), true);
    await new Promise((r) => setTimeout(r, 30));
    assert.deepEqual(rb.seen.at(-1), { type: 'show-app' });
  } finally { c.stop(); await rb.kill(); }
});

test('das Token erscheint in keiner Log-Zeile', async () => {
  const file = tmpEmbedFile();
  const rb = await fakeRowboat();
  writeInfo(file, rb.port);
  const logger = quiet();
  const c = new RowboatEmbedClient({ embedFile: file, backoffMs: [20], logger });
  try {
    c.start();
    await once(c, 'connected');
    await rb.kill();
    await once(c, 'disconnected');
  } finally { c.stop(); }
  assert.ok(logger.lines.length > 0);
  assert.ok(logger.lines.every((l) => !l.includes(TOKEN)));
});
```

- [ ] **Step 2: Tests laufen lassen, sie muessen scheitern**

Run: `cd VOICE; node --test rowboat-embed-client.test.js`
Expected: FAIL („Cannot find module './rowboat-embed-client'“)

- [ ] **Step 3: Implementieren** – `rowboat-embed-client.js`:

```js
/**
 * Verbindung zum Einbettungszugang der laufenden Rowboat-Instanz
 * (Spec vibemind-os 2026-10-17-rowboat-einbettung, Abschnitt 4).
 *
 * Liest <ROWBOAT_WORKDIR | ~/.rowboat>/embed.json, meldet sich mit dem Token an und reicht
 * invoke weiter. Das Token bleibt in diesem Hauptprozess; die Rowboat-Ansicht sieht es nie.
 * Die voice-App startet Rowboat nicht – sie verbindet sich nur und versucht es mit Backoff erneut.
 */
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const BACKOFF_MS = [1000, 2000, 5000, 10000, 30000];
const HELLO_TIMEOUT_MS = 5000;
const MAX_MESSAGE_BYTES = 64 * 1024 * 1024;
const NOT_RUNNING = 'Rowboat laeuft nicht (start_desktop.py --embed)';

function defaultEmbedFile(env = process.env) {
  return path.join(env.ROWBOAT_WORKDIR || path.join(os.homedir(), '.rowboat'), 'embed.json');
}

function defaultPidAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    return err.code === 'EPERM';
  }
}

function readEmbedInfo(file, { readFileSync = fs.readFileSync, isPidAlive = defaultPidAlive } = {}) {
  let raw;
  try {
    raw = JSON.parse(readFileSync(file, 'utf8'));
  } catch {
    return { ok: false, reason: NOT_RUNNING };
  }
  const valid = raw && raw.v === 1 && Number.isInteger(raw.port) && raw.port > 0
    && typeof raw.token === 'string' && raw.token.length > 0
    && Number.isInteger(raw.pid) && raw.pid > 0 && typeof raw.preloadPath === 'string';
  if (!valid) return { ok: false, reason: 'embed.json unvollstaendig' };
  if (!isPidAlive(raw.pid)) return { ok: false, reason: NOT_RUNNING };
  return { ok: true, info: { port: raw.port, token: raw.token, pid: raw.pid, preloadPath: raw.preloadPath } };
}

class RowboatEmbedClient extends EventEmitter {
  constructor(deps = {}) {
    super();
    this.embedFile = deps.embedFile || defaultEmbedFile();
    this.WebSocket = deps.WebSocket || require('ws');
    this.readFileSync = deps.readFileSync || fs.readFileSync;
    this.isPidAlive = deps.isPidAlive || defaultPidAlive;
    this.backoffMs = deps.backoffMs || BACKOFF_MS;
    this.helloTimeoutMs = deps.helloTimeoutMs || HELLO_TIMEOUT_MS;
    this.logger = deps.logger || console;
    this.state = 'disconnected';
    this.reason = NOT_RUNNING;
    this.info = null;
    this.invokeChannels = [];
    this._ws = null;
    this._pending = new Map();
    this._nextId = 1;
    this._attempt = 0;
    this._timer = null;
    this._stopped = true;
  }

  get connected() {
    return this.state === 'connected';
  }

  start() {
    if (!this._stopped) return;
    this._stopped = false;
    this._connect();
  }

  stop() {
    this._stopped = true;
    clearTimeout(this._timer);
    this._timer = null;
    if (this._ws) this._ws.terminate();
  }

  invoke(channel, args) {
    if (!this.connected || !this._ws) return Promise.reject(new Error('Rowboat nicht verbunden'));
    const id = this._nextId++;
    return new Promise((resolve, reject) => {
      this._pending.set(id, { resolve, reject });
      this._ws.send(JSON.stringify({ type: 'invoke', id, channel, args }));
    });
  }

  /** Holt Rowboats eigenes Fenster nach vorn („In Rowboat öffnen“). */
  showApp() {
    if (!this.connected || !this._ws) return false;
    this._ws.send(JSON.stringify({ type: 'show-app' }));
    return true;
  }

  _schedule() {
    if (this._stopped || this._timer) return;
    const delay = this.backoffMs[Math.min(this._attempt, this.backoffMs.length - 1)];
    this._attempt += 1;
    this._timer = setTimeout(() => {
      this._timer = null;
      this._connect();
    }, delay);
  }

  _down(reason) {
    const was = this.connected;
    this.state = 'disconnected';
    this.reason = reason;
    this.info = null;
    this.invokeChannels = [];
    if (was) {
      this.logger.warn(`[RowboatEmbed] getrennt – ${reason}`);
      this.emit('disconnected', reason);
    }
  }

  _connect() {
    if (this._stopped) return;
    const read = readEmbedInfo(this.embedFile, { readFileSync: this.readFileSync, isPidAlive: this.isPidAlive });
    if (!read.ok) {
      this._down(read.reason);
      this._schedule();
      return;
    }
    const info = read.info;
    this.state = 'connecting';
    const ws = new this.WebSocket(`ws://127.0.0.1:${info.port}/embed`, { maxPayload: MAX_MESSAGE_BYTES });
    this._ws = ws;
    const helloTimer = setTimeout(() => ws.terminate(), this.helloTimeoutMs);
    ws.on('open', () => ws.send(JSON.stringify({ type: 'hello', v: 1, token: info.token })));
    ws.on('message', (data) => {
      let msg;
      try { msg = JSON.parse(String(data)); } catch { return; }
      if (msg.type === 'welcome') {
        clearTimeout(helloTimer);
        this.info = info;
        this.invokeChannels = Array.isArray(msg.invokeChannels) ? msg.invokeChannels : [];
        this.state = 'connected';
        this.reason = '';
        this._attempt = 0;
        this.logger.info(`[RowboatEmbed] verbunden (127.0.0.1:${info.port}, ${this.invokeChannels.length} Kanaele)`);
        this.emit('connected', { invokeChannels: this.invokeChannels, preloadPath: info.preloadPath });
      } else if (msg.type === 'result') {
        const pending = this._pending.get(msg.id);
        if (!pending) return;
        this._pending.delete(msg.id);
        if (msg.ok) {
          pending.resolve(msg.result);
        } else {
          const err = new Error((msg.error && msg.error.message) || 'Rowboat-Fehler');
          if (msg.error && msg.error.code) err.code = msg.error.code;
          pending.reject(err);
        }
      } else if (msg.type === 'push') {
        this.emit('push', msg.channel, msg.payload);
      }
    });
    ws.on('error', (err) => this.logger.warn(`[RowboatEmbed] ${err.code || err.message}`));
    ws.on('close', (code) => {
      clearTimeout(helloTimer);
      if (this._ws === ws) this._ws = null;
      for (const pending of this._pending.values()) pending.reject(new Error('Verbindung zu Rowboat getrennt'));
      this._pending.clear();
      this._down(code === 4401 ? 'Anmeldung bei Rowboat abgelehnt' : 'Verbindung zu Rowboat getrennt');
      this._schedule();
    });
  }
}

module.exports = { RowboatEmbedClient, readEmbedInfo, defaultEmbedFile, NOT_RUNNING };
```

`package.json`:
- Unter `dependencies` alphabetisch `"ws": "^8.20.0"` eintragen.
- An `test:unit` ` rowboat-embed-client.test.js` anhaengen.
- Danach `npm install --no-audit --no-fund`.

- [ ] **Step 4: Tests laufen lassen, sie muessen bestehen**

Run: `cd VOICE; node --test rowboat-embed-client.test.js`
Expected: 8 Tests PASS.

- [ ] **Step 5: Commit**

```powershell
cd C:\Users\User\ClaudeWork\wt-voice-rowboat
git add electron-app/rowboat-embed-client.js electron-app/rowboat-embed-client.test.js electron-app/package.json
git commit -m "feat(voice): Verbindung zum Rowboat-Einbettungszugang (embed.json, Token, Backoff)"
```

---

### Task 7: Kanaele und `app://` an die Rowboat-Ansicht anschliessen (voice, `rowboat-embed-view.js`)

**Files:**
- Create: `VOICE\rowboat-embed-view.js`
- Create: `VOICE\rowboat-embed-view.test.js`
- Modify: `VOICE\package.json` (`test:unit` + `rowboat-embed-view.test.js`)

**Interfaces:**
- Consumes: `RowboatEmbedClient` (Task 6): `connected`, `info`, `invoke`, `on('push')`.
- Produces (`module.exports`):
  - `APP_PARTITION = 'persist:rowboat-embed'`
  - `APP_SCHEME_PRIVILEGES` (fuer `protocol.registerSchemesAsPrivileged`)
  - `BROWSER_PANE_NOTE`
  - `attachEmbedIpc(webContents, client, channels, { onBrowserPane? }): () => void` (gibt `detach` zurueck)
  - `proxyAppRequest(request, client, fetchImpl?): Promise<Response>`
  - `registerAppProtocol(session, client, fetchImpl?)`

- [ ] **Step 1: Failing tests schreiben** – `rowboat-embed-view.test.js`:

```js
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const test = require('node:test');

const { APP_PARTITION, APP_SCHEME_PRIVILEGES, attachEmbedIpc, proxyAppRequest, registerAppProtocol } = require('./rowboat-embed-view');

function fakeWebContents() {
  const handlers = new Map();
  const sent = [];
  return {
    handlers, sent, destroyed: false,
    ipc: {
      handle: (ch, fn) => { if (handlers.has(ch)) throw new Error(`doppelt: ${ch}`); handlers.set(ch, fn); },
      removeHandler: (ch) => handlers.delete(ch),
    },
    send(ch, p) { sent.push([ch, p]); },
    isDestroyed() { return this.destroyed; },
  };
}
function fakeClient(connected = true) {
  const c = new EventEmitter();
  c.connected = connected;
  c.info = connected ? { port: 4321, token: 'tok-geheim', pid: 1, preloadPath: 'C:/rb/preload.js' } : null;
  c.calls = [];
  c.invoke = async (ch, args) => { c.calls.push([ch, args]); return { ok: true, ch }; };
  return c;
}

test('jeder Kanal bekommt einen Handler auf genau dieser Ansicht, Aufrufe gehen an den Client', async () => {
  const wc = fakeWebContents();
  const client = fakeClient();
  attachEmbedIpc(wc, client, ['workspace:exists', 'runs:list']);
  assert.deepEqual([...wc.handlers.keys()], ['workspace:exists', 'runs:list']);
  assert.deepEqual(await wc.handlers.get('workspace:exists')({}, { path: 'a' }), { ok: true, ch: 'workspace:exists' });
  assert.deepEqual(client.calls, [['workspace:exists', { path: 'a' }]]);
});

test('Push geht an die Ansicht, nach detach nicht mehr; Handler sind entfernt', () => {
  const wc = fakeWebContents();
  const client = fakeClient();
  const detach = attachEmbedIpc(wc, client, ['workspace:exists']);
  client.emit('push', 'runs:events', { x: 1 });
  detach();
  client.emit('push', 'runs:events', { x: 2 });
  assert.deepEqual(wc.sent, [['runs:events', { x: 1 }]]);
  assert.equal(wc.handlers.size, 0);
  attachEmbedIpc(wc, client, ['workspace:exists']); // erneut anschliessbar ohne „doppelt“
});

test('browser:setVisible {visible:true} meldet den Browser-Bereich, reicht aber trotzdem weiter', async () => {
  const wc = fakeWebContents();
  const client = fakeClient();
  let notes = 0;
  attachEmbedIpc(wc, client, ['browser:setVisible'], { onBrowserPane: () => { notes += 1; } });
  await wc.handlers.get('browser:setVisible')({}, { visible: false });
  await wc.handlers.get('browser:setVisible')({}, { visible: true });
  assert.equal(notes, 1);
  assert.equal(client.calls.length, 2);
});

test('Push an eine zerstoerte Ansicht wird verworfen', () => {
  const wc = fakeWebContents();
  const client = fakeClient();
  attachEmbedIpc(wc, client, []);
  wc.destroyed = true;
  client.emit('push', 'runs:events', { x: 1 });
  assert.deepEqual(wc.sent, []);
});

test('app://-Proxy: Token im Header, nie in der URL; Range geht durch; kodierte Pfade bleiben', async () => {
  const seen = [];
  const fetchImpl = async (url, init) => { seen.push([url, init]); return new Response('ok', { status: 206 }); };
  const client = fakeClient();
  const res = await proxyAppRequest(new Request('app://workspace/Meine%20Notizen/b%C3%A4r.png?x=1', { headers: { range: 'bytes=0-9' } }), client, fetchImpl);
  assert.equal(res.status, 206);
  const [url, init] = seen[0];
  assert.equal(url, 'http://127.0.0.1:4321/app/workspace/Meine%20Notizen/b%C3%A4r.png?x=1');
  assert.ok(!url.includes('tok-geheim'));
  assert.equal(init.headers.authorization, 'Bearer tok-geheim');
  assert.equal(init.headers.range, 'bytes=0-9');
  assert.equal(init.method, 'GET');
});

test('app://-Proxy: Oberflaeche unter app://-/index.html', async () => {
  const seen = [];
  await proxyAppRequest(new Request('app://-/index.html'), fakeClient(), async (url) => { seen.push(url); return new Response(''); });
  assert.deepEqual(seen, ['http://127.0.0.1:4321/app/-/index.html']);
});

test('app://-Proxy ohne Verbindung: 503, kein Abruf', async () => {
  let called = false;
  const res = await proxyAppRequest(new Request('app://-/index.html'), fakeClient(false), async () => { called = true; return new Response(''); });
  assert.equal(res.status, 503);
  assert.equal(called, false);
});

test('registerAppProtocol haengt am Protokoll genau dieser Sitzung; Schema-Rechte wie in Rowboat', async () => {
  const handled = [];
  const ses = { protocol: { handle: (scheme, fn) => handled.push([scheme, fn]) } };
  registerAppProtocol(ses, fakeClient(), async () => new Response('x'));
  assert.equal(handled[0][0], 'app');
  assert.equal(await (await handled[0][1](new Request('app://-/index.html'))).text(), 'x');
  assert.equal(APP_PARTITION, 'persist:rowboat-embed');
  assert.deepEqual(APP_SCHEME_PRIVILEGES, { scheme: 'app', privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: true, stream: true } });
});
```

- [ ] **Step 2: Tests laufen lassen, sie muessen scheitern**

Run: `cd VOICE; node --test rowboat-embed-view.test.js`
Expected: FAIL („Cannot find module './rowboat-embed-view'“)

- [ ] **Step 3: Implementieren** – `rowboat-embed-view.js`:

```js
/**
 * Schliesst die Rowboat-Ansicht an den Einbettungszugang an
 * (Spec vibemind-os 2026-10-17-rowboat-einbettung, Abschnitte 3 und 4).
 *
 * - Rowboats eigener Preload ruft ipcRenderer.invoke(channel); die Handler sitzen nur auf dieser
 *   Ansicht (webContents.ipc), kollidieren also nie mit den Kanaelen der voice-App.
 * - app:// (Oberflaeche, Workspace-Dateien, Vorschauen) laeuft in der eigenen Sitzung
 *   APP_PARTITION und wird hier im Hauptprozess mit dem Token weitergeleitet.
 */
const APP_PARTITION = 'persist:rowboat-embed';
const APP_SCHEME_PRIVILEGES = {
  scheme: 'app',
  privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: true, stream: true },
};
const BROWSER_PANE_NOTE = 'Der Browser-Bereich laeuft nur im Rowboat-Fenster.';

function attachEmbedIpc(webContents, client, channels, { onBrowserPane } = {}) {
  for (const channel of channels) {
    webContents.ipc.handle(channel, (_event, args) => {
      if (channel === 'browser:setVisible' && args && args.visible === true && onBrowserPane) onBrowserPane();
      return client.invoke(channel, args);
    });
  }
  const onPush = (channel, payload) => {
    if (!webContents.isDestroyed()) webContents.send(channel, payload);
  };
  client.on('push', onPush);
  return function detach() {
    for (const channel of channels) webContents.ipc.removeHandler(channel);
    client.removeListener('push', onPush);
  };
}

async function proxyAppRequest(request, client, fetchImpl = globalThis.fetch) {
  const info = client.connected ? client.info : null;
  if (!info) return new Response('Rowboat nicht verbunden', { status: 503 });
  const url = new URL(request.url);
  const headers = { authorization: `Bearer ${info.token}` };
  const range = request.headers.get('range');
  if (range) headers.range = range;
  return fetchImpl(`http://127.0.0.1:${info.port}/app/${url.host}${url.pathname}${url.search}`, {
    method: request.method === 'HEAD' ? 'HEAD' : 'GET',
    headers,
  });
}

function registerAppProtocol(session, client, fetchImpl) {
  session.protocol.handle('app', (request) => proxyAppRequest(request, client, fetchImpl));
}

module.exports = { APP_PARTITION, APP_SCHEME_PRIVILEGES, BROWSER_PANE_NOTE, attachEmbedIpc, proxyAppRequest, registerAppProtocol };
```

An `test:unit` in `package.json` ` rowboat-embed-view.test.js` anhaengen.

- [ ] **Step 4: Tests laufen lassen, sie muessen bestehen**

Run: `cd VOICE; node --test rowboat-embed-view.test.js`
Expected: 8 Tests PASS.

- [ ] **Step 5: Commit**

```powershell
cd C:\Users\User\ClaudeWork\wt-voice-rowboat
git add electron-app/rowboat-embed-view.js electron-app/rowboat-embed-view.test.js electron-app/package.json
git commit -m "feat(voice): Rowboat-Kanaele pro Ansicht und app://-Weiterleitung mit Token"
```

---

### Task 8: RowboatManager auf die Einbettung umstellen, alte Bridge entfernen (voice)

**Files:**
- Modify (neu schreiben): `VOICE\rowboat-manager.js`
- Delete: `VOICE\rowboat-preload.js`, `VOICE\rowboat-core-bridge.mjs`
- Modify: `VOICE\main.js`:
  - `:24` registerSchemesAsPrivileged
  - `:166-270` Kommentar und `autoConfigureRowboatModels`
  - `:546-553` relayEvent
  - Stub-Block ab `// ROWBOAT IPC STUBS` (`:1954`) bis Ende `agent-schedule:updateAgent` (`:2121`)
  - `:3259` new RowboatManager
  - `:3376-3378` Teil-1-Hinweis
  - `:3474` Quit
- Modify: `VOICE\package.json` (`build.files`: Eintrag `"rowboat-preload.js"` entfernen)
- Modify: `VOICE\overlay-managers.test.js`:
  - Tests `:148-156` und `:298-308` ersetzen
  - Rowboat-Zeile aus `FILE_CASES` (`:196`) entfernen

**Interfaces:**
- Consumes:
  - `RowboatEmbedClient` (Task 6): `connected`, `info.preloadPath`, `invokeChannels`, `reason`, `showApp()`, Events `connected`/`disconnected`.
  - Aus Task 7: `APP_PARTITION`, `APP_SCHEME_PRIVILEGES`, `BROWSER_PANE_NOTE`, `attachEmbedIpc`, `registerAppProtocol`.
  - `loadOverlay`, `loadStatus`, `resolveElectronParts` (overlay-status.js).
- Produces:
  - `new RowboatManager(mainWindow, { client, BrowserView?, shell?, dialog?, overlayDeps? })`
  - Methoden: `show()`, `hide()`, `toggle()`, `updateBounds()`, `getIsVisible()`, `reload()`, `destroy()`, `openInRowboat(): boolean`
  - Felder: `overlayReady: Promise`, `overlayResult`.

- [ ] **Step 1: Failing tests schreiben.**
  - In `overlay-managers.test.js` die beiden Platzhalter-Tests (`:148-156` und `:298-308`) und die Rowboat-Zeile in `FILE_CASES` loeschen.
  - Neu einfuegen (nach dem eyeTerm-Test):

```js
const { EventEmitter } = require('node:events');
const { APP_PARTITION, BROWSER_PANE_NOTE } = require('./rowboat-embed-view');

function rowboatClient(connected) {
  const c = new EventEmitter();
  c.connected = connected;
  c.reason = connected ? '' : 'Rowboat laeuft nicht (start_desktop.py --embed)';
  c.info = connected ? { port: 1, token: 't', pid: 1, preloadPath: 'C:/rb/preload.js' } : null;
  c.invokeChannels = connected ? ['workspace:exists', 'browser:setVisible'] : [];
  c.invoke = async () => ({});
  c.shown = 0;
  c.showApp = () => { c.shown += 1; return true; };
  return c;
}
function withIpc(h) {
  const Base = h.FakeBrowserView;
  h.FakeBrowserView = class extends Base {
    constructor(options) {
      super(options);
      const handlers = new Map();
      this.ipcHandlers = handlers;
      this.webContents.ipc = { handle: (ch, fn) => handlers.set(ch, fn), removeHandler: (ch) => handlers.delete(ch) };
    }
  };
  return h;
}
function rowboat(h, client, extra = {}) {
  return new RowboatManager(h.mainWindow, { BrowserView: h.FakeBrowserView, shell: h.shell, client, overlayDeps: { logger: quiet }, ...extra });
}

test('Rowboat: nicht verbunden ergibt Statuskarte mit Hinweis auf start_desktop.py --embed', async () => {
  const h = withIpc(harness());
  const r = rowboat(h, rowboatClient(false));
  r.show();
  await r.overlayReady;
  assert.match(card(h.views[0].loads[0]), /Rowboat nicht verfuegbar – Rowboat laeuft nicht \(start_desktop\.py --embed\)/);
});

test('Rowboat: verbunden laedt app://-/index.html mit Rowboats Preload in eigener Sitzung, Sandbox an', async () => {
  const h = withIpc(harness());
  const r = rowboat(h, rowboatClient(true));
  r.show();
  await r.overlayReady;
  const prefs = h.views[0].options.webPreferences;
  assert.equal(prefs.preload, 'C:/rb/preload.js');
  assert.equal(prefs.partition, APP_PARTITION);
  assert.equal(prefs.sandbox, true);
  assert.equal(prefs.nodeIntegration, false);
  assert.deepEqual(h.views[0].loads[0], ['url', 'app://-/index.html']);
  assert.deepEqual([...h.views[0].ipcHandlers.keys()], ['workspace:exists', 'browser:setVisible']);
});

test('Rowboat: Verbindung kommt spaeter – Ansicht wird mit Preload neu gebaut und bleibt sichtbar', async () => {
  const h = withIpc(harness());
  const attached = [];
  h.mainWindow.setBrowserView = (v) => attached.push(v);
  const client = rowboatClient(false);
  const r = rowboat(h, client);
  r.show();
  await r.overlayReady;
  Object.assign(client, { connected: true, reason: '', info: { port: 1, token: 't', pid: 1, preloadPath: 'C:/rb/preload.js' }, invokeChannels: ['workspace:exists'] });
  client.emit('connected', { invokeChannels: client.invokeChannels, preloadPath: 'C:/rb/preload.js' });
  await r.overlayReady;
  assert.equal(h.views.length, 2);
  assert.equal(h.views[1].options.webPreferences.preload, 'C:/rb/preload.js');
  assert.deepEqual(h.views[1].loads[0], ['url', 'app://-/index.html']);
  assert.equal(attached.at(-1), h.views[1]);
});

test('Rowboat: Abbruch zeigt die Statuskarte, Neuverbindung laedt neu ohne neue Ansicht (gleicher Preload)', async () => {
  const h = withIpc(harness());
  const client = rowboatClient(true);
  const r = rowboat(h, client);
  r.show();
  await r.overlayReady;
  Object.assign(client, { connected: false, reason: 'Verbindung zu Rowboat getrennt', info: null, invokeChannels: [] });
  client.emit('disconnected', client.reason);
  await r.overlayReady;
  assert.match(card(h.views[0].loads[1]), /Verbindung zu Rowboat getrennt/);
  assert.equal(h.views[0].ipcHandlers.size, 0, 'Handler beim Abbruch entfernt');
  Object.assign(client, { connected: true, reason: '', info: { port: 2, token: 't2', pid: 2, preloadPath: 'C:/rb/preload.js' }, invokeChannels: ['workspace:exists'] });
  client.emit('connected', { invokeChannels: client.invokeChannels, preloadPath: 'C:/rb/preload.js' });
  await r.overlayReady;
  assert.equal(h.views.length, 1);
  assert.deepEqual(h.views[0].loads[2], ['url', 'app://-/index.html']);
});

test('Rowboat: Browser-Bereich zeigt den Hinweis einmal pro Sitzung, „In Rowboat öffnen“ holt Rowboats Fenster', async () => {
  const h = withIpc(harness());
  const client = rowboatClient(true);
  const boxes = [];
  const dialog = { showMessageBox: async (_win, opts) => { boxes.push(opts); return { response: 0 }; } };
  const r = rowboat(h, client, { dialog });
  r.show();
  await r.overlayReady;
  const setVisible = h.views[0].ipcHandlers.get('browser:setVisible');
  await setVisible({}, { visible: true }).catch(() => {});
  await setVisible({}, { visible: true }).catch(() => {});
  await tick();
  assert.equal(boxes.length, 1);
  assert.equal(boxes[0].message, BROWSER_PANE_NOTE);
  assert.deepEqual(boxes[0].buttons, ['In Rowboat öffnen', 'Abbrechen']);
  assert.equal(client.shown, 1);
});

test('Rowboat: Statusmeldung „unavailable“ ohne Verbindung, „ready“ mit Verbindung', async () => {
  const h = withIpc(harness());
  const sent = withStatusSink(h);
  const r = rowboat(h, rowboatClient(false));
  r.show();
  await r.overlayReady;
  h.views[0].handlers.get('did-finish-load')();
  assert.deepEqual(sent.at(-1), ['python-message', { type: 'rowboat_view_status', status: 'unavailable', message: 'Rowboat laeuft nicht (start_desktop.py --embed)' }]);
  const h2 = withIpc(harness());
  const sent2 = withStatusSink(h2);
  const r2 = rowboat(h2, rowboatClient(true));
  r2.show();
  await r2.overlayReady;
  h2.views[0].handlers.get('did-finish-load')();
  assert.deepEqual(sent2.at(-1), ['python-message', { type: 'rowboat_view_status', status: 'ready' }]);
});
```

`withStatusSink` und `tick` sind weiter unten in der Datei definiert. Die neuen Tests muessen deshalb **nach** diesen Hilfsfunktionen stehen, also am Dateiende einfuegen.

- [ ] **Step 2: Tests laufen lassen, sie muessen scheitern**

Run: `cd VOICE; node --test overlay-managers.test.js`
Expected: Die 6 neuen Rowboat-Tests FAIL (u. a. „client is undefined“ / alte Platzhalter-Logik). Alle anderen bleiben PASS.

- [ ] **Step 3: `rowboat-manager.js` neu schreiben**

```js
/**
 * Rowboat Manager for VibeMind (Spec vibemind-os 2026-10-17-rowboat-einbettung, Abschnitt 4).
 *
 * Zeigt die echte Rowboat-Oberflaeche als BrowserView im Roarboot-Space. Rowboat laeuft als eigener
 * Prozess (start_desktop.py --embed); diese Ansicht laedt Rowboats eigenen Preload, ihre Aufrufe
 * beantwortet Rowboats Hauptprozess ueber RowboatEmbedClient. Ohne Verbindung: Statuskarte.
 */
const { loadOverlay, loadStatus, resolveElectronParts } = require('./overlay-status');
const { APP_PARTITION, BROWSER_PANE_NOTE, attachEmbedIpc } = require('./rowboat-embed-view');

const APP_URL = 'app://-/index.html';
const _RB_C = '\x1b[94m', _RST = '\x1b[0m'; // Blue (Rowboat)
function _rbLog(...args) { process.stdout.write(`${_RB_C}[RowboatManager] ${args.join(' ')}${_RST}\n`); }

class RowboatManager {
  constructor(mainWindow, dependencies = {}) {
    const { BrowserView, shell, dialog } = resolveElectronParts(dependencies, ['BrowserView', 'shell', 'dialog']);
    if (!dependencies.client) throw new TypeError('RowboatManager braucht dependencies.client (RowboatEmbedClient)');
    this.BrowserView = BrowserView;
    this.shell = shell;
    this.dialog = dialog;
    this.client = dependencies.client;
    this._overlayDeps = dependencies.overlayDeps || {};
    this.mainWindow = mainWindow;
    this.rowboatView = null;
    this.isVisible = false;
    this.overlayReady = null;
    this.overlayResult = null;
    this._viewPreload = null;
    this._detachIpc = null;
    this._browserNoteShown = false;

    // Titlebar (32px) + space-nav tab bar (42px + 1px border)
    this.topOffset = 32 + 43;

    this.client.on('connected', () => this._onConnected());
    this.client.on('disconnected', () => { if (this.rowboatView) this._load(); });

    if (this.mainWindow) {
      this.mainWindow.on('resize', () => {
        if (this.isVisible && this.rowboatView) this.updateBounds();
      });
    }
  }

  _createView() {
    const preload = this.client.connected && this.client.info ? this.client.info.preloadPath : null;
    const view = new this.BrowserView({
      webPreferences: {
        ...(preload ? { preload } : {}),
        partition: APP_PARTITION,
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
        // PDFs in <iframe> wie in Rowboats eigenem Fenster
        plugins: true,
      },
    });
    this.rowboatView = view;
    this._viewPreload = preload;

    view.webContents.setWindowOpenHandler(({ url }) => {
      this.shell.openExternal(url);
      return { action: 'deny' };
    });
    // Wie in Rowboats Fenster: was nicht app:// ist, geht in den System-Browser.
    view.webContents.on('will-navigate', (event, url) => {
      if (url.startsWith('app://')) return;
      event.preventDefault();
      this.shell.openExternal(url);
    });
    // Rowboats macOS-Titelleisten-Reservierung entfernen (VibeMind hat eine eigene Titelleiste)
    view.webContents.on('dom-ready', () => {
      view.webContents.insertCSS('.titlebar-drag-region { -webkit-app-region: no-drag !important; }');
    });
    view.webContents.on('did-finish-load', () => {
      if (this.mainWindow && !this.mainWindow.isDestroyed()) {
        this.mainWindow.webContents.send('python-message', { type: 'rowboat_view_status', ...loadStatus(this.overlayResult) });
      }
      _rbLog('Renderer loaded');
    });
    view.webContents.on('did-fail-load', (_event, errorCode, errorDescription) => {
      if (this.mainWindow && !this.mainWindow.isDestroyed()) {
        this.mainWindow.webContents.send('python-message', {
          type: 'rowboat_view_status', status: 'error', message: `Load failed: ${errorDescription} (${errorCode})`,
        });
      }
      console.error('[RowboatManager] Load failed:', errorCode, errorDescription);
    });

    this._load();
    return view;
  }

  /** Laedt Rowboat (verbunden + Preload passt) oder die Statuskarte. */
  _load() {
    const view = this.rowboatView;
    if (this._detachIpc) { this._detachIpc(); this._detachIpc = null; }
    if (this.client.connected && this._viewPreload) {
      this._detachIpc = attachEmbedIpc(view.webContents, this.client, this.client.invokeChannels, {
        onBrowserPane: () => this._browserPaneNote(),
      });
      this.overlayResult = { ok: true, target: APP_URL };
      this.overlayReady = view.webContents.loadURL(APP_URL).then(
        () => this.overlayResult,
        (err) => {
          _rbLog(`Laden fehlgeschlagen – ${err.message}`);
          return this.overlayResult;
        },
      );
      return this.overlayReady;
    }
    const reason = this.client.reason || 'Rowboat nicht verbunden';
    this.overlayReady = loadOverlay(view.webContents,
      { space: 'Rowboat', targets: [{ kind: 'unavailable', reason, target: 'Rowboat-Einbettung' }] },
      { ...this._overlayDeps, onResult: (r) => { this.overlayResult = r; } });
    return this.overlayReady;
  }

  _onConnected() {
    if (!this.rowboatView) return;
    if (this._viewPreload === this.client.info.preloadPath) this._load();
    else this._rebuildView();
  }

  /** Der Preload steht beim Erzeugen fest: Ansicht ohne (oder mit anderem) Preload neu bauen. */
  _rebuildView() {
    const wasVisible = this.isVisible;
    this._disposeView();
    this._createView();
    if (wasVisible) {
      this.mainWindow.setBrowserView(this.rowboatView);
      this.updateBounds();
    }
  }

  _disposeView() {
    if (this._detachIpc) { this._detachIpc(); this._detachIpc = null; }
    if (!this.rowboatView) return;
    if (this.isVisible && this.mainWindow) this.mainWindow.setBrowserView(null);
    try { this.rowboatView.webContents.close(); } catch { /* schon zu */ }
    this.rowboatView = null;
  }

  _browserPaneNote() {
    if (this._browserNoteShown) return;
    this._browserNoteShown = true;
    this.dialog.showMessageBox(this.mainWindow, {
      type: 'info', message: BROWSER_PANE_NOTE, buttons: ['In Rowboat öffnen', 'Abbrechen'], defaultId: 0, cancelId: 1,
    }).then(({ response }) => {
      if (response === 0) this.openInRowboat();
    }).catch(() => {});
  }

  /** Holt Rowboats eigenes (verstecktes) Fenster nach vorn. */
  openInRowboat() {
    return this.client.showApp();
  }

  show() {
    if (!this.mainWindow) {
      console.error('[RowboatManager] No main window available');
      return;
    }
    if (!this.rowboatView) this._createView();
    else if (this.overlayResult && !this.overlayResult.ok) this._onConnectedOrCard();
    this.mainWindow.setBrowserView(this.rowboatView);
    this.updateBounds();
    this.isVisible = true;
    _rbLog('Rowboat shown');
  }

  /** show() nach einer Statuskarte: verbunden → Rowboat laden, sonst Karte mit aktuellem Grund. */
  _onConnectedOrCard() {
    if (this.client.connected) this._onConnected();
    else this._load();
  }

  hide() {
    if (!this.mainWindow || !this.rowboatView) return;
    this.mainWindow.setBrowserView(null);
    this.isVisible = false;
    _rbLog('Rowboat hidden');
  }

  toggle() {
    if (this.isVisible) this.hide();
    else this.show();
  }

  updateBounds() {
    if (!this.mainWindow || !this.rowboatView) return;
    const bounds = this.mainWindow.getContentBounds();
    this.rowboatView.setBounds({ x: 0, y: this.topOffset, width: bounds.width, height: bounds.height - this.topOffset });
  }

  getIsVisible() {
    return this.isVisible;
  }

  reload() {
    if (this.rowboatView) this._load();
  }

  destroy() {
    this._disposeView();
    this.isVisible = false;
  }
}

module.exports = RowboatManager;
```

- [ ] **Step 4: `main.js` aufraeumen.**
  1. Oben bei den Requires:

```js
const { APP_PARTITION: ROWBOAT_PARTITION, APP_SCHEME_PRIVILEGES: ROWBOAT_APP_SCHEME, registerAppProtocol: registerRowboatAppProtocol } = require('./rowboat-embed-view');
const { RowboatEmbedClient } = require('./rowboat-embed-client');
```

  2. `protocol.registerSchemesAsPrivileged([ … ])` (`:24`): als zweiten Eintrag `ROWBOAT_APP_SCHEME,` hinzufuegen. Rowboats Oberflaeche braucht `app://` als standard/secure/stream. Das Schema ist nur in der Sitzung `persist:rowboat-embed` belegt.
  3. Den Kommentar `:166-168` und die ganze Funktion `autoConfigureRowboatModels` (`:170-270`) loeschen. Rowboat pflegt seine Modelle selbst. Vorher mit `rg -n "autoConfigureRowboatModels" .` pruefen, dass es keinen Aufruf mehr gibt (seit Teil 1 keiner).
  4. Die beiden `relayEvent`-Zweige `:546-553` (`rowboat_update_available` / `rowboat_update_applied`) loeschen.
  5. Den Block von der Kommentarzeile `// ROWBOAT IPC STUBS` (`:1954`) bis zum Ende des Handlers `agent-schedule:updateAgent` (`:2121`) loeschen.
     - Vorher pruefen, dass jeder dort registrierte Kanal ein Rowboat-Kanal ist. Pruefung: Jeder Name aus `rg -o "ipcMain.handle\('([^']+)'" -r '$1'` im Block steht in `C:\Users\User\ClaudeWork\wt-rowboat-embed\apps\x\packages\shared\src\ipc.ts`.
     - Gehoert ein Kanal nicht zu Rowboat, bleibt er stehen; das kommt in den Report.
     - `const _fs`/`const _path` nur loeschen, wenn sie danach niemand mehr nutzt.
  6. `:3259` ersetzen:

```js
        // Rowboat (Teil 2): Verbindung zum laufenden Rowboat (start_desktop.py --embed); voice startet es nicht.
        rowboatEmbedClient = new RowboatEmbedClient({ logger: console });
        registerRowboatAppProtocol(session.fromPartition(ROWBOAT_PARTITION), rowboatEmbedClient);
        rowboatEmbedClient.start();
        rowboatManager = new RowboatManager(mainWindow, { client: rowboatEmbedClient });
```

     Neben `let rowboatManager = null;` (`:134`) `let rowboatEmbedClient = null;` deklarieren.
  7. `:3376-3378` (Teil-1-Hinweis „Rowboat-Bridge nicht gestartet – kommt mit Teil 2“) loeschen.
  8. Beim Beenden (`:3474`): `if (rowboatManager) rowboatManager.destroy();` ergaenzen um `if (rowboatEmbedClient) rowboatEmbedClient.stop();`.
  9. `git rm electron-app/rowboat-preload.js electron-app/rowboat-core-bridge.mjs`; in `package.json` unter `build.files` die Zeile `"rowboat-preload.js",` entfernen.
  10. Pruefen: `rg -n "rowboat-core-bridge|rowboat-preload|sendRpc|bridgeReady|relayEvent|placeholderReason" .` (ohne node_modules) findet nichts mehr.

- [ ] **Step 5: Tests laufen lassen, sie muessen bestehen**

Run: `cd VOICE; node --test overlay-managers.test.js; npm run test:unit; node -e "require('./rowboat-manager'); require('./rowboat-embed-client'); require('./rowboat-embed-view')"; node --check main.js`
Expected: alle PASS. Die `test:unit`-Suite ist so gruen wie in V3, plus die neuen Tests. Der Syntax-Check endet mit Exit 0.

- [ ] **Step 6: Commit**

```powershell
cd C:\Users\User\ClaudeWork\wt-voice-rowboat
git add -A electron-app/rowboat-manager.js electron-app/main.js electron-app/package.json electron-app/overlay-managers.test.js
git status --short   # nur diese Dateien plus die zwei Loeschungen
git commit -m "feat(voice): Roarboot zeigt die echte Rowboat-Oberflaeche, alte Bridge und Stubs entfernt"
git push -u origin feat/rowboat-einbettung
```

---

### Task 9: Launcher startet Rowboat vor Phase 5, Update-Skript baut es (aeusseres Repo)

**Files:**
- Modify: `V1\scripts\lib\vibemind-helpers.ps1` (neue Funktionen `Get-RowboatEmbedState`, `Get-RowboatEmbedLaunch`, `Get-RowboatAutostartAction` nach `Get-VoiceElectronLaunch`)
- Modify: `V1\scripts\vibemind-start.ps1`:
  - Parameter `-SkipRowboat`
  - Funktion `Invoke-Phase45-Rowboat` vor `Invoke-Phase5-Electron`
  - Aufruf vor `:881`
  - Doku-Kopf `:19`
- Modify: `V1\scripts\voice_betrieb_update.ps1` (Schritt 5c, Doku-Kopf)
- Create: `V1\scripts\rowboat_embed_autostart.ps1`
- Create: `V1\scripts\tests\rowboat-embed-launch.Tests.ps1`
- Modify: `V1\scripts\tests\voice-betrieb-update.Tests.ps1` (neuer `Describe`)

**Interfaces:**
- Consumes: `start_desktop.py --install | --build-embed | --embed` (Task 5); `embed.json` (Task 4).
- Produces:
  - `Get-RowboatEmbedState [-EmbedFile] [-IsPidAlive]` → `{Running, Pid, Port}`; das Token kommt nie in die Ausgabe.
  - `Get-RowboatEmbedLaunch -Root [-BetriebDir] [-PythonLauncher]` → `{OsDir, Exe, Arguments, Built}`
  - `Get-RowboatAutostartAction -Root [-BetriebDir]` → `{TaskName, Execute, Argument, WorkingDirectory}`

- [ ] **Step 1: Failing Pester-Tests schreiben** – `rowboat-embed-launch.Tests.ps1`:

```powershell
#Requires -Version 7
BeforeAll {
    $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
    . (Join-Path $repoRoot 'scripts\lib\vibemind-helpers.ps1')
    $script:StartScript = Join-Path $repoRoot 'scripts\vibemind-start.ps1'
    $script:Base = Join-Path $HOME ('.vbu-tests\rowboat-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $script:Base -Force | Out-Null
    function New-EmbedFile { param([string]$Name, [string]$Json)
        $f = Join-Path $script:Base $Name; Set-Content -LiteralPath $f -Value $Json; return $f }
}
AfterAll { Remove-Item -LiteralPath $script:Base -Recurse -Force -ErrorAction SilentlyContinue }

Describe 'Get-RowboatEmbedState' {
    It 'ohne embed.json: laeuft nicht' {
        (Get-RowboatEmbedState -EmbedFile (Join-Path $script:Base 'fehlt.json')).Running | Should -BeFalse
    }
    It 'kaputte embed.json: laeuft nicht' {
        (Get-RowboatEmbedState -EmbedFile (New-EmbedFile 'kaputt.json' '{nicht json')).Running | Should -BeFalse
    }
    It 'tote PID: laeuft nicht' {
        $f = New-EmbedFile 'tot.json' '{"v":1,"port":4567,"token":"geheim","pid":4242,"preloadPath":"x"}'
        (Get-RowboatEmbedState -EmbedFile $f -IsPidAlive { param($p) $false }).Running | Should -BeFalse
    }
    It 'lebende PID: laeuft, mit PID und Port, ohne Token' {
        $f = New-EmbedFile 'lebt.json' '{"v":1,"port":4567,"token":"geheim","pid":4242,"preloadPath":"x"}'
        $s = Get-RowboatEmbedState -EmbedFile $f -IsPidAlive { param($p) $p -eq 4242 }
        $s.Running | Should -BeTrue
        $s.Pid | Should -Be 4242
        $s.Port | Should -Be 4567
        ($s | ConvertTo-Json -Compress) | Should -Not -Match 'geheim'
    }
}

Describe 'Get-RowboatEmbedLaunch' {
    It 'nimmt vibemind-betrieb, wenn dort die Einbettung gebaut ist' {
        $betrieb = Join-Path $script:Base 'betrieb'
        $main = Join-Path $betrieb 'spaces\rowboat\rowboat\apps\x\apps\main\.package\dist'
        New-Item -ItemType Directory -Path $main -Force | Out-Null
        Set-Content -LiteralPath (Join-Path $main 'main.cjs') -Value '//'
        $l = Get-RowboatEmbedLaunch -Root 'C:\V1' -BetriebDir $betrieb
        $l.OsDir | Should -Be $betrieb
        $l.Built | Should -BeTrue
        $l.Exe | Should -Be 'py'
        ($l.Arguments -join ' ') | Should -Be ('-3.12 ' + (Join-Path $betrieb 'spaces\rowboat\scripts\start_desktop.py') + ' --embed --env-file C:\V1\.env')
    }
    It 'faellt auf den Haupt-Checkout zurueck und meldet „nicht gebaut“' {
        $l = Get-RowboatEmbedLaunch -Root 'C:\V1' -BetriebDir (Join-Path $script:Base 'gibtsnicht')
        $l.OsDir | Should -Be 'C:\V1\vibemind-os'
        $l.Built | Should -BeFalse
    }
}

Describe 'Get-RowboatAutostartAction' {
    It 'Aufgabe „VibeMind Rowboat (Einbettung)“ startet start_desktop.py --embed versteckt' {
        $a = Get-RowboatAutostartAction -Root 'C:\V1' -BetriebDir 'C:\B'
        $a.TaskName | Should -Be 'VibeMind Rowboat (Einbettung)'
        $a.Execute | Should -Be 'py'
        $a.Argument | Should -Be '-3.12 "C:\B\spaces\rowboat\scripts\start_desktop.py" --embed --env-file "C:\V1\.env"'
        $a.WorkingDirectory | Should -Be 'C:\B'
    }
}

Describe 'vibemind-start.ps1 Reihenfolge' {
    It 'startet Rowboat (Phase 4.5) vor der voice-App (Phase 5)' {
        $src = Get-Content -LiteralPath $script:StartScript -Raw
        $rowboat = $src.IndexOf("`nInvoke-Phase45-Rowboat")
        $electron = $src.IndexOf("`nInvoke-Phase5-Electron")
        $rowboat | Should -BeGreaterThan 0
        $rowboat | Should -BeLessThan $electron
    }
    It 'Phase 4.5 ist mit -SkipRowboat und -SkipElectron abschaltbar' {
        $src = Get-Content -LiteralPath $script:StartScript -Raw
        $src | Should -Match '\[switch\]\$SkipRowboat'
        $src | Should -Match 'if \(\$SkipElectron -or \$SkipRowboat\)'
    }
}
```

In `voice-betrieb-update.Tests.ps1` einen neuen `Describe` anhaengen. Er baut auf `New-VbuFixture` auf und ergaenzt `os-src` um einen getrackten Rowboat-Space:

```powershell
Describe 'voice_betrieb_update.ps1 (Rowboat-Einbettung, Schritt 5c)' {
    BeforeAll {
        New-VbuFixture -Name 'rowboat' -TrackLockfile $true
        New-Item -ItemType Directory -Path (Join-Path $script:OsSrc 'spaces\rowboat\scripts') -Force | Out-Null
        New-Item -ItemType Directory -Path (Join-Path $script:OsSrc 'spaces\rowboat\rowboat\apps\x') -Force | Out-Null
        Set-Content -LiteralPath (Join-Path $script:OsSrc 'spaces\rowboat\scripts\start_desktop.py') -Value 'print(1)'
        Set-Content -LiteralPath (Join-Path $script:OsSrc 'spaces\rowboat\rowboat\apps\x\package.json') -Value '{"v":1}'
        Invoke-T $script:OsSrc @('add', '-A')
        Invoke-T $script:OsSrc @('commit', '-q', '-m', 'rowboat space')
        $script:PyLog = Join-Path $script:Base 'py.log'
        $script:FakePy = Join-Path $script:Base 'fake-py.cmd'
        $script:PyExit = Join-Path $script:Base 'py-exit.txt'
        Set-Content -LiteralPath $script:PyExit -Value '0'
        Set-Content -LiteralPath $script:FakePy -Encoding ascii -Value (
            "@echo off`r`necho %*>>`"$script:PyLog`"`r`nset /p RC=<`"$script:PyExit`"`r`nexit /b %RC%`r`n")
        function Invoke-WithRowboat {
            & $script:UpdateScript -RuntimeDir $script:Runtime -RepoUrl $script:OsSrc -Npm $script:FakeNpm -Pnpm $script:FakePnpm `
                -PythonLauncher $script:FakePy -SkipPython *>&1 | Out-String
        }
        function Get-PyCalls { if (Test-Path -LiteralPath $script:PyLog) { return @(Get-Content -LiteralPath $script:PyLog) } return @() }
    }
    It 'baut beim ersten Lauf: erst --install, dann --build-embed' {
        $out = Invoke-WithRowboat
        $calls = @(Get-PyCalls)
        $calls.Count | Should -Be 2
        $calls[0] | Should -Match '-3\.12 .*start_desktop\.py --install\s*$'
        $calls[1] | Should -Match '-3\.12 .*start_desktop\.py --build-embed\s*$'
        $out | Should -Match 'Rowboat-Einbettung gebaut'
    }
    It 'baut nicht erneut, wenn sich der Rowboat-Stand nicht geaendert hat' {
        $out = Invoke-WithRowboat
        @(Get-PyCalls).Count | Should -Be 2
        $out | Should -Not -Match 'Rowboat-Einbettung'
    }
    It 'baut erneut, wenn sich die Rowboat-Kopie aendert' {
        Set-Content -LiteralPath (Join-Path $script:OsSrc 'spaces\rowboat\rowboat\apps\x\package.json') -Value '{"v":2}'
        Invoke-T $script:OsSrc @('add', '-A')
        Invoke-T $script:OsSrc @('commit', '-q', '-m', 'rowboat sync')
        Invoke-WithRowboat | Out-Null
        @(Get-PyCalls).Count | Should -Be 4
    }
    It 'Build-Fehler ist nicht fatal, wird gemeldet und beim naechsten Lauf wiederholt' {
        Set-Content -LiteralPath (Join-Path $script:OsSrc 'spaces\rowboat\scripts\start_desktop.py') -Value 'print(2)'
        Invoke-T $script:OsSrc @('add', '-A')
        Invoke-T $script:OsSrc @('commit', '-q', '-m', 'start_desktop aendert sich')
        Set-Content -LiteralPath $script:PyExit -Value '1'
        $out = Invoke-WithRowboat
        $out | Should -Match 'Rowboat-Einbettung Build fehlgeschlagen'
        Set-Content -LiteralPath $script:PyExit -Value '0'
        $out = Invoke-WithRowboat
        $out | Should -Match 'Rowboat-Einbettung gebaut'
    }
    It 'fasst Rowboat mit -SkipOverlayBuilds nicht an' {
        $before = @(Get-PyCalls).Count
        & $script:UpdateScript -RuntimeDir $script:Runtime -RepoUrl $script:OsSrc -Npm $script:FakeNpm -Pnpm $script:FakePnpm -PythonLauncher $script:FakePy -SkipPython -SkipOverlayBuilds *>&1 | Out-Null
        @(Get-PyCalls).Count | Should -Be $before
    }
}
```

- [ ] **Step 2: Tests laufen lassen, sie muessen scheitern**

Run: `cd V1; Invoke-Pester scripts/tests/rowboat-embed-launch.Tests.ps1, scripts/tests/voice-betrieb-update.Tests.ps1 -Output Detailed`
Expected: Die neuen Tests FAIL (Funktionen fehlen, keine py-Aufrufe), die alten bleiben PASS.

- [ ] **Step 3: Helfer implementieren** – in `vibemind-helpers.ps1` nach `ConvertTo-CmdSetPrefix`:

```powershell
# --- Rowboat-Einbettung (Spec 2026-10-17 rowboat-einbettung, Abschnitt 5) ----------------------

function Get-RowboatEmbedState {
    <# Laeuft eine Rowboat-Einbettung? Liest embed.json und prueft die PID. Gibt das Token nie aus. #>
    param(
        [string]$EmbedFile = (Join-Path $(if ($env:ROWBOAT_WORKDIR) { $env:ROWBOAT_WORKDIR } else { Join-Path $HOME '.rowboat' }) 'embed.json'),
        [scriptblock]$IsPidAlive = { param($p) [bool](Get-Process -Id $p -ErrorAction SilentlyContinue) }
    )
    $down = [pscustomobject]@{ Running = $false; Pid = 0; Port = 0 }
    if (-not (Test-Path -LiteralPath $EmbedFile)) { return $down }
    try { $info = Get-Content -LiteralPath $EmbedFile -Raw | ConvertFrom-Json } catch { return $down }
    $procId = [int]($info.pid)
    $port = [int]($info.port)
    if ($procId -le 0 -or $port -le 0 -or -not (& $IsPidAlive $procId)) { return $down }
    return [pscustomobject]@{ Running = $true; Pid = $procId; Port = $port }
}

function Get-RowboatEmbedLaunch {
    <#
    .SYNOPSIS
        Startzeile fuer Rowboat im Einbettungsmodus: start_desktop.py --embed aus vibemind-betrieb,
        wenn dort gebaut (scripts\voice_betrieb_update.ps1), sonst aus dem Haupt-Checkout.
    #>
    param(
        [Parameter(Mandatory)][string]$Root,
        [string]$BetriebDir = 'C:\Users\User\ClaudeWork\vibemind-betrieb',
        [string]$PythonLauncher = 'py'
    )
    $built = 'spaces\rowboat\rowboat\apps\x\apps\main\.package\dist\main.cjs'
    $osDir = if (Test-Path -LiteralPath (Join-Path $BetriebDir $built)) { $BetriebDir } else { Join-Path $Root 'vibemind-os' }
    return [pscustomobject]@{
        OsDir     = $osDir
        Exe       = $PythonLauncher
        Arguments = @('-3.12', (Join-Path $osDir 'spaces\rowboat\scripts\start_desktop.py'), '--embed', '--env-file', (Join-Path $Root '.env'))
        Built     = (Test-Path -LiteralPath (Join-Path $osDir $built))
    }
}

function Get-RowboatAutostartAction {
    <# Aufgabe bei der Anmeldung (ohne Launcher): dieselbe Startzeile wie Phase 4.5. #>
    param(
        [Parameter(Mandatory)][string]$Root,
        [string]$BetriebDir = 'C:\Users\User\ClaudeWork\vibemind-betrieb'
    )
    $script = Join-Path $BetriebDir 'spaces\rowboat\scripts\start_desktop.py'
    return [pscustomobject]@{
        TaskName         = 'VibeMind Rowboat (Einbettung)'
        Execute          = 'py'
        Argument         = ('-3.12 "{0}" --embed --env-file "{1}"' -f $script, (Join-Path $Root '.env'))
        WorkingDirectory = $BetriebDir
    }
}
```

- [ ] **Step 4: Phase 4.5 in `vibemind-start.ps1`.**
  - Parameter-Block: hinter `[switch]$SkipElectron,` die Zeile `[switch]$SkipRowboat,   # Phase 4.5: Rowboat-Einbettung nicht starten` einfuegen.
  - Doku-Kopf (bei `:19`): `      - Phase 4.5 : Rowboat-Einbettung (start_desktop.py --embed, versteckt; Spec 2026-10-17)`
  - Vor `# --- Phase 5: Electron + Debug-Agent (E1c)` einfuegen:

```powershell
# --- Phase 4.5: Rowboat-Einbettung (Spec 2026-10-17 rowboat-einbettung, Abschnitt 5) ------------
# Die voice-App startet Rowboat nicht, sie verbindet sich ueber ~/.rowboat/embed.json. Laeuft schon
# eine Einbettung, wird sie uebernommen; Single-Instance in Rowboat verhindert einen zweiten Kern.

function Invoke-Phase45-Rowboat {
    if ($SkipElectron -or $SkipRowboat) { Write-Phase "Phase 4.5: Rowboat-Einbettung (skipped)"; return }
    Write-Phase "Phase 4.5: Rowboat-Einbettung"
    $state = Get-RowboatEmbedState
    if ($state.Running) {
        Write-Ok "Rowboat laeuft schon (PID=$($state.Pid), 127.0.0.1:$($state.Port)) - uebernommen"
        return
    }
    $launch = Get-RowboatEmbedLaunch -Root $script:ROOT
    if (-not $launch.Built) {
        Write-Warn "Rowboat-Einbettung nicht gebaut in $($launch.OsDir) (run: pwsh -File scripts\voice_betrieb_update.ps1) - Roarboot zeigt Statuskarte"
        return
    }
    if ($DryRun) { Write-Warn "[DryRun] Would launch: $($launch.Exe) $($launch.Arguments -join ' ')"; return }
    $logDir = Join-Path $script:LOG_DIR 'rowboat'
    if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
    $argList = @($launch.Arguments | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } })
    $proc = Start-Process -FilePath $launch.Exe -ArgumentList $argList -WorkingDirectory $launch.OsDir `
        -RedirectStandardOutput (Join-Path $logDir 'embed.log') -RedirectStandardError (Join-Path $logDir 'embed.err.log') `
        -WindowStyle Hidden -PassThru
    Register-SpawnedProcess -Name 'rowboat_embed' -ProcId $proc.Id -Command "$($launch.Exe) $($argList -join ' ')" `
        -Port 0 -Runtime 'python' -RegistryFile $script:REGISTRY_FILE
    # Rowboat bootet in ~10-30 s. Phase 5 startet so oder so; voice verbindet sich, sobald embed.json da ist.
    $deadline = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $deadline) {
        $s = Get-RowboatEmbedState
        if ($s.Running) { Write-Ok "Rowboat-Einbettung bereit (PID=$($s.Pid), 127.0.0.1:$($s.Port))"; return }
        Start-Sleep -Seconds 2
    }
    Write-Warn "Rowboat nach 60 s noch nicht bereit - voice verbindet sich spaeter von selbst (Log: $logDir)"
}
```

  - Im Ablauf vor `Invoke-Phase5-Electron    # E1c: …` die Zeile `Invoke-Phase45-Rowboat    # Phase 4.5 — Rowboat-Einbettung (skipped via -SkipRowboat/-SkipElectron)` einfuegen.
  - Die Parameter von `Register-SpawnedProcess` (`vibemind-helpers.ps1:223`) gegenlesen. Weicht `-Runtime` oder `-Port` ab, an die vorhandene Signatur anpassen und das im Report nennen.

- [ ] **Step 5: Schritt 5c in `voice_betrieb_update.ps1`.**
  - Doku-Kopf nach Punkt 5 ergaenzen: `      5c. Rowboat-Einbettung (Spec 2026-10-17): start_desktop.py --install und --build-embed, nur bei geaendertem spaces/rowboat/rowboat oder spaces/rowboat/scripts; Fehler nicht fatal (Roarboot zeigt Statuskarte).`
  - Nach dem Laura-Block (vor `# 6. Ausgabe`) einfuegen:

```powershell
# 5c. Rowboat-Einbettung (Spec 2026-10-17 rowboat-einbettung): nur wenn sich Rowboat-Kopie oder Startskript geaendert haben
if (-not $SkipOverlayBuilds) {
    $rowboatSpace = Join-Path $RuntimeDir 'spaces\rowboat'
    $startDesktop = Join-Path $rowboatSpace 'scripts\start_desktop.py'
    $rowboatMarker = Join-Path $rowboatSpace 'rowboat\apps\x\.vbu-embed-sha'
    if (-not (Test-Path -LiteralPath $startDesktop)) {
        Write-Output 'Rowboat: start_desktop.py fehlt - uebersprungen (Roarboot zeigt Statuskarte)'
    } else {
        $rowboatSha = (Get-BlobSha $RuntimeDir 'spaces/rowboat/rowboat') + '+' + (Get-BlobSha $RuntimeDir 'spaces/rowboat/scripts')
        if ((Read-Marker $rowboatMarker) -ne $rowboatSha) {
            try {
                Invoke-Tool -Exe $PythonLauncher -Arguments @('-3.12', $startDesktop, '--install') -WorkDir $RuntimeDir
                Invoke-Tool -Exe $PythonLauncher -Arguments @('-3.12', $startDesktop, '--build-embed') -WorkDir $RuntimeDir
                Write-Marker $rowboatMarker $rowboatSha
                $neu.Add('Rowboat-Einbettung gebaut')
            } catch {
                Write-Warning "Rowboat-Einbettung: Build fehlgeschlagen - Roarboot zeigt Statuskarte ($($_.Exception.Message))"
                $neu.Add('Rowboat-Einbettung Build fehlgeschlagen')
            }
        }
    }
}
```

  - Pruefen, dass `-SkipPython` den Schritt 5c **nicht** abschaltet. `-SkipPython` gilt nur fuer die pip-Schritte 4/4b, siehe Test „fasst Rowboat mit -SkipOverlayBuilds nicht an“.

- [ ] **Step 6: `rowboat_embed_autostart.ps1` anlegen**

```powershell
#Requires -Version 7
<#
.SYNOPSIS
    Aufgabe „VibeMind Rowboat (Einbettung)“ bei der Anmeldung anlegen oder entfernen (ohne Launcher).
.DESCRIPTION
    Spec vibemind-os/docs/superpowers/specs/2026-10-17-rowboat-einbettung-design.md, Abschnitt 5.
    Startet start_desktop.py --embed aus vibemind-betrieb; die Multiverse-Ansicht verbindet sich von selbst.
.EXAMPLE
    pwsh -File scripts\rowboat_embed_autostart.ps1 -Install
    pwsh -File scripts\rowboat_embed_autostart.ps1 -Remove
#>
[CmdletBinding()]
param([switch]$Install, [switch]$Remove, [string]$BetriebDir = 'C:\Users\User\ClaudeWork\vibemind-betrieb')
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib\vibemind-helpers.ps1')
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$a = Get-RowboatAutostartAction -Root $root -BetriebDir $BetriebDir
if ($Remove) {
    Unregister-ScheduledTask -TaskName $a.TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "Aufgabe entfernt: $($a.TaskName)"
} elseif ($Install) {
    $action = New-ScheduledTaskAction -Execute $a.Execute -Argument $a.Argument -WorkingDirectory $a.WorkingDirectory
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
    Register-ScheduledTask -TaskName $a.TaskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
    Write-Output "Aufgabe angelegt: $($a.TaskName) -> $($a.Execute) $($a.Argument)"
} else {
    Write-Output "Nichts zu tun: -Install oder -Remove angeben."
}
```

Das Skript wird in dieser Task **nicht** ausgefuehrt. Eine geplante Aufgabe veraendert das System und braucht das OK des Users (Task 10).

- [ ] **Step 7: Tests laufen lassen, sie muessen bestehen**

Run: `cd V1; Invoke-Pester scripts/tests/rowboat-embed-launch.Tests.ps1, scripts/tests/voice-betrieb-update.Tests.ps1, scripts/tests/voice-electron-launch.Tests.ps1 -Output Detailed`
Expected: alle PASS. Zusaetzlich `pwsh -NoProfile -Command "[System.Management.Automation.Language.Parser]::ParseFile('scripts/vibemind-start.ps1',[ref]$null,[ref]$null) | Out-Null"` ohne Fehler. **Nicht** `vibemind-start.ps1 -DryRun` als Pruefung verwenden (nicht alle Phasen pruefen DryRun, s. Memory).

- [ ] **Step 8: Commit**

```powershell
cd V1
git status --short
git add scripts/lib/vibemind-helpers.ps1 scripts/vibemind-start.ps1 scripts/voice_betrieb_update.ps1 scripts/rowboat_embed_autostart.ps1 scripts/tests/rowboat-embed-launch.Tests.ps1 scripts/tests/voice-betrieb-update.Tests.ps1
git commit -m "feat(launcher): Rowboat-Einbettung vor Phase 5 starten, im Update-Skript bauen"
```

---

### Task 10: Zusammenfuehren, ausrollen, Abnahme (gesteuert durch den User)

Diese Task hat Wirkung ausserhalb der Worktrees: Merges, Pushes auf geteilte Branches, Pin und Laufzeit-Checkout. **Vor Step 2 das OK des Users einholen** und dabei zeigen, was gemergt und gepusht wird.

- [ ] **Step 1: Whole-Branch-Review abgeschlossen** (SDD Final Review ueber alle vier Repos), Befunde behoben.
- [ ] **Step 2 (nach OK): Fork.**
  - `git -C C:\Users\User\Desktop\rowboat fetch origin`.
  - Ist `origin/vibemind` weitergelaufen (andere Session), `feat/embed-bridge` per Merge (kein Rebase, kein Force) auf den neuen Stand bringen. Danach im Worktree Tests und Typecheck aus Task 1-4 erneut gruen.
  - Dann `vibemind` mergen und pushen. Das ist mit der anderen Session ueber das WORKBOARD abzustimmen; bei aktivem fremdem Claim auf dem Fork stoppen und den User fragen.
- [ ] **Step 3: Rowboat-Kopie synchronisieren** (vibemind-os, setup-agent-Worktree):

```powershell
cd OS
python spaces/rowboat/scripts/sync_desktop.py --fork C:\Users\User\Desktop\rowboat --fork-ref <SHA von origin/vibemind> --ehl <Klon ehl-munich-2026> --ehl-ref <bisher gepinnter EHL-SHA>
python -m pytest spaces/rowboat/tests -q
git add spaces/rowboat/rowboat
git commit -m "chore(rowboat): Rowboat-Kopie mit Einbettungsmodus synchronisieren"
```

  - Den bisherigen EHL-SHA vorher aus dem letzten Sync-Commit (`git log -1 -- spaces/rowboat/schema`) bzw. dem Sync-Marker lesen. Das Schema bleibt also unveraendert.
- [ ] **Step 4: voice.**
  - `feat/rowboat-einbettung` nach `codex/laura-video-interface-embed` mergen (no-ff) und pushen.
  - In vibemind-os den voice-Pin auf den Merge-SHA setzen. Den Pin ueber `rev-parse` + `cat-file -t` + `ls-remote` pruefen (Memory „`--cacheinfo` prueft SHA NICHT“), dann `git ls-tree HEAD voice` kontrollieren.
  - vibemind-os `master` pushen.
- [ ] **Step 5: Laufzeit-Checkout nachziehen.**
  - `pwsh -File V1\scripts\voice_betrieb_update.ps1`; erwartet „Rowboat-Einbettung gebaut“.
  - Vorher die laufende voice-App regulaer beenden. Dabei Teil-1-Abnahme Schritt 8 mitpruefen: OpenFang-PID unveraendert, keine Container beendet.
- [ ] **Step 6: Speicher vor dem Start messen.** `Get-CimInstance Win32_OperatingSystem | Select FreePhysicalMemory, TotalVisibleMemorySize` festhalten; nach dem Start von Rowboat (Einbettung) und voice erneut messen. Ergebnis in den Report.
- [ ] **Step 7: Abnahme mit dem User** (Spec, 8 Schritte), jeweils mit Beleg:
  1. Launcher startet Rowboat versteckt (Log `logs\rowboat\embed.log`, `embed.json` da), dann die Multiverse-Ansicht.
  2. Klick auf den Rowboat-Space zeigt die volle Oberflaeche; CDP `:9223` zeigt die Ansicht mit `app://-/index.html`.
  3. Notiz oeffnen und bearbeiten; ein Chat streamt live.
  4. 3D-Graph (VibeMind im Zentrum) und Plugins-Tab; ein Bild und ein Video aus dem Vault.
  5. Dateidialog oeffnen; Drag & Drop einer Datei.
  6. „In Rowboat öffnen“ (Browser-Bereich) holt Rowboats Fenster; `Get-RowboatEmbedState` zeigt dieselbe PID, es gibt keinen zweiten Kern.
  7. Rowboat ueber das Tray beenden → Statuskarte; `start_desktop.py --embed` erneut → die Ansicht verbindet sich ohne App-Neustart.
  8. Gegenprobe: `node FORK\scripts\embed-smoke.mjs $HOME\.rowboat` (4401 mit falschem Token, 401 ohne Token) und ein `fetch` mit `Origin`-Header aus der Browser-Konsole von Chrome → abgewiesen.
- [ ] **Step 8 (nur mit OK):** `pwsh -File V1\scripts\rowboat_embed_autostart.ps1 -Install`, wenn der User den Start ohne Launcher will.
- [ ] **Step 9: Abschluss.**
  - Den WORKBOARD-Eintrag `cc-rowboat-einbettung` auf **AUSGELIEFERT** setzen und nur die eigene Zeile committen.
  - Die Memory-Datei `project_voice_app_teil1.md` um den Teil-2-Stand ergaenzen (oder eine eigene `project_rowboat_einbettung.md` anlegen), plus Indexzeile.
  - Eingesetzte Worktrees erst nach Rueckfrage entfernen (Branch- und Worktree-Loeschungen brauchen das OK des Users).
