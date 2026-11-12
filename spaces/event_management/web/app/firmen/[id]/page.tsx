'use client'

import { useParams } from 'next/navigation'
import { useEffect, useState } from 'react'
import { AnmeldeSchutz } from '@/components/AnmeldeSchutz'
import { useIch } from '@/components/IchKontext'
import { Meldung } from '@/components/Meldung'
import { EinladenFormular } from '@/components/firma/EinladenFormular'
import { KontakteListe } from '@/components/firma/KontakteListe'
import { MitgliederListe } from '@/components/firma/MitgliederListe'
import { api } from '@/lib/api'
import { darfVerwalten } from '@/lib/rechte'
import type { IchFirma } from '@/lib/typen'

function Firma() {
  const ich = useIch()
  const params = useParams<{ id: string }>()
  const id = Array.isArray(params.id) ? params.id[0]! : params.id
  const [firma, setFirma] = useState<IchFirma | null>(null)
  const [fehler, setFehler] = useState<unknown>(null)

  useEffect(() => {
    let aktiv = true
    api
      .holen<IchFirma[]>('/api/firmen')
      .then((liste) => {
        if (!aktiv) return
        const f = liste.find((x) => x.id === id)
        if (f) setFirma(f)
        else setFehler(true)
      })
      .catch((e: unknown) => {
        if (aktiv) setFehler(e)
      })
    return () => {
      aktiv = false
    }
  }, [id])

  const verwalten = darfVerwalten(ich, id)
  return (
    <div className="mx-auto max-w-screen-2xl space-y-6 px-4 py-6 sm:px-6 lg:px-8">
      {fehler === true ? (
        <Meldung text="Nicht gefunden" />
      ) : fehler ? (
        <Meldung fehler={fehler} />
      ) : (
        <h1 className="text-2xl font-bold text-gray-900">{firma ? firma.name : 'Lädt …'}</h1>
      )}
      {firma && (
        <>
          <MitgliederListe firmaId={id} verwalten={verwalten} />
          {verwalten && <EinladenFormular firmaId={id} />}
          <KontakteListe firmaId={id} verwalten={verwalten} />
        </>
      )}
    </div>
  )
}

export default function FirmaSeite() {
  return (
    <AnmeldeSchutz>
      <Firma />
    </AnmeldeSchutz>
  )
}
