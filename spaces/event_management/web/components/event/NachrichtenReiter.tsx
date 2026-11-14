'use client'

import clsx from 'clsx'
import { Mail, MessageCircle } from 'lucide-react'
import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Bestaetigen } from '@/components/Bestaetigen'
import { useIch } from '@/components/IchKontext'
import { Meldung } from '@/components/Meldung'
import { api } from '@/lib/api'
import { darfVerwalten } from '@/lib/rechte'
import type { Nachricht, NachrichtStatus, Zusage } from '@/lib/typen'
import { zeigeDatum, zeigeZeit } from '@/lib/zeit'

const EINGABE =
  'w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:border-gray-900 focus:outline-none'
const BESCHRIFTUNG = 'mb-1 block text-sm font-medium text-gray-700'
const KNOPF =
  'rounded-lg bg-gray-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-40'
const KNOPF_RAND =
  'rounded-lg border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40'

/** Grenzen wie in system/nachrichten.py (TEXT_MAX, BETREFF_MAX). */
const TEXT_MAX = 4000
const BETREFF_MAX = 200
const ANFANG = 80

const ZUSAGEN: { wert: Zusage; text: string }[] = [
  { wert: 'zugesagt', text: 'zugesagt' },
  { wert: 'vielleicht', text: 'vielleicht' },
  { wert: 'offen', text: 'offen' },
  { wert: 'abgesagt', text: 'abgesagt' },
]

const STATUS: Record<NachrichtStatus, { text: string; klasse: string }> = {
  entwurf: { text: 'Entwurf', klasse: 'bg-gray-100 text-gray-600' },
  freigegeben: { text: 'freigegeben', klasse: 'bg-blue-100 text-blue-700' },
  versendet: { text: 'versendet', klasse: 'bg-green-100 text-green-700' },
  fehler: { text: 'Fehler', klasse: 'bg-red-100 text-red-700' },
  verworfen: { text: 'verworfen', klasse: 'bg-yellow-100 text-yellow-700' },
}

function anfang(n: Nachricht): string {
  if (n.betreff) return n.betreff
  return n.text.length > ANFANG ? `${n.text.slice(0, ANFANG)} …` : n.text
}

function empfaengerText(anzahl: number): string {
  return anzahl === 1 ? '1 Empfänger' : `${anzahl} Empfänger`
}

type Formular = 'gruppe' | 'mail' | null

interface Props {
  eventId: string
  firmaId: string
  /** Archivierte Events: keine neuen Nachrichten und keine Freigabe (die API lehnt ab). */
  archiviert?: boolean
}

/** Reiter „Nachrichten": an die Telegram-Gruppe schreiben, Mails entwerfen und (nur
 *  Leitung) freigeben. Versendet werden Mails getrennt, nie aus der Oberfläche. */
