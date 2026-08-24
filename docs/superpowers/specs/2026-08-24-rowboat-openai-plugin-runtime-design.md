# Rowboat OpenAI Plugin Runtime Design

**Status:** Approved design, ready for implementation planning

**Date:** 2026-08-24

**Rowboat base:** `Flissel/vibemind-os@9a2c9fbf2477764c206f105867ce9df8366b6160`

**Plugin source:** `openai/plugins@11c74d6ba24d3a6d48f54a194cd00ef3beea18f9`

## 1. Objective

Replace Rowboat's public plugin layer with a runtime that consumes the pinned
OpenAI plugin format. The replacement must support `.codex-plugin/plugin.json`,
skills, agents, commands, MCP servers, apps, hooks, and assets with explicit
Rowboat semantics.

The migration preserves existing Rowboat behavior until parity is proven. It
must not delete or silently reinterpret project data, expose secrets, execute
unadmitted code, or claim an unavailable OpenAI connector is usable.

The final supported plugin path is the new OpenAI-compatible runtime. Existing
Prebuilt Assistant, Composio, and Custom MCP implementations may remain behind
provider adapters during migration, but they no longer define the public plugin
model after cutover.

## 2. Current-State Evidence

The pinned OpenAI source contains:

- 180 required plugin manifests;
- 72 plugins with skill bundles;
- 154 plugins with `.app.json` connector references;
- 14 plugins with agent directories;
- 6 plugins with command directories;
- 8 plugins with `.mcp.json` definitions; and
- 2 plugins with command-executing hooks.

License declarations at the pin are: 164 MIT, 6 Apache-2.0, 5 Proprietary,
2 UNLICENSED, 1 empty, 1 Apache-2.0 AND CC-BY-4.0, and 1 Figma developer-terms
reference. The upstream repository has no root license that overrides these
per-plugin declarations.

Rowboat currently has:

- 10 static Prebuilt Assistant workflow cards;
- project-scoped Composio connected accounts and tools;
- project-scoped URL-only Custom MCP servers;
- Composio trigger deployments;
- mock, webhook, image, MCP, and Composio tool execution branches;
- a separate desktop connector and credential surface; and
- Python client, voice, worker, upload, search, and status contracts outside the
  vendored TypeScript applications.

There is no existing Rowboat JavaScript test suite. The clean worktree baseline
is already red for unrelated reasons:

- Web `tsc --noEmit` cannot resolve five referenced image assets.
- Desktop `eslint .` reports that every input file is ignored.

Implementation therefore adds focused plugin-runtime gates and records these
two baseline failures as non-regression evidence. It does not silently claim a
green full-app baseline.

## 3. Scope

### 3.1 In scope

- A framework-independent TypeScript plugin kernel shared by Rowboat Web and
  Desktop.
- A pinned, digest-bearing OpenAI catalog snapshot.
- Strict parsing, component discovery, path containment, and admission.
- Project-scoped install, enable, disable, update, and status operations.
- Explicit Rowboat semantics for every OpenAI plugin surface.
- Provider adapters for MCP, OpenAI app references, and temporary legacy
  provider capabilities.
- Copy-on-write migration of existing projects and ten Prebuilt Assistants.
- Shadow resolution, parity reporting, per-project cutover, and rollback.
- Preservation of the Space-level Python Rowboat contracts.
- Focused automated tests, provenance, security checks, and operator docs.

### 3.2 Out of scope

- Pretending that OpenAI-managed connector IDs are publicly callable APIs.
- Automatically accepting third-party terms or licenses.
- Importing credentials or secret values from plugin files.
- Enabling arbitrary local process or hook execution by default.
- Running legacy and new write-capable tools twice for comparison.
- Repairing unrelated Web assets or Desktop ESLint configuration in this task.
- Changing Brain, OpenFang, production deployment, or external provider
  authority.

## 4. Requirements

### R1. Format compatibility

The importer must discover the required `.codex-plugin/plugin.json` and all
declared or conventionally discovered component surfaces. Folder name and
manifest name must match. Relative paths must resolve inside the plugin root;
absolute paths, traversal, symlink escapes, malformed JSON/YAML/frontmatter,
duplicate namespaces, and unsupported schema shapes fail closed.

### R2. Provenance and reproducibility

Every catalog snapshot and installation records source URL, source commit,
plugin name, plugin version, manifest digest, full content-tree digest, import
time, schema version, and policy version. Runtime resolution uses a
content-addressed local store, not a mutable branch or unpinned URL.

