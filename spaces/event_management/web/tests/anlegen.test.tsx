import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import EventNeu from '@/app/events/neu/page'
import { ApiFehler, api } from '@/lib/api'
import { KERN } from '@/lib/begriffe'
import type { Ich, Kategorie, WirksamesSchema } from '@/lib/typen'

const push = vi.fn<(pfad: string) => void>()
vi.mock('next/navigation', () => ({ useRouter: () => ({ push }) }))

const LEITUNG: Ich = {
  person: { id: 'p1', name: 'Lea Beispiel', mail: 'lea@example.org' },
  betreiber: false,
  firmen: [{ id: 'f1', name: 'Beispiel GmbH', kurz: 'BG', rolle: 'leitung' }],
  csrf: 't',
  rechte: { event_anlegen: ['f1'], verwalten: ['f1'] },
}
const MITGLIED: Ich = { ...LEITUNG, rechte: { event_anlegen: [], verwalten: [] } }

const KATEGORIEN: Kategorie[] = [
  ['festival', 'Festival'],
  ['hackathon', 'Hackathon'],
  ['messe', 'Messe'],
  ['online_event', 'Online-Event'],
  ['team_meeting', 'Team-Meeting'],
  ['veranstaltung', 'Veranstaltung'],
  ['webinar', 'Webinar'],
  ['weihnachtsfeier', 'Weihnachtsfeier'],
  ['fest', 'Fest'],
].map(([id, name]) => ({ id: id!, name: name!, eltern: null }))

function schema(id: string, o: Partial<WirksamesSchema> = {}): WirksamesSchema {
  return {
    kategorie: id,
    name: KATEGORIEN.find((k) => k.id === id)?.name ?? id,
    kette: [id, 'basis'],
    bausteine: [...KERN],
    felder: [],
    begriffe: {},
    eckdaten_pflicht: [],
    ...o,
  }
}

const SCHEMATA: Record<string, WirksamesSchema> = {
  hackathon: schema('hackathon', {
    bausteine: [...KERN, 'wettbewerb_teams', 'abgaben', 'jury'],
    begriffe: { wettbewerb_teams: 'Teams' },
    felder: [
      { id: 'thema', typ: 'text', beschriftung: 'Thema', pflicht: false, vorgabe: null, optionen: [] },
      { id: 'plaetze', typ: 'zahl', beschriftung: 'Plätze', pflicht: false, vorgabe: null, optionen: [] },
    ],
  }),
  webinar: schema('webinar', {
    bausteine: [...KERN, 'online_raum', 'sprecher', 'aufzeichnung'],
    eckdaten_pflicht: ['ort_link'],
    felder: [
      { id: 'anmeldung_noetig', typ: 'ja_nein', beschriftung: 'Anmeldung nötig', pflicht: false, vorgabe: true, optionen: [] },
    ],
  }),
  weihnachtsfeier: schema('weihnachtsfeier', {
    felder: [
      { id: 'wichteln', typ: 'ja_nein', beschriftung: 'Wichteln', pflicht: false, vorgabe: false, optionen: [] },
    ],
  }),
}

let senden: ReturnType<typeof vi.spyOn>

function mocke(ich: Ich) {
  vi.spyOn(api, 'ich').mockResolvedValue(ich)
  vi.spyOn(api, 'holen').mockImplementation(async (pfad: string) => {
    if (pfad === '/api/kategorien') return KATEGORIEN as never
    const m = /^\/api\/kategorien\/(.+)\/schema$/.exec(pfad)
    if (m) return (SCHEMATA[m[1]!] ?? schema(m[1]!)) as never
    return [] as never
  })
  senden = vi.spyOn(api, 'senden').mockResolvedValue({ id: 'neu-1' } as never)
}

async function waehle(name: string) {
  await userEvent.click(await screen.findByRole('button', { name: new RegExp(name) }))
}

function setze(label: string | RegExp, wert: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value: wert } })
}

function fuelleEckdaten() {
  setze(/^Name/, 'Sommerfest')
  setze(/^Beginn/, '2026-12-01T18:00')
}

const weiter = () => screen.getByRole('button', { name: 'Weiter' }) as HTMLButtonElement

