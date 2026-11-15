import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { checkVideoKonzept, readKonzept } from './konzept.js'

/**
 * Gemeinsame Prüffälle für `readKonzept` + `checkVideoKonzept` (Fork) und den Python-Nachbau im MCP-Dienst
 * `rowboat-auftraege`. Die Datei ist hier AUTORITATIV: die erwarteten Werte sind die eingefrorenen Ergebnisse der echten
 * Funktionen; dieser Test hält sie fest, die Python-Seite wird daran gemessen. Die Bedeutung aller Felder steht im
 * Eintrag `_hinweis` der Datei selbst.
 *
 * Fall: `{ name, html, gueltig, video?, fehler_enthaelt? }`; `video` nur bei gueltig, `fehler_enthaelt` nur bei
 * ungueltig und dort immer (`null` = kein Fehlersatz, nämlich kein Datenblock gefunden).
 */
type Fall = { name: string; html: string; gueltig: boolean; fehler_enthaelt?: string | null; video?: unknown }
type Datei = { _hinweis: string; faelle: Fall[] }

const DATEI = fileURLToPath(new URL('./__fixtures__/konzept_vektoren.json', import.meta.url))
const datei = JSON.parse(readFileSync(DATEI, 'utf8')) as Datei
const faelle = datei.faelle

describe('konzept_vektoren.json', () => {
  it('hat einen Hinweis, Fälle, eindeutige Namen und ein Namensschema, das zur Erwartung passt', () => {
    expect(Object.keys(datei).sort()).toEqual(['_hinweis', 'faelle'])
    for (const wort of ['fehler_enthaelt', 'UTF-16', 'trim', 'ERSTEN']) expect(datei._hinweis).toContain(wort)
    expect(faelle.length).toBeGreaterThanOrEqual(200)
    expect(new Set(faelle.map((f) => f.name)).size).toBe(faelle.length)
    for (const f of faelle) {
      expect(f.name.startsWith(f.gueltig ? 'gueltig_' : 'ungueltig_'), f.name).toBe(true)
      expect(Object.keys(f).every((k) => ['name', 'html', 'gueltig', 'fehler_enthaelt', 'video'].includes(k)), f.name).toBe(true)
      // gültig: video, kein fehler_enthaelt; ungültig: fehler_enthaelt immer da (String oder null), kein video
      expect('video' in f, f.name).toBe(f.gueltig)
      expect('fehler_enthaelt' in f, f.name).toBe(!f.gueltig)
    }
  })

  it('deckt die Grenzen der Aufgabe ab', () => {
    const namen = faelle.map((f) => f.name)
    for (const muss of [
      'gueltig_8_szenen', 'gueltig_15_szenen', 'ungueltig_7_szenen', 'ungueltig_16_szenen', 'ungueltig_minuten_6',
      'ungueltig_9_stufen_9_szenen', 'ungueltig_kurz_301_zeichen', 'gueltig_kurz_300_zeichen', 'ungueltig_bildart_unbekannt',
      'ungueltig_block_20001_zeichen', 'gueltig_block_genau_20000_zeichen', 'gueltig_zwei_bloecke_der_erste_gilt',
      'ungueltig_kaputtes_json_abgeschnitten', 'ungueltig_art_konzept_ohne_video', 'gueltig_auftrag_8_hex', 'ungueltig_auftrag_xyz',
      'ungueltig_minuten_4_5_nicht_ganzzahlig', 'gueltig_zielgruppe_300_zeichen', 'ungueltig_zielgruppe_301_zeichen',
      'gueltig_kernaussage_abschluss_1999_plus_1999_gleich_4000', 'ungueltig_kernaussage_abschluss_1999_plus_2000_gleich_4001',
      'gueltig_script_nicht_geschlossen_koerper_bis_dateiende', 'ungueltig_kaputtes_json_nan_literal',
    ]) expect(namen, muss).toContain(muss)
  })

  for (const f of faelle) {
    it(f.name, () => {
      const r = readKonzept(f.html)
      const fehler = r.daten === null ? r.fehler : checkVideoKonzept(r.daten.video)
      expect(r.daten !== null && fehler.length === 0).toBe(f.gueltig)
      if (f.gueltig) {
        expect(r.daten?.video).toEqual(f.video)
      } else if (typeof f.fehler_enthaelt === 'string') {
        expect(fehler[0]).toContain(f.fehler_enthaelt)
      } else {
        expect(f.fehler_enthaelt).toBeNull()
        expect(r.daten).toBeNull()
        expect(r.fehler).toEqual([])
      }
    })
  }
})
