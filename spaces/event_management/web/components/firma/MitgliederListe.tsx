'use client'

import { useCallback, useEffect, useState } from 'react'
import { Bestaetigen } from '@/components/Bestaetigen'
import { Meldung } from '@/components/Meldung'
import { api } from '@/lib/api'
import type { Mitglied, Rolle } from '@/lib/typen'
import { ABSCHNITT } from './stil'

export const ROLLENNAME: Record<Rolle, string> = { leitung: 'Leitung', mitglied: 'Mitglied' }

interface Props {
  firmaId: string
  verwalten: boolean
}

export function MitgliederListe({ firmaId, verwalten }: Props) {
  const [liste, setListe] = useState<Mitglied[] | null>(null)
  const [fehler, setFehler] = useState<unknown>(null)
  const [entfernen, setEntfernen] = useState<Mitglied | null>(null)
  const [laeuft, setLaeuft] = useState(false)

  const laden = useCallback(async () => {
    try {
      setListe(await api.holen<Mitglied[]>(`/api/firmen/${firmaId}/mitglieder`))
    } catch (e) {
      setFehler(e)
    }
  }, [firmaId])

  useEffect(() => {
    void laden()
  }, [laden])

  async function rolleAendern(m: Mitglied, rolle: Rolle) {
    setFehler(null)
    try {
      await api.senden('PATCH', `/api/firmen/${firmaId}/mitglieder/${m.person_id}`, { rolle })
    } catch (e) {
      setFehler(e)
    }
    await laden()
  }

  async function entfernt() {
    if (!entfernen) return
    setLaeuft(true)
    setFehler(null)
    try {
      await api.senden('DELETE', `/api/firmen/${firmaId}/mitglieder/${entfernen.person_id}`)
    } catch (e) {
      setFehler(e)
    }
    setLaeuft(false)
    setEntfernen(null)
    await laden()
  }

  return (
    <section className={ABSCHNITT} aria-labelledby="mitglieder-titel">
      <h2 id="mitglieder-titel" className="mb-3 text-lg font-bold text-gray-900">
        Mitglieder
      </h2>
      <Meldung fehler={fehler ?? undefined} className="mb-3" />
      {liste === null ? (
        <p className="text-sm text-gray-500">Lädt …</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-xs uppercase text-gray-500">
              <tr>
                <th className="py-2 pr-4">Name</th>
                <th className="py-2 pr-4">Mail</th>
                <th className="py-2 pr-4">Rolle</th>
                {verwalten && <th className="py-2" />}
              </tr>
            </thead>
            <tbody>
              {liste.map((m) => (
                <tr key={m.person_id} className="border-t border-gray-100">
                  <td className="py-2 pr-4 font-medium text-gray-900">{m.name}</td>
                  <td className="py-2 pr-4 text-gray-700">{m.mail}</td>
                  <td className="py-2 pr-4">
                    {verwalten ? (
                      <select
                        aria-label={`Rolle ändern: ${m.name}`}
                        value={m.rolle}
                        onChange={(e) => void rolleAendern(m, e.target.value as Rolle)}
                        className="rounded-lg border border-gray-300 bg-white px-2 py-1 text-sm"
                      >
                        <option value="leitung">{ROLLENNAME.leitung}</option>
                        <option value="mitglied">{ROLLENNAME.mitglied}</option>
                      </select>
                    ) : (
                      ROLLENNAME[m.rolle]
                    )}
                  </td>
                  {verwalten && (
                    <td className="py-2 text-right">
                      <button
                        type="button"
                        aria-label={`${m.name} entfernen`}
                        onClick={() => setEntfernen(m)}
                        className="text-sm font-medium text-red-600 hover:underline"
                      >
                        Entfernen
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {entfernen && (
        <Bestaetigen
          titel="Mitglied entfernen"
          text={`${entfernen.name} aus der Firma entfernen?`}
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
