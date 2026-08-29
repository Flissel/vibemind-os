# Rowboat OpenAI Plugin Runtime Phase 4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate every legacy prebuilt-card project by copy-on-write, prove shadow parity without duplicate writes, and perform a reversible cutover only after explicit action-time confirmation.

**Architecture:** Deterministic recipes translate legacy project configuration into immutable plugin installations and migration receipts. A dry-run API and CLI produce project-by-project evidence before mutation. Shadow comparison resolves both representations but executes write-capable tools only on the active path. A compare-and-swap runtime mode and retained rollback snapshot make cutover reversible during the migration window.

**Tech Stack:** TypeScript strict mode, Zod, Vitest, Next.js route handlers, MongoDB, existing Rowboat project repository, Node CLI, Python pytest space contracts.

---

### Task 1: Define deterministic migration recipes for all ten legacy cards

**Files:**
- Create: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/src/domain/migration.ts`
- Modify: `spaces/rowboat/rowboat/packages/openai-plugin-runtime/src/index.ts`
- Create: `spaces/rowboat/rowboat/apps/rowboat/src/application/services/legacy-plugin-recipes.ts`
- Create: `spaces/rowboat/rowboat/apps/rowboat/src/application/services/legacy-plugin-migration.ts`
- Create: `spaces/rowboat/rowboat/apps/rowboat/test/plugins/legacy-plugin-migration.test.ts`

- [x] **Step 1: Write RED tests covering every checked-in legacy card**

```ts
const legacyCards = [
  "customer-support",
  "eisenhower-email-organizer",
  "github-data-to-spreadsheet",
  "github-issue-to-slack",
  "github-pr-to-slack",
  "interview-scheduler",
  "meeting-prep-assistant",
  "reddit-on-slack",
  "tweet-assistant",
  "twitter-sentiment",
] as const;

it.each(legacyCards)("builds a deterministic recipe for %s", async cardId => {
  const first = await migration.preview(fixtureProject(cardId), catalog);
  const second = await migration.preview(fixtureProject(cardId), catalog);
  expect(first).toEqual(second);
  expect(first.recipeId).toBe(`legacy-card:${cardId}:v1`);
  expect(first.sourceProjectRevision).toBe(7);
  expect(first.mutationsApplied).toBe(false);
});

it("fails closed when an exact app provider is unavailable", async () => {
  const catalogWithoutSlack = catalogFixture({ unavailableProviders: ["slack"] });
  const result = await migration.preview(fixtureProject("github-pr-to-slack"), catalogWithoutSlack);
  expect(result.status).toBe("blocked");
  expect(result.blockers).toContainEqual(expect.objectContaining({ code: "provider_unavailable" }));
});
```

- [x] **Step 2: Run the focused Web suite and capture RED**

```powershell
npm --prefix spaces/rowboat/rowboat/apps/rowboat run test:plugins -- legacy-plugin-migration.test.ts
```

Expected: FAIL because the recipe table and migration service do not exist.

- [x] **Step 3: Add strict migration schemas to the shared kernel**

```ts
export const ZPluginMigrationRecord = z.object({
  id: z.string().min(1),
  projectId: z.string().min(1),
  recipeId: z.string().min(1),
  sourceProjectRevision: z.number().int().nonnegative(),
  sourceDigest: ZSha256,
  targetCatalogDigest: ZSha256,
  targetInstallationIds: z.array(z.string().min(1)),
  rollbackSnapshotDigest: ZSha256,
  status: z.enum(["previewed", "applied", "verified", "rolled_back", "blocked"]),
  blockers: z.array(ZPluginBlocker),
  createdAt: z.string().datetime(),
});

