import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, ApiFehler, setzeBeiAbmeldung } from '@/lib/api'
import type { Ich } from '@/lib/typen'

const ICH: Ich = {
  person: { id: 'p1', name: 'Lea Beispiel', mail: 'lea@example.org' },
  betreiber: false,
  firmen: [],
  csrf: 'token-1',
  rechte: { event_anlegen: [], verwalten: [] },
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('api', () => {
  const fetchMock = vi.fn<typeof fetch>()
  const beiAbmeldung = vi.fn<(pfad: string) => void>()

  beforeEach(() => {
    fetchMock.mockReset()
    beiAbmeldung.mockReset()
    vi.stubGlobal('fetch', fetchMock)
    setzeBeiAbmeldung(beiAbmeldung)
    api.vergessen()
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  function aufruf(n: number): { url: string; init: RequestInit } {
    const call = fetchMock.mock.calls[n]
    if (!call) throw new Error(`kein fetch-Aufruf ${n}`)
    return { url: String(call[0]), init: call[1] ?? {} }
  }

  it('holt den CSRF-Token einmal und schickt ihn mit', async () => {
    fetchMock
      .mockResolvedValueOnce(json(200, ICH))
      .mockResolvedValueOnce(json(201, { id: 'a' }))
      .mockResolvedValueOnce(json(200, { id: 'a' }))
    await api.senden('POST', '/api/firmen', { name: 'X', kurz: 'xx' })
    await api.senden('PATCH', '/api/events/e1', { name: 'Y' })
    expect(fetchMock).toHaveBeenCalledTimes(3)
    expect(aufruf(0).url).toBe('/api/ich')
    const zweiter = aufruf(1)
    expect(zweiter.url).toBe('/api/firmen')
    expect(zweiter.init.method).toBe('POST')
    expect(zweiter.init.credentials).toBe('same-origin')
    expect(new Headers(zweiter.init.headers).get('X-CSRF')).toBe('token-1')
    expect(new Headers(zweiter.init.headers).get('Content-Type')).toBe('application/json')
    expect(zweiter.init.body).toBe(JSON.stringify({ name: 'X', kurz: 'xx' }))
    expect(new Headers(aufruf(2).init.headers).get('X-CSRF')).toBe('token-1')
  })

  it('holt bei CSRF-403 den Token neu und wiederholt genau einmal', async () => {
    fetchMock
      .mockResolvedValueOnce(json(200, ICH))
      .mockResolvedValueOnce(json(403, { detail: 'CSRF-Token fehlt oder ist falsch' }))
      .mockResolvedValueOnce(json(200, { ...ICH, csrf: 'token-2' }))
      .mockResolvedValueOnce(json(200, { ok: true }))
    const antwort = await api.senden<{ ok: boolean }>('POST', '/api/abmelden')
    expect(antwort).toEqual({ ok: true })
    expect(fetchMock).toHaveBeenCalledTimes(4)
    expect(new Headers(aufruf(3).init.headers).get('X-CSRF')).toBe('token-2')
  })

  it('wiederholt nicht endlos, wenn der Token auch danach falsch ist', async () => {
    fetchMock
      .mockResolvedValueOnce(json(200, ICH))
      .mockResolvedValueOnce(json(403, { detail: 'CSRF-Token fehlt oder ist falsch' }))
      .mockResolvedValueOnce(json(200, ICH))
      .mockResolvedValueOnce(json(403, { detail: 'CSRF-Token fehlt oder ist falsch' }))
    await expect(api.senden('POST', '/api/firmen', {})).rejects.toMatchObject({ status: 403 })
    expect(fetchMock).toHaveBeenCalledTimes(4)
  })

  it('403 ohne CSRF im detail wird nicht wiederholt', async () => {
    fetchMock
      .mockResolvedValueOnce(json(200, ICH))
      .mockResolvedValueOnce(json(403, { detail: 'Keine Berechtigung fuer event_absagen' }))
    await expect(api.senden('POST', '/api/events/e1/status', {})).rejects.toMatchObject({
      status: 403,
      detail: 'Keine Berechtigung fuer event_absagen',
    })
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('401 aus einer anderen Anfrage ruft beiAbmeldung mit dem aktuellen Pfad', async () => {
    window.history.pushState({}, '', '/events/abc?x=1')
    fetchMock.mockResolvedValueOnce(json(401, { detail: 'Nicht angemeldet' }))
    await expect(api.holen('/api/events')).rejects.toBeInstanceOf(ApiFehler)
    expect(beiAbmeldung).toHaveBeenCalledTimes(1)
    expect(beiAbmeldung).toHaveBeenCalledWith('/events/abc?x=1')
  })

  it('401 beim Token-Holen fuer eine Schreibanfrage meldet ebenfalls ab', async () => {
    fetchMock.mockResolvedValueOnce(json(401, { detail: 'Nicht angemeldet' }))
    await expect(api.senden('POST', '/api/firmen', {})).rejects.toMatchObject({ status: 401 })
    expect(beiAbmeldung).toHaveBeenCalledTimes(1)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('401 aus api.ich liefert null und meldet nicht ab', async () => {
    fetchMock.mockResolvedValueOnce(json(401, { detail: 'Nicht angemeldet' }))
    expect(await api.ich()).toBeNull()
    expect(beiAbmeldung).not.toHaveBeenCalled()
  })

  it('api.ich liefert die Person und merkt sich den Token', async () => {
    fetchMock.mockResolvedValueOnce(json(200, ICH)).mockResolvedValueOnce(json(200, {}))
    expect(await api.ich()).toEqual(ICH)
    await api.senden('POST', '/api/firmen', {})
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(new Headers(aufruf(1).init.headers).get('X-CSRF')).toBe('token-1')
  })

  it('422 setzt ApiFehler.detail (Text)', async () => {
    fetchMock
      .mockResolvedValueOnce(json(200, ICH))
      .mockResolvedValueOnce(json(422, { detail: "Pflichtfeld 'titel' fehlt" }))
    const fehler = await api
      .senden('POST', '/api/events/e1/b/aufgaben', {})
      .catch((e: unknown) => e)
    expect(fehler).toBeInstanceOf(ApiFehler)
    expect(fehler).toMatchObject({ status: 422, detail: "Pflichtfeld 'titel' fehlt" })
  })

  it('422 mit Validierungsliste wird zu Text', async () => {
    fetchMock.mockResolvedValueOnce(
      json(422, { detail: [{ loc: ['body', 'name'], msg: 'Field required', type: 'missing' }] }),
    )
    await expect(api.holen('/api/events')).rejects.toMatchObject({
      status: 422,
      detail: 'name: Field required',
    })
  })

  it('204 liefert undefined', async () => {
    fetchMock
      .mockResolvedValueOnce(json(200, ICH))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
    expect(await api.senden('DELETE', '/api/firmen/f/kontakte/k')).toBeUndefined()
  })

  it('holen haengt nur gesetzte Parameter an', async () => {
    fetchMock.mockResolvedValueOnce(json(200, []))
    await api.holen('/api/events', { suche: 'ä b', firma: undefined, kategorie: 'x' })
    expect(aufruf(0).url).toBe('/api/events?suche=%C3%A4+b&kategorie=x')
    expect(aufruf(0).init.method).toBe('GET')
  })

  it('ein Netzfehler wird ein ApiFehler mit Status 0', async () => {
    fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    await expect(api.holen('/api/events')).rejects.toMatchObject({ status: 0 })
  })

  it('anmelden merkt sich den Token aus der Antwort', async () => {
    fetchMock.mockResolvedValueOnce(json(200, ICH)).mockResolvedValueOnce(json(200, {}))
    await api.anmelden('lea@example.org', 'geheim')
    await api.senden('POST', '/api/firmen', {})
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(aufruf(0).url).toBe('/api/anmelden')
    expect(new Headers(aufruf(1).init.headers).get('X-CSRF')).toBe('token-1')
  })
})
