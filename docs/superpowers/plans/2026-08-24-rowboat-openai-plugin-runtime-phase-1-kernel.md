# Rowboat OpenAI Plugin Runtime Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the strict plugin kernel and generate a reproducible conformance lock for all 180 plugins at the pinned OpenAI source commit.

**Architecture:** A standalone TypeScript package owns schemas, safe source access, deterministic digests, component discovery, normalization, and admission policy. The package has no Next.js, React, MongoDB, network, or credential dependency.

**Tech Stack:** TypeScript 5 strict mode, Zod 3, YAML, Vitest, Node fs/path/crypto, tsx.

---

### Task 1: Create the isolated package and focused gate

**Files:**
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/package.json`
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/tsconfig.json`
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/src/index.ts`
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/test/smoke.test.ts`
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/package-lock.json`

- [ ] **Step 1: Write the failing smoke test**

```ts
import { describe, expect, it } from "vitest";
import { RUNTIME_SCHEMA_VERSION } from "../src/index.js";

describe("openai-plugin-runtime", () => {
  it("publishes a versioned schema boundary", () => {
    expect(RUNTIME_SCHEMA_VERSION).toBe("rowboat-openai-plugin-runtime-v1");
  });
});
```

- [ ] **Step 2: Run RED before creating the entrypoint**

Run:

```powershell
npm --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime test
```

Expected: FAIL because the package and `src/index.ts` do not exist.

- [ ] **Step 3: Add the package configuration and minimal entrypoint**

```json
{
  "name": "@rowboat/openai-plugin-runtime",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "exports": "./src/index.ts",
  "scripts": {
    "test": "vitest run",
    "typecheck": "tsc --noEmit",
    "catalog:sync": "tsx scripts/sync-catalog.ts"
  },
  "dependencies": {
    "yaml": "^2.8.1",
    "zod": "^3.25.76"
  },
  "devDependencies": {
    "@types/node": "^22.18.0",
    "tsx": "^4.20.5",
    "typescript": "^5.9.2",
    "vitest": "^3.2.4"
  }
}
```

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "NodeNext",
    "moduleResolution": "NodeNext",
    "strict": true,
    "noUncheckedIndexedAccess": true,
    "exactOptionalPropertyTypes": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "types": ["node", "vitest/globals"]
  },
  "include": ["src/**/*.ts", "scripts/**/*.ts", "test/**/*.ts"]
}
```

```ts
export const RUNTIME_SCHEMA_VERSION = "rowboat-openai-plugin-runtime-v1" as const;
```

Run `npm install --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime` to create the lockfile.

- [ ] **Step 4: Run GREEN and typecheck**

```powershell
npm --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime test
npm --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime run typecheck
```

Expected: 1 test passes and typecheck exits 0.

- [ ] **Step 5: Commit the focused harness**

```powershell
git add spaces/rowboat/rowboat/packages/openai-plugin-runtime
git diff --cached --check
git commit -m "test(rowboat): add plugin runtime harness"
```

### Task 2: Define manifest and canonical domain schemas

**Files:**
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/src/domain/plugin.ts`
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/src/schema/plugin-manifest.ts`
- Modify: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/src/index.ts`
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/test/plugin-manifest.test.ts`
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/test/fixtures/github/plugin.json`

- [ ] **Step 1: Write manifest RED cases**

```ts
import { describe, expect, it } from "vitest";
import { parsePluginManifest } from "../src/schema/plugin-manifest.js";

const valid = {
  name: "github",
  version: "0.1.6",
  description: "GitHub workflows",
  license: "MIT",
  skills: "./skills/",
  apps: "./.app.json",
  mcpServers: "./.mcp.json",
  interface: { displayName: "GitHub", capabilities: ["Interactive", "Write"] },
};

