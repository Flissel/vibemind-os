# Rowboat Plugin OpenFang Release Implementation Plan (W1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One GitHub MCP tool call that runs end to end from a Rowboat agent: released by an OpenFang approval, authenticated by an OpenFang-held credential, recorded in a redacted receipt that names the approval.

**Architecture:** Rowboat owns two ports — a write-release policy and a credential resolver — and OpenFang is one adapter behind each. The kernel policy is untouched: a write stays `review_required` unless a release decision for that exact call says otherwise, and the release is evaluated per call with the arguments' digest, never cached.

**Tech Stack:** TypeScript strict mode, Vitest, MongoDB replica set, `@rowboat/openai-plugin-runtime`, OpenFang HTTP API on `OPENFANG_URL`.

**Spec:** `docs/superpowers/plans/2026-08-30-rowboat-plugin-full-integration-master.md` (workstream W1) and `spaces/rowboat/rowboat/docs/openai-plugin-runtime-operations.md`.

## Global Constraints

Inherited from the master plan. In addition, verified facts this plan builds on:

- The gate is `spaces/rowboat/rowboat/apps/rowboat/src/application/services/plugin-tool-runtime.ts:562`, which calls `evaluateCapability({ kind: trustedCapability }, DEFAULT_POLICY)`.
- `DEFAULT_POLICY` is `{ allowHttpMcp: true, allowProcessMcp: false, allowCommandHooks: false, allowWriteCapabilities: false, admittedLicenses: ["MIT","Apache-2.0"], rejectedLicenses: ["UNLICENSED"] }`.
- OpenFang exposes `POST /api/approvals` with body `{ agent_id, tool_name, description, action_summary }`, `GET /api/approvals` returning pending plus recent records with a decision of `approved` / `rejected` / `expired`, and `POST /api/approvals/{id}/approve|reject`. `risk_level` is forced to `High` server-side and the timeout comes from OpenFang's policy. There is **no** per-id GET.
- OpenFang has **no** credential issuance API today; secrets live in `~/.openfang/secrets.env`.
- The pinned GitHub MCP component declares `bearer_token_env_var: "GITHUB_PAT_TOKEN"` and is `admitted`; only its sibling skills, app and assets are `review_required`.

---

### Task 1: Emit credential slots from the MCP declaration

Nothing ever produced `component.metadata.credentialSlots`, so `requiredCredentialNames` finds none, every install reports "No credential slots required", and there is no name for a credential to be released against.

**Files:**
- Modify: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/src/import/component-discovery.ts`
- Test: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/test/component-discovery.test.ts`
- Modify (re-pin): `packages/openai-plugin-runtime/src/domain/catalog.ts`, `config/openai-plugin-catalog.lock.json`, `apps/x/apps/renderer/src/lib/rowboat-plugin-api.ts`, `apps/rowboat/test/plugins/plugin-migration-keyset-snapshot.test.ts`, `docs/openai-plugin-runtime-operations.md`

**Interfaces:**
- Consumes: the `mcpServer` metadata field added earlier, which holds the validated declaration.
- Produces: `component.metadata.credentialSlots: readonly string[]` for MCP components, consumed by `requiredCredentialNames` in `apps/rowboat/src/application/use-cases/plugins/plugin-service.shared.ts` and by Task 5.

- [ ] **Step 1: Write the failing test**

In `test/component-discovery.test.ts`, inside the existing `describe`:

```ts
it("names the credential an MCP server needs", async () => {
  const source = await createSource("mcp-credentials", [
    { directoryName: "alpha", license: "MIT", surfaces: ["mcp"] },
  ]);
  const lock = await importCatalog(source.pluginsRoot, options(source));
  const mcp = lock.entries[0]!.components.find(item => item.component.kind === "mcp")!;
  // The fixture declares an http server without a token, so it needs none.
  expect(mcp.component.metadata.credentialSlots).toEqual([]);
});
```

- [ ] **Step 2: Run it and capture RED**

```powershell
npm --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime test -- component-discovery
```

Expected: FAIL, `credentialSlots` is `undefined`.

- [ ] **Step 3: Emit the slot names**

In `component-discovery.ts`, next to the existing `definedFields` helper:

```ts
/**
 * The credential references an MCP server declares. These are *names* the
 * operator binds a value to; the declaration never carries a value.
 */
function credentialSlotNames(declaration: Readonly<Record<string, unknown>>): readonly string[] {
  const names: string[] = [];
  const bearer = declaration.bearer_token_env_var;
  if (typeof bearer === "string" && bearer.length > 0) names.push(bearer);
  const environment = declaration.env;
  if (environment !== null && typeof environment === "object" && !Array.isArray(environment)) {
    for (const key of Object.keys(environment).sort()) {
      const value = (environment as Record<string, unknown>)[key];
      if (typeof value === "string" && value.length > 0) names.push(value);
    }
  }
  return Object.freeze([...new Set(names)].sort());
}
```

