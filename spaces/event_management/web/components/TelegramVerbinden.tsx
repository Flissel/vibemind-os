'use client'

import { MessageCircle } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { Bestaetigen } from '@/components/Bestaetigen'
import { Meldung } from '@/components/Meldung'
import { api } from '@/lib/api'
import type { Verbindung, VerbindungArt, VerbindungsCode } from '@/lib/typen'
import { zeigeDatum, zeigeZeit } from '@/lib/zeit'

/** Solange ein Code offen ist, wird so oft nachgesehen, ob er gemeldet wurde. */
export const ABFRAGE_MS = 5000

const KNOPF =
  'rounded-lg bg-gray-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-40'
const KNOPF_RAND =
  'rounded-lg border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40'

const TEXTE: Record<
  VerbindungArt,
  { leer: string; verbunden: string; schritte: (code: string) => string[]; frage: string; erfolg: string }
> = {
  gruppe: {
    leer: 'Noch keine Telegram-Gruppe verbunden.',
    verbunden: 'Verbunden mit',
    schritte: (code) => [
      'Füge den Event-Bot zur Telegram-Gruppe hinzu.',
      `Schreib in der Gruppe an den Bot (mit @-Erwähnung): ${code}`,
      'Bestätige die gemeldete Gruppe danach hier.',
    ],
    frage: 'Diese Gruppe mit dem Event verbinden? Danach richtet sich der Bot in der Gruppe ein.',
    erfolg: 'Gruppe verbunden – der Bot wird eingerichtet.',
  },
  direkt: {
    leer: 'Noch kein Telegram-Chat verbunden.',
    verbunden: 'Verbunden:',
    schritte: (code) => [
      'Öffne den Event-Bot in Telegram (Direktnachricht).',
      `Schick ihm den Code: ${code}`,
      'Bestätige deinen Chat danach hier.',
    ],
    frage: 'Diesen Telegram-Chat verbinden? Danach antwortet dir dort dein persönlicher Bot.',
    erfolg: 'Chat verbunden – der Bot wird eingerichtet.',
  },
}

function name(v: Verbindung): string {
  return v.chat_titel ?? 'Telegram-Chat'
}

interface Props {
  art: VerbindungArt
  titel: string
  /** `/api/events/{id}/verbinden` oder `/api/ich/verbinden` */
  pfad: string
  /** Code erzeugen, bestätigen, verwerfen (Leitung bzw. die Person selbst); die API prüft selbst. */
  darf: boolean
  /** Z. B. archivierte Events: nur ansehen. */
  gesperrt?: boolean
}

/** Telegram-Gruppe (Event) oder eigenen Direktchat per Einmal-Code verbinden: Code erzeugen,
 *  vom Bot melden lassen, gemeldeten Chat bestätigen. */
