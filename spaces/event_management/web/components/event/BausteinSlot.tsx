import { Meldung } from '@/components/Meldung'
import { BausteinReiter } from '@/components/event/BausteinReiter'
import type { BausteineKatalog, EventDetail } from '@/lib/typen'

interface Props {
  event: EventDetail
  baustein: string
  /** Anzeigename des Bausteins (R2). */
  name: string
  /** `GET /api/bausteine`; `null`, solange es lädt. */
  verzeichnis: BausteineKatalog | null
  /** Fehler beim Laden des Verzeichnisses. */
  fehler?: unknown
}

/** Einziger Einsteckpunkt für die Baustein-Reiter der Event-Seite. */
export function BausteinSlot({ event, baustein, name, verzeichnis, fehler }: Props) {
  if (fehler) return <Meldung fehler={fehler} />
  if (!verzeichnis) return <p className="text-sm text-gray-500">Bereich „{name}“ wird geladen …</p>
  return (
    <BausteinReiter
      eventId={event.id}
      firmaId={event.firma_id}
      baustein={baustein}
      begriffe={event.schema.begriffe}
      verzeichnis={verzeichnis}
    />
  )
}
