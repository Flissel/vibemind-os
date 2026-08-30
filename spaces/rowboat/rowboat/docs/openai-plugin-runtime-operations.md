# OpenAI plugin runtime operations

How to import, admit, migrate, cut over, and roll back the OpenAI-compatible
plugin runtime. Every command below is safe to read first: the dry-run and
report paths never mutate a project.

## What is pinned

| Item | Value |
| --- | --- |
| Plugin source | `openai/plugins@11c74d6ba24d3a6d48f54a194cd00ef3beea18f9` |
| Plugin count | 180 |
| Catalog digest | `11035eb884d88be51337853010fc67502f8f6ced64287382a3bb56d24a8c524e` |
| Policy version | `rowboat-plugin-policy-v1` |
| Schema version | `rowboat-plugin-schema-v1` |

A catalog whose digest, source commit, or policy version differs from these is
rejected with `catalog_digest_mismatch`. Nothing resolves a plugin from a
floating branch.

### Refreshing the catalog

```sh
npm --prefix packages/openai-plugin-runtime run catalog:sync
```

The sync imports from the pinned commit, revalidates the lock, and writes it
back. Changing the pin is a deliberate code change: update the constants in
`packages/openai-plugin-runtime/src/domain/catalog.ts`, re-run the sync, and
re-run the full evidence gate below.

### Loading the catalog into MongoDB

The web app reads its catalog from the database, never from the lock file, so a
deployment that skips this step fails every plugin path with
`catalog_digest_mismatch`.

```sh
npm --prefix apps/rowboat run mongodb-ensure-indexes
npm --prefix apps/rowboat run plugins:catalog-load
npm --prefix apps/rowboat run plugins:catalog-load -- --verify-only
```

The loader validates the lock, refuses one whose digest is not the pin, and
reads before writing, so running it twice is a no-op that reports
`alreadyStored: true`. Its report carries provenance only: digest, source
commit, policy version, entry count.

`putCatalog` itself is not idempotent and must not be called blindly: its
immutable-insert conflict check re-captures the stored document, and a real
catalog entry payload (up to ~44 KB) exceeds the 16 KB string capture limit, so
a blind second write raises `catalog_entry_conflict`. Because the write is one
transaction, a failed load rolls back completely and can simply be re-run.

### MongoDB must be a replica set

The catalog, every installation, the admission batch, and the migration apply
are each written in one transaction. A standalone `mongod` refuses transactions,
and the loader fails with `repository_transaction_failed` - verified against a
standalone container. Both shipped topologies (`docker-compose.yml` and
`infra/swarm/vibemind-stack.yml`) currently run a standalone `mongo` image, so
converting them to a single-node replica set is a prerequisite:

```sh
# container: mongod --replSet rs0 --bind_ip_all
mongosh --quiet --eval 'rs.initiate({_id:"rs0",members:[{_id:0,host:"<host>:27017"}]})'
```

### The kernel is consumed as a build artifact

`apps/rowboat` resolves `@rowboat/openai-plugin-runtime` to the package's
compiled `dist/`. Any change to the kernel needs
`npm --prefix packages/openai-plugin-runtime run build` before the app sees it,
and a deployment image must run that build.

## Admission and licensing

Components are admitted per policy version. A component without an admitted
license, a resolvable provider, a declared credential reference, or an
execution policy is never invoked. License decisions are visible per component
in the plugin catalog view (`admitted`, `review_required`, `rejected`); a
`review_required` component stays uninstallable until a human decision changes
the policy input, and it is never silently upgraded.

## Credentials

Plugin source content never supplies a secret value. A component declares
credential *slot names*; the operator binds each slot to a value held outside
the plugin content. Receipts store digests and redaction paths, never values.

The Web install flow additionally requires `PLUGIN_UI_PREVIEW_SECRET` (see the
repository README). Migration and cutover require:

| Variable | Purpose |
| --- | --- |
| `PLUGIN_MIGRATION_CONFIRMATION_SECRET` | Signs short-lived migration confirmations |
| `PLUGIN_MIGRATION_ADMIN_USER_IDS` | Comma-separated ids allowed to run all-project scopes |
| `PLUGIN_MIGRATION_ACTOR_USER_ID` | The acting admin id for CLI/report entry points |

