import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import type { RowboatServer } from './server.js';

// WorkDir is resolved when @x/core loads, so point it at a temp dir first
// and import everything that touches core dynamically.
const workDir = await fs.mkdtemp(path.join(os.tmpdir(), 'rowboat-todo-extern-'));
process.env.ROWBOAT_WORKDIR = workDir;

describe('todo channels for the Freigabe messenger', () => {
  let server: RowboatServer;
  let base: string;
  let busEvents: Array<{ type: string }>;
  let unsubscribe: () => void;

  beforeAll(async () => {
    const { createRowboatServer } = await import('./server.js');
    const { createCoreRpcHandlers, createCoreEventSources } = await import('./core-deps.js');
    const { todoBus } = await import('@x/core/dist/todo/bus.js');
    busEvents = [];
    unsubscribe = todoBus.subscribe((e: { type: string }) => busEvents.push(e));
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
    unsubscribe?.();
    await server.close();
    await fs.rm(workDir, { recursive: true, force: true });
  });

  const call = async (channel: string, body: unknown, auth = true) => {
    const res = await fetch(`${base}/rpc/${channel}`, {
      method: 'POST',
      headers: auth ? { authorization: `Bearer ${server.key}` } : {},
      body: JSON.stringify(body),
    });
    return { status: res.status, json: (await res.json()) as any };
  };

  it('rejects all four channels without a bearer', async () => {
    for (const ch of ['todo:addSuggestions', 'todo:withdrawSuggestion', 'todo:setChecked', 'todo:plannerSignals']) {
      expect((await call(ch, null, false)).status).toBe(401);
    }
  });

  it('addSuggestions dedupes, lists in todo:get and publishes list_changed', async () => {
    busEvents.length = 0;
    const r = await call('todo:addSuggestions', { texts: ['Angebot nachfassen', 'Angebot nachfassen'] });
    expect(r.status).toBe(200);
    expect(r.json.added).toHaveLength(1);
    expect(busEvents.some((e) => e.type === 'list_changed')).toBe(true);
    const got = await call('todo:get', null);
    expect(got.json.suggestions).toContain('Angebot nachfassen');
  });

  it('withdrawSuggestion removes without a dismissed signal', async () => {
    busEvents.length = 0;
    const r = await call('todo:withdrawSuggestion', { text: 'Angebot nachfassen' });
    expect(r.json).toEqual({ success: true, removed: true });
    expect(busEvents.some((e) => e.type === 'list_changed')).toBe(true);
    const got = await call('todo:get', null);
    expect(got.json.suggestions).not.toContain('Angebot nachfassen');
    const sig = await call('todo:plannerSignals', null);
    expect(sig.status).toBe(200);
    expect(sig.json.signals.filter((s: { kind: string }) => s.kind === 'dismissed')).toEqual([]);
  });

  it('setChecked ticks an item, publishes, and fails for an unknown key', async () => {
    await call('todo:addItem', { text: 'Rechnung pruefen', run: false });
    const itemOf = (json: any, needle: string) =>
      json.list.blocks
        .filter((b: { kind: string }) => b.kind === 'item')
        .map((b: { item: { key: string; text: string; checked: boolean } }) => b.item)
        .find((i: { text: string }) => i.text.includes(needle));
    const before = await call('todo:get', null);
    const item = itemOf(before.json, 'Rechnung pruefen');
    expect(item).toBeTruthy();
    busEvents.length = 0;
    const ok = await call('todo:setChecked', { key: item.key, checked: true });
    expect(ok.json).toEqual({ success: true });
    expect(busEvents.some((e) => e.type === 'list_changed')).toBe(true);
    const after = await call('todo:get', null);
    expect(itemOf(after.json, 'Rechnung pruefen').checked).toBe(true);

    busEvents.length = 0;
    const bad = await call('todo:setChecked', { key: 'gibt-es-nicht', checked: true });
    expect(bad.json.success).toBe(false);
    expect(busEvents).toEqual([]);
  });
  it('wrote no planner signal at all; the ledger read works (positive control)', async () => {
    // After add / withdraw / setChecked above: the ledger must be empty.
    const empty = await call('todo:plannerSignals', null);
    expect(empty.json.signals).toEqual([]);

    // Control: a real acceptance records exactly one 'kept' signal.
    await call('todo:addSuggestions', { texts: ['Kontrollfall annehmen'] });
    expect((await call('todo:acceptSuggestion', { text: 'Kontrollfall annehmen' })).json).toEqual({ success: true });
    const got = await call('todo:get', null);
    const accepted = got.json.list.blocks
      .filter((b: { kind: string }) => b.kind === 'item')
      .map((b: { item: { key: string; text: string } }) => b.item)
      .find((i: { text: string }) => i.text.includes('Kontrollfall annehmen'));
    expect(accepted).toBeTruthy();
    // The signal is recorded fire-and-forget; wait for it.
    let signals: Array<{ kind: string; key: string }> = [];
    for (let n = 0; n < 50 && signals.length === 0; n++) {
      signals = (await call('todo:plannerSignals', null)).json.signals;
      if (signals.length === 0) await new Promise((r) => setTimeout(r, 50));
    }
    expect(signals).toHaveLength(1);
    expect(signals[0].kind).toBe('kept');
    expect(signals[0].key).toBe(accepted.key);
  });
});
