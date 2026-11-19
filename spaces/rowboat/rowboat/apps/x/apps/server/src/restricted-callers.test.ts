// Modified for VibeMind (Flissel/rowboat vibemind): ticket #330, Task 17 fix
// round 1 (review I-1, I-2). A scoped key — and the owner key carrying the
// restricted-principal header the Electron main process sets for the embed
// bridge — may not read protected workspace entries and may not answer a
// permission request as the human. Real server over loopback, real handlers,
// a real SessionsImpl + TurnRuntime with a scripted model (no network, no
// process start: addMcpServer only writes mcp.json and records the approval).
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { JsonValue, ResolvedAgent, ToolDescriptor, TurnEvent } from '@x/shared/dist/turns.js';
import type { z } from 'zod';
import type { RowboatServer } from './server.js';

const workDir = await fs.mkdtemp(path.join(os.tmpdir(), 'rowboat-restricted-'));
process.env.ROWBOAT_WORKDIR = workDir;

const container = (await import('@x/core/dist/di/container.js')).default;
const approvals = await import('@x/core/dist/mcp/stdio-approvals.js');
const security = await import('@x/core/dist/config/security.js');
const { TurnRuntime } = await import('@x/core/dist/runtime/turns/runtime.js');
const { InMemoryTurnRepo } = await import('@x/core/dist/runtime/turns/in-memory-turn-repo.js');
const { TurnRepoContextResolver } = await import('@x/core/dist/runtime/turns/context-resolver.js');
const { RealPermissionChecker } = await import('@x/core/dist/runtime/turns/bridges/real-permission-checker.js');
const { RealToolRegistry } = await import('@x/core/dist/runtime/turns/bridges/real-tool-registry.js');
const { SessionsImpl } = await import('@x/core/dist/runtime/sessions/sessions.js');
const { InMemorySessionRepo } = await import('@x/core/dist/runtime/sessions/in-memory-session-repo.js');
type IModelRegistry = import('@x/core/dist/runtime/turns/model-registry.js').IModelRegistry;
type LlmStreamEvent = import('@x/core/dist/runtime/turns/model-registry.js').LlmStreamEvent;

type TEvent = z.infer<typeof TurnEvent>;
type Call = { id: string; name: string; args: JsonValue };

const restricted = await import('@x/core/dist/security/restricted-principal.js');
type CodePermissionRegistry = import('@x/core/dist/code-mode/acp/permission-registry.js').CodePermissionRegistry;
const { runWithAuth } = await import('./auth-context.js');
const { isRpcChannel } = await import('./channels.js');

const STDIO = { command: 'calc.exe' };
const EMPTY = JSON.stringify({ mcpServers: {} });
const KEY_MARKER = 'server-key-content-marker';

function builtin(name: string): z.infer<typeof ToolDescriptor> {
  return { toolId: `builtin:${name}`, name, description: name, inputSchema: {}, execution: 'sync', requiresHuman: false };
}
const agent: z.infer<typeof ResolvedAgent> = {
  agentId: 'copilot',
  systemPrompt: 'SYS',
  model: { provider: 'fake', model: 'm' },
  tools: [builtin('addMcpServer')],
};

function respondCalls(...calls: Call[]) {
  return async function* (): AsyncGenerator<LlmStreamEvent, void, void> {
    yield {
      type: 'completed',
      message: { role: 'assistant', content: calls.map((c) => ({ type: 'tool-call' as const, toolCallId: c.id, toolName: c.name, arguments: c.args })) },
      finishReason: 'tool-calls',
      usage: { inputTokens: 1, outputTokens: 1, totalTokens: 2 },
    };
  };
}
function respondText(text: string) {
  return async function* (): AsyncGenerator<LlmStreamEvent, void, void> {
    yield { type: 'completed', message: { role: 'assistant', content: text }, finishReason: 'stop', usage: { inputTokens: 1, outputTokens: 1, totalTokens: 2 } };
  };
}

// Every model call answers from the queue; the tests push what the next turn says.
const script: Array<() => AsyncGenerator<LlmStreamEvent, void, void>> = [];
const models: IModelRegistry = {
  async resolve(descriptor) {
    return {
      descriptor,
      encodeMessages: (messages) => messages as unknown as JsonValue[],
      stream: () => {
        const next = script.shift();
        if (!next) throw new Error('no scripted model call remaining');
        return next();
      },
    };
  },
};

