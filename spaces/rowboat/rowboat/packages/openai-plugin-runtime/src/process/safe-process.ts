import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { lstat, realpath } from "node:fs/promises";
import { dirname, isAbsolute, join, relative, resolve, sep } from "node:path";

import {
  assertDirectoryIdentity,
  type DirectoryIdentity,
} from "../import/directory-identity.js";
import {
  inspectDigestTree,
  type TreeInventory,
} from "../import/digest-service.js";

const ENVIRONMENT_NAME = /^[A-Z][A-Z0-9_]*$/;
export const DEFAULT_PROCESS_OUTPUT_LIMIT_BYTES = 64 * 1024;
const PROCESS_CLEANUP_GRACE_MS = 1_000;

export interface SafeSpawnOptions {
  readonly shell: false;
  readonly cwd: string;
  readonly env: Readonly<Record<string, string>>;
  readonly executionRootIdentity: DirectoryIdentity;
  readonly trustedStoreIdentity: DirectoryIdentity;
  readonly workingDirectoryIdentity: DirectoryIdentity;
  readonly componentDigest: string;
  readonly executionRootDigest: string;
  readonly executionRootInventory: TreeInventory;
  readonly signal: AbortSignal;
}

export interface SpawnCompletion {
  readonly exitCode: number | null;
  readonly signal: string | null;
}

export interface SpawnedProcess {
  readonly stdout: AsyncIterable<Uint8Array | string>;
  readonly stderr: AsyncIterable<Uint8Array | string>;
  readonly completion: Promise<SpawnCompletion>;
  writeStdin?(chunk: string): Promise<void>;
  closeStdin?(): Promise<void>;
  kill(signal?: "SIGTERM" | "SIGKILL"): void;
}

export interface ProcessSpawner {
  spawn(
    command: string,
    args: readonly string[],
    options: SafeSpawnOptions,
  ): SpawnedProcess | Promise<SpawnedProcess>;
}

export class NodeProcessSpawner implements ProcessSpawner {
  async spawn(command: string, args: readonly string[], options: SafeSpawnOptions): Promise<SpawnedProcess> {
    await assertSafeSpawnIdentity(options);
    // Portable Node has no handle-relative process spawn. The trusted parent
    // directory and scheduler gap between this check and spawn are the residual
    // OS boundary; callers must deny untrusted rename authority there.
    const child = spawn(command, [...args], {
      cwd: options.cwd,
      env: { ...options.env },
      shell: false,
      windowsHide: true,
      stdio: ["pipe", "pipe", "pipe"],
    });
    const completion = new Promise<SpawnCompletion>((resolveCompletion, rejectCompletion) => {
      child.once("error", () => rejectCompletion(new Error("process_spawn_failed")));
      child.once("close", (exitCode, signal) => {
        resolveCompletion(Object.freeze({
          exitCode,
          signal: signal === null ? null : String(signal),
        }));
      });
    });
    return Object.freeze({
      stdout: child.stdout,
      stderr: child.stderr,
      completion,
      writeStdin(chunk: string): Promise<void> {
        return new Promise((resolveWrite, rejectWrite) => {
          child.stdin.write(chunk, (error) => {
            if (error === null || error === undefined) resolveWrite();
            else rejectWrite(new Error("process_input_failed"));
          });
        });
      },
      closeStdin(): Promise<void> {
        return new Promise((resolveClose) => child.stdin.end(resolveClose));
      },
      kill(signal: "SIGTERM" | "SIGKILL" = "SIGTERM"): void {
        child.kill(signal);
      },
    });
  }
}

function isContained(root: string, candidate: string): boolean {
  const pointer = relative(root, candidate);
  return pointer !== ".." && !pointer.startsWith(`..${sep}`) && !isAbsolute(pointer);
}

