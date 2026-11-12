import type { Ich } from '@/lib/typen'

export class ApiFehler extends Error {
  status: number
  detail: string

  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'ApiFehler'
    this.status = status
    this.detail = detail
  }
}

type Parameter = Record<string, string | undefined>

function standardAbmeldung(pfad: string): void {
  window.location.assign('/anmelden?weiter=' + encodeURIComponent(pfad))
}

let beiAbmeldung: (pfad: string) => void = standardAbmeldung
let csrf: string | null = null

/** Ersetzt, was bei einem 401 passiert (Standard: Weiterleitung zur Anmeldung; für Tests). */
export function setzeBeiAbmeldung(fn: (pfad: string) => void): void {
  beiAbmeldung = fn
}

function aktuellerPfad(): string {
  return window.location.pathname + window.location.search
}

function abmelden401(): void {
  // Auf der Anmeldeseite selbst nicht erneut dorthin leiten (kein verschachteltes `weiter`).
  if (window.location.pathname.startsWith('/anmelden')) return
  beiAbmeldung(aktuellerPfad())
}

/** FastAPI liefert `detail` als Text oder (422 der Validierung) als Liste von {loc, msg}. */
function detailText(body: unknown, status: number): string {
  if (typeof body === 'object' && body !== null && 'detail' in body) {
    const detail = (body as { detail: unknown }).detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      const zeilen = detail.map((eintrag: unknown) => {
        if (typeof eintrag !== 'object' || eintrag === null) return ''
        const { loc, msg } = eintrag as { loc?: unknown; msg?: unknown }
        const ort = Array.isArray(loc) ? String(loc[loc.length - 1] ?? '') : ''
        const text = typeof msg === 'string' ? msg : ''
        return ort && text ? `${ort}: ${text}` : text
      })
      const text = zeilen.filter(Boolean).join('; ')
      if (text) return text
    }
  }
  return `Fehler ${status}`
}

async function lesen(antwort: Response): Promise<unknown> {
  if (antwort.status === 204) return undefined
  const text = await antwort.text()
  if (!text) return undefined
  try {
    return JSON.parse(text) as unknown
  } catch {
    return undefined
  }
}

async function roh(
  methode: 'GET' | 'POST' | 'PATCH' | 'DELETE',
  pfad: string,
  daten?: unknown,
  token?: string,
): Promise<{ status: number; body: unknown }> {
  const headers = new Headers()
  if (daten !== undefined) headers.set('Content-Type', 'application/json')
  if (token) headers.set('X-CSRF', token)
  let antwort: Response
  try {
    antwort = await fetch(pfad, {
      method: methode,
      credentials: 'same-origin',
      headers,
      body: daten === undefined ? undefined : JSON.stringify(daten),
    })
  } catch {
    throw new ApiFehler(0, 'Keine Verbindung zum Server')
  }
  return { status: antwort.status, body: await lesen(antwort) }
}

function mitParametern(pfad: string, params?: Parameter): string {
  if (!params) return pfad
  const q = new URLSearchParams()
  for (const [name, wert] of Object.entries(params)) {
    if (wert !== undefined) q.set(name, wert)
  }
  const text = q.toString()
  return text ? `${pfad}?${text}` : pfad
}

async function holen<T>(pfad: string, params?: Parameter): Promise<T> {
  const { status, body } = await roh('GET', mitParametern(pfad, params))
  if (status >= 400) {
    if (status === 401) abmelden401()
    throw new ApiFehler(status, detailText(body, status))
  }
  return body as T
}

/** `GET /api/ich`; null, wenn nicht angemeldet. Merkt sich den CSRF-Token. */
async function ich(): Promise<Ich | null> {
  const { status, body } = await roh('GET', '/api/ich')
  if (status === 401) {
    csrf = null
    return null
  }
  if (status >= 400) throw new ApiFehler(status, detailText(body, status))
  const daten = body as Ich
  csrf = daten.csrf
  return daten
}

async function tokenHolen(): Promise<string> {
  if (csrf) return csrf
  const daten = await ich()
  if (!daten) {
    abmelden401()
    throw new ApiFehler(401, 'Nicht angemeldet')
  }
  return daten.csrf
}

function istCsrfFehler(status: number, detail: string): boolean {
  return status === 403 && detail.includes('CSRF')
}

async function senden<T>(
  methode: 'POST' | 'PATCH' | 'DELETE',
  pfad: string,
  daten?: unknown,
): Promise<T> {
  let token = await tokenHolen()
  let antwort = await roh(methode, pfad, daten, token)
  if (antwort.status === 403 && istCsrfFehler(403, detailText(antwort.body, 403))) {
    csrf = null
    token = await tokenHolen()
    antwort = await roh(methode, pfad, daten, token)
  }
  if (antwort.status >= 400) {
    if (antwort.status === 401) abmelden401()
    throw new ApiFehler(antwort.status, detailText(antwort.body, antwort.status))
  }
  return antwort.body as T
}

/** `POST /api/anmelden` (ohne CSRF-Token, setzt das Sitzungs-Cookie). */
async function anmelden(mail: string, passwort: string): Promise<Ich> {
  const { status, body } = await roh('POST', '/api/anmelden', { mail, passwort })
  if (status >= 400) throw new ApiFehler(status, detailText(body, status))
  const daten = body as Ich
  csrf = daten.csrf
  return daten
}

async function abmelden(): Promise<void> {
  await senden('POST', '/api/abmelden')
  csrf = null
}

/** Vergisst den gemerkten CSRF-Token (nach Abmeldung, in Tests). */
function vergessen(): void {
  csrf = null
}

export const api = { holen, senden, ich, anmelden, abmelden, vergessen }
