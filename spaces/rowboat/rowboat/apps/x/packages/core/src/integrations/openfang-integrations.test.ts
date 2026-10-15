import { describe, it, expect, vi } from 'vitest'
import {
  createOpenFangIntegrations, pruefeAnmeldeUrl, pruefeSchluesselLink, sichereMeldung, takeOpenFangKey, OPENFANG_BASE,
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
  it('tool_count ausserhalb Ganzzahl >= 0 ergibt werkzeuge 0', async () => {
    for (const tc of [-1, 2.5, NaN, Infinity, '46']) {
      const { client } = deps({ ...OK3,
        'GET /api/integrations': { status: 200, body: { installed: [{ id: 'g', name: 'G', icon: 'g', zustand: 'verbunden', detail: null, tool_count: tc, auth_headers: [] }] } } })
      expect((await client.status()).installiert[0].werkzeuge).toBe(0)
    }
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

describe('Fix round 1', () => {
  it('500 auf /api/integrations -> openfang_aus, leere Listen', async () => {
    const s = await deps({ ...OK3, 'GET /api/integrations': { status: 500, body: {} } }).client.status()
    expect(s).toEqual({ verbindung: 'openfang_aus', installiert: [], verfuegbar: [] })
  })
  it('404 auf /available -> openfang_aus', async () => {
    const s = await deps({ ...OK3, 'GET /api/integrations/available': { status: 404, body: {} } }).client.status()
    expect(s).toEqual({ verbindung: 'openfang_aus', installiert: [], verfuegbar: [] })
  })
  it('500 auf /api/approvals -> ok, installiert intakt, offeneFreigaben 0', async () => {
    const s = await deps({ ...OK3, 'GET /api/approvals': { status: 500, body: {} } }).client.status()
    expect(s.verbindung).toBe('ok')
    expect(s.installiert).toHaveLength(2)
    expect(s.installiert.map((i) => i.offeneFreigaben)).toEqual([0, 0])
  })
  it('opaqueredirect / status 0 -> openfang_aus', async () => {
    const fake = (async () => ({ status: 0, type: 'opaqueredirect', json: async () => ({}) })) as unknown as typeof fetch
    const client = createOpenFangIntegrations({ fetch: fake, apiKey: () => 'K', openExternal: async () => {} })
    expect((await client.status()).verbindung).toBe('openfang_aus')
  })
  it('sichereMeldung: strengere Klassen', () => {
    for (const bad of ['ermittlung fehlgeschlagen: abc123def456', 'nicht erreichbar: a1b2'])
      expect(sichereMeldung({ error: bad }, 500)).toBe('Aktion fehlgeschlagen (HTTP 500)')
    expect(sichereMeldung({ error: 'ermittlung fehlgeschlagen: keine client_id' }, 409)).toBe('ermittlung fehlgeschlagen: keine client_id')
  })
  it('add: Folgeaufruf scheitert -> ok false mit zustand', async () => {
    const { client } = deps({ 'POST /api/integrations/add': { status: 201, body: { zustand: 'anmeldung_noetig' } } })
    const r = await client.add('vercel')
    expect(r.ok).toBe(false)
    expect(r.zustand).toBe('anmeldung_noetig')
    expect(typeof r.meldung).toBe('string')
  })
})

describe('Final fix', () => {
  it('I1 takeOpenFangKey: liefert den Wert und entfernt ihn aus env', () => {
    const env: Record<string, string | undefined> = { OPENFANG_API_KEY: '  FAKE-KEY-1  ', ANDERES: 'x' }
    expect(takeOpenFangKey(env)).toBe('FAKE-KEY-1')
    expect('OPENFANG_API_KEY' in env).toBe(false)
    expect(env.ANDERES).toBe('x')
  })
  it('I1 takeOpenFangKey: leer/Leerzeichen -> undefined und trotzdem entfernt', () => {
    for (const v of ['', '   ']) {
      const env: Record<string, string | undefined> = { OPENFANG_API_KEY: v }
      expect(takeOpenFangKey(env)).toBeUndefined()
      expect('OPENFANG_API_KEY' in env).toBe(false)
    }
  })
  it('I1 takeOpenFangKey: fehlt -> undefined', () => {
    const env: Record<string, string | undefined> = {}
    expect(takeOpenFangKey(env)).toBeUndefined()
    expect('OPENFANG_API_KEY' in env).toBe(false)
  })
  it('M1 detail nur bei anmeldung_noetig und nicht_erreichbar', async () => {
    const { client } = deps({ ...OK3, 'GET /api/integrations': { status: 200, body: { installed: [
      { id: 'github', name: 'G', icon: 'g', zustand: 'fehlt_schluessel', detail: 'fehlende Schluessel: INTEGRATION_GITHUB_PAT', tool_count: 0, auth_headers: [] },
      { id: 'vercel', name: 'V', icon: 'v', zustand: 'anmeldung_noetig', detail: 'abgemeldet', tool_count: 0, auth_headers: [] },
      { id: 'linear', name: 'L', icon: 'l', zustand: 'nicht_erreichbar', detail: 'zeitueberschreitung', tool_count: 0, auth_headers: [] },
      { id: 'notion', name: 'N', icon: 'n', zustand: 'verbunden', detail: 'irgendwas', tool_count: 1, auth_headers: [] },
    ] } } })
    const s = await client.status()
    expect(s.installiert.map((i) => i.detail)).toEqual([null, 'abgemeldet', 'zeitueberschreitung', null])
    const flat = JSON.stringify(s)
    expect(flat).not.toContain('INTEGRATION_GITHUB_PAT')
    expect(flat).not.toContain('irgendwas')
  })
  it('M3 anmelden oeffnet die normalisierte, gepruefte URL', async () => {
    const { client, openExternal } = deps({ 'POST /api/integrations/vercel/oauth/start': { status: 200, body: { anmelde_url: 'https://Vercel.COM/oauth/authorize?x=1' } } })
    expect(await client.anmelden('vercel')).toEqual({ ok: true })
    expect(openExternal).toHaveBeenCalledWith('https://vercel.com/oauth/authorize?x=1')
  })
  it('M3 schluessel oeffnet die normalisierte, gepruefte URL', async () => {
    const { client, openExternal } = deps({ 'POST /api/integrations/github/schluessel/link': { status: 200, body: { url: 'HTTP://127.0.0.1:4200/api/integrations/github/schluessel/abc' } } })
    expect(await client.schluessel('github')).toEqual({ ok: true })
    expect(openExternal).toHaveBeenCalledWith('http://127.0.0.1:4200/api/integrations/github/schluessel/abc')
  })
  it('M4 Positivliste: anmelde_url und token einer installierten Integration fehlen', async () => {
    const { client } = deps({ ...OK3, 'GET /api/integrations': { status: 200, body: { installed: [
      { id: 'vercel', name: 'V', icon: 'v', zustand: 'anmeldung_noetig', detail: null, tool_count: 0, auth_headers: [],
        anmelde_url: 'https://x.example/?state=STATE-XYZ', token: 'TOKEN-XYZ' },
    ] } } })
    const flat = JSON.stringify(await client.status())
    for (const verboten of ['anmelde_url', 'STATE-XYZ', 'TOKEN-XYZ', 'token']) expect(flat).not.toContain(verboten)
  })
})
