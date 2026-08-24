# Rowboat OpenAI Plugin Runtime Phase 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Web application the authoritative plugin service and expose the same catalog and installation truth in Web and Desktop without removing legacy execution yet.

**Architecture:** The Web app depends on the framework-free kernel, persists plugin records in explicit MongoDB collections, authorizes versioned APIs, and bridges admitted plugin tools into the existing agent runtime. Desktop uses a typed HTTP client and renders server-owned states; neither UI makes independent admission decisions.

**Tech Stack:** Next.js 15, MongoDB, Awilix, Zod, Vitest, React 19, Vite renderer, Testing Library, existing Rowboat authorization and logger boundaries.

---

### Task 1: Add focused Web tests and plugin persistence

**Files:**
- Modify: `spaces/rowboat/rowboat/apps/rowboat/package.json`
- Modify: `spaces/rowboat/rowboat/apps/rowboat/package-lock.json`
- Create: `spaces/rowboat/rowboat/apps/rowboat/vitest.config.ts`
- Create: `spaces/rowboat/rowboat/apps/rowboat/src/application/repositories/plugins.repository.interface.ts`
- Create: `spaces/rowboat/rowboat/apps/rowboat/src/infrastructure/repositories/mongodb.plugins.repository.ts`
- Create: `spaces/rowboat/rowboat/apps/rowboat/src/infrastructure/repositories/mongodb.plugins.indexes.ts`
- Modify: `spaces/rowboat/rowboat/apps/rowboat/di/container.ts`
- Create: `spaces/rowboat/rowboat/apps/rowboat/test/plugins/plugins.repository.test.ts`

- [ ] **Step 1: Write repository contract RED tests using an in-memory adapter**

```ts
it("stores catalog snapshots by digest and never overwrites them", async () => {
  await repository.putCatalogSnapshot(snapshot);
  await repository.putCatalogSnapshot(snapshot);
  expect(await repository.getCatalogSnapshot(snapshot.catalogDigest)).toEqual(snapshot);
  expect(repository.snapshotWrites).toBe(1);
});

it("uses optimistic concurrency for installation enablement", async () => {
  await repository.putInstallation({ ...installation, revision: 1 });
  await expect(repository.setInstallationEnabled(installation.id, false, 0)).rejects.toThrow("installation_conflict");
});

it("stores credential references without secret values", async () => {
  await expect(repository.putCredentialSlot({ ...slot, value: "secret" } as unknown)).rejects.toThrow("secret_value_rejected");
});
```

- [ ] **Step 2: Add the focused test command and run RED**

Add to `package.json`:

```json
{
  "scripts": {
    "test:plugins": "vitest run test/plugins"
  },
  "dependencies": {
    "@rowboat/openai-plugin-runtime": "file:../../packages/openai-plugin-runtime"
  },
  "devDependencies": {
    "vite-tsconfig-paths": "^5.1.4",
    "vitest": "^3.2.4"
  }
}
```

Run `npm install`, then:

```powershell
npm --prefix spaces/rowboat/rowboat/apps/rowboat run test:plugins
```

Expected: FAIL because repository contracts are absent.

- [ ] **Step 3: Implement the repository boundary and exact collections**

```ts
export interface IPluginsRepository {
  putCatalogSnapshot(snapshot: PluginCatalogSnapshot): Promise<void>;
  getCatalogSnapshot(digest: string): Promise<PluginCatalogSnapshot | null>;
  listCatalogEntries(catalogDigest: string): Promise<readonly PluginCatalogEntry[]>;
  putCatalogEntries(entries: readonly PluginCatalogEntry[]): Promise<void>;
  putInstallation(installation: PluginInstallation): Promise<void>;
  listInstallations(projectId: string): Promise<readonly PluginInstallation[]>;
  setInstallationEnabled(id: string, enabled: boolean, expectedRevision: number): Promise<PluginInstallation>;
  putAdmissions(admissions: readonly PluginComponentAdmission[]): Promise<void>;
  listAdmissions(installationId: string): Promise<readonly PluginComponentAdmission[]>;
  putCredentialSlot(slot: PluginCredentialSlot): Promise<void>;
  putMigrationRecord(record: PluginMigrationRecord): Promise<void>;
  putReceipt(receipt: PluginReceipt): Promise<void>;
}
```