let ids = 0;
const turnRepo = new InMemoryTurnRepo();
const turnRuntime = new TurnRuntime({
  turnRepo,
  idGenerator: { next: async () => `2026-11-16T10-00-00Z-${String(++ids).padStart(7, '0')}-000` },
  clock: { now: () => '2026-11-16T10:00:00Z' },
  agentResolver: { resolve: async () => agent },
  modelRegistry: models,
  toolRegistry: new RealToolRegistry(),
  contextResolver: new TurnRepoContextResolver({ turnRepo }),
  permissionChecker: new RealPermissionChecker(),
  permissionClassifier: { classify: async (batch) => batch.requests.map((r) => ({ toolCallId: r.toolCallId, decision: 'defer' as const, reason: 'test' })) },
  lifecycleBus: { publish: () => undefined },
  turnEventBus: { publish: () => undefined, subscribe: () => () => undefined, subscribeAll: () => () => undefined },
  usageReporter: { reportModelUsage: () => undefined },
});
let sid = 0;
const sessions = new SessionsImpl({
  sessionRepo: new InMemorySessionRepo(),
  turnRuntime,
  idGenerator: { next: async () => `2026-11-16T09-00-00Z-${String(++sid).padStart(7, '0')}-000` },
  clock: { now: () => '2026-11-16T09:00:00Z' },
  sessionBus: { publish: () => undefined },
});

// Legacy runs: the run log and the runtime trigger are the only seams.
const legacy = { log: [] as unknown[], appended: [] as unknown[] };

// awilix resolver objects without importing awilix here.
const value = (v: unknown) => ({ resolve: () => v, isLeakSafe: true });
container.register({
  sessions: value(sessions) as never,
  runsRepo: value({
    fetch: async () => ({ log: legacy.log }),
    appendEvents: async (_runId: string, events: unknown[]) => { legacy.appended.push(...events); },
  }) as never,
  agentRuntime: value({ trigger: () => undefined }) as never,
});

async function waitForPermission(turnId: string, toolCallId: string): Promise<void> {
  for (let i = 0; i < 200; i++) {
    const { events } = await sessions.getTurn(turnId);
    if ((events as TEvent[]).some((e) => e.type === 'tool_permission_required' && e.toolCallId === toolCallId)) return;
    await new Promise((r) => setTimeout(r, 10));
  }
  throw new Error(`no permission request for ${toolCallId}`);
}