## Migration

### Safe dry run (no mutation)

```sh
npm --prefix apps/rowboat run plugins:migrate -- --dry-run --scope fixtures
npm --prefix apps/rowboat run plugins:migrate -- --dry-run --scope all --output .artifacts/migration-report.json
```

The dry run reports per project: recipe id, source revision and digests, target
installations, rollback snapshot digest, and any blockers. It applies nothing,
writes no project document, and contains no credential values.

### Applying one project

Applying requires a signed, short-lived confirmation that binds the exact
preview it was issued for. A stale preview is rejected with
`migration_preview_stale`, and a replayed confirmation with
`migration_confirmation_replayed`.

```sh
npm --prefix apps/rowboat run plugins:migrate -- --apply --project-id <uuid> --confirmation-token <token>
```

The apply is atomic: installations, admissions, the migration record, the
replay nonce, the idempotency record, and the project pointer are written in
one transaction, or nothing is.

## Using a plugin from the Rowboat UI

The plugins page lists the pinned catalog. Installing an admitted plugin writes
an installation with the provider bindings the catalog declares. Each executable
component of an installed plugin then offers **Add to workflow**, which appends
a tool to the project draft workflow bound to that component.

A tool added this way carries a **native** binding: it has no legacy tool it
could displace, so the runtime mode gate below does not apply to it and it is
executable in every mode. A binding written by a cutover carries **migration**
instead and stays gated. A binding with no readable origin is treated as
migrated, which is the gated reading.

Only a component the catalog admits can be added. A component whose admission is
`review_required` - which is where the Slack, Gmail, and Google connectors
currently sit - is refused with `component_not_admitted` until that review is
decided. Adding the same component twice is a no-op; a different tool already
holding the generated name is a conflict rather than something to overwrite.

### Running the UI locally

```sh
docker run -d --name rowboat-rs -p 127.0.0.1:27017:27017 mongo:7 --replSet rs0 --bind_ip_all
docker exec rowboat-rs mongosh --quiet --eval 'rs.initiate({_id:"rs0",members:[{_id:0,host:"127.0.0.1:27017"}]})'
npm --prefix packages/openai-plugin-runtime run build
npm --prefix apps/rowboat run mongodb-ensure-indexes   # creates the indexes, then does not exit
npm --prefix apps/rowboat run plugins:catalog-load
npx --prefix apps/rowboat next dev                      # not `npm run dev`, see below
```

`npm run dev` runs Next with Turbopack, which does not resolve the workspace
package `@rowboat/openai-plugin-runtime`: every plugin page fails with
`Module not found` and returns 500. `next dev` without Turbopack resolves it.
`next build` does not use Turbopack and is unaffected.

With `USE_AUTH` unset the plugin actions run as `guest_user`, which needs a
`project_members` row for the project, otherwise every action is `forbidden`.

A plugin is installable from the UI only while its *plugin-level* status is
`available`, which means every one of its components is admitted and available.
A single `review_required` component makes the whole plugin
`partially_available` and blocks installing the admitted ones - `github` is in
that bucket today. 117 of the 180 pinned plugins are installable as they stand.

## Runtime modes and cutover

Each project carries a plugin runtime state with a `mode` and a `revision`.
A project stored before the runtime existed has no state and resolves to
`legacy`; reads never rewrite it.

| Mode | Who executes tools |
| --- | --- |
| `legacy` | The existing workflow tools. Plugin bindings are stripped from the executable configuration. |
| `shadow` | Still the legacy tools. The plugin representation is compared as descriptors; a read-classified provider may additionally be executed read-only. A write-capable or unclassified provider is never executed a second time. |
| `openai` | The plugin runtime. |

Admitted transitions are `legacy -> shadow`, `shadow -> openai`, and
`openai -> legacy`. Everything else, including a direct `legacy -> openai`
jump, is rejected with `runtime_mode_transition_rejected`.

```sh
curl -X POST "$ROWBOAT_URL/api/v1/projects/<projectId>/plugins/runtime-mode" \
  -H "content-type: application/json" \
  -d '{"mode":"shadow","expectedRevision":0,"catalogDigest":null,"migrationRecordId":null,"parityReceiptId":null}'
```

