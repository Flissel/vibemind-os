# Rowboat OpenAI Plugin Runtime Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every OpenAI plugin component explicit, tested Rowboat semantics without silently granting execution authority.

**Architecture:** Pure normalizers turn source components into canonical descriptors. A provider registry resolves admitted external capabilities; HTTP/process MCP and hooks execute only through injected, policy-checked runners. Resolution and receipts remain secret-free.

**Tech Stack:** TypeScript strict mode, Zod, YAML, Vitest, MCP TypeScript SDK, Node child_process, React-free runtime package.

---

### Task 1: Normalize skills, agents, commands, and assets

**Files:**
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/src/components/skill-normalizer.ts`
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/src/components/agent-normalizer.ts`
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/src/components/command-normalizer.ts`
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/src/components/asset-normalizer.ts`
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/test/instruction-components.test.ts`

- [ ] **Step 1: Write RED tests for instruction and asset boundaries**

```ts
it("loads a complete skill and contains every referenced resource", async () => {
  const skill = await normalizeSkill(skillRoot, pluginRoot);
  expect(skill.instructions).toContain("# Skill");
  expect(skill.resources.map((item) => item.path)).toEqual(["references/rules.md"]);
});

it("turns only markdown command files into user-invoked actions", async () => {
  const commands = await normalizeCommands(commandsRoot, pluginRoot);
  expect(commands.find((item) => item.name === "_conventions")).toBeUndefined();
  expect(commands.every((item) => item.invocation === "explicit_user")).toBe(true);
});

it.each(["text/html", "image/svg+xml"])("rejects active asset MIME %s", async (mime) => {
  await expect(normalizeAsset(activeAsset, pluginRoot, { mime })).rejects.toThrow("asset_unsafe");
});
```

- [ ] **Step 2: Run RED**

```powershell
npm --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime test -- instruction-components
```

Expected: FAIL because the normalizers do not exist.

- [ ] **Step 3: Implement deterministic descriptors**

```ts
export interface NormalizedSkill {
  name: string;
  description: string;
  instructions: string;
  triggerRules: readonly string[];
  resources: readonly { path: string; digest: string }[];
}

export interface NormalizedCommand {
  name: string;
  instructions: string;
  invocation: "explicit_user";
  resources: readonly { path: string; digest: string }[];
}
```

Parse skill YAML frontmatter, read the full selected `SKILL.md`, and resolve
directly referenced local resources through `resolveContainedPath`. Parse
`agents/openai.yaml` as composer metadata and agent Markdown as immutable
templates. Treat `_conventions.md`, `*.tmpl`, and non-Markdown files as command
resources. Admit raster images and bounded plain text only; return a
Rowboat-owned placeholder descriptor for missing optional assets.

- [ ] **Step 4: Run GREEN and full-catalog normalization**

```powershell
npm --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime test -- instruction-components
npm --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime test
npm --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime run typecheck
```

Expected: focused and full package gates pass.

- [ ] **Step 5: Commit instruction components**

```powershell
git add spaces/rowboat/rowboat/packages/openai-plugin-runtime
git commit -m "feat(rowboat): normalize plugin instruction components"
```

### Task 2: Define MCP, app, and provider resolution contracts

**Files:**
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/src/providers/provider.ts`
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/src/providers/provider-registry.ts`
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/src/components/mcp-normalizer.ts`
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/src/components/app-normalizer.ts`
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/src/providers/temporary-adapters.ts`
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/test/provider-registry.test.ts`

- [ ] **Step 1: Write fail-closed registry RED tests**

```ts
it("returns unavailable instead of falling back for an unknown connector", () => {
  const result = registry.resolve({ kind: "app", connectorId: "connector_deadbeef" });
  expect(result).toEqual({ status: "unavailable", reason: "provider_unavailable" });
  expect(nativeProvider.calls).toBe(0);
});

it("resolves only an exact admitted provider binding", () => {
  registry.register("connector_abc123", admittedProvider);
  expect(registry.resolve({ kind: "app", connectorId: "connector_abc123" }).status).toBe("available");
});
```

- [ ] **Step 2: Run RED**

```powershell
npm --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime test -- provider-registry
```

Expected: FAIL because registry contracts are absent.

- [ ] **Step 3: Implement exact provider bindings**

```ts
export interface ProviderRequest {
  projectId: string;
  pluginName: string;
  componentName: string;
  capability: "read" | "write";
  arguments: Readonly<Record<string, unknown>>;
}

