// VibeMind: Client fuer die OpenFang-Integrationen (Teilprojekt 2, Spec vibemind-os
// docs/superpowers/specs/2026-10-15-integrationen-rowboat-design.md). Laeuft nur im
// Main-Prozess; gibt dem Renderer ausschliesslich die Positivlisten-Felder weiter.

export const OPENFANG_BASE = 'http://127.0.0.1:4200'
export const INTEGRATION_ID_RE = /^[a-z0-9][a-z0-9-]{0,62}$/
const FREIGABEN_URL = `${OPENFANG_BASE}/#approvals`
const DEFAULT_TIMEOUT_MS = 10_000
const DEFAULT_AKTIONS_TIMEOUT_MS = 60_000

export type Verbindung = 'ok' | 'openfang_aus' | 'schluessel_fehlt' | 'schluessel_falsch'
export interface InstallierteIntegration { id: string; name: string; icon: string; zustand: string; detail: string | null; werkzeuge: number; statischerSchluessel: boolean; offeneFreigaben: number }
export interface VerfuegbareVorlage { id: string; name: string; icon: string; beschreibung: string; kategorie: string }
export interface IntegrationsStatus { verbindung: Verbindung; installiert: InstallierteIntegration[]; verfuegbar: VerfuegbareVorlage[] }
export interface AktionsErgebnis { ok: boolean; zustand?: string; meldung?: string }
export interface OpenFangDeps { fetch: typeof fetch; apiKey: () => string | undefined; openExternal: (url: string) => Promise<void>; timeoutMs?: number; aktionsTimeoutMs?: number; log?: (line: string) => void }
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
  '|ermittlung fehlgeschlagen: (?:fremder host|iss passt nicht|issuer passt nicht|kein anmeldeserver|kein s256|keine client_id' +
  '|keine selbstregistrierung|resource passt nicht|schutzbeschreibung fremder host|schutzbeschreibung nicht lesbar' +
  '|ungueltige url|unsicheres schema|unzulaessiger host|weiterleitung)' +
  '|nicht erreichbar: [a-z ]{1,30}' +
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

function parseUrl(raw: string): URL | null {
  try { return new URL(raw) } catch { return null }
}

// Liefert die geparste, gepruefte URL; geoeffnet wird genau deren href (nicht der Rohtext).
function gepruefteAnmeldeUrl(raw: string): URL | null {
  const u = parseUrl(raw)
  if (!u) return null
  return u.protocol === 'https:' && u.username === '' && u.password === '' && u.hostname !== '' && !istIpLiteral(u.hostname) ? u : null
}

function gepruefterSchluesselLink(raw: string, id: string): URL | null {
  const u = parseUrl(raw)
  if (!u) return null
  if (u.protocol !== 'http:' || u.hostname !== '127.0.0.1' || u.port !== '4200') return null
  if (u.search !== '' || u.hash !== '' || u.username !== '' || u.password !== '') return null
  const prefix = `/api/integrations/${id}/schluessel/`
  if (!u.pathname.startsWith(prefix)) return null
  const token = u.pathname.slice(prefix.length)
  return /^[A-Za-z0-9_-]{1,256}$/.test(token) && token !== 'link' ? u : null
}

export function pruefeAnmeldeUrl(raw: string): boolean {
  return gepruefteAnmeldeUrl(raw) !== null
}

export function pruefeSchluesselLink(raw: string, id: string): boolean {
  return gepruefterSchluesselLink(raw, id) !== null
}

// Liest OPENFANG_API_KEY genau einmal und entfernt ihn IMMER aus env (auch leer), damit
// kein Kindprozess (rowboat-server, Terminal, Agenten-Shell, ACP-Agenten) ihn erbt.
export function takeOpenFangKey(env: Record<string, string | undefined>): string | undefined {
  const raw = env.OPENFANG_API_KEY
  delete env.OPENFANG_API_KEY
  const key = typeof raw === 'string' ? raw.trim() : ''
  return key === '' ? undefined : key
}

