import type { KatalogEvent } from '@/lib/typen'

/** Testdaten: ein Katalogeintrag mit überschreibbaren Feldern. */
export function katalogEvent(o: Partial<KatalogEvent> = {}): KatalogEvent {
  return {
    id: 'e1',
    firma_id: 'f1',
    kategorie: 'hackathon',
    name: 'Sommer-Hackathon',
    beginn: '2026-11-20T09:00:00+01:00',
    ende: '2026-11-21T18:00:00+01:00',
    ort_adresse: 'Musterstraße 1, Berlin',
    ort_link: null,
    beschreibung: null,
    status: 'geplant',
    quelle: 'neu',
    legacy_url: null,
    metadaten: {},
    angelegt: '2026-11-01T10:00:00+01:00',
    geaendert: '2026-11-01T10:00:00+01:00',
    kategorie_name: 'Hackathon',
    firma_name: 'Beispiel GmbH',
    phase: 'kommend',
    kennzahlen: { aufgaben_offen: 1, aufgaben_gesamt: 4, teilnehmende_zugesagt: 7 },
    ...o,
  }
}
