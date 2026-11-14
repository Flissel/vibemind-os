import fs from 'node:fs/promises';
import nodeFs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import git from 'isomorphic-git';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

let tmpDir: string;
let workDir: string;
let k: string;

beforeEach(async () => {
  tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), 'rowboat-vh-test-'));
  workDir = path.join(tmpDir, 'workspace');
  k = path.join(workDir, 'knowledge');
  process.env.ROWBOAT_WORKDIR = workDir;
  vi.resetModules();
});
afterEach(async () => {
  delete process.env.ROWBOAT_WORKDIR;
  vi.resetModules();
  await fs.rm(tmpDir, { recursive: true, force: true });
});

/**
 * Frisches Repo VOR dem Import anlegen: config.ts startet beim Import selbst initRepo() (nicht abwartbar);
 * mit vorhandenem .git kehrt das sofort zurück, und alle Commits laufen deterministisch über commitAll().
 */
async function importWithRepo() {
  await fs.mkdir(k, { recursive: true });
  await git.init({ fs: nodeFs, dir: k });
  return import('./version_history.js');
}

describe('version history', () => {
  it('nimmt HTML-Konzepte in den Versionsverlauf (auch .htm, auch unter Kanal), andere Dateien nicht', async () => {
    const vh = await importWithRepo();
    await fs.mkdir(path.join(k, 'Konzepte'), { recursive: true });
    await fs.mkdir(path.join(k, 'Kanal'), { recursive: true });
    await fs.writeFile(path.join(k, 'Konzepte', 'k.html'), '<p>1</p>');
    await fs.writeFile(path.join(k, 'Konzepte', 'm.htm'), '<p>m</p>');
    await fs.writeFile(path.join(k, 'Kanal', 'q.html'), '<p>q</p>');
    await fs.writeFile(path.join(k, 'n.md'), '# n');
    await fs.writeFile(path.join(k, 'x.txt'), 'x');
    await vh.commitAll('t1', 'a');
    await fs.writeFile(path.join(k, 'Konzepte', 'k.html'), '<p>zwei, laenger</p>');
    await vh.commitAll('t2', 'a');
    expect(await vh.getFileHistory('Konzepte/k.html')).toHaveLength(2);
    expect(await vh.getFileHistory('Konzepte/m.htm')).toHaveLength(1);
    expect(await vh.getFileHistory('Kanal/q.html')).toHaveLength(1);
    expect(await vh.getFileHistory('n.md')).toHaveLength(1);
    expect(await vh.getFileHistory('x.txt')).toHaveLength(0);
  });

  it('HTML über 5 MB wird nicht aufgenommen; wächst eine versionierte Datei darüber, bleibt die letzte Fassung', async () => {
    const vh = await importWithRepo();
    await fs.writeFile(path.join(k, 'gross.html'), `<p>${'x'.repeat(5 * 1024 * 1024)}</p>`);
    await fs.writeFile(path.join(k, 'wachst.html'), '<p>klein</p>');
    await vh.commitAll('t1', 'a');
    expect(await vh.getFileHistory('gross.html')).toHaveLength(0);
    expect(await vh.getFileHistory('wachst.html')).toHaveLength(1);
    await fs.writeFile(path.join(k, 'wachst.html'), `<p>${'y'.repeat(5 * 1024 * 1024)}</p>`);
    await vh.commitAll('t2', 'a');
    expect(await vh.getFileHistory('wachst.html')).toHaveLength(1);
  });

  it('restoreFile bringt eine frühere Fassung einer HTML-Datei zurück', async () => {
    const vh = await importWithRepo();
    await fs.writeFile(path.join(k, 'k.html'), '<p>eins</p>');
    await vh.commitAll('t1', 'a');
    await fs.writeFile(path.join(k, 'k.html'), '<p>zwei, laenger</p>');
    await vh.commitAll('t2', 'a');
    const history = await vh.getFileHistory('k.html');
    expect(history).toHaveLength(2);
    await vh.restoreFile('k.html', history[1]!.oid);
    expect(await fs.readFile(path.join(k, 'k.html'), 'utf8')).toBe('<p>eins</p>');
    expect(await vh.getFileHistory('k.html')).toHaveLength(3);
  });

  it('erster Snapshot (initRepo) enthält vorhandene HTML-Dateien', async () => {
    // Hier startet config.ts den Snapshot beim Import selbst; es gibt kein Promise zum Abwarten,
    // darum wird (begrenzt) gewartet, bis der Verlauf der Notiz erscheint. Das Ergebnis hängt nicht von der Zeit ab.
    await fs.mkdir(k, { recursive: true });
    await fs.writeFile(path.join(k, 'n.md'), '# n');
    await fs.writeFile(path.join(k, 'neu.html'), '<p>neu</p>');
    const vh = await import('./version_history.js');
    for (let i = 0; i < 200 && (await vh.getFileHistory('neu.html')).length === 0; i++) {
      await new Promise((r) => setTimeout(r, 50));
    }
    const history = await vh.getFileHistory('neu.html');
    expect(history).toHaveLength(1);
    expect(await vh.getFileAtCommit('neu.html', history[0]!.oid)).toBe('<p>neu</p>');
  });
});
