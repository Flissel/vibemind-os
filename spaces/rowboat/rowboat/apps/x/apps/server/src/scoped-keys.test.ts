import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { WebSocket } from 'ws';
import {
  SCOPED_KEYS_FILE,
  addScopedKey,
  findScopedKey,
  loadScopedKeys,
  removeScopedKey,
} from './auth.js';
import type { RpcHandlers } from './channels.js';
import { RPC_CHANNELS } from './channels.js';
import { runScopedKeyCli } from './scoped-key-cli.js';
import { createRowboatServer, type RowboatServer } from './server.js';
import { WS_CLOSE_UNAUTHORIZED } from './ws-hub.js';

function stubHandlers(overrides: Partial<RpcHandlers>): RpcHandlers {
  const base = Object.fromEntries(
    RPC_CHANNELS.map((ch) => [
      ch,
      () => {
        throw new Error(`no stub for ${ch}`);
      },
    ]),
  );
  return { ...base, ...overrides } as RpcHandlers;
}

async function tmpWorkDir(): Promise<string> {
  return fs.mkdtemp(path.join(os.tmpdir(), 'rowboat-scoped-'));
}

describe('scoped keys file', () => {
  it('stores only a hash, never the token', async () => {
    const dir = await tmpWorkDir();
    const token = await addScopedKey(dir, 'freigabe-bote', ['todo:get']);
    const raw = await fs.readFile(path.join(dir, SCOPED_KEYS_FILE), 'utf8');
    expect(raw).not.toContain(token);
    const keys = await loadScopedKeys(dir);
    expect(keys).toHaveLength(1);
    expect(keys[0]).toMatchObject({ name: 'freigabe-bote', channels: ['todo:get'] });
    expect(keys[0].sha256).toMatch(/^[0-9a-f]{64}$/);
    if (process.platform !== 'win32') {
      const st = await fs.stat(path.join(dir, SCOPED_KEYS_FILE));
      expect(st.mode & 0o777).toBe(0o600);
    }
  });

  it('finds a key by token and nothing else', async () => {
    const dir = await tmpWorkDir();
    const token = await addScopedKey(dir, 'a', ['todo:get']);
    const keys = await loadScopedKeys(dir);
    expect(findScopedKey(token, keys)?.name).toBe('a');
    expect(findScopedKey(token + 'x', keys)).toBeNull();
    expect(findScopedKey('', keys)).toBeNull();
  });

  it('re-adding a name replaces the old key', async () => {
    const dir = await tmpWorkDir();
    const alt = await addScopedKey(dir, 'a', ['todo:get']);
    const neu = await addScopedKey(dir, 'a', ['todo:get', 'todo:setChecked']);
    const keys = await loadScopedKeys(dir);
    expect(keys).toHaveLength(1);
    expect(findScopedKey(alt, keys)).toBeNull();
    expect(findScopedKey(neu, keys)?.channels).toEqual(['todo:get', 'todo:setChecked']);
  });

  it('rejects unknown channels and bad names', async () => {
    const dir = await tmpWorkDir();
    await expect(addScopedKey(dir, 'a', ['todo:nope'])).rejects.toThrow(/unknown channel/);
    await expect(addScopedKey(dir, 'a', [])).rejects.toThrow(/channel/);
    await expect(addScopedKey(dir, 'Böse Name', ['todo:get'])).rejects.toThrow(/name/);
  });

  it('a corrupt or missing file means no scoped keys', async () => {
    const dir = await tmpWorkDir();
    expect(await loadScopedKeys(dir)).toEqual([]);
    await fs.writeFile(path.join(dir, SCOPED_KEYS_FILE), '{kaputt');
    expect(await loadScopedKeys(dir)).toEqual([]);
    await fs.writeFile(
      path.join(dir, SCOPED_KEYS_FILE),
      JSON.stringify({ keys: [{ name: 'x', sha256: 'nothex', channels: ['todo:get'] }] }),
    );
    expect(await loadScopedKeys(dir)).toEqual([]);
  });

  it('remove drops the entry', async () => {
    const dir = await tmpWorkDir();
    await addScopedKey(dir, 'a', ['todo:get']);
    expect(await removeScopedKey(dir, 'a')).toBe(true);
    expect(await removeScopedKey(dir, 'a')).toBe(false);
    expect(await loadScopedKeys(dir)).toEqual([]);
  });
});

