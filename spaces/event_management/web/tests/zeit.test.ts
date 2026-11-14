import { describe, expect, it } from 'vitest'
import { alsEingabe, ausEingabe, zeigeDatum, zeigeSpanne, zeigeZeit } from '@/lib/zeit'

describe('zeit (Prozess-TZ UTC, Anzeige immer Europe/Berlin)', () => {
  it('Testlauf ist wirklich UTC', () => {
    expect(new Date(2026, 0, 1, 12).toISOString()).toBe('2026-01-01T12:00:00.000Z')
  })

  it('zeigt 22:30 UTC als 23:30 Berliner Zeit', () => {
    expect(zeigeZeit('2026-11-12T22:30:00+00:00')).toBe('23:30')
    expect(zeigeDatum('2026-11-12T22:30:00+00:00')).toBe('12.11.2026')
  })

  it('rollt das Datum ueber Mitternacht nach Berliner Zeit', () => {
    expect(zeigeDatum('2026-11-12T23:30:00+00:00')).toBe('13.11.2026')
    expect(zeigeZeit('2026-11-12T23:30:00+00:00')).toBe('00:30')
  })

  it('Sommerzeit-Grenze 29.03.2026', () => {
    expect(zeigeZeit('2026-03-29T00:59:00+00:00')).toBe('01:59')
    expect(zeigeZeit('2026-03-29T01:00:00+00:00')).toBe('03:00')
    expect(alsEingabe('2026-03-29T01:00:00+00:00')).toBe('2026-03-29T03:00')
    expect(alsEingabe('2026-03-28T23:00:00Z')).toBe('2026-03-29T00:00')
  })

  it('Spanne am selben Tag mit Uhrzeiten', () => {
    expect(zeigeSpanne('2026-11-12T17:00:00+00:00', '2026-11-12T22:30:00+00:00')).toBe(
      '12.11.2026, 18:00–23:30',
    )
  })

  it('Spanne ueber mehrere Tage im selben Monat', () => {
    expect(zeigeSpanne('2026-05-14T07:00:00+00:00', '2026-05-15T16:00:00+00:00')).toBe(
      '14.–15.05.2026',
    )
  })

  it('Spanne ueber Monats- und Jahresgrenze', () => {
    expect(zeigeSpanne('2026-05-28T07:00:00Z', '2026-06-02T16:00:00Z')).toBe('28.05.–02.06.2026')
    expect(zeigeSpanne('2026-12-30T07:00:00Z', '2027-01-02T16:00:00Z')).toBe(
      '30.12.2026–02.01.2027',
    )
  })

  it('Spanne zaehlt nach Berliner Tag, nicht nach UTC-Tag', () => {
    // 22:30 UTC und 23:30 UTC sind am 12. bzw. 13. in Berlin
    expect(zeigeSpanne('2026-11-12T22:30:00Z', '2026-11-12T23:30:00Z')).toBe('12.–13.11.2026')
  })

  it('alsEingabe liefert Berliner Wandzeit fuer datetime-local', () => {
    expect(alsEingabe('2026-11-12T22:30:00+00:00')).toBe('2026-11-12T23:30')
    expect(alsEingabe('2026-07-01T10:00:00+00:00')).toBe('2026-07-01T12:00')
  })

  it('alsEingabe und ausEingabe sind roundtrip-stabil', () => {
    for (const iso of [
      '2026-11-12T22:30:00+00:00',
      '2026-03-29T01:00:00+00:00',
      '2026-07-01T10:15:00Z',
    ]) {
      const wand = alsEingabe(iso)
      expect(ausEingabe(wand)).toBe(wand)
    }
    // Wandzeit + Berliner Offset ergibt wieder dieselbe Wandzeit (Winter +01:00, Sommer +02:00)
    expect(alsEingabe('2026-11-12T23:30:00+01:00')).toBe('2026-11-12T23:30')
    expect(alsEingabe('2026-07-01T12:00:00+02:00')).toBe('2026-07-01T12:00')
  })

  it('Zeiten ohne Offset werden abgelehnt (sonst zaehlte die Browser-Zeitzone)', () => {
    expect(() => alsEingabe('2026-11-12T23:30')).toThrow()
  })

  it('ausEingabe lehnt Unsinn ab', () => {
    expect(() => ausEingabe('morgen')).toThrow()
    expect(() => ausEingabe('')).toThrow()
  })

  it('ungueltige ISO-Werte werfen statt "Invalid Date" anzuzeigen', () => {
    expect(() => zeigeDatum('kein datum')).toThrow()
  })
})
