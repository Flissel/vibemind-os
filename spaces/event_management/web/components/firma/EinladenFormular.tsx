'use client'

import { useState, type FormEvent } from 'react'
import { Meldung } from '@/components/Meldung'
import { api } from '@/lib/api'
import type { Rolle } from '@/lib/typen'
import { ROLLENNAME } from './MitgliederListe'
import { ABSCHNITT, BESCHRIFTUNG, EINGABE, KNOPF } from './stil'

export function EinladenFormular({ firmaId }: { firmaId: string }) {
  const [mail, setMail] = useState('')
  const [name, setName] = useState('')
  const [rolle, setRolle] = useState<Rolle>('mitglied')
  const [fehler, setFehler] = useState<unknown>(null)
  const [hinweis, setHinweis] = useState('')
  const [erfolg, setErfolg] = useState('')
  const [laeuft, setLaeuft] = useState(false)

  async function senden(e: FormEvent) {
    e.preventDefault()
    setFehler(null)
    setErfolg('')
    if (!mail.trim() || !name.trim()) {
      setHinweis('Mail und Name werden gebraucht.')
      return
    }
    setHinweis('')
    setLaeuft(true)
    try {
      await api.senden('POST', `/api/firmen/${firmaId}/einladungen`, {
        mail: mail.trim(),
        name: name.trim(),
        rolle,
      })
      setErfolg('Einladung verschickt')
      setMail('')
      setName('')
    } catch (err) {
      setFehler(err)
    } finally {
      setLaeuft(false)
    }
  }

  return (
    <section className={ABSCHNITT} aria-labelledby="einladen-titel">
      <h2 id="einladen-titel" className="mb-3 text-lg font-bold text-gray-900">
        Einladen
      </h2>
      <form onSubmit={(e) => void senden(e)} className="grid gap-3 sm:grid-cols-4 sm:items-end">
        <div>
          <label htmlFor="einl-mail" className={BESCHRIFTUNG}>
            Mail
          </label>
          <input
            id="einl-mail"
            type="email"
            value={mail}
            onChange={(e) => setMail(e.target.value)}
            className={EINGABE}
          />
        </div>
        <div>
          <label htmlFor="einl-name" className={BESCHRIFTUNG}>
            Name
          </label>
          <input
            id="einl-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            className={EINGABE}
          />
        </div>
        <div>
          <label htmlFor="einl-rolle" className={BESCHRIFTUNG}>
            Rolle
          </label>
          <select
            id="einl-rolle"
            value={rolle}
            onChange={(e) => setRolle(e.target.value as Rolle)}
            className={EINGABE}
          >
            <option value="mitglied">{ROLLENNAME.mitglied}</option>
            <option value="leitung">{ROLLENNAME.leitung}</option>
          </select>
        </div>
        <button type="submit" disabled={laeuft} className={KNOPF}>
          Einladen
        </button>
      </form>
      <Meldung
        text={hinweis || undefined}
        fehler={fehler ?? undefined}
        erfolg={erfolg}
        className="mt-3"
      />
    </section>
  )
}
