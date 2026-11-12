import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AnmeldeSchutz, useIch } from '@/components/AnmeldeSchutz'
import { api, setzeBeiAbmeldung } from '@/lib/api'
import type { Ich } from '@/lib/typen'

const ICH: Ich = {
  person: { id: 'p1', name: 'Lea Beispiel', mail: 'lea@example.org' },
  betreiber: false,
  firmen: [],
  csrf: 't',
  rechte: { event_anlegen: [], verwalten: [] },
}

function Name() {
  return <span>{useIch().person.name}</span>
}

describe('AnmeldeSchutz', () => {
  const beiAbmeldung = vi.fn<(pfad: string) => void>()
  beforeEach(() => {
    beiAbmeldung.mockReset()
    setzeBeiAbmeldung(beiAbmeldung)
    window.history.pushState({}, '', '/events/abc')
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('leitet ohne Anmeldung zur Anmeldung mit weiter', async () => {
    vi.spyOn(api, 'ich').mockResolvedValue(null)
    render(<AnmeldeSchutz><Name /></AnmeldeSchutz>)
    await waitFor(() => expect(beiAbmeldung).toHaveBeenCalledWith('/events/abc'))
  })

  it('stellt ich per Context bereit', async () => {
    vi.spyOn(api, 'ich').mockResolvedValue(ICH)
    render(<AnmeldeSchutz><Name /></AnmeldeSchutz>)
    expect(await screen.findByText('Lea Beispiel')).toBeTruthy()
  })
})
