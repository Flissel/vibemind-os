/**
 * END-TO-END PROOF, PART V: the plugin-setup-agent chain (Aufgabe 8).
 *
 * Parts I-IV proved the OpenFang release/approval/provider chain from
 * Rowboat's side. This part proves the plugin-setup-agent's OWN job: taking
 * custody of a credential (spaces/plugin-setup/werkzeuge.py::
 * schluessel_entgegennehmen -> ablage.py -> pruefung.py -> OpenFang
 * /api/credentials/store) and the daemon issuing it back out again, on top
 * of the same release/approval/provider chain Parts I-IV already proved.
 *
 * THE CONTRADICTION THE BRIEF NAMES, AND HOW THIS FILE RESOLVES IT:
 * `schluessel_entgegennehmen` only hands a value to OpenFang after
 * `pruefung.pruefe()` gets a 200 from the REAL provider. No real provider
 * credential is in scope for this proof (every value here is obviously
 * invented), so "verification passes and OpenFang then takes custody" is
 * NOT something this file can run as one continuous call. Joining the two
 * halves by faking a passing verification is explicitly forbidden -- see
 * E2E-PROOF.md Part V for the full accounting. Instead this file proves
 * three adjacent, independently-live pieces:
 *
 *   A. the fail-closed half:      Supabase intake -> real provider 401 ->
 *                                  fehlschlagen() -> nothing reaches OpenFang
 *   B. the custody half, direct:  bypass verification, call OpenFang's own
 *                                  /store and /issue directly (the
 *                                  production credential-custody mechanism
 *                                  Aufgabe 1 built, exercised for its own
 *                                  sake, not through the agent's gate)
 *   C. the Rowboat half:          component-scoped install -> tool binding
 *                                  -> invocation -> release gate raises an
 *                                  approval -> decision -> real provider
 *                                  call -> receipt carrying the approval id
 *                                  (same production seams as Parts I-IV,
 *                                  same obviously-fake-credential shape as
 *                                  Part I/II: the provider's own 401, not a
 *                                  mock)
 *   D. hygiene, as an assertion, not a claim: every invented value from A-C
 *      is searched for -- in the Supabase state row, in `vault.secrets`
 *      (with a Postgres-log zero-control window, because a prior finding on
 *      this task showed a value can leak into the server log via a FAILING
 *      statement), in the OpenFang daemon's own log file (if its path is
 *      given), in the Mongo receipt, and in this test's own evidence log --
 *      and asserted absent, not eyeballed.
 *
 * Opt-in (own variable, like Parts I-IV):
 *   ROWBOAT_LIVE_MONGO_URL=mongodb://127.0.0.1:27017/rowboat
 *   ROWBOAT_LIVE_OPENFANG_URL=http://127.0.0.1:4273
 *   OPENFANG_API_KEY=<the isolated daemon's api_key>
 *   ROWBOAT_LIVE_SETUP_AGENT=1
 * Optional:
 *   ROWBOAT_E2E_EVIDENCE=<path>                 -- step log, mirrored to stdout
 *   PLUGIN_SETUP_PYTHON=<python executable>      -- default "python"
 *   PLUGIN_SETUP_OPENFANG_LOG_FILE=<path>        -- daemon's own log file;
 *                                                    D's daemon-log leak
 *                                                    check is skipped
 *                                                    (logged, not silently)
 *                                                    without it
 *   PLUGIN_SETUP_OPENFANG_HOME=<path>            -- the daemon's OPENFANG_HOME;
 *                                                    B additionally reads
 *                                                    issuable_credentials.list
 *                                                    directly (skipped,
 *                                                    logged, without it)
 *
 * This test never restarts the daemon, never approves on the agent's
 * behalf outside its own proof loop (the SAME role every prior part's
 * `approveWhenRaised` plays -- a human at a dashboard approves too), and
 * never touches the shared :4200 daemon or `~/.openfang/`.
 */
import { randomUUID } from "node:crypto";
import { spawnSync } from "node:child_process";
import { appendFileSync, existsSync, readFileSync, statSync } from "node:fs";
import { resolve } from "node:path";
import { MongoClient } from "mongodb";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { PINNED_PLUGIN_CATALOG_DIGEST } from "@rowboat/openai-plugin-runtime";
import type { PluginToolRuntimeDependencies } from "@/src/application/services/plugin-tool-runtime";
import { classifyPluginOperation } from "@/src/application/services/plugin-operation-classifier";
import {
  deriveRuntimeDeadlineMs,
  resolveOpenFangApprovalWindowMs,
  resolveOpenFangComposedProvider,
  resolveOpenFangCredentialTimeoutMs,
  resolveOpenFangReleaseWrite,
} from "@/di/plugins-container";

