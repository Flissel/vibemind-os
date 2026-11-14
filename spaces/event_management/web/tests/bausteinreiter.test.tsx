import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { BausteinReiter } from '@/components/event/BausteinReiter'
import { ApiFehler, api } from '@/lib/api'
import { formatEuro, parseEuro } from '@/lib/euro'
import type { BausteineKatalog, Kontakt, Zeile } from '@/lib/typen'

// Antwortform von GET /api/bausteine, erzeugt aus system/bausteine.py REGISTRY
// (`baustein` = `t.baustein or t.name`, also nie null).
const VERZEICHNIS: BausteineKatalog = {
  aufgaben: {
    baustein: 'aufgaben',
    felder: {
      titel: 'text',
      beschreibung: 'text',
      status: 'auswahl:offen,in_arbeit,erledigt',
      kategorie_id: 'ref:aufgaben_kategorien',
      verantwortlich_id: 'kontakt',
      faellig: 'datumzeit',
    },
    pflicht: ['titel'],
    reihenfolge: ['titel', 'beschreibung', 'status', 'kategorie_id', 'verantwortlich_id', 'faellig'],
  },
  aufgaben_kategorien: {
    baustein: 'aufgaben',
    felder: { name: 'text' },
    pflicht: ['name'],
    reihenfolge: ['name'],
  },
  orga_team: {
    baustein: 'orga_team',
    felder: { kontakt_id: 'kontakt', rolle: 'text' },
    pflicht: ['kontakt_id'],
    reihenfolge: ['kontakt_id', 'rolle'],
  },
  budget: {
    baustein: 'budget',
    felder: {
      titel: 'text',
      art: 'auswahl:kosten,einnahme',
      betrag_cent: 'ganzzahl',
      status: 'auswahl:geplant,bestaetigt,bezahlt',
    },
    pflicht: ['art', 'betrag_cent', 'titel'],
    reihenfolge: ['titel', 'art', 'betrag_cent', 'status'],
  },
}

const KONTAKT: Kontakt = {
  id: 'k1',
  firma_id: 'f1',
  name: 'Max Beispiel',
  mail: 'max@example.org',
  telegram: null,
  person_id: null,
  angelegt: '2026-11-01T10:00:00+01:00',
}

let tabellen: Record<string, Zeile[]>
let kontakte: Kontakt[]
let senden: ReturnType<typeof vi.spyOn>

beforeEach(() => {
  tabellen = {
    aufgaben: [
      {
        id: 'a1',
        titel: 'Halle buchen',
        beschreibung: null,
        status: 'offen',
        kategorie_id: 'c1',
        verantwortlich_id: 'k1',
        faellig: '2026-12-01T09:30:00+01:00',
      },
    ],
    aufgaben_kategorien: [{ id: 'c1', name: 'Location' }],
    budget: [{ id: 'b1', titel: 'Saalmiete', art: 'kosten', betrag_cent: 123450, status: 'geplant' }],
    orga_team: [],
  }
  kontakte = [KONTAKT]
  vi.spyOn(api, 'holen').mockImplementation(async (pfad: string) => {
    if (pfad === '/api/firmen/f1/kontakte') return kontakte as never
    const m = /^\/api\/events\/e1\/b\/([a-z_]+)$/.exec(pfad)
    if (m?.[1] && tabellen[m[1]]) return tabellen[m[1]] as never
    throw new ApiFehler(404, 'Nicht gefunden')
  })
  senden = vi.spyOn(api, 'senden').mockImplementation(async () => ({ ok: true }) as never)
})
afterEach(() => {
  vi.restoreAllMocks()
})

function zeige(baustein: string, verzeichnis: BausteineKatalog = VERZEICHNIS) {
  return render(
    <BausteinReiter
      eventId="e1"
      firmaId="f1"
      baustein={baustein}
      begriffe={{}}
      verzeichnis={verzeichnis}
    />,
  )
}

