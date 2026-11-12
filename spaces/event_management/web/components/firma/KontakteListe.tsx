'use client'

import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Bestaetigen } from '@/components/Bestaetigen'
import { Meldung } from '@/components/Meldung'
import { api } from '@/lib/api'
import type { Kontakt } from '@/lib/typen'
import { ABSCHNITT, BESCHRIFTUNG, EINGABE, KNOPF, KNOPF_ZURUECK } from './stil'

interface Props {
  firmaId: string
  verwalten: boolean
}

export function KontakteListe({ firmaId, verwalten }: Props) {
  const [liste, setListe] = useState<Kontakt[] | null>(null)
  const [suche, setSuche] = useState('')
  const [fehler, setFehler] = useState<unknown>(null)
  const [hinweis, setHinweis] = useState('')
  const [bearbeiten, setBearbeiten] = useState<Kontakt | null>(null)
  const [eName, setEName] = useState('')
  const [eMail, setEMail] = useState('')
  const [eTelegram, setETelegram] = useState('')
  const [entfernen, setEntfernen] = useState<Kontakt | null>(null)
  const [laeuft, setLaeuft] = useState(false)
  const [nName, setNName] = useState('')
  const [nMail, setNMail] = useState('')
  const [nTelegram, setNTelegram] = useState('')

  const laden = useCallback(async () => {
    try {
      const s = suche.trim()
      setListe(
        await api.holen<Kontakt[]>(`/api/firmen/${firmaId}/kontakte`, s ? { suche: s } : undefined),
      )
    } catch (e) {
      setFehler(e)
    }
  }, [firmaId, suche])

  useEffect(() => {
    void laden()
  }, [laden])

  function bearbeitenOeffnen(k: Kontakt) {
    setBearbeiten(k)
    setEName(k.name)
    setEMail(k.mail ?? '')
    setETelegram(k.telegram ?? '')
    setHinweis('')
    setFehler(null)
  }

  async function anlegen(e: FormEvent) {
    e.preventDefault()
    setFehler(null)
    const name = nName.trim()
    if (!name) {
      setHinweis('Name fehlt')
      return
    }
    setHinweis('')
    const mail = nMail.trim()
    const telegram = nTelegram.trim()
    try {
      await api.senden('POST', `/api/firmen/${firmaId}/kontakte`, {
        name,
        ...(mail ? { mail } : {}),
        ...(telegram ? { telegram } : {}),
      })
      setNName('')
      setNMail('')
      setNTelegram('')
      await laden()
    } catch (err) {
      setFehler(err)
    }
  }

  async function speichern(e: FormEvent) {
    e.preventDefault()
    if (!bearbeiten) return
    setFehler(null)
    const name = eName.trim()
    if (!name) {
      setHinweis('Name fehlt')
      return
    }
    setHinweis('')
    const aenderung: { name?: string; mail?: string | null; telegram?: string | null } = {}
    if (name !== bearbeiten.name) aenderung.name = name
    if (eMail.trim() !== (bearbeiten.mail ?? '')) aenderung.mail = eMail.trim() || null
    if (eTelegram.trim() !== (bearbeiten.telegram ?? '')) aenderung.telegram = eTelegram.trim() || null
    if (Object.keys(aenderung).length > 0) {
      try {
        await api.senden('PATCH', `/api/firmen/${firmaId}/kontakte/${bearbeiten.id}`, aenderung)
      } catch (err) {
        setFehler(err)
        return
      }
    }
    setBearbeiten(null)
    await laden()
  }

  async function entfernt() {
    if (!entfernen) return
    setLaeuft(true)
    setFehler(null)
    try {
      await api.senden('DELETE', `/api/firmen/${firmaId}/kontakte/${entfernen.id}`)
    } catch (err) {
      setFehler(err)
    }
    setLaeuft(false)
    setEntfernen(null)
    await laden()
  }

  return (
    <section className={ABSCHNITT} aria-labelledby="kontakte-titel">
      <h2 id="kontakte-titel" className="mb-3 text-lg font-bold text-gray-900">
        Kontakte
      </h2>
      <Meldung text={hinweis || undefined} fehler={fehler ?? undefined} className="mb-3" />
      <label htmlFor="kontakte-suche" className={BESCHRIFTUNG}>
        Suche
      </label>
      <input
        id="kontakte-suche"
        type="search"
        value={suche}
        onChange={(e) => setSuche(e.target.value)}
        className={`${EINGABE} mb-3 sm:max-w-xs`}
      />
      {liste === null ? (
        <p className="text-sm text-gray-500">Lädt …</p>
      ) : liste.length === 0 ? (
        <p className="text-sm text-gray-500">Keine Kontakte.</p>
      ) : (
        <ul className="divide-y divide-gray-100">
          {liste.map((k) =>
            bearbeiten?.id === k.id ? (
              <li key={k.id} className="py-3">
                <form
                  onSubmit={(e) => void speichern(e)}
                  className="grid gap-3 sm:grid-cols-4 sm:items-end"
                >
                  <div>
                    <label htmlFor="k-name" className={BESCHRIFTUNG}>
                      Name
                    </label>
                    <input
                      id="k-name"
                      value={eName}
                      onChange={(e) => setEName(e.target.value)}
                      className={EINGABE}
                    />
                  </div>
                  <div>
                    <label htmlFor="k-mail" className={BESCHRIFTUNG}>
                      Mail
                    </label>
                    <input
                      id="k-mail"
                      value={eMail}
                      onChange={(e) => setEMail(e.target.value)}
                      className={EINGABE}
                    />
                  </div>
                  <div>
                    <label htmlFor="k-telegram" className={BESCHRIFTUNG}>
                      Telegram
                    </label>
                    <input
                      id="k-telegram"
                      value={eTelegram}
                      onChange={(e) => setETelegram(e.target.value)}
                      className={EINGABE}
                    />
                  </div>
                  <div className="flex gap-2">
                    <button type="submit" className={KNOPF}>
                      Speichern
                    </button>
                    <button type="button" onClick={() => setBearbeiten(null)} className={KNOPF_ZURUECK}>
                      Abbrechen
                    </button>
                  </div>
                </form>
              </li>
            ) : (
              <li key={k.id} className="flex flex-wrap items-center justify-between gap-2 py-3 text-sm">
                <div>
                  <div className="font-medium text-gray-900">{k.name}</div>
                  <div className="text-gray-600">{[k.mail, k.telegram].filter(Boolean).join(' · ')}</div>
                </div>
                <div className="flex gap-3">
                  <button
                    type="button"
                    aria-label={`${k.name} bearbeiten`}
                    onClick={() => bearbeitenOeffnen(k)}
                    className="font-medium text-gray-700 hover:underline"
                  >
                    Bearbeiten
                  </button>
                  {verwalten && (
                    <button
                      type="button"
                      aria-label={`${k.name} entfernen`}
                      onClick={() => setEntfernen(k)}
                      className="font-medium text-red-600 hover:underline"
                    >
                      Entfernen
                    </button>
                  )}
                </div>
              </li>
            ),
          )}
        </ul>
      )}
      <form
        onSubmit={(e) => void anlegen(e)}
        className="mt-4 grid gap-3 border-t border-gray-100 pt-4 sm:grid-cols-4 sm:items-end"
      >
        <div>
          <label htmlFor="n-name" className={BESCHRIFTUNG}>
            Neuer Kontakt: Name
          </label>
          <input
            id="n-name"
            value={nName}
            onChange={(e) => setNName(e.target.value)}
            className={EINGABE}
          />
        </div>
        <div>
          <label htmlFor="n-mail" className={BESCHRIFTUNG}>
            Neuer Kontakt: Mail
          </label>
          <input
            id="n-mail"
            value={nMail}
            onChange={(e) => setNMail(e.target.value)}
            className={EINGABE}
          />
        </div>
        <div>
          <label htmlFor="n-telegram" className={BESCHRIFTUNG}>
            Neuer Kontakt: Telegram
          </label>
          <input
            id="n-telegram"
            value={nTelegram}
            onChange={(e) => setNTelegram(e.target.value)}
            className={EINGABE}
          />
        </div>
        <button type="submit" className={KNOPF}>
          Kontakt anlegen
        </button>
      </form>
      {entfernen && (
        <Bestaetigen
          titel="Kontakt entfernen"
          text={`${entfernen.name} entfernen?`}
          aktion="Entfernen"
          gefaehrlich
          laeuft={laeuft}
          onBestaetigen={() => void entfernt()}
          onAbbrechen={() => setEntfernen(null)}
        />
      )}
    </section>
  )
}