export function NachrichtenReiter({ eventId, firmaId, archiviert = false }: Props) {
  const ich = useIch()
  const verwalten = darfVerwalten(ich, firmaId) && !archiviert
  const [liste, setListe] = useState<Nachricht[] | null>(null)
  const [ladeFehler, setLadeFehler] = useState<unknown>(null)
  const [formular, setFormular] = useState<Formular>(null)
  const [gruppeText, setGruppeText] = useState('')
  const [betreff, setBetreff] = useState('')
  const [mailText, setMailText] = useState('')
  const [zusage, setZusage] = useState<Zusage[]>(['zugesagt'])
  const [laeuft, setLaeuft] = useState(false)
  const [fehler, setFehler] = useState<unknown>(null)
  const [hinweis, setHinweis] = useState('')
  const [erfolg, setErfolg] = useState('')
  const [freigabe, setFreigabe] = useState<Nachricht | null>(null)

  const laden = useCallback(async () => {
    setListe(await api.holen<Nachricht[]>(`/api/events/${eventId}/nachrichten`))
  }, [eventId])

  useEffect(() => {
    let aktiv = true
    setLadeFehler(null)
    api
      .holen<Nachricht[]>(`/api/events/${eventId}/nachrichten`)
      .then((l) => {
        if (aktiv) setListe(l)
      })
      .catch((e: unknown) => {
        if (aktiv) setLadeFehler(e)
      })
    return () => {
      aktiv = false
    }
  }, [eventId])

  function zuruecksetzen() {
    setFehler(null)
    setHinweis('')
    setErfolg('')
  }

  /** Führt `aktion` aus und lädt danach neu; ein Ladefehler zählt nicht als Fehlschlag. */
  async function ausfuehren(aktion: () => Promise<string>): Promise<boolean> {
    zuruecksetzen()
    setLaeuft(true)
    try {
      setErfolg(await aktion())
    } catch (e: unknown) {
      setFehler(e)
      return false
    } finally {
      setLaeuft(false)
    }
    try {
      await laden()
    } catch (e: unknown) {
      setFehler(e)
    }
    return true
  }

  async function anGruppe(e: FormEvent) {
    e.preventDefault()
    const text = gruppeText.trim()
    if (!text) {
      setHinweis('Bitte einen Text eingeben.')
      return
    }
    let gesendet = false
    await ausfuehren(async () => {
      const n = await api.senden<Nachricht>('POST', `/api/events/${eventId}/nachrichten/gruppe`, { text })
      // Gespeichert ist sie auch bei einem Telegram-Fehler (Status „Fehler" in der Liste).
      if (n.status === 'fehler') {
        setHinweis('Senden an die Gruppe fehlgeschlagen')
        return ''
      }
      gesendet = true
      return 'An die Gruppe gesendet'
    })
    if (gesendet) {
      setGruppeText('')
      setFormular(null)
    }
  }

  async function mailEntwerfen(e: FormEvent) {
    e.preventDefault()
    if (!betreff.trim() || !mailText.trim() || zusage.length === 0) {
      setHinweis('Betreff, Text und mindestens eine Zusage werden gebraucht.')
      return
    }
    const ok = await ausfuehren(async () => {
      const n = await api.senden<Nachricht>('POST', `/api/events/${eventId}/nachrichten/mail`, {
        betreff: betreff.trim(),
        text: mailText.trim(),
        zusage,
      })
      return `Entwurf gespeichert (${empfaengerText(n.empfaenger ?? 0)}); versendet wird erst nach der Freigabe`
    })
    if (ok) {
      setBetreff('')
      setMailText('')
      setZusage(['zugesagt'])
      setFormular(null)
    }
  }

  async function freigeben(n: Nachricht) {
    setFreigabe(null)
    await ausfuehren(async () => {
      // Die gezeigte Zahl geht mit; hat sie sich inzwischen geändert, lehnt die API ab (422).
      await api.senden('POST', `/api/nachrichten/${n.id}/freigeben`, { erwartet: n.empfaenger ?? 0 })
      return 'Freigegeben – die Mail geht mit dem nächsten Versandlauf raus'
    })
  }

  async function verwerfen(n: Nachricht) {
    await ausfuehren(async () => {
      await api.senden('POST', `/api/nachrichten/${n.id}/verwerfen`)
      return 'Entwurf verworfen'
    })
  }

  function zusageUmschalten(wert: Zusage) {
    setZusage((alt) => (alt.includes(wert) ? alt.filter((z) => z !== wert) : [...alt, wert]))
  }

  if (ladeFehler) return <Meldung fehler={ladeFehler} />
  if (!liste) return <p className="text-sm text-gray-500">Lädt …</p>

  return (
    <div className="space-y-4">
      {archiviert ? (
        <p className="rounded-lg bg-gray-50 px-3 py-2 text-sm text-gray-600">
          Event ist archiviert – neue Nachrichten und Freigaben sind nicht mehr möglich.
        </p>
      ) : (
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => {
              zuruecksetzen()
              setFormular(formular === 'gruppe' ? null : 'gruppe')
            }}
            aria-expanded={formular === 'gruppe'}
            className={formular === 'gruppe' ? KNOPF_RAND : KNOPF}
          >
            An Gruppe schreiben
          </button>
          <button
            type="button"
            onClick={() => {
              zuruecksetzen()
              setFormular(formular === 'mail' ? null : 'mail')
            }}
            aria-expanded={formular === 'mail'}
            className={formular === 'mail' ? KNOPF_RAND : KNOPF}
          >
            Mail entwerfen
          </button>
        </div>
      )}

      {!archiviert && formular === 'gruppe' && (
        <form
          onSubmit={(e) => void anGruppe(e)}
          aria-label="An Gruppe schreiben"
          className="space-y-3 rounded-xl bg-white p-4 nice-shadow"
        >
          <p className="text-sm text-gray-600">Geht sofort an die verbundene Telegram-Gruppe.</p>
          <div>
            <label htmlFor="nachricht-gruppe-text" className={BESCHRIFTUNG}>
              Text
            </label>
            <textarea
              id="nachricht-gruppe-text"
              value={gruppeText}
              maxLength={TEXT_MAX}
              rows={4}
              onChange={(e) => setGruppeText(e.target.value)}
              className={EINGABE}
            />
          </div>
          <div className="flex gap-2">
            <button type="submit" disabled={laeuft} className={KNOPF}>
              Senden
            </button>
            <button type="button" onClick={() => setFormular(null)} className={KNOPF_RAND}>
              Abbrechen
            </button>
          </div>
        </form>
      )}

      {!archiviert && formular === 'mail' && (
        <form
          onSubmit={(e) => void mailEntwerfen(e)}
          aria-label="Mail entwerfen"
          className="space-y-3 rounded-xl bg-white p-4 nice-shadow"
        >
          <p className="text-sm text-gray-600">
            Wird als Entwurf gespeichert. Versendet wird erst, wenn eine Leitung freigibt.
          </p>
          <div>
            <label htmlFor="nachricht-mail-betreff" className={BESCHRIFTUNG}>
              Betreff
            </label>
            <input
              id="nachricht-mail-betreff"
              value={betreff}
              maxLength={BETREFF_MAX}
              onChange={(e) => setBetreff(e.target.value)}
              className={EINGABE}
            />
          </div>
          <div>
            <label htmlFor="nachricht-mail-text" className={BESCHRIFTUNG}>
              Text
            </label>
            <textarea
              id="nachricht-mail-text"
              value={mailText}
              maxLength={TEXT_MAX}
              rows={6}
              onChange={(e) => setMailText(e.target.value)}
              className={EINGABE}
            />
          </div>
          <fieldset>
            <legend className={BESCHRIFTUNG}>An Teilnehmende mit Zusage</legend>
            <div className="flex flex-wrap gap-4">
              {ZUSAGEN.map((z) => (
                <label key={z.wert} className="flex items-center gap-2 text-sm text-gray-700">
                  <input
                    type="checkbox"
                    checked={zusage.includes(z.wert)}
                    onChange={() => zusageUmschalten(z.wert)}
                  />
                  {z.text}
                </label>
              ))}
            </div>
          </fieldset>
          <div className="flex gap-2">
            <button type="submit" disabled={laeuft} className={KNOPF}>
              Entwurf speichern
            </button>
            <button type="button" onClick={() => setFormular(null)} className={KNOPF_RAND}>
              Abbrechen
            </button>
          </div>
        </form>
      )}

      <Meldung text={hinweis || undefined} fehler={fehler ?? undefined} erfolg={erfolg} />

      {liste.length === 0 ? (
        <p className="text-sm text-gray-500">Noch keine Nachrichten</p>
      ) : (
        <div className="overflow-x-auto rounded-xl bg-white nice-shadow">
          <table className="w-full text-left text-sm">
            <thead className="bg-gray-50 text-xs uppercase tracking-wide text-gray-500">
              <tr>
                <th scope="col" className="px-4 py-2 font-semibold">
                  Kanal
                </th>
                <th scope="col" className="px-4 py-2 font-semibold">
                  Betreff / Anfang
                </th>
                <th scope="col" className="px-4 py-2 font-semibold">
                  Status
                </th>
                <th scope="col" className="px-4 py-2 font-semibold">
                  Empfänger
                </th>
                <th scope="col" className="px-4 py-2 font-semibold">
                  Angelegt
                </th>
                <th scope="col" className="px-4 py-2" />
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {liste.map((n) => {
                const Symbol = n.kanal === 'gruppe' ? MessageCircle : Mail
                const status = STATUS[n.status]
                return (
                  <tr key={n.id}>
                    <td className="whitespace-nowrap px-4 py-2 text-gray-700">
                      <span className="inline-flex items-center gap-1.5">
                        <Symbol className="h-4 w-4 text-gray-400" aria-hidden />
                        {n.kanal === 'gruppe' ? 'Gruppe' : 'Mail'}
                      </span>
                    </td>
                    <td className="px-4 py-2 text-gray-900">{anfang(n)}</td>
                    <td className="px-4 py-2">
                      <span
                        className={clsx('rounded-full px-2 py-0.5 text-xs font-medium', status.klasse)}
                        title={n.fehler ?? undefined}
                      >
                        {status.text}
                      </span>
                    </td>
                    <td className="px-4 py-2 text-gray-700">
                      {n.empfaenger === null ? '–' : n.empfaenger}
                    </td>
                    <td className="whitespace-nowrap px-4 py-2 text-gray-500">
                      {zeigeDatum(n.angelegt)} {zeigeZeit(n.angelegt)}
                    </td>
                    <td className="whitespace-nowrap px-4 py-2 text-right">
                      {n.kanal === 'mail' && n.status === 'entwurf' && (
                        <span className="inline-flex gap-2">
                          {verwalten && (
                            <button
                              type="button"
                              disabled={laeuft || !n.empfaenger}
                              title={n.empfaenger ? undefined : 'Keine Empfänger mit E-Mail-Adresse'}
                              onClick={() => setFreigabe(n)}
                              aria-label={`Freigeben: ${anfang(n)}`}
                              className={KNOPF}
                            >
                              Freigeben
                            </button>
                          )}
                          <button
                            type="button"
                            disabled={laeuft}
                            onClick={() => void verwerfen(n)}
                            aria-label={`Verwerfen: ${anfang(n)}`}
                            className={KNOPF_RAND}
                          >
                            Verwerfen
                          </button>
                        </span>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {freigabe && (
        <Bestaetigen
          titel="Mail freigeben"
          text={`„${anfang(freigabe)}" geht an ${empfaengerText(freigabe.empfaenger ?? 0)}. Versendet wird mit dem nächsten Versandlauf; danach lässt sich nichts mehr zurückholen.`}
          aktion="Freigeben"
          laeuft={laeuft}
          onBestaetigen={() => void freigeben(freigabe)}
          onAbbrechen={() => setFreigabe(null)}
        />
      )}
    </div>
  )
}
