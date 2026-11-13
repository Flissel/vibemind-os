import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { IchProvider } from '@/components/IchKontext'
import { NachrichtenReiter } from '@/components/event/NachrichtenReiter'
import { ApiFehler, api } from '@/lib/api'
import type { Ich, Nachricht } from '@/lib/typen'

const LEITUNG: Ich = {
  person: { id: 'p1', name: 'Lea Beispiel', mail: 'lea@example.org' },
  betreiber: false,
  firmen: [{ id: 'f1', name: 'Beispiel GmbH', kurz: 'bsp', rolle: 'leitung' }],
  csrf: 't',
  rechte: { event_anlegen: ['f1'], verwalten: ['f1'] },
}
const MITGLIED: Ich = {
  ...LEITUNG,
  firmen: [{ id: 'f1', name: 'Beispiel GmbH', kurz: 'bsp', rolle: 'mitglied' }],
  rechte: { event_anlegen: [], verwalten: [] },
}

function nachricht(o: Partial<Nachricht>): Nachricht {
  return {
    id: 'n0',
    event_id: 'e1',
    kanal: 'mail',
    betreff: null,
    text: 'Text',
    status: 'entwurf',
    angelegt: '2026-11-10T09:30:00+01:00',
    erstellt_von: 'Lea Beispiel',
    freigegeben: null,
    versendet: null,
    fehler: null,
    zusage: ['zugesagt'],
    empfaenger: 0,
    ...o,
  }
}

let liste: Nachricht[]
let senden: ReturnType<typeof vi.spyOn>

beforeEach(() => {
  liste = [
    nachricht({ id: 'n1', betreff: 'Programm am Samstag', empfaenger: 12 }),
    nachricht({
      id: 'n2',
      kanal: 'gruppe',
      text: 'Hallo zusammen, der Einlass beginnt heute schon um 17 Uhr – bitte pünktlich sein und Ausweis mitbringen!',
      status: 'versendet',
      zusage: null,
      empfaenger: null,
    }),
    nachricht({ id: 'n3', betreff: 'Danke', status: 'versendet', empfaenger: 7 }),
  ]
  vi.spyOn(api, 'holen').mockImplementation(async () => liste as never)
  senden = vi.spyOn(api, 'senden').mockImplementation(async (_m, pfad: string) => {
    if (pfad.endsWith('/gruppe')) return nachricht({ id: 'neu', kanal: 'gruppe', status: 'versendet' }) as never
    if (pfad.endsWith('/mail')) return nachricht({ id: 'neu', empfaenger: 5 }) as never
    return nachricht({ status: 'freigegeben' }) as never
  })
})
afterEach(() => {
  vi.restoreAllMocks()
})

function zeige(ich: Ich = LEITUNG) {
  render(
    <IchProvider ich={ich}>
      <NachrichtenReiter eventId="e1" firmaId="f1" />
    </IchProvider>,
  )
}

async function zeileVon(text: string | RegExp): Promise<HTMLElement> {
  return (await screen.findByText(text)).closest('tr') as HTMLElement
}

