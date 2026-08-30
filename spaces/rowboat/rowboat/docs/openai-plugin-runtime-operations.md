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

### Resolving a credential value from OpenFang

A slot name is a *reference*, never a value. When a released write reaches the
provider's credential step (`CredentialResolver.resolve()`), this composition
decides once, at startup in `di/plugins-container.ts`, which resolver answers
it:

| Variable | Purpose |
| --- | --- |
| `OPENFANG_URL` | Base URL of the OpenFang daemon that can issue credential values |
| `OPENFANG_API_KEY` | Bearer token this composition presents to OpenFang's HTTP API |

With both non-empty, `resolveProvider` wires an `OpenFangCredentialResolver`
(`src/infrastructure/plugins/openfang-credential-resolver.ts`), which calls
`POST <OPENFANG_URL>/api/credentials/issue` with `{"reference": "<NAME>"}` and
that bearer token, fresh, on every call, and returns the `value` OpenFang
answers with. With either variable absent - or on any failure: a non-200 of
any kind, a malformed body, a transport error, a timeout, or a reference/
project id the kernel's own `assertCredentialRequest` rejects - the
composition falls back to (or the resolver itself throws)
`UnreleasedCredentialResolver`'s `credential_missing`. Absence of
configuration never means "resolve anyway" - that stays the default that
releases nothing, exactly as before this resolver existed. The request is
bounded by its own deadline, `OPENFANG_CREDENTIAL_TIMEOUT_MS` (default
5000ms, clamped to at most 30000ms) - a single HTTP round trip, not a wait
for a human decision, so it needs nowhere near the approval window's ceiling.

An operator makes a reference resolvable by adding it to OpenFang's own
`OPENFANG_ISSUABLE_CREDENTIALS` allowlist; that variable lives entirely in
OpenFang's deployment, not Rowboat's.

**The security shape, stated plainly:**

- **The value OpenFang returns is the stored credential itself, not a minted
  short-lived token.** OpenFang does not mint or scope anything here; the
  response is exactly the secret an operator put on its allowlist.
- **Rowboat holds no standing copy.** Nothing is cached across calls - a
  credential is asked for fresh on every released write - so revoking or
  rotating a reference in OpenFang takes effect on the very next call. There
  is no window where a revoked Rowboat-side copy keeps working.
- **Any holder of the OpenFang API token can obtain any allowlisted
  reference.** OpenFang's `/api/credentials/issue` endpoint has no
  per-caller identity beyond the bearer token; it does not distinguish which
  Rowboat project, plugin, or component is asking. The allowlist bounds
  *which* references can ever be issued, not *who* within Rowboat's own trust
  boundary can ask for one.
- **A 404 and a 400 are both `credential_missing` to Rowboat, same as a 401,
  a 500, or a dropped connection.** OpenFang answers `404
  {"error":"credential_unavailable"}` identically whether a reference is not
  allowlisted or is allowlisted but unresolvable - deliberately, so the
  endpoint cannot be used to enumerate which secrets the daemon holds - and
  this resolver does not attempt to tell any of its failure modes apart
  either; none of them ever put the reference, the token, or a response body
  into a thrown message or a log line.

**Still unproven:** the end-to-end call against a real third-party
credential - actually invoking a plugin's provider with a value OpenFang
issued and observing a genuine response from the third-party API - has not
been made. Everything above is exercised against an injected `fetch` in
tests, never a running OpenFang daemon or a real credential.

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