Use exact MongoDB collections from the design. Create unique indexes for
catalog digest, `(catalogDigest, name)`, installation ID,
`(projectId, pluginName)`, `(installationId, componentDigest)`, credential slot
ID, migration ID, and receipt ID. Register `pluginsRepository` in Awilix.

- [ ] **Step 4: Run GREEN and inspect indexes**

```powershell
npm --prefix spaces/rowboat/rowboat/apps/rowboat run test:plugins -- plugins.repository
npm --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime test
```

Expected: repository contract and kernel tests pass.

- [ ] **Step 5: Commit persistence**

```powershell
git add spaces/rowboat/rowboat/apps/rowboat spaces/rowboat/rowboat/packages/openai-plugin-runtime
git diff --cached --check
git commit -m "feat(rowboat): persist plugin catalog and installations"
```

### Task 2: Add authorized catalog and installation services

**Files:**
- Create: `spaces/rowboat/rowboat/apps/rowboat/src/application/policies/plugin-api-authorization.policy.ts`
- Create: `spaces/rowboat/rowboat/apps/rowboat/src/infrastructure/policies/auth0.plugin-api-authorization.policy.ts`
- Create: `spaces/rowboat/rowboat/apps/rowboat/src/application/use-cases/plugins/list-plugin-catalog.use-case.ts`
- Create: `spaces/rowboat/rowboat/apps/rowboat/src/application/use-cases/plugins/preview-plugin-installation.use-case.ts`
- Create: `spaces/rowboat/rowboat/apps/rowboat/src/application/use-cases/plugins/install-plugin.use-case.ts`
- Create: `spaces/rowboat/rowboat/apps/rowboat/src/application/use-cases/plugins/set-plugin-enabled.use-case.ts`
- Create: `spaces/rowboat/rowboat/apps/rowboat/src/interface-adapters/controllers/plugins/plugin-catalog.controller.ts`
- Create: `spaces/rowboat/rowboat/apps/rowboat/src/interface-adapters/controllers/plugins/plugin-installation.controller.ts`
- Modify: `spaces/rowboat/rowboat/apps/rowboat/di/container.ts`
- Create: `spaces/rowboat/rowboat/apps/rowboat/test/plugins/plugin-use-cases.test.ts`

- [ ] **Step 1: Write authorization, idempotency, and secret RED tests**

```ts
it("does not write when project authorization fails", async () => {
  authorization.rejectWith("forbidden");
  await expect(useCase.execute(request)).rejects.toThrow("forbidden");
  expect(repository.installationWrites).toBe(0);
});

it("returns the existing receipt for a repeated idempotency key", async () => {
  const first = await useCase.execute(request);
  const second = await useCase.execute(request);
  expect(second.receiptId).toBe(first.receiptId);
  expect(repository.installationWrites).toBe(1);
});

it("returns credential slot names and never their values", async () => {
  const preview = await previewUseCase.execute(previewRequest);
  expect(preview.credentialSlots).toEqual([{ name: "GITHUB_PAT_TOKEN", configured: false }]);
  expect(JSON.stringify(preview)).not.toContain("secret-value");
});
```

- [ ] **Step 2: Run RED**

```powershell
npm --prefix spaces/rowboat/rowboat/apps/rowboat run test:plugins -- plugin-use-cases
```

Expected: FAIL because plugin services do not exist.

- [ ] **Step 3: Implement service and identity boundaries**

```ts
export type PluginApiIdentity =
  | { kind: "user"; userId: string }
  | { kind: "project_api_key"; projectId: string };

export interface IPluginApiAuthorizationPolicy {
  authenticate(request: Request): Promise<PluginApiIdentity>;
  authorizeProject(identity: PluginApiIdentity, projectId: string): Promise<void>;
}
```

Cookie requests use the existing user session. Bearer requests first use the
existing project API-key boundary; Auth0 user tokens are accepted only after
issuer, audience, signature, expiry, and subject verification. `USE_AUTH=false`
maps to the existing guest user without accepting arbitrary bearer strings.
All mutation use cases require an idempotency key, project authorization,
catalog digest match, admitted license, and optimistic revision.