and extend the mcp metadata:

```ts
declaration.success
  ? {
    transport: declaration.data.type,
    mcpServer: definedFields(declaration.data),
    credentialSlots: credentialSlotNames(declaration.data),
  }
  : {},
```

- [ ] **Step 4: Add the positive case and run GREEN**

Add to the same test:

```ts
it("names a declared bearer token reference as a credential slot", async () => {
  const source = await createSource("mcp-bearer", [
    { directoryName: "alpha", license: "MIT", surfaces: ["mcp"] },
  ]);
  await writeFile(
    join(source.pluginsRoot, "alpha", ".mcp.json"),
    JSON.stringify({ mcpServers: { sample: { type: "http", url: "https://example.com/mcp", bearer_token_env_var: "SAMPLE_TOKEN" } } }),
  );
  const lock = await importCatalog(source.pluginsRoot, options(source));
  const mcp = lock.entries[0]!.components.find(item => item.component.kind === "mcp")!;
  expect(mcp.component.metadata.credentialSlots).toEqual(["SAMPLE_TOKEN"]);
});
```

```powershell
npm --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime test -- component-discovery
```

Expected: PASS.

- [ ] **Step 5: Re-pin the catalog**

```powershell
npm --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime run build
$env:ROWBOAT_PLUGIN_STORE="<absolute store path>"; $env:ROWBOAT_INVOCATION_ROOT="<worktree root>"
spaces/rowboat/rowboat/packages/openai-plugin-runtime/node_modules/.bin/tsx spaces/rowboat/rowboat/packages/openai-plugin-runtime/scripts/sync-catalog.ts --source "<clone>/plugins" --commit 11c74d6ba24d3a6d48f54a194cd00ef3beea18f9 --output "<worktree root>/spaces/rowboat/rowboat/config/openai-plugin-catalog.lock.json"
```

Take the printed digest and replace the previous one in `packages/openai-plugin-runtime/src/domain/catalog.ts`, `apps/x/apps/renderer/src/lib/rowboat-plugin-api.ts` and the operations doc; update the byte size in `apps/rowboat/test/plugins/plugin-migration-keyset-snapshot.test.ts` line with `openai-plugin-catalog.lock.json`, then rebuild the kernel.

Verify the GitHub server now carries its slot:

```powershell
node -e "const l=require('./spaces/rowboat/rowboat/config/openai-plugin-catalog.lock.json');const g=l.entries.find(e=>e.name==='github');console.log(JSON.stringify(g.components.find(c=>c.component.kind==='mcp').component.metadata.credentialSlots))"
```

Expected: `["GITHUB_PAT_TOKEN"]`.

- [ ] **Step 6: Run both suites and commit**

```powershell
npm --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime test
npm --prefix spaces/rowboat/rowboat/apps/rowboat run test:plugins
git diff --check
git add spaces/rowboat/rowboat
git commit -m "feat(rowboat): name the credential an MCP component needs"
```

---

### Task 2: A write-release port that fails closed

**Files:**
- Create: `spaces/rowboat/rowboat/apps/rowboat/src/application/policies/plugin-write-release.policy.ts`
- Test: `spaces/rowboat/rowboat/apps/rowboat/test/plugins/plugin-write-release.test.ts`

**Interfaces:**
- Produces: `IPluginWriteReleasePolicy.release(request, signal)` returning `PluginWriteReleaseDecision`, consumed by Tasks 3 and 4.

- [ ] **Step 1: Write the failing test**

```ts
import { describe, expect, it } from "vitest";
import { DeniedWriteReleasePolicy, captureWriteReleaseRequest } from "@/src/application/policies/plugin-write-release.policy";

const request = Object.freeze({
  projectId: "11111111-1111-4111-8111-111111111111",
  pluginName: "github",
  toolName: "plugin_github_github",
  componentDigest: "a".repeat(64),
  argumentsDigest: "b".repeat(64),
});

describe("plugin write release", () => {
  it("denies when nothing is configured to release a write", async () => {
    const decision = await new DeniedWriteReleasePolicy().release(request, new AbortController().signal);
    expect(decision).toEqual({ status: "unavailable" });
  });

  it("captures only the fields a release decision may see", () => {
    expect(captureWriteReleaseRequest({ ...request, secret: "sk-live" } as never)).toEqual(request);
    expect(() => captureWriteReleaseRequest({ ...request, projectId: "nope" })).toThrow("write_release_request_invalid");
    expect(() => captureWriteReleaseRequest({ ...request, argumentsDigest: "short" })).toThrow("write_release_request_invalid");
  });
});
```