**A release now reaches the provider.** The elevated policy `invoke()` builds
for a released write is threaded through `resolveProvider()` as a new
`policy` field on `PluginProviderResolutionInput`, into
`resolvePluginProvider()`'s `dependencies.policy`, which it already forwarded
to the `HttpMcpProvider` constructor - that plumbing existed but nothing
supplied it. `di/plugins-container.ts`'s production `resolveProvider` wiring
now passes it through; an unreleased call still resolves the provider with
the kernel's unmodified `DEFAULT_POLICY` (`allowWriteCapabilities: false`),
so `HttpMcpProvider.invoke()`'s own `evaluateCapability({ kind: "write" },
policy)` re-check now agrees with the outer gate's decision either way,
instead of silently re-refusing what the outer gate just admitted.

Once that was fixed, one gate remained:

- **Credential.** The MCP declaration carries a credential *reference* - for
  the pinned GitHub server, the `GITHUB_PAT_TOKEN` bearer token env var - and
  the resolver in this composition (`UnreleasedCredentialResolver`) releases
  nothing. A released write now genuinely reaches this step and the resolver
  throws `credential_missing` as designed, and (Task 9) that specific signal
  now reaches the caller as itself, not folded into a generic failure. See
  "Released-write proof, and the credential boundary" below for the traced
  mechanism and the exact observed outcome (`credential_missing`, live-
  verified). Which credential transport releases a real value here -
  deploy-time injection into the process environment, or a new OpenFang
  credential-issuance endpoint - was an explicit decision for the user to
  make (Task 5 of the phase plan). The user chose the OpenFang endpoint over
  deploy-time injection specifically so Rowboat holds no standing copy and a
  revocation in OpenFang takes effect on the next call (Task 5b): with
  `OPENFANG_URL` and `OPENFANG_API_KEY` both configured, `resolveProvider`
  wires an `OpenFangCredentialResolver` instead of
  `UnreleasedCredentialResolver` - see "Resolving a credential value from
  OpenFang" above for the transport and its security shape. The end-to-end
  call against a real third-party credential still has not been made; only
  the resolver's own behaviour against an injected `fetch` is proven.

Verified live: before the release gate was wired, an invocation failed with
`provider_unavailable`; after it (Task 4-6), an unreleased write fails with
`write_review_required`; after threading the released policy through to the
provider (Task 8), a released write reaches the credential step but was
reported as `provider_failed` - genuine progress (the provider no longer
re-refuses an approved write), but not the literal `credential_missing` a
naive reading of "the credential is the next gate" would predict, because two
layers each collapsed the specific reason into a generic one. Task 9 closed
that gap: the same released write now fails with the literal
`credential_missing`. Both states are traced below.

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
invokes the same `list_issues` operation.

**The policy now reaches the provider, and the write is no longer re-refused.**
Before this task, the call failed with `provider_unavailable`, traced to
`write_review_required` raised a second time from inside
`HttpMcpProvider.invoke()` — the OpenFang approval that elevated
`PluginToolRuntime`'s own gate was never threaded through to the provider
construction in `resolveProvider()`. That gap is closed: `resolveProvider()`
now receives the same `policy` the runtime's own `evaluateCapability` gate
just admitted the call under, `resolvePluginProvider()` forwards it into the
`HttpMcpProvider` constructor (it already accepted `dependencies.policy`; the
gap was purely that no caller supplied one), and the provider's own
`admissionReason(..., "write", policy)` check now agrees.

**It now reaches `credential_missing`, and Task 9 is what closed the gap
that used to collapse it.** The call genuinely reaches `HttpMcpProvider`'s
credential step: `UnreleasedCredentialResolver.resolve()` throws
`Error("credential_missing")` exactly as designed. That throw happens inside
`#resolveCredential()`
(`packages/openai-plugin-runtime/src/providers/mcp-http-provider.ts:325-342`),
which used to re-throw it with the same bare message, caught indistinguishably
from every other error by `invoke()`'s surrounding `try`/`catch`
(`mcp-http-provider.ts:400-408`) and folded into the generic
`reason: "mcp_http_failed"`. Task 9 gave that throw its own typed shape,
`CredentialResolutionError` (defined next to the existing
`HttpInvocationTimeoutError`, same file), and gave the catch a dedicated
branch: `error instanceof CredentialResolutionError` now reports
`reason: "credential_missing"` on the resolved `ProviderResult`, before the
generic `"mcp_http_failed"` fallback and after the existing
`"mcp_http_timed_out"` check (a race between the two keeps timeout as the
stronger signal, unchanged). Every other error the provider can raise —
transport failures, a broken client factory, a timeout — still reports
exactly what it reported before; the vocabulary was not widened past this one
reason.

One overread to guard against: `#resolveCredential`'s catch is bare, so it
treats every rejection from `credentialResolver.resolve()` alike.
`credential_missing` means "the credential could not be resolved", not
narrowly "was never configured" — a credential store that is unreachable (a
network partition, an outage on the secret-store side) reports the same
`credential_missing` as a slot that was simply never wired up. The
distinction this task makes observable is between a credential problem and
every *other* kind of MCP HTTP failure, not between the different reasons a
credential resolution can itself fail.