- [ ] **Step 4: Run GREEN**

```powershell
npm --prefix spaces/rowboat/rowboat/apps/rowboat run test:plugins -- plugin-use-cases
```

Expected: authorization, idempotency, digest, policy, and redaction cases pass.

- [ ] **Step 5: Commit services**

```powershell
git add spaces/rowboat/rowboat/apps/rowboat
git commit -m "feat(rowboat): add authorized plugin services"
```

### Task 3: Expose versioned plugin APIs

**Files:**
- Create: `spaces/rowboat/rowboat/apps/rowboat/app/api/v1/plugins/route.ts`
- Create: `spaces/rowboat/rowboat/apps/rowboat/app/api/v1/plugins/[pluginName]/route.ts`
- Create: `spaces/rowboat/rowboat/apps/rowboat/app/api/v1/projects/[projectId]/plugins/route.ts`
- Create: `spaces/rowboat/rowboat/apps/rowboat/app/api/v1/projects/[projectId]/plugins/[pluginName]/route.ts`
- Create: `spaces/rowboat/rowboat/apps/rowboat/app/api/v1/projects/[projectId]/plugins/_responses.ts`
- Create: `spaces/rowboat/rowboat/apps/rowboat/test/plugins/plugin-routes.test.ts`

- [ ] **Step 1: Write route RED tests**

```ts
it("returns component reason codes instead of full-success for a partial plugin", async () => {
  const response = await GET(catalogRequest);
  const body = await response.json();
  expect(body.items[0].status).toBe("partially_available");
  expect(body.items[0].components.app.reason).toBe("provider_unavailable");
});

it("requires an idempotency key for installation mutation", async () => {
  const response = await POST(requestWithoutIdempotencyKey, routeContext);
  expect(response.status).toBe(400);
  expect(await response.json()).toEqual({ error: "idempotency_key_required" });
});
```

- [ ] **Step 2: Run RED**

```powershell
npm --prefix spaces/rowboat/rowboat/apps/rowboat run test:plugins -- plugin-routes
```

Expected: FAIL because routes are missing.

- [ ] **Step 3: Implement thin, typed routes**

```ts
export function pluginErrorResponse(error: unknown): Response {
  const classified = classifyPluginError(error);
  return Response.json(
    { error: classified.reason, details: classified.safeDetails },
    { status: classified.httpStatus },
  );
}
```

Routes parse params, query, body, authorization, and `Idempotency-Key`, call one
controller, and serialize Zod-validated responses. Catalog GET is user-authenticated;
project GET/POST/PATCH is project-authorized. Never include exception stacks,
credential values, raw provider errors, or mutable source paths.

- [ ] **Step 4: Run GREEN**

```powershell
npm --prefix spaces/rowboat/rowboat/apps/rowboat run test:plugins -- plugin-routes
```

Expected: status, authorization, idempotency, and safe-error cases pass.

- [ ] **Step 5: Commit APIs**

```powershell
git add spaces/rowboat/rowboat/apps/rowboat/app/api spaces/rowboat/rowboat/apps/rowboat/test/plugins
git commit -m "feat(rowboat): expose versioned plugin APIs"
```

### Task 4: Bridge plugin tools into the agent runtime without fallback

**Files:**
- Modify: `spaces/rowboat/rowboat/apps/rowboat/app/lib/types/workflow_types.ts`
- Create: `spaces/rowboat/rowboat/apps/rowboat/src/application/services/plugin-tool-runtime.ts`
- Modify: `spaces/rowboat/rowboat/apps/rowboat/src/application/lib/agents-runtime/agent-tools.ts`
- Modify: `spaces/rowboat/rowboat/apps/rowboat/di/container.ts`
- Create: `spaces/rowboat/rowboat/apps/rowboat/test/plugins/plugin-tool-runtime.test.ts`

- [ ] **Step 1: Write exact-routing and no-fallback RED tests**