- [ ] **Step 2: Run it and capture RED**

```powershell
npm --prefix spaces/rowboat/rowboat/apps/rowboat run test:plugins -- plugin-write-release
```

Expected: FAIL, the module does not exist.

- [ ] **Step 3: Write the port**

```ts
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const DIGEST = /^[a-f0-9]{64}$/;

export interface PluginWriteReleaseRequest {
  readonly projectId: string;
  readonly pluginName: string;
  readonly toolName: string;
  readonly componentDigest: string;
  /** Digest of the captured arguments; the values themselves never travel. */
  readonly argumentsDigest: string;
}

export type PluginWriteReleaseDecision =
  | Readonly<{ status: "approved"; approvalId: string }>
  | Readonly<{ status: "denied"; approvalId: string }>
  | Readonly<{ status: "expired"; approvalId: string }>
  | Readonly<{ status: "unavailable" }>;

export interface IPluginWriteReleasePolicy {
  release(request: PluginWriteReleaseRequest, signal: AbortSignal): Promise<PluginWriteReleaseDecision>;
}

export function captureWriteReleaseRequest(input: unknown): PluginWriteReleaseRequest {
  if (input === null || typeof input !== "object") throw new Error("write_release_request_invalid");
  const record = input as Record<string, unknown>;
  const { projectId, pluginName, toolName, componentDigest, argumentsDigest } = record;
  if (typeof projectId !== "string" || !UUID.test(projectId)) throw new Error("write_release_request_invalid");
  if (typeof pluginName !== "string" || !IDENTIFIER.test(pluginName)) throw new Error("write_release_request_invalid");
  if (typeof toolName !== "string" || !IDENTIFIER.test(toolName)) throw new Error("write_release_request_invalid");
  if (typeof componentDigest !== "string" || !DIGEST.test(componentDigest)) throw new Error("write_release_request_invalid");
  if (typeof argumentsDigest !== "string" || !DIGEST.test(argumentsDigest)) throw new Error("write_release_request_invalid");
  return Object.freeze({ projectId, pluginName, toolName, componentDigest, argumentsDigest });
}

/**
 * The composition default. A write is never released by absence of a decision.
 */
export class DeniedWriteReleasePolicy implements IPluginWriteReleasePolicy {
  async release(): Promise<PluginWriteReleaseDecision> {
    return Object.freeze({ status: "unavailable" as const });
  }
}
```

- [ ] **Step 4: Run GREEN and commit**

```powershell
npm --prefix spaces/rowboat/rowboat/apps/rowboat run test:plugins -- plugin-write-release
git diff --check
git add spaces/rowboat/rowboat/apps/rowboat/src/application/policies/plugin-write-release.policy.ts spaces/rowboat/rowboat/apps/rowboat/test/plugins/plugin-write-release.test.ts
git commit -m "feat(rowboat): add a fail-closed write release port"
```

---

### Task 3: The OpenFang release adapter

**Files:**
- Create: `spaces/rowboat/rowboat/apps/rowboat/src/infrastructure/policies/openfang.plugin-write-release.policy.ts`
- Test: `spaces/rowboat/rowboat/apps/rowboat/test/plugins/openfang-write-release.test.ts`

**Interfaces:**
- Consumes: `IPluginWriteReleasePolicy`, `captureWriteReleaseRequest` from Task 2.
- Produces: `OpenFangWriteReleasePolicy`, constructed with `{ baseUrl, fetch, timeoutMs, pollIntervalMs, now }`, consumed by Task 4.

- [ ] **Step 1: Write the failing tests**

