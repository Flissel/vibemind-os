import { describe, it, expect } from 'vitest'
import { kartenModell } from './zustand'
const basis = { id: 'x', name: 'X', icon: '', detail: null, werkzeuge: 0, statischerSchluessel: false, offeneFreigaben: 0 }

describe('kartenModell', () => {
  it.each([
    ['verbunden', false, 'gruen', 'verbunden · 5 Werkzeuge', ['abmelden', 'neuVerbinden', 'entfernen']],
    ['verbunden', true, 'gruen', 'verbunden · 5 Werkzeuge', ['neuVerbinden', 'entfernen']],
    ['anmeldung_noetig', false, 'gelb', 'Anmeldung nötig', ['anmelden', 'entfernen']],
    ['fehlt_schluessel', true, 'gelb', 'Schlüssel fehlt', ['schluessel', 'entfernen']],
    ['schluessel_abgelehnt', true, 'rot', 'Schlüssel abgelehnt', ['schluessel', 'neuVerbinden', 'entfernen']],
    ['nicht_erreichbar', true, 'rot', 'nicht erreichbar', ['neuVerbinden', 'schluessel', 'entfernen']],
    ['nicht_erreichbar', false, 'rot', 'nicht erreichbar', ['neuVerbinden', 'entfernen']],
    ['vorlage_ungueltig', false, 'grau', 'Vorlage ungültig', ['entfernen']],
    ['nicht_zugelassen', false, 'grau', 'nicht zugelassen', ['entfernen']],
    ['verfuegbar', false, 'grau', 'wird geprüft …', []],
    ['irgendwas', false, 'grau', 'wird geprüft …', []],
  ])('%s (statisch=%s)', (zustand, statisch, farbe, text, knoepfe) => {
    const m = kartenModell({ ...basis, zustand, statischerSchluessel: statisch, werkzeuge: zustand === 'verbunden' ? 5 : 0 })
    expect(m.farbe).toBe(farbe)
    expect(m.text).toBe(text)
    expect(m.knoepfe).toEqual(knoepfe)
  })
  it('haengt detail an', () => {
    expect(kartenModell({ ...basis, zustand: 'anmeldung_noetig', detail: 'abgemeldet' }).text).toBe('Anmeldung nötig · abgemeldet')
  })
})
