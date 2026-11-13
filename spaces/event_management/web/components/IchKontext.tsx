'use client'

import { createContext, useContext, type ReactNode } from 'react'
import type { Ich } from '@/lib/typen'

const IchContext = createContext<Ich | null>(null)
const NeuLadenContext = createContext<() => Promise<void>>(() => Promise.resolve())

interface Props {
  ich: Ich
  /** Lädt `GET /api/ich` neu (z. B. nach einer eigenen Rollenänderung). */
  neuLaden?: () => Promise<void>
  children: ReactNode
}

export function IchProvider({ ich, neuLaden, children }: Props) {
  return (
    <IchContext.Provider value={ich}>
      <NeuLadenContext.Provider value={neuLaden ?? (() => Promise.resolve())}>
        {children}
      </NeuLadenContext.Provider>
    </IchContext.Provider>
  )
}

/** Die angemeldete Person; nur unter `AnmeldeSchutz` (oder `IchProvider`) benutzbar. */
export function useIch(): Ich {
  const ich = useContext(IchContext)
  if (!ich) throw new Error('useIch braucht AnmeldeSchutz')
  return ich
}

/** Aktualisiert die Angaben der angemeldeten Person (Firmen, Rollen, Rechte). */
export function useIchNeuLaden(): () => Promise<void> {
  return useContext(NeuLadenContext)
}