export interface PluginProvider {
  readonly id: string;
  describe(): ProviderDescriptor;
  invoke(request: ProviderRequest, context: ProviderContext): Promise<ProviderResult>;
}

export class ProviderRegistry {
  private readonly bindings = new Map<string, PluginProvider>();

  register(bindingId: string, provider: PluginProvider): void {
    if (this.bindings.has(bindingId)) throw new Error(`provider_duplicate:${bindingId}`);
    this.bindings.set(bindingId, provider);
  }

  resolve(binding: ProviderBinding): ProviderResolution {
    const provider = this.bindings.get(binding.id);
    return provider
      ? { status: "available", provider }
      : { status: "unavailable", reason: "provider_unavailable" };
  }
}
```

Normalize each MCP server to `mcp-http` or `mcp-process`; normalize each app to
its exact connector ID. Do not pair an app with an MCP server by name alone.
Pairing is an explicit catalog policy binding carrying both component digests.

Register the provider kinds `mcp-http`, `mcp-process`, `rowboat-native`,
`legacy-composio-adapter`, and the reserved-but-unavailable
`openai-connector-bridge`. Define explicit temporary binding descriptors for
Reddit, X/Twitter, Google Drive/Sheets, admitted search, and mock tools. These
bindings remain behind the OpenAI-compatible registry, carry
`temporaryAdapter: true` in receipts, and are not exposed as public legacy
plugin types. A migration may select one only by its exact binding ID.

- [ ] **Step 4: Run GREEN**

```powershell
npm --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime test -- provider-registry
npm --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime run typecheck
```

Expected: exact match succeeds; unknown and duplicate bindings fail closed.

- [ ] **Step 5: Commit provider contracts**

```powershell
git add spaces/rowboat/rowboat/packages/openai-plugin-runtime
git commit -m "feat(rowboat): add plugin provider registry"
```

### Task 3: Implement admitted HTTP and process MCP providers

**Files:**
- Modify: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/package.json`
- Modify: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/package-lock.json`
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/src/providers/mcp-http-provider.ts`
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/src/providers/mcp-process-provider.ts`
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/src/providers/credential-resolver.ts`
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/test/mcp-providers.test.ts`

- [ ] **Step 1: Write transport and no-call RED tests**

```ts
it("uses Streamable HTTP and closes the client", async () => {
  const result = await provider.invoke(request, context);
  expect(result.status).toBe("success");
  expect(client.connectTransports).toEqual(["streamable-http"]);
  expect(client.closeCalls).toBe(1);
});

it("uses SSE only after a classified compatibility failure", async () => {
  streamable.connectError = new McpCompatibilityError("unsupported_transport");
  await provider.invoke(request, context);
  expect(client.connectTransports).toEqual(["streamable-http", "sse"]);
});

it("never spawns a rejected process provider", async () => {
  await expect(processProvider.invoke(request, rejectedContext)).rejects.toThrow("process_not_admitted");
  expect(spawner.calls).toBe(0);
});
```

- [ ] **Step 2: Run RED**

```powershell
npm --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime test -- mcp-providers
```

Expected: FAIL because the providers are absent.

- [ ] **Step 3: Implement controlled providers and credential references**

Add `@modelcontextprotocol/sdk` to runtime dependencies. Implement HTTP with
injected client/transport factories, validated URL policy, exact OAuth or
bearer-token reference resolution, and `finally { await client.close(); }`.

```ts
export interface CredentialResolver {
  resolve(reference: CredentialReference, projectId: string): Promise<SecretValue>;
}

export interface ProcessSpawner {
  spawn(command: string, args: readonly string[], options: SafeSpawnOptions): SpawnedProcess;
}
```

Process execution must use `shell: false`, a contained working directory, a
minimal environment built only from admitted references, bounded stdout/stderr,
and an enforced timeout. Never log `SecretValue.value` or request arguments
classified sensitive.

- [ ] **Step 4: Run GREEN with call counters**

```powershell
npm --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime test -- mcp-providers
npm --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime run typecheck
```

Expected: transport, cleanup, timeout, redaction, and zero-call guards pass.

- [ ] **Step 5: Commit MCP providers**

```powershell
git add spaces/rowboat/rowboat/packages/openai-plugin-runtime
git commit -m "feat(rowboat): execute admitted plugin MCP providers"
```

### Task 4: Map and execute policy-controlled hooks

**Files:**
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/src/hooks/hook-event.ts`
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/src/hooks/hook-matcher.ts`
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/src/hooks/hook-runner.ts`
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/test/hooks.test.ts`

- [ ] **Step 1: Write matcher, denial, and failure-preservation RED tests**

```ts
it("maps pinned PostToolUse and Stop events", () => {
  expect(normalizeHookEvent("PostToolUse")).toBe("post_tool_use");
  expect(normalizeHookEvent("Stop")).toBe("stop");
});

