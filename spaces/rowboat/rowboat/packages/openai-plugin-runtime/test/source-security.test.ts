import { createHash } from "node:crypto";
import {
  lstat,
  mkdir,
  mkdtemp,
  readFile,
  readdir,
  realpath,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { isAbsolute, join, relative, resolve, sep } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import {
  assertPinnedSource,
  type GitProbe,
  type GitProbeCommand,
} from "../src/import/source-reader.js";
import {
  PluginSourceSecurityError,
  resolveContainedPath,
} from "../src/import/path-guard.js";
import { digestTree } from "../src/import/digest-service.js";
import { ContentStore } from "../src/store/content-store.js";

const temporaryRoots = new Set<string>();

async function createTemporaryRoot(label: string): Promise<string> {
  const root = await mkdtemp(join(tmpdir(), `rowboat-source-security-${label}-`));
  temporaryRoots.add(root);
  return root;
}

afterEach(async () => {
  await Promise.all(
    [...temporaryRoots].map(async (root) => {
      temporaryRoots.delete(root);
      await rm(root, { recursive: true, force: true });
    }),
  );
});

async function expectSecurityError(
  operation: Promise<unknown>,
  code: PluginSourceSecurityError["code"],
): Promise<PluginSourceSecurityError> {
  try {
    await operation;
  } catch (error: unknown) {
    expect(error).toBeInstanceOf(PluginSourceSecurityError);
    const securityError = error as PluginSourceSecurityError;
    expect(securityError.code).toBe(code);
    expect(securityError.message.startsWith(`${code}:`)).toBe(true);
    return securityError;
  }

  throw new Error(`Expected ${code} security error.`);
}

async function createDirectoryLink(
  target: string,
  linkPath: string,
): Promise<void> {
  try {
    await symlink(target, linkPath, process.platform === "win32" ? "junction" : "dir");
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : String(error);
    throw new Error(
      `The environment cannot create a directory symlink or junction for the realpath escape test: ${message}`,
    );
  }
}

type ReferenceEntry =
  | { readonly type: "directory"; readonly path: string }
  | { readonly type: "file"; readonly path: string; readonly bytes: Buffer };

function uint32(value: number): Buffer {
  const framed = Buffer.alloc(4);
  framed.writeUInt32BE(value);
  return framed;
}

function uint64(value: number): Buffer {
  const framed = Buffer.alloc(8);
  framed.writeBigUInt64BE(BigInt(value));
  return framed;
}

function referenceDigest(entries: readonly ReferenceEntry[]): string {
  const hash = createHash("sha256");
  hash.update("rowboat-plugin-tree-v1\0", "utf8");

  for (const entry of [...entries].sort((left, right) =>
    left.path.localeCompare(right.path, "en"),
  )) {
    const normalizedPath = entry.path.replaceAll("\\", "/");
    const pathBytes = Buffer.from(normalizedPath, "utf8");
    hash.update(entry.type === "directory" ? Buffer.from([0x44]) : Buffer.from([0x46]));
    hash.update(uint32(pathBytes.length));
    hash.update(pathBytes);

    if (entry.type === "file") {
      hash.update(uint64(entry.bytes.length));
      hash.update(entry.bytes);
    }
  }

  return hash.digest("hex");
}

describe("resolveContainedPath", () => {
  it("rejects traversal outside the root with a stable path_escape reason", async () => {
    const root = await createTemporaryRoot("traversal");
    const pluginRoot = join(root, "plugin");
    await mkdir(pluginRoot);
    await writeFile(join(root, "secret.txt"), "outside");

    await expectSecurityError(
      resolveContainedPath(pluginRoot, "../secret.txt"),
      "path_escape",
    );
  });

  it("rejects a nonexistent lexical escape before candidate realpath", async () => {
    const root = await createTemporaryRoot("missing-traversal");
    const pluginRoot = join(root, "plugin");
    await mkdir(pluginRoot);

    await expectSecurityError(
      resolveContainedPath(pluginRoot, "../missing.txt"),
      "path_escape",
    );
  });

  it("rejects an absolute pointer before resolving the filesystem root", async () => {
    const root = join(await createTemporaryRoot("absolute"), "missing-root");

    await expectSecurityError(
      resolveContainedPath(root, resolve(tmpdir(), "absolute-pointer")),
      "path_escape",
    );
  });

  it("rejects a link inside the plugin root that resolves outside", async () => {
    const root = await createTemporaryRoot("link-escape");
    const pluginRoot = join(root, "plugin");
    const outsideRoot = join(root, "outside");
    await mkdir(pluginRoot);
    await mkdir(outsideRoot);
    await writeFile(join(outsideRoot, "secret.txt"), "outside");
    await createDirectoryLink(outsideRoot, join(pluginRoot, "linked"));

    await expectSecurityError(
      resolveContainedPath(pluginRoot, join("linked", "secret.txt")),
      "path_escape",
    );
  });

  it("returns real paths for contained regular files and directories", async () => {
    const root = await createTemporaryRoot("contained");
    const pluginRoot = join(root, "plugin");
    const directory = join(pluginRoot, "nested");
    const file = join(directory, "plugin.txt");
    await mkdir(directory, { recursive: true });
    await writeFile(file, "contained");

    await expect(resolveContainedPath(pluginRoot, "nested")).resolves.toBe(
      await realpath(directory),
    );
    await expect(
      resolveContainedPath(pluginRoot, join("nested", "plugin.txt")),
    ).resolves.toBe(await realpath(file));
  });
});

describe("digestTree", () => {
  it("is deterministic across creation order and includes empty directories", async () => {
    const root = await createTemporaryRoot("deterministic");
    const first = join(root, "first");
    const second = join(root, "second");
    await mkdir(first);
    await mkdir(second);

    await mkdir(join(first, "z-empty"));
    await writeFile(join(first, "b.txt"), "bravo");
    await mkdir(join(first, "nested"));
    await writeFile(join(first, "nested", "a.txt"), "alpha");

    await mkdir(join(second, "nested"));
    await writeFile(join(second, "nested", "a.txt"), "alpha");
    await writeFile(join(second, "b.txt"), "bravo");
    await mkdir(join(second, "z-empty"));

    const firstDigest = await digestTree(first);
    const secondDigest = await digestTree(second);
    expect(firstDigest).toBe(secondDigest);
    expect(firstDigest).toMatch(/^[a-f0-9]{64}$/);

    await mkdir(join(second, "another-empty"));
    await expect(digestTree(second)).resolves.not.toBe(firstDigest);
  });

  it("normalizes nested relative separators to forward slashes", async () => {
    const root = await createTemporaryRoot("separators");
    const pluginRoot = join(root, "plugin");
    const bytes = Buffer.from("portable", "utf8");
    await mkdir(join(pluginRoot, "nested"), { recursive: true });
    await writeFile(join(pluginRoot, "nested", "file.txt"), bytes);

    await expect(digestTree(pluginRoot)).resolves.toBe(
      referenceDigest([
        { type: "directory", path: "nested" },
        { type: "file", path: "nested/file.txt", bytes },
      ]),
    );
  });

  it("changes when a relative path or file bytes change", async () => {
    const root = await createTemporaryRoot("digest-change");
    const first = join(root, "first");
    const renamed = join(root, "renamed");
    const changed = join(root, "changed");
    await mkdir(first);
    await mkdir(renamed);
    await mkdir(changed);
    await writeFile(join(first, "a.txt"), "same");
    await writeFile(join(renamed, "b.txt"), "same");
    await writeFile(join(changed, "a.txt"), "different");

    const originalDigest = await digestTree(first);
    await expect(digestTree(renamed)).resolves.not.toBe(originalDigest);
    await expect(digestTree(changed)).resolves.not.toBe(originalDigest);
  });

  it("rejects symbolic links instead of following them", async () => {
    const root = await createTemporaryRoot("digest-link");
    const pluginRoot = join(root, "plugin");
    const outsideRoot = join(root, "outside");
    await mkdir(pluginRoot);
    await mkdir(outsideRoot);
    await writeFile(join(outsideRoot, "secret.txt"), "outside");
    await createDirectoryLink(outsideRoot, join(pluginRoot, "linked"));

    await expectSecurityError(digestTree(pluginRoot), "path_escape");
  });
});

const PINNED_COMMIT = "0123456789abcdef0123456789abcdef01234567";

class RecordingGitProbe implements GitProbe {
  readonly calls: Array<{
    readonly repositoryRoot: string;
    readonly command: GitProbeCommand;
  }> = [];

  constructor(
    private readonly headOutput: string,
    private readonly statusOutput: string,
  ) {}

  async run(repositoryRoot: string, command: GitProbeCommand): Promise<string> {
    this.calls.push({ repositoryRoot, command });
    return command[0] === "rev-parse" ? this.headOutput : this.statusOutput;
  }
}

describe("assertPinnedSource", () => {
  it("rejects a mismatched HEAD after only the exact read-only HEAD probe", async () => {
    const repositoryRoot = resolve("repository");
    const probe = new RecordingGitProbe(
      "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n",
      "",
    );

    await expectSecurityError(
      assertPinnedSource({ repositoryRoot, expectedCommit: PINNED_COMMIT, probe }),
      "source_mismatch",
    );
    expect(probe.calls).toStrictEqual([
      { repositoryRoot, command: ["rev-parse", "HEAD"] },
    ]);
  });

  it("rejects a dirty status after exactly the two read-only probes", async () => {
    const repositoryRoot = resolve("repository");
    const probe = new RecordingGitProbe(`${PINNED_COMMIT}\n`, " M secret.txt\n");

    await expectSecurityError(
      assertPinnedSource({ repositoryRoot, expectedCommit: PINNED_COMMIT, probe }),
      "source_mismatch",
    );
    expect(probe.calls).toStrictEqual([
      { repositoryRoot, command: ["rev-parse", "HEAD"] },
      { repositoryRoot, command: ["status", "--porcelain"] },
    ]);
  });

  it("accepts a clean exact pin and rejects malformed pins and probe output", async () => {
    const repositoryRoot = resolve("repository");
    const cleanProbe = new RecordingGitProbe(` ${PINNED_COMMIT}\n`, "\n");

    await expect(
      assertPinnedSource({
        repositoryRoot,
        expectedCommit: PINNED_COMMIT,
        probe: cleanProbe,
      }),
    ).resolves.toBeUndefined();
    expect(cleanProbe.calls).toHaveLength(2);

    const invalidExpectedProbe = new RecordingGitProbe(PINNED_COMMIT, "");
    await expectSecurityError(
      assertPinnedSource({
        repositoryRoot,
        expectedCommit: PINNED_COMMIT.toUpperCase(),
        probe: invalidExpectedProbe,
      }),
      "source_mismatch",
    );
    expect(invalidExpectedProbe.calls).toStrictEqual([]);

    const malformedOutputProbe = new RecordingGitProbe(
      `${PINNED_COMMIT}\n${PINNED_COMMIT}`,
      "",
    );
    await expectSecurityError(
      assertPinnedSource({
        repositoryRoot,
        expectedCommit: PINNED_COMMIT,
        probe: malformedOutputProbe,
      }),
      "source_mismatch",
    );
  });
});

async function createStoreFixture(label: string): Promise<{
  readonly root: string;
  readonly repositoryRoot: string;
  readonly pluginRoot: string;
  readonly storeRoot: string;
}> {
  const root = await createTemporaryRoot(label);
  const repositoryRoot = join(root, "repository");
  const pluginRoot = join(repositoryRoot, "plugin");
  const storeRoot = join(root, "content-store");
  await mkdir(join(pluginRoot, "empty"), { recursive: true });
  await writeFile(join(pluginRoot, "plugin.txt"), "immutable source");
  return { root, repositoryRoot, pluginRoot, storeRoot };
}

describe("ContentStore", () => {
  it("requires an absolute, non-overlapping store root", async () => {
    const fixture = await createStoreFixture("store-config");

    expect(
      () =>
        new ContentStore({
          storeRoot: "relative-store",
          repositoryRoot: fixture.repositoryRoot,
        }),
    ).toThrow("path_escape:");
    expect(
      () =>
        new ContentStore({
          storeRoot: fixture.repositoryRoot,
          repositoryRoot: fixture.repositoryRoot,
        }),
    ).toThrow("path_escape:");
    expect(
      () =>
        new ContentStore({
          storeRoot: join(fixture.repositoryRoot, "store"),
          repositoryRoot: fixture.repositoryRoot,
        }),
    ).toThrow("path_escape:");
    expect(
      () =>
        new ContentStore({
          storeRoot: fixture.root,
          repositoryRoot: fixture.repositoryRoot,
        }),
    ).toThrow("path_escape:");
  });

  it("rejects a digest mismatch before publishing content", async () => {
    const fixture = await createStoreFixture("store-mismatch");
    const store = new ContentStore(fixture);

    await expectSecurityError(
      store.put(fixture.pluginRoot, "0".repeat(64)),
      "digest_mismatch",
    );
    await expect(readdir(fixture.storeRoot)).resolves.toStrictEqual([]);
  });

  it("publishes only regular contained content to an outside digest path", async () => {
    const fixture = await createStoreFixture("store-publish");
    const store = new ContentStore(fixture);
    const expectedDigest = await digestTree(fixture.pluginRoot);

    const published = await store.put(fixture.pluginRoot, expectedDigest);
    const publishedPath = published.path;
    const canonicalRepository = await realpath(fixture.repositoryRoot);
    const repositoryRelative = relative(canonicalRepository, publishedPath);

    expect(published).toStrictEqual({ digest: expectedDigest, path: publishedPath });
    expect(isAbsolute(publishedPath)).toBe(true);
    expect(repositoryRelative === ".." || repositoryRelative.startsWith(`..${sep}`)).toBe(
      true,
    );
    expect(publishedPath).toBe(join(await realpath(fixture.storeRoot), expectedDigest));
    await expect(readFile(join(publishedPath, "plugin.txt"), "utf8")).resolves.toBe(
      "immutable source",
    );
    await expect(lstat(join(publishedPath, "empty"))).resolves.toMatchObject({
      isDirectory: expect.any(Function),
    });
    expect((await lstat(join(publishedPath, "empty"))).isDirectory()).toBe(true);
    await expect(digestTree(publishedPath)).resolves.toBe(expectedDigest);
  });

  it("is idempotent without overwriting an existing digest directory", async () => {
    const fixture = await createStoreFixture("store-idempotent");
    const store = new ContentStore(fixture);
    const expectedDigest = await digestTree(fixture.pluginRoot);

    const first = await store.put(fixture.pluginRoot, expectedDigest);
    const second = await store.put(fixture.pluginRoot, expectedDigest);
    expect(second).toStrictEqual(first);

    await writeFile(join(first.path, "plugin.txt"), "tampered store");
    await expectSecurityError(
      store.put(fixture.pluginRoot, expectedDigest),
      "digest_mismatch",
    );
    await expect(readFile(join(first.path, "plugin.txt"), "utf8")).resolves.toBe(
      "tampered store",
    );
  });

  it("rejects changed source content when given its old digest", async () => {
    const fixture = await createStoreFixture("store-source-change");
    const store = new ContentStore(fixture);
    const oldDigest = await digestTree(fixture.pluginRoot);
    const published = await store.put(fixture.pluginRoot, oldDigest);
    await writeFile(join(fixture.pluginRoot, "plugin.txt"), "changed source");

    await expectSecurityError(
      store.put(fixture.pluginRoot, oldDigest),
      "digest_mismatch",
    );
    await expect(readFile(join(published.path, "plugin.txt"), "utf8")).resolves.toBe(
      "immutable source",
    );
  });

  it("rejects symlinked source entries without publishing them", async () => {
    const fixture = await createStoreFixture("store-link");
    const outsideRoot = join(fixture.root, "outside");
    await mkdir(outsideRoot);
    await writeFile(join(outsideRoot, "secret.txt"), "outside");
    await createDirectoryLink(outsideRoot, join(fixture.pluginRoot, "linked"));
    const store = new ContentStore(fixture);

    await expectSecurityError(store.put(fixture.pluginRoot, "0".repeat(64)), "path_escape");
    await expect(readdir(fixture.storeRoot)).resolves.toStrictEqual([]);
  });

  it("converges concurrent publishes on the same immutable digest path", async () => {
    const fixture = await createStoreFixture("store-race");
    const store = new ContentStore(fixture);
    const expectedDigest = await digestTree(fixture.pluginRoot);

    const publishedEntries = await Promise.all([
      store.put(fixture.pluginRoot, expectedDigest),
      store.put(fixture.pluginRoot, expectedDigest),
    ]);

    expect(publishedEntries[0]).toStrictEqual(publishedEntries[1]);
    await expect(digestTree(publishedEntries[0]?.path ?? "")).resolves.toBe(
      expectedDigest,
    );
    const storeEntries = await readdir(fixture.storeRoot);
    expect(storeEntries).toContain(expectedDigest);
    expect(
      storeEntries.some(
        (entry) => entry.startsWith(".tmp-") || entry.startsWith(".lock-"),
      ),
    ).toBe(false);
  });

  it("waits for a cooperating writer with an incomplete reserved destination", async () => {
    const fixture = await createStoreFixture("store-incomplete-writer");
    const expectedDigest = await digestTree(fixture.pluginRoot);
    let announceReservation: (() => void) | undefined;
    let releaseReservation: (() => void) | undefined;
    const reservationObserved = new Promise<void>((resolveReservation) => {
      announceReservation = resolveReservation;
    });
    const reservationRelease = new Promise<void>((resolveRelease) => {
      releaseReservation = resolveRelease;
    });
    const firstConfig = {
      ...fixture,
      publicationObserver: {
        async afterReserve(): Promise<void> {
          announceReservation?.();
          await reservationRelease;
        },
      },
    };
    const firstPublication = new ContentStore(firstConfig).put(
      fixture.pluginRoot,
      expectedDigest,
    );

    await Promise.race([
      reservationObserved,
      firstPublication.then(() => {
        throw new Error("First publication completed before reservation pause.");
      }),
    ]);

    let observedState: "absent" | "complete" | "invalid" | undefined;
    let announceInspection: (() => void) | undefined;
    const inspectionObserved = new Promise<void>((resolveInspection) => {
      announceInspection = resolveInspection;
    });
    const secondConfig = {
      ...fixture,
      publicationObserver: {
        async afterInitialInspect(
          state: "absent" | "complete" | "invalid",
        ): Promise<void> {
          observedState = state;
          announceInspection?.();
        },
      },
    };
    const secondPublication = new ContentStore(secondConfig).put(
      fixture.pluginRoot,
      expectedDigest,
    );

    try {
      await Promise.race([
        inspectionObserved,
        secondPublication.then(() => {
          throw new Error(
            "Second publication completed before observing reservation.",
          );
        }),
      ]);
      expect(observedState).toBe("invalid");
    } finally {
      releaseReservation?.();
    }

    const [first, second] = await Promise.all([
      firstPublication,
      secondPublication,
    ]);
    expect(second).toStrictEqual(first);
    await expect(digestTree(first.path)).resolves.toBe(expectedDigest);
  });

  it("never overwrites an empty destination created immediately before reservation", async () => {
    const fixture = await createStoreFixture("store-no-replace-race");
    const expectedDigest = await digestTree(fixture.pluginRoot);
    let racedDestination: string | undefined;
    const raceConfig = {
      ...fixture,
      publicationObserver: {
        async beforeReserve(destination: string): Promise<void> {
          racedDestination = destination;
          await mkdir(destination);
        },
      },
    };
    const store = new ContentStore(raceConfig);

    await expectSecurityError(
      store.put(fixture.pluginRoot, expectedDigest),
      "digest_mismatch",
    );

    expect(racedDestination).toBe(join(await realpath(fixture.storeRoot), expectedDigest));
    await expect(readdir(racedDestination ?? "")).resolves.toStrictEqual([]);
    const storeEntries = await readdir(fixture.storeRoot);
    expect(storeEntries).toContain(expectedDigest);
    expect(
      storeEntries.some(
        (entry) => entry.startsWith(".tmp-") || entry.startsWith(".lock-"),
      ),
    ).toBe(false);
  });
});
