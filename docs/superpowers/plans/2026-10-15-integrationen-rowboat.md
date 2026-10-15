# Integrationen in Rowboat Desktop – Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rowboat Desktop bekommt einen Seitenleisten-Eintrag „Integrationen“. Dort verwaltet der User die OpenFang-Integrationen: Zustand ansehen, anmelden, abmelden, Schlüssel eintragen, neu verbinden, entfernen, hinzufügen. OpenFang bleibt dabei unverändert.

**Architecture:** Die Logik liegt als reines, testbares Modul in `packages/core`: OpenFang-Client, Positivliste der Felder, URL-Prüfung, Fehlerabbildung. Abhängigkeiten werden injiziert (fetch, Schlüssel, openExternal). `apps/main` verdrahtet nur IPC-Handler mit `process.env.OPENFANG_API_KEY` und `shell.openExternal`. Der Renderer bekommt eine neue Seite und einen Eintrag in der Seitenleiste. `start_desktop.py` in vibemind-os gibt genau `OPENFANG_API_KEY` an den App-Prozess weiter.

**Tech Stack:** TypeScript, Electron (main/preload/renderer), React 19, zod (IPC-Vertrag), vitest (core: node, renderer: jsdom + Testing Library), sonner (Toasts), shadcn `alert-dialog`, lucide-react. Dazu Python 3 und pytest für `start_desktop.py`.

**Spec:** `docs/superpowers/specs/2026-10-15-integrationen-rowboat-design.md` (vibemind-os `2490a25d`)

## Global Constraints

- **Wo der App-Code geändert wird:** NUR im Fork-Worktree `C:/Users/User/ClaudeWork/wt-rowboat-vibemind`, Branch `vibemind`, Start `ef2b538f`. Nie direkt unter `vibemind-os/spaces/rowboat/rowboat/apps/x`, denn der Sync-Wächter lehnt Handänderungen dort ab.
- **Pfad-Kürzel:** `X` bezeichnet unten `C:/Users/User/ClaudeWork/wt-rowboat-vibemind/apps/x`.
- **vibemind-os:** Änderungen dort (Tasks 6 und 7) im Worktree `C:/Users/User/ClaudeWork/wt-os-pins`, detached auf `origin/master`. Commit und Push gehen direkt auf `master`.
- **OpenFang-Adresse:** fest `http://127.0.0.1:4200`, weder konfigurierbar noch aus dem Renderer setzbar.
- **OpenFang-Schlüssel:** nur aus `process.env.OPENFANG_API_KEY`. Er wird nie geloggt, nie an den Renderer gegeben und nie gespeichert.
- **Integrations-IDs:** müssen `^[a-z0-9][a-z0-9-]{0,62}$` erfüllen, geprüft vor jedem Netzaufruf.
- **IPC-Kanäle:** genau `integrations:status`, `integrations:add`, `integrations:anmelden`, `integrations:abmelden`, `integrations:schluessel`, `integrations:neuVerbinden`, `integrations:entfernen`, `integrations:freigabenOeffnen`. Sie kommen NICHT in `RPC_CHANNELS` von `apps/server`, werden also nicht weitergeleitet.
- **Logs:** nur Kanal, ID, HTTP-Status und Dauer. Nie URLs, Links, Bodys oder Schlüssel.
- **Positivliste der Felder an den Renderer:**
  - `verbindung`
  - `installiert[]: {id, name, icon, zustand, detail, werkzeuge, statischerSchluessel, offeneFreigaben}`
  - `verfuegbar[]: {id, name, icon, beschreibung, kategorie}`
