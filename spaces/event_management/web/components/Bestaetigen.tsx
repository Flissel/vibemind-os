'use client'

import { type ReactNode, useEffect, useId, useState } from 'react'
import clsx from 'clsx'

interface Props {
  titel: string
  text: string
  /** Beschriftung des Bestätigen-Knopfs, z. B. „Löschen". */
  aktion: string
  /** Wenn gesetzt, muss dieser Name zum Bestätigen eingetippt werden. */
  namePruefen?: string
  gefaehrlich?: boolean
  laeuft?: boolean
  /** Zusätzliche Eingaben unter dem Text (z. B. ein Prüfcode). */
  children?: ReactNode
  /** false sperrt den Bestätigen-Knopf (z. B. solange die Zusatzeingabe fehlt). */
  bereit?: boolean
  onBestaetigen: () => void
  onAbbrechen: () => void
}

/** Modaler Dialog für heikle Aktionen. */
export function Bestaetigen({
  titel,
  text,
  aktion,
  namePruefen,
  gefaehrlich = false,
  laeuft = false,
  children,
  bereit = true,
  onBestaetigen,
  onAbbrechen,
}: Props) {
  const [eingabe, setEingabe] = useState('')
  const titelId = useId()
  const feldId = useId()
  const freigegeben = (namePruefen === undefined || eingabe === namePruefen) && bereit && !laeuft

  useEffect(() => {
    function taste(e: KeyboardEvent) {
      if (e.key === 'Escape') onAbbrechen()
    }
    document.addEventListener('keydown', taste)
    return () => document.removeEventListener('keydown', taste)
  }, [onAbbrechen])

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titelId}
        className="w-full max-w-md rounded-xl bg-white p-6 shadow-xl"
      >
        <h2 id={titelId} className="text-lg font-semibold text-gray-900">
          {titel}
        </h2>
        <p className="mt-2 text-sm text-gray-600">{text}</p>
        {children}
        {namePruefen !== undefined && (
          <div className="mt-4">
            <label htmlFor={feldId} className="block text-sm text-gray-700">
              Zum Bestätigen den Namen eintippen: <strong>{namePruefen}</strong>
            </label>
            <input
              id={feldId}
              value={eingabe}
              onChange={(e) => setEingabe(e.target.value)}
              autoComplete="off"
              className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
            />
          </div>
        )}
        <div className="mt-6 flex justify-end gap-2">
          <button
            type="button"
            onClick={onAbbrechen}
            className="rounded-lg border border-gray-200 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
          >
            Abbrechen
          </button>
          <button
            type="button"
            disabled={!freigegeben}
            onClick={onBestaetigen}
            className={clsx(
              'rounded-lg px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50',
              gefaehrlich ? 'bg-red-600 hover:bg-red-700' : 'bg-gray-900 hover:bg-gray-800',
            )}
          >
            {aktion}
          </button>
        </div>
      </div>
    </div>
  )
}
