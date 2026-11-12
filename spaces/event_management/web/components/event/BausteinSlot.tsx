import type { EventDetail } from '@/lib/typen'

interface Props {
  event: EventDetail
  baustein: string
  /** Anzeigename des Bausteins (R2). */
  name: string
}

/** Einziger Einsteckpunkt für die Baustein-Reiter; Task 7 ersetzt den Inhalt durch `BausteinReiter`. */
export function BausteinSlot({ name }: Props) {
  return <p className="text-sm text-gray-500">Bereich „{name}“ wird geladen …</p>
}