describe('NachrichtenReiter', () => {
  it('Liste zeigt Kanal, Betreff/Anfang, Status und Empfängerzahl', async () => {
    zeige()
    const entwurf = await zeileVon('Programm am Samstag')
    expect(api.holen).toHaveBeenCalledWith('/api/events/e1/nachrichten')
    expect(within(entwurf).getByText('Mail')).toBeTruthy()
    expect(within(entwurf).getByText('Entwurf')).toBeTruthy()
    expect(within(entwurf).getByText('12')).toBeTruthy()
    const gruppe = await zeileVon(/^Hallo zusammen, der Einlass/)
    expect(within(gruppe).getByText('Gruppe')).toBeTruthy()
    expect(within(gruppe).getByText('versendet')).toBeTruthy()
    expect(within(gruppe).getByText('–')).toBeTruthy()
    // Langer Text wird gekürzt.
    expect(within(gruppe).queryByText(/Ausweis/)).toBeNull()
    // Nur Entwürfe haben Aktionen.
    expect(within(await zeileVon('Danke')).queryByRole('button')).toBeNull()
  })

  it('Freigeben nur bei darfVerwalten, mit Bestätigung und Empfängerzahl', async () => {
    const u = userEvent.setup()
    zeige(MITGLIED)
    let zeile = await zeileVon('Programm am Samstag')
    expect(within(zeile).queryByRole('button', { name: /^Freigeben/ })).toBeNull()
    expect(within(zeile).getByRole('button', { name: /^Verwerfen/ })).toBeTruthy()

    cleanup()
    zeige(LEITUNG)
    zeile = await zeileVon('Programm am Samstag')
    await u.click(within(zeile).getByRole('button', { name: /^Freigeben/ }))
    expect(senden).not.toHaveBeenCalled()
    const dialog = screen.getByRole('dialog')
    expect(dialog.textContent).toContain('12 Empfänger')
    await u.click(within(dialog).getByRole('button', { name: 'Freigeben' }))
    await waitFor(() => expect(senden).toHaveBeenCalledWith('POST', '/api/nachrichten/n1/freigeben'))
    expect(await screen.findByRole('status')).toBeTruthy()
    expect(api.holen).toHaveBeenCalledTimes(3) // zweimal gezeigt, danach neu geladen
  })

  it('Betreiber darf freigeben', async () => {
    zeige({ ...MITGLIED, betreiber: true })
    const zeile = await zeileVon('Programm am Samstag')
    expect(within(zeile).getByRole('button', { name: /^Freigeben/ })).toBeTruthy()
  })

  it('Verwerfen sendet POST', async () => {
    const u = userEvent.setup()
    zeige(MITGLIED)
    const zeile = await zeileVon('Programm am Samstag')
    await u.click(within(zeile).getByRole('button', { name: /^Verwerfen/ }))
    await waitFor(() => expect(senden).toHaveBeenCalledWith('POST', '/api/nachrichten/n1/verwerfen'))
  })

  it('Entwurf-Formular sendet zusage', async () => {
    const u = userEvent.setup()
    zeige(MITGLIED)
    await screen.findByText('Programm am Samstag')
    await u.click(screen.getByRole('button', { name: 'Mail entwerfen' }))
    const form = screen.getByRole('form', { name: 'Mail entwerfen' })
    await u.type(within(form).getByLabelText('Betreff'), '  Einladung  ')
    await u.type(within(form).getByLabelText('Text'), 'Hallo')
    expect((within(form).getByLabelText('zugesagt') as HTMLInputElement).checked).toBe(true)
    await u.click(within(form).getByLabelText('vielleicht'))
    await u.click(within(form).getByRole('button', { name: 'Entwurf speichern' }))
    await waitFor(() =>
      expect(senden).toHaveBeenCalledWith('POST', '/api/events/e1/nachrichten/mail', {
        betreff: 'Einladung',
        text: 'Hallo',
        zusage: ['zugesagt', 'vielleicht'],
      }),
    )
    expect((await screen.findByRole('status')).textContent).toContain('5 Empfänger')
  })

  it('Entwurf ohne Zusage wird nicht gesendet', async () => {
    const u = userEvent.setup()
    zeige()
    await screen.findByText('Programm am Samstag')
    await u.click(screen.getByRole('button', { name: 'Mail entwerfen' }))
    const form = screen.getByRole('form', { name: 'Mail entwerfen' })
    await u.type(within(form).getByLabelText('Betreff'), 'B')
    await u.type(within(form).getByLabelText('Text'), 'T')
    await u.click(within(form).getByLabelText('zugesagt'))
    await u.click(within(form).getByRole('button', { name: 'Entwurf speichern' }))
    expect(senden).not.toHaveBeenCalled()
    expect((await screen.findByRole('alert')).textContent).toContain('Zusage')
  })

  it('An Gruppe schreiben sendet den Text; Telegram-Fehler wird gemeldet', async () => {
    const u = userEvent.setup()
    zeige(MITGLIED)
    await screen.findByText('Programm am Samstag')
    await u.click(screen.getByRole('button', { name: 'An Gruppe schreiben' }))
    let form = screen.getByRole('form', { name: 'An Gruppe schreiben' })
    await u.type(within(form).getByLabelText('Text'), ' Hallo Gruppe ')
    await u.click(within(form).getByRole('button', { name: 'Senden' }))
    await waitFor(() =>
      expect(senden).toHaveBeenCalledWith('POST', '/api/events/e1/nachrichten/gruppe', { text: 'Hallo Gruppe' }),
    )
    expect((await screen.findByRole('status')).textContent).toContain('gesendet')

    senden.mockResolvedValueOnce(nachricht({ kanal: 'gruppe', status: 'fehler' }) as never)
    await u.click(screen.getByRole('button', { name: 'An Gruppe schreiben' }))
    form = screen.getByRole('form', { name: 'An Gruppe schreiben' })
    await u.type(within(form).getByLabelText('Text'), 'Nochmal')
    await u.click(within(form).getByRole('button', { name: 'Senden' }))
    expect((await screen.findByRole('alert')).textContent).toContain('fehlgeschlagen')
  })

  it('422 der API zeigt detail', async () => {
    const u = userEvent.setup()
    senden.mockRejectedValueOnce(new ApiFehler(422, 'Keine Telegram-Gruppe verbunden'))
    zeige()
    await screen.findByText('Programm am Samstag')
    await u.click(screen.getByRole('button', { name: 'An Gruppe schreiben' }))
    await u.type(screen.getByLabelText('Text'), 'Hallo')
    await u.click(screen.getByRole('button', { name: 'Senden' }))
    expect((await screen.findByRole('alert')).textContent).toContain('Keine Telegram-Gruppe verbunden')
  })

  it('leere Liste', async () => {
    liste = []
    zeige()
    expect(await screen.findByText('Noch keine Nachrichten')).toBeTruthy()
  })
})
