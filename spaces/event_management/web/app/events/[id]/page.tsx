'use client'

import clsx from 'clsx'
import Link from 'next/link'
import { useParams, useSearchParams } from 'next/navigation'
import { Suspense, useEffect, useState } from 'react'
import { AnmeldeSchutz } from '@/components/AnmeldeSchutz'
import { useIch } from '@/components/IchKontext'
import { Meldung } from '@/components/Meldung'
import { BausteinSlot } from '@/components/event/BausteinSlot'
import { EventKopf } from '@/components/event/EventKopf'
import { UebersichtReiter } from '@/components/event/UebersichtReiter'
import { VerlaufReiter } from '@/components/event/VerlaufReiter'
import { api } from '@/lib/api'
import { bausteinName } from '@/lib/begriffe'
import type { BausteineKatalog, EventDetail, Firma } from '@/lib/typen'

interface ReiterInfo {
  /** Wert von `?reiter=`; `null` für die Übersicht (kein Parameter). */
  schluessel: string | null
  name: string
}

function reiterListe(e: EventDetail): ReiterInfo[] {
  return [
    { schluessel: null, name: 'Übersicht' },
    ...e.schema.bausteine
      .filter((b) => b !== 'verlauf')
      .map((b) => ({ schluessel: b, name: bausteinName(b, e.schema.begriffe) })),
    { schluessel: 'verlauf', name: 'Verlauf' },
  ]
}

function EventSeite({ id }: { id: string }) {
  const ich = useIch()
  const gewaehlt = useSearchParams().get('reiter')
  const [event, setEvent] = useState<EventDetail | null>(null)
  const [fehler, setFehler] = useState<unknown>(null)
  const [firmaNachgeladen, setFirmaNachgeladen] = useState<Firma[]>([])
  const [verzeichnis, setVerzeichnis] = useState<BausteineKatalog | null>(null)
  const [verzeichnisFehler, setVerzeichnisFehler] = useState<unknown>(null)

  useEffect(() => {
    let aktiv = true
    api
      .holen<EventDetail>(`/api/events/${id}`)
      .then((e) => {
        if (aktiv) setEvent(e)
      })
      .catch((e: unknown) => {
        if (aktiv) setFehler(e)
      })
    return () => {
      aktiv = false
    }
  }, [id])

  // Das Verzeichnis der Baustein-Spalten gilt für alle Reiter und wird einmal pro Seite geladen.
  useEffect(() => {
    let aktiv = true
    api
      .holen<BausteineKatalog>('/api/bausteine')
      .then((v) => {
        if (aktiv) setVerzeichnis(v)
      })
      .catch((e: unknown) => {
        if (aktiv) setVerzeichnisFehler(e)
      })
    return () => {
      aktiv = false
    }
  }, [])

  const firmaId = event?.firma_id
  const bekannt = firmaId ? ich.firmen.find((f) => f.id === firmaId) : undefined
  useEffect(() => {
    if (!firmaId || bekannt || !ich.betreiber) return
    let aktiv = true
    api
      .holen<Firma[]>('/api/firmen')
      .then((liste) => {
        if (aktiv) setFirmaNachgeladen(liste)
      })
      .catch(() => {
        // Nur der Firmenname in der Brotkrume fehlt dann.
      })
    return () => {
      aktiv = false
    }
  }, [firmaId, bekannt, ich.betreiber])

  if (fehler) return <Meldung fehler={fehler} className="m-6" />
  if (!event) return <p className="p-6 text-sm text-gray-500">Lädt …</p>

  const firmaName = bekannt?.name ?? firmaNachgeladen.find((f) => f.id === event.firma_id)?.name ?? 'Firma'
  const reiter = reiterListe(event)
  const aktiv = reiter.find((r) => r.schluessel === gewaehlt) ?? reiter[0]

  return (
    <div className="mx-auto max-w-screen-2xl space-y-6 px-4 py-6 sm:px-6 lg:px-8">
      <EventKopf event={event} firmaName={firmaName} />
      {event.quelle !== 'legacy' && aktiv && (
        <>
          <nav aria-label="Bereiche" className="flex flex-wrap gap-1 border-b border-gray-200">
            {reiter.map((r) => (
              <Link
                key={r.schluessel ?? 'uebersicht'}
                href={r.schluessel ? `/events/${id}?reiter=${r.schluessel}` : `/events/${id}`}
                aria-current={r === aktiv ? 'page' : undefined}
                className={clsx(
                  '-mb-px border-b-2 px-3 py-2 text-sm font-medium',
                  r === aktiv
                    ? 'border-gray-900 text-gray-900'
                    : 'border-transparent text-gray-500 hover:text-gray-800',
                )}
              >
                {r.name}
              </Link>
            ))}
          </nav>
          <section>
            {aktiv.schluessel === null && <UebersichtReiter event={event} onAktualisiert={setEvent} />}
            {aktiv.schluessel === 'verlauf' && (
              <VerlaufReiter eventId={event.id} begriffe={event.schema.begriffe} />
            )}
            {aktiv.schluessel !== null && aktiv.schluessel !== 'verlauf' && (
              <BausteinSlot
                event={event}
                baustein={aktiv.schluessel}
                name={aktiv.name}
                verzeichnis={verzeichnis}
                fehler={verzeichnisFehler}
              />
            )}
          </section>
        </>
      )}
    </div>
  )
}

function Seite() {
  const params = useParams<{ id: string }>()
  const id = Array.isArray(params.id) ? params.id[0] : params.id
  if (!id) return <Meldung text="Nicht gefunden" className="m-6" />
  return <EventSeite id={id} />
}

export default function EventPage() {
  return (
    <AnmeldeSchutz>
      <Suspense fallback={<p className="p-6 text-sm text-gray-500">Lädt …</p>}>
        <Seite />
      </Suspense>
    </AnmeldeSchutz>
  )
}
