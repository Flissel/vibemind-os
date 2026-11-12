// Euro-Beträge ohne Fließkomma: Text <-> ganze Cent.

const MIT_TAUSENDERPUNKT = /^\d{1,3}(\.\d{3})+(,\d{1,2})?$/
const EINFACH = /^\d+([.,]\d{1,2})?$/

/** „12,50" -> 1250, „0,1" -> 10, „1.234,50" -> 123450. `null` bei allem Ungültigen
 *  (Buchstaben, negativ, mehr als zwei Nachkommastellen, leer). */
export function parseEuro(eingabe: string): number | null {
  const text = eingabe.trim().replace(/\s*€$/, '')
  let ganz: string
  let rest: string
  if (MIT_TAUSENDERPUNKT.test(text)) {
    const [vorne = '', hinten = ''] = text.replace(/\./g, '').split(',')
    ganz = vorne
    rest = hinten
  } else if (EINFACH.test(text)) {
    const [vorne = '', hinten = ''] = text.replace(',', '.').split('.')
    ganz = vorne
    rest = hinten
  } else {
    return null
  }
  const cent = Number(ganz + rest.padEnd(2, '0'))
  return Number.isSafeInteger(cent) ? cent : null
}

/** 123450 -> „1.234,50 €" */
export function formatEuro(cent: number): string {
  const euro = Math.floor(cent / 100)
  const rest = String(cent % 100).padStart(2, '0')
  return `${euro.toLocaleString('de-DE')},${rest} €`
}

/** 123450 -> „1234,50" (für das Eingabefeld, ohne Tausenderpunkt). */
export function alsEuroEingabe(cent: number): string {
  return `${Math.floor(cent / 100)},${String(cent % 100).padStart(2, '0')}`
}