export async function assertSafeSpawnIdentity(options: SafeSpawnOptions): Promise<void> {
  const root = options.executionRootIdentity;
  const store = options.trustedStoreIdentity;
  const workingDirectory = options.workingDirectoryIdentity;
  if (
    root === undefined
    || store === undefined
    || workingDirectory === undefined
    || options.componentDigest === undefined
    || options.executionRootDigest === undefined
    || !/^[a-f0-9]{64}$/u.test(options.componentDigest)
    || !/^[a-f0-9]{64}$/u.test(options.executionRootDigest)
    || !(options.signal instanceof AbortSignal)
    || options.signal.aborted
    || options.cwd !== workingDirectory.canonicalPath
    || dirname(root.canonicalPath) !== store.canonicalPath
    || !isContained(root.canonicalPath, workingDirectory.canonicalPath)
  ) {
    throw new Error("path_escape");
  }
  const expectedInventory = options.executionRootInventory;
  if (
    expectedInventory === undefined
    || !Number.isSafeInteger(expectedInventory.fileCount) || expectedInventory.fileCount < 0
    || !Number.isSafeInteger(expectedInventory.directoryCount) || expectedInventory.directoryCount < 0
    || !Number.isSafeInteger(expectedInventory.maxDepth) || expectedInventory.maxDepth < 0
    || !Number.isSafeInteger(expectedInventory.totalBytes) || expectedInventory.totalBytes < 0
  ) {
    throw new Error("path_escape");
  }
  await assertDirectoryIdentity(store);
  await assertDirectoryIdentity(root);
  await assertDirectoryIdentity(workingDirectory);
  const inspection = await inspectDigestTree(root.canonicalPath, {
    signal: options.signal,
    limits: {
      maxFiles: expectedInventory.fileCount,
      maxDirectories: expectedInventory.directoryCount,
      maxDepth: expectedInventory.maxDepth,
      maxBytes: expectedInventory.totalBytes,
    },
  });
  if (
    inspection.digest !== options.executionRootDigest
    || inspection.inventory.fileCount !== expectedInventory.fileCount
    || inspection.inventory.directoryCount !== expectedInventory.directoryCount
    || inspection.inventory.maxDepth !== expectedInventory.maxDepth
    || inspection.inventory.totalBytes !== expectedInventory.totalBytes
  ) {
    throw new Error("path_escape");
  }
  await assertDirectoryIdentity(store);
  await assertDirectoryIdentity(root);
  await assertDirectoryIdentity(workingDirectory);
}

export async function resolveSafeWorkingDirectory(
  pluginRoot: string,
  workingDirectory = ".",
): Promise<string> {
  if (isAbsolute(workingDirectory) || workingDirectory.includes("\0")) {
    throw new Error("path_escape");
  }
  const normalized = workingDirectory.replace(/\\/gu, "/");
  const segments = normalized.split("/").filter((segment) => segment !== "" && segment !== ".");
  if (segments.some((segment) => segment === "..")) throw new Error("path_escape");

  const canonicalRoot = await realpath(pluginRoot);
  let current = canonicalRoot;
  try {
    for (const segment of segments) {
      current = join(current, segment);
      const metadata = await lstat(current);
      if (metadata.isSymbolicLink()) throw new Error("path_escape");
    }
    const metadata = await lstat(current);
    if (!metadata.isDirectory() || metadata.isSymbolicLink()) throw new Error("path_escape");
    const canonicalCandidate = await realpath(resolve(canonicalRoot, ...segments));
    if (!isContained(canonicalRoot, canonicalCandidate)) throw new Error("path_escape");
    return canonicalCandidate;
  } catch (error: unknown) {
    if (error instanceof Error && error.message === "path_escape") throw error;
    throw new Error("path_escape");
  }
}

export function captureSafeEnvironment(
  baseline: Readonly<Record<string, string>>,
  resolved: Readonly<Record<string, string>>,
): Readonly<Record<string, string>> {
  const captured: Record<string, string> = Object.create(null) as Record<string, string>;
  for (const source of [baseline, resolved]) {
    for (const [name, value] of Object.entries(source)) {
      if (!ENVIRONMENT_NAME.test(name) || value.includes("\0")) {
        throw new Error("process_environment_invalid");
      }
      captured[name] = value;
    }
  }
  return Object.freeze(captured);
}