export type PluginMigrationRecord = z.infer<typeof ZPluginMigrationRecord>;
```

Export the schema and type from `src/index.ts`. Keep source configuration represented by a digest plus the existing project revision; do not copy credential values into the record.

- [x] **Step 4: Implement the exact ten-recipe table and copy-on-write preview**

```ts
export const LEGACY_PLUGIN_RECIPES = {
  "customer-support": recipe("legacy-card:customer-support:v1", ["customer-support-agent", "mock-tools"]),
  "eisenhower-email-organizer": recipe("legacy-card:eisenhower-email-organizer:v1", ["gmail"]),
  "github-data-to-spreadsheet": recipe("legacy-card:github-data-to-spreadsheet:v1", ["github", "google-drive-sheets", "slack"]),
  "github-issue-to-slack": recipe("legacy-card:github-issue-to-slack:v1", ["github", "slack"]),
  "github-pr-to-slack": recipe("legacy-card:github-pr-to-slack:v1", ["github", "slack"]),
  "interview-scheduler": recipe("legacy-card:interview-scheduler:v1", ["google-calendar", "google-drive-sheets"]),
  "meeting-prep-assistant": recipe("legacy-card:meeting-prep-assistant:v1", ["gmail", "search"]),
  "reddit-on-slack": recipe("legacy-card:reddit-on-slack:v1", ["reddit", "slack"]),
  "tweet-assistant": recipe("legacy-card:tweet-assistant:v1", ["x", "search"]),
  "twitter-sentiment": recipe("legacy-card:twitter-sentiment:v1", ["x"]),
} as const;
```

Resolve each logical capability to an exact admitted catalog component. If the pinned catalog has no exact provider, return `blocked`; never silently select a similarly named plugin. Build new installation objects without modifying the input project.

- [x] **Step 5: Prove determinism, idempotency, and source immutability**

Add assertions that a repeated preview produces identical stable IDs/digests, an already-applied record returns the same installation IDs, and `structuredClone(project)` still equals the source fixture after preview.

```powershell
npm --prefix spaces/rowboat/rowboat/apps/rowboat run test:plugins -- legacy-plugin-migration.test.ts
npm --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime run typecheck
git diff --check
```

Expected: PASS.

- [x] **Step 6: Commit Task 1**

```powershell
git add spaces/rowboat/rowboat/packages/openai-plugin-runtime/src/domain/migration.ts spaces/rowboat/rowboat/packages/openai-plugin-runtime/src/index.ts spaces/rowboat/rowboat/apps/rowboat/src/application/services/legacy-plugin-recipes.ts spaces/rowboat/rowboat/apps/rowboat/src/application/services/legacy-plugin-migration.ts spaces/rowboat/rowboat/apps/rowboat/test/plugins/legacy-plugin-migration.test.ts
git commit -m "feat(rowboat): define legacy plugin migration recipes"
```

### Task 2: Add read-only preview APIs and an explicit migration CLI

**Files:**
- Create: `spaces/rowboat/rowboat/apps/rowboat/src/application/use-cases/plugins/preview-plugin-migration.use-case.ts`
- Create: `spaces/rowboat/rowboat/apps/rowboat/src/application/use-cases/plugins/apply-plugin-migration.use-case.ts`
- Create: `spaces/rowboat/rowboat/apps/rowboat/src/interface-adapters/controllers/plugins/preview-plugin-migration.controller.ts`
- Create: `spaces/rowboat/rowboat/apps/rowboat/src/interface-adapters/controllers/plugins/apply-plugin-migration.controller.ts`
- Create: `spaces/rowboat/rowboat/apps/rowboat/app/api/v1/projects/[projectId]/plugins/migration/preview/route.ts`
- Create: `spaces/rowboat/rowboat/apps/rowboat/app/api/v1/projects/[projectId]/plugins/migration/apply/route.ts`
- Create: `spaces/rowboat/rowboat/apps/rowboat/scripts/migrate-openai-plugins.ts`
- Modify: `spaces/rowboat/rowboat/apps/rowboat/package.json`
- Modify: `spaces/rowboat/rowboat/apps/rowboat/di/container.ts`
- Create: `spaces/rowboat/rowboat/apps/rowboat/test/plugins/plugin-migration-api.test.ts`
- Create: `spaces/rowboat/rowboat/apps/rowboat/test/plugins/plugin-migration-cli.test.ts`

- [x] **Step 1: Write RED tests for dry-run purity and apply authorization**

```ts
it("previews every project without writing", async () => {
  const report = await preview.execute({ actor, scope: "all" });
  expect(report.projects).toHaveLength(3);
  expect(repository.writeCount).toBe(0);
  expect(report.mutationsApplied).toBe(false);
});