Cutover materializes the plugin bindings: the legacy tools the applied
migration mapped are rewritten with their `pluginBinding`, in the *same*
conditional update as the mode, so the mode and the tools can never disagree.
The binding comes from the installation, which is where the kernel keeps the
admitted provider binding. A tool that drifted from the one the recipe resolved
against is refused (`materialization_source_drift`), and a missing installation
or component binding is refused (`materialization_binding_missing`); in both
cases nothing is written and the project stays where it was. A materialized
tool is bound write-capable, because a provider binding carries no read/write
classification and an unknown effect is never treated as read-only.

Rollback restores the retained snapshot the apply wrote to
`plugin_migration_rollbacks`, again in the same conditional update. A missing or
foreign snapshot fails closed with `rollback_snapshot_unavailable` rather than
reconstructing a workflow.

Cutover to `openai` additionally requires, in one request:

- `catalogDigest` equal to the pinned catalog digest
- `migrationRecordId` of an applied or verified migration for *this* project,
  with zero blockers and at least one target installation
- `parityReceiptId` of a successful parity receipt for *this* project
- a rollback snapshot digest recorded on that migration

Anything missing fails closed with `cutover_evidence_required`. The write is a
compare-and-swap on `expectedRevision`; a losing swap returns
`plugin_runtime_state_conflict` and the caller must re-read before retrying.

### Rollback

```sh
curl -X POST "$ROWBOAT_URL/api/v1/projects/<projectId>/plugins/runtime-mode" \
  -H "content-type: application/json" \
  -d '{"mode":"legacy","expectedRevision":<n>,"catalogDigest":null,"migrationRecordId":null,"parityReceiptId":null}'
```

Rollback returns authority to the legacy tools, restoring the retained snapshot
where a cutover materialized bindings, and records its own receipt stamped with
`rolledBackAt`.

## What still blocks an actual call

The provider is wired: an HTTP MCP component resolves to a real provider built
from the pinned catalog record, through a registry that only admits the exact
binding. `PluginToolRuntime` now has a release gate (`releaseWrite`): given a
project-wide `OPENFANG_URL`, a write is put to OpenFang for approval instead of
being refused outright, and an approval elevates that one call's local policy
so the runtime's own `evaluateCapability` check admits it. Without a release
(no `OPENFANG_URL`, or OpenFang unreachable, denied, or expired) the call still
fails closed with `write_review_required` - unchanged from before.

**A release does not yet reach the provider.** `PluginToolRuntimeDependencies.resolveProvider`
receives no signal that a write was released: `resolvePluginProvider()` (in
`src/infrastructure/plugins/provider-resolution.ts`) always constructs the
provider with the kernel's own unmodified `DEFAULT_POLICY`
(`allowWriteCapabilities: false`), because neither the test double nor the
production wiring in `di/plugins-container.ts` passes a `policy` override
through. `HttpMcpProvider.invoke()` then re-runs the identical
`evaluateCapability({ kind: "write" }, policy)` check the outer gate just
cleared, against that unmodified policy, and throws its own
`write_review_required` - which `PluginToolRuntime`'s `classifyFailure` (it
only special-cases the literal message `"credential_missing"`) reports to the
caller as `provider_unavailable`. **Concretely: today, no plugin write can run
even with a real OpenFang approval and a real credential**, because the
approval never reaches the component that would need it. This was found by
actually running the released-write case against the live gate below (see
"Released-write proof, and the credential boundary"), not predicted; no
mocked-provider unit test exercises the real kernel provider construction
path, so nothing caught it earlier. Threading the elevated policy from the
release decision through to `resolveProvider()` is unimplemented work, not a
decision pending on the user the way the credential transport below is.

Once that is fixed, one gate remains:

- **Credential.** The MCP declaration carries a credential *reference* - for
  the pinned GitHub server, the `GITHUB_PAT_TOKEN` bearer token env var - and
  the resolver in this composition (`UnreleasedCredentialResolver`) releases
  nothing, so a call that reached the provider would fail with
  `credential_missing`. Which credential transport releases a real value here
  - deploy-time injection into the process environment, or a new OpenFang
  credential-issuance endpoint - is an explicit decision for the user to make
  (Task 5 of the phase plan); no code exists for either option yet.

