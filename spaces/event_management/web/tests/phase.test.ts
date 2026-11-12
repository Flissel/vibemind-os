import { describe, expect, it } from 'vitest'
import { gruppiere } from '@/lib/phase'
import type { KatalogEvent, Phase } from '@/lib/typen'

function ev(id: string, phase: Phase, beginn: string, ende: string): KatalogEvent {
  return {
    id,
    firma_id: 'f',
    kategorie: 'hackathon',
    name: id,
    beginn,
    ende,
    ort_adresse: null,
    ort_link: null,
    beschreibung: null,
    status: 'geplant',
    quelle: 'neu',
    legacy_url: null,
    metadaten: {},
    angelegt: beginn,
    geaendert: beginn,
    kategorie_name: 'Hackathon',
    firma_name: 'Beispiel GmbH',
    phase,
    kennzahlen: { aufgaben_offen: 0, aufgaben_gesamt: 0, teilnehmende_zugesagt: 0 },
  }
}

describe('gruppiere', () => {
  it('liefert die Gruppen in der Reihenfolge laeuft, kommend, vergangen', () => {
    const g = gruppiere([])
    expect(Object.keys(g)).toEqual(['laeuft', 'kommend', 'vergangen'])
    expect(g).toEqual({ laeuft: [], kommend: [], vergangen: [] })
  })

  it('sortiert kommend nach Beginn aufsteigend, vergangen nach Ende absteigend', () => {
    const g = gruppiere([
      ev('v1', 'vergangen', '2026-01-01T10:00:00Z', '2026-01-02T10:00:00Z'),
      ev('k2', 'kommend', '2026-12-01T10:00:00Z', '2026-12-02T10:00:00Z'),
      ev('l1', 'laeuft', '2026-11-12T08:00:00Z', '2026-11-13T08:00:00Z'),
      ev('v2', 'vergangen', '2026-03-01T10:00:00Z', '2026-03-02T10:00:00Z'),
      ev('k1', 'kommend', '2026-11-20T10:00:00Z', '2026-11-21T10:00:00Z'),
    ])
    expect(g.laeuft.map((e) => e.id)).toEqual(['l1'])
    expect(g.kommend.map((e) => e.id)).toEqual(['k1', 'k2'])
    expect(g.vergangen.map((e) => e.id)).toEqual(['v2', 'v1'])
  })

  it('verlaesst sich auf die Phase der API (kein Neuberechnen)', () => {
    const g = gruppiere([ev('x', 'kommend', '2020-01-01T00:00:00Z', '2020-01-02T00:00:00Z')])
    expect(g.kommend).toHaveLength(1)
  })

  it('aendert die Eingabe nicht', () => {
    const eingabe = [
      ev('b', 'kommend', '2026-12-01T00:00:00Z', '2026-12-02T00:00:00Z'),
      ev('a', 'kommend', '2026-11-20T00:00:00Z', '2026-11-21T00:00:00Z'),
    ]
    gruppiere(eingabe)
    expect(eingabe.map((e) => e.id)).toEqual(['b', 'a'])
  })
})