it("rejects apply without an explicit confirmation token", async () => {
  await expect(apply.execute({ actor, projectId, confirmationToken: undefined }))
    .rejects.toThrow("migration_confirmation_required");
  expect(repository.writeCount).toBe(0);
});
```

- [x] **Step 2: Run focused tests and capture RED**

```powershell
npm --prefix spaces/rowboat/rowboat/apps/rowboat run test:plugins -- plugin-migration-api.test.ts plugin-migration-cli.test.ts
```

Expected: FAIL because preview/apply boundaries are absent.

- [x] **Step 3: Implement authenticated preview and compare-and-swap apply**

The preview endpoint is read-only. The apply endpoint must verify the same project authorization used by Phase 3, require a short-lived server-issued confirmation token bound to actor, project, source revision, and catalog digest, then write in this order:

1. immutable rollback snapshot;
2. plugin installations and admissions;
3. migration record;
4. project migration pointer using expected source revision.

On any failure before step 4, the legacy project remains authoritative. A retry reuses deterministic IDs.

```ts
await projects.compareAndSetPluginMigrationPointer({
  projectId,
  expectedRevision: preview.sourceProjectRevision,
  migrationRecordId: preview.id,
});
```

- [x] **Step 4: Implement CLI modes with machine-readable output**

Add scripts:

```json
{
  "scripts": {
    "plugins:migrate": "tsx scripts/migrate-openai-plugins.ts",
    "plugins:verify": "vitest run test/plugins && npm run plugins:migrate -- --dry-run --scope fixtures"
  }
}
```

Supported commands are exact:

```powershell
npm --prefix spaces/rowboat/rowboat/apps/rowboat run plugins:migrate -- --dry-run --scope all --output .artifacts/openai-plugin-migration-preview.json
$migrationPreview = Get-Content -Raw -LiteralPath .artifacts/openai-plugin-migration-preview.json | ConvertFrom-Json
$migrationProjectId = ($migrationPreview.projects | Where-Object status -eq "ready" | Select-Object -First 1).projectId
$migrationConfirmationToken = Read-Host "Paste the short-lived confirmation token"
npm --prefix spaces/rowboat/rowboat/apps/rowboat run plugins:migrate -- --apply --project-id $migrationProjectId --confirmation-token $migrationConfirmationToken --output .artifacts/openai-plugin-migration-apply.json
```

The output schema includes catalog digest, timestamp, scope, per-project recipe/status/blockers, mutation count, and receipt IDs. It must never serialize environment variables or credential values.

- [x] **Step 5: Verify the safe path only**

```powershell
npm --prefix spaces/rowboat/rowboat/apps/rowboat run test:plugins -- plugin-migration-api.test.ts plugin-migration-cli.test.ts
npm --prefix spaces/rowboat/rowboat/apps/rowboat run plugins:migrate -- --dry-run --scope fixtures --output .artifacts/openai-plugin-migration-fixtures.json
git diff --check
```

Expected: tests pass; fixture report says `mutationsApplied: false` and repository write count remains zero.

- [x] **Step 6: Stop for action-time confirmation before a live database apply**

Present the all-project dry-run artifact, exact project count, blocker count, catalog digest, and rollback strategy to the user. Do not run `--apply` against a non-fixture database until the user confirms that exact action and scope.

- [x] **Step 7: Commit Task 2 without generated reports**

```powershell
git add spaces/rowboat/rowboat/apps/rowboat/src/application/use-cases/plugins spaces/rowboat/rowboat/apps/rowboat/src/interface-adapters/controllers/plugins spaces/rowboat/rowboat/apps/rowboat/app/api/v1/projects spaces/rowboat/rowboat/apps/rowboat/scripts/migrate-openai-plugins.ts spaces/rowboat/rowboat/apps/rowboat/package.json spaces/rowboat/rowboat/apps/rowboat/di/container.ts spaces/rowboat/rowboat/apps/rowboat/test/plugins/plugin-migration-api.test.ts spaces/rowboat/rowboat/apps/rowboat/test/plugins/plugin-migration-cli.test.ts
git commit -m "feat(rowboat): add guarded plugin migration workflow"
```

### Task 3: Prove shadow parity without duplicate write calls

**Files:**
- Create: `spaces/rowboat/rowboat/apps/rowboat/src/application/services/plugin-shadow-parity.ts`
- Create: `spaces/rowboat/rowboat/apps/rowboat/src/application/services/plugin-parity-report.ts`
- Create: `spaces/rowboat/rowboat/apps/rowboat/test/plugins/plugin-shadow-parity.test.ts`
- Modify: `spaces/rowboat/rowboat/apps/rowboat/src/application/lib/agents-runtime/agents.ts`

- [x] **Step 1: Write RED behavioral tests with call counters**

```ts
it("compares write-capable descriptors without executing the shadow provider", async () => {
  const active = providerSpy({ effect: "write" });
  const shadow = providerSpy({ effect: "write", throwOnCall: true });
  const report = await parity.compareAndRun({ active, shadow, request });
  expect(active.calls).toBe(1);
  expect(shadow.calls).toBe(0);
  expect(report.shadow.execution).toBe("descriptor_only");
});

