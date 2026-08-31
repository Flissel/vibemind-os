import { mkdir } from "node:fs/promises";
import { join } from "node:path";
import {
  createOwnedTempRoot,
  recoverStaleOwnedTempRoots,
  removeOwnedTempRoot,
  type OwnedTempRecoveryResult,
} from "../src/import/snapshot-temp.js";

interface RegisteredRoot {
  readonly prefix: string;
  readonly kind: string;
}

const registeredRoots = new Map<string, RegisteredRoot>();

function namespace(label: string): RegisteredRoot {
  if (!/^[a-z0-9-]+$/.test(label)) throw new Error("invalid test temp label");
  return {
    prefix: `rowboat-openai-plugin-runtime-test-${label}-`,
    kind: "test-root",
  };
}

export async function createOwnedTestRoot(label: string): Promise<string> {
  const owned = namespace(label);
  await recoverStaleOwnedTempRoots("rowboat-openai-plugin-runtime-test-", "test-root");
  const root = await createOwnedTempRoot(owned.prefix, owned.kind);
  registeredRoots.set(root, owned);
  return root;
}

export async function cleanupOwnedTestRoot(root: string): Promise<void> {
  const owned = registeredRoots.get(root);
  if (owned === undefined) throw new Error("test temp root is not registered");
  await removeOwnedTempRoot(root, owned.prefix, owned.kind);
  registeredRoots.delete(root);
}

export async function cleanupRegisteredTestRoots(): Promise<void> {
  const roots = [...registeredRoots.keys()];
  const results = await Promise.allSettled(roots.map(cleanupOwnedTestRoot));
  const failure = results.find((result) => result.status === "rejected");
  if (failure?.status === "rejected") throw failure.reason;
}

export function recoverOwnedTestRoots(label: string): Promise<OwnedTempRecoveryResult> {
  const owned = namespace(label);
  return recoverStaleOwnedTempRoots(owned.prefix, owned.kind);
}

export async function createOwnedTestDirectory(label: string, child: string): Promise<string> {
  const root = await createOwnedTestRoot(label);
  const directory = join(root, child);
  await mkdir(directory, { recursive: true });
  return directory;
}
