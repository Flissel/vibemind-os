import { isAbsolute } from "node:path";

import {
  assertDirectoryIdentity,
  snapshotDirectoryIdentity,
  type DirectoryIdentity,
} from "../import/directory-identity.js";
import { digestTree } from "../import/digest-service.js";
import { PluginSourceSecurityError } from "../import/path-guard.js";
import type { StoredPluginContent } from "../store/content-store.js";

const COMPONENT_DIGEST = /^[a-f0-9]{64}$/;
const verifiedRoots = new WeakMap<VerifiedProcessExecutionRoot, ProcessExecutionRootDetails>();

interface ProcessExecutionRootDetails {
  readonly path: string;
  readonly componentDigest: string;
  readonly executionRootDigest: string;
  readonly identity: DirectoryIdentity;
}

export class VerifiedProcessExecutionRoot {
  private constructor() {
    Object.freeze(this);
  }

  static async create(
    content: StoredPluginContent,
    componentDigest: string,
  ): Promise<VerifiedProcessExecutionRoot> {
    if (typeof content !== "object" || content === null) {
      throw new Error("provider_invalid:execution_root");
    }
    const prototype = Object.getPrototypeOf(content);
    const descriptors = Object.getOwnPropertyDescriptors(content);
    const pathDescriptor = descriptors.path;
    const digestDescriptor = descriptors.digest;
    if (
      (prototype !== Object.prototype && prototype !== null)
      || Object.getOwnPropertySymbols(content).length !== 0
      || Object.keys(descriptors).length !== 2
      || pathDescriptor === undefined
      || pathDescriptor.get !== undefined
      || pathDescriptor.set !== undefined
      || !pathDescriptor.enumerable
      || !("value" in pathDescriptor)
      || digestDescriptor === undefined
      || digestDescriptor.get !== undefined
      || digestDescriptor.set !== undefined
      || !digestDescriptor.enumerable
      || !("value" in digestDescriptor)
    ) {
      throw new Error("provider_invalid:execution_root");
    }
    const path = pathDescriptor.value as unknown;
    const executionRootDigest = digestDescriptor.value as unknown;
    if (
      typeof path !== "string"
      || !isAbsolute(path)
      || typeof executionRootDigest !== "string"
      || !COMPONENT_DIGEST.test(executionRootDigest)
      || !COMPONENT_DIGEST.test(componentDigest)
    ) {
      throw new Error("provider_invalid:execution_root");
    }
    const identity = await snapshotDirectoryIdentity(path);
    const observedDigest = await digestTree(identity.canonicalPath);
    await assertDirectoryIdentity(identity);
    if (observedDigest !== executionRootDigest) throw new Error("provider_invalid:execution_root");
    const verified = new VerifiedProcessExecutionRoot();
    verifiedRoots.set(verified, Object.freeze({
      path: identity.configuredPath,
      componentDigest,
      executionRootDigest,
      identity,
    }));
    return verified;
  }
}

export function verifyProcessExecutionRoot(
  content: StoredPluginContent,
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
  await assertDirectoryIdentity(details.identity);
  let observedDigest: string;
  try {
    observedDigest = await digestTree(details.identity.canonicalPath);
  } catch (error: unknown) {
    if (error instanceof PluginSourceSecurityError) throw error;
    throw new Error("provider_invalid:execution_root_changed");
  }
  await assertDirectoryIdentity(details.identity);
  if (observedDigest !== details.executionRootDigest) {
    throw new Error("provider_invalid:execution_root_changed");
  }
}