Verified live: before the release gate was wired, an invocation failed with
`provider_unavailable`; after it (Task 4-6), an unreleased write fails with
`write_review_required`; and a released write, proven for the first time here,
fails with `provider_unavailable` again, for the different reason above.

Two component kinds still cannot execute here at all, and are reported
unavailable rather than approximated: a **process MCP** server needs a verified
execution root from the content store, which the web runtime does not mount, and
an **app** component needs the OpenAI connector bridge, which the provider
registry refuses by design. Of the 180 pinned plugins that leaves the HTTP MCP
servers as the executable set.

## Receipts

All receipts live in the `plugin_receipts` collection, keyed by `receiptId`:

| Prefix / type | Meaning |
| --- | --- |
| `type: "import"` / `"install"` | Catalog import and installation provenance |
| `type: "execution"`, `parity:<digest>` | Shadow parity comparison evidence |
| `type: "migration"`, `runtime-mode:<digest>` | A runtime mode transition, including rollback |

Receipts are immutable and redacted: arguments and results are declared under
`redactions` and stored only as digests.

## Removing the legacy public surfaces

The removal gate is a read-only all-project report. It reports `ready` only
when every stored project is verified on the OpenAI runtime *and* the 14-day
rollback retention has elapsed for each of them. Evidence belonging to a
different project does not count, an openai project with no recorded cutover
keeps its rollback window open, an empty deployment is not ready, and a scan
larger than 1000 projects fails closed rather than producing a partial report.

Run it from an admin context via `legacyRemovalReport()` in
`di/plugin-migration-container.ts`. Removing the legacy entry points is a
separate, explicitly confirmed step and has **not** been performed.

## Evidence gate

```sh
npm --prefix apps/rowboat run plugins:evidence   # records; run twice
npm --prefix apps/rowboat run test:plugins
npm --prefix packages/openai-plugin-runtime run typecheck
```

### Live MongoDB gate

```sh
docker run -d --name rowboat-plugin-demo -p 27018:27017 mongo:7
ROWBOAT_LIVE_MONGO_URL=mongodb://127.0.0.1:27018/rowboat \
  npx vitest run test/plugins/live-mongo-runtime.test.ts
```

Runs the index bootstrap, both repositories, the runtime mode use case, the
removal gate, and shadow parity against a real MongoDB. Without the variable it
reports itself skipped. Run it before any deployment: a fake collection cannot
show what the real driver does to the document it is handed, and two defects
that every fake-backed test passed were only visible here — the driver assigns
`_id` onto the caller's frozen document, and a stored receipt comes back as its
canonical JSON string rather than an object.

### Released-write proof, and the credential boundary

The gate's last step now also proves the release path against the real
kernel provider (`HttpMcpProvider`, resolved through the real catalog, the
real installation, and the real admission record for the pinned GitHub MCP
server — nothing about this step is a mock). It builds a second
`PluginToolRuntime` sharing every dependency of the one used earlier in the
gate, except `releaseWrite`, which is stubbed to approve unconditionally, and
invokes the same `list_issues` operation. **This currently fails, and the
failure is recorded rather than hidden**: instead of reaching the credential
boundary (`credential_missing`, the intended and asserted outcome), the call
fails with `provider_unavailable`. The real reason, traced against the actual
thrown error, is `write_review_required` — raised a second time, from inside
`HttpMcpProvider.invoke()` itself, because the OpenFang approval that
elevated `PluginToolRuntime`'s own gate is never threaded through to the
provider construction in `resolveProvider()`. See "What still blocks an
actual call" above for the full mechanism. This is a genuine, previously
unverified gap in the Task 4-6 release wiring, found by this test, not by
inspection — no unit test exercises the real provider construction path, so
nothing had caught it before this gate ran with a released write.

