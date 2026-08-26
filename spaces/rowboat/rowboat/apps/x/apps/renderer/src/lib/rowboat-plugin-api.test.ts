import { describe, expect, it, vi } from 'vitest';

import {
  DESKTOP_PLUGIN_CATALOG_PIN,
  RowboatPluginApi,
  type PluginApiSession,
  type PluginCatalogResponse,
} from './rowboat-plugin-api';

const digest = 'a'.repeat(64);
const componentDigest = 'b'.repeat(64);

function catalog(status: PluginCatalogResponse['items'][number]['status'] = 'available'): PluginCatalogResponse {
  return {
    items: [{
      name: 'github',
      pluginName: 'github',
      pluginVersion: '1.0.0',
      catalogDigest: digest,
      sourceCommit: 'c'.repeat(40),
      policyVersion: 'policy-v1',
      license: { declaration: 'MIT', decision: 'admitted' },
      admission: 'admitted',
      components: [{
        componentDigest,
        name: 'github-mcp',
        kind: 'mcp',
        admission: { status: 'admitted', policyVersion: 'policy-v1' },
        availability: { status },
        status,
      }],
      status,
    }],
  };
}

function json(value: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(value), {
    ...init,
    headers: { 'content-type': 'application/json; charset=utf-8', ...init.headers },
  });
}

const session: PluginApiSession = Object.freeze({
  baseUrl: 'https://rowboat.example/',
  accessToken: 'token-value',
  accountFingerprint: 'f'.repeat(64),
});

