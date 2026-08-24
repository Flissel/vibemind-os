import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import {
  lstat,
  mkdir,
  readFile,
  readdir,
  realpath,
  rename,
  rm,
  symlink,
  stat,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { promisify } from "node:util";
import { afterEach, describe, expect, it } from "vitest";
import {
  assertPinnedSource,
  getVerifiedPluginDigests,
  getVerifiedPluginFileModes,
} from "../src/import/source-reader.js";
import {
  PluginSourceSecurityError,
  resolveContainedPath,
} from "../src/import/path-guard.js";
import { digestTree } from "../src/import/digest-service.js";
import { ContentStore } from "../src/store/content-store.js";
import { cleanupRegisteredTestRoots, createOwnedTestRoot } from "./test-temp.js";

const temporaryRoots = new Set<string>();

async function createTemporaryRoot(label: string): Promise<string> {
  const root = await createOwnedTestRoot(`source-security-${label}`);
  temporaryRoots.add(root);
  return root;
}

afterEach(async () => {
  temporaryRoots.clear();
  await cleanupRegisteredTestRoots();
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
  hash.update("rowboat-plugin-tree-v2\0", "utf8");

  for (const entry of [...entries].sort((left, right) =>
    left.path.localeCompare(right.path, "en"),
  )) {
    const normalizedPath = entry.path.replaceAll("\\", "/");
    const pathBytes = Buffer.from(normalizedPath, "utf8");
    hash.update(entry.type === "directory" ? Buffer.from([0x44]) : Buffer.from([0x46]));
    hash.update(uint32(pathBytes.length));
    hash.update(pathBytes);

    if (entry.type === "file") {
      hash.update("100644", "ascii");
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

  it("streams one file at a time using bounded read chunks", async () => {
    const root = await createTemporaryRoot("bounded-digest");
    const pluginRoot = join(root, "plugin");
    const firstBytes = Buffer.alloc(128 * 1024 + 7, 0x61);
    const secondBytes = Buffer.alloc(96 * 1024 + 3, 0x62);
    await mkdir(pluginRoot);
    await writeFile(join(pluginRoot, "a.bin"), firstBytes);
    await writeFile(join(pluginRoot, "b.bin"), secondBytes);

    let activeFiles = 0;
    let maximumActiveFiles = 0;
    let observedBytes = 0;
    let maximumChunk = 0;
    const digestOptions = {
      chunkSize: 31,
      fileObserver: {
        onFileOpen(): void {
          activeFiles += 1;
          maximumActiveFiles = Math.max(maximumActiveFiles, activeFiles);
        },
        onChunk(_candidate: string, bytesRead: number): void {
          observedBytes += bytesRead;
          maximumChunk = Math.max(maximumChunk, bytesRead);
        },
        onFileClose(): void {
          activeFiles -= 1;
        },
      },
    };

    await digestTree(pluginRoot, digestOptions);

    expect(maximumActiveFiles).toBe(1);
    expect(activeFiles).toBe(0);
    expect(maximumChunk).toBeLessThanOrEqual(31);
    expect(observedBytes).toBe(firstBytes.length + secondBytes.length);
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

const SOURCE_URL = "https://github.com/openai/plugins.git";
const execFileAsync = promisify(execFile);

async function git(repositoryRoot: string, ...args: readonly string[]): Promise<string> {
  const result = await execFileAsync("git", ["-C", repositoryRoot, ...args], {
    encoding: "utf8",
    windowsHide: true,
  });
  return result.stdout.trim();
}

async function createPinnedGitSource(label: string): Promise<{
  readonly repositoryRoot: string;
  readonly storeRoot: string;
  readonly commit: string;
}> {
  const root = await createTemporaryRoot(label);
  const repositoryRoot = join(root, "repository");
  const storeRoot = join(root, "store");
  await mkdir(repositoryRoot);
  await git(repositoryRoot, "init");
  await git(repositoryRoot, "config", "user.email", "tests@example.com");
  await git(repositoryRoot, "config", "user.name", "Tests");
  await git(repositoryRoot, "remote", "add", "origin", SOURCE_URL);
  await writeFile(join(repositoryRoot, ".gitignore"), "ignored.txt\n", "utf8");
  await writeFile(join(repositoryRoot, "tracked.txt"), "committed", "utf8");
  await git(repositoryRoot, "add", "--all");
  await git(repositoryRoot, "commit", "-m", "fixture");
  return { repositoryRoot, storeRoot, commit: await git(repositoryRoot, "rev-parse", "HEAD") };
}

describe("assertPinnedSource", () => {
  it("rejects a non-Git directory and malformed expected commit", async () => {
    const repositoryRoot = await createTemporaryRoot("not-git");
    await expectSecurityError(
      assertPinnedSource({
        repositoryRoot,
        expectedCommit: "0".repeat(40),
        sourceUrl: SOURCE_URL,
        storeRoot: join(dirname(repositoryRoot), "store"),
      }),
      "source_mismatch",
    );
  });

  it("accepts only the clean exact repository state", async () => {
    const fixture = await createPinnedGitSource("verified-pin");
    await expect(assertPinnedSource({
      repositoryRoot: fixture.repositoryRoot,
      expectedCommit: fixture.commit,
      sourceUrl: SOURCE_URL,
      storeRoot: fixture.storeRoot,
    })).resolves.toMatchObject({ sourceCommit: fixture.commit, sourceUrl: SOURCE_URL });
  });

  it.each([
    "https://user:token-secret@github.com/openai/plugins.git",
    "https://github.com/openai/plugins.git?token=token-secret",
    "https://github.com/openai/plugins.git#token-secret",
    "ssh://git@github.com/openai/plugins.git",
    "file:///openai/plugins.git",
    "https://example.com/openai/plugins.git",
  ])("rejects a non-canonical origin without leaking it: %s", async (sourceUrl) => {
    const fixture = await createPinnedGitSource("origin-contract");
    await git(fixture.repositoryRoot, "remote", "set-url", "origin", sourceUrl);

    const error = await expectSecurityError(assertPinnedSource({
      repositoryRoot: fixture.repositoryRoot,
      expectedCommit: fixture.commit,
      sourceUrl,
      storeRoot: fixture.storeRoot,
    }), "source_mismatch");
    expect(error.message).not.toContain("token-secret");
    expect(error.message).not.toContain(sourceUrl);
  });

  it("binds Git executable mode into snapshot digest and mode map", async () => {
    const fixture = await createPinnedGitSource("git-mode");
    const pluginRoot = join(fixture.repositoryRoot, "plugin");
    await mkdir(join(pluginRoot, ".codex-plugin"), { recursive: true });
    await writeFile(join(pluginRoot, ".codex-plugin", "plugin.json"), "{}", "utf8");
    await writeFile(join(pluginRoot, "run.sh"), "echo test\n", "utf8");
    await git(fixture.repositoryRoot, "add", "--all");
    await git(fixture.repositoryRoot, "commit", "-m", "plugin snapshot");
    const firstCommit = await git(fixture.repositoryRoot, "rev-parse", "HEAD");
    const firstContext = await assertPinnedSource({
      repositoryRoot: fixture.repositoryRoot,
      expectedCommit: firstCommit,
      sourceUrl: SOURCE_URL,
      storeRoot: fixture.storeRoot,
    });
    const first = await getVerifiedPluginDigests(firstContext, pluginRoot);

    await git(fixture.repositoryRoot, "update-index", "--chmod=+x", "plugin/run.sh");
    await git(fixture.repositoryRoot, "commit", "-m", "executable mode only");
    const secondCommit = await git(fixture.repositoryRoot, "rev-parse", "HEAD");
    const secondContext = await assertPinnedSource({
      repositoryRoot: fixture.repositoryRoot,
      expectedCommit: secondCommit,
      sourceUrl: SOURCE_URL,
      storeRoot: fixture.storeRoot,
    });
    const second = await getVerifiedPluginDigests(secondContext, pluginRoot);

    expect(second.treeDigest).not.toBe(first.treeDigest);
    await expect(readdir(fixture.storeRoot)).resolves.toEqual(expect.arrayContaining([
      first.treeDigest,
      second.treeDigest,
    ]));
    expect(await getVerifiedPluginFileModes(secondContext, pluginRoot))
      .toMatchObject({ "run.sh": "100755" });
    if (process.platform !== "win32") {
      expect((await stat(join(fixture.storeRoot, second.treeDigest, "run.sh"))).mode & 0o111)
        .not.toBe(0);
    }
  }, 30_000);

  it.each(["tracked blob", "ignored extra"] as const)("rejects %s changes", async (change) => {
    const fixture = await createPinnedGitSource(`changed-${change.replace(" ", "-")}`);
    await writeFile(
      join(fixture.repositoryRoot, change === "tracked blob" ? "tracked.txt" : "ignored.txt"),
      "changed",
      "utf8",
    );
    await expectSecurityError(assertPinnedSource({
      repositoryRoot: fixture.repositoryRoot,
      expectedCommit: fixture.commit,
      sourceUrl: SOURCE_URL,
      storeRoot: fixture.storeRoot,
    }), "source_mismatch");
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

  it("initializes a nested store root through multiple missing segments", async () => {
    const fixture = await createStoreFixture("store-nested-initialization");
    const storeRoot = join(fixture.root, "stores", "plugins", "content");
    const expectedDigest = await digestTree(fixture.pluginRoot);
    const store = new ContentStore({
      ...fixture,
      storeRoot,
    });

    const published = await store.put(fixture.pluginRoot, expectedDigest);

    expect(published).toStrictEqual({
      digest: expectedDigest,
      path: join(await realpath(storeRoot), expectedDigest),
    });
    await expect(digestTree(published.path)).resolves.toBe(expectedDigest);
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

  it("rejects an entry swapped to an outside link before copy without reading outside bytes", async () => {
    const fixture = await createStoreFixture("store-file-swap");
    const nestedRoot = join(fixture.pluginRoot, "nested");
    const displacedRoot = join(fixture.pluginRoot, "displaced-nested");
    const displacedPayload = join(fixture.pluginRoot, "displaced-payload.txt");
    const outsideRoot = await createTemporaryRoot("store-file-swap-outside");
    const sourcePayload = join(nestedRoot, "payload.txt");
    const outsidePayload = join(outsideRoot, "payload.txt");
    const trustedSource = "trusted source";
    await mkdir(nestedRoot);
    await writeFile(sourcePayload, trustedSource);
    await writeFile(outsidePayload, "outside secret");
    const expectedDigest = await digestTree(fixture.pluginRoot);
    let payloadOpenCount = 0;
    let observedPayloadBytes = 0;
    const storeConfig = {
      ...fixture,
      sourceFileReadOptions: {
        fileObserver: {
          async beforeOpen(candidate: string): Promise<void> {
            if (!candidate.endsWith(`${sep}nested${sep}payload.txt`)) {
              return;
            }

            payloadOpenCount += 1;
            if (payloadOpenCount === 2) {
              if (process.platform === "win32") {
                await rename(nestedRoot, displacedRoot);
                await createDirectoryLink(outsideRoot, nestedRoot);
              } else {
                await rename(sourcePayload, displacedPayload);
                await symlink(outsidePayload, sourcePayload, "file");
              }
            }
          },
          onChunk(candidate: string, bytesRead: number): void {
            if (candidate.endsWith(`${sep}nested${sep}payload.txt`)) {
              observedPayloadBytes += bytesRead;
            }
          },
        },
      },
    };
    const store = new ContentStore(storeConfig);

    await expectSecurityError(
      store.put(fixture.pluginRoot, expectedDigest),
      "path_escape",
    );
    expect(payloadOpenCount).toBe(2);
    expect(observedPayloadBytes).toBe(Buffer.byteLength(trustedSource));
    await expect(readFile(outsidePayload, "utf8")).resolves.toBe(
      "outside secret",
    );
    const storeEntries = await readdir(fixture.storeRoot);
    expect(storeEntries.some((entry) => entry === expectedDigest)).toBe(false);
    expect(
      storeEntries.some((entry) => entry === `.complete-${expectedDigest}`),
    ).toBe(false);
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

  it("fails before writing when the store root is replaced before reservation", async () => {
    const fixture = await createStoreFixture("store-root-write-swap");
    const outsideRoot = await createTemporaryRoot("store-root-write-outside");
    const displacedStore = join(fixture.root, "displaced-write-store");
    await writeFile(join(outsideRoot, "sentinel.txt"), "outside sentinel");
    const expectedDigest = await digestTree(fixture.pluginRoot);
    let observerCalled = false;
    const storeConfig = {
      ...fixture,
      publicationObserver: {
        async beforeReserve(): Promise<void> {
          observerCalled = true;
          await rename(fixture.storeRoot, displacedStore);
          await createDirectoryLink(outsideRoot, fixture.storeRoot);
        },
      },
    };

    await expectSecurityError(
      new ContentStore(storeConfig).put(fixture.pluginRoot, expectedDigest),
      "path_escape",
    );
    expect(observerCalled).toBe(true);
    await expect(readdir(outsideRoot)).resolves.toStrictEqual([
      ".rowboat-openai-plugin-runtime-test-temp",
      "sentinel.txt",
    ]);
    expect(
      (await readdir(displacedStore)).some(
        (entry) => entry.startsWith(".tmp-") || entry.startsWith(".lock-"),
      ),
    ).toBe(true);
  });

  it("skips cleanup when the store root is replaced and preserves an outside sentinel", async () => {
    const fixture = await createStoreFixture("store-root-cleanup-swap");
    const outsideRoot = await createTemporaryRoot("store-root-cleanup-outside");
    const displacedStore = join(fixture.root, "displaced-cleanup-store");
    const expectedDigest = await digestTree(fixture.pluginRoot);
    let displacedTemporaryDirectory: string | undefined;
    let outsideSentinel: string | undefined;
    const storeConfig = {
      ...fixture,
      publicationObserver: {
        async beforeTemporaryCleanup(
          storeRoot: string,
          temporaryDirectory: string,
        ): Promise<void> {
          const temporaryName = temporaryDirectory.slice(storeRoot.length + 1);
          displacedTemporaryDirectory = join(displacedStore, temporaryName);
          outsideSentinel = join(outsideRoot, temporaryName, "sentinel.txt");
          await rename(fixture.storeRoot, displacedStore);
          await mkdir(dirname(outsideSentinel), { recursive: true });
          await writeFile(outsideSentinel, "outside sentinel");
          await createDirectoryLink(outsideRoot, fixture.storeRoot);
        },
      },
    };

    await expectSecurityError(
      new ContentStore(storeConfig).put(fixture.pluginRoot, expectedDigest),
      "path_escape",
    );
    await expect(readFile(outsideSentinel ?? "", "utf8")).resolves.toBe(
      "outside sentinel",
    );
    await expect(lstat(displacedTemporaryDirectory ?? "")).resolves.toMatchObject({
      isDirectory: expect.any(Function),
    });
    expect((await lstat(displacedTemporaryDirectory ?? "")).isDirectory()).toBe(
      true,
    );
  });
});
