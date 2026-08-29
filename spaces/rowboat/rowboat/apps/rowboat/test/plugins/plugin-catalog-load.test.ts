import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { afterAll, describe, expect, it } from "vitest";
import { PINNED_PLUGIN_CATALOG_DIGEST } from "@rowboat/openai-plugin-runtime";
import { catalogLoadReport, parseCatalogLoadArguments, readCatalogLock } from "@/scripts/load-plugin-catalog";

const REPOSITORY_ROOT = resolve(process.cwd(), "..", "..");
const temporaryRoots: string[] = [];

afterAll(async () => {
  for (const root of temporaryRoots) await rm(root, { recursive: true, force: true });
});

async function temporaryLock(content: string): Promise<string> {
  const root = await mkdtemp(join(tmpdir(), "rowboat-catalog-load-"));
  temporaryRoots.push(root);
  const path = join(root, "lock.json");
  await writeFile(path, content, "utf8");
  return path;
}

describe("plugin catalog load", () => {
  it("defaults to the committed lock and accepts only the documented flags", () => {
    expect(parseCatalogLoadArguments([])).toEqual({ lock: "config/openai-plugin-catalog.lock.json", verifyOnly: false });
    expect(parseCatalogLoadArguments(["--verify-only"])).toMatchObject({ verifyOnly: true });
    expect(parseCatalogLoadArguments(["--lock", "config/other.lock.json"])).toMatchObject({ lock: "config/other.lock.json" });
    for (const argv of [["--lock"], ["--lock", "--verify-only"], ["--unknown"], ["--lock", "a", "--lock", "b"], ["--verify-only", "--verify-only"], ["--lock", "-"]]) {
      expect(() => parseCatalogLoadArguments(argv)).toThrow("catalog_load_invalid");
    }
  });

  it("reads the committed lock and binds it to the pinned digest", async () => {
    const lock = await readCatalogLock("config/openai-plugin-catalog.lock.json", REPOSITORY_ROOT);
    expect(lock.catalogDigest).toBe(PINNED_PLUGIN_CATALOG_DIGEST);
    expect(lock.entries).toHaveLength(180);
    expect(catalogLoadReport(lock, null)).toMatchObject({ entries: 180, alreadyStored: false, catalogDigest: PINNED_PLUGIN_CATALOG_DIGEST });
    expect(catalogLoadReport(lock, lock)).toMatchObject({ alreadyStored: true });
    // The report is provenance only: no plugin content, no credential material.
    expect(JSON.stringify(catalogLoadReport(lock, null))).not.toContain("components");
  });

  it("refuses a malformed, foreign, or unpinned lock instead of loading it", async () => {
    await expect(readCatalogLock(await temporaryLock("{"), REPOSITORY_ROOT)).rejects.toThrow();
    await expect(readCatalogLock(await temporaryLock("{}"), REPOSITORY_ROOT)).rejects.toThrow();
    await expect(readCatalogLock(join(REPOSITORY_ROOT, "config", "does-not-exist.json"), REPOSITORY_ROOT)).rejects.toThrow();

    const authentic = await readCatalogLock("config/openai-plugin-catalog.lock.json", REPOSITORY_ROOT);
    const tampered = { ...authentic, catalogDigest: "f".repeat(64) };
    await expect(readCatalogLock(await temporaryLock(JSON.stringify(tampered)), REPOSITORY_ROOT)).rejects.toThrow();
  });
});
