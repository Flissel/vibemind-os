import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import FirmaSeite from '@/app/firmen/[id]/page'
import FirmenSeite from '@/app/firmen/page'
import { ApiFehler, api } from '@/lib/api'
import type { Ich, IchFirma, Kontakt, Mitglied } from '@/lib/typen'

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'f1' }),
  useRouter: () => ({ push: vi.fn() }),
}))

const FIRMA: IchFirma = { id: 'f1', name: 'Beispiel GmbH', kurz: 'bg', rolle: 'leitung' }
const LEITUNG: Ich = {
  person: { id: 'p1', name: 'Lea Beispiel', mail: 'lea@example.org' },
  betreiber: false,
  firmen: [FIRMA],
  csrf: 't',
  rechte: { event_anlegen: ['f1'], verwalten: ['f1'] },
}
const MITGLIED: Ich = {
  ...LEITUNG,
  firmen: [{ ...FIRMA, rolle: 'mitglied' }],
  rechte: { event_anlegen: [], verwalten: [] },
}
const BETREIBER: Ich = { ...LEITUNG, betreiber: true, rechte: { event_anlegen: [], verwalten: [] } }

const MITGLIEDER: Mitglied[] = [
  { person_id: 'p1', name: 'Lea Beispiel', mail: 'lea@example.org', rolle: 'leitung' },
  { person_id: 'p2', name: 'Max Muster', mail: 'max@example.org', rolle: 'mitglied' },
]
const KONTAKTE: Kontakt[] = [
  {
    id: 'k1',
    firma_id: 'f1',
    name: 'Erika Kontakt',
    mail: 'erika@example.org',
    telegram: null,
    person_id: null,
    angelegt: '2026-11-01T10:00:00+01:00',
  },
]

let senden: ReturnType<typeof vi.spyOn>