```ts
import { describe, expect, it } from "vitest";
import { OpenFangWriteReleasePolicy } from "@/src/infrastructure/policies/openfang.plugin-write-release.policy";

const request = Object.freeze({
  projectId: "11111111-1111-4111-8111-111111111111",
  pluginName: "github",
  toolName: "plugin_github_github",
  componentDigest: "a".repeat(64),
  argumentsDigest: "b".repeat(64),
});

function policyWith(responses: readonly unknown[], calls: string[] = []) {
  let index = 0;
  const fetchImpl = async (input: string, init?: { method?: string; body?: string }) => {
    calls.push(`${init?.method ?? "GET"} ${input}`);
    const body = responses[Math.min(index++, responses.length - 1)];
    return { ok: true, status: 200, json: async () => body } as Response;
  };
  return new OpenFangWriteReleasePolicy({
    baseUrl: "http://openfang.invalid:4200",
    fetch: fetchImpl as unknown as typeof fetch,
    timeoutMs: 1_000,
    pollIntervalMs: 1,
  });
}

describe("OpenFang write release", () => {
  it("creates one request and returns the approval decision", async () => {
    const calls: string[] = [];
    const policy = policyWith([
      { id: "3f0f8a1e-0000-4000-8000-000000000001" },
      { approvals: [{ id: "3f0f8a1e-0000-4000-8000-000000000001", status: "approved" }] },
    ], calls);
    expect(await policy.release(request, new AbortController().signal))
      .toEqual({ status: "approved", approvalId: "3f0f8a1e-0000-4000-8000-000000000001" });
    expect(calls[0]).toBe("POST http://openfang.invalid:4200/api/approvals");
    expect(calls[1]).toBe("GET http://openfang.invalid:4200/api/approvals");
  });

  it("reports a rejection and an expiry as themselves", async () => {
    for (const [status, expected] of [["rejected", "denied"], ["expired", "expired"]] as const) {
      const policy = policyWith([
        { id: "3f0f8a1e-0000-4000-8000-000000000002" },
        { approvals: [{ id: "3f0f8a1e-0000-4000-8000-000000000002", status }] },
      ]);
      expect((await policy.release(request, new AbortController().signal)).status).toBe(expected);
    }
  });

  it("never sends argument values, only their digest", async () => {
    const bodies: string[] = [];
    const fetchImpl = async (_input: string, init?: { body?: string }) => {
      if (init?.body !== undefined) bodies.push(init.body);
      return { ok: true, status: 200, json: async () => ({ id: "3f0f8a1e-0000-4000-8000-000000000003", approvals: [{ id: "3f0f8a1e-0000-4000-8000-000000000003", status: "approved" }] }) } as Response;
    };
    const policy = new OpenFangWriteReleasePolicy({ baseUrl: "http://openfang.invalid:4200", fetch: fetchImpl as unknown as typeof fetch, timeoutMs: 1_000, pollIntervalMs: 1 });
    await policy.release(request, new AbortController().signal);
    expect(bodies[0]).toContain(request.argumentsDigest);
    expect(bodies[0]).not.toContain("sk-");
    expect(JSON.parse(bodies[0]!)).toMatchObject({ agent_id: request.projectId, tool_name: request.toolName });
  });

  it("is unavailable when OpenFang cannot be reached or never decides", async () => {
    const unreachable = new OpenFangWriteReleasePolicy({
      baseUrl: "http://openfang.invalid:4200",
      fetch: (async () => { throw new Error("ECONNREFUSED"); }) as unknown as typeof fetch,
      timeoutMs: 50, pollIntervalMs: 1,
    });
    expect(await unreachable.release(request, new AbortController().signal)).toEqual({ status: "unavailable" });

    const undecided = policyWith([
      { id: "3f0f8a1e-0000-4000-8000-000000000004" },
      { approvals: [{ id: "3f0f8a1e-0000-4000-8000-000000000004", status: "pending" }] },
    ]);
    expect((await undecided.release(request, new AbortController().signal)).status).toBe("expired");
  });
});
```

- [ ] **Step 2: Run and capture RED**

```powershell
npm --prefix spaces/rowboat/rowboat/apps/rowboat run test:plugins -- openfang-write-release
```

Expected: FAIL, the module does not exist.

- [ ] **Step 3: Write the adapter**