### R3. License admission

License policy is evaluated per plugin before content is installed or exposed.
MIT and Apache-2.0 are allowlisted initially. Composite, proprietary,
UNLICENSED, empty, and `LicenseRef-*` declarations require an explicit local
policy decision. An unavailable or rejected license state is visible and cannot
be bypassed by enabling a component directly.

### R4. Secret handling

Plugin content may name environment variables, OAuth resources, or credential
slots, but may not carry secret values into the catalog, database, logs, API
responses, or receipts. Install records contain secret references only.
Provider adapters resolve values at execution time through the existing local
credential boundary.

### R5. Skills

Each skill is parsed as an instruction bundle with metadata, trigger rules, its
complete `SKILL.md`, and referenced local resources. Skills are loaded
progressively and namespaced by plugin. A skill changes agent instructions; it
does not itself grant tool, filesystem, network, process, or approval authority.

### R6. Agents

OpenAI YAML agent metadata becomes Rowboat composer metadata. Markdown agent
definitions become immutable agent instruction templates. Imported agents are
namespaced and materialized into a project workflow only through an explicit
install or migration action. Unsupported agent fields remain preserved as
opaque source metadata and produce a component warning rather than invented
behavior.

### R7. Commands

Markdown commands become named, user-invoked prompt actions. They are never
triggered implicitly. Template and convention files are retained as command
resources rather than presented as runnable commands. A command receives only
the tools and skills admitted for its installed plugin and project.

### R8. MCP servers

The runtime supports the pinned source shapes:

- HTTP with URL and optional OAuth resource;
- HTTP with a bearer-token environment-variable reference; and
- local process definitions with command, arguments, working directory,
  environment-variable references, and timeout.

HTTP servers use Streamable HTTP with an explicit SSE compatibility fallback.
Redirects, hosts, OAuth resources, and token references are policy checked.
Local process MCP is disabled by default and requires a component-specific
admission decision. The runtime passes a minimal environment and enforces
working-directory containment and timeouts.

### R9. Apps

An `.app.json` entry is catalog metadata that references an OpenAI connector ID.
It becomes executable only when the provider registry has an admitted adapter
for that exact app capability. An app may resolve to an admitted MCP server, a
Rowboat-native provider, or a future authorized OpenAI connector bridge.
Otherwise its component status is `unavailable` with a stable reason such as
`provider_unavailable`; the plugin itself may still expose independent skills
or commands.

### R10. Hooks

Hooks map to a versioned Rowboat event vocabulary. The initial vocabulary must
cover the pinned `PostToolUse` and `Stop` events and preserve unmatched source
events as unsupported metadata. Matchers operate on canonical Rowboat action
types.

Command hooks are disabled by default. Enabling one requires explicit policy,
an admitted runner, plugin-root containment, a minimal environment, timeout,
captured bounded output, and a receipt. No implicit shell is selected. A hook
failure is visible and cannot convert a failed tool action into success.

### R11. Assets

Assets are served from the content-addressed plugin store. Paths are validated,
MIME types are restricted, file sizes are bounded, and SVG/HTML content is
treated as untrusted. Missing or rejected optional assets use a Rowboat-owned
placeholder without changing component capability status.

### R12. Shared product semantics

The Web application is the authoritative plugin service and persistence layer.
Desktop consumes its catalog, installation, credential-slot, and component
status APIs. A framework-independent package at
`spaces/rowboat/rowboat/packages/openai-plugin-runtime` owns schemas,
validation, normalization, policy decisions, digests, and resolution. UI code
must not reimplement admission logic.

### R13. Legacy migration

Migration is copy-on-write and idempotent. Each project receives a migration
record containing source workflow digest, target plugin composition, unresolved
capabilities, target digest, timestamp, and migration version. Existing draft
and live workflows remain intact until cutover.

The ten cards migrate as follows:

| Legacy card | Target composition |
| --- | --- |
| GitHub data to spreadsheet | GitHub + Google Drive/Sheets provider + Slack |
| GitHub issue to Slack | GitHub + Slack |
| GitHub PR to Slack | GitHub + Slack |
| Interview scheduler | Google Calendar + Google Drive/Sheets provider |
| Meeting prep assistant | Gmail + admitted search provider |
| Eisenhower email organizer | Gmail |
| Reddit on Slack | Slack + temporary Reddit provider adapter |
| Tweet assistant | temporary X/Twitter provider + admitted search provider |
| Twitter sentiment | temporary X/Twitter provider adapter |
| Customer support | imported agent instructions plus explicit mock-tool policy |

