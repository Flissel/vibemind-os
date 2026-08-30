import { execFile } from "node:child_process";
import { mkdir, readFile, symlink, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { promisify } from "node:util";
import { afterEach, describe, expect, it } from "vitest";
import {
  OPENAI_PLUGINS_SOURCE_URL,
  componentBindingDigest,
  importCatalog,
  parseCatalogSyncArgs,
  pluginCatalogDigest,
  validatePluginCatalogLock,
  writeCatalogLock,
  type PluginCatalogLock,
} from "../src/index.js";
import {
  cleanupRegisteredTestRoots,
  createOwnedTestRoot,
} from "./test-temp.js";
import { resolveCatalogSyncPaths } from "../scripts/sync-catalog.js";

const execFileAsync = promisify(execFile);
const FIXED_TIME = "2026-08-24T00:00:00.000Z";

describe("structural component binding digest", () => {
  const provenance = {
    sourceUrl: OPENAI_PLUGINS_SOURCE_URL,
    pluginName: "brand24",
    pluginVersion: "1.0.0",
    sourceCommit: "1".repeat(40),
    manifestDigest: "2".repeat(64),
    treeDigest: "3".repeat(64),
    importedAt: FIXED_TIME,
    schemaVersion: "rowboat-plugin-schema-v1",
    policyVersion: "rowboat-plugin-policy-v1",
  };
  const component = {
    id: "asset:assets/logo.png",
    name: "logo.png",
    kind: "asset" as const,
    status: "available" as const,
    metadata: { digest: "4".repeat(64), path: "assets/logo.png" },
  };
  const admission = { status: "admitted" as const, policyVersion: "rowboat-plugin-policy-v1" };
  const material = { componentAdmission: admission, licenseDeclaration: "MIT", licenseAdmission: admission };

  it("is deterministic across insertion order and changes for every structural identity input", () => {
    const first = componentBindingDigest(provenance, component, material);
    const reordered = componentBindingDigest(
      { policyVersion: provenance.policyVersion, schemaVersion: provenance.schemaVersion, importedAt: provenance.importedAt, treeDigest: provenance.treeDigest, manifestDigest: provenance.manifestDigest, sourceCommit: provenance.sourceCommit, sourceUrl: provenance.sourceUrl, pluginVersion: provenance.pluginVersion, pluginName: provenance.pluginName },
      { metadata: { path: "assets/logo.png", digest: "4".repeat(64) }, status: "available", kind: "asset", name: "logo.png", id: "asset:assets/logo.png" }, material,
    );
    expect(first).toMatch(/^[a-f0-9]{64}$/);
    expect(reordered).toBe(first);
    for (const changed of [
      componentBindingDigest({ ...provenance, pluginName: "dovetail" }, component, material),
      componentBindingDigest({ ...provenance, pluginVersion: "2.0.0" }, component, material),
      componentBindingDigest({ ...provenance, sourceCommit: "5".repeat(40) }, component, material),
      componentBindingDigest({ ...provenance, manifestDigest: "6".repeat(64) }, component, material),
      componentBindingDigest({ ...provenance, treeDigest: "7".repeat(64) }, component, material),
      componentBindingDigest(provenance, { ...component, id: "asset:assets/logo-dark.png" }, material),
      componentBindingDigest(provenance, { ...component, name: "logo-dark.png" }, material),
      componentBindingDigest(provenance, { ...component, kind: "app" }, material),
      componentBindingDigest(provenance, { ...component, status: "unavailable", reason: "provider_unavailable" }, material),
      componentBindingDigest(provenance, { ...component, metadata: { ...component.metadata, digest: "8".repeat(64) } }, material),
      componentBindingDigest(provenance, { ...component, metadata: { ...component.metadata, transport: "process" } }, material),
      componentBindingDigest(provenance, component, { ...material, componentAdmission: { status: "rejected", reason: "license_rejected", policyVersion: admission.policyVersion } }),
    ]) expect(changed).not.toBe(first);
  });
});

describe("exact plugin catalog lock validation", () => {
  const loadPinnedLock = async (): Promise<PluginCatalogLock> => JSON.parse(
    await readFile(join(process.cwd(), "..", "..", "config", "openai-plugin-catalog.lock.json"), "utf8"),
  ) as PluginCatalogLock;

  const rebindAndRehash = (candidate: PluginCatalogLock): PluginCatalogLock => {
    for (let entryIndex = 0; entryIndex < candidate.entries.length; entryIndex += 1) {
      const entry = candidate.entries[entryIndex]!;
      for (let componentIndex = 0; componentIndex < entry.components.length; componentIndex += 1) {
        const selected = entry.components[componentIndex]!;
        (selected.component.metadata as Record<string, unknown>).bindingDigest = componentBindingDigest(entry, selected.component, {
          componentAdmission: selected.admission,
          licenseDeclaration: entry.licenseDeclaration ?? "<missing>",
          licenseAdmission: entry.admission,
        });
      }
    }
    const { catalogDigest: ignored, ...payload } = candidate;
    void ignored;
    (candidate as { catalogDigest: string }).catalogDigest = pluginCatalogDigest(payload);
    return candidate;
  };

  it("addresses the catalog by content, not by import time", () => {
    // The pinned digest is only reachable if importing the same commit twice
    // produces the same digest. Import timestamps are provenance metadata and
    // must not participate in the content address.
    const payload = {
      sourceUrl: "https://github.com/openai/plugins.git", sourceCommit: "1".repeat(40),
      importedAt: "2026-08-29T09:00:00.000Z", schemaVersion: "rowboat-plugin-schema-v1",
      policyVersion: "rowboat-plugin-policy-v1", inventory: { plugins: 1 },
      licenseDeclarations: { MIT: 1 },
      entries: [{ name: "github", importedAt: "2026-08-29T09:00:00.000Z", licenseDeclaration: "MIT" }],
    } as unknown as Parameters<typeof pluginCatalogDigest>[0];
    const later = {
      ...(payload as unknown as Record<string, unknown>),
      importedAt: "2026-08-30T18:45:12.913Z",
      entries: [{ name: "github", importedAt: "2026-08-30T18:45:12.913Z", licenseDeclaration: "MIT" }],
    } as unknown as Parameters<typeof pluginCatalogDigest>[0];
    const different = {
      ...(payload as unknown as Record<string, unknown>),
      entries: [{ name: "gitlab", importedAt: "2026-08-29T09:00:00.000Z", licenseDeclaration: "MIT" }],
    } as unknown as Parameters<typeof pluginCatalogDigest>[0];

    expect(pluginCatalogDigest(later)).toBe(pluginCatalogDigest(payload));
    expect(pluginCatalogDigest(different)).not.toBe(pluginCatalogDigest(payload));

    // The component binding digest is the identity an installation pins, so it
    // must survive a re-import of the same commit for the same reason.
    const provenance = {
      sourceUrl: "https://github.com/openai/plugins.git", sourceCommit: "1".repeat(40),
      pluginName: "github", pluginVersion: "1.0.0", manifestDigest: "a".repeat(64), treeDigest: "b".repeat(64),
      importedAt: "2026-08-29T09:00:00.000Z", schemaVersion: "rowboat-plugin-schema-v1", policyVersion: "rowboat-plugin-policy-v1",
    };
    const component = { id: "app:.app.json#github", name: "github", kind: "app", status: "available", metadata: { digest: "c".repeat(64) } };
    const material = { componentAdmission: { status: "admitted", policyVersion: "rowboat-plugin-policy-v1" }, licenseDeclaration: "MIT", licenseAdmission: { status: "admitted", policyVersion: "rowboat-plugin-policy-v1" } };
    const first = componentBindingDigest(provenance as never, component as never, material as never);
    const reimported = componentBindingDigest({ ...provenance, importedAt: "2026-09-02T11:22:33.444Z" } as never, component as never, material as never);
    const otherContent = componentBindingDigest(provenance as never, { ...component, metadata: { digest: "d".repeat(64) } } as never, material as never);
    expect(reimported).toBe(first);
    expect(otherContent).not.toBe(first);
  });

  it("rejects fully recomputed mutations instead of trusting self-consistent alternate catalogs", async () => {
    const original = await loadPinnedLock();
    const mutators: Array<(candidate: PluginCatalogLock) => void> = [
      (candidate) => { (candidate.entries[0]!.components[0]!.component as { name: string }).name += " forged"; },
      (candidate) => { (candidate.entries[0]!.components[0]!.component as { status: string }).status = "unavailable"; },
      (candidate) => { (candidate.entries[0]!.components[0]!.component.metadata as Record<string, unknown>).transport = "forged"; },
      (candidate) => { (candidate.entries[0]!.components[0]!.component.metadata as Record<string, unknown>).credentialSlots = ["FORGED"]; },
      (candidate) => { (candidate.entries[0]!.components[0] as { admission: unknown }).admission = { status: "rejected", reason: "component_unsupported", policyVersion: candidate.policyVersion }; },
      (candidate) => {
        (candidate.entries[0] as { admission: unknown }).admission = { status: "review_required", reason: "license_review_required", policyVersion: candidate.policyVersion };
        delete (candidate.entries[0] as { storedContentDigest?: string }).storedContentDigest;
      },
      (candidate) => {
        (candidate as { policyVersion: string }).policyVersion = "rowboat-plugin-policy-v2";
        for (let entryIndex = 0; entryIndex < candidate.entries.length; entryIndex += 1) {
          const entry = candidate.entries[entryIndex]!;
          (entry as { policyVersion: string }).policyVersion = candidate.policyVersion;
          (entry.admission as { policyVersion: string }).policyVersion = candidate.policyVersion;
          for (let componentIndex = 0; componentIndex < entry.components.length; componentIndex += 1) {
            (entry.components[componentIndex]!.admission as { policyVersion: string }).policyVersion = candidate.policyVersion;
          }
        }
      },
    ];
    for (let index = 0; index < mutators.length; index += 1) {
      const candidate = structuredClone(original);
      mutators[index]!(candidate);
      rebindAndRehash(candidate);
      expect(() => validatePluginCatalogLock(candidate)).toThrow("catalog_lock_invalid");
    }
  });

  it("does not depend on mutable Array prototype methods after module initialization", async () => {
    const lock = await loadPinnedLock();
    for (const method of ["includes", "some", "map", "sort", "every", "filter", "reduce"] as const) {
      const original = Array.prototype[method];
      let result: PluginCatalogLock | undefined;
      let failure: unknown;
      try {
        Object.defineProperty(Array.prototype, method, {
          configurable: true,
          writable: true,
          value: () => { throw new Error(`live_array_primordial:${method}`); },
        });
        try { result = validatePluginCatalogLock(lock); } catch (error: unknown) { failure = error; }
      } finally {
        Object.defineProperty(Array.prototype, method, { configurable: true, writable: true, value: original });
      }
      expect(failure).toBeUndefined();
      expect(result?.catalogDigest).toBe(lock.catalogDigest);
    }
  });

  it("accepts the committed full lock and rejects entry, inventory, and cardinality drift", async () => {
    const lock = JSON.parse(await readFile(join(process.cwd(), "..", "..", "config", "openai-plugin-catalog.lock.json"), "utf8")) as unknown;
    const validated = validatePluginCatalogLock(lock);
    expect(validated.entries).toHaveLength(180);
    const mutatedName = structuredClone(validated);
    (mutatedName.entries[0] as { name: string }).name = "swapped";
    expect(() => validatePluginCatalogLock(mutatedName)).toThrow("catalog_lock_invalid");
    const missing = { ...structuredClone(validated), entries: structuredClone(validated.entries).slice(0, 179) };
    expect(() => validatePluginCatalogLock(missing)).toThrow("catalog_lock_invalid");
    const inventory = structuredClone(validated);
    (inventory.inventory as { pluginsWithApps: number }).pluginsWithApps += 1;
    expect(() => validatePluginCatalogLock(inventory)).toThrow("catalog_lock_invalid");
  });

  it("rejects every execution, admission, license, inventory, and cardinality mutation even after rehash", async () => {
    const original = JSON.parse(await readFile(join(process.cwd(), "..", "..", "config", "openai-plugin-catalog.lock.json"), "utf8")) as PluginCatalogLock;
    const rehash = (candidate: PluginCatalogLock): PluginCatalogLock => {
      const { catalogDigest: ignored, ...payload } = candidate;
      void ignored;
      return { ...candidate, catalogDigest: pluginCatalogDigest(payload) };
    };
    const firstComponent = (candidate: PluginCatalogLock) => candidate.entries.find((entry) => entry.components.length > 0)!.components[0]!;
    const mutations: PluginCatalogLock[] = [];
    for (const mutate of [
      (candidate: PluginCatalogLock) => { (firstComponent(candidate).component as { name: string }).name += " changed"; },
      (candidate: PluginCatalogLock) => { (firstComponent(candidate).component as { status: string }).status = "unavailable"; },
      (candidate: PluginCatalogLock) => { (firstComponent(candidate).component.metadata as Record<string, unknown>).transport = "forged"; },
      (candidate: PluginCatalogLock) => { (firstComponent(candidate).component.metadata as Record<string, unknown>).credentialSlots = ["FORGED"]; },
      (candidate: PluginCatalogLock) => { (firstComponent(candidate) as { admission: unknown }).admission = { status: "rejected", reason: "license_rejected", policyVersion: candidate.policyVersion }; },
      (candidate: PluginCatalogLock) => { (candidate.entries[0] as { licenseDeclaration: string }).licenseDeclaration = "FORGED"; },
      (candidate: PluginCatalogLock) => { (candidate.inventory as { pluginsWithApps: number }).pluginsWithApps += 1; },
    ]) {
      const candidate = structuredClone(original);
      mutate(candidate);
      mutations.push(rehash(candidate));
    }
    const missing = structuredClone(original);
    (missing.entries as unknown as unknown[]).pop();
    mutations.push(rehash(missing));
    const extra = structuredClone(original);
    (extra.entries as unknown as unknown[]).push(structuredClone(extra.entries[0]));
    mutations.push(rehash(extra));
    const swapped = structuredClone(original);
    const mutableEntries = swapped.entries as unknown as PluginCatalogLock["entries"][number][];
    [mutableEntries[0], mutableEntries[1]] = [mutableEntries[1]!, mutableEntries[0]!];
    mutations.push(rehash(swapped));
    for (const candidate of mutations) expect(() => validatePluginCatalogLock(candidate)).toThrow("catalog_lock_invalid");
  });

  it("rejects proxies and accessors without invoking their traps or getters", async () => {
    const lock = JSON.parse(await readFile(join(process.cwd(), "..", "..", "config", "openai-plugin-catalog.lock.json"), "utf8")) as PluginCatalogLock;
    let getterCalls = 0;
    const withGetter = structuredClone(lock) as PluginCatalogLock & { catalogSecret?: string };
    Object.defineProperty(withGetter, "catalogSecret", { enumerable: true, get: () => { getterCalls += 1; return "token-secret"; } });
    expect(() => validatePluginCatalogLock(withGetter)).toThrow("catalog_lock_invalid");
    let trapCalls = 0;
    const proxied = new Proxy(lock, { ownKeys: (target) => { trapCalls += 1; return Reflect.ownKeys(target); } });
    expect(() => validatePluginCatalogLock(proxied)).toThrow("catalog_lock_invalid");
    expect(getterCalls).toBe(0);
    expect(trapCalls).toBe(0);
  });

  it("rejects an invented admission reason even when binding and catalog digests are recomputed", async () => {
    const candidate = JSON.parse(await readFile(join(process.cwd(), "..", "..", "config", "openai-plugin-catalog.lock.json"), "utf8")) as PluginCatalogLock;
    const entry = candidate.entries.find((selected) => selected.components.length > 0)!;
    const selected = entry.components[0]!;
    const forgedAdmission = { status: "rejected" as const, reason: "invented_reason", policyVersion: candidate.policyVersion };
    (selected as { admission: unknown }).admission = forgedAdmission;
    (selected.component.metadata as Record<string, unknown>).bindingDigest = componentBindingDigest(entry, selected.component, {
      componentAdmission: forgedAdmission as never,
      licenseDeclaration: entry.licenseDeclaration ?? "<missing>",
      licenseAdmission: entry.admission,
    });
    const { catalogDigest: ignored, ...payload } = candidate;
    void ignored;
    (candidate as { catalogDigest: string }).catalogDigest = pluginCatalogDigest(payload);
    expect(() => validatePluginCatalogLock(candidate)).toThrow("catalog_lock_invalid");
  });
});

interface FixturePlugin {
  readonly directoryName: string;
  readonly manifestName?: string;
  readonly version?: string;
  readonly license?: string;
  readonly surfaces?: readonly ("skills" | "apps" | "agents" | "commands" | "mcp" | "hooks")[];
  readonly conventionalSurfaces?: readonly ("agents" | "commands" | "hooks")[];
}

afterEach(cleanupRegisteredTestRoots);

async function createSource(
  label: string,
  plugins: readonly FixturePlugin[],
): Promise<{ readonly repositoryRoot: string; readonly pluginsRoot: string; readonly commit: string; readonly storeRoot: string }> {
  const root = await createOwnedTestRoot(label);
  const repositoryRoot = join(root, "source");
  const pluginsRoot = join(repositoryRoot, "plugins");
  const storeRoot = join(root, "store");
  await mkdir(pluginsRoot, { recursive: true });

  for (const plugin of plugins) {
    const pluginRoot = join(pluginsRoot, plugin.directoryName);
    const manifestRoot = join(pluginRoot, ".codex-plugin");
    await mkdir(manifestRoot, { recursive: true });
    const surfaces = new Set(plugin.surfaces ?? []);
    const conventionalSurfaces = new Set(plugin.conventionalSurfaces ?? []);
    const manifest: Record<string, unknown> = {
      name: plugin.manifestName ?? plugin.directoryName,
      version: plugin.version ?? "1.0.0",
      description: `${plugin.directoryName} fixture`,
      interface: { displayName: plugin.directoryName },
    };
    if (plugin.license !== undefined) manifest.license = plugin.license;
    if (surfaces.has("skills")) {
      manifest.skills = "./skills/";
      await mkdir(join(pluginRoot, "skills", "sample"), { recursive: true });
      await writeFile(join(pluginRoot, "skills", "sample", "SKILL.md"), "# Sample\n");
    }
    if (surfaces.has("apps")) {
      manifest.apps = "./.app.json";
      await writeFile(join(pluginRoot, ".app.json"), JSON.stringify({ apps: { sample: { id: "connector_abcdef" } } }));
    }
    if (surfaces.has("agents") || conventionalSurfaces.has("agents")) {
      if (surfaces.has("agents")) manifest.agents = "./agents/";
      await mkdir(join(pluginRoot, "agents"));
      await writeFile(join(pluginRoot, "agents", "sample.md"), "# Agent\n");
    }
    if (surfaces.has("commands") || conventionalSurfaces.has("commands")) {
      if (surfaces.has("commands")) manifest.commands = "./commands/";
      await mkdir(join(pluginRoot, "commands"));
      await writeFile(join(pluginRoot, "commands", "sample.md"), "# Command\n");
    }
    if (surfaces.has("mcp")) {
      manifest.mcpServers = "./.mcp.json";
      await writeFile(join(pluginRoot, ".mcp.json"), JSON.stringify({ mcpServers: { sample: { type: "http", url: "https://example.com/mcp" } } }));
    }
    if (surfaces.has("hooks") || conventionalSurfaces.has("hooks")) {
      if (surfaces.has("hooks")) manifest.hooks = "./hooks.json";
      await writeFile(join(pluginRoot, "hooks.json"), JSON.stringify({ hooks: { PreToolUse: [{ command: "echo blocked" }] } }));
    }
    await writeFile(join(manifestRoot, "plugin.json"), `${JSON.stringify(manifest)}\n`);
  }

  await execFileAsync("git", ["init", "--quiet", repositoryRoot]);
  await execFileAsync("git", ["-C", repositoryRoot, "config", "user.email", "rowboat@example.invalid"]);
  await execFileAsync("git", ["-C", repositoryRoot, "config", "user.name", "Rowboat Test"]);
  await execFileAsync("git", ["-C", repositoryRoot, "remote", "add", "origin", OPENAI_PLUGINS_SOURCE_URL]);
  await execFileAsync("git", ["-C", repositoryRoot, "add", "."]);
  await execFileAsync("git", ["-C", repositoryRoot, "commit", "--quiet", "-m", "fixture"]);
  const { stdout } = await execFileAsync("git", ["-C", repositoryRoot, "rev-parse", "HEAD"]);
  return { repositoryRoot, pluginsRoot, commit: stdout.trim(), storeRoot };
}

function options(source: Awaited<ReturnType<typeof createSource>>) {
  return {
    repositoryRoot: source.repositoryRoot,
    sourceCommit: source.commit,
    sourceUrl: OPENAI_PLUGINS_SOURCE_URL,
    storeRoot: source.storeRoot,
    clock: (): Date => new Date(FIXED_TIME),
  } as const;
}

async function createGitInvocationRoot(label: string): Promise<string> {
  const root = await createOwnedTestRoot(label);
  await execFileAsync("git", ["init", "--quiet", root]);
  return root;
}

describe("importCatalog", () => {
  it("resolves a relative CLI output from the validated invocation root", async () => {
    const source = await createSource("catalog-cli-paths", [
      { directoryName: "alpha", license: "MIT" },
    ]);
    const invocationRoot = await createGitInvocationRoot("catalog-invocation-root");
    const relativeOutput = join(
      "spaces",
      "rowboat",
      "rowboat",
      "config",
      "openai-plugin-catalog.lock.json",
    );

    const paths = await resolveCatalogSyncPaths([
      "--source", source.pluginsRoot,
      "--commit", "11c74d6ba24d3a6d48f54a194cd00ef3beea18f9",
      "--output", relativeOutput,
    ], source.storeRoot, invocationRoot);

    expect(paths.output).toBe(join(invocationRoot, relativeOutput));
    expect(paths.repositoryRoot).toBe(source.repositoryRoot);
    expect(paths.source).toBe(source.pluginsRoot);
    expect(paths.storeRoot).toBe(source.storeRoot);
  });

  it("rejects absolute output outside the invocation worktree", async () => {
    const source = await createSource("catalog-absolute-outside", [
      { directoryName: "alpha", license: "MIT" },
    ]);
    const invocationRoot = await createGitInvocationRoot("catalog-absolute-root");
    const outsideRoot = await createOwnedTestRoot("catalog-absolute-target");

    await expect(resolveCatalogSyncPaths([
      "--source", source.pluginsRoot,
      "--commit", "11c74d6ba24d3a6d48f54a194cd00ef3beea18f9",
      "--output", join(outsideRoot, "catalog.json"),
    ], source.storeRoot, invocationRoot)).rejects.toThrow("path_escape:catalog_output");
  });

  it("rejects a non-Git root and a Git worktree subdirectory", async () => {
    const source = await createSource("catalog-fake-root", [
      { directoryName: "alpha", license: "MIT" },
    ]);
    const nonGitRoot = await createOwnedTestRoot("catalog-non-git-root");
    const invocationRoot = await createGitInvocationRoot("catalog-git-root");
    const subdirectory = join(invocationRoot, "subdirectory");
    await mkdir(subdirectory);
    const args = [
      "--source", source.pluginsRoot,
      "--commit", "11c74d6ba24d3a6d48f54a194cd00ef3beea18f9",
      "--output", "catalog.json",
    ] as const;

    await expect(resolveCatalogSyncPaths(args, source.storeRoot, nonGitRoot))
      .rejects.toThrow("source_mismatch:invocation_root");
    await expect(resolveCatalogSyncPaths(args, source.storeRoot, subdirectory))
      .rejects.toThrow("source_mismatch:invocation_root");
  });

  it("rejects an output whose existing ancestor escapes through a reparse point", async () => {
    const source = await createSource("catalog-reparse-source", [
      { directoryName: "alpha", license: "MIT" },
    ]);
    const invocationRoot = await createGitInvocationRoot("catalog-reparse-root");
    const outsideRoot = await createOwnedTestRoot("catalog-reparse-target");
    const link = join(invocationRoot, "escaped");
    try {
      await symlink(outsideRoot, link, process.platform === "win32" ? "junction" : "dir");
    } catch {
      return;
    }

    await expect(resolveCatalogSyncPaths([
      "--source", source.pluginsRoot,
      "--commit", "11c74d6ba24d3a6d48f54a194cd00ef3beea18f9",
      "--output", join("escaped", "catalog.json"),
    ], source.storeRoot, invocationRoot)).rejects.toThrow("path_escape:catalog_output");
  });

  it("accepts only the exact pinned commit at the CLI boundary", () => {
    expect(parseCatalogSyncArgs([
      "--source", "C:\\source\\plugins",
      "--commit", "11c74d6ba24d3a6d48f54a194cd00ef3beea18f9",
      "--output", "C:\\output\\catalog.json",
    ])).toEqual({
      source: "C:\\source\\plugins",
      commit: "11c74d6ba24d3a6d48f54a194cd00ef3beea18f9",
      output: "C:\\output\\catalog.json",
    });
    expect(() => parseCatalogSyncArgs([
      "--source", "C:\\source\\plugins",
      "--commit", "0000000000000000000000000000000000000000",
      "--output", "C:\\output\\catalog.json",
    ])).toThrow("source_mismatch:catalog_arguments");
  });

  it("rejects a catalog when folder and manifest names differ", async () => {
    const source = await createSource("catalog-mismatch", [
      { directoryName: "wrong", manifestName: "right", license: "MIT" },
    ]);

    await expect(importCatalog(source.pluginsRoot, options(source))).rejects.toThrow(
      "manifest_invalid:name_mismatch",
    );
  });

  it("rejects a direct plugin directory with no manifest", async () => {
    const source = await createSource("catalog-missing", [
      { directoryName: "alpha", license: "MIT" },
    ]);
    await mkdir(join(source.pluginsRoot, "missing"));
    await writeFile(join(source.pluginsRoot, "missing", "README.md"), "missing manifest\n");
    await execFileAsync("git", ["-C", source.repositoryRoot, "add", "."]);
    await execFileAsync("git", ["-C", source.repositoryRoot, "commit", "--quiet", "-m", "missing"]);
    const { stdout } = await execFileAsync("git", ["-C", source.repositoryRoot, "rev-parse", "HEAD"]);

    await expect(importCatalog(source.pluginsRoot, {
      ...options(source),
      sourceCommit: stdout.trim(),
    })).rejects.toThrow("manifest_invalid:missing_manifest");
  });

  it("sorts entries by code point and records complete source, schema, and policy provenance", async () => {
    const source = await createSource("catalog-provenance", [
      { directoryName: "zeta", license: "Apache-2.0" },
      { directoryName: "alpha", license: "MIT" },
    ]);

    const lock = await importCatalog(source.pluginsRoot, options(source));

    expect(lock.entries.map((entry) => entry.name)).toEqual(["alpha", "zeta"]);
    expect(lock.sourceUrl).toBe(OPENAI_PLUGINS_SOURCE_URL);
    expect(lock.sourceCommit).toBe(source.commit);
    expect(lock.entries[0]).toMatchObject({
      name: "alpha",
      pluginName: "alpha",
      pluginVersion: "1.0.0",
      sourceUrl: OPENAI_PLUGINS_SOURCE_URL,
      sourceCommit: source.commit,
      manifestDigest: expect.stringMatching(/^[a-f0-9]{64}$/),
      treeDigest: expect.stringMatching(/^[a-f0-9]{64}$/),
      importedAt: FIXED_TIME,
      schemaVersion: "rowboat-plugin-schema-v1",
      policyVersion: "rowboat-plugin-policy-v1",
      admission: { status: "admitted", policyVersion: "rowboat-plugin-policy-v1" },
      storedContentDigest: expect.stringMatching(/^[a-f0-9]{64}$/),
    });
    expect(lock.policyVersion).toBe("rowboat-plugin-policy-v1");
    expect(lock.schemaVersion).toBe("rowboat-plugin-schema-v1");
    expect(lock.catalogDigest).toMatch(/^[a-f0-9]{64}$/);
    expect(JSON.parse(JSON.stringify(lock))).toEqual(lock);
  });

  it("counts plugin surfaces and license declarations", async () => {
    const source = await createSource("catalog-inventory", [
      { directoryName: "alpha", license: "MIT", surfaces: ["skills", "apps", "agents", "commands", "mcp", "hooks"] },
      { directoryName: "beta", license: "UNLICENSED" },
      { directoryName: "gamma" },
    ]);

    const lock = await importCatalog(source.pluginsRoot, options(source));

    expect(lock.inventory).toEqual({
      pluginsWithSkills: 1,
      pluginsWithApps: 1,
      pluginsWithAgents: 1,
      pluginsWithCommands: 1,
      pluginsWithMcp: 1,
      pluginsWithCommandHooks: 1,
    });
    expect(lock.licenseDeclarations).toEqual({
      MIT: 1,
      UNLICENSED: 1,
      "<missing>": 1,
    });
    expect(lock.entries.find(({ name }) => name === "beta")?.admission).toMatchObject({
      status: "rejected",
      reason: "license_rejected",
    });
    expect(lock.entries.find(({ name }) => name === "gamma")?.admission).toMatchObject({
      status: "review_required",
      reason: "license_review_required",
    });
    expect(lock.entries.map(({ name, licenseDeclaration }) => ({ name, licenseDeclaration }))).toEqual([
      { name: "alpha", licenseDeclaration: "MIT" },
      { name: "beta", licenseDeclaration: "UNLICENSED" },
      { name: "gamma", licenseDeclaration: "<missing>" },
    ]);
  });

  it("counts prototype-like license names as own data keys", async () => {
    const source = await createSource("catalog-license-keys", [
      { directoryName: "alpha", license: "__proto__" },
      { directoryName: "beta", license: "constructor" },
    ]);

    const lock = await importCatalog(source.pluginsRoot, options(source));

    expect(lock.licenseDeclarations).toEqual({
      ["__proto__"]: 1,
      constructor: 1,
    });
    expect(Object.values(lock.licenseDeclarations).reduce(
      (sum, count) => sum + count,
      0,
    )).toBe(lock.entries.length);
  });

  it("counts conventional agent, command, and hook surfaces", async () => {
    const source = await createSource("catalog-conventional", [
      {
        directoryName: "alpha",
        license: "MIT",
        conventionalSurfaces: ["agents", "commands", "hooks"],
      },
    ]);

    const lock = await importCatalog(source.pluginsRoot, options(source));

    expect(lock.inventory).toMatchObject({
      pluginsWithAgents: 1,
      pluginsWithCommands: 1,
      pluginsWithCommandHooks: 1,
    });
  });

  it("declares an executable provider binding for every app and mcp component", async () => {
    const source = await createSource("provider-binding", [
      { directoryName: "alpha", license: "MIT", surfaces: ["apps", "mcp", "skills"] },
    ]);
    const lock = await importCatalog(source.pluginsRoot, options(source));
    const entry = lock.entries[0]!;
    const bindings = new Map<string, unknown>();
    for (const { component } of entry.components) {
      const binding = (component.metadata as { providerBinding?: unknown }).providerBinding;
      if (component.kind !== "app" && component.kind !== "mcp") {
        // A skill or asset is not executed through a provider, so declaring one
        // would claim an execution path that does not exist.
        expect(binding).toBeUndefined();
        continue;
      }
      // The resolver admits a component only when the installed binding matches
      // the component binding digest exactly.
      expect(binding).toMatchObject({
        componentDigest: component.metadata.bindingDigest,
        providerKind: component.kind === "app" ? "openai-connector-bridge" : "mcp-http",
      });
      const id = (binding as { id: string }).id;
      expect(id).toMatch(/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/);
      expect(bindings.has(id)).toBe(false);
      bindings.set(id, binding);
    }
    expect(bindings.size).toBe(2);
  });

  it("keeps the declared binding out of the digest it carries", async () => {
    const source = await createSource("provider-binding-digest", [
      { directoryName: "alpha", license: "MIT", surfaces: ["mcp"] },
    ]);
    const lock = await importCatalog(source.pluginsRoot, options(source));
    const entry = lock.entries[0]!;
    const bound = entry.components.find(item => item.component.kind === "mcp")!;
    // Recomputing the binding digest over the component that already carries
    // the binding must reproduce it: a self-referential digest would be
    // unverifiable and would break on every re-import.
    expect(componentBindingDigest(entry, bound.component, {
      componentAdmission: bound.admission,
      licenseDeclaration: entry.licenseDeclaration ?? "<missing>",
      licenseAdmission: entry.admission,
    })).toBe(bound.component.metadata.bindingDigest);
  });

  it("names the credential an MCP server needs", async () => {
    const source = await createSource("mcp-credentials", [
      { directoryName: "alpha", license: "MIT", surfaces: ["mcp"] },
    ]);
    const lock = await importCatalog(source.pluginsRoot, options(source));
    const mcp = lock.entries[0]!.components.find(item => item.component.kind === "mcp")!;
    // The fixture declares an http server without a token, so it needs none.
    expect(mcp.component.metadata.credentialSlots).toEqual([]);
  });

  it("names a declared bearer token reference as a credential slot", async () => {
    const source = await createSource("mcp-bearer", [
      { directoryName: "alpha", license: "MIT", surfaces: ["mcp"] },
    ]);
    await writeFile(
      join(source.pluginsRoot, "alpha", ".mcp.json"),
      JSON.stringify({ mcpServers: { sample: { type: "http", url: "https://example.com/mcp", bearer_token_env_var: "SAMPLE_TOKEN" } } }),
    );
    await execFileAsync("git", ["-C", source.repositoryRoot, "add", "."]);
    await execFileAsync("git", ["-C", source.repositoryRoot, "commit", "--quiet", "-m", "bearer token"]);
    const { stdout } = await execFileAsync("git", ["-C", source.repositoryRoot, "rev-parse", "HEAD"]);
    const lock = await importCatalog(source.pluginsRoot, { ...options(source), sourceCommit: stdout.trim() });
    const mcp = lock.entries[0]!.components.find(item => item.component.kind === "mcp")!;
    expect(mcp.component.metadata.credentialSlots).toEqual(["SAMPLE_TOKEN"]);
  });

  it("names a declared process env_vars reference as a credential slot", async () => {
    const source = await createSource("mcp-process-env-vars", [
      { directoryName: "alpha", license: "MIT", surfaces: ["mcp"] },
    ]);
    await writeFile(
      join(source.pluginsRoot, "alpha", ".mcp.json"),
      JSON.stringify({ mcpServers: { sample: { command: "node", args: ["server.mjs"], env_vars: ["MCP_TOKEN"] } } }),
    );
    await execFileAsync("git", ["-C", source.repositoryRoot, "add", "."]);
    await execFileAsync("git", ["-C", source.repositoryRoot, "commit", "--quiet", "-m", "process env_vars"]);
    const { stdout } = await execFileAsync("git", ["-C", source.repositoryRoot, "rev-parse", "HEAD"]);
    const lock = await importCatalog(source.pluginsRoot, { ...options(source), sourceCommit: stdout.trim() });
    const mcp = lock.entries[0]!.components.find(item => item.component.kind === "mcp")!;
    expect(mcp.component.metadata.credentialSlots).toEqual(["MCP_TOKEN"]);
  });

  it("names no credential slot for a process server declaring neither env_vars nor a bearer token", async () => {
    const source = await createSource("mcp-process-no-credentials", [
      { directoryName: "alpha", license: "MIT", surfaces: ["mcp"] },
    ]);
    await writeFile(
      join(source.pluginsRoot, "alpha", ".mcp.json"),
      JSON.stringify({ mcpServers: { sample: { command: "node", args: ["server.mjs"] } } }),
    );
    await execFileAsync("git", ["-C", source.repositoryRoot, "add", "."]);
    await execFileAsync("git", ["-C", source.repositoryRoot, "commit", "--quiet", "-m", "process no credentials"]);
    const { stdout } = await execFileAsync("git", ["-C", source.repositoryRoot, "rev-parse", "HEAD"]);
    const lock = await importCatalog(source.pluginsRoot, { ...options(source), sourceCommit: stdout.trim() });
    const mcp = lock.entries[0]!.components.find(item => item.component.kind === "mcp")!;
    expect(mcp.component.metadata.credentialSlots).toEqual([]);
  });

  it("fails closed when an expected catalog count differs", async () => {
    const source = await createSource("catalog-count", [
      { directoryName: "alpha", license: "MIT" },
    ]);

    await expect(importCatalog(source.pluginsRoot, {
      ...options(source),
      expectedPluginCount: 180,
    })).rejects.toThrow("source_mismatch:plugin_count");
  });

  it("binds required provenance to the catalog digest and stays reproducible across imports", async () => {
    const source = await createSource("catalog-digest", [
      { directoryName: "alpha", license: "MIT" },
    ]);
    const first = await importCatalog(source.pluginsRoot, options(source));
    const later = await importCatalog(source.pluginsRoot, {
      ...options(source),
      clock: (): Date => new Date("2026-08-25T00:00:00.000Z"),
    });

    // Import time is provenance metadata, not content. Binding it would make
    // the pinned catalog digest unreachable: no re-import of the pinned commit
    // could ever reproduce it, and every product path asserting the pin would
    // fail closed forever.
    expect(later.catalogDigest).toBe(first.catalogDigest);
    expect(later.importedAt).not.toBe(first.importedAt);
    // The provenance that identifies *what* was imported stays bound: a source
    // URL or commit that differs from the pin is refused by assertPinnedSource
    // before a digest is ever computed, and differing content changes the
    // digest (see "addresses the catalog by content, not by import time").
    await expect(importCatalog(source.pluginsRoot, {
      ...options(source),
      sourceUrl: "https://github.com/openai/plugins-mirror.git",
    })).rejects.toThrow("source_mismatch");
  });

  it("writes canonical JSON with a final newline", async () => {
    const source = await createSource("catalog-write", [
      { directoryName: "alpha", license: "MIT" },
    ]);
    const lock = await importCatalog(source.pluginsRoot, options(source));
    const output = join(await createOwnedTestRoot("catalog-output"), "catalog.lock.json");

    await writeCatalogLock(output, lock);
    const written = await readFile(output, "utf8");

    expect(written.endsWith("\n")).toBe(true);
    expect(JSON.parse(written)).toEqual(lock);
    expect(written).toBe(`${JSON.stringify(lock, null, 2)}\n`);
  });

  it("rejects a final-file symlink without changing its outside target", async () => {
    const source = await createSource("catalog-write-link", [
      { directoryName: "alpha", license: "MIT" },
    ]);
    const lock = await importCatalog(source.pluginsRoot, options(source));
    const outputRoot = await createOwnedTestRoot("catalog-link-output");
    const outsideRoot = await createOwnedTestRoot("catalog-link-outside");
    const outsideTarget = join(outsideRoot, "outside.json");
    const output = join(outputRoot, "catalog.lock.json");
    await writeFile(outsideTarget, "outside-sentinel\n");
    await symlink(outsideTarget, output, "file");

    await expect(writeCatalogLock(output, lock)).rejects.toThrow(
      "path_escape:catalog_output",
    );
    expect(await readFile(outsideTarget, "utf8")).toBe("outside-sentinel\n");
  });
});