```ts
import { captureWriteReleaseRequest, type IPluginWriteReleasePolicy, type PluginWriteReleaseDecision, type PluginWriteReleaseRequest } from "@/src/application/policies/plugin-write-release.policy";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export interface OpenFangWriteReleaseOptions {
  readonly baseUrl: string;
  readonly fetch: typeof fetch;
  readonly timeoutMs: number;
  readonly pollIntervalMs: number;
  readonly now?: () => number;
}

/**
 * Asks OpenFang to release one write.
 *
 * OpenFang models an approval as a request that a human resolves or that times
 * out, and it exposes no per-id read, so the decision is polled from the list.
 * Only identifiers and digests travel: the arguments themselves never leave
 * this process.
 */
export class OpenFangWriteReleasePolicy implements IPluginWriteReleasePolicy {
  readonly #options: OpenFangWriteReleaseOptions;

  constructor(options: OpenFangWriteReleaseOptions) {
    this.#options = options;
  }

  async release(input: PluginWriteReleaseRequest, signal: AbortSignal): Promise<PluginWriteReleaseDecision> {
    const request = captureWriteReleaseRequest(input);
    const now = this.#options.now ?? (() => Date.now());
    const deadline = now() + this.#options.timeoutMs;
    let approvalId: string;
    try {
      const created = await this.#json(`${this.#options.baseUrl}/api/approvals`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          agent_id: request.projectId,
          tool_name: request.toolName,
          description: `Rowboat plugin write: ${request.pluginName}`,
          action_summary: `component ${request.componentDigest.slice(0, 12)} arguments ${request.argumentsDigest.slice(0, 12)}`,
        }),
        signal,
      });
      const id = (created as { id?: unknown }).id;
      if (typeof id !== "string" || !UUID.test(id)) return Object.freeze({ status: "unavailable" as const });
      approvalId = id;
    } catch {
      return Object.freeze({ status: "unavailable" as const });
    }

    while (now() < deadline) {
      try {
        const listed = await this.#json(`${this.#options.baseUrl}/api/approvals`, { method: "GET", signal });
        const approvals = (listed as { approvals?: unknown }).approvals;
        const record = Array.isArray(approvals)
          ? approvals.find(candidate => candidate !== null && typeof candidate === "object" && (candidate as { id?: unknown }).id === approvalId)
          : undefined;
        const status = record === undefined ? undefined : (record as { status?: unknown }).status;
        if (status === "approved") return Object.freeze({ status: "approved" as const, approvalId });
        if (status === "rejected") return Object.freeze({ status: "denied" as const, approvalId });
        if (status === "expired") return Object.freeze({ status: "expired" as const, approvalId });
      } catch {
        return Object.freeze({ status: "unavailable" as const });
      }
      await new Promise(resolve => setTimeout(resolve, this.#options.pollIntervalMs));
    }
    // No decision inside our own window is the same as no release.
    return Object.freeze({ status: "expired" as const, approvalId });
  }

  async #json(url: string, init: RequestInit): Promise<unknown> {
    const response = await this.#options.fetch(url, init);
    if (!response.ok) throw new Error("openfang_unavailable");
    return response.json();
  }
}
```

- [ ] **Step 4: Run GREEN and commit**

```powershell
npm --prefix spaces/rowboat/rowboat/apps/rowboat run test:plugins -- openfang-write-release
git diff --check
git add spaces/rowboat/rowboat/apps/rowboat/src/infrastructure/policies/openfang.plugin-write-release.policy.ts spaces/rowboat/rowboat/apps/rowboat/test/plugins/openfang-write-release.test.ts
git commit -m "feat(rowboat): release a plugin write through OpenFang"
```

---

### Task 4: Let a released write through the runtime gate

**Files:**
- Modify: `spaces/rowboat/rowboat/apps/rowboat/src/application/services/plugin-tool-runtime.ts` (the gate at the `evaluateCapability` call)
- Modify: `spaces/rowboat/rowboat/apps/rowboat/di/plugins-container.ts`
- Test: `spaces/rowboat/rowboat/apps/rowboat/test/plugins/plugin-tool-runtime.test.ts`

**Interfaces:**
- Consumes: `IPluginWriteReleasePolicy` from Task 2, `OpenFangWriteReleasePolicy` from Task 3.
- Produces: `PluginToolRuntimeDependencies.releaseWrite?: IPluginWriteReleasePolicy["release"]`, and an `approvalId` recorded on the execution receipt.

- [ ] **Step 1: Write the failing tests**

The file already has a `setup(options)` helper whose options include
`operationCapability?: "read" | "write"`, `providerAvailable?`, `credentialFailure?`
and others. Extend that options type with `releaseWrite?: PluginToolRuntimeDependencies["releaseWrite"]`
and pass it straight through to the `PluginToolRuntime` it constructs, then add:

```ts
it("runs a write that OpenFang released and records the approval", async () => {
  const seen: unknown[] = [];
  const state = setup({
    operationCapability: "write",
    releaseWrite: async (request: unknown) => { seen.push(request); return { status: "approved", approvalId: "3f0f8a1e-0000-4000-8000-000000000005" }; },
  });
  const result = await state.runtime.invoke(binding, { query: "safe" }, { projectId: "project-1", operationName: "lookup" });
  expect(result).toEqual({ status: "success", output: { ok: true } });
  expect(seen).toHaveLength(1);
  expect(state.repository.receipts[0]).toMatchObject({ status: "success" });
  expect(JSON.stringify(state.repository.receipts[0])).toContain("3f0f8a1e-0000-4000-8000-000000000005");
});

it("keeps refusing a write that was denied, expired, or never released", async () => {
  for (const decision of [
    { status: "denied", approvalId: "3f0f8a1e-0000-4000-8000-000000000006" },
    { status: "expired", approvalId: "3f0f8a1e-0000-4000-8000-000000000007" },
    { status: "unavailable" },
  ]) {
    const state = setup({ operationCapability: "write", releaseWrite: async () => decision });
    await expect(state.runtime.invoke(binding, {}, { projectId: "project-1", operationName: "lookup" }))
      .rejects.toThrow("write_review_required");
  }
});

