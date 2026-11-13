'use client'

import { useEffect, useState, type ReactNode } from 'react'
import { IchProvider } from '@/components/IchKontext'
import { Kopfleiste } from '@/components/Kopfleiste'
import { Meldung } from '@/components/Meldung'
import { api, zurAnmeldung } from '@/lib/api'
import type { Ich } from '@/lib/typen'

/** Lädt `GET /api/ich`; ohne Anmeldung geht es zu `/anmelden?weiter=…`. */
export function AnmeldeSchutz({ children }: { children: ReactNode }) {
  const [ich, setIch] = useState<Ich | null>(null)
  const [fehler, setFehler] = useState<unknown>(null)

  useEffect(() => {
    let aktiv = true
    api
      .ich()
      .then((daten) => {
        if (!aktiv) return
        if (daten === null) {
          zurAnmeldung()
          return
        }
        setIch(daten)
      })
      .catch((e: unknown) => {
        if (aktiv) setFehler(e)
      })
    return () => {
      aktiv = false
    }
  }, [])

  async function neuLaden() {
    const daten = await api.ich()
    if (daten === null) zurAnmeldung()
    else setIch(daten)
  }

  if (fehler) return <Meldung fehler={fehler} className="m-6" />
  if (!ich) {
    return (
      <div className="p-6" aria-busy="true" aria-label="Lädt">
        <div className="mb-4 h-10 w-full animate-pulse rounded bg-gray-200" />
        <div className="h-32 w-full animate-pulse rounded bg-gray-200" />
      </div>
    )
  }
  return (
    <IchProvider ich={ich} neuLaden={neuLaden}>
      <Kopfleiste />
      <main className="pt-[60px]">{children}</main>
    </IchProvider>
  )
}
