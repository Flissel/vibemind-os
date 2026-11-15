import { describe, expect, it } from 'vitest';
import { checkVideoKonzept, readKonzept, type KonzeptDaten } from '@x/shared/dist/konzept.js';
import { VIDEOKONZEPT_VORLAGE, videoKonzeptSeite } from './vorlage.js';

const VIDEO = { thema: 'KI im Alltag', zielgruppe: 'Inhaber', minuten: 4, hook: 'H', kernaussage: 'K', abschluss: 'A', auftrag: null,
  stufen: [{ name: 'Einstieg', szenen: Array.from({ length: 8 }, (_, i) => ({ kurz: `Szene ${i}`, bildart: 'slide' as const })) }] }

describe('videoKonzeptSeite', () => {
  it('Seite trägt den Block unverändert und ist gültig', () => {
    const daten: KonzeptDaten = { art: 'videokonzept', bezuege: ['[[Marke/Marke]]'], video: VIDEO };
    const html = videoKonzeptSeite(daten, 'KI im Alltag');
    expect(readKonzept(html)).toEqual({ daten, fehler: [] });
    expect(checkVideoKonzept(readKonzept(html).daten!.video)).toEqual([]);
    expect(html).not.toMatch(/https?:\/\//);
    expect(html).toContain('rowboat:notiz-oeffnen');
    expect(new TextEncoder().encode(html).length).toBeLessThan(200_000);
  });

  it('Seite mit gesetzter Auftragsnummer ist gültig, zeigt "Übergeben als Auftrag" und warnt nicht darüber', () => {
    const daten: KonzeptDaten = { art: 'videokonzept', bezuege: [], video: { ...VIDEO, auftrag: 'a1b2c3d4' } };
    const html = videoKonzeptSeite(daten, 'KI im Alltag');
    expect(readKonzept(html)).toEqual({ daten, fehler: [] });
    expect(checkVideoKonzept(readKonzept(html).daten!.video)).toEqual([]);
    // Die Zeile hängt nur an einem gesetzten Auftrag und steht nicht in der Liste der Warnungen.
    expect(html).toContain("'Übergeben als Auftrag ' + v.auftrag");
    const hinweise = html.slice(html.indexOf('var hinweise = []'), html.indexOf('if (hinweise.length)'));
    expect(hinweise).not.toMatch(/auftrag/i);
  });

  it('Seite warnt wie der Check: ganze Minuten, Zielgruppe 300, Kernaussage+Abschluss 4000; Auftrag nur bei 8 Hex', () => {
    const html = videoKonzeptSeite({ art: 'videokonzept', bezuege: [], video: VIDEO }, 't');
    expect(html).toContain('ZIELGRUPPE_MAX = 300');
    expect(html).toContain('KERN_ABSCHLUSS_MAX = 4000');
    expect(html).toContain("v.minuten % 1 !== 0) hinweise.push('Die Länge soll eine ganze Zahl von Minuten sein.')");
    expect(html).toContain("v.zielgruppe.length > ZIELGRUPPE_MAX) hinweise.push('Die Zielgruppe soll höchstens ' + ZIELGRUPPE_MAX + ' Zeichen lang sein.')");
    expect(html).toContain("(v.kernaussage.trim() + '\\n\\n' + v.abschluss.trim()).length > KERN_ABSCHLUSS_MAX) hinweise.push('Kernaussage und Abschluss sollen zusammen höchstens ' + KERN_ABSCHLUSS_MAX + ' Zeichen lang sein.')");
    expect(html).toContain("if (typeof v.auftrag === 'string' && /^[0-9a-f]{8}$/.test(v.auftrag)) wurzel.appendChild(el('p', 'leise', 'Übergeben als Auftrag ' + v.auftrag));");
  });

  it('ein </script> in den Daten bricht den Block nicht', () => {
    const html = videoKonzeptSeite({ art: 'videokonzept', bezuege: [], video: { ...VIDEO, hook: 'a</script><b>' } }, 't');
    expect(readKonzept(html).daten?.video?.hook).toBe('a</script><b>');
  });

  it('schreibt <, > und & im Datenblock als \\uXXXX, der Block enthält kein rohes </script', () => {
    const html = videoKonzeptSeite({ art: 'videokonzept', bezuege: [], video: { ...VIDEO, hook: 'a</script><b>&' } }, 't');
    const start = html.indexOf('id="rowboat-konzept">');
    const block = html.slice(start, html.indexOf('</script>', start));
    const bs = String.fromCharCode(92);
    expect(block).toContain(`a${bs}u003c/script${bs}u003e${bs}u003cb${bs}u003e${bs}u0026`);
    expect(block).not.toContain('</script');
    expect(block).not.toContain('<b>');
  });

  it('maskiert den Titel in title und Seite und lässt keine Skript-Einschleusung zu', () => {
    const titel = '</title><script>alert(1)</script>';
    const html = videoKonzeptSeite({ art: 'videokonzept', bezuege: [], video: { ...VIDEO, hook: '</script><script>alert(2)</script>' } }, titel);
    expect(html).not.toContain('<script>alert');
    expect(html).not.toContain('</title><script>');
    expect(html).toContain('<title>&lt;/title&gt;&lt;script&gt;alert(1)&lt;/script&gt;</title>');
    expect(html).toContain('<h1>&lt;/title&gt;');
    expect(readKonzept(html).daten?.video?.hook).toBe('</script><script>alert(2)</script>');
  });

  it('Platzhalter-Text und $-Muster in Titel und Daten bleiben wörtlich', () => {
    const html = videoKonzeptSeite({ art: 'videokonzept', bezuege: [], video: { ...VIDEO, hook: "$& $1 {{TITEL}}" } }, "$& {{DATEN}}");
    expect(readKonzept(html).daten?.video?.hook).toBe('$& $1 {{TITEL}}');
    expect(html).toContain('<title>$&amp; {{DATEN}}</title>');
  });
});

describe('VIDEOKONZEPT_VORLAGE', () => {
  it('enthält einen lesbaren Datenblock und keine Platzhalter mehr', () => {
    expect(VIDEOKONZEPT_VORLAGE).not.toContain('{{');
    const gelesen = readKonzept(VIDEOKONZEPT_VORLAGE);
    expect(gelesen.fehler).toEqual([]);
    expect(gelesen.daten?.art).toBe('videokonzept');
  });

  it('Skript und Stil der Vorlage nutzen nur sichere DOM-Wege und keine Adresse', () => {
    for (const verboten of ['innerHTML', 'insertAdjacentHTML', 'document.write', 'eval(', 'new Function', 'http://', 'https://', 'target=']) {
      expect(VIDEOKONZEPT_VORLAGE, verboten).not.toContain(verboten);
    }
    expect(VIDEOKONZEPT_VORLAGE).toContain("JSON.parse(document.getElementById('rowboat-konzept').textContent)");
    expect(VIDEOKONZEPT_VORLAGE).toContain('prefers-color-scheme: dark');
    expect(VIDEOKONZEPT_VORLAGE).toContain('name="viewport"');
  });

  it('Reihenfolge: title, Datenblock, Stil, Inhalt, Skript', () => {
    const v = VIDEOKONZEPT_VORLAGE;
    const pos = ['<title>', 'id="rowboat-konzept"', '<style>', '<main>', 'document.createElement'].map((s) => v.indexOf(s));
    expect(pos.every((p) => p >= 0)).toBe(true);
    expect([...pos].sort((a, b) => a - b)).toEqual(pos);
  });
});
