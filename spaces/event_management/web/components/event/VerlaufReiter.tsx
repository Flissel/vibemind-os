'use client'

import { useEffect, useState } from 'react'
import { Meldung } from '@/components/Meldung'
import { api } from '@/lib/api'
import { eintragName } from '@/lib/begriffe'
import type { VerlaufEintrag } from '@/lib/typen'
import { zeigeDatum, zeigeZeit } from '@/lib/zeit'

interface Props {
  eventId: string
  begriffe: Readonly<Record<string, string>>
}

export function VerlaufReiter({ eventId, begriffe }: Props) {
  const [eintraege, setEintraege] = useState<VerlaufEintrag[] | null>(null)
  const [fehler, setFehler] = useState<unknown>(null)

  useEffect(() => {
    let aktiv = true
    api
      .holen<VerlaufEintrag[]>(`/api/events/${eventId}/verlauf`, { limit: '100' })
      .then((liste) => {
        if (aktiv) setEintraege(liste)
      })
      .catch((e: unknown) => {
        if (aktiv) setFehler(e)
      })
    return () => {
      aktiv = false
    }
  }, [eventId])

  if (fehler) return <Meldung fehler={fehler} />
  if (!eintraege) return <p className="text-sm text-gray-500">Lädt …</p>
  if (eintraege.length === 0) return <p className="text-sm text-gray-500">Noch keine Einträge.</p>
  return (
    <div className="overflow-x-auto rounded-xl bg-white nice-shadow">
      <table className="w-full text-left text-sm">
        <thead className="border-b border-gray-100 text-xs uppercase tracking-wide text-gray-500">
          <tr>
            <th className="px-4 py-2">Zeit</th>
            <th className="px-4 py-2">Actor</th>
            <th className="px-4 py-2">Baustein</th>
            <th className="px-4 py-2">Aktion</th>
            <th className="px-4 py-2">Quelle</th>
          </tr>
        </thead>
        <tbody>
          {eintraege.map((v) => (
            <tr key={v.id} className="border-b border-gray-50 last:border-0">
              <td className="whitespace-nowrap px-4 py-2 text-gray-700">
                {zeigeDatum(v.zeitpunkt)} {zeigeZeit(v.zeitpunkt)}
              </td>
              <td className="px-4 py-2 text-gray-900">{v.actor}</td>
              <td className="px-4 py-2 text-gray-700">{v.baustein === 'event' ? 'Event' : eintragName(v.baustein, begriffe)}</td>
              <td className="px-4 py-2 text-gray-700">{v.aktion}</td>
              <td className="px-4 py-2 text-gray-500">{v.quelle}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