describe('Event anlegen', () => {
  beforeEach(() => {
    push.mockReset()
    mocke(LEITUNG)
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('Schritt 1 zeigt alle Kategorien mit Zusätzen', async () => {
    render(<EventNeu />)
    await screen.findByRole('button', { name: /Hackathon/ })
    expect(screen.getAllByRole('button').filter((b) => KATEGORIEN.some((k) => b.textContent?.includes(k.name)))).toHaveLength(9)
    expect(screen.getByRole('button', { name: /Hackathon/ }).textContent).toContain('+ Teams, Abgaben, Jury')
  })

  it('Webinar: Weiter ist ohne Online-Link gesperrt', async () => {
    render(<EventNeu />)
    await waehle('Webinar')
    fuelleEckdaten()
    expect(screen.getByLabelText('Online-Link *')).toBeTruthy()
    expect(weiter().disabled).toBe(true)
    setze(/^Online-Link/, 'https://example.org/live')
    expect(weiter().disabled).toBe(false)
  })

  it('Weihnachtsfeier: wichteln ist mit false vorbelegt', async () => {
    render(<EventNeu />)
    await waehle('Weihnachtsfeier')
    expect((screen.getByLabelText('Wichteln') as HTMLInputElement).checked).toBe(false)
    fuelleEckdaten()
    fireEvent.click(weiter())
    await userEvent.click(screen.getByRole('button', { name: 'Event anlegen' }))
    await waitFor(() => expect(senden).toHaveBeenCalled())
    const body = senden.mock.calls[0]![2] as { metadaten: Record<string, unknown> }
    expect(body.metadaten).toEqual({ wichteln: false })
  })

  it('Ende wird mit Beginn + 2 h vorbelegt; Ende vor Beginn sperrt Weiter', async () => {
    render(<EventNeu />)
    await waehle('Messe')
    setze(/^Name/, 'Messe')
    setze(/^Beginn/, '2026-12-01T18:00')
    expect((screen.getByLabelText(/^Ende/) as HTMLInputElement).value).toBe('2026-12-01T20:00')
    expect(weiter().disabled).toBe(false)
    setze(/^Ende/, '2026-12-01T17:00')
    expect(weiter().disabled).toBe(true)
  })

  it('Abschicken sendet naive Berliner Zeiten und typisierte Metadaten', async () => {
    render(<EventNeu />)
    await waehle('Hackathon')
    fuelleEckdaten()
    setze(/^Thema/, 'KI')
    setze(/^Plätze/, '40')
    fireEvent.click(weiter())
    await screen.findByText(/Kommt mit dem Bot-Ausbau/)
    await userEvent.click(screen.getByRole('button', { name: 'Event anlegen' }))
    await waitFor(() => expect(push).toHaveBeenCalledWith('/events/neu-1'))
    expect(senden).toHaveBeenCalledWith('POST', '/api/events', {
      firma_id: 'f1',
      kategorie: 'hackathon',
      name: 'Sommerfest',
      beginn: '2026-12-01T18:00',
      ende: '2026-12-01T20:00',
      ort_adresse: undefined,
      ort_link: undefined,
      beschreibung: undefined,
      metadaten: { thema: 'KI', plaetze: 40 },
    })
  })

  it('Webinar sendet die Checkbox als boolean', async () => {
    render(<EventNeu />)
    await waehle('Webinar')
    fuelleEckdaten()
    setze(/^Online-Link/, 'https://example.org/live')
    fireEvent.click(weiter())
    await userEvent.click(screen.getByRole('button', { name: 'Event anlegen' }))
    await waitFor(() => expect(senden).toHaveBeenCalled())
    const body = senden.mock.calls[0]![2] as { metadaten: Record<string, unknown> }
    expect(body.metadaten).toEqual({ anmeldung_noetig: true })
  })

  it('nach erfolgreichem Anlegen sendet ein zweiter Klick nichts mehr', async () => {
    render(<EventNeu />)
    await waehle('Messe')
    fuelleEckdaten()
    fireEvent.click(weiter())
    const knopf = screen.getByRole('button', { name: 'Event anlegen' }) as HTMLButtonElement
    await userEvent.click(knopf)
    await waitFor(() => expect(push).toHaveBeenCalledTimes(1))
    fireEvent.click(knopf)
    expect(senden).toHaveBeenCalledTimes(1)
  })

  it('Betreiber: Firmen kommen aus /api/ich, kein zusätzlicher Abruf', async () => {
    mocke({ ...LEITUNG, betreiber: true, rechte: { event_anlegen: [], verwalten: [] } })
    render(<EventNeu />)
    await waehle('Messe')
    expect(screen.getByText('Beispiel GmbH')).toBeTruthy()
    expect((api.holen as unknown as { mock: { calls: string[][] } }).mock.calls.some((c) => c[0] === '/api/firmen')).toBe(false)
  })

  it('422 zeigt detail und führt zurück zu den Eckdaten', async () => {
    senden.mockRejectedValue(new ApiFehler(422, "Eckdatum 'ort_link' ist Pflicht"))
    render(<EventNeu />)
    await waehle('Messe')
    fuelleEckdaten()
    fireEvent.click(weiter())
    await userEvent.click(screen.getByRole('button', { name: 'Event anlegen' }))
    expect((await screen.findByRole('alert')).textContent).toContain("Eckdatum 'ort_link' ist Pflicht")
    expect((screen.getByLabelText(/^Name/) as HTMLInputElement).value).toBe('Sommerfest')
    expect((screen.getByLabelText(/^Beginn/) as HTMLInputElement).value).toBe('2026-12-01T18:00')
    expect((screen.getByLabelText(/^Ende/) as HTMLInputElement).value).toBe('2026-12-01T20:00')
    expect(push).not.toHaveBeenCalled()
  })

  it('Mitglied ohne Anlegerecht bekommt einen Hinweis statt des Formulars', async () => {
    mocke(MITGLIED)
    render(<EventNeu />)
    expect((await screen.findByRole('alert')).textContent).toContain('keine Events anlegen')
    expect(screen.queryByRole('button', { name: /Hackathon/ })).toBeNull()
  })
})