// Nur diese Zustaende zeigen detail an; bei anderen (z. B. fehlt_schluessel) setzt OpenFang
// Referenznamen wie INTEGRATION_GITHUB_PAT hinein.
const ZUSTAENDE_MIT_DETAIL = new Set(['anmeldung_noetig', 'nicht_erreichbar'])

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
  const aktionsTimeoutMs = deps.aktionsTimeoutMs ?? DEFAULT_AKTIONS_TIMEOUT_MS
  const log = deps.log ?? (() => {})

  async function rufe(zeitMs: number, method: 'GET' | 'POST' | 'DELETE', path: string, label: string, body?: unknown): Promise<Antwort> {
    const key = deps.apiKey()
    if (!key) throw new VerbindungsFehler('schluessel_fehlt')
    const start = Date.now()
    let res: Response
    try {
      res = await deps.fetch(`${OPENFANG_BASE}${path}`, {
        method,
        redirect: 'manual',
        signal: AbortSignal.timeout(zeitMs),
        headers: { authorization: `Bearer ${key}`, ...(body === undefined ? {} : { 'content-type': 'application/json' }) },
        body: body === undefined ? undefined : JSON.stringify(body),
      })
    } catch {
      log(`integrations ${label} fehler=verbindung dauer_ms=${Date.now() - start}`)
      throw new VerbindungsFehler('openfang_aus')
    }
    log(`integrations ${label} status=${res.status} dauer_ms=${Date.now() - start}`)
    if (res.type === 'opaqueredirect' || res.status === 0 || (res.status >= 300 && res.status < 400)) throw new VerbindungsFehler('openfang_aus')
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
    const a = await rufe(aktionsTimeoutMs, 'POST', `/api/integrations/${id}/oauth/start`, `anmelden id=${id}`, {})
    if (!gut(a)) return fehler(a)
    const url = obj(a.body).anmelde_url
    const geprueft = typeof url === 'string' ? gepruefteAnmeldeUrl(url) : null
    if (!geprueft) return UNGUELTIGER_LINK
    await deps.openExternal(geprueft.href)
    return { ok: true }
  }

  async function oeffneSchluessel(id: string): Promise<AktionsErgebnis> {
    const a = await rufe(aktionsTimeoutMs, 'POST', `/api/integrations/${id}/schluessel/link`, `schluessel id=${id}`, {})
    if (!gut(a)) return fehler(a)
    const url = obj(a.body).url
    const geprueft = typeof url === 'string' ? gepruefterSchluesselLink(url, id) : null
    if (!geprueft) return UNGUELTIGER_LINK
    await deps.openExternal(geprueft.href)
    return { ok: true }
  }

  async function einfach(method: 'POST' | 'DELETE', path: string, label: string): Promise<AktionsErgebnis> {
    const a = await rufe(aktionsTimeoutMs, method, path, label, method === 'POST' ? {} : undefined)
    return gut(a) ? { ok: true } : fehler(a)
  }

  async function folge(zustand: string, f: () => Promise<AktionsErgebnis>): Promise<AktionsErgebnis> {
    try {
      const r = await f()
      return r.ok ? { ok: true, zustand } : { ...r, zustand }
    } catch (e) {
      if (e instanceof VerbindungsFehler) return { ok: false, zustand, meldung: VERBINDUNG_MELDUNG[e.art] }
      throw e
    }
  }

  return {
    async status() {
      try {
        const [inst, verf, frei] = await Promise.all([
          rufe(timeoutMs, 'GET', '/api/integrations', 'status'),
          rufe(timeoutMs, 'GET', '/api/integrations/available', 'verfuegbar'),
          rufe(timeoutMs, 'GET', '/api/approvals', 'freigaben'),
        ])
        if (!gut(inst) && inst.status !== 401) throw new VerbindungsFehler('openfang_aus')
        if (!gut(verf) && verf.status !== 401) throw new VerbindungsFehler('openfang_aus')
        const offen = (gut(frei) ? arr(obj(frei.body).approvals) : []).map(obj).filter((x) => x.status === 'pending').map((x) => str(x.tool_name))
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
              detail: ZUSTAENDE_MIT_DETAIL.has(zustand) && typeof x.detail === 'string' ? x.detail : null,
              werkzeuge: zustand === 'verbunden' && Number.isInteger(x.tool_count) && (x.tool_count as number) >= 0 ? (x.tool_count as number) : 0,
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
      const a = await rufe(aktionsTimeoutMs, 'POST', '/api/integrations/add', `add id=${id}`, { id })
      if (!gut(a)) return fehler(a)
      const zustand = str(obj(a.body).zustand, 'unbekannt')
      if (zustand === 'anmeldung_noetig') { const r = await folge(zustand, () => oeffneAnmeldung(id)); return r }
      if (zustand === 'fehlt_schluessel') { const r = await folge(zustand, () => oeffneSchluessel(id)); return r }
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
