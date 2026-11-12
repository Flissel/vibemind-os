import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import EventPage from '@/app/events/[id]/page'
import { ApiFehler, api } from '@/lib/api'
import { KERN } from '@/lib/begriffe'
import type { EventDetail, EventSchema, Ich, VerlaufEintrag } from '@/lib/typen'

const push = vi.fn<(pfad: string) => void>()
let reiterParam: string | null = null
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push }),
  useParams: () => ({ id: 'e1' }),
  useSearchParams: () => new URLSearchParams(reiterParam ? { reiter: reiterParam } : {}),
}))

const LEITUNG: Ich = {
  person: { id: 'p1', name: 'Lea Beispiel', mail: 'lea@example.org' },
  betreiber: false,
  firmen: [{ id: 'f1', name: 'Beispiel GmbH', kurz: 'BG', rolle: 'leitung' }],
  csrf: 't',
  rechte: { event_anlegen: ['f1'], verwalten: ['f1'] },
}
const MITGLIED: Ich = { ...LEITUNG, rechte: { event_anlegen: [], verwalten: [] } }

function schema(kategorie: string, name: string, extra: string[], begriffe = {}): EventSchema {
  return {
    kategorie,
    name,
    bausteine: [...KERN.filter((b) => b !== 'budget' || kategorie !== 'team_meeting'), ...extra],
    felder: [
      { id: 'thema', typ: 'text', beschriftung: 'Thema', pflicht: false, vorgabe: null, optionen: [] },
    ],
    begriffe,
  }
}

function event(o: Partial<EventDetail> = {}): EventDetail {
  return {
    id: 'e1',
    firma_id: 'f1',
    kategorie: 'hackathon',
    name: 'Sommer-Hackathon',
    beginn: '2026-12-01T09:00:00+01:00',
    ende: '2026-12-02T17:00:00+01:00',
    ort_adresse: 'Musterstraße 1',
    ort_link: null,
    beschreibung: null,
    status: 'geplant',
    quelle: 'neu',
    legacy_url: null,
    metadaten: { thema: 'KI' },
    angelegt: '2026-11-01T10:00:00+01:00',
    geaendert: '2026-11-01T10:00:00+01:00',
    schema: schema('hackathon', 'Hackathon', ['wettbewerb_teams'], { wettbewerb_teams: 'Teams' }),
    ...o,
  }
}

let senden: ReturnType<typeof vi.spyOn>
let verlauf: VerlaufEintrag[]
let detail: EventDetail | ApiFehler

function mocke(ich: Ich, e: EventDetail | ApiFehler = event()) {
  detail = e
  vi.spyOn(api, 'ich').mockResolvedValue(ich)
  vi.spyOn(api, 'holen').mockImplementation(async (pfad: string) => {
    if (pfad === '/api/events/e1') {
      if (detail instanceof ApiFehler) throw detail
      return detail as never
    }
    if (pfad === '/api/events/e1/verlauf') return verlauf as never
    return [] as never
  })
  senden = vi.spyOn(api, 'senden').mockImplementation(async (_m, pfad: string, daten?: unknown) => {
    if (pfad.endsWith('/status')) {
      return { ...event(), status: (daten as { status: EventDetail['status'] }).status } as never
    }
    if (_m === 'PATCH') return { ...event(), ...(daten as object) } as never
    return { ok: true } as never
  })
}

beforeEach(() => {
  reiterParam = null
  verlauf = []
  push.mockReset()
})
afterEach(() => {
  vi.restoreAllMocks()
})

async function laden(name = 'Sommer-Hackathon') {
  return screen.findByRole('heading', { level: 1, name })
}

