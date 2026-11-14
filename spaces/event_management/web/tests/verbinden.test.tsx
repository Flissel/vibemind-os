import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import TelegramPage from '@/app/ich/telegram/page'
import { IchProvider } from '@/components/IchKontext'
import { Kopfleiste } from '@/components/Kopfleiste'
import { ABFRAGE_MS, TelegramVerbinden } from '@/components/TelegramVerbinden'
import { UebersichtReiter } from '@/components/event/UebersichtReiter'
import { api, ApiFehler } from '@/lib/api'
import type { EventDetail, Ich, Verbindung, VerbindungsCode } from '@/lib/typen'

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }))

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
const BETREIBER: Ich = { ...MITGLIED, betreiber: true, firmen: [] }

const JETZT = new Date('2026-11-13T10:00:00Z')
const PFAD = '/api/events/e1/verbinden'
const CODE: VerbindungsCode = { code: 'ABCD2345', gueltig_bis: '2026-11-13T10:15:00+00:00' }

function verbindung(o: Partial<Verbindung>): Verbindung {
  return {
    id: 'v0',
    art: 'gruppe',
    status: 'offen',
    chat_titel: null,
    ablauf: CODE.gueltig_bis,
    gemeldet: null,
    bestaetigt: null,
    pruefcode_zugestellt: false,
    ...o,
  }
}

const OFFEN = verbindung({ id: 'v1' })
const GEMELDET = verbindung({
  id: 'v1',
  status: 'gemeldet',
  chat_titel: 'Beispielgruppe',
  gemeldet: '2026-11-13T10:01:00+00:00',
  pruefcode_zugestellt: true,
})
const BESTAETIGT = verbindung({
  ...GEMELDET,
  status: 'bestaetigt',
  bestaetigt: '2026-11-13T10:02:00+00:00',
})

let liste: Verbindung[]
let senden: ReturnType<typeof vi.spyOn>

beforeEach(() => {
  liste = []
  vi.spyOn(api, 'holen').mockImplementation(async () => liste as never)
  senden = vi.spyOn(api, 'senden').mockImplementation(async (_m, pfad: string) => {
    if (pfad.endsWith('/verbinden')) {
      liste = [OFFEN]
      return CODE as never
    }
    if (pfad.endsWith('/bestaetigen')) {
      liste = [BESTAETIGT]
      return { id: 'v1', status: 'bestaetigt', bereitstellung: 'b1' } as never
    }
    if (pfad.endsWith('/trennen')) {
      liste = []
      return { id: 'v1', status: 'verworfen', bereitstellung: 'b2' } as never
    }
    return verbindung({ status: 'verworfen' }) as never
  })
})
afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

function zeige(ich: Ich = LEITUNG, darf = true) {
  render(
    <IchProvider ich={ich}>
      <TelegramVerbinden art="gruppe" titel="Telegram-Gruppe" pfad={PFAD} darf={darf} />
    </IchProvider>,
  )
}

function abfragen(): number {
  return (api.holen as unknown as { mock: { calls: unknown[] } }).mock.calls.length
}

/** Nur Intervall und Uhr sind künstlich; setTimeout bleibt echt (für findBy/waitFor). */
function kuenstlicheZeit() {
  vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'Date'] })
  vi.setSystemTime(JETZT)
}