it("normalizes agent, prompt, tool, and provider descriptors", async () => {
  const report = await parity.compareOnly(legacyFixture, pluginFixture);
  expect(report.dimensions).toEqual(["agent", "prompt", "tool", "provider"]);
  expect(report.differences).toEqual([]);
});
```

- [x] **Step 2: Run the parity test and capture RED**

```powershell
npm --prefix spaces/rowboat/rowboat/apps/rowboat run test:plugins -- plugin-shadow-parity.test.ts
```

Expected: FAIL because the comparison service is absent.

- [x] **Step 3: Implement canonical comparison and execution guards**

```ts
export type ShadowExecution = "not_requested" | "read_only" | "descriptor_only";

export async function evaluateShadow(request: ShadowRequest): Promise<ParityReport> {
  const activeResult = await request.active.invoke(request.input);
  const shadowExecution = request.shadow.effect === "read"
    ? await request.shadow.invoke(request.input).then(() => "read_only" as const)
    : "descriptor_only" as const;
  return compareDescriptors(request, activeResult, shadowExecution);
}
```

Normalize order-insensitive fields before hashing. Redact arguments and results before persistence. Store direct comparison evidence in `plugin_receipts`; do not claim output parity for descriptor-only write tools.

- [x] **Step 4: Wire shadow mode behind persisted project state**

In `agents.ts`, use the Phase 3 runtime bridge. When the project mode is `shadow`, legacy remains active and the plugin representation is comparison-only unless every selected plugin provider is classified read-only. Unknown effect classification is treated as write-capable.

- [x] **Step 5: Verify zero shadow writes and commit**

```powershell
npm --prefix spaces/rowboat/rowboat/apps/rowboat run test:plugins -- plugin-shadow-parity.test.ts
npm --prefix spaces/rowboat/rowboat/apps/rowboat run test:plugins
git diff --check
git add spaces/rowboat/rowboat/apps/rowboat/src/application/services/plugin-shadow-parity.ts spaces/rowboat/rowboat/apps/rowboat/src/application/services/plugin-parity-report.ts spaces/rowboat/rowboat/apps/rowboat/test/plugins/plugin-shadow-parity.test.ts spaces/rowboat/rowboat/apps/rowboat/src/application/lib/agents-runtime/agents.ts
git commit -m "feat(rowboat): add write-safe plugin shadow parity"
```

### Task 4: Add reversible cutover state and rollback

**Files:**
- Modify: `spaces/rowboat/rowboat/apps/rowboat/src/entities/models/project.ts`
- Modify: `spaces/rowboat/rowboat/apps/rowboat/src/application/repositories/projects.repository.interface.ts`
- Modify: `spaces/rowboat/rowboat/apps/rowboat/src/infrastructure/repositories/mongodb.projects.repository.ts`
- Create: `spaces/rowboat/rowboat/apps/rowboat/src/application/use-cases/plugins/set-plugin-runtime-mode.use-case.ts`
- Create: `spaces/rowboat/rowboat/apps/rowboat/src/interface-adapters/controllers/plugins/set-plugin-runtime-mode.controller.ts`
- Create: `spaces/rowboat/rowboat/apps/rowboat/app/api/v1/projects/[projectId]/plugins/runtime-mode/route.ts`
- Create: `spaces/rowboat/rowboat/apps/rowboat/test/plugins/plugin-cutover-rollback.test.ts`

- [x] **Step 1: Write the transition table as RED tests**

```ts
it.each([
  ["legacy", "shadow", true],
  ["shadow", "openai", true],
  ["openai", "legacy", true],
  ["legacy", "openai", false],
] as const)("permits %s -> %s: %s", async (from, to, allowed) => {
  const operation = service.execute(requestFixture({ from, to }));
  if (allowed) await expect(operation).resolves.toMatchObject({ mode: to });
  else await expect(operation).rejects.toThrow("runtime_mode_transition_rejected");
});

