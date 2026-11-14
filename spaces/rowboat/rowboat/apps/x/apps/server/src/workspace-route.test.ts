import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { Hono } from 'hono';
import { WORKSPACE_HTML_CSP } from '@x/shared/dist/konzept.js';
import { createWorkspaceRoutes } from './workspace-route.js';

describe('workspace route: HTML delivery', () => {
  let root: string;
  let app: Hono;
  let symlinkOk = false;

  beforeAll(() => {
    root = fs.mkdtempSync(path.join(os.tmpdir(), 'ws-route-'));
    fs.mkdirSync(path.join(root, 'knowledge'));
    fs.writeFileSync(path.join(root, 'knowledge', 'a.html'), '<p>hi</p>');
    fs.writeFileSync(path.join(root, 'knowledge', 'b.md'), '# b');
    fs.writeFileSync(path.join(root, 'knowledge', 'c.HTM'), '<p>c</p>');
    fs.writeFileSync(path.join(root, 'knowledge', 'd.xhtml'), '<p>d</p>');
    fs.mkdirSync(path.join(root, 'bg-tasks', 'x'), { recursive: true });
    fs.writeFileSync(path.join(root, 'bg-tasks', 'x', 'index.html'), '<img src="bild.png"><a href="https://example.com">x</a>');
    fs.writeFileSync(path.join(root, 'bg-tasks', 'x', 'bild.png'), 'png');
    try {
      fs.symlinkSync(path.join(root, 'knowledge', 'a.html'), path.join(root, 'knowledge', 'x.txt'));
      symlinkOk = true;
    } catch {
      symlinkOk = false; // Windows ohne Symlink-Recht
    }
    app = createWorkspaceRoutes((rel) => path.join(root, rel));
  });

  afterAll(() => {
    fs.rmSync(root, { recursive: true, force: true });
  });

  it('liefert HTML mit Typ und Kein-Netz-Regel', async () => {
    const res = await app.request('/workspace/knowledge/a.html');
    expect(res.status).toBe(200);
    expect(res.headers.get('content-type')).toBe('text/html; charset=utf-8');
    expect(res.headers.get('content-security-policy')).toBe(WORKSPACE_HTML_CSP);
    expect(res.headers.get('x-content-type-options')).toBe('nosniff');
    expect(res.headers.get('x-dns-prefetch-control')).toBe('off');
    expect(await res.text()).toBe('<p>hi</p>');
    const md = await app.request('/workspace/knowledge/b.md');
    expect(md.headers.get('content-type')).toBe('text/markdown; charset=utf-8');
    expect(md.headers.get('content-security-policy')).toBeNull();
  });

  it('wendet die Regel auch auf .HTM, .xhtml und Endpunkt-Namen an', async () => {
    for (const p of ['c.HTM', 'd.xhtml', 'a.html.', 'a.html%20']) {
      const res = await app.request(`/workspace/knowledge/${p}`);
      if (res.status !== 200) continue; // Namen mit Endpunkt loest nicht jedes Dateisystem auf
      expect(res.headers.get('content-security-policy'), p).toBe(WORKSPACE_HTML_CSP);
    }
    for (const p of ['c.HTM', 'd.xhtml']) {
      const res = await app.request(`/workspace/knowledge/${p}`);
      expect(res.status, p).toBe(200);
      expect(res.headers.get('content-type'), p).toBe('text/html; charset=utf-8');
      expect(res.headers.get('x-content-type-options'), p).toBe('nosniff');
    }
  });

  it('wendet die Regel auf eine Verknuepfung x.txt -> a.html an', async (ctx) => {
    if (!symlinkOk) ctx.skip();
    const res = await app.request('/workspace/knowledge/x.txt');
    expect(res.status).toBe(200);
    expect(res.headers.get('content-security-policy')).toBe(WORKSPACE_HTML_CSP);
    expect(res.headers.get('content-type')).toBe('text/html; charset=utf-8');
  });

  it('wendet die Regel auf eine Verknuepfung an, auch wenn der Arbeitsordner # oder % im Namen hat', async (ctx) => {
    const sonder = fs.mkdtempSync(path.join(os.tmpdir(), 'ws#50%-'));
    try {
      fs.mkdirSync(path.join(sonder, 'knowledge'));
      fs.writeFileSync(path.join(sonder, 'knowledge', 'y.html'), '<p>y</p>');
      try {
        fs.symlinkSync(path.join(sonder, 'knowledge', 'y.html'), path.join(sonder, 'knowledge', 'x.txt'));
      } catch {
        ctx.skip(); // Windows ohne Symlink-Recht
      }
      const sApp = createWorkspaceRoutes((rel) => path.join(sonder, rel));
      const res = await sApp.request('/workspace/knowledge/x.txt');
      expect(res.status).toBe(200);
      expect(res.headers.get('content-security-policy')).toBe(WORKSPACE_HTML_CSP);
    } finally {
      fs.rmSync(sonder, { recursive: true, force: true });
    }
  });

  it('behandelt eine Textdatei im Arbeitsordner page.html#d nicht als HTML', async () => {
    const eltern = fs.mkdtempSync(path.join(os.tmpdir(), 'ws-'));
    const sonder = path.join(eltern, 'page.html#d');
    try {
      fs.mkdirSync(sonder);
      fs.writeFileSync(path.join(sonder, 'notes.txt'), 'hi');
    } catch {
      fs.rmSync(eltern, { recursive: true, force: true });
      return; // Dateisystem erlaubt den Namen nicht
    }
    try {
      const sApp = createWorkspaceRoutes((rel) => path.join(sonder, rel));
      const res = await sApp.request('/workspace/notes.txt');
      expect(res.status).toBe(200);
      expect(res.headers.get('content-security-policy')).toBeNull();
      expect(res.headers.get('content-type')).toBe('text/plain; charset=utf-8');
    } finally {
      fs.rmSync(eltern, { recursive: true, force: true });
    }
  });

  it('liefert Textdateien in Ordnern mit html-artigem Namen ohne HTML-Kopf, echtes HTML auch kodiert', async () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ws-'));
    const angelegt: string[] = [];
    try {
      for (const d of ['page.html#d', 'a.html?x']) {
        try {
          fs.mkdirSync(path.join(dir, d));
          fs.writeFileSync(path.join(dir, d, 'notes.txt'), 'hi');
          angelegt.push(d);
        } catch {
          // Dateisystem erlaubt den Namen nicht (z. B. ? unter Windows)
        }
      }
      const sApp = createWorkspaceRoutes((rel) => path.join(dir, rel));
      for (const d of angelegt) {
        const res = await sApp.request(`/workspace/${encodeURIComponent(d)}/notes.txt`);
        expect(res.status, d).toBe(200);
        expect(res.headers.get('content-security-policy'), d).toBeNull();
        expect(res.headers.get('content-type'), d).toBe('text/plain; charset=utf-8');
      }
    } finally {
      fs.rmSync(dir, { recursive: true, force: true });
    }
    for (const p of ['/workspace/knowledge/a.html', '/workspace/knowledge/a%2Ehtml']) {
      const res = await app.request(p);
      expect(res.status, p).toBe(200);
      expect(res.headers.get('content-security-policy'), p).toBe(WORKSPACE_HTML_CSP);
      expect(res.headers.get('content-type'), p).toBe('text/html; charset=utf-8');
    }
  });

  it('liefert Hintergrund-Berichte ausserhalb von knowledge/ wie vor der Konzept-Flaeche (R38)', async () => {
    const res = await app.request('/workspace/bg-tasks/x/index.html');
    expect(res.status).toBe(200);
    expect(res.headers.get('content-security-policy')).toBeNull();
    expect(res.headers.get('x-dns-prefetch-control')).toBeNull();
    expect(res.headers.get('x-content-type-options')).toBeNull();
    // Typ wie vor der Konzept-Flaeche (die Tabelle kennt .html nicht)
    expect(res.headers.get('content-type')).toBe('application/octet-stream');
    const bild = await app.request('/workspace/bg-tasks/x/bild.png');
    expect(bild.status).toBe(200);
    expect(bild.headers.get('content-type')).toBe('image/png');
    expect(bild.headers.get('content-security-policy')).toBeNull();
  });

  it('wendet die Regel an, wenn eine Verknuepfung ausserhalb auf Wissens-HTML zeigt', async (ctx) => {
    const ziel = path.join(root, 'bg-tasks', 'r.html');
    try {
      fs.symlinkSync(path.join(root, 'knowledge', 'a.html'), ziel);
    } catch {
      ctx.skip(); // Windows ohne Symlink-Recht
    }
    try {
      const res = await app.request('/workspace/bg-tasks/r.html');
      expect(res.status).toBe(200);
      expect(res.headers.get('content-security-policy')).toBe(WORKSPACE_HTML_CSP);
      expect(res.headers.get('content-type')).toBe('text/html; charset=utf-8');
    } finally {
      fs.rmSync(ziel, { force: true });
    }
  });
});
