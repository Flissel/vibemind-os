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

import { ortText, phaseAus } from '@/lib/phase'

describe('phaseAus', () => {
  const zeit = { beginn: '2026-11-10T10:00:00+01:00', ende: '2026-11-10T12:00:00+01:00' }
  const um = (iso: string) => new Date(iso).getTime()
  it('rechnet nach der Zeit', () => {
    expect(phaseAus({ status: 'geplant', ...zeit }, um('2026-11-09T10:00:00+01:00'))).toBe('kommend')
    expect(phaseAus({ status: 'geplant', ...zeit }, um('2026-11-10T11:00:00+01:00'))).toBe('laeuft')
    expect(phaseAus({ status: 'geplant', ...zeit }, um('2026-11-11T10:00:00+01:00'))).toBe('vergangen')
  })
  it('abgesagt und archiviert gelten als vergangen, auch bei künftigem Termin', () => {
    const frueh = um('2026-11-01T10:00:00+01:00')
    expect(phaseAus({ status: 'abgesagt', ...zeit }, frueh)).toBe('vergangen')
    expect(phaseAus({ status: 'archiviert', ...zeit }, frueh)).toBe('vergangen')
  })
})

describe('ortText', () => {
  it('Adresse, online oder offen', () => {
    expect(ortText({ ort_adresse: 'Hauptstr. 1', ort_link: 'https://x.example.org' })).toBe('Hauptstr. 1')
    expect(ortText({ ort_adresse: null, ort_link: 'https://x.example.org' })).toBe('online')
    expect(ortText({ ort_adresse: null, ort_link: null })).toBe('Ort offen')
  })
})