describe('scoped-key cli', () => {
  it('add prints only the token, list never prints it', async () => {
    const dir = await tmpWorkDir();
    const out: string[] = [];
    const err: string[] = [];
    const io = { out: (s: string) => out.push(s), err: (s: string) => err.push(s) };
    expect(await runScopedKeyCli(['add', 'bote', 'todo:get,todo:setChecked'], dir, io)).toBe(0);
    expect(out).toHaveLength(1);
    const token = out[0].trim();
    expect(findScopedKey(token, await loadScopedKeys(dir))?.name).toBe('bote');
    out.length = 0;
    expect(await runScopedKeyCli(['list'], dir, io)).toBe(0);
    expect(out.join('\n')).toContain('bote');
    expect(out.join('\n')).toContain('todo:setChecked');
    expect(out.join('\n')).not.toContain(token);
    expect(await runScopedKeyCli(['remove', 'bote'], dir, io)).toBe(0);
    expect(await runScopedKeyCli(['add', 'bote', 'todo:nope'], dir, io)).toBe(1);
    expect(await runScopedKeyCli(['frobnicate'], dir, io)).toBe(2);
  });
});

describe('server with a scoped key', () => {
  let workDir: string;
  let server: RowboatServer;
  let base: string;
  let token: string;

  beforeAll(async () => {
    workDir = await tmpWorkDir();
    await fs.mkdir(path.join(workDir, 'notes'), { recursive: true });
    await fs.writeFile(path.join(workDir, 'notes', 'hello.md'), '# hi\n');
    token = await addScopedKey(workDir, 'freigabe-bote', ['sessions:list']);
    server = await createRowboatServer({
      workDir,
      handlers: stubHandlers({
        'sessions:list': async () => ({ sessions: [] }),
        'todo:get': async () => {
          throw new Error('must not be reached with the scoped key');
        },
      }),
      events: {
        subscribeTurnEvents: () => () => {},
        subscribeSessionEvents: () => () => {},
      },
      resolveWorkspacePath: (rel) => path.join(workDir, rel),
      serverVersion: 'test',
      port: 0,
    });
    base = `http://127.0.0.1:${server.port}`;
  });

  afterAll(async () => {
    await server.close();
    await fs.rm(workDir, { recursive: true, force: true });
  });

  const mit = (t: string, init?: RequestInit): RequestInit => ({
    method: 'POST',
    body: '{}',
    ...init,
    headers: { authorization: `Bearer ${t}` },
  });

  it('may call its own channel', async () => {
    const res = await fetch(`${base}/rpc/sessions:list`, mit(token));
    expect(res.status).toBe(200);
  });

  it('percent-encoded channel names are judged decoded', async () => {
    const res = await fetch(`${base}/rpc/sessions%3Alist`, mit(token));
    expect(res.status).toBe(200);
  });

  it('is refused on any other channel', async () => {
    const res = await fetch(`${base}/rpc/todo:get`, mit(token));
    expect(res.status).toBe(403);
    const unknown = await fetch(`${base}/rpc/does:not-exist`, mit(token));
    expect(unknown.status).toBe(403);
  });

  it('is refused outside /rpc', async () => {
    const ws = await fetch(`${base}/workspace/notes/hello.md`, { headers: { authorization: `Bearer ${token}` } });
    expect(ws.status).toBe(403);
    const get = await fetch(`${base}/rpc/sessions:list`, { headers: { authorization: `Bearer ${token}` } });
    expect(get.status).toBe(403);
    const sneaky = await fetch(`${base}/rpc/sessions:list/../todo:get`, mit(token));
    expect(sneaky.status).toBe(403);
  });

  it('cannot open the websocket', async () => {
    const socket = new WebSocket(`ws://127.0.0.1:${server.port}/events`, {
      headers: { authorization: `Bearer ${token}` },
    });
    const code = await new Promise<number>((resolve) => {
      socket.on('close', (c) => resolve(c));
      socket.on('error', () => resolve(-1));
    });
    expect([WS_CLOSE_UNAUTHORIZED, -1]).toContain(code);
  });

  it('the master key keeps full access', async () => {
    const res = await fetch(`${base}/workspace/notes/hello.md`, {
      headers: { authorization: `Bearer ${server.key}` },
    });
    expect(res.status).toBe(200);
  });

  it('a revoked key is refused without a restart', async () => {
    const extra = await addScopedKey(workDir, 'kurzlebig', ['sessions:list']);
    expect((await fetch(`${base}/rpc/sessions:list`, mit(extra))).status).toBe(200);
    await removeScopedKey(workDir, 'kurzlebig');
    expect((await fetch(`${base}/rpc/sessions:list`, mit(extra))).status).toBe(401);
  });

  it('an unknown token stays 401', async () => {
    expect((await fetch(`${base}/rpc/sessions:list`, mit('nope'))).status).toBe(401);
  });
});
