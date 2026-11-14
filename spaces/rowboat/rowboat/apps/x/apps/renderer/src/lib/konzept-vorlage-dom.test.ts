// Fuehrt die Videokonzept-Vorlage (Kern-Paket) in jsdom aus: das Skript der Seite muss
// aus dem Datenblock die Szenenkarten zeichnen und Verweise als postMessage senden.
// jsdom kommt aus der vorhandenen Renderer-Testumgebung (vite.config.ts), nichts Neues.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { videoKonzeptSeite } from '../../../../packages/core/src/runtime/assembly/skills/konzept/vorlage'

const VIDEO = { thema: 'KI im Alltag', zielgruppe: 'Inhaber', minuten: 4, hook: 'H', kernaussage: 'K', abschluss: 'A', auftrag: null,
  stufen: [{ name: 'Einstieg', szenen: Array.from({ length: 8 }, (_, i) => ({ kurz: `Szene ${i}`, bildart: 'slide' as const })) }] }

function zeige(html: string): void {
  const doc = new DOMParser().parseFromString(html, 'text/html')
  document.head.innerHTML = doc.head.innerHTML
  document.body.innerHTML = doc.body.innerHTML
  const skript = Array.from(doc.querySelectorAll('script')).find((s) => !s.id)
  // Wie der Rahmen: das Seitenskript laeuft im Dokument; parent ist hier das eigene Fenster.
  new Function(skript!.textContent ?? '')()
}

/** Seite mit beliebigem (auch falsch geformtem) video-Wert, ohne Typpruefung. */
function seiteMit(video: unknown, bezuege: string[] = []): string {
  return videoKonzeptSeite({ art: 'videokonzept', bezuege, video } as unknown as Parameters<typeof videoKonzeptSeite>[0], 't')
}

afterEach(() => { document.head.innerHTML = ''; document.body.innerHTML = '' })

describe('Videokonzept-Vorlage im Dokument', () => {
  it('zeichnet 8 Szenenkarten, Zusammenfassung mit Spanne und sendet Verweise per postMessage', () => {
    const senden = vi.spyOn(window.parent, 'postMessage').mockImplementation(() => undefined)
    zeige(seiteMit(VIDEO, ['[[Marke/Marke]]', '[[Konzepte/x.html|Seite]]', '[[Notizen/a#Teil]]', '[[Notizen/b|Name]]']))
    expect(document.querySelectorAll('.szene')).toHaveLength(8)
    expect(document.querySelectorAll('details')).toHaveLength(1)
    expect(document.body.textContent).toContain('Geschätzte Länge 2:00 bis 6:00 (Ziel 4:00)')
    expect(document.querySelector('.warnung')).toBeNull()
    const knoepfe = Array.from(document.querySelectorAll('button'))
    expect(knoepfe.map((b) => b.textContent)).toEqual(['Marke/Marke', 'Seite', 'Notizen/a', 'Name'])
    knoepfe.forEach((b) => b.click())
    const pfade = senden.mock.calls.map((c) => (c[0] as { pfad: string }).pfad)
    expect(pfade).toEqual(['knowledge/Marke/Marke.md', 'knowledge/Konzepte/x.html', 'knowledge/Notizen/a.md', 'knowledge/Notizen/b.md'])
    expect(senden.mock.calls.every((c) => (c[0] as { type: string }).type === 'rowboat:notiz-oeffnen' && (c[1] as unknown) === '*')).toBe(true)
    senden.mockRestore()
  })

  it('Ziele mit .. oder führendem / ergeben keinen Knopf, nur Text', () => {
    zeige(seiteMit(VIDEO, ['[[../geheim]]', '[[/etc/x]]', '[[ok/ok]]']))
    expect(Array.from(document.querySelectorAll('button')).map((b) => b.textContent)).toEqual(['ok/ok'])
    expect(Array.from(document.querySelectorAll('.nur-text')).map((b) => b.textContent)).toEqual(['../geheim', '/etc/x'])
  })

  it('warnt außerhalb der Grenzen und zeigt den Auftrag, Daten landen nur als Text', () => {
    const video = { ...VIDEO, minuten: 9, hook: '<img src=x onerror=alert(1)>', auftrag: 'A-7',
      stufen: [{ name: 'S', szenen: [{ kurz: '<b>fett</b>', bildart: 'clip', notiz: 'n' }] }] }
    zeige(seiteMit(video))
    expect(document.querySelector('.warnung')?.textContent).toContain('8 bis 15 Szenen')
    expect(document.querySelector('.warnung')?.textContent).toContain('3 und 5 Minuten')
    expect(document.body.textContent).toContain('Übergeben als Auftrag A-7')
    expect(document.querySelector('blockquote img')).toBeNull()
    expect(document.querySelector('.szene b')).toBeNull()
    expect(document.querySelector('.badge')?.textContent).toBe('Clip')
  })

  it('warnt vor der Spanne nur, wenn das Ziel außerhalb von Szenen × 15 s bis × 45 s liegt', () => {
    // 8 Szenen: 2:00 bis 6:00, Ziel 4 Minuten liegt drin, keine Warnung.
    zeige(seiteMit(VIDEO))
    expect(document.getElementById('inhalt')?.textContent).not.toContain('möglich')
    // 15 Szenen: 3:45 bis 11:15, Ziel 3 Minuten liegt darunter.
    const viele = { ...VIDEO, minuten: 3, stufen: [{ name: 'E', szenen: Array.from({ length: 15 }, (_, i) => ({ kurz: `S${i}`, bildart: 'slide' })) }] }
    zeige(seiteMit(viele))
    expect(document.querySelector('.warnung')?.textContent).toContain('Mit 15 Szenen sind etwa 3:45 bis 11:15 möglich; Ziel sind 3 Minuten.')
  })

  it('zeigt bei fehlendem oder unlesbarem Block eine lesbare Meldung', () => {
    zeige(seiteMit(undefined))
    expect(document.querySelector('.warnung')?.textContent).toContain('fehlt oder ist nicht lesbar')
  })

  it('eine Stufe ohne Namen bekommt die neutrale Bezeichnung Stufe N', () => {
    zeige(seiteMit({ ...VIDEO, stufen: [{ szenen: VIDEO.stufen[0].szenen }] }))
    expect(document.querySelector('summary')?.textContent).toBe('1. Stufe 1 (8 Szenen)')
  })

  it('bleibt bei falsch geformten Daten nicht halb gezeichnet stehen', () => {
    zeige(seiteMit({ ...VIDEO, stufen: 'keine Liste' }))
    expect(document.querySelector('.warnung')?.textContent).toContain('konnte nicht gezeichnet werden')
    expect(document.querySelector('.warnung')?.textContent).toContain('"stufen" muss eine Liste sein')
    expect(document.querySelectorAll('.szene')).toHaveLength(0)
    expect(document.querySelector('h2')).toBeNull()

    zeige(seiteMit({ ...VIDEO, stufen: [{ name: 'X', szenen: 'nein' }] }))
    expect(document.querySelector('.warnung')?.textContent).toContain('konnte nicht gezeichnet werden')
  })
})
