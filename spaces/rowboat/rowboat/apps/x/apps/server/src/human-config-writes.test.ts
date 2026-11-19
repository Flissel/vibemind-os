// Modified for VibeMind (Flissel/rowboat vibemind): the server's workspace:* handlers are human write paths
// only with the owner key (ticket #330, Task 17). Real server over loopback HTTP with the real handlers:
// an owner-key write to config/mcp.json or config/security.json takes effect now; a scoped key may not
// touch protected entries and never triggers the approval; a raw write changes nothing. Nothing is
// spawned: only the approval set and the allow-list are inspected.
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { RowboatServer } from './server.js';

// WorkDir is resolved when @x/core loads, so point it at a temp dir first.
const workDir = await fs.mkdtemp(path.join(os.tmpdir(), 'rowboat-human-config-'));
process.env.ROWBOAT_WORKDIR = workDir;

const security = await import('@x/core/dist/config/security.js');
const approvals = await import('@x/core/dist/mcp/stdio-approvals.js');

const STDIO = { command: 'node', args: ['server.js'] };
const mcpFile = path.join(workDir, 'config', 'mcp.json');

describe('server workspace:* handlers: owner key vs scoped key', () => {
  let server: RowboatServer;
  let base: string;
  let scopedToken: string;

  beforeAll(async () => {
    await fs.mkdir(path.join(workDir, 'config'), { recursive: true });
    const { createRowboatServer } = await import('./server.js');
    const { createCoreRpcHandlers, createCoreEventSources } = await import('./core-deps.js');
    const { addScopedKey } = await import('./auth.js');
    scopedToken = await addScopedKey(workDir, 'test-bot', [
      'workspace:writeFile',
      'workspace:rename',
      'workspace:copy',
      'workspace:remove',
      'workspace:mkdir',
    ]);
    server = await createRowboatServer({
      workDir,
      handlers: createCoreRpcHandlers(),
      events: createCoreEventSources(),
      resolveWorkspacePath: (rel) => path.join(workDir, rel),
      serverVersion: 'test',
      port: 0,
    });
    base = `http://127.0.0.1:${server.port}`;
  }, 60_000);

  afterAll(async () => {
    await server?.close();
    await fs.rm(workDir, { recursive: true, force: true });
  });

  beforeEach(async () => {
    approvals.resetStdioApprovalsForTests();
    await fs.writeFile(mcpFile, JSON.stringify({ mcpServers: {} }));
    await fs.writeFile(path.join(workDir, 'config', 'security.json'), JSON.stringify({ allowedCommands: ['ls'], allowedFileAccess: [] }));
    security.resetSecurityAllowListCache();
    security.reloadSecurityConfig();
  });

  const call = async (channel: string, body: unknown, token: string) => {
    const res = await fetch(`${base}/rpc/${channel}`, {
      method: 'POST',
      headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json' },
      body: JSON.stringify(body),
    });
    return { status: res.status, json: (await res.json()) as { error?: { message?: string } } };
  };

  it('owner key: workspace:writeFile config/mcp.json is written and approved', async () => {
    const data = JSON.stringify({ mcpServers: { s: STDIO } });
    expect((await call('workspace:writeFile', { path: 'config/mcp.json', data }, server.key)).status).toBe(200);
    expect(await fs.readFile(mcpFile, 'utf8')).toBe(data);
    expect(approvals.isStdioEntryApproved('s', STDIO)).toBe(true);
  });

  it('scoped key: workspace:writeFile config/mcp.json is refused, file and approvals unchanged', async () => {
    const data = JSON.stringify({ mcpServers: { s: STDIO } });
    const r = await call('workspace:writeFile', { path: 'config/mcp.json', data }, scopedToken);
    expect(r.status).not.toBe(200);
    expect(r.json.error?.message).toMatch(/protected workspace entries/);
    expect(JSON.parse(await fs.readFile(mcpFile, 'utf8'))).toEqual({ mcpServers: {} });
    expect(approvals.isStdioEntryApproved('s', STDIO)).toBe(false);
  });

  it('scoped key: security.json write, rename onto, copy out of keys and remove of config are refused', async () => {
    await fs.writeFile(path.join(workDir, 'staged.json'), JSON.stringify(['*']));
    const attempts: Array<[string, unknown]> = [
      ['workspace:writeFile', { path: 'config/security.json', data: '["*"]' }],
      ['workspace:rename', { from: 'staged.json', to: 'config/security.json', overwrite: true }],
      ['workspace:copy', { from: 'server-key', to: 'knowledge/k.txt', overwrite: true }],
      ['workspace:remove', { path: 'config' }],
      ['workspace:mkdir', { path: 'extensions/x', recursive: true }],
    ];
    for (const [channel, body] of attempts) {
      const r = await call(channel, body, scopedToken);
      expect(r.status, channel).not.toBe(200);
      expect(r.json.error?.message, channel).toMatch(/protected workspace entries/);
    }
    expect(security.getSecurityAllowList()).toEqual(['ls']);
    expect(JSON.parse(await fs.readFile(path.join(workDir, 'config', 'security.json'), 'utf8'))).toEqual({
      allowedCommands: ['ls'],
      allowedFileAccess: [],
    });
    await expect(fs.access(path.join(workDir, 'knowledge', 'k.txt'))).rejects.toThrow();
  });

  it('scoped key: an unprotected write still works', async () => {
    const r = await call('workspace:writeFile', { path: 'knowledge/note.md', data: '# x\n' }, scopedToken);
    expect(r.status).toBe(200);
    expect(await fs.readFile(path.join(workDir, 'knowledge', 'note.md'), 'utf8')).toBe('# x\n');
  });

  it('owner key: workspace:writeFile config/security.json replaces the live allow-list; a raw write does not', async () => {
    await fs.writeFile(path.join(workDir, 'config', 'security.json'), JSON.stringify(['*']));
    expect(security.getSecurityAllowList()).toEqual(['ls']);
    const r = await call('workspace:writeFile', { path: 'config/security.json', data: JSON.stringify(['git', 'ls']) }, server.key);
    expect(r.status).toBe(200);
    expect(security.getSecurityAllowList().sort()).toEqual(['git', 'ls']);
  });

  it('owner key: workspace:copy onto config/mcp.json approves the copied source', async () => {
    await fs.writeFile(path.join(workDir, 'staged.json'), JSON.stringify({ mcpServers: { c: STDIO } }));
    const r = await call('workspace:copy', { from: 'staged.json', to: 'config/mcp.json', overwrite: true }, server.key);
    expect(r.status).toBe(200);
    expect(approvals.isStdioEntryApproved('c', STDIO)).toBe(true);
  });

  it('a raw write of mcp.json approves nothing', async () => {
    await fs.writeFile(mcpFile, JSON.stringify({ mcpServers: { s: STDIO } }));
    expect(approvals.isStdioEntryApproved('s', STDIO)).toBe(false);
  });

  it('a direct handler call outside a request (no auth context) is treated like a scoped key', async () => {
    const { createCoreRpcHandlers } = await import('./core-deps.js');
    const handlers = createCoreRpcHandlers();
    await expect(
      handlers['workspace:writeFile']({ path: 'config/mcp.json', data: JSON.stringify({ mcpServers: { s: STDIO } }) }),
    ).rejects.toThrow(/protected workspace entries/);
    expect(approvals.isStdioEntryApproved('s', STDIO)).toBe(false);
  });
});