describe("parsePluginManifest", () => {
  it("accepts the pinned manifest shape without discarding metadata", () => {
    expect(parsePluginManifest(valid).name).toBe("github");
  });

  it.each(["Git Hub", "../github", "github/"])("rejects unsafe name %s", (name) => {
    expect(() => parsePluginManifest({ ...valid, name })).toThrow(/manifest_invalid/);
  });

  it("rejects an unknown top-level executable pointer", () => {
    expect(() => parsePluginManifest({ ...valid, executable: "./run.sh" })).toThrow(/manifest_invalid/);
  });
});
```

- [ ] **Step 2: Run RED**

```powershell
npm --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime test -- plugin-manifest
```

Expected: FAIL because `parsePluginManifest` is not defined.

- [ ] **Step 3: Implement strict schemas and explicit component types**

```ts
import { z } from "zod";

const RelativePointer = z.string().min(1);
const Author = z.object({
  name: z.string().min(1),
  email: z.string().email().optional(),
  url: z.string().url().optional(),
}).strict();

export const PluginManifestSchema = z.object({
  name: z.string().regex(/^[a-z0-9]+(?:-[a-z0-9]+)*$/),
  version: z.string().min(1),
  description: z.string().min(1),
  author: Author.optional(),
  homepage: z.string().url().optional(),
  repository: z.string().min(1).optional(),
  license: z.string().optional(),
  keywords: z.array(z.string()).optional(),
  skills: RelativePointer.optional(),
  agents: RelativePointer.optional(),
  commands: RelativePointer.optional(),
  hooks: RelativePointer.optional(),
  mcpServers: RelativePointer.optional(),
  apps: RelativePointer.optional(),
  interface: z.object({
    displayName: z.string().min(1),
    shortDescription: z.string().optional(),
    longDescription: z.string().optional(),
    developerName: z.string().optional(),
    category: z.string().optional(),
    capabilities: z.array(z.enum(["Interactive", "Read", "Write"])).optional(),
    defaultPrompt: z.array(z.string()).optional(),
    brandColor: z.string().optional(),
    composerIcon: RelativePointer.optional(),
    logo: RelativePointer.optional(),
    screenshots: z.array(RelativePointer).optional(),
    websiteURL: z.string().url().optional(),
    privacyPolicyURL: z.string().url().optional(),
    termsOfServiceURL: z.string().url().optional(),
  }).strict(),
}).strict();

export type PluginManifest = z.infer<typeof PluginManifestSchema>;

export function parsePluginManifest(input: unknown): PluginManifest {
  const result = PluginManifestSchema.safeParse(input);
  if (!result.success) {
    throw new Error(`manifest_invalid:${result.error.issues.map((issue) => issue.path.join(".")).join(",")}`);
  }
  return result.data;
}
```

Define in `domain/plugin.ts` the string unions `PluginComponentKind`,
`PluginComponentStatus`, `PluginReasonCode`, and the interfaces
`SourceProvenance`, `NormalizedPluginComponent`, and `NormalizedPlugin` using
only the parsed manifest and secret-free metadata.

`PluginReasonCode` is the closed union below; validation and policy boundaries
return these codes as data, while unexpected programmer faults may still throw:

```ts
export type PluginReasonCode =
  | "source_mismatch"
  | "manifest_invalid"
  | "path_escape"
  | "digest_mismatch"
  | "license_review_required"
  | "license_rejected"
  | "provider_unavailable"
  | "credential_missing"
  | "process_not_admitted"
  | "hook_not_admitted"
  | "component_unsupported"
  | "migration_conflict"
  | "parity_failed"
  | "rollback_unavailable";
```

- [ ] **Step 4: Run GREEN and export the boundary**

```powershell
npm --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime test -- plugin-manifest
npm --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime run typecheck
```

Expected: manifest tests pass and typecheck exits 0.

- [ ] **Step 5: Commit schemas**

```powershell
git add spaces/rowboat/rowboat/packages/openai-plugin-runtime
git commit -m "feat(rowboat): define OpenAI plugin manifest contract"
```

### Task 3: Enforce source pinning, containment, and deterministic digests

**Files:**
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/src/import/source-reader.ts`
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/src/import/path-guard.ts`
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/src/import/digest-service.ts`
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/src/store/content-store.ts`
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/test/source-security.test.ts`

- [ ] **Step 1: Write traversal, symlink, dirty-source, and digest tests**

