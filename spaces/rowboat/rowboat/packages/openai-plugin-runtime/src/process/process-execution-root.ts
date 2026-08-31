import {
  assertDirectoryIdentity,
  type DirectoryIdentity,
} from "../import/directory-identity.js";
import type { TreeInventory } from "../import/digest-service.js";
import { revealStoredPluginContent } from "../store/content-store.js";

const COMPONENT_DIGEST = /^[a-f0-9]{64}$/;
const verifiedRoots = new WeakMap<VerifiedProcessExecutionRoot, ProcessExecutionRootDetails>();

interface ProcessExecutionRootDetails {
  readonly path: string;
  readonly componentDigest: string;
  readonly executionRootDigest: string;
  readonly identity: DirectoryIdentity;
  readonly trustedStoreIdentity: DirectoryIdentity;
  readonly inventory: TreeInventory;
}

export class VerifiedProcessExecutionRoot {
  private constructor() {
    Object.freeze(this);
  }

  static async create(
    content: unknown,
    componentDigest: string,
  ): Promise<VerifiedProcessExecutionRoot> {
    const stored = revealStoredPluginContent(content);
    if (!COMPONENT_DIGEST.test(stored.digest) || !COMPONENT_DIGEST.test(componentDigest)) {
      throw new Error("provider_invalid:execution_root");
    }
    await assertDirectoryIdentity(stored.trustedStoreIdentity);
    await assertDirectoryIdentity(stored.identity);
    const verified = new VerifiedProcessExecutionRoot();
    verifiedRoots.set(verified, Object.freeze({
      path: stored.path,
      componentDigest,
      executionRootDigest: stored.digest,
      identity: stored.identity,
      trustedStoreIdentity: stored.trustedStoreIdentity,
      inventory: stored.inventory,
    }));
    return verified;
  }
}

export function verifyProcessExecutionRoot(
  content: unknown,
  componentDigest: string,
): Promise<VerifiedProcessExecutionRoot> {
  return VerifiedProcessExecutionRoot.create(content, componentDigest);
}

export function revealVerifiedProcessExecutionRoot(
  root: VerifiedProcessExecutionRoot,
): ProcessExecutionRootDetails {
  const details = verifiedRoots.get(root);
  if (details === undefined) throw new Error("provider_invalid:execution_root");
  return details;
}

export async function assertVerifiedProcessExecutionRoot(
  root: VerifiedProcessExecutionRoot,
): Promise<void> {
  const details = revealVerifiedProcessExecutionRoot(root);
  await assertDirectoryIdentity(details.trustedStoreIdentity);
  await assertDirectoryIdentity(details.identity);
}