it("never asks for a release for a read", async () => {
  let asked = 0;
  const state = setup({ operationCapability: "read", releaseWrite: async () => { asked += 1; return { status: "approved", approvalId: "3f0f8a1e-0000-4000-8000-000000000008" }; } });
  await state.runtime.invoke({ ...binding, capability: "read" }, {}, { projectId: "project-1", operationName: "lookup" });
  expect(asked).toBe(0);
});
```

- [ ] **Step 2: Run and capture RED**

```powershell
npm --prefix spaces/rowboat/rowboat/apps/rowboat run test:plugins -- plugin-tool-runtime
```

Expected: FAIL, `releaseWrite` is not a dependency and a write is refused.

- [ ] **Step 3: Add the release step to the gate**

In `plugin-tool-runtime.ts`, extend the dependencies:

```ts
  /**
   * Releases one write. A write stays under review unless a decision for this
   * exact call approves it; the decision is never cached.
   */
  readonly releaseWrite?: (
    request: Readonly<{ projectId: string; pluginName: string; toolName: string; componentDigest: string; argumentsDigest: string }>,
    signal: AbortSignal,
  ) => Promise<Readonly<{ status: "approved" | "denied" | "expired" | "unavailable"; approvalId?: string }>>;
```

and replace the single `evaluateCapability` line with:

```ts
    let policy = DEFAULT_POLICY;
    let approvalId: string | undefined;
    if (trustedCapability === "write" && this.#dependencies.releaseWrite !== undefined) {
      const decision = await this.#dependencies.releaseWrite(Object.freeze({
        projectId: context.projectId,
        pluginName: binding.pluginName,
        toolName: context.operationName,
        componentDigest: binding.componentDigest,
        argumentsDigest,
      }), controller.signal);
      if (decision.status === "approved" && typeof decision.approvalId === "string") {
        // Released for this call only: the policy copy never leaves this scope.
        policy = Object.freeze({ ...DEFAULT_POLICY, allowWriteCapabilities: true });
        approvalId = decision.approvalId;
      }
    }
    const capabilityDecision = evaluateCapability({ kind: trustedCapability }, policy);
```

`argumentsDigest` is the digest the runtime already computes for the receipt; if it is computed later in the method, hoist that computation above this block without changing how it is computed. Record the approval on the receipt where the receipt is built:

```ts
      ...(approvalId === undefined ? {} : { output: { ...receiptOutput, approvalId } }),
```

- [ ] **Step 4: Run GREEN**

```powershell
npm --prefix spaces/rowboat/rowboat/apps/rowboat run test:plugins -- plugin-tool-runtime
```

Expected: PASS, including the pre-existing cases — a write with no `releaseWrite` configured must still fail with `write_review_required`.

- [ ] **Step 5: Wire the adapter into the composition**

In `di/plugins-container.ts`, inside `createToolRuntime`:

```ts
      releaseWrite: async (request, signal) => {
        const url = process.env.OPENFANG_URL;
        if (url === undefined || url.length === 0) return { status: "unavailable" as const };
        const { OpenFangWriteReleasePolicy } = await import("@/src/infrastructure/policies/openfang.plugin-write-release.policy");
        return new OpenFangWriteReleasePolicy({
          baseUrl: url,
          fetch,
          timeoutMs: Number.parseInt(process.env.OPENFANG_APPROVAL_TIMEOUT_MS ?? "120000", 10),
          pollIntervalMs: 1_000,
        }).release(request, signal);
      },
```

- [ ] **Step 6: Run the full suite and commit**

```powershell
npm --prefix spaces/rowboat/rowboat/apps/rowboat run test:plugins
npx --prefix spaces/rowboat/rowboat/apps/rowboat tsc --noEmit
git diff --check
git add spaces/rowboat/rowboat/apps/rowboat
git commit -m "feat(rowboat): let an OpenFang-released write through the runtime gate"
```

Expected from `tsc`: only the six pre-existing missing-asset errors (`public/logo.png`, `public/logo-only.png`, `public/mascot.png`).

---

### Task 5: Decide and implement the credential transport

**This task stops for a decision before it writes code.** OpenFang has no credential issuance API; its secrets live in `~/.openfang/secrets.env`.

**Files:**
- Create: `spaces/rowboat/rowboat/apps/rowboat/src/infrastructure/plugins/openfang-credential-resolver.ts`
- Modify: `spaces/rowboat/rowboat/apps/rowboat/src/infrastructure/plugins/provider-resolution.ts` (accept the resolver), `di/plugins-container.ts`
- Test: `spaces/rowboat/rowboat/apps/rowboat/test/plugins/openfang-credential-resolver.test.ts`
- Possibly modify: `openfang/crates/openfang-api/src/routes.rs` and `server.rs` (option 2 only)

- [ ] **Step 1: Put the decision to the user**

Show them both options with their consequences, and do not proceed until one is chosen:

1. **Deploy-time injection.** OpenFang's secret store fills the Rowboat process environment; the resolver maps the slot name to `process.env[name]`. No OpenFang change; the secret is resident for the process lifetime; rotation needs a restart.
2. **Call-time issuance.** A new OpenFang endpoint returns a short-lived value for a named reference; the resolver fetches per call. Requires a change in the `openfang` submodule; no standing secret in Rowboat; revocation is immediate.

- [ ] **Step 2: Write the failing test for the chosen option**

For option 1:

```ts
it("resolves a slot from the released environment and never logs it", async () => {
  const resolver = new EnvironmentCredentialResolver({ read: name => name === "GITHUB_PAT_TOKEN" ? "ghp_secret" : undefined });
  const secret = await resolver.resolve({ name: "GITHUB_PAT_TOKEN" } as never, "project-1");
  expect(revealSecretValue(secret)).toBe("ghp_secret");
  expect(JSON.stringify(secret)).not.toContain("ghp_secret");
});