```ts
it("rejects a component pointer outside the plugin root", async () => {
  await expect(resolveContainedPath(root, "../secret.txt")).rejects.toThrow("path_escape");
});

it("rejects a symlink whose real path leaves the plugin root", async () => {
  await expect(resolveContainedPath(root, "skills/escape")).rejects.toThrow("path_escape");
});

it("hashes files independent of directory enumeration order", async () => {
  expect(await digestTree(root)).toBe(await digestTree(root));
});

it("rejects a source commit different from the configured pin", async () => {
  await expect(assertPinnedSource(source, PIN)).rejects.toThrow("source_mismatch");
});

it("stores immutable content by verified tree digest outside Git", async () => {
  const stored = await store.put(pluginRoot, expectedTreeDigest);
  expect(stored.digest).toBe(expectedTreeDigest);
  await expect(store.put(changedPluginRoot, expectedTreeDigest)).rejects.toThrow("digest_mismatch");
  expect(stored.path.startsWith(repositoryRoot)).toBe(false);
});
```

- [ ] **Step 2: Run RED**

```powershell
npm --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime test -- source-security
```

Expected: FAIL because the security functions do not exist.

- [ ] **Step 3: Implement contained reads and SHA-256 tree hashing**

```ts
export async function resolveContainedPath(root: string, pointer: string): Promise<string> {
  if (path.isAbsolute(pointer)) throw new Error("path_escape:absolute");
  const realRoot = await fs.realpath(root);
  const candidate = await fs.realpath(path.resolve(realRoot, pointer));
  const relative = path.relative(realRoot, candidate);
  if (relative.startsWith("..") || path.isAbsolute(relative)) throw new Error("path_escape:outside_root");
  return candidate;
}
```

`digestTree` must recursively sort normalized slash-separated relative paths,
reject symlinks, hash each path plus file bytes, and return lowercase SHA-256.
`assertPinnedSource` must run injected read-only Git probes for `rev-parse HEAD`
and `status --porcelain`, requiring the expected commit and empty output.

`ContentStore` requires an absolute `ROWBOAT_PLUGIN_STORE` path outside the Git
worktree, copies only contained regular files into `<store>/<treeDigest>`,
recomputes the digest before publishing, and treats an existing matching digest
as an immutable idempotent hit. It rejects a store path inside the repository,
a digest mismatch, links, and writes through mutable aliases. Runtime
resolution reads only this digest directory, never the source checkout or a
branch name.

- [ ] **Step 4: Run GREEN**

```powershell
npm --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime test -- source-security
npm --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime run typecheck
```

Expected: all path, source, and digest cases pass.

- [ ] **Step 5: Commit source security**

```powershell
git add spaces/rowboat/rowboat/packages/openai-plugin-runtime
git commit -m "feat(rowboat): secure pinned plugin source reads"
```

### Task 4: Discover and classify every plugin surface

**Files:**
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/src/schema/component-schemas.ts`
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/src/import/component-discovery.ts`
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/src/import/normalize-plugin.ts`
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/test/component-discovery.test.ts`
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/test/fixtures/complete-plugin/`

- [ ] **Step 1: Write discovery RED cases for all surfaces**

```ts
it("discovers declared and conventional components once", async () => {
  const plugin = await normalizePlugin(fixtureRoot, provenance);
  expect(plugin.components.map((component) => component.kind).sort()).toEqual([
    "agent", "app", "asset", "command", "hook", "mcp", "skill",
  ]);
});

it("records malformed optional components without making the plugin successful", async () => {
  const plugin = await normalizePlugin(malformedRoot, provenance);
  expect(plugin.components.find((item) => item.kind === "hook")?.status).toBe("invalid");
  expect(plugin.status).toBe("partially_available");
});
```

- [ ] **Step 2: Run RED**

```powershell
npm --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime test -- component-discovery
```

Expected: FAIL because discovery and component schemas do not exist.

- [ ] **Step 3: Implement explicit schemas and deterministic discovery**

Implement Zod schemas for:

```ts
export const AppFileSchema = z.object({
  apps: z.record(z.object({ id: z.string().regex(/^connector_[a-f0-9]+$/) }).strict()),
}).strict();

