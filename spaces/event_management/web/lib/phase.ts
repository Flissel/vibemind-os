import type { EventStatus, KatalogEvent, Phase } from '@/lib/typen'

export interface PhasenGruppen {
  laeuft: KatalogEvent[]
  kommend: KatalogEvent[]
  vergangen: KatalogEvent[]
}

function zeitwert(iso: string): number {
  return new Date(iso).getTime()
}

/** Teilt den Katalog nach der Phase der API (hier wird nichts neu berechnet) und
 *  sortiert wie die API: laufend und kommend nach Beginn aufsteigend, vergangen nach
 *  Ende absteigend. */
export function gruppiere(events: KatalogEvent[]): PhasenGruppen {
  const gruppen: PhasenGruppen = { laeuft: [], kommend: [], vergangen: [] }
  for (const e of events) gruppen[e.phase].push(e)
  gruppen.laeuft.sort((a, b) => zeitwert(a.beginn) - zeitwert(b.beginn))
  gruppen.kommend.sort((a, b) => zeitwert(a.beginn) - zeitwert(b.beginn))
  gruppen.vergangen.sort((a, b) => zeitwert(b.ende) - zeitwert(a.ende))
  return gruppen
}

/** Phase wie `events.py:_phase`: abgesagt/archiviert gelten als vergangen, sonst nach Zeit. */
export function phaseAus(
  e: { status: EventStatus; beginn: string; ende: string },
  jetzt: number,
): Phase {
  if (e.status === 'abgesagt' || e.status === 'archiviert') return 'vergangen'
  if (jetzt < new Date(e.beginn).getTime()) return 'kommend'
  if (jetzt > new Date(e.ende).getTime()) return 'vergangen'
  return 'laeuft'
}

/** Ortszeile: Adresse, sonst „online" bei Link, sonst „Ort offen". */
export function ortText(e: { ort_adresse: string | null; ort_link: string | null }): string {
  if (e.ort_adresse) return e.ort_adresse
  if (e.ort_link) return 'online'
  return 'Ort offen'
}
