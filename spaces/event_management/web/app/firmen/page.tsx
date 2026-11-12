'use client'

import clsx from 'clsx'
import Link from 'next/link'
import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { AnmeldeSchutz } from '@/components/AnmeldeSchutz'
import { useIch } from '@/components/IchKontext'
import { Meldung } from '@/components/Meldung'
import { BESCHRIFTUNG, EINGABE, KNOPF, KNOPF_ZURUECK } from '@/components/firma/stil'
import { BADGE } from '@/components/katalog/EventKachel'
import { api } from '@/lib/api'
import type { IchFirma } from '@/lib/typen'

const KUERZEL = /^[a-z0-9-]{2,32}$/

function rolleText(f: IchFirma): string {
  if (f.rolle === 'leitung') return 'Leitung'
  if (f.rolle === 'mitglied') return 'Mitglied'
  return 'Betreiber'
}

function Firmen() {
  const ich = useIch()
  const [liste, setListe] = useState<IchFirma[] | null>(null)
  const [fehler, setFehler] = useState<unknown>(null)
  const [offen, setOffen] = useState(false)
  const [name, setName] = useState('')
  const [kurz, setKurz] = useState('')
  const [hinweis, setHinweis] = useState('')

  const laden = useCallback(async () => {
    try {
      setListe(await api.holen<IchFirma[]>('/api/firmen'))
    } catch (e) {
      setFehler(e)
    }
  }, [])

  useEffect(() => {
    void laden()
  }, [laden])

  async function anlegen(e: FormEvent) {
    e.preventDefault()
    setFehler(null)
    if (!name.trim()) {
      setHinweis('Der Name fehlt.')
      return
    }
    if (!KUERZEL.test(kurz)) {
      setHinweis('Das Kürzel braucht 2 bis 32 Zeichen: nur a–z, 0–9 und Bindestrich.')
      return
    }
    setHinweis('')
    try {
      await api.senden('POST', '/api/firmen', { name: name.trim(), kurz })
      setName('')
      setKurz('')
      setOffen(false)
      await laden()
    } catch (err) {
      setFehler(err)
    }
  }

  return (
    <div className="mx-auto max-w-screen-2xl space-y-6 px-4 py-6 sm:px-6 lg:px-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold text-gray-900">Firmen</h1>
        {ich.betreiber && !offen && (
          <button type="button" onClick={() => setOffen(true)} className={KNOPF}>
            + Firma anlegen
          </button>
        )}
      </div>
      {ich.betreiber && offen && (
        <form
          onSubmit={(e) => void anlegen(e)}
          className="grid gap-3 rounded-xl bg-white p-4 nice-shadow sm:grid-cols-3 sm:items-end"
        >
          <div>
            <label htmlFor="firma-name" className={BESCHRIFTUNG}>
              Name
            </label>
            <input
              id="firma-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className={EINGABE}
            />
          </div>
          <div>
            <label htmlFor="firma-kurz" className={BESCHRIFTUNG}>
              Kürzel
            </label>
            <input
              id="firma-kurz"
              value={kurz}
              onChange={(e) => setKurz(e.target.value)}
              className={EINGABE}
            />
          </div>
          <div className="flex gap-2">
            <button type="submit" className={KNOPF}>
              Anlegen
            </button>
            <button type="button" onClick={() => setOffen(false)} className={KNOPF_ZURUECK}>
              Abbrechen
            </button>
          </div>
        </form>
      )}
      <Meldung text={hinweis || undefined} fehler={fehler ?? undefined} />
      {liste === null ? (
        !fehler && <p className="text-sm text-gray-500">Lädt …</p>
      ) : liste.length === 0 ? (
        <p className="text-sm text-gray-500">Keine Firmen.</p>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {liste.map((f) => (
            <Link
              key={f.id}
              href={`/firmen/${f.id}`}
              className="block space-y-2 rounded-xl bg-white p-4 nice-shadow transition-all duration-300 hover:scale-[1.01]"
            >
              <h2 className="line-clamp-1 text-base font-bold text-gray-900">{f.name}</h2>
              <div className="flex flex-wrap gap-1.5">
                <span className={clsx(BADGE, 'bg-gray-100 text-gray-700')}>{f.kurz}</span>
                <span className={clsx(BADGE, 'bg-blue-100 text-blue-700')}>{rolleText(f)}</span>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  )
}

export default function FirmenSeite() {
  return (
    <AnmeldeSchutz>
      <Firmen />
    </AnmeldeSchutz>
  )
}