export const HttpMcpSchema = z.object({
  type: z.literal("http"),
  url: z.string().url(),
  oauth_resource: z.string().url().optional(),
  bearer_token_env_var: z.string().regex(/^[A-Z][A-Z0-9_]*$/).optional(),
  note: z.string().optional(),
}).strict();

export const ProcessMcpSchema = z.object({
  command: z.string().min(1),
  args: z.array(z.string()).optional(),
  cwd: z.string().optional(),
  env: z.record(z.string()).optional(),
  env_vars: z.array(z.string().regex(/^[A-Z][A-Z0-9_]*$/)).optional(),
  tool_timeout_sec: z.number().positive().optional(),
}).strict();
```

Discovery order is manifest, skills, agents, commands, MCP, apps, hooks, then
assets. Deduplicate identical declared and conventional paths by real path and
kind. Every component gets its own digest and status.

- [ ] **Step 4: Run GREEN**

```powershell
npm --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime test -- component-discovery
npm --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime run typecheck
```

Expected: all component kinds are deterministic and malformed optional content is explicit.

- [ ] **Step 5: Commit discovery**

```powershell
git add spaces/rowboat/rowboat/packages/openai-plugin-runtime
git commit -m "feat(rowboat): discover OpenAI plugin components"
```

### Task 5: Add license and capability admission

**Files:**
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/src/policy/license-policy.ts`
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/src/policy/capability-policy.ts`
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/src/policy/default-policy.ts`
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/test/policy.test.ts`

- [ ] **Step 1: Write the full policy table as RED tests**

```ts
it.each([
  ["MIT", "admitted"],
  ["Apache-2.0", "admitted"],
  ["Apache-2.0 AND CC-BY-4.0", "review_required"],
  ["Proprietary", "review_required"],
  ["UNLICENSED", "rejected"],
  ["", "review_required"],
  ["LicenseRef-Figma-Developer-Terms", "review_required"],
] as const)("classifies %s as %s", (license, status) => {
  expect(evaluateLicense(license, DEFAULT_POLICY).status).toBe(status);
});

it("denies process MCP and command hooks by default", () => {
  expect(evaluateCapability({ kind: "mcp_process" }, DEFAULT_POLICY).status).toBe("rejected");
  expect(evaluateCapability({ kind: "hook_command" }, DEFAULT_POLICY).status).toBe("rejected");
});
```

- [ ] **Step 2: Run RED**

```powershell
npm --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime test -- policy
```

Expected: FAIL because policy evaluation is undefined.

- [ ] **Step 3: Implement pure, reason-bearing admission decisions**

```ts
export interface AdmissionDecision {
  status: "admitted" | "review_required" | "rejected";
  reason: PluginReasonCode;
  policyVersion: string;
}

export const DEFAULT_POLICY: PluginPolicy = {
  version: "rowboat-plugin-policy-v1",
  admittedLicenses: new Set(["MIT", "Apache-2.0"]),
  rejectedLicenses: new Set(["UNLICENSED"]),
  allowHttpMcp: true,
  allowProcessMcp: false,
  allowCommandHooks: false,
  allowWriteCapabilities: false,
};
```

Policy functions are pure and never inspect environment-variable values. A
component cannot override its parent plugin's rejected or review-required
license decision.

- [ ] **Step 4: Run GREEN**

```powershell
npm --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime test -- policy
npm --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime run typecheck
```

Expected: the complete admission table passes.

- [ ] **Step 5: Commit policy**

```powershell
git add spaces/rowboat/rowboat/packages/openai-plugin-runtime
git commit -m "feat(rowboat): enforce plugin admission policy"
```

### Task 6: Import all 180 plugins and commit the catalog lock

**Files:**
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/src/domain/catalog.ts`
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/src/import/catalog-importer.ts`
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/scripts/sync-catalog.ts`
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/test/catalog-importer.test.ts`
- Create: `spaces/rowboat/rowboat/config/openai-plugin-catalog.lock.json`
- Modify: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/src/index.ts`

- [ ] **Step 1: Write catalog RED tests**

