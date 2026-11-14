import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Anmelden from '@/app/anmelden/page'
import { ApiFehler, api } from '@/lib/api'

const push = vi.fn<(pfad: string) => void>()
vi.mock('next/navigation', () => ({ useRouter: () => ({ push }) }))

async function absenden(): Promise<void> {
  await userEvent.type(screen.getByLabelText('Mail'), 'lea@example.org')
  await userEvent.type(screen.getByLabelText('Passwort'), 'geheim-geheim-1')
  await userEvent.click(screen.getByRole('button', { name: 'Anmelden' }))
}

describe('Anmelden', () => {
  beforeEach(() => {
    push.mockReset()
    window.history.pushState({}, '', '/anmelden')
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('leitet nach Erfolg auf weiter', async () => {
    vi.spyOn(api, 'anmelden').mockResolvedValue({} as never)
    window.history.pushState({}, '', '/anmelden?weiter=' + encodeURIComponent('/events/x'))
    render(<Anmelden />)
    await absenden()
    await waitFor(() => expect(push).toHaveBeenCalledWith('/events/x'))
    expect(api.anmelden).toHaveBeenCalledWith('lea@example.org', 'geheim-geheim-1')
  })

  it('ignoriert ein fremdes weiter (//…)', async () => {
    vi.spyOn(api, 'anmelden').mockResolvedValue({} as never)
    window.history.pushState({}, '', '/anmelden?weiter=' + encodeURIComponent('//boese.example'))
    render(<Anmelden />)
    await absenden()
    await waitFor(() => expect(push).toHaveBeenCalledWith('/'))
  })

  it('ignoriert ein absolutes weiter', async () => {
    vi.spyOn(api, 'anmelden').mockResolvedValue({} as never)
    window.history.pushState({}, '', '/anmelden?weiter=' + encodeURIComponent('https://boese.example/x'))
    render(<Anmelden />)
    await absenden()
    await waitFor(() => expect(push).toHaveBeenCalledWith('/'))
  })

  it('zeigt bei 401 die Meldung', async () => {
    vi.spyOn(api, 'anmelden').mockRejectedValue(new ApiFehler(401, 'x'))
    render(<Anmelden />)
    await absenden()
    expect(
      await screen.findByText('Mail oder Passwort falsch, oder zu viele Versuche. Bitte später erneut versuchen.'),
    ).toBeTruthy()
    expect(push).not.toHaveBeenCalled()
  })

  it('zeigt den Hinweis nach dem Einlösen', async () => {
    window.history.pushState({}, '', '/anmelden?hinweis=konto-bereit')
    render(<Anmelden />)
    expect(await screen.findByText('Konto bereit, bitte anmelden')).toBeTruthy()
  })
})