it("rejects cutover when parity or rollback evidence is missing", async () => {
  await expect(service.execute(requestFixture({ from: "shadow", to: "openai", parity: null })))
    .rejects.toThrow("cutover_evidence_required");
});
```

- [x] **Step 2: Run the test and capture RED**

```powershell
npm --prefix spaces/rowboat/rowboat/apps/rowboat run test:plugins -- plugin-cutover-rollback.test.ts
```

Expected: FAIL because runtime-mode state and transitions do not exist.

- [x] **Step 3: Add backward-compatible project state**

```ts
export const ZPluginRuntimeState = z.object({
  mode: z.enum(["legacy", "shadow", "openai"]),
  migrationRecordId: z.string().min(1).optional(),
  parityReceiptId: z.string().min(1).optional(),
  rollbackSnapshotDigest: ZSha256.optional(),
  revision: z.number().int().nonnegative(),
}).default({ mode: "legacy", revision: 0 });
```

Existing project documents with no field parse as `legacy`. Do not rewrite them during reads.

- [x] **Step 4: Implement evidence-gated compare-and-swap transitions**

Require project authorization, expected runtime revision, matching pinned catalog digest, successful migration receipt, zero unresolved blockers, an accepted parity receipt, and a rollback snapshot before `shadow -> openai`. `openai -> legacy` restores authority to the existing untouched workflow fields and records a rollback receipt.

- [x] **Step 5: Prove rollback behavior and commit**

```powershell
npm --prefix spaces/rowboat/rowboat/apps/rowboat run test:plugins -- plugin-cutover-rollback.test.ts
npm --prefix spaces/rowboat/rowboat/apps/rowboat run test:plugins
git diff --check
git add spaces/rowboat/rowboat/apps/rowboat/src/entities/models/project.ts spaces/rowboat/rowboat/apps/rowboat/src/application/repositories/projects.repository.interface.ts spaces/rowboat/rowboat/apps/rowboat/src/infrastructure/repositories/mongodb.projects.repository.ts spaces/rowboat/rowboat/apps/rowboat/src/application/use-cases/plugins/set-plugin-runtime-mode.use-case.ts spaces/rowboat/rowboat/apps/rowboat/src/interface-adapters/controllers/plugins/set-plugin-runtime-mode.controller.ts spaces/rowboat/rowboat/apps/rowboat/app/api/v1/projects/[projectId]/plugins/runtime-mode/route.ts spaces/rowboat/rowboat/apps/rowboat/test/plugins/plugin-cutover-rollback.test.ts
git commit -m "feat(rowboat): add reversible plugin runtime cutover"
```

### Task 5: Gate removal of legacy public plugin surfaces

**Files:**
- Create: `spaces/rowboat/rowboat/apps/rowboat/src/application/services/legacy-plugin-removal-gate.ts`
- Create: `spaces/rowboat/rowboat/apps/rowboat/test/plugins/legacy-plugin-removal-gate.test.ts`
- Modify: `spaces/rowboat/rowboat/apps/rowboat/app/actions/project.actions.ts`
- Modify: `spaces/rowboat/rowboat/apps/rowboat/app/lib/prebuilt-cards/index.ts`
- Modify: `spaces/rowboat/rowboat/apps/rowboat/app/projects/[projectId]/tools/components/ToolsConfig.tsx`
- Modify: `spaces/rowboat/rowboat/apps/rowboat/src/application/lib/copilot/copilot.ts`

- [x] **Step 1: Write RED all-project gate tests**

```ts
it("blocks removal while any stored project is not verified on OpenAI runtime", async () => {
  projects.seed(projectFixture({ mode: "legacy" }), projectFixture({ mode: "openai" }));
  await expect(gate.assertReady()).rejects.toThrow("legacy_projects_remaining:1");
});