function mocke(ich: Ich) {
  vi.spyOn(api, 'ich').mockResolvedValue(ich)
  vi.spyOn(api, 'holen').mockImplementation(async (pfad: string) => {
    if (pfad === '/api/firmen') return ich.firmen as never
    if (pfad === '/api/firmen/f1/mitglieder') return MITGLIEDER as never
    if (pfad === '/api/firmen/f1/kontakte') return KONTAKTE as never
    return [] as never
  })
  senden = vi.spyOn(api, 'senden').mockResolvedValue({} as never)
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('/firmen', () => {
  it('Betreiber sieht "+ Firma anlegen", die Leitung nicht', async () => {
    mocke(BETREIBER)
    const { unmount } = render(<FirmenSeite />)
    await screen.findByText('Beispiel GmbH')
    expect(screen.getByRole('button', { name: '+ Firma anlegen' })).toBeTruthy()
    unmount()
    mocke(LEITUNG)
    render(<FirmenSeite />)
    await screen.findByText('Beispiel GmbH')
    expect(screen.queryByRole('button', { name: '+ Firma anlegen' })).toBeNull()
  })

  it('zeigt Kürzel und eigene Rolle', async () => {
    mocke(LEITUNG)
    render(<FirmenSeite />)
    await screen.findByText('Beispiel GmbH')
    expect(screen.getByText('bg')).toBeTruthy()
    expect(screen.getByText('Leitung')).toBeTruthy()
  })

  it('ungültiges Kürzel: Hinweis, kein Request', async () => {
    mocke(BETREIBER)
    render(<FirmenSeite />)
    await userEvent.click(await screen.findByRole('button', { name: '+ Firma anlegen' }))
    await userEvent.type(screen.getByLabelText('Name'), 'Beispiel GmbH')
    await userEvent.type(screen.getByLabelText('Kürzel'), 'Beispiel GmbH')
    await userEvent.click(screen.getByRole('button', { name: 'Anlegen' }))
    expect(screen.getByRole('alert').textContent).toContain('Kürzel')
    expect(senden).not.toHaveBeenCalled()
  })

  it('gültiges Kürzel sendet name und kurz', async () => {
    mocke(BETREIBER)
    render(<FirmenSeite />)
    await userEvent.click(await screen.findByRole('button', { name: '+ Firma anlegen' }))
    await userEvent.type(screen.getByLabelText('Name'), 'Neue GmbH')
    await userEvent.type(screen.getByLabelText('Kürzel'), 'neue-gmbh')
    await userEvent.click(screen.getByRole('button', { name: 'Anlegen' }))
    await waitFor(() =>
      expect(senden).toHaveBeenCalledWith('POST', '/api/firmen', { name: 'Neue GmbH', kurz: 'neue-gmbh' }),
    )
  })
})

describe('/firmen/[id]', () => {
  beforeEach(() => {
    mocke(LEITUNG)
  })

  it('Mitglied sieht weder Einladen noch Rolle ändern noch Entfernen', async () => {
    mocke(MITGLIED)
    render(<FirmaSeite />)
    await screen.findByText('Max Muster')
    expect(screen.queryByRole('button', { name: 'Einladen' })).toBeNull()
    expect(screen.queryByLabelText(/Rolle ändern/)).toBeNull()
    expect(screen.queryByRole('button', { name: /Max Muster entfernen/ })).toBeNull()
  })

  it('Leitung ändert die Rolle per PATCH', async () => {
    render(<FirmaSeite />)
    await screen.findByText('Max Muster')
    await userEvent.selectOptions(screen.getByLabelText('Rolle ändern: Max Muster'), 'leitung')
    await waitFor(() =>
      expect(senden).toHaveBeenCalledWith('PATCH', '/api/firmen/f1/mitglieder/p2', { rolle: 'leitung' }),
    )
  })

  it('die letzte Leitung wird nicht entfernt: detail wird gezeigt', async () => {
    render(<FirmaSeite />)
    await screen.findByText('Max Muster')
    senden.mockRejectedValueOnce(new ApiFehler(422, 'Die letzte Leitung kann nicht entfernt werden'))
    await userEvent.click(screen.getByRole('button', { name: 'Lea Beispiel entfernen' }))
    const dialog = screen.getByRole('dialog')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Entfernen' }))
    await screen.findByText('Die letzte Leitung kann nicht entfernt werden')
    expect(senden).toHaveBeenCalledWith('DELETE', '/api/firmen/f1/mitglieder/p1')
  })

  it('Einladung sendet Mail, Name und Rolle', async () => {
    render(<FirmaSeite />)
    await screen.findByText('Max Muster')
    await userEvent.type(screen.getByLabelText('Mail'), 'neu@example.org')
    await userEvent.type(screen.getByLabelText('Name'), 'Nina Neu')
    await userEvent.selectOptions(screen.getByLabelText('Rolle'), 'leitung')
    await userEvent.click(screen.getByRole('button', { name: 'Einladen' }))
    await waitFor(() =>
      expect(senden).toHaveBeenCalledWith('POST', '/api/firmen/f1/einladungen', {
        mail: 'neu@example.org',
        name: 'Nina Neu',
        rolle: 'leitung',
      }),
    )
    await screen.findByText('Einladung verschickt')
  })

  it('Kontakt bearbeiten sendet nur geänderte Felder', async () => {
    render(<FirmaSeite />)
    await screen.findByText('Erika Kontakt')
    await userEvent.click(screen.getByRole('button', { name: 'Erika Kontakt bearbeiten' }))
    await userEvent.type(screen.getByLabelText('Telegram'), '@erika')
    await userEvent.click(screen.getByRole('button', { name: 'Speichern' }))
    await waitFor(() =>
      expect(senden).toHaveBeenCalledWith('PATCH', '/api/firmen/f1/kontakte/k1', { telegram: '@erika' }),
    )
  })

  it('Kontakt entfernen, 422: Meldung', async () => {
    render(<FirmaSeite />)
    await screen.findByText('Erika Kontakt')
    senden.mockRejectedValueOnce(new ApiFehler(422, 'Kontakt wird noch verwendet'))
    await userEvent.click(screen.getByRole('button', { name: 'Erika Kontakt entfernen' }))
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Entfernen' }))
    await screen.findByText('Kontakt wird noch verwendet')
    expect(senden).toHaveBeenCalledWith('DELETE', '/api/firmen/f1/kontakte/k1')
  })

  it('Mitglied kann Kontakte anlegen, aber nicht entfernen', async () => {
    mocke(MITGLIED)
    render(<FirmaSeite />)
    await screen.findByText('Erika Kontakt')
    expect(screen.queryByRole('button', { name: 'Erika Kontakt entfernen' })).toBeNull()
    await userEvent.type(screen.getByLabelText('Neuer Kontakt: Name'), 'Karl Kontakt')
    await userEvent.click(screen.getByRole('button', { name: 'Kontakt anlegen' }))
    await waitFor(() =>
      expect(senden).toHaveBeenCalledWith('POST', '/api/firmen/f1/kontakte', { name: 'Karl Kontakt' }),
    )
  })
})
