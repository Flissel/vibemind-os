import { describe, expect, it } from 'vitest'
import { aktionText, quelleText, spaltenName, wertText } from '@/lib/anzeige'

describe('spaltenName', () => {
  it('kennt die festen Namen mit Umlauten', () => {
    expect(spaltenName('faellig')).toBe('Fällig')
    expect(spaltenName('betrag_cent')).toBe('Betrag')
    expect(spaltenName('kategorie_id')).toBe('Kategorie')
    expect(spaltenName('verantwortlich_id')).toBe('Verantwortlich')
    expect(spaltenName('kontakt_id')).toBe('Kontakt')
    expect(spaltenName('beginn')).toBe('Beginn')
    expect(spaltenName('zusage')).toBe('Zusage')
    expect(spaltenName('titel')).toBe('Titel')
  })
  it('fällt generisch zurück: _id/_cent weg, erster Buchstabe groß', () => {
    expect(spaltenName('anzahl_gaeste')).toBe('Anzahl gaeste')
    expect(spaltenName('preis_cent')).toBe('Preis')
    expect(spaltenName('raum_id')).toBe('Raum')
  })
})

describe('wertText', () => {
  it('übersetzt Auswahlwerte, Rohwert als Rückfall', () => {
    expect(wertText('in_arbeit')).toBe('in Arbeit')
    expect(wertText('bestaetigt')).toBe('bestätigt')
    expect(wertText('einnahme')).toBe('Einnahme')
    expect(wertText('kosten')).toBe('Kosten')
    expect(wertText('unbekannt_x')).toBe('unbekannt_x')
  })
})

describe('Verlauf', () => {
  it('aktionText', () => {
    expect(aktionText('angelegt')).toBe('angelegt')
    expect(aktionText('geaendert')).toBe('geändert')
    expect(aktionText('geloescht')).toBe('gelöscht')
    expect(aktionText('status_laeuft')).toBe('Status: läuft')
    expect(aktionText('status_entwurf')).toBe('Status: Entwurf')
    expect(aktionText('status_archiviert')).toBe('Status: archiviert')
    expect(aktionText('sonstwas')).toBe('sonstwas')
  })
  it('quelleText', () => {
    expect(quelleText('oberflaeche')).toBe('Oberfläche')
    expect(quelleText('bot')).toBe('Bot')
    expect(quelleText('system')).toBe('System')
    expect(quelleText('web')).toBe('web')
  })
})