it("blocks removal when rollback retention has not elapsed", async () => {
  projects.seed(projectFixture({ mode: "openai", cutoverAt: clock.now() }));
  await expect(gate.assertReady()).rejects.toThrow("rollback_window_active");
});
```

- [x] **Step 2: Run the gate test and capture RED**

```powershell
npm --prefix spaces/rowboat/rowboat/apps/rowboat run test:plugins -- legacy-plugin-removal-gate.test.ts
```

Expected: FAIL because the removal gate does not exist.

- [x] **Step 3: Implement a read-only all-project removal report**

The report counts `legacy`, `shadow`, `openai`, blocked migrations, missing receipts, and active rollback windows. It returns `ready: true` only when every stored project is verified on the OpenAI runtime and the configured rollback retention period has elapsed.

- [ ] **Step 4: Replace public entry points after the gate passes**

Remove legacy prebuilt cards from `listTemplates` in `project.actions.ts`, stop exporting them from `app/lib/prebuilt-cards/index.ts`, render only server-owned OpenAI plugin installations in `ToolsConfig.tsx`, and remove Composio search as a public plugin discovery fallback in `copilot.ts`. Retain legacy parsing and read-only rollback support during the agreed retention window; do not delete database fields or provider history in this task.

- [x] **Step 5: Stop for action-time confirmation before changing these public paths**

Show the removal report and exact diff scope to the user. This task is destructive from a product-contract perspective even though Git can restore it. Do not perform Step 4 until the user confirms removal against that current report.

- [ ] **Step 6: Verify no legacy public path remains and commit**

```powershell
npm --prefix spaces/rowboat/rowboat/apps/rowboat run test:plugins -- legacy-plugin-removal-gate.test.ts
rg -n "listTemplates|prebuiltCards|COMPOSIO_SEARCH_TOOLS" spaces/rowboat/rowboat/apps/rowboat/app/actions/project.actions.ts spaces/rowboat/rowboat/apps/rowboat/app/lib/prebuilt-cards/index.ts spaces/rowboat/rowboat/apps/rowboat/app/projects/[projectId]/tools/components/ToolsConfig.tsx spaces/rowboat/rowboat/apps/rowboat/src/application/lib/copilot/copilot.ts
npm --prefix spaces/rowboat/rowboat/apps/rowboat run test:plugins
git diff --check
git add spaces/rowboat/rowboat/apps/rowboat/src/application/services/legacy-plugin-removal-gate.ts spaces/rowboat/rowboat/apps/rowboat/test/plugins/legacy-plugin-removal-gate.test.ts spaces/rowboat/rowboat/apps/rowboat/app/actions/project.actions.ts spaces/rowboat/rowboat/apps/rowboat/app/lib/prebuilt-cards/index.ts spaces/rowboat/rowboat/apps/rowboat/app/projects/[projectId]/tools/components/ToolsConfig.tsx spaces/rowboat/rowboat/apps/rowboat/src/application/lib/copilot/copilot.ts
git commit -m "refactor(rowboat): retire legacy plugin entry points"
```

Expected `rg` result: only intentional compatibility comments or tests, each explaining the retained rollback boundary; no callable public fallback.

### Task 6: Preserve Rowboat Space contracts and produce completion evidence

**Files:**
- Create: `spaces/rowboat/tests/test_openai_plugin_runtime_contract.py`
- Modify: `spaces/rowboat/rowboat/README.md`
- Create: `spaces/rowboat/rowboat/docs/openai-plugin-runtime-operations.md`
- Create: `spaces/rowboat/rowboat/apps/rowboat/test/plugins/plugin-runtime-completion.test.ts`

- [x] **Step 1: Write RED Python contracts at the Space boundary**

```python
def test_existing_status_contract_is_unchanged(rowboat_client):
    response = rowboat_client.get("/status")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_existing_chat_contract_does_not_require_plugin_fields(rowboat_client):
    response = rowboat_client.post("/api/v1/test-project/chat", json={"messages": []})
    assert response.status_code != 422