describe('restricted callers (scoped key, embed via the restricted header)', () => {
  let server: RowboatServer;
  let base: string;
  let scoped: string;

  beforeAll(async () => {
    await fs.mkdir(path.join(workDir, 'config'), { recursive: true });
    await fs.mkdir(path.join(workDir, 'knowledge'), { recursive: true });
    await fs.writeFile(path.join(workDir, 'knowledge', 'note.md'), '# note\n');
    const { createRowboatServer } = await import('./server.js');
    const { createCoreRpcHandlers, createCoreEventSources } = await import('./core-deps.js');
    const { addScopedKey } = await import('./auth.js');
    scoped = await addScopedKey(workDir, 'bot', [
      'workspace:readFile', 'workspace:readdir', 'workspace:stat', 'workspace:exists', 'workspace:writeFile',
      'sessions:respondToPermission', 'runs:authorizePermission',
      'codeRun:resolvePermission', 'channels:getConfig', 'channels:setConfig', 'codeSession:update',
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
    await fs.writeFile(path.join(workDir, 'config', 'mcp.json'), EMPTY);
    await fs.writeFile(path.join(workDir, 'config', 'security.json'), JSON.stringify({ allowedCommands: ['ls'], allowedFileAccess: [] }));
    await fs.writeFile(path.join(workDir, 'cipher-key'), KEY_MARKER);
    security.resetSecurityAllowListCache();
    security.reloadSecurityConfig();
    script.length = 0;
    legacy.log = [];
    legacy.appended = [];
  });

  const call = async (channel: string, body: unknown, token: string, headers: Record<string, string> = {}) => {
    const res = await fetch(`${base}/rpc/${channel}`, {
      method: 'POST',
      headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json', ...headers },
      body: JSON.stringify(body),
    });
    const text = await res.text();
    return { status: res.status, text, json: JSON.parse(text) as { error?: { message?: string } } & Record<string, unknown> };
  };
  const RESTRICTED = { 'x-rowboat-principal': 'restricted' };

  describe('I-1: reads of protected entries', () => {
    it('scoped key: readFile of server-key, cipher-key and config/ is refused, nothing leaks', async () => {
      for (const p of ['server-key', 'cipher-key', 'config/mcp.json', 'scoped-keys.json']) {
        const r = await call('workspace:readFile', { path: p }, scoped);
        expect(r.status, p).not.toBe(200);
        expect(r.json.error?.message, p).toMatch(/protected workspace entries/);
        expect(r.text).not.toContain(server.key);
        expect(r.text).not.toContain(KEY_MARKER);
      }
    });

    it('scoped key: stat/exists/readdir inside a protected entry are refused; the root listing works', async () => {
      expect((await call('workspace:stat', { path: 'server-key' }, scoped)).status).not.toBe(200);
      expect((await call('workspace:exists', { path: 'config/mcp.json' }, scoped)).status).not.toBe(200);
      expect((await call('workspace:readdir', { path: 'config' }, scoped)).status).not.toBe(200);
      const rootList = await call('workspace:readdir', { path: '' }, scoped);
      expect(rootList.status).toBe(200);
      expect((rootList.json as unknown as Array<{ path: string }>).map((e) => e.path)).toEqual(expect.arrayContaining(['config', 'knowledge']));
    });

    it('scoped key: a recursive root listing drops what lies inside protected entries', async () => {
      const r = await call('workspace:readdir', { path: '', opts: { recursive: true } }, scoped);
      expect(r.status).toBe(200);
      const paths = (r.json as unknown as Array<{ path: string }>).map((e) => e.path.replace(/\\/g, '/'));
      expect(paths).toContain('knowledge/note.md');
      expect(paths.filter((p) => p.startsWith('config/'))).toEqual([]);
    });

    it('scoped key: an unprotected read works', async () => {
      const r = await call('workspace:readFile', { path: 'knowledge/note.md' }, scoped);
      expect(r.status).toBe(200);
      expect(r.json.data).toBe('# note\n');
    });

    it('owner key: reads of protected entries are unchanged', async () => {
      const r = await call('workspace:readFile', { path: 'cipher-key' }, server.key);
      expect(r.status).toBe(200);
      expect(r.json.data).toBe(KEY_MARKER);
    });

    it('owner key with the restricted header (embed bridge): treated like a scoped key', async () => {
      const read = await call('workspace:readFile', { path: 'cipher-key' }, server.key, RESTRICTED);
      expect(read.status).not.toBe(200);
      expect(read.text).not.toContain(KEY_MARKER);
      const write = await call('workspace:writeFile', { path: 'config/mcp.json', data: JSON.stringify({ mcpServers: { s: STDIO } }) }, server.key, RESTRICTED);
      expect(write.status).not.toBe(200);
      expect(await fs.readFile(path.join(workDir, 'config', 'mcp.json'), 'utf8')).toBe(EMPTY);
      expect(approvals.isStdioEntryApproved('s', STDIO)).toBe(false);
    });
  });

  describe('I-2: permission answers', () => {
    async function suspendedOn(call0: Call): Promise<string> {
      script.push(respondCalls(call0), respondText('done'));
      const sessionId = await sessions.createSession();
      const { turnId } = await sessions.sendMessage(sessionId, { role: 'user', content: 'go' }, { agent: { agentId: 'copilot' } });
      await waitForPermission(turnId, call0.id);
      return turnId;
    }
    const addStdio: Call = { id: 'A', name: 'addMcpServer', args: { serverName: 'pwn', config: STDIO } };
    const addUrl: Call = { id: 'B', name: 'addMcpServer', args: { serverName: 'web', config: { url: 'http://127.0.0.1:9000/mcp' } } };

    it('scoped key allowing a human-only addMcpServer stdio request is refused: nothing written or approved; owner key then works', async () => {
      const turnId = await suspendedOn(addStdio);
      const refused = await call('sessions:respondToPermission', { turnId, toolCallId: 'A', decision: 'allow' }, scoped);
      expect(refused.status).not.toBe(200);
      expect(refused.json.error?.message).toMatch(/human/);
      expect(await fs.readFile(path.join(workDir, 'config', 'mcp.json'), 'utf8')).toBe(EMPTY);
      expect(approvals.isStdioEntryApproved('pwn', STDIO)).toBe(false);

      const embed = await call('sessions:respondToPermission', { turnId, toolCallId: 'A', decision: 'allow' }, server.key, RESTRICTED);
      expect(embed.status).not.toBe(200);
      expect(approvals.isStdioEntryApproved('pwn', STDIO)).toBe(false);

      const owner = await call('sessions:respondToPermission', { turnId, toolCallId: 'A', decision: 'allow' }, server.key);
      expect(owner.status).toBe(200);
      expect(approvals.isStdioEntryApproved('pwn', STDIO)).toBe(true);
      const { events } = await sessions.getTurn(turnId);
      expect((events as TEvent[]).find((e) => e.type === 'tool_permission_resolved')).toMatchObject({ source: 'human' });
    });

    it('scoped key answering "always" on an ordinary request is refused; a one-time allow is recorded as delegated', async () => {
      const turnId = await suspendedOn(addUrl);
      const always = await call('sessions:respondToPermission', { turnId, toolCallId: 'B', decision: 'allow', metadata: { scope: 'always' } }, scoped);
      expect(always.status).not.toBe(200);
      expect(always.json.error?.message).toMatch(/scope/);
      const once = await call('sessions:respondToPermission', { turnId, toolCallId: 'B', decision: 'allow' }, scoped);
      expect(once.status).toBe(200);
      const { events } = await sessions.getTurn(turnId);
      expect((events as TEvent[]).find((e) => e.type === 'tool_permission_resolved')).toMatchObject({ decision: 'allow', source: 'delegated' });
    });

    const legacyRequest = (toolCallId: string) => ({
      runId: 'r1', type: 'tool-permission-request', subflow: [],
      toolCall: { type: 'tool-call', toolCallId, toolName: 'executeCommand', arguments: { command: 'git status' } },
      permission: { kind: 'command', commandNames: ['git'] },
    });
    const authorize = (scope: 'once' | 'always', token: string, headers: Record<string, string> = {}) =>
      call('runs:authorizePermission', { runId: 'r1', authorization: { toolCallId: 'C', response: 'approve', scope, subflow: [] } }, token, headers);

    it('legacy: scoped key answering "always" on an ordinary command is refused, the allow-list is unchanged; owner key adds it', async () => {
      legacy.log = [legacyRequest('C')];
      const refused = await authorize('always', scoped);
      expect(refused.status).not.toBe(200);
      expect(refused.json.error?.message).toMatch(/scope/);
      expect(security.getSecurityAllowList()).toEqual(['ls']);
      expect(JSON.parse(await fs.readFile(path.join(workDir, 'config', 'security.json'), 'utf8')).allowedCommands).toEqual(['ls']);
      expect(legacy.appended).toEqual([]);

      expect((await authorize('always', server.key, RESTRICTED)).status).not.toBe(200);
      expect(security.getSecurityAllowList()).toEqual(['ls']);

      const owner = await authorize('always', server.key);
      expect(owner.status).toBe(200);
      expect(security.getSecurityAllowList()).toContain('git');
    });

    it('legacy: a scoped one-time approve of an ordinary command is accepted', async () => {
      legacy.log = [legacyRequest('C')];
      expect((await authorize('once', scoped)).status).toBe(200);
      expect(legacy.appended).toMatchObject([{ toolCallId: 'C', response: 'approve' }]);
      expect(security.getSecurityAllowList()).toEqual(['ls']);
    });
  });

  // Fix round 2 (review F-1): the coding agent's mid-run asks.
  describe('F-1: code-run permission answers', () => {
    const registry = () => container.resolve<CodePermissionRegistry>('codePermissionRegistry');
    function pendingAsk(): { id: string; decision: Promise<string> } {
      let id = '';
      const decision = registry().request('run-f1', (requestId) => { id = requestId; });
      return { id, decision };
    }

    it('scoped key and embed header: allow_always is refused and the ask stays pending; allow_once is accepted as once-only', async () => {
      const ask = pendingAsk();
      const always = await call('codeRun:resolvePermission', { requestId: ask.id, decision: 'allow_always' }, scoped);
      expect(always.status).not.toBe(200);
      expect(always.json.error?.message).toMatch(/human's own answer/);
      const embed = await call('codeRun:resolvePermission', { requestId: ask.id, decision: 'allow_always' }, server.key, RESTRICTED);
      expect(embed.status).not.toBe(200);
      const once = await call('codeRun:resolvePermission', { requestId: ask.id, decision: 'allow_once' }, scoped);
      expect(once.status).toBe(200);
      expect(await ask.decision).toBe('allow_once_only');
    });

    it('owner key: allow_always works', async () => {
      const ask = pendingAsk();
      const owner = await call('codeRun:resolvePermission', { requestId: ask.id, decision: 'allow_always' }, server.key);
      expect(owner.status).toBe(200);
      expect(await ask.decision).toBe('allow_always');
    });
  });

  // Fix round 2 (review F-2): config and authority channels, credentials.
  describe('F-2: config channels and credentials', () => {
    const channelsFile = path.join(workDir, 'config', 'channels.json');
    const CHANNELS = {
      whatsapp: { enabled: false, allowFrom: [] },
      telegram: { enabled: false, botToken: '123:TELEGRAM-SECRET', allowFrom: ['1'] },
      readOnlyMcpTools: ['wissen:wissen_suchen'],
    };

    it('every denied and conditional channel served by the server is refused by the real handler table for a scoped caller; the rest are main-only', async () => {
      const { createCoreRpcHandlers } = await import('./core-deps.js');
      const handlers = createCoreRpcHandlers() as unknown as Record<string, (args: unknown) => Promise<unknown>>;
      const served: string[] = [];
      for (const channel of restricted.RESTRICTED_DENIED_CHANNELS.keys()) {
        const handler = handlers[channel];
        if (!handler) {
          // Not a server channel: only main's IPC dispatch can serve it, and
          // it checks the same set for the embed bridge.
          expect(isRpcChannel(channel), channel).toBe(false);
          continue;
        }
        served.push(channel);
        await expect(runWithAuth('scoped', () => handler({})), channel).rejects.toThrow(restricted.RESTRICTED_REFUSAL);
      }
      expect(served.length).toBeGreaterThan(30);
      const policy = [
        ['codeSession:update', { sessionId: 's', patch: { policy: 'yolo' } }],
        ['codeSession:create', { projectId: 'p', agent: 'claude', isolation: 'in-repo', policy: 'yolo' }],
        ['runs:createMessage', { runId: 'r', message: 'hi', codePolicy: 'yolo' }],
        // Fix round 3 (review I-1, I-4).
        ['spaces:decideApproval', { orgId: 'o', spaceId: 's', approvalId: 'a', decision: 'allow_always' }],
        ['spaces:decideApproval', { orgId: 'o', spaceId: 's', approvalId: 'a', decision: 'allow_once' }],
        ['apps:update', { folder: 'f', confirmNewCapabilities: true }],
      ] as const;
      for (const [channel, args] of policy) {
        const handler = handlers[channel];
        if (!handler) {
          expect(isRpcChannel(channel), channel).toBe(false);
          continue;
        }
        await expect(runWithAuth('scoped', () => handler(args)), channel).rejects.toThrow(restricted.RESTRICTED_REFUSAL);
      }
      // Fix round 3 (review I-4): prompt-free tool execution is among the served denials.
      expect(served).toEqual(expect.arrayContaining(['mcp:executeTool', 'composio:execute-tool', 'spaces:createInvite', 'apps:install', 'todo:setPlanner']));
    });

    it('scoped key and embed header: channels:setConfig is refused and channels.json is unchanged; the owner key writes it', async () => {
      await fs.writeFile(channelsFile, JSON.stringify(CHANNELS));
      const widened = { ...CHANNELS, telegram: { ...CHANNELS.telegram, allowFrom: ['1', '666'] }, readOnlyMcpTools: ['files:write'] };
      expect((await call('channels:setConfig', widened, scoped)).status).not.toBe(200);
      expect((await call('channels:setConfig', widened, server.key, RESTRICTED)).status).not.toBe(200);
      expect(JSON.parse(await fs.readFile(channelsFile, 'utf8'))).toEqual(CHANNELS);
      expect((await call('channels:setConfig', widened, server.key)).status).toBe(200);
      expect(JSON.parse(await fs.readFile(channelsFile, 'utf8')).telegram.allowFrom).toEqual(['1', '666']);
    });

    it('scoped key and embed header: channels:getConfig has no bot token; the owner key sees it', async () => {
      await fs.writeFile(channelsFile, JSON.stringify(CHANNELS));
      for (const [token, headers] of [[scoped, {}], [server.key, RESTRICTED]] as const) {
        const r = await call('channels:getConfig', null, token, headers);
        expect(r.status).toBe(200);
        expect(r.text).not.toContain('TELEGRAM-SECRET');
        expect((r.json as unknown as typeof CHANNELS).telegram).toEqual({ ...CHANNELS.telegram, botToken: '' });
      }
      const owner = await call('channels:getConfig', null, server.key);
      expect((owner.json as unknown as typeof CHANNELS).telegram.botToken).toBe('123:TELEGRAM-SECRET');
    });

    it('every guarded channel the real server handler map serves is wrapped by the guard', async () => {
      const { createCoreRpcHandlers, RESTRICTED_GUARD_MARK } = await import('./core-deps.js');
      const handlers = createCoreRpcHandlers() as unknown as Record<string, unknown>;
      const unguarded: string[] = [];
      let served = 0;
      for (const channel of restricted.RESTRICTED_GUARDED_CHANNELS) {
        const handler = handlers[channel] as Record<symbol, unknown> | undefined;
        if (!handler) {
          expect(isRpcChannel(channel), channel).toBe(false);
          continue;
        }
        served++;
        if (handler[RESTRICTED_GUARD_MARK] !== true) unguarded.push(channel);
      }
      expect(unguarded).toEqual([]);
      expect(served).toBeGreaterThan(60);
    });

    it('the guard blanks the secret of every masked channel for a scoped caller, not for the owner', async () => {
      const { guardRestrictedCallers } = await import('./core-deps.js');
      const secrets: Record<string, unknown> = {
        'channels:getConfig': { whatsapp: { enabled: false, allowFrom: [] }, telegram: { enabled: true, botToken: 'S-1', allowFrom: [] } },
        'channels:getStatus': { whatsapp: { state: 'qr', qrDataUrl: 'S-2' }, telegram: { state: 'disabled' } },
        'voice:getConfig': { deepgram: { apiKey: 'S-3' }, elevenlabs: { apiKey: 'S-4' } },
        'account:getRowboat': { signedIn: true, accessToken: 'S-5' },
        'server:getPairingInfo': { url: 'http://x', token: 'S-6' },
      };
      expect(Object.keys(secrets).sort()).toEqual([...restricted.RESTRICTED_MASKED_CHANNELS].sort());
      const fake = Object.fromEntries(Object.entries(secrets).map(([k, v]) => [k, async () => structuredClone(v)]));
      const guarded = guardRestrictedCallers(fake as never) as unknown as Record<string, (a: unknown) => Promise<unknown>>;
      for (const channel of Object.keys(secrets)) {
        const scopedResult = JSON.stringify(await runWithAuth('scoped', () => guarded[channel]!(null)));
        expect(scopedResult, channel).not.toMatch(/S-\d/);
        const ownerResult = JSON.stringify(await runWithAuth('owner', () => guarded[channel]!(null)));
        expect(ownerResult, channel).toMatch(/S-\d/);
      }
    });

    it('scoped key and embed header: voice:getConfig through the real handler returns no Deepgram key', async () => {
      const file = path.join(workDir, 'config', 'deepgram.json');
      await fs.writeFile(file, JSON.stringify({ apiKey: 'DEEPGRAM-SECRET' }));
      try {
        const { createCoreRpcHandlers } = await import('./core-deps.js');
        const handlers = createCoreRpcHandlers() as unknown as Record<string, (a: unknown) => Promise<unknown>>;
        const scopedResult = await runWithAuth('scoped', () => handlers['voice:getConfig']!(null));
        expect(JSON.stringify(scopedResult)).not.toContain('DEEPGRAM-SECRET');
        expect(scopedResult).toMatchObject({ deepgram: null });
        const owner = await runWithAuth('owner', () => handlers['voice:getConfig']!(null));
        expect(owner).toMatchObject({ deepgram: { apiKey: 'DEEPGRAM-SECRET' } });
      } finally {
        await fs.rm(file, { force: true });
      }
    });

    it('scoped key: codeSession:update that sets a policy is refused before the session is looked up', async () => {
      const r = await call('codeSession:update', { sessionId: 'nope', patch: { policy: 'yolo' } }, scoped);
      expect(r.status).not.toBe(200);
      expect(r.json.error?.message).toMatch(/protected workspace entries|may not/);
    });
  });

  // Fix round 3 (operator decision 19.11.2026): main forwards an embed call
  // with forwardHeadersForOrigin('embed') and the owner key. Both values of
  // the switch: trusted = exactly the app's own renderer, untrusted = the
  // refusals of fix rounds 1 and 2. A scoped key and a call with no recorded
  // origin stay restricted either way.
  describe.each([true, false])('embed forwarded by main, EMBED_IS_TRUSTED=%s', (trusted) => {
    const embed = () => restricted.forwardHeadersForOrigin('embed', trusted);
    const channelsFile = path.join(workDir, 'config', 'channels.json');
    const CHANNELS = { whatsapp: { enabled: false, allowFrom: [] }, telegram: { enabled: false, botToken: '123:EMBED-SECRET', allowFrom: [] } };

    it('reads a protected entry only when trusted', async () => {
      const r = await call('workspace:readFile', { path: 'cipher-key' }, server.key, embed());
      expect(r.status === 200).toBe(trusted);
      expect(r.text.includes(KEY_MARKER)).toBe(trusted);
    });

    it('a write of config/mcp.json is a human write path (entry approved) only when trusted', async () => {
      const data = JSON.stringify({ mcpServers: { s: STDIO } });
      const r = await call('workspace:writeFile', { path: 'config/mcp.json', data }, server.key, embed());
      expect(r.status === 200).toBe(trusted);
      expect(await fs.readFile(path.join(workDir, 'config', 'mcp.json'), 'utf8')).toBe(trusted ? data : EMPTY);
      expect(approvals.isStdioEntryApproved('s', STDIO)).toBe(trusted);
    });

    it('channels:setConfig and the bot token: as the renderer only when trusted', async () => {
      await fs.writeFile(channelsFile, JSON.stringify(CHANNELS));
      const get = await call('channels:getConfig', null, server.key, embed());
      expect(get.text.includes('EMBED-SECRET')).toBe(trusted);
      const widened = { ...CHANNELS, telegram: { ...CHANNELS.telegram, allowFrom: ['666'] } };
      expect((await call('channels:setConfig', widened, server.key, embed())).status === 200).toBe(trusted);
      expect(JSON.parse(await fs.readFile(channelsFile, 'utf8')).telegram.allowFrom).toEqual(trusted ? ['666'] : []);
    });

    it('a coding agent allow_always is the human answer only when trusted', async () => {
      const registry = container.resolve<CodePermissionRegistry>('codePermissionRegistry');
      let id = '';
      const decision = registry.request('run-f3', (requestId) => { id = requestId; });
      const r = await call('codeRun:resolvePermission', { requestId: id, decision: 'allow_always' }, server.key, embed());
      expect(r.status === 200).toBe(trusted);
      if (!trusted) registry.resolve(id, 'reject', 'human');
      expect(await decision).toBe(trusted ? 'allow_always' : 'reject');
    });

    it('allowing a human-only turn request is recorded as human only when trusted', async () => {
      script.push(respondCalls({ id: 'E', name: 'addMcpServer', args: { serverName: 'emb', config: STDIO } }), respondText('done'));
      const sessionId = await sessions.createSession();
      const { turnId } = await sessions.sendMessage(sessionId, { role: 'user', content: 'go' }, { agent: { agentId: 'copilot' } });
      await waitForPermission(turnId, 'E');
      const r = await call('sessions:respondToPermission', { turnId, toolCallId: 'E', decision: 'allow' }, server.key, embed());
      expect(r.status === 200).toBe(trusted);
      expect(approvals.isStdioEntryApproved('emb', STDIO)).toBe(trusted);
      if (trusted) {
        const { events } = await sessions.getTurn(turnId);
        expect((events as TEvent[]).find((e) => e.type === 'tool_permission_resolved')).toMatchObject({ source: 'human' });
      }
    });

    it('a scoped key and a call with no recorded origin stay restricted', async () => {
      await fs.writeFile(channelsFile, JSON.stringify(CHANNELS));
      const widened = { ...CHANNELS, telegram: { ...CHANNELS.telegram, allowFrom: ['666'] } };
      expect((await call('channels:setConfig', widened, scoped)).status).not.toBe(200);
      const unknown = restricted.forwardHeadersForOrigin('unknown', trusted);
      expect((await call('channels:setConfig', widened, server.key, unknown)).status).not.toBe(200);
      expect((await call('workspace:readFile', { path: 'cipher-key' }, server.key, unknown)).text).not.toContain(KEY_MARKER);
      expect(JSON.parse(await fs.readFile(channelsFile, 'utf8'))).toEqual(CHANNELS);
    });
  });
});