export function TelegramVerbinden({ art, titel, pfad, darf, gesperrt = false }: Props) {
  const texte = TEXTE[art]
  const [liste, setListe] = useState<Verbindung[] | null>(null)
  const [code, setCode] = useState<VerbindungsCode | null>(null)
  const [fehler, setFehler] = useState<unknown>(null)
  const [erfolg, setErfolg] = useState('')
  const [hinweis, setHinweis] = useState('')
  const [laeuft, setLaeuft] = useState(false)
  const [frage, setFrage] = useState<Verbindung | null>(null)

  const laden = useCallback(async (): Promise<Verbindung[] | null> => {
    try {
      const neu = await api.holen<Verbindung[]>(pfad)
      setListe(neu)
      return neu
    } catch (e: unknown) {
      setFehler(e)
      return null
    }
  }, [pfad])

  useEffect(() => {
    void laden()
  }, [laden])

  // Abfrage alle 5 s, solange der angezeigte Code offen ist; sie endet, sobald er gemeldet
  // (kein offener Code mehr in der Liste) oder abgelaufen ist, und nach dem Bestätigen.
  useEffect(() => {
    if (!code) return
    const bis = Date.parse(code.gueltig_bis)
    const zeitgeber = setInterval(() => {
      if (Date.now() >= bis) {
        setCode(null)
        setHinweis('Der Code ist abgelaufen. Erzeuge bei Bedarf einen neuen.')
        void laden()
        return
      }
      void laden().then((neu) => {
        if (neu !== null && !neu.some((v) => v.status === 'offen')) setCode(null)
      })
    }, ABFRAGE_MS)
    return () => clearInterval(zeitgeber)
  }, [code, laden])

  async function ausfuehren(arbeit: () => Promise<void>) {
    setFehler(null)
    setErfolg('')
    setHinweis('')
    setLaeuft(true)
    try {
      await arbeit()
    } catch (e: unknown) {
      setFehler(e)
    } finally {
      setLaeuft(false)
    }
  }

  function erzeugen() {
    void ausfuehren(async () => {
      setCode(await api.senden<VerbindungsCode>('POST', pfad))
      await laden()
    })
  }

  function bestaetigen(v: Verbindung) {
    setFrage(null)
    void ausfuehren(async () => {
      await api.senden('POST', `/api/verbindungen/${v.id}/bestaetigen`)
      setCode(null)
      setErfolg(texte.erfolg)
      await laden()
    })
  }

  function verwerfen(v: Verbindung) {
    void ausfuehren(async () => {
      await api.senden('POST', `/api/verbindungen/${v.id}/verwerfen`)
      await laden()
    })
  }

  const bestaetigt = liste?.find((v) => v.status === 'bestaetigt')
  const gemeldet = liste?.filter((v) => v.status === 'gemeldet') ?? []
  const aktiv = darf && !gesperrt

  return (
    <section className="space-y-3">
      <h2 className="flex items-center gap-2 text-lg font-bold text-gray-900">
        <MessageCircle className="h-5 w-5" aria-hidden />
        {titel}
      </h2>
      {liste !== null &&
        (bestaetigt ? (
          <p className="text-sm text-gray-700">
            {texte.verbunden} <strong>{name(bestaetigt)}</strong>
            {bestaetigt.bestaetigt && ` (seit ${zeigeDatum(bestaetigt.bestaetigt)})`}
          </p>
        ) : (
          <p className="text-sm text-gray-600">{texte.leer}</p>
        ))}

      {gemeldet.length > 0 && (
        <ul className="space-y-2">
          {gemeldet.map((v) => (
            <li
              key={v.id}
              className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm"
            >
              <span>
                Gemeldet: <strong>{name(v)}</strong>
                {v.gemeldet && ` um ${zeigeZeit(v.gemeldet)}`}
              </span>
              {aktiv && (
                <span className="flex gap-2">
                  <button type="button" className={KNOPF} disabled={laeuft} onClick={() => setFrage(v)}>
                    Bestätigen
                  </button>
                  <button type="button" className={KNOPF_RAND} disabled={laeuft} onClick={() => verwerfen(v)}>
                    Verwerfen
                  </button>
                </span>
              )}
            </li>
          ))}
        </ul>
      )}

      {code && (
        <div className="space-y-2 rounded-lg border border-gray-200 bg-gray-50 p-4 text-sm text-gray-700">
          <p>
            Dein Code: <span className="font-mono text-2xl font-bold tracking-widest text-gray-900">{code.code}</span>
          </p>
          <p className="text-gray-500">Gültig bis {zeigeZeit(code.gueltig_bis)} Uhr.</p>
          <ol className="list-decimal space-y-1 pl-5">
            {texte.schritte(code.code).map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ol>
          <p className="text-gray-500" aria-live="polite">
            Wartet auf die Meldung des Bots …
          </p>
        </div>
      )}

      {aktiv && (
        <button type="button" className={KNOPF_RAND} disabled={laeuft} onClick={erzeugen}>
          {code ? 'Neuen Code erzeugen' : 'Code erzeugen'}
        </button>
      )}
      {hinweis && <p className="text-sm text-gray-600">{hinweis}</p>}
      <Meldung fehler={fehler} erfolg={erfolg} />

      {frage && (
        <Bestaetigen
          titel={`„${name(frage)}“ verbinden?`}
          text={texte.frage}
          aktion="Verbinden"
          laeuft={laeuft}
          onBestaetigen={() => bestaetigen(frage)}
          onAbbrechen={() => setFrage(null)}
        />
      )}
    </section>
  )
}