const LIVE_MONGO = (process.env.ROWBOAT_LIVE_MONGO_URL ?? "").trim();
const OPENFANG_URL = (process.env.ROWBOAT_LIVE_OPENFANG_URL ?? "").trim();
const OPENFANG_KEY = (process.env.OPENFANG_API_KEY ?? "").trim();
const SETUP_AGENT = (process.env.ROWBOAT_LIVE_SETUP_AGENT ?? "").trim();
if (LIVE_MONGO !== "") process.env.MONGODB_CONNECTION_STRING = LIVE_MONGO;
if (OPENFANG_URL !== "") process.env.OPENFANG_URL = OPENFANG_URL;

const SKIP = LIVE_MONGO === "" || OPENFANG_URL === "" || OPENFANG_KEY === "" || SETUP_AGENT !== "1";
const EVIDENCE = (process.env.ROWBOAT_E2E_EVIDENCE ?? "").trim();
const PYTHON = (process.env.PLUGIN_SETUP_PYTHON ?? "python").trim();
const OPENFANG_LOG_FILE = (process.env.PLUGIN_SETUP_OPENFANG_LOG_FILE ?? "").trim();
const OPENFANG_HOME_DIR = (process.env.PLUGIN_SETUP_OPENFANG_HOME ?? "").trim();

// worktree_root/spaces/rowboat/rowboat/apps/rowboat -> worktree_root, then
// down into spaces/plugin-setup. Verified to exist in beforeAll rather than
// trusted blindly.
const ROWBOAT_REPO_ROOT = resolve(process.cwd(), "..", "..");
const REPO_ROOT = resolve(ROWBOAT_REPO_ROOT, "..", "..", "..");
const PLUGIN_SETUP_DIR = resolve(REPO_ROOT, "spaces", "plugin-setup");

let step = 0;
const log = (message: string): void => {
  const line = `  ${String(++step).padStart(2, "0")}  ${message}`;
  // eslint-disable-next-line no-console
  console.log(line);
  if (EVIDENCE !== "") appendFileSync(EVIDENCE, `${line}\n`, "utf8");
};

// Every invented value used anywhere in this file. D scans for each of
// these, everywhere the hygiene contract forbids them -- populated as A-C
// run, consumed by D.
const SECRET_VALUES: string[] = [];
function invented(label: string): string {
  const value = `obviously-fake-task8-${label}-${randomUUID()}`;
  SECRET_VALUES.push(value);
  return value;
}

function sqlLiteral(value: string): string {
  return `'${value.replace(/'/g, "''")}'`;
}

function findSupabaseContainer(): string {
  const result = spawnSync("docker", ["ps", "--format", "{{.Names}}"], { encoding: "utf8" });
  const names = (result.stdout ?? "").split(/\r?\n/).map(line => line.trim()).filter(Boolean);
  const match = names.find(name => name.includes("supabase-db"));
  if (match === undefined) throw new Error("no running supabase-db container found (docker ps)");
  return match;
}

function psql(container: string, sql: string): { readonly code: number; readonly output: string } {
  const result = spawnSync(
    "docker",
    ["exec", "-i", container, "psql", "-U", "postgres", "-h", "127.0.0.1", "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-tA"],
    { input: sql, encoding: "utf8" },
  );
  return { code: result.status ?? -1, output: `${result.stdout ?? ""}${result.stderr ?? ""}` };
}

function dockerLogsSince(container: string, sinceIso: string): string {
  const result = spawnSync("docker", ["logs", "--since", sinceIso, container], {
    encoding: "utf8",
    maxBuffer: 32 * 1024 * 1024,
  });
  return `${result.stdout ?? ""}${result.stderr ?? ""}`;
}