it("fails closed for a slot nothing released", async () => {
  const resolver = new EnvironmentCredentialResolver({ read: () => undefined });
  await expect(resolver.resolve({ name: "GITHUB_PAT_TOKEN" } as never, "project-1")).rejects.toThrow("credential_missing");
});
```

For option 2, the same two cases against an injected `fetch`, plus one asserting that a non-200 response is `credential_missing` rather than an empty secret.

- [ ] **Step 3: Run and capture RED**

```powershell
npm --prefix spaces/rowboat/rowboat/apps/rowboat run test:plugins -- openfang-credential-resolver
```

- [ ] **Step 4: Implement the resolver**

It implements the kernel's `CredentialResolver` interface, returns `createSecretValue(value)`, and throws `credential_missing` when nothing is released. Replace `UnreleasedCredentialResolver` in `provider-resolution.ts`'s container call site with the chosen resolver; keep `UnreleasedCredentialResolver` as the default when nothing is configured.

- [ ] **Step 5: Run GREEN and commit**

```powershell
npm --prefix spaces/rowboat/rowboat/apps/rowboat run test:plugins
git diff --check
git add spaces/rowboat/rowboat
git commit -m "feat(rowboat): resolve a plugin credential released by OpenFang"
```

---

### Task 6: Classify operations so reads stop asking for a release

**Files:**
- Create: `spaces/rowboat/rowboat/apps/rowboat/src/application/services/plugin-operation-classifier.ts`
- Modify: `spaces/rowboat/rowboat/apps/rowboat/di/plugins-container.ts`
- Test: `spaces/rowboat/rowboat/apps/rowboat/test/plugins/plugin-operation-classifier.test.ts`

**Interfaces:**
- Produces: `classifyPluginOperation(input): "read" | "write"`, used as `classifyOperation` in the container.

- [ ] **Step 1: Write the failing test**

```ts
import { describe, expect, it } from "vitest";
import { classifyPluginOperation } from "@/src/application/services/plugin-operation-classifier";

const component = { id: "mcp:.mcp.json#github", name: "github", kind: "mcp", status: "available", metadata: { digest: "a".repeat(64), bindingDigest: "b".repeat(64) } };

describe("plugin operation classification", () => {
  it("classifies an operation the component declares read-only as read", () => {
    const readOnly = { ...component, metadata: { ...component.metadata, readOnlyOperations: ["list_issues", "search"] } };
    expect(classifyPluginOperation({ pluginName: "github", component: readOnly, operationName: "list_issues" })).toBe("read");
    expect(classifyPluginOperation({ pluginName: "github", component: readOnly, operationName: "create_issue" })).toBe("write");
  });

  it("classifies everything else as write", () => {
    expect(classifyPluginOperation({ pluginName: "github", component, operationName: "list_issues" })).toBe("write");
    expect(classifyPluginOperation({ pluginName: "github", component, operationName: "anything" })).toBe("write");
  });

  it("ignores an unreadable declaration rather than trusting it", () => {
    for (const declared of ["list_issues", { list_issues: true }, [1, 2], null]) {
      const broken = { ...component, metadata: { ...component.metadata, readOnlyOperations: declared } };
      expect(classifyPluginOperation({ pluginName: "github", component: broken, operationName: "list_issues" })).toBe("write");
    }
  });
});
```

- [ ] **Step 2: Run and capture RED**

```powershell
npm --prefix spaces/rowboat/rowboat/apps/rowboat run test:plugins -- plugin-operation-classifier
```

- [ ] **Step 3: Implement the classifier**

```ts
/**
 * Only an explicit, structurally valid read-only declaration makes an operation
 * a read. Anything else - absent, malformed, or unknown - is a write, because a
 * wrong read classification is the one that runs a side effect without release.
 */
