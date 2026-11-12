// Antworttypen der event-api (abgeleitet aus system/api.py, events.py, kategorien.py,
// kern.py, verlauf.py, firmen.py). Zeitpunkte und UUIDs kommen als Text (ISO 8601).

export type Rolle = 'leitung' | 'mitglied'

export type EventStatus =
  | 'entwurf'
  | 'geplant'
  | 'laeuft'
  | 'abgeschlossen'
  | 'abgesagt'
  | 'archiviert'

export type Phase = 'laeuft' | 'kommend' | 'vergangen'

export interface Person {
  id: string
  name: string
  mail: string
}

export interface Firma {
  id: string
  name: string
  kurz: string
}

/** Firma in `GET /api/ich` und `GET /api/firmen`: mit der Rolle der angemeldeten Person
 *  (null, wenn ein Betreiber ohne Mitgliedschaft die Firma sieht). */
export interface IchFirma extends Firma {
  rolle: Rolle | null
}

export interface Ich {
  person: Person
  betreiber: boolean
  firmen: IchFirma[]
  csrf: string
  rechte: { event_anlegen: string[]; verwalten: string[] }
}

export interface Mitglied {
  person_id: string
  name: string
  mail: string
  rolle: Rolle
}

export interface Kategorie {
  id: string
  name: string
  eltern: string | null
}

export type FeldTyp =
  | 'text'
  | 'langtext'
  | 'zahl'
  | 'ja_nein'
  | 'datum'
  | 'datumzeit'
  | 'auswahl'
  | 'url'

export interface Feld {
  id: string
  typ: FeldTyp
  beschriftung: string
  pflicht: boolean
  vorgabe: unknown
  optionen: string[]
}

export type Eckdatum = 'ort_adresse' | 'ort_link' | 'ende' | 'beschreibung'

/** `GET /api/kategorien/{id}/schema` */
export interface WirksamesSchema {
  kategorie: string
  name: string
  kette: string[]
  bausteine: string[]
  felder: Feld[]
  begriffe: Record<string, string>
  eckdaten_pflicht: Eckdatum[]
}

/** `schema` im Event-Detail (ohne `kette` und `eckdaten_pflicht`). */
export type EventSchema = Omit<WirksamesSchema, 'kette' | 'eckdaten_pflicht'>

/** Zeile der Tabelle `event.event`, auch die Antwort von POST/PATCH/Status. */
export interface EventBasis {
  id: string
  firma_id: string
  kategorie: string
  name: string
  beginn: string
  ende: string
  ort_adresse: string | null
  ort_link: string | null
  beschreibung: string | null
  status: EventStatus
  quelle: 'neu' | 'legacy'
  legacy_url: string | null
  metadaten: Record<string, unknown>
  angelegt: string
  geaendert: string
}

export interface Kennzahlen {
  aufgaben_offen: number
  aufgaben_gesamt: number
  teilnehmende_zugesagt: number
}

/** `GET /api/events` */
export interface KatalogEvent extends EventBasis {
  kategorie_name: string
  firma_name: string
  phase: Phase
  kennzahlen: Kennzahlen
}

/** `GET /api/events/{id}` */
export interface EventDetail extends EventBasis {
  schema: EventSchema
}

/** `GET /api/bausteine`: Spaltentypen sind `text|ganzzahl|datumzeit|auswahl:a,b|url|kontakt|ref:<eintrag>`. */
export interface BausteinInfo {
  /** Zugehöriger Baustein (der Eintragsname selbst, z. B. `aufgaben`, oder der Eltern-Baustein). */
  baustein: string
  felder: Record<string, string>
  pflicht: string[]
  reihenfolge: string[]
}

export type BausteineKatalog = Record<string, BausteinInfo>

/** Zeile eines Bausteins (`GET /api/events/{id}/b/{baustein}`); die Spalten stehen in `BausteinInfo`. */
export interface Zeile {
  id: string
  [spalte: string]: unknown
}

export interface Kontakt {
  id: string
  firma_id: string
  name: string
  mail: string | null
  telegram: string | null
  person_id: string | null
  angelegt: string
}

export interface VerlaufEintrag {
  id: number
  event_id: string
  firma_id: string
  zeitpunkt: string
  actor: string
  actor_person_id: string | null
  quelle: string
  baustein: string
  aktion: string
  ziel_id: string | null
}

/** `GET /api/einladung/{token}` (ungueltig: nur `gueltig: false`). */
export type EinladungInfo =
  | { gueltig: false }
  | { gueltig: true; firma_name: string; braucht_passwort: boolean }