**The end-to-end call against a real GitHub credential (plan Task 7 Step 3)
has not been attempted**, and this is why: it was already out of scope
pending the credential-transport decision (deploy-time injection into the
process environment vs. a new OpenFang credential-issuance endpoint — see
Task 5 in the phase plan; OpenFang has no such endpoint today, so there is no
released credential to call with regardless). Running it now would in any
case get no further than the gap above: a released write does not yet reach
the credential step at all, so there is nothing downstream to call with a
real `GITHUB_PAT_TOKEN` yet. No approval id, no receipt id, and no result
exist for that call. Recording otherwise would misstate what was observed.

### Recorded evidence

`plugins:evidence` runs the app suite, the runtime package suite, and the Space
contract file, writing `.artifacts/plugin-app-tests.json`,
`packages/openai-plugin-runtime/.artifacts/plugin-runtime-tests.json`, and
`.artifacts/plugin-space-contract.xml`. The completion verifier reads those
recorded runs and marks a design requirement `verified` only when every test
bound to it *passed* in them. The first run on a clean tree has nothing to read
and reports itself skipped; the second run verifies. A skipped or missing test
is never counted as evidence.

## Non-claims

These are true limits of the current state, not oversights to work around:

- **The app-wide TypeScript check depends on a generated file.** `npx tsc
  --noEmit` in `apps/rowboat` is clean once `next-env.d.ts` exists; that file is
  gitignored and written by Next on its first run, and its reference to
  `next/image-types/global` is what declares image modules. In a tree where Next
  has never run it reports six errors about `@/public/logo.png`,
  `logo-only.png` and `mascot.png` — the images are present and tracked, the
  declaration is not. An earlier version of this document called those six a
  permanent baseline; that was wrong. None of this is a claim that `next build`
  succeeds.
- **The Desktop root ESLint run is red before this feature** because every input
  file is ignored.
- **`spaces/rowboat/__init__.py` cannot be imported** on `master` either: it
  imports `.config`, `.agents`, and `.broadcast`, which are not tracked. The
  Space contract test loads `mcp_server.py` by path instead of repairing that
  unrelated baseline.
- **The live Space contract checks are skipped unless `ROWBOAT_CONTRACT_URL`
  points at a reachable Rowboat.** A skip is reported as unrun.
- **No project has been migrated, cut over, or rolled back on a live
  deployment.** Every gate above is proven by tests against fakes and fixtures.
  The removal gate has never been run against real deployment data.
- **An OpenFang-approved write does not yet reach a real provider.** The
  release decision elevates only `PluginToolRuntime`'s own gate; it is never
  passed to `resolveProvider()`, so the real provider (`HttpMcpProvider`)
  re-applies the kernel's unmodified default policy and blocks the same write
  again, surfacing as `provider_unavailable`. See "Released-write proof, and
  the credential boundary" above for the traced mechanism. Every unit test
  that shows a released write completing (`plugin-tool-runtime.test.ts`) does
  so against a stub provider that carries no capability check of its own, so
  none of them exercise this path; the live gate above is the only place that
  currently does.
- **The end-to-end call against a real GitHub credential has not been
  attempted.** It depends on both the fix above and a credential-transport
  decision the user has not made (deploy-time injection vs. a new OpenFang
  issuance endpoint - OpenFang has no such endpoint today). No approval id,
  receipt id, or result exists for a real call anywhere in this repository.
- **`shadow -> legacy` is not an admitted transition.** The plan's table admits
  only the three transitions listed above, so a project in shadow returns to
  legacy by going through a cutover and rollback. Widening the table is a
  separate decision.
- **The OpenFang write-release approval window is capped at four minutes.**
  `OPENFANG_APPROVAL_TIMEOUT_MS` (default 120 000 ms) configures how long
  OpenFang is given to collect a human decision, but it is clamped to at most
  240 000 ms (four minutes) regardless of what is configured. The reason is
  structural, not a missing knob: a call that may wait on a human release
  still runs under the same single deadline as the rest of the tool
  invocation, and `PluginToolRuntime`'s constructor caps that deadline at
  300 000 ms (five minutes) - the runtime's deadline is derived as the
  approval window plus a fixed margin for the rest of the call (the release
  call's own overhead, then admission/credential/provider work), so a window
  above four minutes would leave no room for that margin. A longer human
  approval window needs per-phase budgets, so the release wait stops counting
  against the provider's own time budget; that does not exist yet, so four
  minutes is the real limit today, not an oversight to work around.
