import clsx from 'clsx'
import { Search } from 'lucide-react'
import type { Firma } from '@/lib/typen'

export interface KategorieZaehlung {
  id: string
  name: string
  anzahl: number
}

interface Props {
  suche: string
  onSuche: (wert: string) => void
  firmen: Firma[]
  firma: string
  onFirma: (id: string) => void
  kategorien: KategorieZaehlung[]
  gesamt: number
  kategorie: string
  onKategorie: (id: string) => void
}

const CHIP = 'px-3 py-1 text-xs font-semibold rounded-full transition-colors'
const AKTIV = 'bg-gray-900 text-white'
const RUHIG = 'bg-gray-100 text-gray-700 hover:bg-gray-200'

export function KatalogFilter(p: Props) {
  return (
    <div className="space-y-3">
      <div className="flex flex-col gap-3 sm:flex-row">
        <div className="relative flex-1">
          <Search
            className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400"
            aria-hidden
          />
          <input
            type="search"
            value={p.suche}
            onChange={(e) => p.onSuche(e.target.value)}
            placeholder="Suchen: Name, Ort, Firma …"
            aria-label="Suchen"
            className="w-full rounded-lg bg-white py-2 pl-9 pr-3 text-sm nice-shadow focus:outline-none"
          />
        </div>
        <select
          value={p.firma}
          onChange={(e) => p.onFirma(e.target.value)}
          aria-label="Firma"
          className="rounded-lg bg-white px-3 py-2 text-sm nice-shadow"
        >
          <option value="">Alle Firmen</option>
          {p.firmen.map((f) => (
            <option key={f.id} value={f.id}>
              {f.name}
            </option>
          ))}
        </select>
      </div>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => p.onKategorie('')}
          aria-pressed={p.kategorie === ''}
          className={clsx(CHIP, p.kategorie === '' ? AKTIV : RUHIG)}
        >
          Alle {p.gesamt}
        </button>
        {p.kategorien.map((k) => (
          <button
            key={k.id}
            type="button"
            onClick={() => p.onKategorie(k.id)}
            aria-pressed={p.kategorie === k.id}
            className={clsx(CHIP, p.kategorie === k.id ? AKTIV : RUHIG)}
          >
            {k.name} {k.anzahl}
          </button>
        ))}
      </div>
    </div>
  )
}
