'use client'

import Link from 'next/link'
import { useState } from 'react'
import { LogOut } from 'lucide-react'
import { useIch } from '@/components/AnmeldeSchutz'
import { Meldung } from '@/components/Meldung'
import { api } from '@/lib/api'

function zurAnmeldeseite(): void {
  window.location.assign('/anmelden')
}

export function Kopfleiste({ nachAbmeldung = zurAnmeldeseite }: { nachAbmeldung?: () => void }) {
  const ich = useIch()
  const [fehler, setFehler] = useState<unknown>(null)
  const zeigeFirmen = ich.betreiber || ich.rechte.verwalten.length > 0

  async function abmelden() {
    setFehler(null)
    try {
      await api.abmelden()
      nachAbmeldung()
    } catch (e: unknown) {
      setFehler(e)
    }
  }

  return (
    <header className="fixed inset-x-0 top-0 z-40 h-[60px] bg-white/90 backdrop-blur-lg nice-shadow">
      <div className="mx-auto flex h-full max-w-screen-2xl items-center justify-between gap-4 px-4 sm:px-6 lg:px-8">
        <nav className="flex items-center gap-6" aria-label="Hauptnavigation">
          <Link href="/" className="text-lg font-bold tracking-tight text-gray-900">
            VibeMind Events
          </Link>
          <Link href="/" className="text-sm font-medium text-gray-600 hover:text-gray-900">
            Katalog
          </Link>
          {zeigeFirmen && (
            <Link href="/firmen" className="text-sm font-medium text-gray-600 hover:text-gray-900">
              Firmen
            </Link>
          )}
        </nav>
        <div className="flex items-center gap-3 text-sm">
          <span className="text-gray-700">{ich.person.name}</span>
          <button
            type="button"
            onClick={abmelden}
            className="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 px-3 py-1.5 font-medium text-gray-700 hover:bg-gray-50"
          >
            <LogOut className="h-4 w-4" aria-hidden />
            Abmelden
          </button>
        </div>
      </div>
      {fehler !== null && (
        <div className="absolute inset-x-0 top-full mx-auto max-w-screen-2xl px-4 pt-2 sm:px-6 lg:px-8">
          <Meldung fehler={fehler} />
        </div>
      )}
    </header>
  )
}
