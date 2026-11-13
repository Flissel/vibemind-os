import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Startseite from '@/app/page'
import { api } from '@/lib/api'
import type { Ich, KatalogEvent } from '@/lib/typen'
import { katalogEvent } from './katalog-daten'

const MITGLIED: Ich = {
  person: { id: 'p1', name: 'Lea Beispiel', mail: 'lea@example.org' },
  betreiber: false,
  firmen: [{ id: 'f1', name: 'Beispiel GmbH', kurz: 'BG', rolle: 'mitglied' }],
  csrf: 't',
  rechte: { event_anlegen: [], verwalten: [] },
}
const LEITUNG: Ich = { ...MITGLIED, rechte: { event_anlegen: ['f1'], verwalten: ['f1'] } }

const EVENTS: KatalogEvent[] = [
  katalogEvent({
    id: 'a',
    name: 'Alt-Treffen',
    phase: 'vergangen',
    beginn: '2026-10-01T09:00:00+02:00',
    ende: '2026-10-01T17:00:00+02:00',
  }),
  katalogEvent({
    id: 'b',
    name: 'Kommender Workshop',
    kategorie: 'workshop',
    kategorie_name: 'Workshop',
  }),
  katalogEvent({
    id: 'c',
    name: 'Nachtschicht',
    phase: 'laeuft',
    beginn: '2026-11-12T18:00:00+01:00',
    ende: '2026-11-12T23:30:00+01:00',
  }),
  katalogEvent({ id: 'd', name: 'Zweiter Hackathon', phase: 'kommend' }),
]

let holen: ReturnType<typeof vi.spyOn>

function mocke(ich: Ich, events: KatalogEvent[] = EVENTS) {
  vi.spyOn(api, 'ich').mockResolvedValue(ich)
  holen = vi.spyOn(api, 'holen').mockImplementation(async (pfad: string) => {
    if (pfad === '/api/events') return events as never
    return [] as never
  })
}

afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
})

describe('Katalog', () => {
  beforeEach(() => mocke(LEITUNG))

  it('zeigt die Gruppen in der Reihenfolge, Vergangen eingeklappt', async () => {
    render(<Startseite />)
    await screen.findByText('Nachtschicht')
    const titel = screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent ?? '')
    expect(titel[0]).toMatch(/^Läuft gerade/)
    expect(titel[1]).toMatch(/^Kommend/)
    expect(titel[2]).toMatch(/^Vergangen/)
    const vergangen = screen.getByRole('button', { name: /Vergangen/ })
    expect(vergangen.getAttribute('aria-expanded')).toBe('false')
    expect(screen.queryByText('Alt-Treffen')).toBeNull()
    fireEvent.click(vergangen)
    expect(screen.getByText('Alt-Treffen')).toBeTruthy()
  })

  it('ordnet ein Event mit Ende 23:30 Berlin und Phase laeuft unter „Läuft gerade“', async () => {
    render(<Startseite />)
    const kopf = await screen.findByRole('heading', { name: /Läuft gerade/ })
    const abschnitt = kopf.closest('section')
    expect(abschnitt).not.toBeNull()
    expect(within(abschnitt as HTMLElement).getByText('Nachtschicht')).toBeTruthy()
  })

  it('zählt Chips aus der Trefferliste und filtert per Klick exakt', async () => {
    render(<Startseite />)
    await screen.findByText('Nachtschicht')
    expect(screen.getByRole('button', { name: 'Alle 4' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Hackathon 3' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Workshop 1' }))
    expect(screen.getByText('Kommender Workshop')).toBeTruthy()
    expect(screen.queryByText('Nachtschicht')).toBeNull()
    expect(screen.getByRole('button', { name: 'Hackathon 3' })).toBeTruthy()
  })

  it('schickt die Suche entprellt an die API', async () => {
    render(<Startseite />)
    await screen.findByText('Nachtschicht')
    vi.useFakeTimers()
    holen.mockClear()
    const feld = screen.getByLabelText('Suchen')
    fireEvent.change(feld, { target: { value: 'ha' } })
    fireEvent.change(feld, { target: { value: 'hack' } })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(299)
    })
    expect(holen).not.toHaveBeenCalled()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2)
    })
    expect(holen).toHaveBeenCalledTimes(1)
    expect(holen).toHaveBeenCalledWith('/api/events', { firma: undefined, suche: 'hack' })
  })

  it('schickt die Firma an die API', async () => {
    render(<Startseite />)
    await screen.findByText('Nachtschicht')
    fireEvent.change(screen.getByLabelText('Firma'), { target: { value: 'f1' } })
    await waitFor(() =>
      expect(holen).toHaveBeenLastCalledWith('/api/events', { firma: 'f1', suche: undefined }),
    )
  })

  it('setzt eine gewählte Kategorie zurück, wenn sie nach der Suche nicht mehr vorkommt', async () => {
    render(<Startseite />)
    await screen.findByText('Nachtschicht')
    fireEvent.click(screen.getByRole('button', { name: 'Workshop 1' }))
    expect(screen.queryByText('Nachtschicht')).toBeNull()
    holen.mockImplementation(async (pfad: string) => {
      if (pfad === '/api/events') return EVENTS.filter((e) => e.kategorie === 'hackathon') as never
      return [] as never
    })
    fireEvent.change(screen.getByLabelText('Firma'), { target: { value: 'f1' } })
    await screen.findByText('Nachtschicht')
    expect(screen.getByRole('button', { name: 'Alle 3', pressed: true })).toBeTruthy()
  })

  it('ruft für den Betreiber nicht zusätzlich /api/firmen ab', async () => {
    mocke({ ...LEITUNG, betreiber: true })
    render(<Startseite />)
    await screen.findByText('Nachtschicht')
    expect(holen.mock.calls.some((c: unknown[]) => c[0] === '/api/firmen')).toBe(false)
    expect(screen.getByRole('option', { name: 'Beispiel GmbH' })).toBeTruthy()
  })

  it('zeigt „+ Neues Event“ mit Anlegerecht', async () => {
    render(<Startseite />)
    const link = await screen.findByRole('link', { name: '+ Neues Event' })
    expect(link.getAttribute('href')).toBe('/events/neu')
  })
})

describe('Katalog ohne Anlegerecht und leer', () => {
  it('blendet „+ Neues Event“ für ein Mitglied ohne Anlegerecht aus', async () => {
    mocke(MITGLIED)
    render(<Startseite />)
    await screen.findByText('Nachtschicht')
    expect(screen.queryByText('+ Neues Event')).toBeNull()
  })

  it('zeigt den Leer-Zustand ohne Events', async () => {
    mocke(MITGLIED, [])
    render(<Startseite />)
    expect(await screen.findByText('Keine Events gefunden')).toBeTruthy()
    expect(screen.queryByRole('heading', { level: 2 })).toBeNull()
  })
})
