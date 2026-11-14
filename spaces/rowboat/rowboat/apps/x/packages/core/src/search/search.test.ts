import fs from 'fs';
import os from 'os';
import path from 'path';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { KONZEPT_LESE_MAX } from '@x/shared/dist/konzept.js';

let workDir: string;
let search: typeof import('./search.js').search;

function put(rel: string, content: string | Buffer): void {
  const full = path.join(workDir, 'knowledge', rel);
  fs.mkdirSync(path.dirname(full), { recursive: true });
  fs.writeFileSync(full, content);
}

beforeAll(async () => {
  workDir = fs.mkdtempSync(path.join(os.tmpdir(), 'search-html-'));
  process.env.ROWBOAT_WORKDIR = workDir;
  put(
    'Konzepte/k.html',
    '<title>Sommer</title><script>var zebrastreifen=1</script><style>.kaenguru{}</style>'
      + '<!-- igelwort --><p>Ein <b>Pinguinwort</b> &amp; mehr Text</p>',
  );
  put('Konzepte/ohne-titel.HTM', '<p>Walrosswort hier</p>');
  put('Konzepte/kaputt.html', Buffer.from([0xff, 0xfe, 0x00, 0x80, 0xc3, 0x28]));
  put('Konzepte/gross.html', '<p>x</p>' + ' '.repeat(2_000_000) + '<p>Hinterwort</p>');
  put('Konzepte/leer.html', '');
  put('Konzepte/knapp.html', '<p>' + ' '.repeat(KONZEPT_LESE_MAX - 30) + 'Randwort</p>');
  ({ search } = await import('./search.js'));
});

afterAll(() => {
  fs.rmSync(workDir, { recursive: true, force: true });
});

describe('search in HTML-Dateien', () => {
  it('findet sichtbaren Text in HTML, nicht Skriptinhalt', async () => {
    const hit = await search('Pinguinwort', 20, ['knowledge']);
    expect(hit.results.map((r) => r.title)).toContain('Sommer');
    expect(hit.results[0]).toMatchObject({ type: 'knowledge', path: path.join('knowledge', 'Konzepte', 'k.html') });
    expect((await search('zebrastreifen', 20, ['knowledge'])).results).toHaveLength(0);
    expect((await search('kaenguru', 20, ['knowledge'])).results).toHaveLength(0);
    expect((await search('igelwort', 20, ['knowledge'])).results).toHaveLength(0);
    expect((await search('k.html', 20, ['knowledge'])).results).toHaveLength(1);
  });

  it('Vorschau ist reiner, dekodierter Text rund um den Treffer; Suche ohne Beachtung der Gross-/Kleinschreibung', async () => {
    const { results } = await search('PINGUINWORT', 20, ['knowledge']);
    expect(results).toHaveLength(1);
    expect(results[0].preview).toContain('Pinguinwort & mehr Text');
    expect(results[0].preview).not.toMatch(/[<>]|&amp;/);
    expect(results[0].preview.length).toBeLessThanOrEqual(150);
  });

  it('ohne Titel gilt der Dateiname mit Endung; .HTM wird gefunden', async () => {
    const { results } = await search('walrosswort', 20, ['knowledge']);
    expect(results.map((r) => r.title)).toEqual(['ohne-titel.HTM']);
  });

  it('eine nicht lesbare Datei bricht die Suche nicht ab', async () => {
    expect((await search('Pinguinwort', 20, ['knowledge'])).results).toHaveLength(1);
    expect((await search('kaputt', 20, ['knowledge'])).results).toHaveLength(1); // Dateiname
  });

  it('liest je Datei hoechstens KONZEPT_LESE_MAX Zeichen', async () => {
    expect((await search('Hinterwort', 20, ['knowledge'])).results).toHaveLength(0);
  });

  it('liest kleine und leere Dateien ganz (Puffer nach Dateigroesse)', async () => {
    expect((await search('Randwort', 20, ['knowledge'])).results.map((r) => r.title)).toEqual(['knapp.html']);
    expect((await search('leer', 20, ['knowledge'])).results.map((r) => r.title)).toEqual(['leer.html']); // Dateiname
    const { results } = await search('walrosswort', 20, ['knowledge']);
    expect(results[0].preview).toBe('Walrosswort hier');
  });

  it('HTML-Treffer teilen sich das Limit', async () => {
    expect((await search('html', 1, ['knowledge'])).results).toHaveLength(1);
  });
});
