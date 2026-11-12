'use client'

import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { Meldung } from '@/components/Meldung'
import { api, zurAnmeldung } from '@/lib/api'
import type { Ich } from '@/lib/typen'

const IchContext = createContext<Ich | null>(null)

export function IchProvider({ ich, children }: { ich: Ich; children: ReactNode }) {
  return <IchContext.Provider value={ich}>{children}</IchContext.Provider>
}

/** Die angemeldete Person; nur unter `AnmeldeSchutz` (oder `IchProvider`) benutzbar. */
export function useIch(): Ich {
  const ich = useContext(IchContext)
  if (!ich) throw new Error('useIch braucht AnmeldeSchutz')
  return ich
}

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

  if (fehler) return <Meldung fehler={fehler} className="m-6" />
  if (!ich) {
    return (
      <div className="p-6" aria-busy="true" aria-label="Lädt">
        <div className="mb-4 h-10 w-full animate-pulse rounded bg-gray-200" />
        <div className="h-32 w-full animate-pulse rounded bg-gray-200" />
      </div>
    )
  }
  return <IchProvider ich={ich}>{children}</IchProvider>
}
