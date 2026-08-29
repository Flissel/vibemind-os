# OpenAI plugin runtime operations

How to import, admit, migrate, cut over, and roll back the OpenAI-compatible
plugin runtime. Every command below is safe to read first: the dry-run and
report paths never mutate a project.

## What is pinned

| Item | Value |
| --- | --- |
| Plugin source | `openai/plugins@11c74d6ba24d3a6d48f54a194cd00ef3beea18f9` |
| Plugin count | 180 |
| Catalog digest | `209d785ef95cc3b785fe973da869bd780a09917fecb06dbabb36a1a156f35fe8` |
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

Rollback restores authority to the legacy workflow fields, which the migration
never rewrote, so no workflow document is touched. It records its own receipt
and stamps `rolledBackAt`.

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

- **The app-wide TypeScript build is red before this feature** — tracked source
  references `logo.png`, `logo-only.png`, and `mascot.png`, which are not in the
  repository. The focused gates above are green; that is not a claim that
  `next build` succeeds.
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
- **`shadow -> legacy` is not an admitted transition.** The plan's table admits
  only the three transitions listed above, so a project in shadow returns to
  legacy by going through a cutover and rollback. Widening the table is a
  separate decision.