describe('RowboatPluginApi', () => {
  it('sends an ephemeral bearer token without retaining it in snapshots or errors', async () => {
    let authorization: string | null = null;
    const fetcher = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      authorization = new Headers(init?.headers).get('authorization');
      expect(String(request)).toBe(`https://rowboat.example/api/v1/plugins?catalogDigest=${digest}`);
      expect(init?.redirect).toBe('error');
      return json(catalog());
    });
    const client = new RowboatPluginApi(fetcher);

    await expect(client.listCatalog(session, digest)).resolves.toEqual(catalog());
    expect(authorization).toBe('Bearer token-value');
    expect(JSON.stringify(client.snapshot())).not.toContain('token-value');

    fetcher.mockRejectedValueOnce(new Error('provider exploded token-value'));
    await expect(client.listCatalog(session, digest)).rejects.toThrow('plugin_api_unavailable');
  });

  it.each([
    'ftp://rowboat.example/',
    'https://user:pass@rowboat.example/',
    'https://rowboat.example/path',
    'https://rowboat.example/?next=https://evil.example',
    'https://rowboat.example/#fragment',
    'http://rowboat.example/',
    'https://rowboat.example\\@evil.example/',
  ])('rejects unsafe configured base URL %s before fetch', async (baseUrl) => {
    const fetcher = vi.fn();
    const client = new RowboatPluginApi(fetcher);
    await expect(client.listCatalog({ baseUrl, accessToken: 'safe-token', accountFingerprint: 'f'.repeat(64) }, digest)).rejects.toThrow('plugin_api_config_invalid');
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('allows loopback HTTP for local development and blocks redirects', async () => {
    const fetcher = vi.fn(async (_request: RequestInfo | URL, init?: RequestInit) => {
      expect(init?.redirect).toBe('error');
      return json(catalog());
    });
    await new RowboatPluginApi(fetcher).listCatalog({ baseUrl: 'http://127.0.0.1:3000/', accessToken: 'token', accountFingerprint: 'f'.repeat(64) }, digest);
    expect(fetcher).toHaveBeenCalledOnce();
  });

  it('rejects duplicate keys, non-JSON, oversized, and malformed responses', async () => {
    const responses = [
      new Response(`{"items":[],"it\\u0065ms":[]}`, { headers: { 'content-type': 'application/json' } }),
      new Response('<html>no</html>', { headers: { 'content-type': 'text/html' } }),
      new Response(' '.repeat(262_145), { headers: { 'content-type': 'application/json' } }),
      json({ items: [{ ...catalog().items[0], surprise: 'field' }] }),
    ];
    const fetcher = vi.fn(async () => responses.shift()!);
    const client = new RowboatPluginApi(fetcher);
    for (let index = 0; index < 4; index += 1) {
      await expect(client.listCatalog(session, digest)).rejects.toThrow('plugin_api_response_invalid');
    }
  });

  it('rejects proxied response objects without evaluating attacker fields', async () => {
    const target = json(catalog());
    const response = new Proxy(target, { getPrototypeOf: () => { throw new Error('token-value'); } });
    const client = new RowboatPluginApi(vi.fn(async () => response));
    await expect(client.listCatalog(session, digest)).rejects.toThrow('plugin_api_response_invalid');
  });

  it.each([
    { sourceCommit: 'd'.repeat(40), policyVersion: DESKTOP_PLUGIN_CATALOG_PIN.policyVersion },
    { sourceCommit: DESKTOP_PLUGIN_CATALOG_PIN.sourceCommit, policyVersion: 'forged-policy' },
  ])('rejects a consistently forged Desktop catalog source/policy pin', async ({ sourceCommit, policyVersion }) => {
    const original = catalog().items[0]!;
    const body = { items: [{ ...original, catalogDigest: DESKTOP_PLUGIN_CATALOG_PIN.catalogDigest, sourceCommit, policyVersion,
      components: original.components.map((component) => ({ ...component, admission: { ...component.admission, policyVersion } })) }] };
    const client = new RowboatPluginApi(vi.fn(async () => json(body)));
    await expect(client.listCatalog(session, DESKTOP_PLUGIN_CATALOG_PIN.catalogDigest)).rejects.toThrow('plugin_api_response_invalid');
  });

  it('accepts the exact Desktop catalog digest/source/policy pin', async () => {
    const original = catalog().items[0]!;
    const policyVersion = DESKTOP_PLUGIN_CATALOG_PIN.policyVersion;
    const body = { items: [{ ...original, catalogDigest: DESKTOP_PLUGIN_CATALOG_PIN.catalogDigest,
      sourceCommit: DESKTOP_PLUGIN_CATALOG_PIN.sourceCommit, policyVersion,
      components: original.components.map((component) => ({ ...component, admission: { ...component.admission, policyVersion } })) }] };
    const client = new RowboatPluginApi(vi.fn(async () => json(body)));
    await expect(client.listCatalog(session, DESKTOP_PLUGIN_CATALOG_PIN.catalogDigest)).resolves.toMatchObject({ items: [{ pluginName: 'github' }] });
  });

  it('cancels a hanging response body at the whole-request deadline', async () => {
    let cancellations = 0;
    const body = new ReadableStream<Uint8Array>({ pull: () => new Promise<void>(() => undefined), cancel: () => { cancellations += 1; } });
    const client = new RowboatPluginApi(vi.fn(async () => new Response(body, { headers: { 'content-type': 'application/json' } })), { timeoutMs: 10 });
    await expect(client.listCatalog(session, digest)).rejects.toThrow('plugin_api_timeout');
    expect(cancellations).toBe(1);
  });

  it('cancels an oversized open response body exactly once', async () => {
    let cancellations = 0;
    const body = new ReadableStream<Uint8Array>({
      start(controller) { controller.enqueue(new Uint8Array(262_145)); },
      cancel() { cancellations += 1; },
    });
    const client = new RowboatPluginApi(vi.fn(async () => new Response(body, { headers: { 'content-type': 'application/json' } })));
    await expect(client.listCatalog(session, digest)).rejects.toThrow('plugin_api_response_invalid');
    expect(cancellations).toBe(1);
  });

  it('cancels an open non-JSON response before rejecting', async () => {
    let cancellations = 0;
    const body = new ReadableStream<Uint8Array>({ cancel() { cancellations += 1; } });
    const client = new RowboatPluginApi(vi.fn(async () => new Response(body, { headers: { 'content-type': 'text/html' } })));
    await expect(client.listCatalog(session, digest)).rejects.toThrow('plugin_api_response_invalid');
    expect(cancellations).toBe(1);
  });

  it('honors caller abort and deadline without exposing the token', async () => {
    const fetcher = vi.fn((_request: RequestInfo | URL, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => reject(new DOMException('token-value', 'AbortError')), { once: true });
    }));
    const controller = new AbortController();
    const promise = new RowboatPluginApi(fetcher, { timeoutMs: 20 }).listCatalog(session, digest, controller.signal);
    controller.abort();
    await expect(promise).rejects.toThrow('plugin_api_aborted');
  });

  it('previews before install and scopes a delimiter-free idempotency key to the project', async () => {
    const calls: Array<{ url: string; method: string; key: string | null; body: string | null }> = [];
    const fetcher = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      calls.push({
        url: String(request),
        method: init?.method ?? 'GET',
        key: new Headers(init?.headers).get('idempotency-key'),
        body: typeof init?.body === 'string' ? init.body : null,
      });
      if (calls.length === 1) return json({ items: [{
        pluginName: 'github', pluginVersion: '1.0.0', catalogDigest: digest, policyVersion: 'policy-v1',
        license: { declaration: 'MIT', decision: 'admitted' }, admission: 'admitted', components: catalog().items[0].components,
        enabled: true, revision: 4, status: 'available',
      }] });
      if (calls.length === 2) return json({
        pluginName: 'github', catalogDigest: digest, sourceCommit: 'c'.repeat(40), policyVersion: 'policy-v1',
        license: { declaration: 'MIT', decision: 'admitted' }, admission: 'admitted', components: catalog().items[0].components,
        status: 'available',
        credentialSlots: [{ name: 'GITHUB_TOKEN', configured: false }],
      });
      return json({ type: 'install', receiptId: 'receipt-1', projectId: 'project-1', pluginName: 'github', status: 'success', redactions: ['credentials'] }, { status: 201 });
    });
    const client = new RowboatPluginApi(fetcher, { createIdempotencyKey: () => 'desktop-install-123' });
    const result = await client.previewAndInstall(session, {
      projectId: 'project-1', pluginName: 'github', catalogDigest: digest,
    });

    expect(result.receipt.status).toBe('success');
    expect(calls.map((call) => call.method)).toEqual(['GET', 'GET', 'POST']);
    expect(calls[0]?.url).toContain('/api/v1/projects/project-1/plugins?catalogDigest=');
    expect(calls[1]?.url).toContain('/api/v1/projects/project-1/plugins/github?catalogDigest=');
    expect(calls[2]?.url).toBe('https://rowboat.example/api/v1/projects/project-1/plugins');
    expect(calls[2]?.key).toBe('desktop-install-123');
    expect(calls[2]?.key).toMatch(/^[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$/);
    expect(JSON.parse(calls[2]?.body ?? '{}')).toEqual({ pluginName: 'github', catalogDigest: digest, expectedRevision: 4 });
    expect(JSON.stringify(client.snapshot())).not.toContain('token-value');
  });

  it('replays an uncertain install once with the same idempotency key', async () => {
    const keys: Array<string | null> = [];
    let call = 0;
    const fetcher = vi.fn(async (_request: RequestInfo | URL, init?: RequestInit) => {
      call += 1;
      if (call === 1) return json({ items: [] });
      if (call === 2) return json({
        pluginName: 'github', catalogDigest: digest, sourceCommit: 'c'.repeat(40), policyVersion: 'policy-v1',
        license: { declaration: 'MIT', decision: 'admitted' }, admission: 'admitted', components: catalog().items[0].components,
        status: 'available', credentialSlots: [],
      });
      keys.push(new Headers(init?.headers).get('idempotency-key'));
      if (call === 3) throw new TypeError('uncertain network failure');
      return json({ type: 'install', receiptId: 'receipt-replay', projectId: 'project-1', pluginName: 'github', status: 'success', redactions: [] }, { status: 201 });
    });
    const client = new RowboatPluginApi(fetcher, { createIdempotencyKey: () => 'stable-replay-key' });
    const result = await client.previewAndInstall(session, { projectId: 'project-1', pluginName: 'github', catalogDigest: digest });
    expect(result.receipt.receiptId).toBe('receipt-replay');
    expect(keys).toEqual(['stable-replay-key', 'stable-replay-key']);
  });

  it('retains one preview-scoped idempotency key across repeated failures', async () => {
    const keys: Array<string | null> = [];
    let call = 0;
    const fetcher = vi.fn(async (_request: RequestInfo | URL, init?: RequestInit) => {
      call += 1;
      if (call === 1) return json({ items: [] });
      if (call === 2) return json({
        pluginName: 'github', catalogDigest: digest, sourceCommit: 'c'.repeat(40), policyVersion: 'policy-v1',
        license: { declaration: 'MIT', decision: 'admitted' }, admission: 'admitted', components: catalog().items[0].components,
        status: 'available', credentialSlots: [],
      });
      keys.push(new Headers(init?.headers).get('idempotency-key'));
      throw new TypeError('still uncertain');
    });
    let generated = 0;
    const client = new RowboatPluginApi(fetcher, { createIdempotencyKey: () => `preview-key-${++generated}` });
    const preview = await client.previewInstallation(session, { projectId: 'project-1', pluginName: 'github', catalogDigest: digest });
    await expect(client.installPreview(session, preview)).rejects.toThrow('plugin_api_unavailable');
    await expect(client.installPreview(session, preview)).rejects.toThrow('plugin_api_unavailable');
    expect(keys).toEqual(['preview-key-1', 'preview-key-1', 'preview-key-1', 'preview-key-1']);
    expect(generated).toBe(1);
  });

  it('uses a new idempotency key for a different preview', async () => {
    let call = 0;
    const keys: Array<string | null> = [];
    const fetcher = vi.fn(async (_request: RequestInfo | URL, init?: RequestInit) => {
      call += 1;
      if (call === 1 || call === 3) return json({ items: [] });
      if (call === 2 || call === 4) return json({
        pluginName: 'github', catalogDigest: digest, sourceCommit: 'c'.repeat(40), policyVersion: 'policy-v1',
        license: { declaration: 'MIT', decision: 'admitted' }, admission: 'admitted', components: catalog().items[0].components,
        status: 'available', credentialSlots: [],
      });
      keys.push(new Headers(init?.headers).get('idempotency-key'));
      return json({ type: 'install', receiptId: `receipt-${call}`, projectId: 'project-1', pluginName: 'github', status: 'success', redactions: [] });
    });
    let generated = 0;
    const client = new RowboatPluginApi(fetcher, { createIdempotencyKey: () => `preview-key-${++generated}` });
    const first = await client.previewInstallation(session, { projectId: 'project-1', pluginName: 'github', catalogDigest: digest });
    const second = await client.previewInstallation(session, { projectId: 'project-1', pluginName: 'github', catalogDigest: digest });
    await client.installPreview(session, first);
    await client.installPreview(session, second);
    expect(keys).toEqual(['preview-key-1', 'preview-key-2']);
  });

  it('binds a preview to the account fingerprint and base origin before install fetch', async () => {
    let call = 0;
    const fetcher = vi.fn(async () => {
      call += 1;
      if (call === 1) return json({ items: [] });
      return json({
        pluginName: 'github', catalogDigest: digest, sourceCommit: 'c'.repeat(40), policyVersion: 'policy-v1',
        license: { declaration: 'MIT', decision: 'admitted' }, admission: 'admitted', components: catalog().items[0].components,
        status: 'available', credentialSlots: [],
      });
    });
    const client = new RowboatPluginApi(fetcher);
    const preview = await client.previewInstallation(session, { projectId: 'project-1', pluginName: 'github', catalogDigest: digest });
    await expect(client.installPreview({ ...session, accountFingerprint: 'e'.repeat(64) }, preview)).rejects.toThrow('plugin_api_scope_changed');
    await expect(client.installPreview({ ...session, baseUrl: 'https://other.example/' }, preview)).rejects.toThrow('plugin_api_scope_changed');
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it('returns failed receipt status and reason verbatim without converting it to success', async () => {
    let call = 0;
    const fetcher = vi.fn(async () => {
      call += 1;
      if (call === 1) return json({ items: [] });
      if (call === 2) return json({
        pluginName: 'github', catalogDigest: digest, sourceCommit: 'c'.repeat(40), policyVersion: 'policy-v1',
        license: { declaration: 'MIT', decision: 'admitted' }, admission: 'admitted', components: catalog().items[0].components,
        status: 'available', credentialSlots: [],
      });
      return json({ type: 'install', receiptId: 'receipt-failed', projectId: 'project-1', pluginName: 'github', status: 'failed', reason: 'provider_unavailable', redactions: [] });
    });
    const result = await new RowboatPluginApi(fetcher).previewAndInstall(session, { projectId: 'project-1', pluginName: 'github', catalogDigest: digest });
    expect(result.receipt).toMatchObject({ status: 'failed', reason: 'provider_unavailable' });
  });

  it.each([
    [{ items: [{ ...catalog().items[0], catalogDigest: 'd'.repeat(64) }] }, 'catalog digest drift'],
    [{ items: [catalog().items[0], { ...catalog().items[0], name: 'gitlab', pluginName: 'gitlab', sourceCommit: 'd'.repeat(40) }] }, 'source commit drift'],
    [{ items: [catalog().items[0], { ...catalog().items[0], name: 'gitlab', pluginName: 'gitlab', policyVersion: 'policy-v2' }] }, 'policy drift'],
    [{ items: [{ ...catalog().items[0], admission: 'rejected', reason: 'license_rejected', status: 'available' }] }, 'rejected available contradiction'],
  ])('rejects catalog binding contradiction: %s', async (body) => {
    const client = new RowboatPluginApi(vi.fn(async () => json(body)));
    await expect(client.listCatalog(session, digest)).rejects.toThrow('plugin_api_response_invalid');
  });

  it('rejects cross-project or mutated preview truth before install', async () => {
    const fetcher = vi.fn(async () => json({
      ...catalog().items[0],
      credentialSlots: [],
    }));
    const client = new RowboatPluginApi(fetcher);
    await expect(client.previewAndInstall(session, {
      projectId: 'project/escape', pluginName: 'github', catalogDigest: digest,
    })).rejects.toThrow('plugin_api_request_invalid');
    expect(fetcher).not.toHaveBeenCalled();
  });
});
