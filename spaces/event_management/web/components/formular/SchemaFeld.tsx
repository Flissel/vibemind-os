'use client'

import type { Feld } from '@/lib/typen'

interface Props {
  feld: Feld
  /** Aktueller Wert; `undefined` = nicht gesetzt. */
  wert: unknown
  aendern: (wert: unknown) => void
}

const EINGABE =
  'w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:border-gray-900 focus:outline-none'
const BESCHRIFTUNG = 'mb-1 block text-sm font-medium text-gray-700'

function alsText(wert: unknown): string {
  return typeof wert === 'string' || typeof wert === 'number' ? String(wert) : ''
}

function leerAlsNichtGesetzt(text: string): string | undefined {
  return text === '' ? undefined : text
}

/** Eine Eingabe für ein Schema-Feld (`Feld.typ`). Leere Eingaben melden `undefined`;
 *  `ja_nein` meldet immer true/false. */
export function SchemaFeld({ feld, wert, aendern }: Props) {
  const id = `feld-${feld.id}`
  const text = feld.pflicht ? `${feld.beschriftung} *` : feld.beschriftung

  if (feld.typ === 'ja_nein') {
    return (
      <div className="flex items-center gap-2">
        <input
          id={id}
          type="checkbox"
          checked={wert === true}
          onChange={(e) => aendern(e.target.checked)}
          className="h-4 w-4 rounded border-gray-300"
        />
        <label htmlFor={id} className="text-sm font-medium text-gray-700">
          {text}
        </label>
      </div>
    )
  }

  let eingabe
  if (feld.typ === 'langtext') {
    eingabe = (
      <textarea
        id={id}
        rows={4}
        value={alsText(wert)}
        onChange={(e) => aendern(leerAlsNichtGesetzt(e.target.value))}
        className={EINGABE}
      />
    )
  } else if (feld.typ === 'auswahl') {
    eingabe = (
      <select
        id={id}
        value={alsText(wert)}
        onChange={(e) => aendern(leerAlsNichtGesetzt(e.target.value))}
        className={EINGABE}
      >
        <option value="">–</option>
        {feld.optionen.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    )
  } else if (feld.typ === 'zahl') {
    eingabe = (
      <input
        id={id}
        type="number"
        value={alsText(wert)}
        onChange={(e) => {
          const zahl = e.target.valueAsNumber
          aendern(e.target.value === '' || Number.isNaN(zahl) ? undefined : zahl)
        }}
        className={EINGABE}
      />
    )
  } else {
    const typ =
      feld.typ === 'datum'
        ? 'date'
        : feld.typ === 'datumzeit'
          ? 'datetime-local'
          : feld.typ === 'url'
            ? 'url'
            : 'text'
    eingabe = (
      <input
        id={id}
        type={typ}
        value={alsText(wert)}
        onChange={(e) => aendern(leerAlsNichtGesetzt(e.target.value))}
        className={EINGABE}
      />
    )
  }

  return (
    <div>
      <label htmlFor={id} className={BESCHRIFTUNG}>
        {text}
      </label>
      {eingabe}
    </div>
  )
}