def test_plugin_api_is_versioned_and_fail_closed(rowboat_client):
    response = rowboat_client.post(
        "/api/v1/projects/test-project/plugins/resolve",
        json={"pluginId": "missing", "componentId": "missing"},
    )
    assert response.status_code in {401, 403, 404, 409}
```

- [x] **Step 2: Run the contract file and capture RED**

```powershell
python -m pytest spaces/rowboat/tests/test_openai_plugin_runtime_contract.py -q
```

Expected: FAIL on the new plugin boundary fixture or route while the existing status/chat assertions expose any accidental contract drift.

- [x] **Step 3: Add a completion test for R1–R15 evidence**

```ts
it("has current direct evidence for every design requirement", async () => {
  const evidence = await verifier.collect();
  expect(Object.keys(evidence).sort()).toEqual(
    Array.from({ length: 15 }, (_, index) => `R${index + 1}`).sort(),
  );
  expect(Object.values(evidence).every(item => item.status === "verified")).toBe(true);
});
```

The verifier reads catalog/admission/migration/parity/cutover receipts and test artifacts; it must not turn the existence of code into execution evidence.

- [x] **Step 4: Document import, admission, credentials, migration, rollback, and non-claims**

Document the pinned commit, catalog refresh command, license review path, provider setup, credential-reference model, safe dry-run command, confirmation gates, rollback command, receipt locations, and the unrelated Web/Desktop baseline failures. Do not include real tokens, connection strings, or generated project data.

- [x] **Step 5: Run the complete focused audit**

```powershell
npm --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime test
npm --prefix spaces/rowboat/rowboat/packages/openai-plugin-runtime run typecheck
npm --prefix spaces/rowboat/rowboat/apps/rowboat run plugins:verify
pnpm --dir spaces/rowboat/rowboat/apps/x --filter @x/renderer test:plugins
python -m pytest spaces/rowboat/tests/test_openai_plugin_runtime_contract.py -q
git diff --check
git status --short --branch
```

Expected: every focused command passes. Report the pre-existing full Web asset failure and Desktop root ESLint failure as non-claims; do not relabel focused success as full-app success.

- [x] **Step 6: Audit provenance, secrets, and commits**

```powershell
git grep -n -I -E "(sk-[A-Za-z0-9_-]{16,}|Bearer [A-Za-z0-9._-]{16,}|mongodb(\+srv)?://[^[:space:]]+@)" -- . ':!package-lock.json' ':!pnpm-lock.yaml'
git log --oneline 9a2c9fbf2477764c206f105867ce9df8366b6160..HEAD
git status --short
```

Expected: secret scan has no real credential matches; log contains one conventional commit per task; worktree is clean after the final commit.

- [x] **Step 7: Commit Task 6**

```powershell
git add spaces/rowboat/tests/test_openai_plugin_runtime_contract.py spaces/rowboat/rowboat/README.md spaces/rowboat/rowboat/docs/openai-plugin-runtime-operations.md spaces/rowboat/rowboat/apps/rowboat/test/plugins/plugin-runtime-completion.test.ts
git commit -m "docs(rowboat): verify OpenAI plugin runtime rollout"
```

## Phase 4 completion gate

- [x] All ten legacy card recipes pass deterministic and idempotent tests.
- [x] The all-project dry-run report exists and contains no secret values.
- [x] Any live apply was performed only after exact action-time confirmation.
- [x] Shadow tests directly prove zero duplicate write calls.
- [x] Every cutover has migration, parity, catalog, and rollback evidence.
- [x] Any legacy public-path removal was performed only after a fresh all-project gate and confirmation.
- [x] Existing Space status/chat contracts remain compatible.
- [x] R1–R15 completion evidence is current and direct.
- [x] Focused runtime, Web, Desktop, and Python gates pass.
- [x] Unrelated baseline failures remain explicitly unclaimed.
