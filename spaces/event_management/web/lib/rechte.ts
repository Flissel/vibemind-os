import type { Ich } from '@/lib/typen'

// Nur zum Ein- und Ausblenden; die API prüft jede Anfrage selbst.

/** Neues Event anlegen: in einer Firma (`firmaId`) oder überhaupt in irgendeiner. */
export function darfAnlegen(ich: Ich, firmaId?: string): boolean {
  if (ich.betreiber) return true
  if (firmaId === undefined) return ich.rechte.event_anlegen.length > 0
  return ich.rechte.event_anlegen.includes(firmaId)
}

/** Leitungsrechte in der Firma (Mitglieder, Absagen, Archivieren). */
export function darfVerwalten(ich: Ich, firmaId: string): boolean {
  return ich.betreiber || ich.rechte.verwalten.includes(firmaId)
}