export interface CollectedOutput {
  readonly text: string;
  readonly truncated: boolean;
  readonly digest: string;
}

export async function collectBoundedOutput(
  stream: AsyncIterable<Uint8Array | string>,
  limit: number,
  secrets: readonly string[],
): Promise<CollectedOutput> {
  const hash = createHash("sha256");
  const chunks: Buffer[] = [];
  let capturedBytes = 0;
  let totalBytes = 0;
  for await (const chunk of stream) {
    const bytes = typeof chunk === "string" ? Buffer.from(chunk) : Buffer.from(chunk);
    hash.update(bytes);
    totalBytes += bytes.byteLength;
    if (capturedBytes < limit) {
      const bounded = bytes.subarray(0, Math.max(0, limit - capturedBytes));
      chunks.push(bounded);
      capturedBytes += bounded.byteLength;
    }
  }

  const truncated = totalBytes > limit;
  let text = Buffer.concat(chunks).toString("utf8");
  for (const secret of secrets) {
    text = text.replaceAll(secret, "[REDACTED]");
    if (truncated) {
      for (let length = Math.min(secret.length - 1, text.length); length > 0; length -= 1) {
        const prefix = secret.slice(0, length);
        if (text.endsWith(prefix)) {
          text = `${text.slice(0, -length)}[REDACTED]`;
          break;
        }
      }
    }
  }
  return Object.freeze({
    text,
    truncated,
    digest: hash.digest("hex"),
  });
}

export interface SafeProcessResult {
  readonly status: "success" | "failed" | "timed_out";
  readonly stdout?: CollectedOutput;
  readonly stderr?: CollectedOutput;
}

function delay(milliseconds: number): Promise<"timeout"> {
  return new Promise((resolveDelay) => {
    const timer = setTimeout(() => resolveDelay("timeout"), milliseconds);
    timer.unref?.();
  });
}

export async function terminateSpawnedProcess(spawned: SpawnedProcess): Promise<void> {
  try {
    spawned.kill("SIGTERM");
  } catch {
    return;
  }
  const settled = spawned.completion.then(() => true, () => true);
  if (await Promise.race([settled, delay(PROCESS_CLEANUP_GRACE_MS)]) === "timeout") {
    try {
      spawned.kill("SIGKILL");
    } catch {
      return;
    }
    await Promise.race([settled, delay(PROCESS_CLEANUP_GRACE_MS)]);
  }
}

export async function executeSafeProcess(options: {
  readonly spawner: ProcessSpawner;
  readonly command: string;
  readonly args: readonly string[];
  readonly spawnOptions: SafeSpawnOptions;
  readonly timeoutMilliseconds: number;
  readonly maxOutputBytes?: number;
  readonly secrets?: readonly string[];
}): Promise<SafeProcessResult> {
  const outputLimit = options.maxOutputBytes ?? DEFAULT_PROCESS_OUTPUT_LIMIT_BYTES;
  if (!Number.isSafeInteger(outputLimit) || outputLimit < 1) {
    throw new Error("process_output_limit_invalid");
  }
  let spawned: SpawnedProcess;
  try {
    spawned = await options.spawner.spawn(
      options.command,
      Object.freeze([...options.args]),
      options.spawnOptions,
    );
  } catch {
    return Object.freeze({ status: "failed" });
  }
  const execution = Promise.all([
    spawned.completion,
    collectBoundedOutput(spawned.stdout, outputLimit, options.secrets ?? []),
    collectBoundedOutput(spawned.stderr, outputLimit, options.secrets ?? []),
  ]);

  try {
    const outcome = await Promise.race([execution, delay(options.timeoutMilliseconds)]);
    if (outcome === "timeout") {
      await terminateSpawnedProcess(spawned);
      void execution.catch(() => undefined);
      return Object.freeze({ status: "timed_out" });
    }
    const [completion, stdout, stderr] = outcome;
    return Object.freeze({
      status: completion.exitCode === 0 ? "success" : "failed",
      stdout,
      stderr,
    });
  } catch {
    await terminateSpawnedProcess(spawned);
    return Object.freeze({ status: "failed" });
  }
}