describe('Event-Seite', () => {
  it('zeigt Kopf mit Brotkrumen, Spanne, Ort und Badges', async () => {
    mocke(LEITUNG)
    render(<EventPage />)
    await laden()
    expect(screen.getByText('Beispiel GmbH › Hackathon')).toBeTruthy()
    expect(screen.getByText(/01\.–02\.12\.2026 · Musterstraße 1/)).toBeTruthy()
    expect(screen.getAllByText('geplant').length).toBeGreaterThan(0)
  })

  it('Reiter folgen dem Schema: team_meeting hat Tagesordnung, aber kein Budget', async () => {
    mocke(
      LEITUNG,
      event({
        kategorie: 'team_meeting',
        schema: schema('team_meeting', 'Team-Meeting', ['protokoll'], { ablauf: 'Tagesordnung' }),
      }),
    )
    render(<EventPage />)
    await laden()
    const nav = screen.getByRole('navigation', { name: 'Bereiche' })
    expect(within(nav).getByText('Tagesordnung')).toBeTruthy()
    expect(within(nav).queryByText('Budget')).toBeNull()
    expect(within(nav).getByText('Übersicht')).toBeTruthy()
    const namen = within(nav)
      .getAllByRole('link')
      .map((l) => l.textContent)
    expect(namen[0]).toBe('Übersicht')
    expect(namen[namen.length - 1]).toBe('Verlauf')
    expect(namen.filter((n) => n === 'Verlauf')).toHaveLength(1)
  })

  it('festival hat Timetable', async () => {
    mocke(
      LEITUNG,
      event({
        kategorie: 'festival',
        schema: schema('festival', 'Festival', ['buehnen'], { ablauf: 'Timetable' }),
      }),
    )
    render(<EventPage />)
    await laden()
    expect(within(screen.getByRole('navigation', { name: 'Bereiche' })).getByText('Timetable')).toBeTruthy()
  })

  it('der aktive Reiter steht in der URL', async () => {
    reiterParam = 'wettbewerb_teams'
    mocke(LEITUNG)
    render(<EventPage />)
    await laden()
    const nav = screen.getByRole('navigation', { name: 'Bereiche' })
    const teams = within(nav).getByText('Teams')
    expect(teams.getAttribute('href')).toBe('/events/e1?reiter=wettbewerb_teams')
    expect(teams.getAttribute('aria-current')).toBe('page')
    expect(within(nav).getByText('Übersicht').getAttribute('href')).toBe('/events/e1')
    expect(within(nav).getByText('Verlauf').getAttribute('href')).toBe('/events/e1?reiter=verlauf')
    expect(screen.getByText(/Bereich „Teams“/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Speichern' })).toBeNull()
  })

  it('Speichern schickt nur geänderte Felder', async () => {
    mocke(LEITUNG)
    const u = userEvent.setup()
    render(<EventPage />)
    await laden()
    const speichern = screen.getByRole('button', { name: 'Speichern' }) as HTMLButtonElement
    expect(speichern.disabled).toBe(true)
    const name = screen.getByLabelText('Name *')
    await u.clear(name)
    await u.type(name, 'Winter-Hackathon')
    await u.click(speichern)
    await waitFor(() => expect(senden).toHaveBeenCalledTimes(1))
    expect(senden).toHaveBeenCalledWith('PATCH', '/api/events/e1', { name: 'Winter-Hackathon' })
    expect(await screen.findByText('Gespeichert')).toBeTruthy()
  })

  it('Metadaten gehen als Ganzes mit, wenn ein Feld abweicht', async () => {
    mocke(LEITUNG)
    const u = userEvent.setup()
    render(<EventPage />)
    await laden()
    const thema = screen.getByLabelText('Thema')
    await u.clear(thema)
    await u.type(thema, 'Daten')
    await u.click(screen.getByRole('button', { name: 'Speichern' }))
    await waitFor(() => expect(senden).toHaveBeenCalled())
    expect(senden).toHaveBeenCalledWith('PATCH', '/api/events/e1', { metadaten: { thema: 'Daten' } })
  })

  it('ein Mitglied sieht weder Löschen noch Absagen noch Archivieren', async () => {
    mocke(MITGLIED)
    render(<EventPage />)
    await laden()
    expect(screen.queryByRole('button', { name: /Löschen/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /Absagen/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /Archivieren/ })).toBeNull()
    expect(screen.getByRole('button', { name: 'Läuft' })).toBeTruthy()
  })

  it('ein Mitglied kann ein abgesagtes Event nicht wieder aufnehmen (R3)', async () => {
    mocke(MITGLIED, event({ status: 'abgesagt' }))
    render(<EventPage />)
    await laden()
    expect(screen.queryByRole('button', { name: 'Wieder aufnehmen' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Geplant' })).toBeNull()
  })

  it('Mitglied setzt den Status auf läuft', async () => {
    mocke(MITGLIED)
    const u = userEvent.setup()
    render(<EventPage />)
    await laden()
    await u.click(screen.getByRole('button', { name: 'Läuft' }))
    await waitFor(() =>
      expect(senden).toHaveBeenCalledWith('POST', '/api/events/e1/status', { status: 'laeuft' }),
    )
  })

  it('Absagen verlangt eine Bestätigung', async () => {
    mocke(LEITUNG)
    const u = userEvent.setup()
    render(<EventPage />)
    await laden()
    await u.click(screen.getByRole('button', { name: 'Absagen' }))
    expect(senden).not.toHaveBeenCalled()
    await u.click(screen.getByRole('button', { name: 'Absagen bestätigen' }))
    await waitFor(() =>
      expect(senden).toHaveBeenCalledWith('POST', '/api/events/e1/status', { status: 'abgesagt' }),
    )
    expect(await screen.findByRole('button', { name: 'Wieder aufnehmen' })).toBeTruthy()
  })

  it('Löschen erst nach dem Eintippen des Namens, danach zum Katalog', async () => {
    mocke(LEITUNG)
    const u = userEvent.setup()
    render(<EventPage />)
    await laden()
    await u.click(screen.getByRole('button', { name: 'Event löschen' }))
    const ok = screen.getByRole('button', { name: 'Endgültig löschen' }) as HTMLButtonElement
    expect(ok.disabled).toBe(true)
    await u.type(screen.getByLabelText(/Zum Bestätigen/), 'Sommer-Hackathon')
    expect(ok.disabled).toBe(false)
    await u.click(ok)
    await waitFor(() => expect(senden).toHaveBeenCalledWith('DELETE', '/api/events/e1'))
    await waitFor(() => expect(push).toHaveBeenCalledWith('/'))
  })

  it('403 der API wird als „Keine Berechtigung“ gezeigt', async () => {
    mocke(LEITUNG)
    senden.mockRejectedValue(new ApiFehler(403, 'verboten'))
    const u = userEvent.setup()
    render(<EventPage />)
    await laden()
    await u.click(screen.getByRole('button', { name: 'Läuft' }))
    expect((await screen.findByRole('alert')).textContent).toContain('Keine Berechtigung')
  })

  it('403 beim Laden zeigt „Keine Berechtigung“', async () => {
    mocke(LEITUNG, new ApiFehler(403, 'x'))
    render(<EventPage />)
    expect((await screen.findByRole('alert')).textContent).toContain('Keine Berechtigung')
  })

  it('Legacy: Banner mit Link, keine Reiter', async () => {
    mocke(LEITUNG, event({ quelle: 'legacy', legacy_url: 'https://alt.example.org/e/1' }))
    render(<EventPage />)
    await laden()
    expect(screen.getByText(/Dieses Event wird in der alten App gepflegt/)).toBeTruthy()
    expect(screen.getByRole('link', { name: /alten App/ }).getAttribute('href')).toBe(
      'https://alt.example.org/e/1',
    )
    expect(screen.queryByRole('navigation', { name: 'Bereiche' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Speichern' })).toBeNull()
  })

  it('Legacy mit unsicherer URL: Banner ohne Link', async () => {
    mocke(LEITUNG, event({ quelle: 'legacy', legacy_url: 'javascript:alert(1)' }))
    render(<EventPage />)
    await laden()
    expect(screen.getByText(/Dieses Event wird in der alten App gepflegt/)).toBeTruthy()
    expect(screen.queryByRole('link', { name: /alten App/ })).toBeNull()
  })

  it('Verlauf zeigt Zeiten in Berlin, Actor, Baustein, Aktion und Quelle', async () => {
    reiterParam = 'verlauf'
    verlauf = [
      {
        id: 1,
        event_id: 'e1',
        firma_id: 'f1',
        zeitpunkt: '2026-11-12T22:30:00+00:00',
        actor: 'lea@example.org',
        actor_person_id: 'p1',
        quelle: 'web',
        baustein: 'wettbewerb_teams',
        aktion: 'angelegt',
        ziel_id: null,
      },
    ]
    mocke(LEITUNG)
    render(<EventPage />)
    await laden()
    const zeile = (await screen.findByText('lea@example.org')).closest('tr')!
    expect(zeile.textContent).toContain('12.11.2026 23:30')
    expect(zeile.textContent).toContain('Teams')
    expect(zeile.textContent).toContain('angelegt')
    expect(zeile.textContent).toContain('web')
    expect(api.holen).toHaveBeenCalledWith('/api/events/e1/verlauf', { limit: '100' })
  })
})
