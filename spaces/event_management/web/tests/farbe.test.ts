import { describe, expect, it } from 'vitest'
import { kategorieVerlauf, VERLAEUFE } from '@/lib/farbe'

describe('kategorieVerlauf', () => {
  it('hat neun feste Paare', () => {
    expect(VERLAEUFE).toHaveLength(9)
    expect(new Set(VERLAEUFE).size).toBe(9)
  })

  it('ist je Kategorie stabil', () => {
    expect(kategorieVerlauf('hackathon')).toBe(kategorieVerlauf('hackathon'))
  })

  it('liefert fuer jede Eingabe einen gueltigen Verlauf aus der Liste', () => {
    for (const id of ['hackathon', 'konferenz', 'unbekannt-xyz', '', 'ä'.repeat(50)]) {
      const v = kategorieVerlauf(id)
      expect(VERLAEUFE).toContain(v)
      expect(v).toMatch(/^from-[a-z]+-\d{3} to-[a-z]+-\d{3}$/)
    }
  })

  it('streut ueber mehrere Verlaeufe', () => {
    const ids = ['hackathon', 'konferenz', 'workshop', 'meetup', 'messe', 'webinar', 'sitzung']
    expect(new Set(ids.map(kategorieVerlauf)).size).toBeGreaterThan(2)
  })
})
