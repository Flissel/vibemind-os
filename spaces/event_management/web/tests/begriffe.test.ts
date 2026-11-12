import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { KERN, bausteinName, begriffe } from '@/lib/begriffe'

function pythonNamen(konstante: string): string[] {
  // npm test läuft im Ordner web/; die Fachschicht liegt eine Ebene darüber.
  const quelle = readFileSync(resolve(process.cwd(), '../system/bausteine.py'), 'utf-8')
  const treffer = new RegExp(`${konstante}: tuple\\[str, \\.\\.\\.\\] = \\(([^)]*)\\)`).exec(quelle)
  if (!treffer?.[1]) throw new Error(`${konstante} nicht gefunden`)
  return [...treffer[1].matchAll(/"([a-z_]+)"/g)].map((m) => m[1] ?? '')
}

describe('begriffe', () => {
  it('deckt alle 21 Bausteine aus system/bausteine.py ab (Drift-Waechter)', () => {
    const erwartet = [...pythonNamen('KERN'), ...pythonNamen('ZUSAETZE')]
    expect(erwartet).toHaveLength(21)
    expect(Object.keys(begriffe).sort()).toEqual([...erwartet].sort())
  })

  it('KERN entspricht system/bausteine.py KERN (Drift-Waechter)', () => {
    expect([...KERN]).toEqual(pythonNamen('KERN'))
  })

  it('kennt die Standardnamen', () => {
    expect(bausteinName('wettbewerb_teams')).toBe('Teams')
    expect(bausteinName('orga_team')).toBe('Orga-Team')
    expect(bausteinName('abgaben')).toBe('Abgaben')
  })

  it('Kategorie-Begriff gewinnt vor Standardname, unbekannt faellt auf die Id zurueck', () => {
    expect(bausteinName('wettbewerb_teams', { wettbewerb_teams: 'Mannschaften' })).toBe(
      'Mannschaften',
    )
    expect(bausteinName('jury', { abgaben: 'X' })).toBe('Jury')
    expect(bausteinName('neu_unbekannt')).toBe('neu_unbekannt')
  })
})