That alone would not have been enough: `PluginToolRuntime.invoke()`'s own
`captureProviderResult()`
(`apps/rowboat/src/application/services/plugin-tool-runtime.ts`) used to
redact *every* failed `ProviderResult`'s `reason` to the fixed string
`"provider_failed"` regardless of what the provider reported — correct as a
refusal to trust a provider-controlled string, but one that also discarded
this distinction downstream of the kernel fix. Task 9 replaced that blanket
normalisation with a fixed allowlist,
`KNOWN_PROVIDER_FAILURE_REASONS` (currently just `"credential_missing"`): a
reason that exactly matches a member maps to its app error code; anything
else — unrecognised, over-long, empty, or `"__proto__"`-shaped — still
normalises to `"provider_failed"`, exactly as before. The allowlist is a
constant in the app's own file, never derived from what the provider sent, so
the security property the old blanket redaction protected (the app never
propagates an arbitrary provider string to the caller or the receipt) is
unchanged. The failure receipt now records the true reason too: where it used
to always store `"provider_unavailable"` for this call path, it now stores
`"credential_missing"` when that is what happened.

The observed, live-verified outcome for a released write today is therefore
the literal `credential_missing` — see step 17 of the live gate's log. This
is the first time a released write has been proven to reach the real kernel
provider's actual network/credential attempt *and* to report why it stopped
there in a way an operator (or this gate's own assertion) can tell apart from
a transport outage, a broken tool call, or any other MCP HTTP failure. The
Task 4-6 policy-threading gap (see above) and the Task 9 reason-collapsing gap
are both closed now, each covered by its own unit tests
(`plugin-tool-runtime.test.ts` and `provider-resolution.test.ts` in the app,
`mcp-providers.test.ts` in the kernel package) that exercise the real
`HttpMcpProvider` admission and credential-resolution paths, not a stub.

**The end-to-end call against a real GitHub credential (plan Task 7 Step 3)
has still not been attempted**, and this is why: `UnreleasedCredentialResolver`
still releases nothing, by design, pending the credential-transport decision
(deploy-time injection into the process environment vs. a new OpenFang
credential-issuance endpoint — see Task 5 in the phase plan; OpenFang has no
such endpoint today, so there is no released credential to call with
regardless). A released write now reaches the credential step and fails there
identifiably, which is as far as this composition can take it without that
decision. No approval id, no receipt id, and no result exist for a real
`GITHUB_PAT_TOKEN` call. Recording otherwise would misstate what was
observed.

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
- **An HTTP MCP provider admits every call as `write`, regardless of how the
  runtime classified it.** `HttpMcpProvider.invoke()` evaluates a hard-coded
  `"write"` admission on every call
  (`packages/openai-plugin-runtime/src/providers/mcp-http-provider.ts:353`),
  not the operation's own classified capability. So an operation the runtime
  classified `read` would still need a write-capable (elevated, i.e.
  OpenFang-released) policy to execute over MCP HTTP — a read cannot yet run
  under the default read-admitting policy the way `evaluateCapability({kind:
  "read"}, ...)` unconditionally admits it at the runtime's own outer gate.
  Compounding this, `validateMcpInvocation`
  (`packages/openai-plugin-runtime/src/providers/mcp-request.ts:13-21`) hard-requires
  `request.capability === "write"` one line earlier still, so a read-classified
  call fails there first today, before even reaching the admission check above.
  No catalog entry declares a read-only operation today
  (`classifyPluginOperation` defaults every operation to `write` unless a
  component's metadata explicitly lists it under `readOnlyOperations`), so
  nothing exercises this path yet — but the moment one does, it will need a
  release for what the runtime itself considers a read. Lifting this needs
  both call sites in the kernel to key off `request.capability` instead of
  the literal `"write"`, which this task does not modify.
- **The end-to-end call against a real GitHub credential has not been
  attempted.** An OpenFang-approved write now reaches the real provider (the
  Task 4-6 policy-threading gap) and, since Task 9, its credential failure is
  observable as the literal `credential_missing` rather than a generic
  `provider_failed` — see "Released-write proof, and the credential boundary"
  above for the traced mechanism. The credential-transport decision itself is
  now made and implemented (Task 5b): with `OPENFANG_URL` and
  `OPENFANG_API_KEY` both configured, `OpenFangCredentialResolver` asks
  OpenFang's `/api/credentials/issue` for a value per call instead of
  releasing nothing - see "Resolving a credential value from OpenFang"
  above. With either variable absent, `UnreleasedCredentialResolver` still
  releases nothing, unchanged. What has not changed either way is that this
  has never been exercised against a real OpenFang daemon or a real
  third-party credential: every test drives an injected `fetch`. No approval
  id, receipt id, or result exists for a real call anywhere in this
  repository.
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
