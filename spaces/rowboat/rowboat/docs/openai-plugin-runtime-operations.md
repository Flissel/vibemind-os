# OpenAI plugin runtime operations

How to import, admit, migrate, cut over, and roll back the OpenAI-compatible
plugin runtime. Every command below is safe to read first: the dry-run and
report paths never mutate a project.

**Every plugin call today requires a human approval, capped at four
minutes.** No catalog entry declares any operation read-only yet
(`classifyPluginOperation` defaults every operation to `write` unless a
component's metadata lists it under `readOnlyOperations`, and none does);
both places that construct a provider binding hard-code `capability:
"write"` regardless of the component
(`application/services/plugin-binding-materialization.ts:108` and
`application/use-cases/plugins/add-plugin-tool.use-case.ts:113`); and
`HttpMcpProvider.invoke()` itself hard-codes a `"write"` admission check on
every call, not the operation's own classified capability
(`packages/openai-plugin-runtime/src/providers/mcp-http-provider.ts:353`).
So every executable call goes through the OpenFang release gate and waits on
a human decision, and that wait is clamped to four minutes regardless of
`OPENFANG_APPROVAL_TIMEOUT_MS` - see "What still blocks an actual call"
below, and the read/write and approval-window entries under "Non-claims"
for the full mechanism.

## What is pinned

| Item | Value |
| --- | --- |
| Plugin source | `openai/plugins@11c74d6ba24d3a6d48f54a194cd00ef3beea18f9` |
| Plugin count | 180 |
| Catalog digest | `5c9ea0690406824b3e78751ee0bc7765e3d4a7d0ae40afdbd9f4757c22666c94` |
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

**A re-pin leaves a hazard for any database that already holds rows under
the OLD digest.** `requireCatalogEntryForInstallation` matches a plugin's
stored catalog entry by content tuple
(`manifestDigest`/`treeDigest`/`sourceCommit`/`pluginVersion`/`policyVersion`)
across *every* stored catalog digest, and that tuple stays byte-identical
across a re-pin for a plugin whose components did not themselves change
shape - so a database still holding the superseded digest's
`plugin_catalog_snapshots` and `plugin_catalog_entries` rows matches two
documents instead of one and fails `requireCatalogEntryForInstallation` with
`installation_catalog_mismatch` for every plugin's installation, not only
the ones the re-pin actually changed. Remediation: delete the superseded
digest's rows from both collections - and only those rows; no project,
installation, admission, credential-slot, or receipt document needs
touching. This happened live once; see `E2E-PROOF.md` Part IV, §IV.2.2, for
the exact collision and fix.

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
standalone container. One node is enough - it is still a replica set.

`docker-compose.yml` **does this already**: `mongo` runs
`mongod --replSet rs0 --bind_ip_all`, a one-shot `mongo-init` service initiates
the set (idempotent - an initiated set answers `AlreadyInitialized`), the
healthcheck reports healthy only once the node is a writable primary, and every
consumer addresses `mongodb://mongo:27017/rowboat?replicaSet=rs0`.

`infra/swarm/vibemind-stack.yml` (outer repo) **does not yet**: `rowboat-mongo`
is still a standalone holding live data, so every plugin write there fails.
Converting it is an operation on a running datastore, not a file edit: stop the
service, restart `mongod` with `--replSet rs0`, run
`rs.initiate({_id:"rs0",members:[{_id:0,host:"rowboat-mongo:27017"}]})` once,
then switch the connection strings. Existing data survives - the set adopts the
existing dbpath - but plan it as a maintenance step with a tested rollback.

A host client reaching a published single-node set needs
`?directConnection=true`, because replica-set discovery hands out the
in-network hostname the set was initiated with.

### The kernel is consumed as a build artifact

`apps/rowboat` resolves `@rowboat/openai-plugin-runtime` to the package's
compiled `dist/`. Any change to the kernel needs
`npm --prefix packages/openai-plugin-runtime run build` before the app sees it,
and a deployment image must run that build.

The image does. **Its build context is the rowboat repo root, not
`apps/rowboat`** - with the old context the kernel's `file:` path pointed
outside the context and `npm ci` failed outright, so no image could carry the
plugin runtime at all. `apps/rowboat/Dockerfile` therefore builds the kernel in
its own stage first and places it where the dependency resolves, and it needs
Node 20 (the app's `sharp`, via next, refuses to load on 18 and has no musl
prebuild, which also rules out Alpine).

## Deploying: the steps, in order

A deployment that skips these serves a plugin page that either errors or
refuses every install.

1. **Build the image** from the repo root:
   `docker build -f apps/rowboat/Dockerfile -t <tag> .`
   The default target is the app; `--target tools` builds the deploy-step image
   (same dependencies plus `tsx` and the catalog lock, which the standalone
   runner deliberately does not carry).
2. **Provision `PLUGIN_UI_PREVIEW_SECRET`** in the environment the app reads
   (`.env`, delivered via `env_file`). Without it the page lists the catalog and
   every install fails `preview_configuration_invalid`. See `.env.example` in
   the outer repo for all four `PLUGIN_*` variables.
3. **Ensure the indexes**, from the tools image against the target database:
   `npm run mongodb-ensure-indexes` - seconds, exits 0.
4. **Load the catalog**: `npm run plugins:catalog-load`, then the same command
   with `-- --verify-only`. Reports `entries: 180` at the pinned digest.
5. **After a catalog re-pin only:** delete the superseded rows first - see
   "Refreshing the catalog" above. Skipping this fails *every* installation,
   not only new ones.

Verified on 2026-09-08 against a single-node replica set: indexes exit 0 in 2s,
the catalog loads 180 entries in 7s, and the plugins page renders the pinned
catalog server-side. A page that answers `forbidden` is missing its
`project_members` row, not a broken deployment.

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
decides which resolver answers it fresh on every call, inside
`resolveProvider`'s own closure in `di/plugins-container.ts` - reading
`OPENFANG_URL` and `OPENFANG_API_KEY` from the current environment each time,
not once at startup, so a config change to either one takes effect on the
very next call without a process restart:

| Variable | Purpose |
| --- | --- |
| `OPENFANG_URL` | Base URL of the OpenFang daemon that can issue credential values. Must be `https:`, or `http:` to a loopback host (`127.0.0.1`, `localhost`, `::1`) - anything else is refused. |
| `OPENFANG_API_KEY` | Bearer token this composition presents to OpenFang's HTTP API |

**How a reference becomes the name OpenFang resolves.** OpenFang issues a
credential BY NAME (its vault → dotenv → env-var chain) and its endpoint
accepts only `^[A-Za-z_][A-Za-z0-9_]{0,127}$`. The resolver therefore derives
the wire name from the kernel's credential reference:

- a **bearer** reference (from `bearer_token_env_var`, e.g.
  `GITHUB_PAT_TOKEN`) passes through verbatim;
- an **oauth** reference (from `oauth_resource`, a URL) deterministically
  becomes `OAUTH_BEARER_` + host and path, uppercased, every non-alphanumeric
  run collapsed to one underscore;
- an HTTP MCP server that declares **neither** is normalized as an OAuth
  resource at its own URL - "declares nothing" never means "call
  unauthenticated"; without a provisioned reference the call fails closed as
  `credential_missing` before any network I/O.

For the pinned catalog's executable set, the names an operator provisions in
OpenFang's secrets and allowlists in `OPENFANG_ISSUABLE_CREDENTIALS` are:

| Component | Declared | OpenFang reference name |
| --- | --- | --- |
| github | `bearer_token_env_var: GITHUB_PAT_TOKEN` | `GITHUB_PAT_TOKEN` |
| linear | `oauth_resource: https://mcp.linear.app/mcp` | `OAUTH_BEARER_MCP_LINEAR_APP_MCP` |
| notion | `oauth_resource: https://mcp.notion.com` | `OAUTH_BEARER_MCP_NOTION_COM` |
| cloudflare | (nothing - fallback to its own URL) | `OAUTH_BEARER_MCP_CLOUDFLARE_COM_MCP` |

A reference that cannot be derived (an oauth reference that is not a URL, or
a derived name over the 128-character bound) is refused locally as
`credential_missing`, before any request to OpenFang.

**Provisioning the `OAUTH_BEARER_*` tokens.**
`scripts/provision-oauth-token.py` automates the whole MCP authorization
flow except the consent click (protected-resource discovery, dynamic client
registration, PKCE, localhost callback, token exchange) and appends
`<NAME>=<token>` to `--out` without ever printing the value. All three
providers' discovery and registration are verified live; `--dry-run` re-runs
that proof without opening a browser. The exact commands:

```sh
python scripts/provision-oauth-token.py https://mcp.cloudflare.com/mcp --out <secrets file>
python scripts/provision-oauth-token.py https://mcp.linear.app/mcp    --out <secrets file>
python scripts/provision-oauth-token.py https://mcp.notion.com/mcp    --out <secrets file>     --name OAUTH_BEARER_MCP_NOTION_COM
```

notion needs `--name`: its live metadata reports the resource as
`https://mcp.notion.com/mcp`, but the pinned catalog declares
`oauth_resource: https://mcp.notion.com`, and the resolver derives from the
catalog - the provisioned name must match the resolver's, not the live one.
Access tokens from these providers are typically short-lived; a refresh
token, when granted, is stored alongside as `<NAME>_REFRESH` for a future
refresh design in OpenFang. Note the tokens behind
the three `OAUTH_BEARER_*` names are the operator's to obtain; for linear and
notion the providers issue short-lived OAuth access tokens, and refreshing
them inside OpenFang is its own design, not covered here.

`OPENFANG_CREDENTIAL_TIMEOUT_MS` is different: unlike the two above, it is
parsed exactly once, at composition startup, into `openFangCredentialTimeoutMs`
(alongside `OPENFANG_APPROVAL_TIMEOUT_MS`, parsed the same way - see "The
OpenFang write-release approval window" below), and that parsed value is
reused for every call for the life of the process. Changing it needs a
restart to take effect, unlike `OPENFANG_URL` and `OPENFANG_API_KEY` above.

| Variable | Purpose |
| --- | --- |
| `OPENFANG_CREDENTIAL_TIMEOUT_MS` | Optional. Bounds one credential-issue round trip. Default 5000ms, floor 100ms, ceiling 30000ms; a value below the floor (or missing, fractional, negative, non-numeric, or beyond a safe integer) falls back to the default rather than clamping up to the floor - see `resolveOpenFangCredentialTimeoutMs` in `di/plugins-container.ts`. |

With `OPENFANG_URL` and `OPENFANG_API_KEY` both non-blank once trimmed, and
`OPENFANG_URL` passing the secure-or-loopback check above,
`resolveOpenFangCredentialSource` (`di/plugins-container.ts`) admits the pair
and `resolveProvider` wires an `OpenFangCredentialResolver`
(`src/infrastructure/plugins/openfang-credential-resolver.ts`), which calls
`POST <OPENFANG_URL>/api/credentials/issue` with `{"reference": "<NAME>"}` and
that bearer token, fresh, on every call, and returns the `value` OpenFang
answers with. With either variable absent or blank, an insecure
`OPENFANG_URL`, or on any failure - a non-200 of any kind, a malformed body, a
transport error, a timeout that covers the whole exchange (the fetch *and*
the body read, not just the connection), or a reference/project id the
kernel's own `assertCredentialRequest` rejects - the composition falls back
to (or the resolver itself throws) `UnreleasedCredentialResolver`'s
`credential_missing`. Absence or misconfiguration never means "resolve
anyway" - that stays the default that releases nothing, exactly as before
this resolver existed.

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
  into a thrown message, a `cause`, or a log line.
- **`OPENFANG_URL` must be `https:`, or `http:` to a host that cannot leave
  this machine.** Before this task that URL only carried approval requests;
  it now also carries the bearer token and, on success, a plaintext
  credential value. A URL that is neither is refused at composition time -
  falling back to `UnreleasedCredentialResolver`, never throwing - the same
  way `validateSecureUrl` already holds MCP server URLs to `https:` in the
  kernel (`mcp-http-provider.ts`), with one deliberate widening: a loopback
  exception, because the documented local setup runs OpenFang on
  `http://127.0.0.1:4200`.
- **The shared daemon on `127.0.0.1:4200` does not serve this endpoint
  today.** It still runs an older binary, and it runs with no `api_key`
  configured in its `config.toml`. `issue_credential`
  (`crates/openfang-api/src/routes.rs`) refuses outright whenever
  `api_key.trim().is_empty() && !auth.enabled` - a deliberate fail-open
  guard, the same one that also leaves that daemon's own `POST
  /api/approvals` unauthenticated. A daemon that can actually issue a
  credential needs a build with the issuance route and a non-blank
  `api_key`; the proof below used a separate, purpose-built daemon on its
  own port for exactly that reason, and never touched the shared one on
  `:4200`.

**Proven, once, against a real running daemon:** a released write now asks
OpenFang for a credential and gets one issued per call (`POST
/api/credentials/issue -> 200`), and the real `HttpMcpProvider` carries it
over HTTPS to a real third-party MCP endpoint
(`https://api.githubcopilot.com/mcp/`) - see `E2E-PROOF.md` at the
repository root for the full run: one call, the production composition
(`resolveOpenFangComposedProvider`, `resolveOpenFangReleaseWrite`, and the
container's other exported seams, called the way `createToolRuntime` calls
them), no hand-added header anywhere. **Since proven:** E2E-PROOF.md
Part III repeats this composition with a real `GITHUB_PAT_TOKEN` and the
provider ACCEPTS the released call (`status=success`, authenticated
identity returned). In the Part I run described here the token was
deliberately fake, so
GitHub's rejection is the intended outcome - the call ends in
`provider_failed`, a code distinct from `credential_missing` and reachable
only after the credential itself resolved to a value, so the proof also
confirms the two stay observably distinct even under a real network
exchange, not only in the unit tests that exercise the mapping directly.
Also still unproven: the Next.js container path itself (the proof composed
the runtime's dependency object directly from the container's exported
composition seams rather than by calling `createPluginControllers()`, which
needs Auth0/session machinery it has no business standing up), and anything
about revocation, rotation, or concurrent calls - the proof is one call,
once.

### The connector bridge

An `app`-kind component whose `.app.json` declares a `connector_...` id
executes through a second provider, `ConnectorBridgeProvider`
(`packages/openai-plugin-runtime/src/providers/connector-bridge-provider.ts`),
instead of `HttpMcpProvider`. It makes exactly one `POST
{OPENAI_BASE_URL}/v1/responses`, with `tools:[{type:"mcp",
server_label:<app.name>, connector_id:<the pinned connector_... id>,
authorization:<the resolved connector credential>, require_approval:"never",
allowed_tools:[<operationName>]}]`, and reads the `mcp_call` output item back
from the response. Everything else about the call - the admission gate
(`mcp_http` + `write`, identical to `HttpMcpProvider`), the OpenFang release
gate, and the receipt shape - is the same mechanism the rest of this document
describes; only the provider and its credentials differ.

**Argument fidelity and single-execution are advisory here, unlike the MCP
path.** The provider's `input` string asks the model, in prose, to call
`operationName` "exactly once with exactly these arguments" and
`allowed_tools` pins which single tool name the model may invoke - but
nothing on the OpenAI side enforces either instruction. Across the model
boundary, the OpenFang approval's arguments digest binds the arguments this
call *requested* of the model, not the arguments the model actually executes
with; `allowed_tools` bounds the tool name only, not call count.
`HttpMcpProvider`'s MCP path has no such gap: it calls the named tool
directly, once, with the exact arguments it was given, with nothing composed
in between for a model to reinterpret.

**Two credentials per call, both through the same `CredentialResolver`, both
required before any network I/O.** Where an MCP server resolves one
reference, a connector call resolves two, in this order:

| Reference | Purpose |
| --- | --- |
| `OPENAI_API_KEY` | Authenticates the `POST /v1/responses` call itself |
| `CONNECTOR_<APP_NAME>` | The app's own OAuth token, passed as the `mcp` tool's `authorization` |

`CONNECTOR_<APP_NAME>` is derived the same deterministic way the
`OAUTH_BEARER_*` names above are: the app's catalog `name`, uppercased, every
non-alphanumeric run collapsed to one underscore (`deriveConnectorReference`
in `connector-bridge-provider.ts`; e.g. `canva` -> `CONNECTOR_CANVA`,
`monday-com` -> `CONNECTOR_MONDAY_COM`). For the pinned catalog's 15 admitted
`connector_...` app components, the names an operator provisions in
OpenFang's secrets and allowlists in `OPENFANG_ISSUABLE_CREDENTIALS`
(alongside `OPENAI_API_KEY`) are:

| App | `CONNECTOR_*` reference |
| --- | --- |
| alpaca | `CONNECTOR_ALPACA` |
| amplitude | `CONNECTOR_AMPLITUDE` |
| biorender | `CONNECTOR_BIORENDER` |
| canva | `CONNECTOR_CANVA` |
| daloopa | `CONNECTOR_DALOOPA` |
| egnyte | `CONNECTOR_EGNYTE` |
| fireflies | `CONNECTOR_FIREFLIES` |
| help-scout | `CONNECTOR_HELP_SCOUT` |
| intercom | `CONNECTOR_INTERCOM` |
| jam | `CONNECTOR_JAM` |
| monday-com | `CONNECTOR_MONDAY_COM` |
| pipedrive | `CONNECTOR_PIPEDRIVE` |
| semrush | `CONNECTOR_SEMRUSH` |
| sendgrid | `CONNECTOR_SENDGRID` |
| teamwork-com | `CONNECTOR_TEAMWORK_COM` |

A further 14 `connector_...` apps exist in the pinned catalog (gmail,
google-drive, google-calendar, several outlook/teams/sharepoint surfaces,
github, vercel and figma among them) but are `review_required`, not
`admitted`, so none of them is installable yet - this table lists only the
15 that are.

Neither the 15 above nor the 14 `review_required` ones are the whole story
on admitted-but-uncallable apps: of the pinned catalog's **117** admitted
`app`-kind components, **102** (101 `asdk_app_...` + 1 `templated_apps_...`)
are genuinely policy-admitted - not merely `review_required` - yet still
have no public invocation path outside ChatGPT's own Apps SDK surface (spec
D4; see "asdk_app_... and templated_apps_... apps have no public invocation
path here" below for the mechanism). Admission and invocability are
independent questions for this id shape: the 15 `connector_...` apps above
are the only admitted `app` components this runtime can actually call.

**Configuration**, read fresh from the environment on every call (same
convention as `OPENFANG_URL`/`OPENFANG_API_KEY` above - see
`resolveOpenFangProvider` in `di/plugins-container.ts`):

| Variable | Purpose |
| --- | --- |
| `OPENAI_RESPONSES_MODEL` | Optional. Model the connector bridge asks the Responses API for. Default `gpt-5.6`. |
| `OPENAI_BASE_URL` | Optional. Base URL for the `/v1/responses` call. Default `https://api.openai.com`. Must be `https:`, or `http:` to a loopback host - the same rule the kernel provider's own `validateBaseUrl` enforces. |

Both collapse a blank (unset or whitespace-only) value to the kernel's own
default rather than an empty string.

**`asdk_app_...` and `templated_apps_...` apps have no public invocation path
here, by design (spec D4).** These ids only run inside ChatGPT's own Apps SDK
surface; there is no OpenAI HTTP endpoint this runtime, or any Rowboat
deployment, could call on their behalf. `resolvePluginProvider`'s app branch
(`apps/rowboat/src/infrastructure/plugins/provider-resolution.ts`) refuses
any id that does not start with `connector_` before ever constructing a
provider, and `ConnectorBridgeProvider`'s own constructor refuses the same
thing again if somehow reached (`provider_unavailable:not_a_connector`) -
belt and suspenders, never approximated as "probably fine". `AddPluginToolUseCase`
has no kind-based gate, so an `asdk_app_...` component still installs and
binds as a tool exactly like a `connector_...` one; it is the runtime's
resolution step, not install or add-tool, that draws this line. Because
`PluginToolRuntime` releases a write *before* it resolves the provider
(`invoke()`'s release gate runs first, resolution afterward - see
`plugin-tool-runtime.ts`), invoking such a tool still raises, and if a human
approves it still consumes, a real OpenFang approval before resolution
refuses it; no network I/O ever leaves this process either way, approved or
not. `E2E-PROOF.md` Part IV observed exactly this live, once, and pins it
rather than assuming it: one approval raised and approved, zero bytes sent to
any provider, and the call still ending in `provider_unavailable`.

**Process MCP servers are cut from W2 entirely (spec D5), not merely
unimplemented.** All 3 process-transport MCP components in the pinned
catalog are `rejected` at admission - policy already refuses to run them, so
"make process MCP executable" would mean overturning an admission decision
this runtime never overturns anywhere else, not adding a resolution branch.
A verified execution root from the content store, mounted into the web
runtime, remains a prerequisite for process MCP and belongs to deployment
(W5) whenever an admitted process MCP component first exists.

**Live-proven, once, against OpenAI's own API, with the production
composition:** a released canva call resolves both `OPENAI_API_KEY` and
`CONNECTOR_CANVA` per call from OpenFang, then reaches a real `POST
https://api.openai.com/v1/responses` and draws OpenAI's own 401 on a
deliberately fake key, ending in `provider_failed` - the receipt lands with
`componentKind:"app"` and the approval id, same shape as the MCP proofs
above. (The spec's acceptance criterion for this proof names the stored
receipt's `reason` as `provider_failed`; what actually lands there is
`provider_unavailable` - `provider_failed` is the runtime's own outcome, and
the receipt-reason vocabulary predates this bridge and, per "Receipts"
below, collapses every non-`credential_missing` provider failure to that one
constant.) See `E2E-PROOF.md` Part IV. Still unproven, and not claimed: that
OpenAI accepts anything, including whether the pinned `connector_...` hex ids
are valid entries in OpenAI's own connector directory at all (D6) - only a
real accepted call could prove that, and none has been made.

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

The plugins page lists the pinned catalog. Installation is **per component**:
the install dialog shows one checkbox per component, pre-checked for every
component the catalog reports as `available`, and writes an admission row and a
provider binding for the selected components only. Each executable component of
an installed plugin then offers **Add to workflow**, which appends a tool to the
project draft workflow bound to that component.

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

### Installing part of a plugin

A plugin is offered from the UI while its own admission is decided and at least
one of its components is `available`. A plugin the catalog reports as
`partially_available` (one admitted component beside `review_required`
siblings) is installable; it is only not installable *as a whole*.

The selection travels as `componentDigests`, sorted, unique and lowercase
64-hex, and is optional at every boundary:

- `POST /api/v1/projects/{projectId}/plugins` accepts it beside `pluginName`,
  `catalogDigest` and `expectedRevision`. **Absent means every component**,
  which is what every client written before this existed already sends.
- The preview server action accepts it; the install server action takes
  `{previewToken, componentDigests}`.
- The signed preview envelope carries a `componentSelectionDigest`, so a token
  issued for one selection cannot install another. The idempotency fingerprint
  binds the same digest, so one idempotency key cannot be replayed with a
  different selection.
- Admission rows and provider bindings are written for the selected components
  only. There is no path that adds a further component afterwards: the
  selection is made once, at install.
- REST bodies cap every JSON array at 64 elements, so a REST caller can name at
  most 64 components explicitly. An absent selection still covers all 88
  components of `zoom`.

Rejections: an empty, duplicated, malformed or foreign digest is
`request_invalid`; a selected component the catalog does not admit is
`component_not_admitted`; a selected component that cannot run reports its own
availability reason; installing a plugin that is already installed is
`installation_conflict`.

### Catalog facts at digest `5c9ea069...`

Re-derived from the pinned lock (`config/openai-plugin-catalog.lock.json`).
Several of the rulings above rest on these:

- 180 entries and **1093** components, **all 1093 `available`** - what varies
  across the catalog is admission, not availability.
- **0** duplicate binding digests: a digest identifies exactly one component.
- **0** provider bindings declare `pairedComponentDigests`.
- Installable as a whole - own admission `admitted` *and* every component
  admitted and `available` or `installed`, which is `serializedPlugin`'s own
  criterion in `app/api/v1/projects/[projectId]/plugins/_responses.ts` -
  **118** of 180. Installable per component: **121**. The three this unlocks
  are **cloudflare**, **github** and **notion**, each of which has exactly one
  admitted `mcp` component beside non-admitted siblings.
- Only **4** admitted components are `mcp`: github, cloudflare, linear and
  notion. All four are OAuth-gated at the provider; `credentialSlots: []`
  means "no env-var-style slot declared", not "no credential needed". See
  `E2E-PROOF.md` Part II.
- **15** admitted `app` components declare a `connector_...` id and execute
  through the connector bridge (see "The connector bridge" above) - alpaca,
  amplitude, biorender, canva, daloopa, egnyte, fireflies, help-scout,
  intercom, jam, monday-com, pipedrive, semrush, sendgrid, teamwork-com.
  Together with the 4 `mcp` components above, these 19 are the catalog's
  entire executable set; the remaining 1074 admitted-or-not components (every
  `skill`, `asset`, the 3 rejected process `mcp` servers, and every
  `asdk_app_...`/`templated_apps_...` app) have no invocation path in this
  runtime at all.

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
  OpenFang" above for the transport and its security shape. **The
  end-to-end call against a real third-party credential HAS now been made
  and accepted**: E2E-PROOF.md Part III carries a released `get_me` through
  real issuance of `GITHUB_PAT_TOKEN` to `api.githubcopilot.com`, which
  answered with the authenticated identity; the receipt landed as
  `status=success` with the approval id. What remains open is acceptance for
  the three `OAUTH_BEARER_*` references (see "Resolving a credential value
  from OpenFang" above for the exact names): the derivation is implemented
  and unit-proven, the tokens behind the names are the operator's to
  provision.

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

Two kinds of catalog record still cannot execute here at all, and both are
reported unavailable rather than approximated - see "The connector bridge"
above for the third case (`app` components with a `connector_...` id) that
now *can*:

- **A process-transport MCP server** needs a verified execution root from the
  content store, which the web runtime does not mount. Moot for the pinned
  catalog today regardless: all 3 process MCP components in it are `rejected`
  at admission (spec D5) - this is a policy decision this runtime never
  overturns, not a missing resolution branch, and stays out of scope until an
  admitted process MCP component first exists.
- **An `app` component whose declared id is `asdk_app_...` or
  `templated_apps_...`** (spec D4) has no public invocation path outside
  ChatGPT's own Apps SDK surface. `resolvePluginProvider`'s app branch and
  `ConnectorBridgeProvider`'s own constructor both refuse it, independently,
  before any provider is ever built.

Of the 180 pinned plugins the executable set is the 4 admitted `mcp`
components plus the 15 admitted `connector_...` `app` components - see
"Catalog facts" above for the exact count and names.

## Receipts

All receipts live in the `plugin_receipts` collection, keyed by `receiptId`:

| Prefix / type | Meaning |
| --- | --- |
| `type: "import"` / `"install"` | Catalog import and installation provenance |
| `type: "execution"`, `parity:<digest>` | Shadow parity comparison evidence |
| `type: "execution"`, `receiptId: <requestId>` | One `PluginToolRuntime.invoke()` call - success, failure, or a refusal at the release gate. `output.approvalId` is present whenever OpenFang produced an approval id for this call, on any of those three outcomes, not only success - a release is attempted for an `unavailable`, timed-out, or aborted refusal too, and none of those ever produces an id to carry (see below for what the refusal receipt can and cannot distinguish). |
| `type: "migration"`, `runtime-mode:<digest>` | A runtime mode transition, including rollback |

Receipts are immutable and redacted: arguments and results are declared under
`redactions` and stored only as digests.

`write_review_required` names two different things, not one: it is also a
license/admission decision the pinned catalog itself declares for 634
components (upstream's own string, unrelated to release), as well as the
runtime's own refusal at the release gate documented above. A receipt for the
latter (`type: "execution"`, `status: "denied"` or `"timed_out"`, `reason:
"write_review_required"`) is what lets an operator tell the two apart - the
catalog-level meaning never produces one.

The refusal receipt's `reason` is always the single constant
`"write_review_required"` - the kernel's reason vocabulary has no room for a
finer one, the same fixed-allowlist constraint that also collapses
provider failures elsewhere in this section (below) - so `status` and the presence of `output.approvalId`
together are what actually separate the five outcomes a release attempt can
produce, into three buckets an operator can tell apart on the receipt itself:

| Receipt shows | Actual outcome | What an operator can conclude |
| --- | --- | --- |
| `status: "denied"`, `approvalId` present | denied or expired | A human (or OpenFang's own expiry) decided against this exact call; the id names which OpenFang record to check. |
| `status: "denied"`, no `approvalId` | unavailable, or approved with an `approvalId` that failed the UUID guard | OpenFang never produced a trustworthy decision for this call - unreachable, no record created, or a malformed response. There is no id to check because none was ever validated. |
| `status: "timed_out"`, no `approvalId` | the release call itself hit this runtime's own deadline, or the caller aborted while it was pending | No decision came back in time either way; same as the provider-failure collapse documented below, a receipt alone cannot tell a deadline from a caller cancellation apart. |

That collapse is deliberate, the same call made for provider failures below:
recording is bounded by what the kernel's fixed `PluginReasonCode` allowlist
accepts, not by widening it per call site.

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
(`packages/openai-plugin-runtime/src/providers/mcp-http-provider.ts:326-344`),
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
else — unrecognised, empty, or `"__proto__"`-shaped — still normalises to
`"provider_failed"`, exactly as before. An over-long reason never reaches
this allowlist at all: `captureProviderResult`'s own byte-length guard on
`reason` (4096 bytes) runs first and raises `"provider_result_invalid"`
instead, pinned by the test at the 4097-byte boundary. The allowlist is a
constant in the app's own file, never derived from what the provider sent, so
the security property the old blanket redaction protected (the app never
propagates an arbitrary provider string to the caller or the receipt) is
unchanged. The failure receipt distinguishes some reasons now, not all of
them: `credential_missing` and `execution_state_changed` each keep their own
name on the receipt; `provider_failed`, `provider_result_invalid`,
`provider_unavailable`, and `credential_invalid` - four distinct app/kernel
failure codes - still all collapse to the single receipt reason
`"provider_unavailable"`, exactly as every failure did before this task. The
receipt's `status` field collapses further still, independent of `reason`:
`provider_timed_out` and `request_aborted` both write `status: "timed_out"` -
a receipt alone cannot tell a runtime deadline from a caller's own
cancellation apart, only that one of the two happened before the provider
settled.

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

**The end-to-end call against a real GitHub credential has now been made
once, and here is exactly what it proved and did not.** Task 5b implemented
the credential-transport decision this section used to say was still open:
with `OPENFANG_URL` and `OPENFANG_API_KEY` both configured,
`OpenFangCredentialResolver` asks a real OpenFang daemon's `POST
/api/credentials/issue` for a value per call, instead of
`UnreleasedCredentialResolver` releasing nothing. `E2E-PROOF.md` (repository
root) then ran that path end to end, against a purpose-built OpenFang
daemon, with the production composition and no hand-added header anywhere:
one write raised a real approval, a human approved it, the release reached
the provider, `POST /api/credentials/issue` returned 200 and the daemon
logged `Credential issued reference=GITHUB_PAT_TOKEN`, and the real
`HttpMcpProvider` carried that value over HTTPS to
`https://api.githubcopilot.com/mcp/`. The call still ended in
`provider_failed`, not success: the credential the daemon issued was a
deliberately fake test value generated only for that run, so GitHub's
rejection is the intended outcome - and it is GitHub's rejection
specifically, not a network fault: two different 401 bodies for "no token"
versus "this token," confirmed a second time against the same MCP SDK
transport the provider uses. What that proves: the chain from approval
through issuance to a real third-party HTTP call now works end to end, and
`provider_failed` (credential resolved, the remote call itself failed)
stays observably distinct from `credential_missing` (credential never
resolved) even under a real network exchange, not only in the unit tests
that exercise the mapping directly. What it does not prove: that a *real*,
valid third-party credential succeeds - the token was fake by design, so
only the rejection path is exercised there (Part III has since proven the
acceptance path with a real credential) - nor does it exercise the Next.js
container path itself (the proof assembled `PluginToolRuntimeDependencies`
by hand from the container's own exported composition seams rather than by
calling `createPluginControllers()`, which needs Auth0/session
infrastructure the proof has no business standing up), or anything about
revocation latency, rotation, or concurrent calls - one call, once. No
approval id, receipt id, or result exists for a *successful* real credential
call anywhere in this repository. Recording otherwise would misstate what
was observed.

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
  (`packages/openai-plugin-runtime/src/providers/mcp-request.ts:19-21`) also
  hard-requires `request.capability === "write"` - but neither kernel check is
  what a read-classified call actually meets first. It fails earlier still, in
  the *app*, with `capability_mismatch`: every binding this codebase ever
  writes is `capability: "write"` unconditionally
  (`application/services/plugin-binding-materialization.ts:108` and
  `application/use-cases/plugins/add-plugin-tool.use-case.ts:113`, both citing
  "a provider binding carries no read/write classification"), while
  `PluginToolRuntime.invoke()` requires the binding's own declared capability
  to agree with what the classifier says
  (`if (binding.capability !== trustedCapability) throw ...
  "capability_mismatch"`, in `plugin-tool-runtime.ts`). A read-classified
  operation is refused there, in the app, before the call ever reaches
  `resolveProvider` or the kernel at all - and `capability_mismatch` is not a
  write refusal, so it is never eligible for an OpenFang release in the first
  place, not "needing" one.
  No catalog entry declares a read-only operation today
  (`classifyPluginOperation` defaults every operation to `write` unless a
  component's metadata explicitly lists it under `readOnlyOperations`), so
  nothing exercises this path yet. Lifting it needs three changes, not two:
  the two kernel call sites named above keyed off `request.capability` instead
  of the literal `"write"`, *and* something in the app that actually
  constructs a binding with `capability: "read"` for a component the
  classifier would agree is read-only - today both binding-construction sites
  hard-code `"write"` regardless, so a fixed kernel alone would still never
  see a read reach it. None of this is modified here.
- **The Next.js container path itself was not exercised.** (A real, valid
  third-party credential HAS succeeded since: E2E-PROOF.md Part III, a
  released `get_me` accepted by `api.githubcopilot.com` after real issuance
  of `GITHUB_PAT_TOKEN`. The remaining acceptance gap is the three
  `OAUTH_BEARER_*` references, whose tokens are the operator's to
  provision.) `E2E-PROOF.md`
  (repository root) ran the full chain once, end to end, against a
  purpose-built OpenFang daemon rather than an injected `fetch`: an
  OpenFang-approved write reached the real provider (the Task 4-6
  policy-threading gap), asked OpenFang for a credential and got one issued
  per call (Task 5b, `POST /api/credentials/issue -> 200`), and the real
  `HttpMcpProvider` carried it over HTTPS to
  `https://api.githubcopilot.com/mcp/`. Because the daemon's allowlisted
  credential was a deliberately fake test value, GitHub refused it and the
  call ended in `provider_failed`, distinct from `credential_missing` -
  see "Released-write proof, and the credential boundary" above for both
  codes' mechanism, and `E2E-PROOF.md` for the run itself (Part III has since
  proven the acceptance path with a genuine credential). Still not proven:
  that the Next.js
  container path behaves this way (the proof assembled
  `PluginToolRuntimeDependencies` by hand from the container's exported
  composition seams rather than by calling `createPluginControllers()`,
  which needs Auth0/session infrastructure the proof has no business
  standing up); and anything about revocation latency, rotation, or
  concurrent calls - one call, once. The shared OpenFang daemon on `:4200`
  was not exercised either; it still runs the old binary without an
  `api_key` configured, a mode in which `issue_credential` refuses by
  design (see "The security shape, stated plainly" above).
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