async function weiter(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

describe('TelegramVerbinden', () => {
  it('Code erzeugen zeigt den Code und die drei Schritte', async () => {
    zeige()
    expect(await screen.findByText('Noch keine Telegram-Gruppe verbunden.')).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Code erzeugen' }))
    expect(senden).toHaveBeenCalledWith('POST', PFAD)
    expect(await screen.findByText('ABCD2345')).toBeTruthy()
    const schritte = screen.getAllByRole('listitem')
    expect(schritte).toHaveLength(3)
    expect(schritte[0]?.textContent).toContain('zur Telegram-Gruppe hinzu')
    expect(schritte[1]?.textContent).toContain('ABCD2345')
    expect(schritte[2]?.textContent).toContain('Bestätige')
    // Hinweis auf den Prüfcode, den der Bot danach in die Gruppe schreibt (F1).
    expect(schritte[2]?.textContent).toContain('Prüfcode')
    expect(screen.getByRole('button', { name: 'Neuen Code erzeugen' })).toBeTruthy()
  })

  it('Bestätigen und Verwerfen nur mit Recht; ohne Recht auch kein Code', async () => {
    liste = [GEMELDET]
    zeige(MITGLIED, false)
    const zeile = (await screen.findByText('Beispielgruppe')).closest('li') as HTMLElement
    expect(within(zeile).queryByRole('button')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Code erzeugen' })).toBeNull()

    cleanup()
    const u = userEvent.setup()
    zeige(LEITUNG, true)
    const leitung = (await screen.findByText('Beispielgruppe')).closest('li') as HTMLElement
    await u.click(within(leitung).getByRole('button', { name: 'Bestätigen' }))
    expect(senden).not.toHaveBeenCalled()
    const dialog = screen.getByRole('dialog')
    expect(dialog.textContent).toContain('Beispielgruppe')
    // Erst mit dem Prüfcode aus dem Chat lässt sich bestätigen.
    const verbinden = within(dialog).getByRole('button', { name: 'Verbinden' }) as HTMLButtonElement
    expect(verbinden.disabled).toBe(true)
    await u.type(within(dialog).getByLabelText('Code aus dem Telegram-Chat'), ' ab2cd3 ')
    expect(verbinden.disabled).toBe(false)
    await u.click(verbinden)
    await waitFor(() =>
      expect(senden).toHaveBeenCalledWith('POST', '/api/verbindungen/v1/bestaetigen', { pruefcode: 'AB2CD3' }),
    )
    expect(await screen.findByRole('status')).toBeTruthy()
    expect(await screen.findByText(/Verbunden mit/)).toBeTruthy()
  })

  it('Verwerfen ruft die API', async () => {
    liste = [GEMELDET]
    zeige()
    const zeile = (await screen.findByText('Beispielgruppe')).closest('li') as HTMLElement
    await userEvent.click(within(zeile).getByRole('button', { name: 'Verwerfen' }))
    await waitFor(() => expect(senden).toHaveBeenCalledWith('POST', '/api/verbindungen/v1/verwerfen'))
  })

  it('falscher Prüfcode: Meldung der API, kein Erfolg', async () => {
    liste = [GEMELDET]
    senden.mockRejectedValueOnce(new ApiFehler(422, 'Prüfcode falsch'))
    const u = userEvent.setup()
    zeige()
    const zeile = (await screen.findByText('Beispielgruppe')).closest('li') as HTMLElement
    await u.click(within(zeile).getByRole('button', { name: 'Bestätigen' }))
    const dialog = screen.getByRole('dialog')
    await u.type(within(dialog).getByLabelText('Code aus dem Telegram-Chat'), 'ZZZZZZ')
    await u.click(within(dialog).getByRole('button', { name: 'Verbinden' }))
    expect((await screen.findByRole('alert')).textContent).toContain('Prüfcode falsch')
    expect(screen.queryByText(/Verbunden mit/)).toBeNull()
  })

  it('Prüfcode nicht zustellbar: kein Bestätigen, Verwerfen angeboten', async () => {
    liste = [verbindung({ ...GEMELDET, pruefcode_zugestellt: false })]
    zeige()
    const zeile = (await screen.findByText('Beispielgruppe')).closest('li') as HTMLElement
    expect(zeile.textContent).toContain('Prüfcode nicht zustellbar')
    expect(within(zeile).queryByRole('button', { name: 'Bestätigen' })).toBeNull()
    await userEvent.click(within(zeile).getByRole('button', { name: 'Verwerfen' }))
    await waitFor(() => expect(senden).toHaveBeenCalledWith('POST', '/api/verbindungen/v1/verwerfen'))
  })

  it('Verbindung trennen nur mit Recht und nach Rückfrage', async () => {
    liste = [BESTAETIGT]
    zeige(MITGLIED, false)
    expect(await screen.findByText(/Verbunden mit/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Verbindung trennen' })).toBeNull()

    cleanup()
    const u = userEvent.setup()
    zeige(LEITUNG, true)
    await u.click(await screen.findByRole('button', { name: 'Verbindung trennen' }))
    expect(senden).not.toHaveBeenCalled()
    const dialog = screen.getByRole('dialog')
    expect(dialog.textContent).toContain('Beispielgruppe')
    await u.click(within(dialog).getByRole('button', { name: 'Trennen' }))
    await waitFor(() => expect(senden).toHaveBeenCalledWith('POST', '/api/verbindungen/v1/trennen'))
    expect(await screen.findByText('Noch keine Telegram-Gruppe verbunden.')).toBeTruthy()
  })

  it('fragt alle 5 s nach, solange der Code offen ist, und hört nach der Meldung auf', async () => {
    kuenstlicheZeit()
    zeige()
    await screen.findByText('Noch keine Telegram-Gruppe verbunden.')
    // Ohne Code wird nicht nachgefragt.
    await weiter(3 * ABFRAGE_MS)
    expect(abfragen()).toBe(1)
    await userEvent.click(screen.getByRole('button', { name: 'Code erzeugen' }))
    await screen.findByText('ABCD2345')
    await waitFor(() => expect(abfragen()).toBe(2))
    await weiter(ABFRAGE_MS)
    expect(abfragen()).toBe(3)
    await weiter(ABFRAGE_MS)
    expect(abfragen()).toBe(4)

    liste = [GEMELDET]
    await weiter(ABFRAGE_MS)
    expect(abfragen()).toBe(5)
    expect(await screen.findByText('Beispielgruppe')).toBeTruthy()
    expect(screen.queryByText('ABCD2345')).toBeNull()
    await weiter(5 * ABFRAGE_MS)
    expect(abfragen()).toBe(5)
  })

  it('hört nach dem Bestätigen auf (auch wenn noch ein Code offen war)', async () => {
    kuenstlicheZeit()
    const u = userEvent.setup()
    zeige()
    await screen.findByText('Noch keine Telegram-Gruppe verbunden.')
    await u.click(screen.getByRole('button', { name: 'Code erzeugen' }))
    await screen.findByText('ABCD2345')
    // Ein früherer Code wurde gemeldet, der neue ist noch offen: es wird weiter gefragt.
    liste = [OFFEN, verbindung({ ...GEMELDET, id: 'v0' })]
    await weiter(ABFRAGE_MS)
    const zeile = (await screen.findByText('Beispielgruppe')).closest('li') as HTMLElement
    const vorher = abfragen()
    await weiter(ABFRAGE_MS)
    expect(abfragen()).toBe(vorher + 1)

    await u.click(within(zeile).getByRole('button', { name: 'Bestätigen' }))
    const dialog = screen.getByRole('dialog')
    await u.type(within(dialog).getByLabelText('Code aus dem Telegram-Chat'), 'AB2CD3')
    await u.click(within(dialog).getByRole('button', { name: 'Verbinden' }))
    await waitFor(() =>
      expect(senden).toHaveBeenCalledWith('POST', '/api/verbindungen/v0/bestaetigen', { pruefcode: 'AB2CD3' }),
    )
    await screen.findByText(/Verbunden mit/)
    const danach = abfragen()
    await weiter(5 * ABFRAGE_MS)
    expect(abfragen()).toBe(danach)
    expect(screen.queryByText('ABCD2345')).toBeNull()
  })

  it('hört nach Ablauf des Codes auf und sagt es', async () => {
    kuenstlicheZeit()
    zeige()
    await screen.findByText('Noch keine Telegram-Gruppe verbunden.')
    await userEvent.click(screen.getByRole('button', { name: 'Code erzeugen' }))
    await screen.findByText('ABCD2345')
    await weiter(15 * 60 * 1000)
    expect(await screen.findByText(/Der Code ist abgelaufen/)).toBeTruthy()
    expect(screen.queryByText('ABCD2345')).toBeNull()
    const danach = abfragen()
    await weiter(5 * ABFRAGE_MS)
    expect(abfragen()).toBe(danach)
  })
})

function event(o: Partial<EventDetail> = {}): EventDetail {
  return {
    id: 'e1',
    firma_id: 'f1',
    kategorie: 'fest',
    name: 'Sommerfest',
    beginn: '2026-12-01T17:00:00+00:00',
    ende: '2026-12-01T22:00:00+00:00',
    ort_adresse: null,
    ort_link: null,
    beschreibung: null,
    status: 'geplant',
    quelle: 'neu',
    legacy_url: null,
    metadaten: {},
    angelegt: '2026-11-01T10:00:00+00:00',
    geaendert: '2026-11-01T10:00:00+00:00',
    schema: { kategorie: 'fest', name: 'Fest', bausteine: [], felder: [], begriffe: {} },
    ...o,
  }
}

describe('Event-Übersicht: Telegram-Gruppe', () => {
  function zeigeUebersicht(ich: Ich, e: EventDetail = event()) {
    render(
      <IchProvider ich={ich}>
        <UebersichtReiter event={e} onAktualisiert={() => undefined} />
      </IchProvider>,
    )
  }

  it('Leitung bestätigt eine gemeldete Gruppe, ein Mitglied sieht sie nur', async () => {
    liste = [GEMELDET]
    zeigeUebersicht(MITGLIED)
    expect(await screen.findByRole('heading', { name: 'Telegram-Gruppe' })).toBeTruthy()
    expect(api.holen).toHaveBeenCalledWith(PFAD)
    const mitglied = (await screen.findByText('Beispielgruppe')).closest('li') as HTMLElement
    expect(within(mitglied).queryByRole('button', { name: 'Bestätigen' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Code erzeugen' })).toBeNull()

    cleanup()
    zeigeUebersicht(LEITUNG)
    const leitung = (await screen.findByText('Beispielgruppe')).closest('li') as HTMLElement
    expect(within(leitung).getByRole('button', { name: 'Bestätigen' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Code erzeugen' })).toBeTruthy()
  })

  it('archiviertes Event: kein Code und kein Bestätigen', async () => {
    liste = [GEMELDET]
    zeigeUebersicht(LEITUNG, event({ status: 'archiviert' }))
    const zeile = (await screen.findByText('Beispielgruppe')).closest('li') as HTMLElement
    expect(within(zeile).queryByRole('button')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Code erzeugen' })).toBeNull()
  })

  it('Leitung trennt die verbundene Gruppe in der Event-Übersicht', async () => {
    liste = [BESTAETIGT]
    const u = userEvent.setup()
    zeigeUebersicht(LEITUNG)
    await u.click(await screen.findByRole('button', { name: 'Verbindung trennen' }))
    await u.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Trennen' }))
    await waitFor(() => expect(senden).toHaveBeenCalledWith('POST', '/api/verbindungen/v1/trennen'))
  })
})

describe('Meinen Telegram-Chat verbinden', () => {
  function zeigeKopf(ich: Ich) {
    render(
      <IchProvider ich={ich}>
        <Kopfleiste />
      </IchProvider>,
    )
  }

  it('Kopfleiste verlinkt nur für Leitung oder Betreiber', () => {
    zeigeKopf(MITGLIED)
    expect(screen.queryByRole('link', { name: 'Meinen Telegram-Chat verbinden' })).toBeNull()
    for (const ich of [LEITUNG, BETREIBER]) {
      cleanup()
      zeigeKopf(ich)
      const link = screen.getByRole('link', { name: 'Meinen Telegram-Chat verbinden' })
      expect(link.getAttribute('href')).toBe('/ich/telegram')
    }
  })

  it('Seite /ich/telegram nur für Leitung oder Betreiber', async () => {
    vi.spyOn(api, 'ich').mockResolvedValue(MITGLIED)
    render(<TelegramPage />)
    expect(await screen.findByText(/nur Leitungen und Betreiber/)).toBeTruthy()
    expect(api.holen).not.toHaveBeenCalled()

    cleanup()
    vi.spyOn(api, 'ich').mockResolvedValue(BETREIBER)
    render(<TelegramPage />)
    expect(await screen.findByRole('heading', { level: 1, name: 'Meinen Telegram-Chat verbinden' })).toBeTruthy()
    await waitFor(() => expect(api.holen).toHaveBeenCalledWith('/api/ich/verbinden'))
    expect(await screen.findByText('Noch kein Telegram-Chat verbunden.')).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Code erzeugen' }))
    expect(senden).toHaveBeenCalledWith('POST', '/api/ich/verbinden')
    const schritte = await screen.findAllByRole('listitem')
    expect(schritte[0]?.textContent).toContain('Direktnachricht')
  })

  it('gemeldeter Direktchat zeigt den Anzeigenamen aus Telegram (Bestätigen nicht blind)', async () => {
    liste = [verbindung({ ...GEMELDET, art: 'direkt', chat_titel: 'Erika (@erika_beispiel)' })]
    render(
      <IchProvider ich={LEITUNG}>
        <TelegramVerbinden art="direkt" titel="Mein Telegram-Chat" pfad="/api/ich/verbinden" darf />
      </IchProvider>,
    )
    const zeile = (await screen.findByText('Erika (@erika_beispiel)')).closest('li') as HTMLElement
    expect(zeile.textContent).toContain('Gemeldet: Erika (@erika_beispiel)')
    await userEvent.click(within(zeile).getByRole('button', { name: 'Bestätigen' }))
    expect(await screen.findByText('„Erika (@erika_beispiel)“ verbinden?')).toBeTruthy()
  })

  it('/ich/telegram: eigenen Chat trennen', async () => {
    vi.spyOn(api, 'ich').mockResolvedValue(LEITUNG)
    liste = [verbindung({ ...BESTAETIGT, art: 'direkt', chat_titel: 'Erika (@erika_beispiel)' })]
    const u = userEvent.setup()
    render(<TelegramPage />)
    expect(await screen.findByText('Erika (@erika_beispiel)')).toBeTruthy()
    await u.click(screen.getByRole('button', { name: 'Verbindung trennen' }))
    await u.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Trennen' }))
    await waitFor(() => expect(senden).toHaveBeenCalledWith('POST', '/api/verbindungen/v1/trennen'))
    expect(await screen.findByText('Noch kein Telegram-Chat verbunden.')).toBeTruthy()
  })
})