Temporary Reddit, X/Twitter, Sheets, search, and mock adapters are provider
bindings behind the OpenAI-compatible runtime. They are not public legacy
plugin types. Removing them requires either an equivalent admitted plugin
provider or an explicit product decision that accepts loss of parity.

### R14. Space-level compatibility

Existing Python Rowboat client, voice, knowledge search, upload, worker, and
status entry points remain contract compatible. They call project workflows and
do not depend on the internal plugin storage representation. Focused contract
tests guard their request paths, success/failure envelopes, and fail-closed
behavior without requiring live provider credentials.

### R15. Cutover and rollback

Cutover is per project and requires all of the following:

1. A valid pinned catalog and admitted installation set.
2. A successful idempotent migration with matching source and target digests.
3. No unresolved required capability.
4. Read-only shadow resolution parity for agent, prompt, tool, and provider
   bindings.
5. Behavioral tests for representative success and failure paths.
6. A stored rollback pointer to the untouched legacy workflow.

Shadow mode compares resolution and mocked/provider-contract behavior. It never
duplicates a live write-capable tool call. A failed gate leaves the project on
the legacy path and records the reason.

After every migrated project passes the gate, the old public Prebuilt
Assistant, Composio-tool, and Custom-MCP configuration surfaces are removed.
Legacy database fields remain read-only for one versioned rollback window.
Their later destructive cleanup is a separate migration requiring explicit
authorization.

## 5. Architecture

### 5.1 Plugin kernel

`openai-plugin-runtime` is a strict, framework-independent TypeScript package
with these units:

- **source-reader:** reads an already pinned source tree; it performs no network
  or Git mutation;
- **manifest-validator:** validates core metadata and component pointers;
- **component-discovery:** discovers skills, agents, commands, MCP, apps, hooks,
  and assets;
- **path-guard:** enforces plugin-root containment and rejects unsafe links;
- **digest-service:** produces deterministic manifest and tree digests;
- **license-policy:** returns admitted, review-required, or rejected;
- **capability-policy:** evaluates network, credential, process, hook, and write
  capabilities;
- **normalizer:** creates the canonical Rowboat plugin model;
- **resolver:** resolves admitted components for a project without executing
  them; and
- **receipt-builder:** creates bounded, secret-free import, install, migration,
  and execution receipts.

Each unit exposes Zod-validated inputs and outputs. Unknown input is narrowed at
the boundary; new code does not use `any`.

### 5.2 Catalog and content store

The repository tracks a small catalog lock containing metadata, component
availability, source commit, and digests. Full plugin content lives in a local,
content-addressed `ROWBOAT_PLUGIN_STORE` outside Git. An explicit operator sync
command imports from a checked-out source at the configured pin and refuses
dirty or mismatched source content.

Catalog sync produces a reviewable diff and never activates plugins. Install is
a separate project-scoped action.

### 5.3 Persistence model

MongoDB repositories use these exact collection/domain-record pairs:

- `plugin_catalog_snapshots` / `PluginCatalogSnapshot`;
- `plugin_catalog_entries` / `PluginCatalogEntry`;
- `plugin_installations` / `PluginInstallation`;
- `plugin_component_admissions` / `PluginComponentAdmission`;
- `plugin_credential_slots` / secret-free `PluginCredentialSlot` references;
- `plugin_migration_records` / `PluginMigrationRecord`; and
- `plugin_receipts` / `PluginReceipt`.

Records are immutable by digest where practical. Mutable enablement state uses
optimistic concurrency. Project reads tolerate absent plugin fields so old
projects remain valid before migration. Collection names use the existing
Rowboat naming convention. The domain records above are not embedded into
legacy workflow documents.

### 5.4 Provider registry

All executable external capabilities resolve through one registry:

- `mcp-http`;
- `mcp-process`;
- `rowboat-native`;
- `legacy-composio-adapter`; and
- `openai-connector-bridge` reserved but unavailable until an authorized API
  exists.

Provider selection is explicit and receipt-bearing. There is no silent fallback
from a requested OpenAI connector to a different provider. A migration recipe
may explicitly select a temporary legacy adapter and records that fact.