```ts
it("invokes the provider bound by installation and component digest", async () => {
  const result = await runtime.invoke(binding, args, context);
  expect(result.status).toBe("success");
  expect(admittedProvider.calls).toBe(1);
  expect(legacyComposio.calls).toBe(0);
});

it("does not fall back to Composio when the plugin provider is unavailable", async () => {
  await expect(runtime.invoke(unavailableBinding, args, context)).rejects.toThrow("provider_unavailable");
  expect(legacyComposio.calls).toBe(0);
});
```

- [ ] **Step 2: Run RED**

```powershell
npm --prefix spaces/rowboat/rowboat/apps/rowboat run test:plugins -- plugin-tool-runtime
```

Expected: FAIL because plugin bindings are not executable.

- [ ] **Step 3: Add a typed workflow binding and bridge**

```ts
export const PluginToolBinding = z.object({
  installationId: z.string().uuid(),
  pluginName: z.string(),
  componentDigest: z.string().regex(/^[a-f0-9]{64}$/),
  providerBindingId: z.string(),
  capability: z.enum(["read", "write"]),
}).strict();
```

Add optional `pluginBinding` to `WorkflowTool`. In `createTools`, check
`pluginBinding` before legacy flags and delegate only to `PluginToolRuntime`.
The service resolves current catalog/install/admission state on every execution,
resolves credential references at call time, invokes the exact provider, and
stores a redacted receipt. No provider match means an error, not a legacy branch.

- [ ] **Step 4: Run GREEN and existing focused runtime tests**

```powershell
npm --prefix spaces/rowboat/rowboat/apps/rowboat run test:plugins -- plugin-tool-runtime
npm --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime test
```

Expected: exact plugin routing and zero-fallback counters pass.

- [ ] **Step 5: Commit the runtime bridge**

```powershell
git add spaces/rowboat/rowboat/apps/rowboat
git commit -m "feat(rowboat): route admitted plugin tools"
```

### Task 5: Add the Web plugin catalog and installation UI

**Files:**
- Create: `spaces/rowboat/rowboat/apps/rowboat/app/actions/plugin.actions.ts`
- Create: `spaces/rowboat/rowboat/apps/rowboat/app/projects/[projectId]/plugins/page.tsx`
- Create: `spaces/rowboat/rowboat/apps/rowboat/app/projects/[projectId]/plugins/components/plugin-catalog.tsx`
- Create: `spaces/rowboat/rowboat/apps/rowboat/app/projects/[projectId]/plugins/components/plugin-card.tsx`
- Create: `spaces/rowboat/rowboat/apps/rowboat/app/projects/[projectId]/plugins/components/plugin-install-dialog.tsx`
- Modify: `spaces/rowboat/rowboat/apps/rowboat/app/projects/layout/components/sidebar.tsx`
- Modify: `spaces/rowboat/rowboat/apps/rowboat/app/projects/[projectId]/tools/components/ToolsConfig.tsx`
- Create: `spaces/rowboat/rowboat/apps/rowboat/test/plugins/plugin-ui-state.test.ts`

- [ ] **Step 1: Write UI-state RED tests as pure view-model tests**

```ts
it("does not label a partial plugin installed", () => {
  expect(toPluginCardView(partialPlugin).badge).toBe("Partially available");
  expect(toPluginCardView(partialPlugin).canInstall).toBe(false);
});

it("shows policy reason before any mutation", () => {
  expect(toPluginCardView(reviewPlugin).reason).toBe("license_review_required");
});
```

- [ ] **Step 2: Run RED**

```powershell
npm --prefix spaces/rowboat/rowboat/apps/rowboat run test:plugins -- plugin-ui-state
```

Expected: FAIL because the view model is absent.

- [ ] **Step 3: Implement server-owned status rendering**

The page loads catalog and project installations through controllers. Cards
render `available`, `review_required`, `installed`, `partially_available`,
`unavailable`, `migration_required`, and `error` verbatim. The install dialog
first calls preview, lists component decisions and credential slots, then sends
an idempotent mutation. Add a Plugins sidebar destination. Keep legacy Tools
visible but label them `Legacy — migration pending`; do not remove them in this
phase.

- [ ] **Step 4: Run GREEN**