it("does not run a command hook without admission", async () => {
  await expect(runner.run(hook, event, deniedContext)).rejects.toThrow("hook_not_admitted");
  expect(spawner.calls).toBe(0);
});

it("cannot convert a failed tool outcome into success", async () => {
  const result = await runner.run(admittedHook, failedToolEvent, admittedContext);
  expect(result.parentOutcome).toBe("failed");
});
```

- [ ] **Step 2: Run RED**

```powershell
npm --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime test -- hooks
```

Expected: FAIL because hook semantics are absent.

- [ ] **Step 3: Implement canonical events and the admitted runner**

```ts
export type HookEventType = "post_tool_use" | "stop";

export interface HookExecutionReceipt {
  event: HookEventType;
  matcher: string | null;
  status: "success" | "failed" | "denied" | "timed_out";
  parentOutcome: "success" | "failed";
  outputDigest?: string;
}
```

Compile matchers against canonical action names. Preserve unknown source events
as `component_unsupported`. Command hooks use the same safe process boundary as
process MCP, with plugin-root cwd, minimal environment, timeout, bounded output,
and secret-free receipts.

- [ ] **Step 4: Run GREEN**

```powershell
npm --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime test -- hooks
npm --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime run typecheck
```

Expected: event mapping, matchers, denial, timeout, and failure preservation pass.

- [ ] **Step 5: Commit hooks**

```powershell
git add spaces/rowboat/rowboat/packages/openai-plugin-runtime
git commit -m "feat(rowboat): enforce plugin hook lifecycle policy"
```

### Task 5: Resolve installed plugins and build secret-free receipts

**Files:**
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/src/domain/installation.ts`
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/src/domain/receipt.ts`
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/src/resolution/plugin-resolver.ts`
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/src/receipts/receipt-builder.ts`
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/src/security/redact.ts`
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/test/resolver-receipts.test.ts`

- [ ] **Step 1: Write partial-availability and redaction RED tests**

```ts
it("resolves independent skills while an app provider is unavailable", () => {
  const resolved = resolveInstallation(installation, catalog, registry, policy);
  expect(resolved.status).toBe("partially_available");
  expect(resolved.skills).toHaveLength(1);
  expect(resolved.apps[0]).toMatchObject({ status: "unavailable", reason: "provider_unavailable" });
});

it("omits secret values from receipts", () => {
  const receipt = buildReceipt(eventContainingSecret, sensitivePaths);
  expect(JSON.stringify(receipt)).not.toContain("super-secret-value");
  expect(receipt.redactions).toContain("credential.value");
});
```

- [ ] **Step 2: Run RED**

```powershell
npm --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime test -- resolver-receipts
```

Expected: FAIL because resolution and receipt functions do not exist.

- [ ] **Step 3: Implement deterministic resolution and bounded receipts**

```ts
export interface PluginInstallation {
  id: string;
  projectId: string;
  pluginName: string;
  pluginVersion: string;
  sourceCommit: string;
  manifestDigest: string;
  treeDigest: string;
  policyVersion: string;
  enabled: boolean;
  revision: number;
}
```

Resolution must validate all recorded digests against the catalog, evaluate
plugin license before components, keep component reason codes, and return
immutable arrays sorted by kind and name. Receipt building allowlists fields,
replaces sensitive values with `[REDACTED]`, truncates bounded output, and hashes
full output when truncation occurs.

- [ ] **Step 4: Run the Phase 2 gate**

```powershell
npm --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime test
npm --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime run typecheck
git diff --check
```

Expected: all package tests and typecheck pass.

- [ ] **Step 5: Commit resolution**

```powershell
git add spaces/rowboat/rowboat/packages/openai-plugin-runtime
git commit -m "feat(rowboat): resolve plugin installations safely"
```