describe('Euro', () => {
  it('parst ohne Fließkomma', () => {
    expect(parseEuro('12,50')).toBe(1250)
    expect(parseEuro('0,1')).toBe(10)
    expect(parseEuro('1.234,50')).toBe(123450)
    expect(parseEuro('12.5')).toBe(1250)
    expect(parseEuro('1.234')).toBe(123400)
    expect(parseEuro('7')).toBe(700)
    expect(parseEuro('19,99')).toBe(1999)
    expect(parseEuro('abc')).toBeNull()
    expect(parseEuro('-5')).toBeNull()
    expect(parseEuro('1,234')).toBeNull()
    expect(parseEuro('')).toBeNull()
  })
  it('formatiert', () => {
    expect(formatEuro(123450)).toBe('1.234,50 €')
    expect(formatEuro(5)).toBe('0,05 €')
  })
})

describe('BausteinReiter', () => {
  it('Aufgaben-Reiter zeigt Aufgaben und Kategorien mit aufgelösten Werten', async () => {
    zeige('aufgaben')
    expect(await screen.findByRole('heading', { name: 'Aufgaben' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Kategorien' })).toBeTruthy()
    const zeile = (await screen.findByText('Halle buchen')).closest('tr') as HTMLElement
    expect(within(zeile).getByText('Location')).toBeTruthy()
    expect(within(zeile).getByText('Max Beispiel')).toBeTruthy()
    expect(within(zeile).getByText('01.12.2026 09:30')).toBeTruthy()
    expect(within(zeile).getByText('offen')).toBeTruthy()
  })

  it('zeigt Beträge in Euro', async () => {
    zeige('budget')
    expect(await screen.findByText('1.234,50 €')).toBeTruthy()
  })

  it('legt eine Aufgabe mit Kategorie aus der Ref-Auswahl an; leere optionale Felder fehlen', async () => {
    const u = userEvent.setup()
    zeige('aufgaben')
    await screen.findByText('Halle buchen')
    await u.click(screen.getAllByRole('button', { name: '+ Hinzufügen' })[0] as HTMLElement)
    await u.type(screen.getByLabelText(/^Titel/), 'Catering')
    await u.selectOptions(screen.getByLabelText(/^Kategorie/), 'Location')
    await u.click(screen.getByRole('button', { name: 'Speichern' }))
    await waitFor(() => expect(senden).toHaveBeenCalled())
    expect(senden).toHaveBeenCalledWith('POST', '/api/events/e1/b/aufgaben', {
      titel: 'Catering',
      kategorie_id: 'c1',
    })
  })

  it('datetime-local geht als Berliner Wandzeit ohne Offset an die API (anlegen und ändern)', async () => {
    const u = userEvent.setup()
    zeige('aufgaben')
    const zeile = (await screen.findByText('Halle buchen')).closest('tr') as HTMLElement
    await u.click(screen.getAllByRole('button', { name: '+ Hinzufügen' })[0] as HTMLElement)
    await u.type(screen.getByLabelText(/^Titel/), 'Catering')
    fireEvent.change(screen.getByLabelText(/^Fällig/), { target: { value: '2026-12-01T09:30' } })
    await u.click(screen.getByRole('button', { name: 'Speichern' }))
    await waitFor(() =>
      expect(senden).toHaveBeenCalledWith('POST', '/api/events/e1/b/aufgaben', {
        titel: 'Catering',
        faellig: '2026-12-01T09:30',
      }),
    )
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Speichern' })).toBeNull())
    senden.mockClear()
    await u.click(within(zeile).getByRole('button', { name: 'Bearbeiten Halle buchen' }))
    expect((screen.getByLabelText(/^Fällig/) as HTMLInputElement).value).toBe('2026-12-01T09:30')
    fireEvent.change(screen.getByLabelText(/^Fällig/), { target: { value: '2026-12-02T18:45' } })
    await u.click(screen.getByRole('button', { name: 'Speichern' }))
    await waitFor(() =>
      expect(senden).toHaveBeenCalledWith('PATCH', '/api/events/e1/b/aufgaben/a1', {
        faellig: '2026-12-02T18:45',
      }),
    )
  })

  it('Budget: 12,50 wird 1250, 0,1 wird 10', async () => {
    const u = userEvent.setup()
    zeige('budget')
    await screen.findByText('Saalmiete')
    for (const [eingabe, cent] of [
      ['12,50', 1250],
      ['0,1', 10],
    ] as const) {
      senden.mockClear()
      await u.click(screen.getByRole('button', { name: '+ Hinzufügen' }))
      await u.type(screen.getByLabelText(/^Titel/), 'Posten')
      await u.selectOptions(screen.getByLabelText(/^Art/), 'kosten')
      await u.type(screen.getByLabelText(/^Betrag/), eingabe)
      await u.click(screen.getByRole('button', { name: 'Speichern' }))
      await waitFor(() => expect(senden).toHaveBeenCalled())
      expect(senden).toHaveBeenCalledWith('POST', '/api/events/e1/b/budget', {
        titel: 'Posten',
        art: 'kosten',
        betrag_cent: cent,
      })
      await waitFor(() => expect(screen.queryByRole('button', { name: 'Speichern' })).toBeNull())
    }
  })

  it('Budget: abc zeigt einen Hinweis und sendet nichts', async () => {
    const u = userEvent.setup()
    zeige('budget')
    await screen.findByText('Saalmiete')
    await u.click(screen.getByRole('button', { name: '+ Hinzufügen' }))
    await u.type(screen.getByLabelText(/^Titel/), 'Posten')
    await u.selectOptions(screen.getByLabelText(/^Art/), 'kosten')
    await u.type(screen.getByLabelText(/^Betrag/), 'abc')
    await u.click(screen.getByRole('button', { name: 'Speichern' }))
    expect((await screen.findByRole('alert')).textContent).toMatch(/Betrag/)
    expect(senden).not.toHaveBeenCalled()
  })

  it('Neuer Kontakt inline: POST an die Kontakte, danach vorausgewählt', async () => {
    const u = userEvent.setup()
    const neu: Kontakt = { ...KONTAKT, id: 'k2', name: 'Eva Muster', mail: 'eva@example.org' }
    senden.mockImplementation(async (_m: string, pfad: string) => {
      if (pfad === '/api/firmen/f1/kontakte') return neu as never
      return { ok: true } as never
    })
    zeige('orga_team')
    await screen.findByRole('button', { name: '+ Hinzufügen' })
    await u.click(screen.getByRole('button', { name: '+ Hinzufügen' }))
    await u.selectOptions(screen.getByLabelText(/^Kontakt/), '+ Neuer Kontakt …')
    await u.type(screen.getByLabelText('Name'), 'Eva Muster')
    await u.type(screen.getByLabelText('Mail'), 'eva@example.org')
    await u.click(screen.getByRole('button', { name: 'Kontakt anlegen' }))
    await waitFor(() =>
      expect(senden).toHaveBeenCalledWith('POST', '/api/firmen/f1/kontakte', {
        name: 'Eva Muster',
        mail: 'eva@example.org',
      }),
    )
    const auswahl = (await screen.findByLabelText(/^Kontakt/)) as HTMLSelectElement
    await waitFor(() => expect(auswahl.selectedOptions[0]?.textContent).toBe('Eva Muster'))
    await u.click(screen.getByRole('button', { name: 'Speichern' }))
    await waitFor(() =>
      expect(senden).toHaveBeenCalledWith('POST', '/api/events/e1/b/orga_team', { kontakt_id: 'k2' }),
    )
  })

  it('Bearbeiten sendet nur Geändertes per PATCH', async () => {
    const u = userEvent.setup()
    zeige('budget')
    const zeile = (await screen.findByText('Saalmiete')).closest('tr') as HTMLElement
    await u.click(within(zeile).getByRole('button', { name: /^Bearbeiten/ }))
    const betrag = screen.getByLabelText(/^Betrag/) as HTMLInputElement
    expect(betrag.value).toBe('1234,50')
    await u.clear(betrag)
    await u.type(betrag, '2000')
    await u.click(screen.getByRole('button', { name: 'Speichern' }))
    await waitFor(() =>
      expect(senden).toHaveBeenCalledWith('PATCH', '/api/events/e1/b/budget/b1', { betrag_cent: 200000 }),
    )
  })

  it('Löschen erst nach Bestätigung', async () => {
    const u = userEvent.setup()
    zeige('budget')
    const zeile = (await screen.findByText('Saalmiete')).closest('tr') as HTMLElement
    await u.click(within(zeile).getByRole('button', { name: /^Löschen/ }))
    expect(senden).not.toHaveBeenCalled()
    const dialog = screen.getByRole('dialog')
    await u.click(within(dialog).getByRole('button', { name: 'Löschen' }))
    await waitFor(() => expect(senden).toHaveBeenCalledWith('DELETE', '/api/events/e1/b/budget/b1'))
  })

  it('Baustein ohne Eintrag zeigt den Hinweis', () => {
    zeige('nachrichten')
    expect(screen.getByText('Dieser Bereich kommt mit dem Bot-Ausbau')).toBeTruthy()
    expect(api.holen).not.toHaveBeenCalled()
  })

  it('422 zeigt detail', async () => {
    const u = userEvent.setup()
    senden.mockRejectedValue(new ApiFehler(422, 'Betrag ist zu hoch'))
    zeige('budget')
    await screen.findByText('Saalmiete')
    await u.click(screen.getByRole('button', { name: '+ Hinzufügen' }))
    await u.type(screen.getByLabelText(/^Titel/), 'Posten')
    await u.selectOptions(screen.getByLabelText(/^Art/), 'kosten')
    await u.type(screen.getByLabelText(/^Betrag/), '5')
    await u.click(screen.getByRole('button', { name: 'Speichern' }))
    expect((await screen.findByRole('alert')).textContent).toContain('Betrag ist zu hoch')
  })

  it('zeigt deutsche Spaltenköpfe und Auswahlwerte (Rohwert bleibt der gesendete Wert)', async () => {
    const u = userEvent.setup()
    zeige('aufgaben')
    await screen.findByText('Halle buchen')
    expect(screen.getByRole('columnheader', { name: 'Fällig' })).toBeTruthy()
    expect(screen.getByRole('columnheader', { name: 'Kategorie' })).toBeTruthy()
    await u.click(screen.getAllByRole('button', { name: '+ Hinzufügen' })[0] as HTMLElement)
    const status = screen.getByLabelText(/^Status/) as HTMLSelectElement
    const arbeit = Array.from(status.options).find((o) => o.value === 'in_arbeit')
    expect(arbeit?.textContent).toBe('in Arbeit')
  })

  it('zeigt Auswahlwerte in der Zelle deutsch', async () => {
    tabellen.budget = [{ id: 'b1', titel: 'Saalmiete', art: 'einnahme', betrag_cent: 100, status: 'bestaetigt' }]
    zeige('budget')
    const zeile = (await screen.findByText('Saalmiete')).closest('tr') as HTMLElement
    expect(within(zeile).getByText('bestätigt')).toBeTruthy()
    expect(within(zeile).getByText('Einnahme')).toBeTruthy()
  })

  it('beim Wechsel des Bausteins verschwinden Löschfehler und Dialog', async () => {
    const u = userEvent.setup()
    const { rerender } = zeige('aufgaben')
    const zeile = (await screen.findByText('Halle buchen')).closest('tr') as HTMLElement
    senden.mockRejectedValueOnce(new ApiFehler(403, 'x'))
    await u.click(within(zeile).getByRole('button', { name: 'Löschen Halle buchen' }))
    await u.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Löschen' }))
    await screen.findByText('Keine Berechtigung')
    rerender(
      <BausteinReiter eventId="e1" firmaId="f1" baustein="budget" begriffe={{}} verzeichnis={VERZEICHNIS} />,
    )
    await screen.findByText('Saalmiete')
    expect(screen.queryByText('Keine Berechtigung')).toBeNull()
  })

  it('nach dem Speichern wird erst neu geladen; ein Ladefehler ist sichtbar, das Formular schließt', async () => {
    const u = userEvent.setup()
    zeige('budget')
    await screen.findByText('Saalmiete')
    await u.click(screen.getByRole('button', { name: '+ Hinzufügen' }))
    await u.type(screen.getByLabelText(/^Titel/), 'Posten')
    await u.selectOptions(screen.getByLabelText(/^Art/), 'kosten')
    await u.type(screen.getByLabelText(/^Betrag/), '5')
    delete tabellen.budget
    await u.click(screen.getByRole('button', { name: 'Speichern' }))
    await screen.findByText('Nicht gefunden')
    expect(senden).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('button', { name: 'Speichern' })).toBeNull()
  })
})
