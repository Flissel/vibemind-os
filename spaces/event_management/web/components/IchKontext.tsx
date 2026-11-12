'use client'

import { createContext, useContext, type ReactNode } from 'react'
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