interface SchluesselErgebnis {
  readonly ok: boolean;
  readonly referenz?: string;
  readonly status?: number;
  readonly fehler?: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function parseSchluesselErgebnis(raw: string): SchluesselErgebnis {
  const parsed: unknown = JSON.parse(raw);
  if (!isRecord(parsed) || typeof parsed.ok !== "boolean") {
    throw new Error(`unexpected shape from schluessel_entgegennehmen: ${raw.slice(0, 200)}`);
  }
  return {
    ok: parsed.ok,
    referenz: typeof parsed.referenz === "string" ? parsed.referenz : undefined,
    status: typeof parsed.status === "number" ? parsed.status : undefined,
    fehler: typeof parsed.fehler === "string" ? parsed.fehler : undefined,
  };
}

// The only real network + DB call this file makes into the Python side --
// the production tool the agent actually exposes over MCP, not a
// reimplementation of its logic in TypeScript.
function runSchluesselEntgegennehmen(referenz: string, wert: string): SchluesselErgebnis {
  const script = [
    "import json, sys",
    "import werkzeuge",
    "ergebnis = werkzeuge.schluessel_entgegennehmen(",
    "    'task8-live-proof', 'demo-plugin', sys.argv[1], 'bearer', sys.argv[2])",
    "print(json.dumps(ergebnis))",
  ].join("\n");
  const result = spawnSync(PYTHON, ["-c", script, referenz, wert], {
    cwd: PLUGIN_SETUP_DIR,
    env: {
      ...process.env,
      PLUGIN_SETUP_OPENFANG_URL: OPENFANG_URL,
      PLUGIN_SETUP_OPENFANG_API_KEY: OPENFANG_KEY,
    },
    encoding: "utf8",
    timeout: 30_000,
  });
  if (result.status !== 0) {
    // The stderr of a fail-soft tool should never carry `wert`, but this is
    // the harness's own diagnostic path, not the tool's contract -- assert
    // that separately in D rather than trusting it here.
    throw new Error(`python schluessel_entgegennehmen exited ${String(result.status)}: ${(result.stderr ?? "").slice(0, 500)}`);
  }
  return parseSchluesselErgebnis(result.stdout);
}

interface OpenFangApproval {
  readonly id: string;
  readonly status: string;
  readonly tool_name: string;
  readonly action_summary: string;
  readonly decided_at?: string;
}

async function listApprovals(): Promise<readonly OpenFangApproval[]> {
  const response = await fetch(`${OPENFANG_URL}/api/approvals`);
  const body = (await response.json()) as { approvals?: OpenFangApproval[] };
  return body.approvals ?? [];
}

async function approve(id: string): Promise<{ readonly status: number; readonly body: unknown }> {
  const response = await fetch(`${OPENFANG_URL}/api/approvals/${id}/approve`, {
    method: "POST",
    headers: { Authorization: `Bearer ${OPENFANG_KEY}` },
  });
  return { status: response.status, body: await response.json() };
}

async function approveWhenRaised(deadlineMs: number): Promise<OpenFangApproval | undefined> {
  const cutoff = Date.now() + deadlineMs;
  while (Date.now() < cutoff) {
    const pending = (await listApprovals()).find(candidate => candidate.status === "pending");
    if (pending !== undefined) {
      log(`OpenFang approval raised   id=${pending.id} tool_name=${pending.tool_name}`);
      log(`  action_summary  = ${pending.action_summary}`);
      const decision = await approve(pending.id);
      log(`  approve() -> ${JSON.stringify(decision)}`);
      return pending;
    }
    await new Promise(done => setTimeout(done, 250));
  }
  return undefined;
}

async function storeCredential(reference: string, value: string, overwrite: boolean): Promise<{ readonly status: number; readonly body: unknown }> {
  const response = await fetch(`${OPENFANG_URL}/api/credentials/store`, {
    method: "POST",
    headers: { Authorization: `Bearer ${OPENFANG_KEY}`, "Content-Type": "application/json" },
    body: JSON.stringify({ reference, value, overwrite }),
  });
  return { status: response.status, body: await response.json() };
}

async function issueCredential(reference: string): Promise<{ readonly status: number; readonly body: unknown }> {
  const response = await fetch(`${OPENFANG_URL}/api/credentials/issue`, {
    method: "POST",
    headers: { Authorization: `Bearer ${OPENFANG_KEY}`, "Content-Type": "application/json" },
    body: JSON.stringify({ reference }),
  });
  return { status: response.status, body: await response.json() };
}

// MongodbProjectsRepository binds to a module-level singleton `db` that is
// hardcoded to `mongoClient.db("rowboat")` (app/lib/mongodb.ts) -- it does
// not take a database handle as a constructor argument the way
// MongodbPluginsRepository does. So this file cannot isolate itself in a
// dedicated database the way its Mongo cleanup would prefer; it uses the
// same shared `rowboat` db every prior Part used, and cleans up by deleting
// only the rows this run's own `projectId` (a fresh random UUID) touches,
// leaving the shared, reused plugin catalog snapshot/entries alone.
const DB_NAME = "rowboat";
const OPERATION = "get_me";
const ARGUMENTS = Object.freeze({});

let client: MongoClient;
const projectId = randomUUID();
const identity = Object.freeze({ kind: "user" as const, userId: "guest_user" });

// A's referenz/wert -- module scope so D can search for it after A runs.
const failClosedReferenz = `TASK8_FAILCLOSED_${randomUUID().replace(/-/g, "").toUpperCase()}`;
const failClosedWert = invented("failclosed-bearer");

// B's own uuid-suffixed names -- deliberately never colliding with the
// pre-existing PLUGIN_SETUP_PROBE_TOKEN reference (left untouched, per the
// brief).
const custodyReferenz = `TASK8_CUSTODY_${randomUUID().replace(/-/g, "").toUpperCase()}`;
const custodyWert = invented("custody-direct");
const neverStoredReferenz = `TASK8_NEVERSTORED_${randomUUID().replace(/-/g, "").toUpperCase()}`;

// C's credential slot name is fixed by the pinned catalog's github MCP
// component (GITHUB_PAT_TOKEN) -- not ours to rename.
const GITHUB_REFERENCE = "GITHUB_PAT_TOKEN";
const githubWert = invented("rowboat-github");

let controlMarker = "";
let windowStartIso = "";
let openfangLogStartSize = -1;
let installedInstallationId: string | undefined;

describe.skipIf(SKIP)("plugin-setup-agent live proof (Part V)", () => {
  beforeAll(async () => {
    if (!existsSync(resolve(PLUGIN_SETUP_DIR, "werkzeuge.py"))) {
      throw new Error(`plugin-setup dir not found at ${PLUGIN_SETUP_DIR} (path arithmetic is wrong)`);
    }
    client = new MongoClient(LIVE_MONGO);
    await client.connect();
    windowStartIso = new Date(Date.now() - 3_000).toISOString();
    if (OPENFANG_LOG_FILE !== "" && existsSync(OPENFANG_LOG_FILE)) {
      openfangLogStartSize = statSync(OPENFANG_LOG_FILE).size;
    }
    log(`setup: project=${projectId} db=${DB_NAME} pluginSetupDir=${PLUGIN_SETUP_DIR}`);
  });

  afterAll(async () => {
    // Mongo: unlike Parts I-IV (which deliberately kept their evidence in
    // the shared `rowboat` db), this task's constraints ask for cleanup --
    // but MongodbProjectsRepository is bound to that same shared db as a
    // module singleton (see the DB_NAME comment above), so isolation via a
    // dedicated database name is not available here. Delete only what this
    // run's own `projectId` touched; the shared, reused plugin catalog
    // snapshot/entries (180 entries, relied on by other live proofs) are
    // deliberately left alone.
    try {
      const database = client.db(DB_NAME);
      const projectsDeleted = await database.collection("projects").deleteMany({ _id: projectId } as never);
      const installationsDeleted = await database.collection("plugin_installations").deleteMany({ projectId });
      let admissionsDeleted = 0;
      let slotsDeleted = 0;
      let claimsDeleted = 0;
      if (installedInstallationId !== undefined) {
        admissionsDeleted = (await database.collection("plugin_component_admissions").deleteMany({ installationId: installedInstallationId })).deletedCount;
        slotsDeleted = (await database.collection("plugin_credential_slots").deleteMany({ installationId: installedInstallationId })).deletedCount;
        claimsDeleted = (await database.collection("plugin_execution_claims").deleteMany({ installationId: installedInstallationId })).deletedCount;
      }
      const receiptDocs = await database.collection("plugin_receipts").find({}).toArray();
      const receiptIds = receiptDocs
        .filter(document => typeof document.payload === "string" && (document.payload as string).includes(projectId))
        .map(document => document.receiptId as string);
      const receiptsDeleted = receiptIds.length === 0
        ? 0
        : (await database.collection("plugin_receipts").deleteMany({ receiptId: { $in: receiptIds } })).deletedCount;
      log(`cleanup: Mongo rows for project ${projectId} removed -- ` +
        `projects=${projectsDeleted.deletedCount} installations=${installationsDeleted.deletedCount} ` +
        `admissions=${admissionsDeleted} credentialSlots=${slotsDeleted} executionClaims=${claimsDeleted} receipts=${receiptsDeleted}`);
    } catch (error) {
      log(`cleanup: Mongo cleanup failed: ${error instanceof Error ? error.message : String(error)}`);
    }
    await client?.close();

    // Supabase: only A touched it. plugin_setup_agent has no DELETE (by
    // design), so cleanup runs as postgres, matching deploy/smoke.sh.
    try {
      const container = findSupabaseContainer();
      const cleanupSql = [
        `DELETE FROM vault.secrets WHERE id IN (`,
        `  SELECT vault_secret_id FROM plugin_setup.einrichtungen`,
        `   WHERE referenz_name = ${sqlLiteral(failClosedReferenz)} AND vault_secret_id IS NOT NULL);`,
        `DELETE FROM plugin_setup.einrichtungen WHERE referenz_name = ${sqlLiteral(failClosedReferenz)};`,
      ].join("\n");
      const result = psql(container, cleanupSql);
      log(`cleanup: Supabase row+vault-secret for ${failClosedReferenz} -> exit=${result.code}`);
    } catch (error) {
      log(`cleanup: Supabase cleanup failed: ${error instanceof Error ? error.message : String(error)}`);
    }

    // OpenFang: no delete/revoke endpoint exists on this daemon (probed
    // directly against this build during this proof: DELETE and POST to
    // /api/credentials/<ref>, /revoke, /remove, /delete all answer 404).
    // The references this run stored (${custodyReferenz}, ${GITHUB_REFERENCE
    // with an overwritten fake value) therefore remain in this daemon's
    // issuable list until the controller's own teardown of the whole
    // isolated daemon -- the same disposition the environment already
    // states for PLUGIN_SETUP_PROBE_TOKEN. Not touching the daemon further
    // (no restart, no file edits under its live OPENFANG_HOME) is itself a
    // global constraint, so this is the honest stopping point, not an
    // oversight.
    log("cleanup: OpenFang has no credential-delete endpoint (probed: 404 on every guess) " +
      "-- stored references remain until the controller tears the isolated daemon down");
  });

  it("A -- fail-closed: Supabase intake, a real provider 401, nothing reaches OpenFang", () => {
    const result = runSchluesselEntgegennehmen(failClosedReferenz, failClosedWert);
    log(`schluessel_entgegennehmen(${failClosedReferenz}, art=bearer, <invented>) -> ok=${String(result.ok)} status=${String(result.status)}`);
    expect(result.ok).toBe(false);
    expect(result.status).toBe(401);
    expect(result.referenz).toBe(failClosedReferenz);
    expect(result.fehler ?? "").not.toContain(failClosedWert);

    const container = findSupabaseContainer();
    const rowSql = `SELECT status, hinweis, (vault_secret_id IS NOT NULL) FROM plugin_setup.einrichtungen WHERE referenz_name = ${sqlLiteral(failClosedReferenz)};`;
    const row = psql(container, rowSql);
    expect(row.code).toBe(0);
    const [status, hinweis, hasSecret] = row.output.trim().split("|");
    log(`Supabase row: status=${String(status)} hinweis=${String(hinweis)} vault_secret_present=${String(hasSecret)}`);
    expect(status).toBe("fehlgeschlagen");
    expect(hinweis).toBe("401");
    expect(hasSecret).toBe("t");
    expect(row.output).not.toContain(failClosedWert);
  });

  it("A2 -- OpenFang never saw the fail-closed reference", async () => {
    const issued = await issueCredential(failClosedReferenz);
    log(`OpenFang issue(${failClosedReferenz}) after the fail-closed run -> HTTP ${issued.status}`);
    expect(issued.status).toBe(404);
  });

  it("B -- custody, direct: /store -> 200, issuable without a restart, unknown name -> 404", async () => {
    const stored = await storeCredential(custodyReferenz, custodyWert, false);
    log(`OpenFang store(${custodyReferenz}) -> HTTP ${stored.status}`);
    expect(stored.status).toBe(200);

    // No restart happened between store() and issue() -- same daemon
    // process, same test, milliseconds apart. This IS the "without a
    // daemon restart" proof: the reference is immediately issuable.
    const issued = await issueCredential(custodyReferenz);
    const issuedValue = isRecord(issued.body) && typeof issued.body.value === "string" ? issued.body.value : undefined;
    log(`OpenFang issue(${custodyReferenz}) -> HTTP ${issued.status} value_matches_stored=${String(issuedValue === custodyWert)}`);
    expect(issued.status).toBe(200);
    expect(issuedValue).toBe(custodyWert);

    const unknown = await issueCredential(neverStoredReferenz);
    log(`OpenFang issue(${neverStoredReferenz}) [never stored] -> HTTP ${unknown.status}`);
    expect(unknown.status).toBe(404);

    // The brief names this file specifically: confirm the store landed in
    // it, by name, not merely inferred from the API round-trip above.
    if (OPENFANG_HOME_DIR === "") {
      log("issuable_credentials.list check: skipped (PLUGIN_SETUP_OPENFANG_HOME not given) -- not claimed, not asserted");
    } else {
      const listPath = resolve(OPENFANG_HOME_DIR, "issuable_credentials.list");
      expect(existsSync(listPath)).toBe(true);
      const listContent = readFileSync(listPath, "utf8");
      const names = listContent.split(/\r?\n/).map(line => line.trim()).filter(Boolean);
      log(`issuable_credentials.list: contains ${custodyReferenz}=${String(names.includes(custodyReferenz))}, ${names.length} total reference(s)`);
      expect(names).toContain(custodyReferenz);
      expect(names).not.toContain(neverStoredReferenz);
    }
  });

  it("C -- Rowboat: component-scoped install, tool binding, release, decision, provider, receipt", async () => {
    const database = client.db(DB_NAME);

    const { ensureAllIndexes } = await import("@/src/infrastructure/mongodb/ensure-indexes");
    await ensureAllIndexes(database);
    const workflow = { agents: [], prompts: [], tools: [], startAgent: "", lastUpdatedAt: new Date().toISOString() };
    await database.collection("projects").insertOne({
      _id: projectId, name: "setup-agent-live-proof", createdAt: new Date().toISOString(), createdByUserId: "guest_user",
      secret: "e2e-secret", draftWorkflow: workflow, liveWorkflow: workflow,
    } as never);
    const { MongodbPluginsRepository, MongoPluginTransactionRunner } = await import("@/src/infrastructure/repositories/mongodb.plugins.repository");
    const { MongodbProjectsRepository } = await import("@/src/infrastructure/repositories/mongodb.projects.repository");
    const plugins = new MongodbPluginsRepository({ pluginsDatabase: database, pluginTransactionRunner: new MongoPluginTransactionRunner({ pluginsMongoClient: client }) });
    const projects = new MongodbProjectsRepository({ projectMembersRepository: {} as never });
    log(`project ${projectId} inserted into its own database ${DB_NAME}`);

    const { readCatalogLock } = await import("@/scripts/load-plugin-catalog");
    const lock = await readCatalogLock("config/openai-plugin-catalog.lock.json", resolve(process.cwd(), "..", ".."));
    if ((await plugins.getCatalog(PINNED_PLUGIN_CATALOG_DIGEST)) === null) await plugins.putCatalog(lock);
    const seeded = await plugins.getCatalog(PINNED_PLUGIN_CATALOG_DIGEST);
    expect(seeded?.entries).toHaveLength(180);

    const entry = seeded!.entries.find(candidate => candidate.name === "github")!;
    const admitted = entry.components.filter(candidate => candidate.admission.status === "admitted");
    const component = admitted.find(candidate => candidate.component.kind === "mcp")!;
    const componentDigest = component.component.metadata.bindingDigest as string;
    log(`github: ${entry.components.length} components, ${admitted.length} admitted, selecting ${component.component.name} ${componentDigest.slice(0, 12)}...`);
    log(`  credentialSlots = ${JSON.stringify(component.component.metadata.credentialSlots)}`);

    const { InstallPluginUseCase } = await import("@/src/application/use-cases/plugins/install-plugin.use-case");
    const install = new InstallPluginUseCase({ pluginsRepository: plugins, pluginApiAuthorizationPolicy: { authenticate: async () => identity, authorizeProject: async () => undefined } });
    const receipt = await install.execute({
      identity, projectId, pluginName: "github", catalogDigest: PINNED_PLUGIN_CATALOG_DIGEST,
      expectedRevision: 0, idempotencyKey: randomUUID(), componentDigests: [componentDigest],
    });
    log(`install with [${componentDigest.slice(0, 12)}...] -> receipt ${receipt.receiptId} status=${receipt.status}`);
    const installation = (await plugins.getInstallation(projectId, "github"))!;
    installedInstallationId = installation.id;
    const providerBindings = installation.providerBindings ?? [];
    expect(providerBindings).toHaveLength(1);

    const { AddPluginToolUseCase } = await import("@/src/application/use-cases/plugins/add-plugin-tool.use-case");
    const addTool = new AddPluginToolUseCase({
      authorizeProject: async () => undefined,
      loadInstallation: (id, pluginName) => plugins.getInstallation(id, pluginName),
      loadCatalogEntry: async pluginName => {
        const catalog = await plugins.getCatalog(PINNED_PLUGIN_CATALOG_DIGEST);
        const selected = catalog!.entries.find(candidate => candidate.name === pluginName);
        return selected === undefined ? null : { ...selected, catalogDigest: PINNED_PLUGIN_CATALOG_DIGEST };
      },
      loadDraftWorkflow: async id => (await projects.fetch(id))!.draftWorkflow,
      saveDraftWorkflow: async (id, value) => { await projects.updateDraftWorkflow(id, value as never); },
    });
    const addition = await addTool.execute({ identity, projectId, pluginName: "github", componentDigest });
    log(`tool bound: ${addition.toolName} added=${addition.added}`);
    expect(addition.added).toBe(true);

    // The credential slot's own value: stored via the SAME /store endpoint
    // B just proved, not injected some other way. overwrite=true because a
    // re-run of this file must not 409 against a value this same file
    // stored before.
    const stored = await storeCredential(GITHUB_REFERENCE, githubWert, true);
    log(`OpenFang store(${GITHUB_REFERENCE}) [for the runtime's own credential resolver] -> HTTP ${stored.status}`);
    expect(stored.status).toBe(200);

    const approvalWindowMs = resolveOpenFangApprovalWindowMs(process.env.OPENFANG_APPROVAL_TIMEOUT_MS);
    const credentialTimeoutMs = resolveOpenFangCredentialTimeoutMs(process.env.OPENFANG_CREDENTIAL_TIMEOUT_MS);
    const { OpenFangWriteReleasePolicy } = await import("@/src/infrastructure/policies/openfang.plugin-write-release.policy");
    const { PluginToolRuntime } = await import("@/src/application/services/plugin-tool-runtime");
    const dependencies: PluginToolRuntimeDependencies = {
      pluginsRepository: plugins,
      authorizationContext: { caller: "user", userId: "guest_user" },
      authorizeProject: async () => undefined,
      classifyOperation: input => classifyPluginOperation(input),
      timeoutMilliseconds: deriveRuntimeDeadlineMs(approvalWindowMs),
      resolveProvider: input => resolveOpenFangComposedProvider(input, { credentialTimeoutMs }),
      releaseWrite: async (request, signal) => resolveOpenFangReleaseWrite(request, signal, { approvalWindowMs, fetchImpl: fetch, OpenFangWriteReleasePolicyImpl: OpenFangWriteReleasePolicy }),
    };
    const runtime = new PluginToolRuntime(dependencies);
    const executionBinding = { installationId: installation.id, pluginName: "github", componentDigest, providerBindingId: providerBindings[0]!.binding.id, capability: "write" as const };
    log(`runtime composed against ${process.env.OPENFANG_URL}; operation=${OPERATION} (no readOnlyOperations declared -> classified write)`);

    const approver = approveWhenRaised(30_000);
    let outcome = "success";
    const started = Date.now();
    try {
      await runtime.invoke(executionBinding, ARGUMENTS, { projectId, operationName: OPERATION });
    } catch (error) {
      outcome = error instanceof Error ? error.message : "unknown";
    }
    const elapsed = Date.now() - started;
    const raised = await approver;
    log(`invocation outcome: ${outcome} after ${elapsed}ms; approval raised: ${raised === undefined ? "NONE" : raised.id}`);
    expect(raised).toBeDefined();
    expect(raised!.action_summary).toContain(componentDigest.slice(0, 16));

    // No real provider credential is in scope (spec D6-adjacent posture,
    // same as Parts I/II): the fake GITHUB_PAT_TOKEN resolves through the
    // real OpenFangCredentialResolver, and the real HttpMcpProvider reaches
    // https://api.githubcopilot.com/mcp/ over real HTTPS -- and GitHub
    // rejects it. That is `provider_failed`, not `credential_missing`: the
    // credential resolved to A value, the HTTP exchange itself failed. See
    // E2E-PROOF.md Part I section 5 for the code path this distinguishes.
    expect(outcome).toBe("provider_failed");

    await new Promise(done => setTimeout(done, 2_000));
    const storedDocs = await database.collection("plugin_receipts").find({}).toArray();
    const payloads = storedDocs
      .filter(document => typeof document.payload === "string" && (document.payload as string).includes(projectId))
      .map(document => JSON.parse(document.payload as string) as Record<string, unknown>)
      .filter(payload => payload.type === "execution");
    expect(payloads).toHaveLength(1);
    const execution = payloads[0]!;
    const output = execution.output as { approvalId?: string; componentDigest?: string } | undefined;
    log(`execution receipt: status=${String(execution.status)} reason=${String(execution.reason)} approvalId=${String(output?.approvalId)}`);
    expect(execution.status).toBe("failed");
    expect(output?.approvalId).toBe(raised!.id);
    expect(output?.componentDigest).toBe(componentDigest);

    const decided = (await listApprovals()).find(candidate => candidate.id === raised!.id);
    log(`decision for ${raised!.id}: status=${decided?.status} decided_at=${decided?.decided_at}`);
    expect(decided?.status).toBe("approved");
  }, 120_000);

  it("D -- hygiene: every invented value, asserted absent, with a zero-control window", async () => {
    const container = findSupabaseContainer();

    // D1. A positive control: deliberately trigger a FAILING statement whose
    // message contains a KNOWN, harmless marker (never one of our secret
    // VALUES) -- this is exactly the shape of leak a prior finding on this
    // task actually observed (a failing statement's error text landing in
    // `docker logs`). If this marker does not show up in the log window
    // below, the window/filter itself is broken and the "0 hits" for the
    // real secrets would be meaningless.
    controlMarker = `CONTROL_MARKER_TASK8_${randomUUID().replace(/-/g, "")}`;
    const controlSql = `SELECT plugin_setup.fehlschlagen(${sqlLiteral(controlMarker)}, 'x');`;
    const controlResult = psql(container, controlSql);
    log(`positive control: fehlschlagen(${controlMarker}) [expected to fail, unknown referenz] -> exit=${controlResult.code}`);
    expect(controlResult.code).not.toBe(0);

    const logs = dockerLogsSince(container, windowStartIso);
    const controlHits = (logs.match(new RegExp(controlMarker, "g")) ?? []).length;
    log(`docker logs --since ${windowStartIso}: control marker hits=${controlHits} (proves the window captures activity)`);
    expect(controlHits).toBeGreaterThan(0);

    for (const secret of SECRET_VALUES) {
      expect(logs).not.toContain(secret);
    }
    log(`docker logs --since ${windowStartIso}: 0 occurrences of any of the ${SECRET_VALUES.length} invented values`);

    // D2. vault.secrets itself -- asserted, not assumed encrypted-therefore-safe.
    // None of SECRET_VALUES contains a LIKE wildcard (`%`/`_`), so a plain
    // substring LIKE needs no ESCAPE clause. The secret text itself is
    // never selected back out (only a count), so it never re-enters this
    // process's captured stdout either.
    const vaultChecks = SECRET_VALUES.map(secret =>
      `SELECT count(*) FROM vault.secrets ` +
      `WHERE secret LIKE '%' || ${sqlLiteral(secret)} || '%' ` +
      `OR name LIKE '%' || ${sqlLiteral(secret)} || '%' ` +
      `OR description LIKE '%' || ${sqlLiteral(secret)} || '%';`,
    ).join("\n");
    const vaultResult = psql(container, vaultChecks);
    expect(vaultResult.code).toBe(0);
    const counts = vaultResult.output.trim().split("\n").filter(Boolean).map(line => Number(line.trim()));
    log(`vault.secrets scan across ${SECRET_VALUES.length} invented values: counts=[${counts.join(",")}]`);
    expect(counts).toHaveLength(SECRET_VALUES.length);
    for (const count of counts) expect(count).toBe(0);

    // D3. the daemon's own log file, if its path was given. Zero-control:
    // assert the file actually grew during this run before trusting an
    // absence result from it.
    if (OPENFANG_LOG_FILE === "" || !existsSync(OPENFANG_LOG_FILE)) {
      log("daemon log check: skipped (PLUGIN_SETUP_OPENFANG_LOG_FILE not given or file not found) -- not claimed, not asserted");
    } else {
      const endSize = statSync(OPENFANG_LOG_FILE).size;
      log(`daemon log ${OPENFANG_LOG_FILE}: size ${openfangLogStartSize} -> ${endSize}`);
      expect(endSize).toBeGreaterThan(openfangLogStartSize);
      const content = readFileSync(OPENFANG_LOG_FILE, "utf8");
      for (const secret of SECRET_VALUES) expect(content).not.toContain(secret);
      expect(content).toContain(GITHUB_REFERENCE);
      log(`daemon log: 0 occurrences of any invented value; reference name ${GITHUB_REFERENCE} present (name only)`);
    }

    // D4. the Mongo receipt from C.
    const database = client.db(DB_NAME);
    const receipts = await database.collection("plugin_receipts").find({}).toArray();
    const receiptsText = JSON.stringify(receipts);
    for (const secret of SECRET_VALUES) expect(receiptsText).not.toContain(secret);
    log(`plugin_receipts (${receipts.length} document(s)): 0 occurrences of any invented value`);

    // D5. this file's own evidence log, if one was requested -- a
    // self-check that `log()` itself never printed a secret.
    if (EVIDENCE !== "" && existsSync(EVIDENCE)) {
      const evidenceText = readFileSync(EVIDENCE, "utf8");
      for (const secret of SECRET_VALUES) expect(evidenceText).not.toContain(secret);
      log("evidence log: 0 occurrences of any invented value (self-check)");
    }
  }, 60_000);
});
