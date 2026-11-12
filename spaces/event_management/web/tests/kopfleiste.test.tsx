import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { IchProvider } from '@/components/AnmeldeSchutz'
import { Kopfleiste } from '@/components/Kopfleiste'
import { api } from '@/lib/api'
import type { Ich } from '@/lib/typen'

function ich(extra: Partial<Ich> = {}): Ich {
  return {
    person: { id: 'p1', name: 'Lea Beispiel', mail: 'lea@example.org' },
    betreiber: false,
    firmen: [{ id: 'f1', name: 'Beispiel GmbH', kurz: 'beispiel', rolle: 'mitglied' }],
    csrf: 't',
    rechte: { event_anlegen: [], verwalten: [] },
    ...extra,
  }
}

function zeige(wer: Ich, nachAbmeldung: () => void = () => undefined): void {
  render(
    <IchProvider ich={wer}>
      <Kopfleiste nachAbmeldung={nachAbmeldung} />
    </IchProvider>,
  )
}

describe('Kopfleiste', () => {
  beforeEach(() => {
    vi.spyOn(api, 'abmelden').mockResolvedValue(undefined)
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('zeigt Logo, Katalog und den Personennamen', () => {
    zeige(ich())
    expect(screen.getByText('VibeMind Events')).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Katalog' }).getAttribute('href')).toBe('/')
    expect(screen.getByText('Lea Beispiel')).toBeTruthy()
  })

  it('ein Mitglied ohne Verwaltungsrecht sieht "Firmen" nicht', () => {
    zeige(ich())
    expect(screen.queryByRole('link', { name: 'Firmen' })).toBeNull()
  })

  it('wer eine Firma verwalten darf, sieht "Firmen"', () => {
    zeige(ich({ rechte: { event_anlegen: ['f1'], verwalten: ['f1'] } }))
    expect(screen.getByRole('link', { name: 'Firmen' }).getAttribute('href')).toBe('/firmen')
  })

  it('ein Betreiber sieht "Firmen"', () => {
    zeige(ich({ betreiber: true }))
    expect(screen.getByRole('link', { name: 'Firmen' })).toBeTruthy()
  })

  it('"Abmelden" ruft die API und geht danach zur Anmeldung', async () => {
    const danach = vi.fn()
    zeige(ich(), danach)
    await userEvent.click(screen.getByRole('button', { name: 'Abmelden' }))
    await waitFor(() => expect(danach).toHaveBeenCalledTimes(1))
    expect(api.abmelden).toHaveBeenCalledTimes(1)
  })
})
