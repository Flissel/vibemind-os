'use client'

import Link from 'next/link'
import { useEffect, useMemo, useState } from 'react'
import { ChevronDown, ChevronRight, Search } from 'lucide-react'
import { AnmeldeSchutz } from '@/components/AnmeldeSchutz'
import { useIch } from '@/components/IchKontext'
import { Meldung } from '@/components/Meldung'
import { EventKachel } from '@/components/katalog/EventKachel'
import { KatalogFilter, type KategorieZaehlung } from '@/components/katalog/KatalogFilter'
import { api } from '@/lib/api'
import { gruppiere } from '@/lib/phase'
import { darfAnlegen } from '@/lib/rechte'
import type { KatalogEvent } from '@/lib/typen'

const RASTER = 'grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4'

function Gruppe({
  titel,
  events,
  klappbar,
}: {
  titel: string
  events: KatalogEvent[]
  klappbar?: boolean
}) {
  const [offen, setOffen] = useState(!klappbar)
  if (events.length === 0) return null
  const Pfeil = offen ? ChevronDown : ChevronRight
  const anzahl = <span className="text-sm font-medium text-gray-500">{events.length}</span>
  return (
    <section className="space-y-3">
      {klappbar ? (
        <h2 className="text-lg font-bold text-gray-900">
          <button
            type="button"
            onClick={() => setOffen(!offen)}
            aria-expanded={offen}
            className="flex items-center gap-1"
          >
            <Pfeil className="h-5 w-5" aria-hidden />
            {titel} {anzahl}
          </button>
        </h2>
      ) : (
        <h2 className="text-lg font-bold text-gray-900">
          {titel} {anzahl}
        </h2>
      )}
      {offen && (
        <div className={RASTER}>
          {events.map((e) => (
            <EventKachel key={e.id} event={e} />
          ))}
        </div>
      )}
    </section>
  )
}

function Katalog() {
  const ich = useIch()
  const [eingabe, setEingabe] = useState('')
  const [suche, setSuche] = useState('')
  const [firma, setFirma] = useState('')
  const [kategorie, setKategorie] = useState('')
  const [events, setEvents] = useState<KatalogEvent[] | null>(null)
  const [fehler, setFehler] = useState<unknown>(null)

  useEffect(() => {
    const t = setTimeout(() => setSuche(eingabe.trim()), 300)
    return () => clearTimeout(t)
  }, [eingabe])

  useEffect(() => {
    let aktiv = true
    api
      .holen<KatalogEvent[]>('/api/events', {
        firma: firma || undefined,
        suche: suche || undefined,
      })
      .then((liste) => {
        if (!aktiv) return
        setFehler(null)
        setEvents(liste)
      })
      .catch((e: unknown) => {
        if (aktiv) setFehler(e)
      })
    return () => {
      aktiv = false
    }
  }, [firma, suche])

  // Gefiltert wird im Browser nach exakter Kategorie; so bleiben die Chip-Zahlen
  // die der aktuellen Trefferliste (Firma/Suche), auch wenn eine Kategorie gewählt ist.
  const zaehlung = useMemo<KategorieZaehlung[]>(() => {
    const karte = new Map<string, KategorieZaehlung>()
    for (const e of events ?? []) {
      const z = karte.get(e.kategorie)
      if (z) z.anzahl += 1
      else karte.set(e.kategorie, { id: e.kategorie, name: e.kategorie_name, anzahl: 1 })
    }
    return [...karte.values()].sort((a, b) => a.name.localeCompare(b.name, 'de'))
  }, [events])

  // Eine gewählte Kategorie, die nach Suche/Firmenwechsel nicht mehr vorkommt, wird verworfen.
  useEffect(() => {
    if (events && kategorie !== '' && !zaehlung.some((z) => z.id === kategorie)) setKategorie('')
  }, [events, zaehlung, kategorie])

  const sichtbar = useMemo(
    () => (events ?? []).filter((e) => kategorie === '' || e.kategorie === kategorie),
    [events, kategorie],
  )
  const gruppen = useMemo(() => gruppiere(sichtbar), [sichtbar])

  return (
    <div className="mx-auto max-w-screen-2xl space-y-6 px-4 py-6 sm:px-6 lg:px-8">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">Katalog</h1>
        {darfAnlegen(ich) && (
          <Link
            href="/events/neu"
            className="rounded-lg bg-gray-900 px-4 py-2 text-sm font-semibold text-white hover:bg-gray-700"
          >
            + Neues Event
          </Link>
        )}
      </div>
      <KatalogFilter
        suche={eingabe}
        onSuche={setEingabe}
        firmen={ich.firmen}
        firma={firma}
        onFirma={setFirma}
        kategorien={zaehlung}
        gesamt={events?.length ?? 0}
        kategorie={kategorie}
        onKategorie={setKategorie}
      />
      <Meldung fehler={fehler} />
      {events !== null && sichtbar.length === 0 && !fehler && (
        <div className="flex flex-col items-center gap-2 py-16 text-gray-500">
          <Search className="h-10 w-10 text-gray-300" aria-hidden />
          <p>Keine Events gefunden</p>
        </div>
      )}
      <Gruppe titel="Läuft gerade" events={gruppen.laeuft} />
      <Gruppe titel="Kommend" events={gruppen.kommend} />
      <Gruppe titel="Vergangen" events={gruppen.vergangen} klappbar />
    </div>
  )
}

export default function Startseite() {
  return (
    <AnmeldeSchutz>
      <Katalog />
    </AnmeldeSchutz>
  )
}
