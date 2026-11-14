import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Einladung from '@/app/einladung/[token]/page'
import { api } from '@/lib/api'

const push = vi.fn<(pfad: string) => void>()
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push }),
  useParams: () => ({ token: 'tok/en' }),
}))

describe('Einladung einlösen', () => {
  beforeEach(() => {
    push.mockReset()
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('zeigt bei ungültiger Einladung den Text', async () => {
    vi.spyOn(api, 'holen').mockResolvedValue({ gueltig: false })
    render(<Einladung />)
    expect(await screen.findByText('Diese Einladung ist ungültig oder abgelaufen.')).toBeTruthy()
    expect(api.holen).toHaveBeenCalledWith('/api/einladung/tok%2Fen')
  })

  it('zu kurzes Passwort: Hinweis, kein Request', async () => {
    vi.spyOn(api, 'holen').mockResolvedValue({ gueltig: true, firma_name: 'Beispiel GmbH', braucht_passwort: true })
    const senden = vi.spyOn(api, 'sendenOeffentlich').mockResolvedValue({ eingeloest: true })
    render(<Einladung />)
    expect(await screen.findByText('Einladung zu Beispiel GmbH')).toBeTruthy()
    await userEvent.type(screen.getByLabelText('Passwort'), 'kurz')
    await userEvent.type(screen.getByLabelText('Passwort wiederholen'), 'kurz')
    await userEvent.click(screen.getByRole('button', { name: 'Einladung annehmen' }))
    expect(screen.getByText('Das Passwort muss mindestens 12 Zeichen lang sein.')).toBeTruthy()
    expect(senden).not.toHaveBeenCalled()
  })

  it('ungleiche Passwörter: Hinweis, kein Request', async () => {
    vi.spyOn(api, 'holen').mockResolvedValue({ gueltig: true, firma_name: 'Beispiel GmbH', braucht_passwort: true })
    const senden = vi.spyOn(api, 'sendenOeffentlich').mockResolvedValue({ eingeloest: true })
    render(<Einladung />)
    await screen.findByText('Einladung zu Beispiel GmbH')
    await userEvent.type(screen.getByLabelText('Passwort'), 'ein-langes-passwort-1')
    await userEvent.type(screen.getByLabelText('Passwort wiederholen'), 'ein-langes-passwort-2')
    await userEvent.click(screen.getByRole('button', { name: 'Einladung annehmen' }))
    expect(screen.getByText('Die Passwörter stimmen nicht überein.')).toBeTruthy()
    expect(senden).not.toHaveBeenCalled()
  })

  it('Erfolg: sendet Passwort und Name, leitet mit Hinweis zur Anmeldung', async () => {
    vi.spyOn(api, 'holen').mockResolvedValue({ gueltig: true, firma_name: 'Beispiel GmbH', braucht_passwort: true })
    const senden = vi.spyOn(api, 'sendenOeffentlich').mockResolvedValue({ eingeloest: true })
    render(<Einladung />)
    await screen.findByText('Einladung zu Beispiel GmbH')
    await userEvent.type(screen.getByLabelText('Name'), 'Lea Beispiel')
    await userEvent.type(screen.getByLabelText('Passwort'), 'ein-langes-passwort-1')
    await userEvent.type(screen.getByLabelText('Passwort wiederholen'), 'ein-langes-passwort-1')
    await userEvent.click(screen.getByRole('button', { name: 'Einladung annehmen' }))
    await waitFor(() => expect(push).toHaveBeenCalledWith('/anmelden?hinweis=konto-bereit'))
    expect(senden).toHaveBeenCalledWith('/api/einladung/tok%2Fen', {
      passwort: 'ein-langes-passwort-1',
      name: 'Lea Beispiel',
    })
  })

  it('ohne Passwortbedarf nur "Einladung annehmen"', async () => {
    vi.spyOn(api, 'holen').mockResolvedValue({ gueltig: true, firma_name: 'Beispiel GmbH', braucht_passwort: false })
    const senden = vi.spyOn(api, 'sendenOeffentlich').mockResolvedValue({ eingeloest: true })
    render(<Einladung />)
    await screen.findByText('Einladung zu Beispiel GmbH')
    expect(screen.queryByLabelText('Passwort')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Einladung annehmen' }))
    await waitFor(() => expect(push).toHaveBeenCalledWith('/anmelden?hinweis=konto-bereit'))
    expect(senden).toHaveBeenCalledWith('/api/einladung/tok%2Fen', {})
  })
})