```powershell
npm --prefix spaces/rowboat/rowboat/apps/rowboat run test:plugins -- plugin-ui-state
```

Expected: partial, unavailable, review, and installed view states pass.

- [ ] **Step 5: Commit Web UI**

```powershell
git add spaces/rowboat/rowboat/apps/rowboat/app spaces/rowboat/rowboat/apps/rowboat/test/plugins
git commit -m "feat(rowboat): add Web plugin catalog"
```

### Task 6: Add the Desktop plugin API client and shared status UI

**Files:**
- Modify: `spaces/rowboat/rowboat/apps/x/apps/renderer/package.json`
- Modify: `spaces/rowboat/rowboat/apps/x/pnpm-lock.yaml`
- Create: `spaces/rowboat/rowboat/apps/x/apps/renderer/src/lib/rowboat-plugin-api.ts`
- Create: `spaces/rowboat/rowboat/apps/x/apps/renderer/src/hooks/usePlugins.ts`
- Create: `spaces/rowboat/rowboat/apps/x/apps/renderer/src/components/settings/plugin-settings.tsx`
- Modify: `spaces/rowboat/rowboat/apps/x/apps/renderer/src/components/settings-dialog.tsx`
- Create: `spaces/rowboat/rowboat/apps/x/apps/renderer/src/lib/rowboat-plugin-api.test.ts`
- Create: `spaces/rowboat/rowboat/apps/x/apps/renderer/src/components/settings/plugin-settings.test.tsx`

- [ ] **Step 1: Write client and status RED tests**

```ts
it("sends the Rowboat access token without persisting it", async () => {
  await client.listCatalog({ baseUrl, accessToken: "token-value" });
  expect(fetcher.lastHeaders.Authorization).toBe("Bearer token-value");
  expect(JSON.stringify(client.snapshot())).not.toContain("token-value");
});

it("renders server reason codes and disables unavailable install", () => {
  render(<PluginSettings state={partialState} />);
  expect(screen.getByText("provider_unavailable")).toBeVisible();
  expect(screen.getByRole("button", { name: "Install" })).toBeDisabled();
});
```

- [ ] **Step 2: Add focused renderer tests and run RED**

Add `"test:plugins": "vitest run src/lib/rowboat-plugin-api.test.ts src/components/settings/plugin-settings.test.tsx"` plus Vitest, jsdom, and Testing Library dev dependencies. Run:

```powershell
pnpm --dir spaces/rowboat/rowboat/apps/x install --frozen-lockfile=false
pnpm --dir spaces/rowboat/rowboat/apps/x --filter @x/renderer test:plugins
```

Expected: FAIL because the client and component are absent.

- [ ] **Step 3: Implement the typed client and Plugins settings tab**

```ts
export interface PluginApiSession {
  baseUrl: string;
  accessToken: string;
}

export class RowboatPluginApi {
  constructor(private readonly fetcher: typeof fetch = fetch) {}

  async listCatalog(session: PluginApiSession): Promise<PluginCatalogResponse> {
    const response = await this.fetcher(new URL("/api/v1/plugins", session.baseUrl), {
      headers: { Authorization: `Bearer ${session.accessToken}` },
    });
    return PluginCatalogResponseSchema.parse(await readJsonOrThrow(response));
  }
}
```

`usePlugins` obtains ephemeral account state from `useRowboatAccount`, derives
the HTTP API URL from `config`, fetches server state, and drops the token after
each request. Add a `plugins` settings tab. The UI uses response statuses and
reason codes unchanged and sends idempotency keys for mutations.

- [ ] **Step 4: Run the Phase 3 focused gates**

```powershell
npm --prefix spaces/rowboat/rowboat/apps/rowboat run test:plugins
pnpm --dir spaces/rowboat/rowboat/apps/x --filter @x/renderer test:plugins
npm --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime test
git diff --check
```

Expected: kernel, Web plugin, and Desktop plugin tests pass. Report unrelated
full Web TypeScript and root Desktop lint failures separately.

- [ ] **Step 5: Commit Desktop integration**

```powershell
git add spaces/rowboat/rowboat/apps/x
git commit -m "feat(rowboat): show plugin state in Desktop"
```