```ts
it("rejects a catalog when folder and manifest names differ", async () => {
  await expect(importCatalog(sourceWithMismatch, options)).rejects.toThrow("manifest_invalid:name_mismatch");
});

it("sorts entries and records complete source and policy provenance", async () => {
  const lock = await importCatalog(twoPluginSource, { ...options, clock: fixedClock });
  expect(lock.entries.map((entry) => entry.name)).toEqual(["alpha", "zeta"]);
  expect(lock.sourceUrl).toBe("https://github.com/openai/plugins.git");
  expect(lock.sourceCommit).toBe(PIN);
  expect(lock.entries[0]).toMatchObject({
    pluginName: "alpha",
    pluginVersion: "1.0.0",
    manifestDigest: expect.stringMatching(/^[a-f0-9]{64}$/),
    treeDigest: expect.stringMatching(/^[a-f0-9]{64}$/),
    importedAt: "2026-08-24T00:00:00.000Z",
    schemaVersion: "rowboat-plugin-schema-v1",
  });
  expect(lock.policyVersion).toBe("rowboat-plugin-policy-v1");
});

it("matches the independently inventoried pinned catalog", async () => {
  const lock = await importCatalog(pinnedSource, { ...options, clock: fixedClock });
  expect(lock.entries).toHaveLength(180);
  expect(lock.inventory).toEqual({
    pluginsWithSkills: 72,
    pluginsWithApps: 154,
    pluginsWithAgents: 14,
    pluginsWithCommands: 6,
    pluginsWithMcp: 8,
    pluginsWithCommandHooks: 2,
  });
  expect(lock.licenseDeclarations).toEqual({
    MIT: 164,
    "Apache-2.0": 6,
    Proprietary: 5,
    UNLICENSED: 2,
    "<missing>": 1,
    "Apache-2.0 AND CC-BY-4.0": 1,
    "LicenseRef-Figma-Developer-Terms": 1,
  });
});
```

- [ ] **Step 2: Run RED**

```powershell
npm --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime test -- catalog-importer
```

Expected: FAIL because `importCatalog` is undefined.

- [ ] **Step 3: Implement import and a pin-enforcing CLI**

```ts
const ArgsSchema = z.object({
  source: z.string().min(1),
  commit: z.literal("11c74d6ba24d3a6d48f54a194cd00ef3beea18f9"),
  output: z.string().min(1),
}).strict();
```

The importer must assert clean pinned source, enumerate direct directories under
`plugins/`, normalize every plugin, sort entries by name, compute the catalog
digest, store each admitted content tree by digest, and write canonical JSON
with a final newline. Every snapshot and entry records source URL, source
commit, plugin name/version, manifest/tree digests, injected import time,
schema version, and policy version. The catalog digest excludes no required
provenance field; tests inject the clock for reproducibility. Import must refuse
to write when any manifest is missing or when the count differs from 180 for
the pinned source.

- [ ] **Step 4: Run the pinned import and full conformance gate**

```powershell
$pluginSourceRoot = Join-Path $env:TEMP "openai-plugins-rowboat-11c74d6"
if (-not (Test-Path -LiteralPath $pluginSourceRoot)) {
  git clone --filter=blob:none --no-checkout https://github.com/openai/plugins.git $pluginSourceRoot
  git -C $pluginSourceRoot checkout --detach 11c74d6ba24d3a6d48f54a194cd00ef3beea18f9
}
git -C $pluginSourceRoot rev-parse HEAD
git -C $pluginSourceRoot status --porcelain
npm --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime run catalog:sync -- --source "$pluginSourceRoot\plugins" --commit 11c74d6ba24d3a6d48f54a194cd00ef3beea18f9 --output spaces/rowboat/rowboat/config/openai-plugin-catalog.lock.json
npm --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime test
npm --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime run typecheck
```

Expected: `rev-parse` prints the exact pin, `status --porcelain` prints nothing,
the lock contains 180 sorted entries, and tests and typecheck pass. If an
existing source directory is dirty or points elsewhere, stop and use a new
task-specific directory outside the repository; never weaken the pin.

- [ ] **Step 5: Commit the kernel and generated lock**

```powershell
git add spaces/rowboat/rowboat/packages/openai-plugin-runtime spaces/rowboat/rowboat/config/openai-plugin-catalog.lock.json
git diff --cached --check
git commit -m "feat(rowboat): import pinned OpenAI plugin catalog"
```