export function classifyPluginOperation(input: Readonly<{
  pluginName: string;
  component: Readonly<{ metadata: Readonly<Record<string, unknown>> }>;
  operationName: string;
}>): "read" | "write" {
  const declared = input.component.metadata.readOnlyOperations;
  if (!Array.isArray(declared)) return "write";
  return declared.every(entry => typeof entry === "string") && declared.includes(input.operationName) ? "read" : "write";
}
```

- [ ] **Step 4: Use it in the composition**

In `di/plugins-container.ts` replace `classifyOperation: () => "write" as const` with `classifyOperation: input => classifyPluginOperation(input)`, importing it statically.

Note for the reviewer: the pinned catalog declares no `readOnlyOperations` today, so this changes nothing observable yet — it removes the hard-coded lie and gives the importer somewhere to put a declaration when the MCP tool list is imported. Do not invent read-only names for plugins here.

- [ ] **Step 5: Run GREEN and commit**

```powershell
npm --prefix spaces/rowboat/rowboat/apps/rowboat run test:plugins
git diff --check
git add spaces/rowboat/rowboat/apps/rowboat
git commit -m "feat(rowboat): classify a plugin operation instead of assuming write"
```

---

### Task 7: Prove one real call and record the evidence

**Files:**
- Modify: `spaces/rowboat/rowboat/apps/rowboat/test/plugins/live-mongo-runtime.test.ts`
- Modify: `spaces/rowboat/rowboat/docs/openai-plugin-runtime-operations.md`
- Modify: `spaces/rowboat/rowboat/apps/rowboat/src/application/services/plugin-runtime-completion.ts` (bind the new evidence)

- [ ] **Step 1: Extend the live gate to the released path**

Replace the existing expectation that an invocation fails with `write_review_required` by a two-part check: with no release configured it still fails that way, and with a stub release that approves, the call proceeds past the gate and fails only on the credential:

```ts
    const releasedRuntime = new PluginToolRuntime({
      ...runtimeDependencies,
      releaseWrite: async () => ({ status: "approved" as const, approvalId: "3f0f8a1e-0000-4000-8000-00000000000a" }),
    });
    let releasedError = "none";
    try {
      await releasedRuntime.invoke(executionBinding, { query: "issues" }, { projectId, operationName: "list_issues" });
    } catch (error) {
      releasedError = error instanceof Error ? error.message : "unknown";
    }
    log(`released write reaches the credential: ${releasedError}`);
    expect(releasedError).toBe("credential_missing");
```

- [ ] **Step 2: Run the live gate**

```powershell
docker run -d --name rowboat-rs -p 127.0.0.1:27017:27017 mongo:7 --replSet rs0 --bind_ip_all
docker exec rowboat-rs mongosh --quiet --eval 'rs.initiate({_id:"rs0",members:[{_id:0,host:"127.0.0.1:27017"}]})'
$env:ROWBOAT_LIVE_MONGO_URL="mongodb://127.0.0.1:27017/rowboat"
npx --prefix spaces/rowboat/rowboat/apps/rowboat vitest run test/plugins/live-mongo-runtime.test.ts
```

Expected: PASS, with the log line showing `credential_missing`.

- [ ] **Step 3: Run the end-to-end call against a real credential**

With the chosen credential transport configured and a real `GITHUB_PAT_TOKEN` released, and an OpenFang reachable at `OPENFANG_URL`, invoke the tool once from the UI playground and approve the request in OpenFang. Record in the operations doc: the approval id, the receipt id, and that the result came back. If the call fails, record the exact failure instead — an unfinished last mile is a finding, not a reason to soften the claim.

- [ ] **Step 4: Bind the new evidence and refresh the artifacts**

Add the new test names to `REQUIREMENT_EVIDENCE` under R4 (secret handling) and R8 (MCP servers) in `plugin-runtime-completion.ts`, then:

```powershell
npm --prefix spaces/rowboat/rowboat/apps/rowboat run plugins:evidence
npm --prefix spaces/rowboat/rowboat/apps/rowboat run plugins:evidence
```

- [ ] **Step 5: Commit**

```powershell
git diff --check
git add spaces/rowboat/rowboat
git commit -m "docs(rowboat): record the released plugin call"
```

## Phase completion gate

- [ ] A credential slot name exists for every MCP component that declares one, and the pin was updated for it.
- [ ] A write is never released by the absence of a decision, and the composition default denies.
- [ ] The OpenFang adapter sends identifiers and digests only, never argument values.
- [ ] A released write runs and its approval id is on the receipt; a denied, expired, or unavailable decision keeps failing with `write_review_required`.
- [ ] A read never asks for a release.
- [ ] The credential transport was decided by the user before any resolver was written.
- [ ] The live gate shows a released write reaching `credential_missing`, and the end-to-end result is recorded truthfully either way.
- [ ] Unrelated baseline failures stay unclaimed: the six missing-asset typecheck errors and the Desktop root ESLint run.
