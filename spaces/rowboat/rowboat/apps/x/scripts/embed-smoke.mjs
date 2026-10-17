#!/usr/bin/env node
// Rauchprobe fuer den Einbettungszugang (Spec vibemind-os 2026-10-17-rowboat-einbettung):
//   node scripts/embed-smoke.mjs <workdir>
// Liest <workdir>/embed.json und prueft Anmeldung, Aufruf, Ablehnung und app://. Gibt das Token nie aus.
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(new URL('../apps/server/package.json', import.meta.url));
const { WebSocket } = require('ws');

const workdir = process.argv[2];
if (!workdir) {
  console.error('Aufruf: node scripts/embed-smoke.mjs <workdir>');
  process.exit(2);
}
const info = JSON.parse(fs.readFileSync(path.join(workdir, 'embed.json'), 'utf8'));
const base = `127.0.0.1:${info.port}`;
let failed = 0;
const check = (ok, what) => {
  console.log(`${ok ? 'OK  ' : 'FAIL'} ${what}`);
  if (!ok) failed += 1;
};

function session(token) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(`ws://${base}/embed`);
    const inbox = [];
    const waiters = [];
    const closed = new Promise((r) => ws.on('close', (code) => r(code)));
    ws.on('message', (d) => {
      const m = JSON.parse(String(d));
      const w = waiters.shift();
      if (w) w(m); else inbox.push(m);
    });
    ws.on('error', reject);
    ws.on('open', () => {
      ws.send(JSON.stringify({ type: 'hello', v: 1, token }));
      resolve({ ws, closed, next: () => (inbox.length ? Promise.resolve(inbox.shift()) : new Promise((r) => waiters.push(r))) });
    });
  });
}

const bad = await session('falsch');
check((await bad.closed) === 4401, 'falsches Token wird abgewiesen (4401)');

const s = await session(info.token);
const welcome = await s.next();
check(welcome.type === 'welcome' && welcome.invokeChannels.length > 400, `welcome mit ${welcome.invokeChannels?.length} Kanaelen`);
s.ws.send(JSON.stringify({ type: 'invoke', id: 1, channel: 'app:getVersions', args: null }));
const versions = await s.next();
check(versions.ok === true && typeof versions.result?.electron === 'string', 'app:getVersions antwortet');
s.ws.send(JSON.stringify({ type: 'invoke', id: 2, channel: 'browser:getState', args: null }));
const unsupported = await s.next();
check(unsupported.ok === false && unsupported.error?.code === 'embed_unsupported', 'browser:getState ist embed_unsupported');
s.ws.close();

const page = await fetch(`http://${base}/app/-/index.html`, { headers: { authorization: `Bearer ${info.token}` } });
const html = await page.text();
check(page.status === 200 && html.includes('<html'), 'GET /app/-/index.html liefert die Oberflaeche');
check((await fetch(`http://${base}/app/-/index.html`)).status === 401, 'ohne Token 401');
check(info.pid > 0 && typeof info.preloadPath === 'string' && fs.existsSync(info.preloadPath), 'embed.json nennt PID und vorhandenen Preload');
// exitCode statt process.exit(): ein hartes exit, waehrend undici/ws noch Handles schliessen,
// loest unter Windows eine libuv-Assertion aus (UV_HANDLE_CLOSING, Exit 0xC0000409).
process.exitCode = failed ? 1 : 0;