### 5.5 API and UI

The Web service exposes versioned endpoints for catalog status, plugin detail,
install preview, installation mutation, credential-slot status, migration
preview, migration execution, parity report, cutover, and rollback.

Mutation endpoints require the existing project authorization boundary plus an
idempotency key. Preview endpoints are read-only. Responses expose capability
and reason codes, never secret values.

Web and Desktop show the same states: `available`, `review_required`,
`installed`, `partially_available`, `unavailable`, `migration_required`, and
`error`. Partial availability is component-specific and must not be displayed
as full installation success.

## 6. Error Semantics

Errors use stable classes and reason codes:

- `source_mismatch`;
- `manifest_invalid`;
- `path_escape`;
- `digest_mismatch`;
- `license_review_required`;
- `license_rejected`;
- `provider_unavailable`;
- `credential_missing`;
- `process_not_admitted`;
- `hook_not_admitted`;
- `component_unsupported`;
- `migration_conflict`;
- `parity_failed`; and
- `rollback_unavailable`.

Validation and policy failures are data, not crashes. Execution failures retain
their failure status through hooks, UI, receipts, and API responses. Logs use
the project-local logger and redact arguments marked sensitive by provider or
credential schemas.

## 7. Testing Strategy

Implementation is test-driven. The first implementation change establishes a
focused test command for the new package.

Required test layers:

1. **Schema fixtures:** valid and invalid manifests for every pinned component
   shape.
2. **Path/security tests:** traversal, absolute paths, symlink escape, oversized
   assets, unsafe MIME, and secret-value rejection.
3. **Policy tables:** all license classes and process/network/hook combinations.
4. **Golden imports:** representative Figma, GitHub, Superpowers,
   build-web-apps, Replay, and app-only plugins.
5. **Full-catalog conformance:** all 180 pinned manifests parse; every component
   ends in an explicit admitted, review-required, unavailable, or unsupported
   state.
6. **Provider behavior:** counters and raise-on-call guards prove unavailable or
   unadmitted providers are never invoked.
7. **Hook behavior:** event matching, timeout, environment isolation, bounded
   output, and failure preservation.
8. **Migration fixtures:** all ten cards produce deterministic, idempotent
   migration records.
9. **Parity tests:** agent, prompt, tool, provider, and external Python contract
   behavior.
10. **API tests:** authorization, idempotency, concurrency, reason codes, and
    secret redaction.
11. **UI tests:** shared status rendering and guarded mutation paths for Web and
    Desktop.
12. **Cutover tests:** no activation before all gates, no duplicate write calls,
    and deterministic rollback.

The pre-existing Web asset and Desktop ESLint failures are rerun and reported
separately. Focused plugin gates must be green; unrelated red baselines never
support a broad clean-build claim.

## 8. Delivery Sequence

1. Add the isolated plugin package and focused test harness.
2. Implement schemas, source reading, containment, digests, and license policy.
3. Import and classify all 180 pinned plugins.
4. Add skill, agent, command, and asset normalization.
5. Add MCP, app, provider, and hook admission without live activation.
6. Add persistence, service APIs, and secret-free receipts.
7. Add Web and Desktop catalog/status surfaces.
8. Implement ten idempotent legacy migration recipes.
9. Add shadow resolution and parity reports.
10. Enable per-project cutover and rollback.
11. Remove legacy public configuration paths only after every stored Rowboat
    project containing legacy plugin state passes the parity gate.
12. Run the completion audit and produce the final handoff.

Each implementation slice must be small, independently tested, and committed
with a conventional commit. No slice may broaden runtime authority merely to
make a fixture pass.

## 9. Completion Evidence

The goal is complete only when current-state evidence proves:

- the catalog is pinned to the specified OpenAI commit and all 180 manifests
  have explicit conformance results;
- every named plugin surface has implemented Rowboat semantics and tests;
- unsafe or unavailable components fail closed and are not invoked;
- no plugin content, database record, receipt, or log contains imported secret
  values;
- all ten legacy cards and existing projects have deterministic migration and
  parity evidence;
- Web and Desktop consume the same authoritative installation states;
- Space-level Python contracts remain compatible;
- old public plugin paths are disabled only after the parity gate;
- rollback is proven without destructive cleanup;
- focused tests are green and pre-existing baseline failures are reported as
  non-claims; and
- the feature worktree contains only intentional commits and a documented
  upstream/plugin-source provenance chain.