- **Texte:** auf Deutsch, Fehlermeldungen fest formuliert.
- **Commits:** Conventional Commits, Betreff auf Deutsch wie die bestehenden VibeMind-Patches. Jeder Commit endet mit `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- **Tests:** im Fork mit `npm run test` in den jeweiligen Paketen. Für den Typecheck vorher `npm run deps` in `X` (baut shared/core/…), danach `npm run typecheck` in `X`.

## Review Focus

1. **OpenFang startet gerade neu** und beantwortet `/api/integrations` mit Zustand `verfuegbar`. Die Karte zeigt dann „wird geprüft …“ ohne Knöpfe, nicht „Fehler“. Test in Task 4.
2. **ID mit Bindestrich** (z. B. `azure-mcp`). Freigaben werden über das Präfix `mcp_azure_mcp_` gezählt, wie in OpenFang `normalize_name`. Test in Task 1.
3. **OpenFang antwortet 200 mit unerwartetem Format**, etwa einem fehlenden Feld oder einem Array statt Objekt. Die Seite zeigt dann eine leere Liste und den Status `ok`, sie stürzt nicht ab. Test in Task 1.
4. **Doppelklick auf einen Knopf.** Solange die Aktion läuft, ist der Knopf gesperrt, damit kein zweiter Browser-Tab und kein zweiter `add` entsteht. Test in Task 4.
5. **Der Fehlertext von OpenFang enthält einen fremden Wert** (Großbuchstaben, `_`, `/`, URL). Dann erscheint die generische Meldung, nie der Text. Test in Task 1.

---

## File Structure

| Datei | Verantwortung |
|---|---|
| `X/packages/core/src/integrations/openfang-integrations.ts` (neu) | OpenFang-Client: Aufrufe, Positivliste, Freigaben zählen, URL-Prüfung, Fehlerabbildung. Keine Electron-Abhängigkeit. |
| `X/packages/core/src/integrations/openfang-integrations.test.ts` (neu) | Unit-Tests (vitest, node) |
| `X/packages/shared/src/integrations.ts` (neu) | zod-Schemas für Request und Response der Kanäle |
| `X/packages/shared/src/integrations.test.ts` (neu) | Schema-Tests (ID-Regel) |
| `X/packages/shared/src/ipc.ts` (ändern) | 8 Kanäle in `ipcSchemas` eintragen |
| `X/apps/main/src/integrations-ipc.ts` (neu) | Baut den Client mit `process.env` und `shell.openExternal` und liefert die Handler-Funktionen |
| `X/apps/main/src/ipc.ts` (ändern) | Handler in `registerIpcHandlers({...})` eintragen |
| `X/apps/renderer/src/components/integrations/integrations-page.tsx` (neu) | Seite: Hinweise, Karten, Verfügbar-Liste, Polling, Rückfragen |
| `X/apps/renderer/src/components/integrations/integration-card.tsx` (neu) | Eine Karte: Zustand → Anzeige und Knöpfe (reine Präsentation + Callbacks) |
| `X/apps/renderer/src/components/integrations/zustand.ts` (neu) | Reine Funktion `kartenModell(i)`: Zustand → Farbe, Text, Knöpfe |
| `X/apps/renderer/src/components/integrations/*.test.tsx` (neu) | Renderer-Tests |
| `X/apps/renderer/src/App.tsx` (ändern) | `ViewState`/`MiddleView` `integrations`, State, Navigation, Mount |
| `X/apps/renderer/src/components/dock-sidebar.tsx` (ändern) | Kachel „Integrationen“ unter „Apps“ |
| `vibemind-os/spaces/rowboat/scripts/start_desktop.py` (ändern) | `OPENFANG_API_KEY` aus `.env` an den App-Prozess |
| `vibemind-os/spaces/rowboat/tests/test_start_desktop.py` (ändern) | pytest dazu |

---

### Task 1: OpenFang-Client in `packages/core`

**Files:**
- Create: `X/packages/core/src/integrations/openfang-integrations.ts`
- Test: `X/packages/core/src/integrations/openfang-integrations.test.ts`

**Interfaces:**
- Produces (für Task 3 und indirekt für Task 4):
```ts
export const OPENFANG_BASE = 'http://127.0.0.1:4200'
export const INTEGRATION_ID_RE = /^[a-z0-9][a-z0-9-]{0,62}$/
export type Verbindung = 'ok' | 'openfang_aus' | 'schluessel_fehlt' | 'schluessel_falsch'
export interface InstallierteIntegration { id: string; name: string; icon: string; zustand: string; detail: string | null; werkzeuge: number; statischerSchluessel: boolean; offeneFreigaben: number }
export interface VerfuegbareVorlage { id: string; name: string; icon: string; beschreibung: string; kategorie: string }
export interface IntegrationsStatus { verbindung: Verbindung; installiert: InstallierteIntegration[]; verfuegbar: VerfuegbareVorlage[] }
export interface AktionsErgebnis { ok: boolean; zustand?: string; meldung?: string }
export interface OpenFangDeps { fetch: typeof fetch; apiKey: () => string | undefined; openExternal: (url: string) => Promise<void>; timeoutMs?: number; log?: (line: string) => void }
export interface OpenFangIntegrations {
  status(): Promise<IntegrationsStatus>
  add(id: string): Promise<AktionsErgebnis>
  anmelden(id: string): Promise<AktionsErgebnis>
  abmelden(id: string): Promise<AktionsErgebnis>
  schluessel(id: string): Promise<AktionsErgebnis>
  neuVerbinden(id: string): Promise<AktionsErgebnis>
  entfernen(id: string): Promise<AktionsErgebnis>
  freigabenOeffnen(): Promise<AktionsErgebnis>
}
export function createOpenFangIntegrations(deps: OpenFangDeps): OpenFangIntegrations
export function pruefeAnmeldeUrl(raw: string): boolean
export function pruefeSchluesselLink(raw: string, id: string): boolean
export function sichereMeldung(raw: unknown, status: number): string
```

- [ ] **Step 1: Write the failing tests**

```ts
// X/packages/core/src/integrations/openfang-integrations.test.ts
import { describe, it, expect, vi } from 'vitest'
import {
  createOpenFangIntegrations, pruefeAnmeldeUrl, pruefeSchluesselLink, sichereMeldung, OPENFANG_BASE,
} from './openfang-integrations.js'

type Route = { status: number; body?: unknown; headers?: Record<string, string> }
function fakeFetch(routes: Record<string, Route | (() => Route)>) {
  const calls: { url: string; init: RequestInit }[] = []
  const fn = vi.fn(async (url: string | URL, init: RequestInit = {}) => {
    const u = String(url)
    calls.push({ url: u, init })
    const key = `${init.method ?? 'GET'} ${u.replace(OPENFANG_BASE, '')}`
    const r = routes[key]
    if (!r) throw new TypeError('fetch failed') // = Verbindung abgelehnt
    const route = typeof r === 'function' ? r() : r
    return new Response(route.body === undefined ? null : JSON.stringify(route.body), { status: route.status, headers: route.headers })
  })
  return { fn: fn as unknown as typeof fetch, calls }
}
const INSTALLED = { count: 2, installed: [
  { id: 'github', name: 'GitHub', icon: '🐙', zustand: 'verbunden', detail: null, tool_count: 46, status: 'installed', installed_at: 'x',
    auth_headers: [{ name: 'Authorization', credential: 'INTEGRATION_GITHUB_PAT' }] },
  { id: 'vercel', name: 'Vercel', icon: '▲', zustand: 'anmeldung_noetig', detail: 'abgemeldet', tool_count: 236, status: 'installed', installed_at: 'x', auth_headers: [] },
] }
const AVAILABLE = { count: 4, integrations: [
  { id: 'github', name: 'GitHub', icon: '🐙', description: 'd', category: 'Dev Tools', transport: 'http', installed: true, admission: 'admitted' },
  { id: 'notion-remote', name: 'Notion', icon: 'N', description: 'Notizen', category: 'productivity', transport: 'http', installed: false, admission: 'admitted' },
  { id: 'aws', name: 'AWS', icon: 'a', description: 'd', category: 'cloud', transport: 'stdio', installed: false, admission: 'admitted' },
  { id: 'blocked', name: 'B', icon: 'b', description: 'd', category: 'c', transport: 'sse', installed: false, admission: { not_admitted: 'x' } },
] }
const APPROVALS = { total: 3, approvals: [
  { id: '1', tool_name: 'mcp_github_issue_write', status: 'pending' },
  { id: '2', tool_name: 'mcp_github_issue_write', status: 'rejected' },
  { id: '3', tool_name: 'mcp_vercel_create_project', status: 'pending' },
] }
function deps(routes: Record<string, Route | (() => Route)>, key: string | undefined = 'K-SECRET') {
  const f = fakeFetch(routes)
  const openExternal = vi.fn(async (_u: string) => {})
  const lines: string[] = []
  const client = createOpenFangIntegrations({ fetch: f.fn, apiKey: () => key, openExternal, log: (l) => lines.push(l) })
  return { client, calls: f.calls, openExternal, lines }
}
const OK3 = { 'GET /api/integrations': { status: 200, body: INSTALLED }, 'GET /api/integrations/available': { status: 200, body: AVAILABLE }, 'GET /api/approvals': { status: 200, body: APPROVALS } }

describe('status', () => {
  it('ruft nur 127.0.0.1:4200 mit Bearer auf und liefert nur Positivlisten-Felder', async () => {
    const { client, calls } = deps(OK3)
    const s = await client.status()
    expect(calls.every((c) => c.url.startsWith('http://127.0.0.1:4200/'))).toBe(true)
    expect(calls.every((c) => (c.init.headers as Record<string, string>).authorization === 'Bearer K-SECRET')).toBe(true)
    expect(calls.every((c) => c.init.redirect === 'manual')).toBe(true)
    expect(s.verbindung).toBe('ok')
    expect(s.installiert).toEqual([
      { id: 'github', name: 'GitHub', icon: '🐙', zustand: 'verbunden', detail: null, werkzeuge: 46, statischerSchluessel: true, offeneFreigaben: 1 },
      { id: 'vercel', name: 'Vercel', icon: '▲', zustand: 'anmeldung_noetig', detail: 'abgemeldet', werkzeuge: 0, statischerSchluessel: false, offeneFreigaben: 1 },
    ])
    expect(s.verfuegbar).toEqual([{ id: 'notion-remote', name: 'Notion', icon: 'N', beschreibung: 'Notizen', kategorie: 'productivity' }])
    const flat = JSON.stringify(s)
    for (const verboten of ['INTEGRATION_GITHUB_PAT', 'auth_headers', 'credential', 'installed_at', 'K-SECRET']) expect(flat).not.toContain(verboten)
  })
  it('zaehlt Freigaben fuer IDs mit Bindestrich ueber mcp_<id mit _>_', async () => {
    const { client } = deps({ ...OK3,
      'GET /api/integrations': { status: 200, body: { installed: [{ id: 'azure-mcp', name: 'A', icon: 'a', zustand: 'verbunden', detail: null, tool_count: 3, auth_headers: [] }] } },
      'GET /api/approvals': { status: 200, body: { approvals: [{ tool_name: 'mcp_azure_mcp_list', status: 'pending' }, { tool_name: 'mcp_azure_list', status: 'pending' }] } } })
    expect((await client.status()).installiert[0].offeneFreigaben).toBe(1)
  })
  it('ohne Schluessel: schluessel_fehlt ohne Netzaufruf', async () => {
    const { client, calls } = deps(OK3, '')
    expect((await client.status()).verbindung).toBe('schluessel_fehlt')
    expect(calls).toHaveLength(0)
  })
  it('401 -> schluessel_falsch, Verbindung abgelehnt -> openfang_aus', async () => {
    expect((await deps({ ...OK3, 'GET /api/integrations': { status: 401, body: { error: 'x' } } }).client.status()).verbindung).toBe('schluessel_falsch')
    expect((await deps({}).client.status()).verbindung).toBe('openfang_aus')
  })
  it('unerwartetes Format -> leere Listen statt Absturz', async () => {
    const { client } = deps({ 'GET /api/integrations': { status: 200, body: [1, 2] }, 'GET /api/integrations/available': { status: 200, body: { integrations: 'kaputt' } }, 'GET /api/approvals': { status: 200, body: null } })
    expect(await client.status()).toEqual({ verbindung: 'ok', installiert: [], verfuegbar: [] })
  })
  it('Weiterleitung gilt als Fehler (openfang_aus), nie folgen', async () => {
    const { client } = deps({ ...OK3, 'GET /api/integrations': { status: 302, headers: { location: 'https://evil.example/' } } })
    expect((await client.status()).verbindung).toBe('openfang_aus')
  })
})

describe('Aktionen', () => {
  it('ungueltige ID: kein Netzaufruf, Meldung', async () => {
    const { client, calls } = deps(OK3)
    for (const bad of ['', 'GitHub', '../x', 'a/b', '-x', 'a'.repeat(64), 'x?y']) {
      expect(await client.entfernen(bad)).toEqual({ ok: false, meldung: 'Ungültige Integration' })
    }
    expect(calls).toHaveLength(0)
  })
  it('anmelden oeffnet nur eine gepruefte https-URL im Browser und gibt sie nie zurueck', async () => {
    const url = 'https://vercel.com/oauth/authorize?client_id=c&state=S3CRET&code_challenge=C'
    const { client, openExternal, lines } = deps({ 'POST /api/integrations/vercel/oauth/start': { status: 200, body: { anmelde_url: url } } })
    const r = await client.anmelden('vercel')
    expect(r).toEqual({ ok: true })
    expect(openExternal).toHaveBeenCalledWith(url)
    expect(JSON.stringify(r) + lines.join('\n')).not.toContain('S3CRET')
  })
  it('anmelden mit unzulaessiger URL oeffnet nichts', async () => {
    for (const bad of ['http://vercel.com/a', 'javascript:alert(1)', 'https://u:p@vercel.com/a', 'https://1.2.3.4/a', 'https://[::1]/a', 'kaputt']) {
      const { client, openExternal } = deps({ 'POST /api/integrations/vercel/oauth/start': { status: 200, body: { anmelde_url: bad } } })
      expect(await client.anmelden('vercel')).toEqual({ ok: false, meldung: 'Ungültiger Link von OpenFang' })
      expect(openExternal).not.toHaveBeenCalled()
    }
  })
  it('schluessel oeffnet nur den exakt passenden Loopback-Link', async () => {
    const good = 'http://127.0.0.1:4200/api/integrations/github/schluessel/abcDEF123_-'
    const { client, openExternal } = deps({ 'POST /api/integrations/github/schluessel/link': { status: 200, body: { url: good } } })
    expect(await client.schluessel('github')).toEqual({ ok: true })
    expect(openExternal).toHaveBeenCalledWith(good)
  })
  it('add: anmeldung_noetig -> oauth/start, fehlt_schluessel -> schluessel/link', async () => {
    const a = deps({ 'POST /api/integrations/add': { status: 201, body: { zustand: 'anmeldung_noetig' } },
      'POST /api/integrations/vercel/oauth/start': { status: 200, body: { anmelde_url: 'https://vercel.com/oauth/authorize?x=1' } } })
    expect(await a.client.add('vercel')).toEqual({ ok: true, zustand: 'anmeldung_noetig' })
    expect(a.calls.map((c) => c.url)).toContain('http://127.0.0.1:4200/api/integrations/vercel/oauth/start')
    expect(JSON.parse(String(a.calls[0].init.body))).toEqual({ id: 'vercel' })
    const b = deps({ 'POST /api/integrations/add': { status: 201, body: { zustand: 'fehlt_schluessel' } },
      'POST /api/integrations/github/schluessel/link': { status: 200, body: { url: 'http://127.0.0.1:4200/api/integrations/github/schluessel/t' } } })
    expect(await b.client.add('github')).toEqual({ ok: true, zustand: 'fehlt_schluessel' })
    expect(b.openExternal).toHaveBeenCalledTimes(1)
    const c = deps({ 'POST /api/integrations/add': { status: 201, body: { zustand: 'verbunden' } } })
    expect(await c.client.add('x')).toEqual({ ok: true, zustand: 'verbunden' })
    expect(c.openExternal).not.toHaveBeenCalled()
  })
  it('abmelden/neuVerbinden/entfernen rufen den richtigen Endpunkt', async () => {
    const { client, calls } = deps({ 'POST /api/integrations/vercel/oauth/abmelden': { status: 200, body: { status: 'abgemeldet' } },
      'POST /api/integrations/vercel/reconnect': { status: 200, body: {} }, 'DELETE /api/integrations/vercel': { status: 200, body: {} } })
    expect(await client.abmelden('vercel')).toEqual({ ok: true })
    expect(await client.neuVerbinden('vercel')).toEqual({ ok: true })
    expect(await client.entfernen('vercel')).toEqual({ ok: true })
    expect(calls.map((c) => `${c.init.method} ${c.url.replace(OPENFANG_BASE, '')}`)).toEqual([
      'POST /api/integrations/vercel/oauth/abmelden', 'POST /api/integrations/vercel/reconnect', 'DELETE /api/integrations/vercel'])
  })
  it('409 mit fester Klasse wird gezeigt, fremder Text nie', async () => {
    const a = deps({ 'POST /api/integrations/vercel/oauth/abmelden': { status: 409, body: { error: 'tresor nicht verfuegbar' } } })
    expect(await a.client.abmelden('vercel')).toEqual({ ok: false, meldung: 'tresor nicht verfuegbar' })
    const b = deps({ 'POST /api/integrations/vercel/oauth/abmelden': { status: 409, body: { error: 'Bearer github_pat_ABC/xyz' } } })
    expect(await b.client.abmelden('vercel')).toEqual({ ok: false, meldung: 'Aktion fehlgeschlagen (HTTP 409)' })
  })
  it('freigabenOeffnen oeffnet fest das Dashboard', async () => {
    const { client, openExternal, calls } = deps({})
    expect(await client.freigabenOeffnen()).toEqual({ ok: true })
    expect(openExternal).toHaveBeenCalledWith('http://127.0.0.1:4200/#approvals')
    expect(calls).toHaveLength(0)
  })
  it('Logs enthalten nie Schluessel, URLs oder Bodys', async () => {
    const { client, lines } = deps({ ...OK3, 'POST /api/integrations/github/schluessel/link': { status: 200, body: { url: 'http://127.0.0.1:4200/api/integrations/github/schluessel/TOKEN123' } } })
    await client.status(); await client.schluessel('github')
    const all = lines.join('\n')
    for (const v of ['K-SECRET', 'TOKEN123', 'http', 'INTEGRATION_']) expect(all).not.toContain(v)
    expect(lines.length).toBeGreaterThan(0)
  })
})

describe('URL-Pruefung', () => {
  it('pruefeAnmeldeUrl', () => {
    expect(pruefeAnmeldeUrl('https://vercel.com/oauth/authorize?a=1')).toBe(true)
    for (const bad of ['http://vercel.com/', 'javascript:x', 'https://u@vercel.com/', 'https://10.0.0.1/', 'https://[::1]/', 'kaputt', 'file:///c:/x']) expect(pruefeAnmeldeUrl(bad)).toBe(false)
  })
  it('pruefeSchluesselLink', () => {
    expect(pruefeSchluesselLink('http://127.0.0.1:4200/api/integrations/github/schluessel/abc', 'github')).toBe(true)
    for (const bad of [
      'http://localhost:4200/api/integrations/github/schluessel/abc', 'http://127.0.0.1:4201/api/integrations/github/schluessel/abc',
      'https://127.0.0.1:4200/api/integrations/github/schluessel/abc', 'http://127.0.0.1:4200/api/integrations/vercel/schluessel/abc',
      'http://127.0.0.1:4200/api/integrations/github/schluessel/abc?x=1', 'http://127.0.0.1:4200/api/integrations/github/schluessel/abc#f',
      'http://127.0.0.1:4200/api/integrations/github/schluessel/', 'http://127.0.0.1:4200/api/integrations/github/schluessel/a/b',
      'http://127.0.0.1:4200/api/integrations/github/schluessel/link']) expect(pruefeSchluesselLink(bad, 'github')).toBe(false)
  })
  it('sichereMeldung', () => {
    for (const ok of ['tresor nicht verfuegbar', 'ermittlung fehlgeschlagen: fremder host', 'registrierung abgelehnt (http 400)', 'erneuerung fehlgeschlagen', 'nicht erreichbar: unzulaessiges ziel'])
      expect(sichereMeldung({ error: ok }, 409)).toBe(ok)
    for (const bad of [{ error: 'Ghp_ABC' }, { error: 'https://x' }, { error: 'a_b' }, { message: 'tresor nicht verfuegbar' }, 'tresor nicht verfuegbar', null])
      expect(sichereMeldung(bad, 500)).toBe('Aktion fehlgeschlagen (HTTP 500)')
  })
})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd X/packages/core && npx vitest run src/integrations/openfang-integrations.test.ts`
Expected: FAIL. Das Modul `./openfang-integrations.js` gibt es noch nicht.

- [ ] **Step 3: Write the implementation**

```ts
// X/packages/core/src/integrations/openfang-integrations.ts
// VibeMind: Client fuer die OpenFang-Integrationen (Teilprojekt 2, Spec vibemind-os
// docs/superpowers/specs/2026-10-15-integrationen-rowboat-design.md). Laeuft nur im
// Main-Prozess; gibt dem Renderer ausschliesslich die Positivlisten-Felder weiter.

export const OPENFANG_BASE = 'http://127.0.0.1:4200'
export const INTEGRATION_ID_RE = /^[a-z0-9][a-z0-9-]{0,62}$/
const FREIGABEN_URL = `${OPENFANG_BASE}/#approvals`
const DEFAULT_TIMEOUT_MS = 10_000

export type Verbindung = 'ok' | 'openfang_aus' | 'schluessel_fehlt' | 'schluessel_falsch'
export interface InstallierteIntegration { id: string; name: string; icon: string; zustand: string; detail: string | null; werkzeuge: number; statischerSchluessel: boolean; offeneFreigaben: number }
export interface VerfuegbareVorlage { id: string; name: string; icon: string; beschreibung: string; kategorie: string }
export interface IntegrationsStatus { verbindung: Verbindung; installiert: InstallierteIntegration[]; verfuegbar: VerfuegbareVorlage[] }
export interface AktionsErgebnis { ok: boolean; zustand?: string; meldung?: string }
export interface OpenFangDeps { fetch: typeof fetch; apiKey: () => string | undefined; openExternal: (url: string) => Promise<void>; timeoutMs?: number; log?: (line: string) => void }
export interface OpenFangIntegrations {
  status(): Promise<IntegrationsStatus>
  add(id: string): Promise<AktionsErgebnis>
  anmelden(id: string): Promise<AktionsErgebnis>
  abmelden(id: string): Promise<AktionsErgebnis>
  schluessel(id: string): Promise<AktionsErgebnis>
  neuVerbinden(id: string): Promise<AktionsErgebnis>
  entfernen(id: string): Promise<AktionsErgebnis>
  freigabenOeffnen(): Promise<AktionsErgebnis>
}

const UNGUELTIGE_ID: AktionsErgebnis = { ok: false, meldung: 'Ungültige Integration' }
const UNGUELTIGER_LINK: AktionsErgebnis = { ok: false, meldung: 'Ungültiger Link von OpenFang' }
const VERBINDUNG_MELDUNG: Record<Exclude<Verbindung, 'ok'>, string> = {
  openfang_aus: 'OpenFang läuft nicht (127.0.0.1:4200)',
  schluessel_fehlt: 'Rowboat hat keinen OpenFang-Schlüssel',
  schluessel_falsch: 'OpenFang-Schlüssel abgelehnt',
}

// Feste Fehlerklassen aus OpenFang (OAuthError::class, Kernel-Klassen). Alles andere
// wird nie angezeigt.
const SICHERE_KLASSE = new RegExp(
  '^(?:tresor nicht verfuegbar|erneuerung fehlgeschlagen|anmeldung abgebrochen|state ungueltig' +
  '|ermittlung fehlgeschlagen: [a-z0-9 ]{1,40}' +
  '|nicht erreichbar: [a-z0-9 ]{1,40}' +
  '|(?:registrierung abgelehnt|token-tausch fehlgeschlagen|erneuerung fehlgeschlagen|widerruf fehlgeschlagen) \\(http \\d{3}\\))$',
)

export function sichereMeldung(raw: unknown, status: number): string {
  const text = raw && typeof raw === 'object' && !Array.isArray(raw) ? (raw as Record<string, unknown>).error : undefined
  if (typeof text === 'string' && SICHERE_KLASSE.test(text)) return text
  return `Aktion fehlgeschlagen (HTTP ${status})`
}

function istIpLiteral(host: string): boolean {
  return host.startsWith('[') || /^\d{1,3}(\.\d{1,3}){3}$/.test(host)
}

export function pruefeAnmeldeUrl(raw: string): boolean {
  let u: URL
  try { u = new URL(raw) } catch { return false }
  return u.protocol === 'https:' && u.username === '' && u.password === '' && u.hostname !== '' && !istIpLiteral(u.hostname)
}

export function pruefeSchluesselLink(raw: string, id: string): boolean {
  let u: URL
  try { u = new URL(raw) } catch { return false }
  if (u.protocol !== 'http:' || u.hostname !== '127.0.0.1' || u.port !== '4200') return false
  if (u.search !== '' || u.hash !== '' || u.username !== '' || u.password !== '') return false
  const prefix = `/api/integrations/${id}/schluessel/`
  if (!u.pathname.startsWith(prefix)) return false
  const token = u.pathname.slice(prefix.length)
  return /^[A-Za-z0-9_-]{1,256}$/.test(token) && token !== 'link'
}

class VerbindungsFehler extends Error { constructor(readonly art: Exclude<Verbindung, 'ok'>) { super(art) } }
interface Antwort { status: number; body: unknown }

const obj = (v: unknown): Record<string, unknown> => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {})
const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : [])
const str = (v: unknown, fallback = ''): string => (typeof v === 'string' ? v : fallback)

function werkzeugPraefix(id: string): string {
  return `mcp_${id.replace(/-/g, '_')}_`
}

export function createOpenFangIntegrations(deps: OpenFangDeps): OpenFangIntegrations {
  const timeoutMs = deps.timeoutMs ?? DEFAULT_TIMEOUT_MS
  const log = deps.log ?? (() => {})

  async function rufe(method: 'GET' | 'POST' | 'DELETE', path: string, label: string, body?: unknown): Promise<Antwort> {
    const key = deps.apiKey()
    if (!key) throw new VerbindungsFehler('schluessel_fehlt')
    const start = Date.now()
    let res: Response
    try {
      res = await deps.fetch(`${OPENFANG_BASE}${path}`, {
        method,
        redirect: 'manual',
        signal: AbortSignal.timeout(timeoutMs),
        headers: { authorization: `Bearer ${key}`, ...(body === undefined ? {} : { 'content-type': 'application/json' }) },
        body: body === undefined ? undefined : JSON.stringify(body),
      })
    } catch {
      log(`integrations ${label} fehler=verbindung dauer_ms=${Date.now() - start}`)
      throw new VerbindungsFehler('openfang_aus')
    }
    log(`integrations ${label} status=${res.status} dauer_ms=${Date.now() - start}`)
    if (res.status >= 300 && res.status < 400) throw new VerbindungsFehler('openfang_aus')
    if (res.status === 401) throw new VerbindungsFehler('schluessel_falsch')
    const parsed = await res.json().catch(() => null)
    return { status: res.status, body: parsed }
  }

  async function aktion(label: string, id: string | null, f: () => Promise<AktionsErgebnis>): Promise<AktionsErgebnis> {
    if (id !== null && !INTEGRATION_ID_RE.test(id)) return UNGUELTIGE_ID
    try {
      return await f()
    } catch (e) {
      if (e instanceof VerbindungsFehler) return { ok: false, meldung: VERBINDUNG_MELDUNG[e.art] }
      log(`integrations ${label} fehler=intern`)
      return { ok: false, meldung: 'Aktion fehlgeschlagen' }
    }
  }

  const fehler = (a: Antwort): AktionsErgebnis => ({ ok: false, meldung: sichereMeldung(a.body, a.status) })
  const gut = (a: Antwort) => a.status >= 200 && a.status < 300

  async function oeffneAnmeldung(id: string): Promise<AktionsErgebnis> {
    const a = await rufe('POST', `/api/integrations/${id}/oauth/start`, `anmelden id=${id}`, {})
    if (!gut(a)) return fehler(a)
    const url = obj(a.body).anmelde_url
    if (typeof url !== 'string' || !pruefeAnmeldeUrl(url)) return UNGUELTIGER_LINK
    await deps.openExternal(url)
    return { ok: true }
  }

  async function oeffneSchluessel(id: string): Promise<AktionsErgebnis> {
    const a = await rufe('POST', `/api/integrations/${id}/schluessel/link`, `schluessel id=${id}`, {})
    if (!gut(a)) return fehler(a)
    const url = obj(a.body).url
    if (typeof url !== 'string' || !pruefeSchluesselLink(url, id)) return UNGUELTIGER_LINK
    await deps.openExternal(url)
    return { ok: true }
  }

  async function einfach(method: 'POST' | 'DELETE', path: string, label: string): Promise<AktionsErgebnis> {
    const a = await rufe(method, path, label, method === 'POST' ? {} : undefined)
    return gut(a) ? { ok: true } : fehler(a)
  }

  return {
    async status() {
      try {
        const [inst, verf, frei] = await Promise.all([
          rufe('GET', '/api/integrations', 'status'),
          rufe('GET', '/api/integrations/available', 'verfuegbar'),
          rufe('GET', '/api/approvals', 'freigaben'),
        ])
        const offen = arr(obj(frei.body).approvals).map(obj).filter((x) => x.status === 'pending').map((x) => str(x.tool_name))
        const installiert = arr(obj(inst.body).installed).map(obj)
          .filter((x) => typeof x.id === 'string' && INTEGRATION_ID_RE.test(x.id as string))
          .map((x): InstallierteIntegration => {
            const id = x.id as string
            const zustand = str(x.zustand, 'unbekannt')
            return {
              id,
              name: str(x.name, id),
              icon: str(x.icon),
              zustand,
              detail: typeof x.detail === 'string' ? x.detail : null,
              werkzeuge: zustand === 'verbunden' && typeof x.tool_count === 'number' ? x.tool_count : 0,
              statischerSchluessel: arr(x.auth_headers).length > 0,
              offeneFreigaben: offen.filter((t) => t.startsWith(werkzeugPraefix(id))).length,
            }
          })
        const verfuegbar = arr(obj(verf.body).integrations).map(obj)
          .filter((x) => typeof x.id === 'string' && INTEGRATION_ID_RE.test(x.id as string)
            && (x.transport === 'http' || x.transport === 'sse') && x.installed === false && x.admission === 'admitted')
          .map((x): VerfuegbareVorlage => ({ id: x.id as string, name: str(x.name, x.id as string), icon: str(x.icon), beschreibung: str(x.description), kategorie: str(x.category) }))
        return { verbindung: 'ok', installiert, verfuegbar }
      } catch (e) {
        if (e instanceof VerbindungsFehler) return { verbindung: e.art, installiert: [], verfuegbar: [] }
        log('integrations status fehler=intern')
        return { verbindung: 'openfang_aus', installiert: [], verfuegbar: [] }
      }
    },
    add: (id) => aktion('add', id, async () => {
      const a = await rufe('POST', '/api/integrations/add', `add id=${id}`, { id })
      if (!gut(a)) return fehler(a)
      const zustand = str(obj(a.body).zustand, 'unbekannt')
      if (zustand === 'anmeldung_noetig') { const r = await oeffneAnmeldung(id); return r.ok ? { ok: true, zustand } : { ...r, zustand } }
      if (zustand === 'fehlt_schluessel') { const r = await oeffneSchluessel(id); return r.ok ? { ok: true, zustand } : { ...r, zustand } }
      return { ok: true, zustand }
    }),
    anmelden: (id) => aktion('anmelden', id, () => oeffneAnmeldung(id)),
    abmelden: (id) => aktion('abmelden', id, () => einfach('POST', `/api/integrations/${id}/oauth/abmelden`, `abmelden id=${id}`)),
    schluessel: (id) => aktion('schluessel', id, () => oeffneSchluessel(id)),
    neuVerbinden: (id) => aktion('neuVerbinden', id, () => einfach('POST', `/api/integrations/${id}/reconnect`, `neuVerbinden id=${id}`)),
    entfernen: (id) => aktion('entfernen', id, () => einfach('DELETE', `/api/integrations/${id}`, `entfernen id=${id}`)),
    freigabenOeffnen: () => aktion('freigaben', null, async () => { await deps.openExternal(FREIGABEN_URL); return { ok: true } }),
  }
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd X/packages/core && npx vitest run src/integrations/openfang-integrations.test.ts`
Expected: PASS, alle Tests grün.

- [ ] **Step 5: Run the full core suite plus the build**

Run: `cd X/packages/core && npm test && npm run build`
Expected: Tests grün (wie vorher plus die neuen), Build ohne Fehler. `dist/integrations/openfang-integrations.js` existiert.

- [ ] **Step 6: Commit**

```bash
cd C:/Users/User/ClaudeWork/wt-rowboat-vibemind
git add apps/x/packages/core/src/integrations/
git commit -m "feat(vibemind): OpenFang-Integrationen-Client im Core (Positivliste, URL-Pruefung, feste Meldungen)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: IPC-Vertrag in `packages/shared`

**Files:**
- Create: `X/packages/shared/src/integrations.ts`
- Create: `X/packages/shared/src/integrations.test.ts`
- Modify: `X/packages/shared/src/ipc.ts`. Import neben den anderen `./*.js`-Imports (~Zeile 28), Kanäle vor dem Ende von `ipcSchemas` (vor `} as const;` bei ~4515).

**Interfaces:**
- Produces: in `ipcSchemas` die Kanäle `integrations:status` (req `z.null()`), `integrations:add|anmelden|abmelden|schluessel|neuVerbinden|entfernen` (req `{id}`) und `integrations:freigabenOeffnen` (req `z.null()`). Die Response-Typen entsprechen Task 1 (`IntegrationsStatus`, `AktionsErgebnis`).

- [ ] **Step 1: Write the failing test**

```ts
// X/packages/shared/src/integrations.test.ts
import { describe, it, expect } from 'vitest'
import { IntegrationIdReq, IntegrationsStatusSchema, AktionsErgebnisSchema } from './integrations.js'
import { ipcSchemas } from './ipc.js'

describe('Integrationen-IPC', () => {
  it('ID-Regel wie OpenFang', () => {
    expect(IntegrationIdReq.safeParse({ id: 'vercel' }).success).toBe(true)
    expect(IntegrationIdReq.safeParse({ id: 'azure-mcp' }).success).toBe(true)
    for (const bad of ['', 'Vercel', '../x', '-x', 'a'.repeat(64), 'a/b']) expect(IntegrationIdReq.safeParse({ id: bad }).success).toBe(false)
    expect(IntegrationIdReq.safeParse({ id: 'x', extra: 1 }).success).toBe(false)
  })
  it('Status-Schema laesst keine Zusatzfelder durch', () => {
    const ok = { verbindung: 'ok', installiert: [{ id: 'g', name: 'G', icon: '', zustand: 'verbunden', detail: null, werkzeuge: 1, statischerSchluessel: true, offeneFreigaben: 0 }], verfuegbar: [] }
    expect(IntegrationsStatusSchema.safeParse(ok).success).toBe(true)
    expect(IntegrationsStatusSchema.safeParse({ ...ok, installiert: [{ ...ok.installiert[0], credential: 'X' }] }).success).toBe(false)
    expect(AktionsErgebnisSchema.safeParse({ ok: true, url: 'https://x' }).success).toBe(false)
  })
  it('alle acht Kanaele sind registriert', () => {
    for (const k of ['status', 'add', 'anmelden', 'abmelden', 'schluessel', 'neuVerbinden', 'entfernen', 'freigabenOeffnen'])
      expect(Object.keys(ipcSchemas)).toContain(`integrations:${k}`)
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd X/packages/shared && npx vitest run src/integrations.test.ts`
Expected: FAIL. `./integrations.js` gibt es noch nicht.

- [ ] **Step 3: Write the implementation**

```ts
// X/packages/shared/src/integrations.ts
// VibeMind: IPC-Vertrag der Integrationen-Seite (Teilprojekt 2). Strikte Objekte:
// Zusatzfelder (Referenznamen, Links, Tokens) werden abgelehnt.
import { z } from 'zod'

export const INTEGRATION_ID = z.string().regex(/^[a-z0-9][a-z0-9-]{0,62}$/)
export const IntegrationIdReq = z.object({ id: INTEGRATION_ID }).strict()

export const InstallierteIntegrationSchema = z.object({
  id: INTEGRATION_ID,
  name: z.string(),
  icon: z.string(),
  zustand: z.string(),
  detail: z.string().nullable(),
  werkzeuge: z.number().int().nonnegative(),
  statischerSchluessel: z.boolean(),
  offeneFreigaben: z.number().int().nonnegative(),
}).strict()

export const VerfuegbareVorlageSchema = z.object({
  id: INTEGRATION_ID, name: z.string(), icon: z.string(), beschreibung: z.string(), kategorie: z.string(),
}).strict()

export const IntegrationsStatusSchema = z.object({
  verbindung: z.enum(['ok', 'openfang_aus', 'schluessel_fehlt', 'schluessel_falsch']),
  installiert: z.array(InstallierteIntegrationSchema),
  verfuegbar: z.array(VerfuegbareVorlageSchema),
}).strict()

export const AktionsErgebnisSchema = z.object({
  ok: z.boolean(), zustand: z.string().optional(), meldung: z.string().optional(),
}).strict()

export type IntegrationsStatusDto = z.infer<typeof IntegrationsStatusSchema>
export type InstallierteIntegrationDto = z.infer<typeof InstallierteIntegrationSchema>
export type VerfuegbareVorlageDto = z.infer<typeof VerfuegbareVorlageSchema>
export type AktionsErgebnisDto = z.infer<typeof AktionsErgebnisSchema>
```

In `X/packages/shared/src/ipc.ts`:
- Import oben bei den anderen `./*.js`-Imports:

```ts
import { IntegrationIdReq, IntegrationsStatusSchema, AktionsErgebnisSchema } from './integrations.js';
```

- Einträge unmittelbar vor `} as const;` am Ende von `ipcSchemas`:

```ts
  // VibeMind: Integrationen (OpenFang), Teilprojekt 2
  'integrations:status': { req: z.null(), res: IntegrationsStatusSchema },
  'integrations:add': { req: IntegrationIdReq, res: AktionsErgebnisSchema },
  'integrations:anmelden': { req: IntegrationIdReq, res: AktionsErgebnisSchema },
  'integrations:abmelden': { req: IntegrationIdReq, res: AktionsErgebnisSchema },
  'integrations:schluessel': { req: IntegrationIdReq, res: AktionsErgebnisSchema },
  'integrations:neuVerbinden': { req: IntegrationIdReq, res: AktionsErgebnisSchema },
  'integrations:entfernen': { req: IntegrationIdReq, res: AktionsErgebnisSchema },
  'integrations:freigabenOeffnen': { req: z.null(), res: AktionsErgebnisSchema },
```

Falls `packages/shared/src/index.ts` einzelne Module re-exportiert, kommt dort zusätzlich `export * as integrations from './integrations.js';` dazu, nach dem vorhandenen Muster, z. B. `rowboatApp`. Re-exportiert es nichts einzeln, wird `index.ts` nicht geändert.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd X/packages/shared && npx vitest run && npm run build`
Expected: PASS, Build ohne Fehler.

- [ ] **Step 5: Commit**

```bash
cd C:/Users/User/ClaudeWork/wt-rowboat-vibemind
git add apps/x/packages/shared/src/integrations.ts apps/x/packages/shared/src/integrations.test.ts apps/x/packages/shared/src/ipc.ts apps/x/packages/shared/src/index.ts
git commit -m "feat(vibemind): IPC-Vertrag fuer die Integrationen-Seite (strikte Schemas, ID-Regel wie OpenFang)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Handler im Main-Prozess

**Files:**
- Create: `X/apps/main/src/integrations-ipc.ts`
- Modify: `X/apps/main/src/ipc.ts`. Import oben (Muster mit `.js`-Suffix), Handler in `registerIpcHandlers({ ... })` innerhalb von `setupIpcHandlers()` (~Zeile 855).

**Interfaces:**
- Consumes: `createOpenFangIntegrations` aus `@x/core/dist/integrations/openfang-integrations.js` (Task 1) und die Kanäle aus Task 2.
- Produces: `integrationsHandlers()` liefert ein Objekt mit genau den acht Handlern.

Main hat keine eigenen Tests. Die Logik ist in Task 1 getestet. Diese Task belegt sich über Typecheck und Build: Ohne Handler für die neuen Kanäle schlägt `InvokeHandlers` beim Typecheck fehl, das ist der RED-Zustand.

- [ ] **Step 1: RED – Typecheck schlägt ohne Handler fehl**

Run: `cd X && npm run deps && npm run typecheck`
Expected: FAIL mit einem Fehler in `apps/main/src/ipc.ts`, der bemängelt, dass im Handler-Objekt für `registerIpcHandlers` die Eigenschaften `'integrations:status'` usw. fehlen (die Kanäle aus Task 2 sind bereits eingetragen).

- [ ] **Step 2: Write the implementation**

```ts
// X/apps/main/src/integrations-ipc.ts
// VibeMind: verdrahtet den OpenFang-Integrationen-Client (Core) mit Electron.
// Der OpenFang-Schluessel kommt nur aus process.env.OPENFANG_API_KEY (gesetzt von
// vibemind-os spaces/rowboat/scripts/start_desktop.py) und verlaesst diesen Prozess nie.
import { shell } from 'electron';
import { createOpenFangIntegrations } from '@x/core/dist/integrations/openfang-integrations.js';

const client = createOpenFangIntegrations({
  fetch: (input, init) => fetch(input, init),
  apiKey: () => process.env.OPENFANG_API_KEY?.trim() || undefined,
  openExternal: (url) => shell.openExternal(url),
  log: (line) => console.log(`[integrations] ${line}`),
});

export function integrationsHandlers() {
  return {
    'integrations:status': async () => client.status(),
    'integrations:add': async (_e: unknown, args: { id: string }) => client.add(args.id),
    'integrations:anmelden': async (_e: unknown, args: { id: string }) => client.anmelden(args.id),
    'integrations:abmelden': async (_e: unknown, args: { id: string }) => client.abmelden(args.id),
    'integrations:schluessel': async (_e: unknown, args: { id: string }) => client.schluessel(args.id),
    'integrations:neuVerbinden': async (_e: unknown, args: { id: string }) => client.neuVerbinden(args.id),
    'integrations:entfernen': async (_e: unknown, args: { id: string }) => client.entfernen(args.id),
    'integrations:freigabenOeffnen': async () => client.freigabenOeffnen(),
  } as const;
}
```

In `X/apps/main/src/ipc.ts`:
- Oben bei den lokalen Imports: `import { integrationsHandlers } from './integrations-ipc.js';`
- In `registerIpcHandlers({` innerhalb von `setupIpcHandlers()`, z. B. direkt nach dem Eintrag `'app:openPrivacySettings'`: `...integrationsHandlers(),`

- [ ] **Step 3: GREEN – Typecheck und Build laufen sauber**

Run: `cd X && npm run deps && npm run typecheck && cd apps/main && npm run build`
Expected: keine Fehler.

- [ ] **Step 4: Prüfen, dass nichts weitergeleitet wird**

Run: `cd X && grep -n "integrations:" apps/server/src/channels.ts`
Expected: keine Ausgabe. Die Kanäle stehen nicht in `RPC_CHANNELS`, `shouldForwardChannel` liefert also `false`.

- [ ] **Step 5: Commit**

```bash
cd C:/Users/User/ClaudeWork/wt-rowboat-vibemind
git add apps/x/apps/main/src/integrations-ipc.ts apps/x/apps/main/src/ipc.ts
git commit -m "feat(vibemind): Main-Prozess bedient die Integrationen-Kanaele (OpenFang auf 127.0.0.1:4200)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Seite „Integrationen“ im Renderer

**Files:**
- Create: `X/apps/renderer/src/components/integrations/zustand.ts`
- Create: `X/apps/renderer/src/components/integrations/integration-card.tsx`
- Create: `X/apps/renderer/src/components/integrations/integrations-page.tsx`
- Test: `X/apps/renderer/src/components/integrations/zustand.test.ts`
- Test: `X/apps/renderer/src/components/integrations/integrations-page.test.tsx`

**Interfaces:**
- Consumes: `window.ipc.invoke('integrations:…')` (Task 2/3) und die Typen `IntegrationsStatusDto`, `InstallierteIntegrationDto` (Task 2; Import aus `@x/shared/dist/integrations.js`, falls nicht über `@x/shared` re-exportiert).
- Produces: `export function IntegrationsPage(props: { visible: boolean }): JSX.Element` (für Task 5).

- [ ] **Step 1: Write the failing tests**

```ts
// X/apps/renderer/src/components/integrations/zustand.test.ts
import { describe, it, expect } from 'vitest'
import { kartenModell } from './zustand'
const basis = { id: 'x', name: 'X', icon: '', detail: null, werkzeuge: 0, statischerSchluessel: false, offeneFreigaben: 0 }

describe('kartenModell', () => {
  it.each([
    ['verbunden', false, 'gruen', 'verbunden · 5 Werkzeuge', ['abmelden', 'neuVerbinden', 'entfernen']],
    ['verbunden', true, 'gruen', 'verbunden · 5 Werkzeuge', ['neuVerbinden', 'entfernen']],
    ['anmeldung_noetig', false, 'gelb', 'Anmeldung nötig', ['anmelden', 'entfernen']],
    ['fehlt_schluessel', true, 'gelb', 'Schlüssel fehlt', ['schluessel', 'entfernen']],
    ['schluessel_abgelehnt', true, 'rot', 'Schlüssel abgelehnt', ['schluessel', 'neuVerbinden', 'entfernen']],
    ['nicht_erreichbar', true, 'rot', 'nicht erreichbar', ['neuVerbinden', 'schluessel', 'entfernen']],
    ['nicht_erreichbar', false, 'rot', 'nicht erreichbar', ['neuVerbinden', 'entfernen']],
    ['vorlage_ungueltig', false, 'grau', 'Vorlage ungültig', ['entfernen']],
    ['nicht_zugelassen', false, 'grau', 'nicht zugelassen', ['entfernen']],
    ['verfuegbar', false, 'grau', 'wird geprüft …', []],
    ['irgendwas', false, 'grau', 'wird geprüft …', []],
  ])('%s (statisch=%s)', (zustand, statisch, farbe, text, knoepfe) => {
    const m = kartenModell({ ...basis, zustand, statischerSchluessel: statisch, werkzeuge: zustand === 'verbunden' ? 5 : 0 })
    expect(m.farbe).toBe(farbe)
    expect(m.text).toBe(text)
    expect(m.knoepfe).toEqual(knoepfe)
  })
  it('haengt detail an', () => {
    expect(kartenModell({ ...basis, zustand: 'anmeldung_noetig', detail: 'abgemeldet' }).text).toBe('Anmeldung nötig · abgemeldet')
  })
})
```

```tsx
// X/apps/renderer/src/components/integrations/integrations-page.test.tsx
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { IntegrationsPage } from './integrations-page'

const invoke = vi.fn()
const STATUS = {
  verbindung: 'ok',
  installiert: [
    { id: 'github', name: 'GitHub', icon: '🐙', zustand: 'verbunden', detail: null, werkzeuge: 46, statischerSchluessel: true, offeneFreigaben: 2 },
    { id: 'vercel', name: 'Vercel', icon: '▲', zustand: 'verbunden', detail: null, werkzeuge: 236, statischerSchluessel: false, offeneFreigaben: 0 },
  ],
  verfuegbar: [{ id: 'notion-remote', name: 'Notion', icon: 'N', beschreibung: 'Notizen', kategorie: 'productivity' }],
}
beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  Object.assign(window, { ipc: { invoke } })
  invoke.mockImplementation(async (ch: string) => (ch === 'integrations:status' ? STATUS : { ok: true }))
})
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers() })

describe('IntegrationsPage', () => {
  it('zeigt Karten, Werkzeuge, Freigaben-Abzeichen und Verfuegbar-Liste', async () => {
    render(<IntegrationsPage visible />)
    expect(await screen.findByText('GitHub')).toBeInTheDocument()
    expect(screen.getByText('verbunden · 46 Werkzeuge')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '2 Freigaben offen' })).toBeInTheDocument()
    expect(screen.getByText('Notion')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '2 Freigaben offen' }))
    await waitFor(() => expect(invoke).toHaveBeenCalledWith('integrations:freigabenOeffnen', null))
  })
  it('Hinweis bei openfang_aus, schluessel_fehlt, schluessel_falsch', async () => {
    for (const [v, t] of [['openfang_aus', 'OpenFang läuft nicht (127.0.0.1:4200)'],
      ['schluessel_fehlt', 'Rowboat hat keinen gültigen OpenFang-Schlüssel – Rowboat über start_desktop.py neu starten'],
      ['schluessel_falsch', 'Rowboat hat keinen gültigen OpenFang-Schlüssel – Rowboat über start_desktop.py neu starten']]) {
      invoke.mockImplementation(async () => ({ verbindung: v, installiert: [], verfuegbar: [] }))
      render(<IntegrationsPage visible />)
      expect(await screen.findByText(t)).toBeInTheDocument()
      cleanup()
    }
  })
  it('Entfernen und Abmelden fragen erst nach', async () => {
    render(<IntegrationsPage visible />)
    await screen.findByText('Vercel')
    fireEvent.click(screen.getByRole('button', { name: 'Vercel abmelden' }))
    expect(invoke).not.toHaveBeenCalledWith('integrations:abmelden', { id: 'vercel' })
    fireEvent.click(await screen.findByRole('button', { name: 'Abmelden' }))
    await waitFor(() => expect(invoke).toHaveBeenCalledWith('integrations:abmelden', { id: 'vercel' }))
    fireEvent.click(screen.getByRole('button', { name: 'GitHub entfernen' }))
    expect(invoke).not.toHaveBeenCalledWith('integrations:entfernen', { id: 'github' })
    fireEvent.click(await screen.findByRole('button', { name: 'Entfernen' }))
    await waitFor(() => expect(invoke).toHaveBeenCalledWith('integrations:entfernen', { id: 'github' }))
  })
  it('Knopf ist waehrend der Aktion gesperrt (kein Doppelklick)', async () => {
    let resolve!: (v: unknown) => void
    invoke.mockImplementation(async (ch: string) => (ch === 'integrations:status' ? STATUS : new Promise((r) => { resolve = r })))
    render(<IntegrationsPage visible />)
    const btn = await screen.findByRole('button', { name: 'Notion hinzufügen' })
    fireEvent.click(btn); fireEvent.click(btn)
    expect(invoke.mock.calls.filter((c) => c[0] === 'integrations:add')).toHaveLength(1)
    expect(btn).toBeDisabled()
    await act(async () => { resolve({ ok: true, zustand: 'anmeldung_noetig' }) })
  })
  it('Fehlermeldung als Toast-Text sichtbar', async () => {
    invoke.mockImplementation(async (ch: string) => (ch === 'integrations:status' ? STATUS : { ok: false, meldung: 'tresor nicht verfuegbar' }))
    render(<IntegrationsPage visible />)
    fireEvent.click(await screen.findByRole('button', { name: 'GitHub neu verbinden' }))
    expect(await screen.findByText('tresor nicht verfuegbar')).toBeInTheDocument()
  })
  it('Abfrage laeuft nur, solange die Seite sichtbar ist', async () => {
    vi.useFakeTimers()
    const { rerender } = render(<IntegrationsPage visible />)
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    const n0 = invoke.mock.calls.filter((c) => c[0] === 'integrations:status').length
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000) })
    expect(invoke.mock.calls.filter((c) => c[0] === 'integrations:status').length).toBe(n0 + 1)
    rerender(<IntegrationsPage visible={false} />)
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000) })
    expect(invoke.mock.calls.filter((c) => c[0] === 'integrations:status').length).toBe(n0 + 1)
  })
})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd X/apps/renderer && npx vitest run src/components/integrations`
Expected: FAIL. Die Module gibt es noch nicht.

- [ ] **Step 3: Write `zustand.ts`**

```ts
// X/apps/renderer/src/components/integrations/zustand.ts
export type Knopf = 'anmelden' | 'abmelden' | 'schluessel' | 'neuVerbinden' | 'entfernen'
export type Farbe = 'gruen' | 'gelb' | 'rot' | 'grau'
export interface KartenEingabe { zustand: string; detail: string | null; werkzeuge: number; statischerSchluessel: boolean }
export interface KartenModell { farbe: Farbe; text: string; knoepfe: Knopf[] }

const mitDetail = (t: string, d: string | null) => (d ? `${t} · ${d}` : t)

export function kartenModell(i: KartenEingabe): KartenModell {
  switch (i.zustand) {
    case 'verbunden':
      return { farbe: 'gruen', text: `verbunden · ${i.werkzeuge} Werkzeuge`,
        knoepfe: i.statischerSchluessel ? ['neuVerbinden', 'entfernen'] : ['abmelden', 'neuVerbinden', 'entfernen'] }
    case 'anmeldung_noetig':
      return { farbe: 'gelb', text: mitDetail('Anmeldung nötig', i.detail), knoepfe: ['anmelden', 'entfernen'] }
    case 'fehlt_schluessel':
      return { farbe: 'gelb', text: 'Schlüssel fehlt', knoepfe: ['schluessel', 'entfernen'] }
    case 'schluessel_abgelehnt':
      return { farbe: 'rot', text: 'Schlüssel abgelehnt', knoepfe: ['schluessel', 'neuVerbinden', 'entfernen'] }
    case 'nicht_erreichbar':
      return { farbe: 'rot', text: mitDetail('nicht erreichbar', i.detail),
        knoepfe: i.statischerSchluessel ? ['neuVerbinden', 'schluessel', 'entfernen'] : ['neuVerbinden', 'entfernen'] }
    case 'vorlage_ungueltig':
      return { farbe: 'grau', text: 'Vorlage ungültig', knoepfe: ['entfernen'] }
    case 'nicht_zugelassen':
      return { farbe: 'grau', text: 'nicht zugelassen', knoepfe: ['entfernen'] }
    default:
      return { farbe: 'grau', text: 'wird geprüft …', knoepfe: [] }
  }
}
```

- [ ] **Step 4: Write `integration-card.tsx`**

```tsx
// X/apps/renderer/src/components/integrations/integration-card.tsx
import type { Knopf, KartenModell } from './zustand'

const KNOPF_TEXT: Record<Knopf, string> = {
  anmelden: 'Anmelden', abmelden: 'Abmelden', schluessel: 'Schlüssel eintragen', neuVerbinden: 'Neu verbinden', entfernen: 'Entfernen',
}
const KNOPF_ARIA: Record<Knopf, (name: string) => string> = {
  anmelden: (n) => `${n} anmelden`, abmelden: (n) => `${n} abmelden`, schluessel: (n) => `${n} Schlüssel eintragen`,
  neuVerbinden: (n) => `${n} neu verbinden`, entfernen: (n) => `${n} entfernen`,
}
const FARBE: Record<KartenModell['farbe'], string> = {
  gruen: 'bg-emerald-500', gelb: 'bg-amber-500', rot: 'bg-red-500', grau: 'bg-zinc-400',
}

export function IntegrationCard(props: {
  name: string; icon: string; modell: KartenModell; offeneFreigaben: number; gesperrt: boolean; ausgegraut: boolean
  onKnopf: (k: Knopf) => void; onFreigaben: () => void
}) {
  const { name, icon, modell, offeneFreigaben, gesperrt, ausgegraut } = props
  return (
    <div className={`rounded-lg border border-border bg-card p-4 flex flex-col gap-3 ${ausgegraut ? 'opacity-50' : ''}`}>
      <div className="flex items-center gap-3">
        <span className="text-xl" aria-hidden>{icon}</span>
        <span className="font-medium">{name}</span>
        {offeneFreigaben > 0 && (
          <button type="button" className="ml-auto rounded-full bg-amber-500/15 px-2 py-0.5 text-xs text-amber-600 dark:text-amber-400"
            onClick={props.onFreigaben}>{offeneFreigaben} Freigaben offen</button>
        )}
      </div>
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <span className={`inline-block h-2 w-2 rounded-full ${FARBE[modell.farbe]}`} aria-hidden />
        <span>{modell.text}</span>
      </div>
      {modell.knoepfe.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {modell.knoepfe.map((k) => (
            <button key={k} type="button" aria-label={KNOPF_ARIA[k](name)} disabled={gesperrt || ausgegraut}
              className="rounded-md border border-border px-3 py-1 text-sm hover:bg-accent disabled:opacity-50"
              onClick={() => props.onKnopf(k)}>{KNOPF_TEXT[k]}</button>
          ))}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 5: Write `integrations-page.tsx`**

```tsx
// X/apps/renderer/src/components/integrations/integrations-page.tsx
// VibeMind: Integrationen (OpenFang), Teilprojekt 2. Spec vibemind-os
// docs/superpowers/specs/2026-10-15-integrationen-rowboat-design.md
import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { IntegrationCard } from './integration-card'
import { kartenModell, type Knopf } from './zustand'

type Status = Awaited<ReturnType<typeof window.ipc.invoke<'integrations:status'>>>
const SCHNELL_MS = 3_000
const LANGSAM_MS = 10_000
const SCHNELL_DAUER_MS = 120_000
const SCHLUESSEL_HINWEIS = 'Rowboat hat keinen gültigen OpenFang-Schlüssel – Rowboat über start_desktop.py neu starten'

type Rueckfrage = { art: 'abmelden' | 'entfernen'; id: string; name: string } | null
const KANAL = {
  anmelden: 'integrations:anmelden', abmelden: 'integrations:abmelden', schluessel: 'integrations:schluessel',
  neuVerbinden: 'integrations:neuVerbinden', entfernen: 'integrations:entfernen',
} as const

export function IntegrationsPage({ visible }: { visible: boolean }) {
  const [status, setStatus] = useState<Status | null>(null)
  const [laufend, setLaufend] = useState<Set<string>>(new Set())
  const [rueckfrage, setRueckfrage] = useState<Rueckfrage>(null)
  const schnellBis = useRef(0)
  const laufendRef = useRef<Set<string>>(new Set())

  const laden = useCallback(async () => {
    try { setStatus(await window.ipc.invoke('integrations:status', null)) }
    catch { setStatus({ verbindung: 'openfang_aus', installiert: [], verfuegbar: [] }) }
  }, [])

  useEffect(() => {
    if (!visible) return
    let aus = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const runde = async () => {
      await laden()
      if (aus) return
      timer = setTimeout(runde, Date.now() < schnellBis.current ? SCHNELL_MS : LANGSAM_MS)
    }
    void runde()
    return () => { aus = true; if (timer) clearTimeout(timer) }
  }, [visible, laden])

  const ausfuehren = useCallback(async (schluessel: string, f: () => Promise<{ ok: boolean; meldung?: string }>, browser: boolean) => {
    if (laufendRef.current.has(schluessel)) return
    laufendRef.current.add(schluessel)
    setLaufend(new Set(laufendRef.current))
    try {
      const r = await f()
      if (!r.ok) toast.error(r.meldung ?? 'Aktion fehlgeschlagen')
      else if (browser) schnellBis.current = Date.now() + SCHNELL_DAUER_MS
      await laden()
    } catch {
      toast.error('Aktion fehlgeschlagen')
    } finally {
      laufendRef.current.delete(schluessel)
      setLaufend(new Set(laufendRef.current))
    }
  }, [laden])

  const knopf = (id: string, name: string, k: Knopf) => {
    if (k === 'abmelden' || k === 'entfernen') { setRueckfrage({ art: k, id, name }); return }
    void ausfuehren(id, () => window.ipc.invoke(KANAL[k], { id }), k === 'anmelden' || k === 'schluessel')
  }

  const verbindung = status?.verbindung
  const ausgegraut = verbindung !== 'ok'
  return (
    <div className="h-full overflow-auto">
      <div className="mx-auto max-w-4xl px-8 py-10 flex flex-col gap-6">
        <div>
          <h1 className="text-2xl font-semibold">Integrationen</h1>
          <p className="text-sm text-muted-foreground">Anbieter-Anbindungen über OpenFang – für alle Agenten, die sie freigeschaltet haben.</p>
        </div>
        {verbindung === 'openfang_aus' && <div role="alert" className="rounded-md border border-red-500/40 bg-red-500/10 p-3 text-sm">OpenFang läuft nicht (127.0.0.1:4200)</div>}
        {(verbindung === 'schluessel_fehlt' || verbindung === 'schluessel_falsch') && <div role="alert" className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm">{SCHLUESSEL_HINWEIS}</div>}

        <section className="flex flex-col gap-3">
          <h2 className="text-sm font-medium text-muted-foreground">Installiert</h2>
          {status?.installiert.length === 0 && <p className="text-sm text-muted-foreground">Noch keine Integration installiert.</p>}
          <div className="grid gap-3 sm:grid-cols-2">
            {status?.installiert.map((i) => (
              <IntegrationCard key={i.id} name={i.name} icon={i.icon} modell={kartenModell(i)} offeneFreigaben={i.offeneFreigaben}
                gesperrt={laufend.has(i.id)} ausgegraut={ausgegraut}
                onKnopf={(k) => knopf(i.id, i.name, k)}
                onFreigaben={() => void ausfuehren('freigaben', () => window.ipc.invoke('integrations:freigabenOeffnen', null), false)} />
            ))}
          </div>
        </section>

        <section className="flex flex-col gap-3">
          <h2 className="text-sm font-medium text-muted-foreground">Verfügbar</h2>
          {status?.verfuegbar.length === 0 && <p className="text-sm text-muted-foreground">Keine weiteren Vorlagen verfügbar.</p>}
          {status?.verfuegbar.map((v) => (
            <div key={v.id} className={`flex items-center gap-3 rounded-lg border border-border p-3 ${ausgegraut ? 'opacity-50' : ''}`}>
              <span aria-hidden>{v.icon}</span>
              <div className="flex-1"><div className="font-medium">{v.name}</div><div className="text-xs text-muted-foreground">{v.beschreibung}</div></div>
              <button type="button" aria-label={`${v.name} hinzufügen`} disabled={laufend.has(v.id) || ausgegraut}
                className="rounded-md border border-border px-3 py-1 text-sm hover:bg-accent disabled:opacity-50"
                onClick={() => void ausfuehren(v.id, () => window.ipc.invoke('integrations:add', { id: v.id }), true)}>Hinzufügen</button>
            </div>
          ))}
        </section>
      </div>

      <AlertDialog open={rueckfrage !== null} onOpenChange={(o) => { if (!o) setRueckfrage(null) }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{rueckfrage?.art === 'entfernen' ? `${rueckfrage?.name} entfernen?` : `${rueckfrage?.name} abmelden?`}</AlertDialogTitle>
            <AlertDialogDescription>
              {rueckfrage?.art === 'entfernen'
                ? 'Die Integration wird getrennt und deinstalliert. Agenten verlieren ihre Werkzeuge.'
                : 'Der Zugang wird widerrufen und aus dem Tresor gelöscht. Danach ist eine neue Anmeldung nötig.'}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Abbrechen</AlertDialogCancel>
            <AlertDialogAction onClick={() => {
              const r = rueckfrage
              setRueckfrage(null)
              if (r) void ausfuehren(r.id, () => window.ipc.invoke(KANAL[r.art], { id: r.id }), false)
            }}>{rueckfrage?.art === 'entfernen' ? 'Entfernen' : 'Abmelden'}</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
```

Der Toast-Test („Fehlermeldung als Toast-Text sichtbar“) braucht einen gerenderten `<Toaster />`. Gibt `toast.error` im Test-Setup keinen sichtbaren Text aus, wird im Test zusätzlich `import { Toaster } from '@/components/ui/sonner'` gerendert, also `render(<><Toaster /><IntegrationsPage visible /></>)`. Die Komponente selbst bleibt dabei unverändert.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd X/apps/renderer && npx vitest run src/components/integrations`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
cd C:/Users/User/ClaudeWork/wt-rowboat-vibemind
git add apps/x/apps/renderer/src/components/integrations/
git commit -m "feat(vibemind): Seite Integrationen (Zustand, Knoepfe, Rueckfragen, Freigaben-Abzeichen, Abfrage nur sichtbar)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Navigation – Eintrag in der Seitenleiste und Ansicht

**Files:**
- Modify: `X/apps/renderer/src/App.tsx`. Folgende Stellen nach dem Muster von `apps`:
  - Import ~46
  - `MiddleView` und `KEEP_ALIVE_SECTIONS` 204-211
  - `ViewState` 646-673
  - Restore-Switch ~763
  - State ~952
  - View-Ableitung ~4835
  - Titel ~4869
  - `closeAllSections` ~4902
  - Apply-Switch ~5324
  - Callback ~5494
  - `navigateToNamedView` ~5929
  - Befehlspalette ~6692
  - Tour ~6767
  - `activeMiddle` ~7254
  - `activeNav` und `sidebarNavProps` ~7284-7303
  - Mount ~7665
- Modify: `X/apps/renderer/src/components/dock-sidebar.tsx`
  - `DockSidebarProps`: `onOpenIntegrations?: () => void` dazu, `activeNav` um `'integrations'` erweitern
  - Kachel nach `apps` (~1024-1030)
  - Memo-Abhängigkeiten ~1064
  - Weitergabe an das Kind ~1459, nach dem Muster von `onOpenApps`
- Test: `X/apps/renderer/src/components/dock-sidebar.integrations.test.tsx` (neu)

**Interfaces:**
- Consumes: `IntegrationsPage({ visible })` aus Task 4.
- Produces: View-Typ `{ type: 'integrations' }`, Middle-View `'integrations'`, Nav-Key `'integrations'`, Prop `onOpenIntegrations`.

- [ ] **Step 1: Write the failing test**

```tsx
// X/apps/renderer/src/components/dock-sidebar.integrations.test.tsx
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DockSidebar } from './dock-sidebar'

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  Object.assign(window, { ipc: { invoke: vi.fn().mockResolvedValue(null), on: vi.fn(() => () => {}), send: vi.fn() } })
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('DockSidebar Integrationen', () => {
  it('zeigt die Kachel Integrationen und ruft onOpenIntegrations', () => {
    const onOpenIntegrations = vi.fn()
    render(<DockSidebar onOpenIntegrations={onOpenIntegrations} activeNav="integrations" />)
    fireEvent.click(screen.getByText('Integrationen'))
    expect(onOpenIntegrations).toHaveBeenCalledTimes(1)
  })
})
```

Braucht `DockSidebar` beim Rendern weitere Pflicht-Props oder einen Kontext-Provider, setzt der Implementierer sie im Test genauso, wie es andere Aufrufer oder Tests von `DockSidebar` tun. Das Prüfziel bleibt unverändert: Die Kachel ist sichtbar, und ein Klick ruft `onOpenIntegrations` auf.

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd X/apps/renderer && npx vitest run src/components/dock-sidebar.integrations.test.tsx`
Expected: FAIL, weil der Text „Integrationen“ nicht gefunden wird.

- [ ] **Step 3: Kachel in `dock-sidebar.tsx`**

- `import { Plug } from 'lucide-react'` zur bestehenden lucide-Importliste hinzufügen.
- In `DockSidebarProps`: `onOpenIntegrations?: () => void` dazu und `activeNav` um `| 'integrations'` erweitern.
- Direkt nach dem Apps-Eintrag (`key: 'apps' …`) und vor den angehefteten Apps diesen Eintrag einfügen:

```tsx
    {
      item: {
        key: 'integrations', label: 'Integrationen', icon: Plug, tourId: 'nav-integrations',
        running: activeNav === 'integrations',
        onClick: () => { closeFlyouts(); onOpenIntegrations?.() },
      },
    },
```

- `onOpenIntegrations` in die Memo-Abhängigkeiten (~1064) und überall dorthin, wo `onOpenApps` an ein Kind weitergereicht wird (~1459), auf dieselbe Weise.

- [ ] **Step 4: Ansicht in `App.tsx`**

Jede Stelle bekommt `integrations` nach dem Muster von `apps`:

```tsx
// Import
import { IntegrationsPage } from '@/components/integrations/integrations-page'
// MiddleView: | 'integrations'   und in KEEP_ALIVE_SECTIONS: 'integrations'
// ViewState: | { type: 'integrations' }
// Restore-Switch:  case 'integrations': return { type: 'integrations' }
// State:           const [isIntegrationsOpen, setIsIntegrationsOpen] = useState(false)
// View-Ableitung:  if (isIntegrationsOpen) return { type: 'integrations' }
// Titel:           case 'integrations': return 'Integrationen'
// closeAllSections: setIsIntegrationsOpen(false)
// Apply-Switch:    case 'integrations': setIsIntegrationsOpen(true); return
// Callback:
const openIntegrationsView = useCallback(() => { void navigateToView({ type: 'integrations' }) }, [navigateToView])
// navigateToNamedView, Befehlspalette, Tour: case 'integrations': openIntegrationsView(); break
//   (Tour-Abhaengigkeitsliste um openIntegrationsView ergaenzen)
// activeMiddle:    : isIntegrationsOpen ? 'integrations'
// activeNav:       : isIntegrationsOpen ? 'integrations'   und Typ-Union um 'integrations' erweitern
// sidebarNavProps: onOpenIntegrations: openIntegrationsView,
// Mount (nach dem Apps-Block):
{sectionMounted('integrations') && (
  <KeepAliveSection visible={activeMiddle === 'integrations'}>
    <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
      <IntegrationsPage visible={activeMiddle === 'integrations'} />
    </div>
  </KeepAliveSection>
)}
```

Passt ein `switch` über `ViewState` oder `MiddleView` nicht zu diesem Muster, zum Beispiel weil er exhaustiv ist und einen `never`-Check hat, meldet `npm run typecheck` die fehlende Stelle. Dort wird `integrations` dann analog zu `apps` ergänzt.

- [ ] **Step 5: Tests, Typecheck und komplette Renderer-Suite**

Run: `cd X && npm run deps && npm run typecheck && cd apps/renderer && npm test`
Expected: Typecheck sauber, alle Renderer-Tests grün (die bisherigen 1295 plus die neuen).

- [ ] **Step 6: Commit**

```bash
cd C:/Users/User/ClaudeWork/wt-rowboat-vibemind
git add apps/x/apps/renderer/src/App.tsx apps/x/apps/renderer/src/components/dock-sidebar.tsx apps/x/apps/renderer/src/components/dock-sidebar.integrations.test.tsx
git commit -m "feat(vibemind): Seitenleisten-Eintrag Integrationen unter Apps

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: `start_desktop.py` gibt `OPENFANG_API_KEY` weiter (vibemind-os)

**Files:**
- Modify: `C:/Users/User/ClaudeWork/wt-os-pins/spaces/rowboat/scripts/start_desktop.py`
- Test: `C:/Users/User/ClaudeWork/wt-os-pins/spaces/rowboat/tests/test_start_desktop.py`

**Interfaces:**
- Produces: `read_env_value(path: Path, name: str) -> str | None` und `DEFAULT_ENV_FILE`. `start(lay, workdir, windows, env_file)` setzt `OPENFANG_API_KEY` nur im Env der Jobs.

- [ ] **Step 1: Write the failing tests** (unten an `test_start_desktop.py` anhängen)

```python
def test_read_env_value_takes_only_the_named_value(tmp_path):
    env = tmp_path / ".env"
    env.write_text('A=1\nOPENFANG_API_KEY = "sek-ret"\n# OPENFANG_API_KEY=alt\nOPENFANG_API_KEY_X=nein\n', encoding="utf-8")
    assert start_desktop.read_env_value(env, "OPENFANG_API_KEY") == "sek-ret"
    assert start_desktop.read_env_value(env, "FEHLT") is None
    assert start_desktop.read_env_value(tmp_path / "gibtsnicht", "OPENFANG_API_KEY") is None


def test_start_passes_only_the_openfang_key_and_never_prints_it(tmp_path, monkeypatch, caplog, capsys):
    _prereqs(tmp_path, windows=True)
    env = tmp_path / ".env"
    env.write_text("OPENFANG_API_KEY=sek-ret\nANDERES=geheim\n", encoding="utf-8")
    seen = []
    monkeypatch.setattr(start_desktop.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 0})())
    def fake_popen(cmd, cwd=None, env=None, **k):
        seen.append(env)
        return FakeProc(code=0)
    monkeypatch.setattr(start_desktop.subprocess, "Popen", fake_popen)
    monkeypatch.delenv("ANDERES", raising=False)
    caplog.set_level("INFO")
    start_desktop.start(layout(tmp_path), tmp_path, windows=True, env_file=env)
    assert seen and all(e["OPENFANG_API_KEY"] == "sek-ret" for e in seen)
    assert all("ANDERES" not in e for e in seen)
    out = capsys.readouterr()
    assert "sek-ret" not in caplog.text + out.out + out.err


def test_start_warns_and_runs_without_key(tmp_path, monkeypatch, caplog):
    _prereqs(tmp_path, windows=True)
    monkeypatch.setattr(start_desktop.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 0})())
    started = []
    monkeypatch.setattr(start_desktop.subprocess, "Popen", lambda *a, **k: started.append(k.get("env")) or FakeProc(code=0))
    monkeypatch.delenv("OPENFANG_API_KEY", raising=False)
    caplog.set_level("WARNING")
    start_desktop.start(layout(tmp_path), tmp_path, windows=True, env_file=tmp_path / "fehlt.env")
    assert started, "Rowboat muss trotzdem starten"
    assert "OPENFANG_API_KEY nicht gefunden" in caplog.text
```

Die Signaturen von `FakeProc(code=...)` und `_prereqs(...)` richten sich nach den vorhandenen Helfern in der Datei (Zeilen 16-42). Heißt ein Parameter dort anders, wird der Test daran angepasst. Das Prüfziel bleibt gleich.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd C:/Users/User/ClaudeWork/wt-os-pins && python -m pytest spaces/rowboat/tests/test_start_desktop.py -q`
Expected: FAIL. `read_env_value` gibt es nicht, und `start()` kennt keinen Parameter `env_file`.

- [ ] **Step 3: Write the implementation**

In `start_desktop.py`:
- Nach `POLL_SECONDS` einfügen:

```python
DEFAULT_ENV_FILE = SPACE.parents[2] / ".env"   # Vibemind_V1/.env (Space liegt unter vibemind-os/spaces/rowboat)
OPENFANG_KEY = "OPENFANG_API_KEY"


def read_env_value(path: Path, name: str) -> str | None:
    """Liest genau einen Wert aus einer .env-Datei. Gibt sonst nichts weiter."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, _, value = s.partition("=")
        if key.strip() == name:
            value = value.strip().strip('"').strip("'")
            return value or None
    return None
```

- Signatur von `start` ändern zu `def start(lay: Layout, workdir: Path, windows: bool = IS_WINDOWS, env_file: Path = DEFAULT_ENV_FILE) -> int:`
- Die Zeile `env = {**os.environ, "ROWBOAT_WORKDIR": str(workdir)}` ersetzen durch:

```python
    env = {**os.environ, "ROWBOAT_WORKDIR": str(workdir)}
    key = read_env_value(env_file, OPENFANG_KEY) or os.environ.get(OPENFANG_KEY)
    if key:
        env[OPENFANG_KEY] = key
    else:
        log.warning("%s nicht gefunden – Integrationen-Seite zeigt Hinweis", OPENFANG_KEY)
```

- In `main()` ergänzen:
  - `ap.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE, help="Datei, aus der nur OPENFANG_API_KEY gelesen wird")`
  - den Aufruf durch `return start(lay, args.workdir.expanduser().resolve(), env_file=args.env_file)` ersetzen.

`DEFAULT_ENV_FILE` prüfen: `SPACE` ist `.../vibemind-os/spaces/rowboat`, also ist `SPACE.parents[2]` gleich `.../Vibemind_V1`. Im Worktree `wt-os-pins` gibt es dort keine `.env`. Das ist korrekt, denn dann gilt der Fallback auf `os.environ` und die Warnung.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd C:/Users/User/ClaudeWork/wt-os-pins && python -m pytest spaces/rowboat/tests -q`
Expected: PASS, alle Tests in `spaces/rowboat/tests` grün.

- [ ] **Step 5: Commit** (lokal, noch nicht pushen, siehe Task 7)

```bash
cd C:/Users/User/ClaudeWork/wt-os-pins
git add spaces/rowboat/scripts/start_desktop.py spaces/rowboat/tests/test_start_desktop.py
git commit -m "feat(rowboat): start_desktop gibt nur OPENFANG_API_KEY an Rowboat weiter (Integrationen-Seite)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Fork pushen, nach vibemind-os synchronisieren, ausrollen, Live-Nachweis

**Files:**
- Modify (per Skript): `vibemind-os/spaces/rowboat/rowboat/apps/x/**` und `vibemind-os/spaces/rowboat/rowboat/UPSTREAM.md`
- Modify: `vibemind-os/E2E-PROOF.md` (Teil X)

- [ ] **Step 1: Fork pushen**

```bash
cd C:/Users/User/ClaudeWork/wt-rowboat-vibemind
git status --short   # erwartet: leer
git push origin vibemind
git ls-remote origin refs/heads/vibemind   # erwartet: HEAD-SHA des Worktrees
```

- [ ] **Step 2: Sync nach vibemind-os**

Wie `sync_desktop.py` genau aufgerufen wird, steht in `spaces/rowboat/README.md`; Pflicht sind `--fork` und `--ehl`. Den EHL-Klon und dessen Ref ermittelt der Implementierer aus `rowboat/UPSTREAM.md`, und zwar den gepinnten EHL-Commit `44c1024c…` unverändert.

```bash
cd C:/Users/User/ClaudeWork/wt-os-pins
git fetch -q origin master && git rebase origin/master   # Task-6-Commit obenauf
python spaces/rowboat/scripts/sync_desktop.py --fork C:/Users/User/ClaudeWork/wt-rowboat-vibemind --fork-ref vibemind --ehl <EHL-Klon> --ehl-ref <gepinnter EHL-Commit>
git status --short   # erwartet: nur apps/x/** und UPSTREAM.md geaendert
python -m pytest spaces/rowboat/tests -q   # erwartet: gruen
git add spaces/rowboat
git commit -m "chore(rowboat): Desktop-Sync - Integrationen-Seite (Fork vibemind <SHA>)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
git push origin HEAD:master
```

- [ ] **Step 3: STOP – Freigabe des Users einholen** für den Neustart von Rowboat Desktop. Danach im vibemind-os-Haupt-Checkout (`Vibemind_V1/vibemind-os`, detached auf den neuen `master`, vorher `git status` prüfen) in dieser Reihenfolge:
  1. Das laufende Rowboat beenden.
  2. Nur wenn sich Abhängigkeiten geändert haben: `python spaces/rowboat/scripts/start_desktop.py --install`.
  3. `python spaces/rowboat/scripts/start_desktop.py` starten. Die Ausgabe enthält keine Warnung „OPENFANG_API_KEY nicht gefunden“.

- [ ] **Step 4: Live-Nachweis** (mit dem User; Spec §6.4)
  1. Seitenleiste → „Integrationen“: GitHub `verbunden · 46 Werkzeuge`, Vercel `verbunden · 236 Werkzeuge`.
  2. „Vercel abmelden“ → Rückfrage → bestätigen → Karte zeigt „Anmeldung nötig · abgemeldet“.
  3. „Vercel anmelden“ → Standardbrowser öffnet Vercel → User meldet sich an → Karte springt ohne Neuladen auf `verbunden`.
  4. „GitHub Schlüssel eintragen“ → Einmal-Seite öffnet im Browser. Dort nichts eintragen, sie läuft nach 10 Minuten ab.
  5. Katalog-Fluss nur mit User-OK: „Vercel entfernen“ → Rückfrage → Vercel erscheint unter „Verfügbar“ → „Hinzufügen“ → Browser-Login → `verbunden`.
  6. Hinweis „OpenFang läuft nicht“ nur mit User-OK: OpenFang über die Watchdog-Aufgabe kurz stoppen → Hinweis erscheint, Karten ausgegraut → Watchdog-Aufgabe wieder starten → Seite erholt sich von selbst.
  7. Leck-Prüfung: Rowboat-Konsole/Logs und `~/.rowboat` (nur Textdateien, die seit dem Start geändert wurden) enthalten 0 Treffer für `github_pat_`, `ghp_`, `vca_`, `vcr_`, `state=`, `/schluessel/` und den Wert von `OPENFANG_API_KEY`. Der Wert wird dabei nur per Python-Vergleich geprüft und nie ausgegeben.

- [ ] **Step 5: Abschluss**
  - `E2E-PROOF.md` Teil X (Tabelle wie Teil IX).
  - Claims in WORKBOARD und Koordinationsdatei schließen.
  - Notiz `project_openfang_integrationen_kern.md` aktualisieren.
  - Commit und Push in vibemind-os `master`.
