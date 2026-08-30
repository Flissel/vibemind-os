import { describe, expect, it } from "vitest";
import { DEFAULT_POLICY, type PluginPolicy, type ProviderResolution } from "@rowboat/openai-plugin-runtime";
import { resolveOpenFangProvider, resolveOpenFangReleaseWrite } from "@/di/plugins-container";
import { UnreleasedCredentialResolver } from "@/src/infrastructure/plugins/provider-resolution";
import { OpenFangCredentialResolver } from "@/src/infrastructure/plugins/openfang-credential-resolver";
import type { PluginProviderResolutionDependencies, PluginProviderResolutionRequest } from "@/src/infrastructure/plugins/provider-resolution";
import type { IPluginWriteReleasePolicy, PluginWriteReleaseDecision, PluginWriteReleaseRequest } from "@/src/application/policies/plugin-write-release.policy";

/**
 * These two functions are the exact wiring the live gate otherwise only
 * mirrors by hand (its own comment says so) rather than exercises -- which
 * is how dropping `policy` from resolveProvider's destructuring or its call
 * regressed the defect Task 7 found live, with 666 tests and the live gate
 * both green. These tests exercise the real composition functions directly.
 */

const neverFetch = (async () => { throw new Error("fetch must not be called in this test"); }) as unknown as typeof fetch;

async function withOpenFangEnv<T>(url: string | undefined, apiKey: string | undefined, fn: () => Promise<T>): Promise<T> {
  const previousUrl = process.env.OPENFANG_URL;
  const previousKey = process.env.OPENFANG_API_KEY;
  if (url === undefined) delete process.env.OPENFANG_URL; else process.env.OPENFANG_URL = url;
  if (apiKey === undefined) delete process.env.OPENFANG_API_KEY; else process.env.OPENFANG_API_KEY = apiKey;
  try {
    return await fn();
  } finally {
    if (previousUrl === undefined) delete process.env.OPENFANG_URL; else process.env.OPENFANG_URL = previousUrl;
    if (previousKey === undefined) delete process.env.OPENFANG_API_KEY; else process.env.OPENFANG_API_KEY = previousKey;
  }
}

const providerRequest: PluginProviderResolutionRequest = Object.freeze({
  component: Object.freeze({ id: "mcp:.mcp.json#github", name: "github", kind: "mcp", metadata: Object.freeze({}) }),
  entry: Object.freeze({ licenseDeclaration: "MIT" }),
  binding: Object.freeze({ id: "mcp.github", providerKind: "mcp-http" as const, componentDigest: "a".repeat(64) }),
});

const elevatedPolicy: PluginPolicy = Object.freeze({ ...DEFAULT_POLICY, allowWriteCapabilities: true });
const UNAVAILABLE_RESOLUTION: ProviderResolution = Object.freeze({ status: "unavailable" as const, reason: "provider_unavailable" as const });

describe("resolveOpenFangProvider (container wiring)", () => {
  it("forwards the exact policy object it was handed into resolvePluginProviderImpl -- the Task 7 regression", async () => {
    await withOpenFangEnv(undefined, undefined, async () => {
      let received: PluginProviderResolutionDependencies | undefined;
      const spy = (_request: PluginProviderResolutionRequest, dependencies: PluginProviderResolutionDependencies): ProviderResolution => {
        received = dependencies;
        return UNAVAILABLE_RESOLUTION;
      };
      await resolveOpenFangProvider(
        { ...providerRequest, policy: elevatedPolicy },
        { resolvePluginProviderImpl: spy, credentialTimeoutMs: 5_000, fetchImpl: neverFetch },
      );
      // Reference equality, not just structural: a spread copy would also
      // pass toEqual but would not prove the same object reached the call.
      expect(received?.policy).toBe(elevatedPolicy);
    });
  });

  it("forwards component, entry, and binding unchanged", async () => {
    await withOpenFangEnv(undefined, undefined, async () => {
      let received: PluginProviderResolutionRequest | undefined;
      const spy = (request: PluginProviderResolutionRequest): ProviderResolution => {
        received = request;
        return UNAVAILABLE_RESOLUTION;
      };
      await resolveOpenFangProvider(
        { ...providerRequest, policy: DEFAULT_POLICY },
        { resolvePluginProviderImpl: spy, credentialTimeoutMs: 5_000, fetchImpl: neverFetch },
      );
      expect(received?.component).toBe(providerRequest.component);
      expect(received?.entry).toBe(providerRequest.entry);
      expect(received?.binding).toBe(providerRequest.binding);
    });
  });

  it("returns whatever resolvePluginProviderImpl returns -- it does not re-decide anything itself", async () => {
    await withOpenFangEnv(undefined, undefined, async () => {
      const available: ProviderResolution = Object.freeze({
        status: "available" as const,
        provider: Object.freeze({
          id: "mcp.github", describe: () => Object.freeze({ id: "mcp.github", kind: "mcp-http" as const, temporaryAdapter: false }),
          invoke: async () => Object.freeze({ status: "success" as const, output: {} }),
        }),
      });
      const resolution = await resolveOpenFangProvider(
        { ...providerRequest, policy: DEFAULT_POLICY },
        { resolvePluginProviderImpl: () => available, credentialTimeoutMs: 5_000, fetchImpl: neverFetch },
      );
      expect(resolution).toBe(available);
    });
  });

  it("constructs UnreleasedCredentialResolver when OpenFang is not configured", async () => {
    await withOpenFangEnv(undefined, undefined, async () => {
      let received: PluginProviderResolutionDependencies | undefined;
      await resolveOpenFangProvider(
        { ...providerRequest, policy: DEFAULT_POLICY },
        {
          resolvePluginProviderImpl: (_request, dependencies) => { received = dependencies; return UNAVAILABLE_RESOLUTION; },
          credentialTimeoutMs: 5_000, fetchImpl: neverFetch,
        },
      );
      expect(received?.credentialResolver).toBeInstanceOf(UnreleasedCredentialResolver);
    });
  });

  it("constructs OpenFangCredentialResolver when OPENFANG_URL and OPENFANG_API_KEY are both configured", async () => {
    await withOpenFangEnv("https://openfang.example.com", "test-key", async () => {
      let received: PluginProviderResolutionDependencies | undefined;
      await resolveOpenFangProvider(
        { ...providerRequest, policy: DEFAULT_POLICY },
        {
          resolvePluginProviderImpl: (_request, dependencies) => { received = dependencies; return UNAVAILABLE_RESOLUTION; },
          credentialTimeoutMs: 5_000, fetchImpl: neverFetch,
        },
      );
      expect(received?.credentialResolver).toBeInstanceOf(OpenFangCredentialResolver);
    });
  });
});

const writeRequest: PluginWriteReleaseRequest = Object.freeze({
  projectId: "11111111-1111-4111-8111-111111111111",
  pluginName: "github",
  toolName: "plugin_github_github",
  componentDigest: "a".repeat(64),
  argumentsDigest: "b".repeat(64),
});

class RecordingPolicy implements IPluginWriteReleasePolicy {
  static constructedCount = 0;
  static lastOptions: unknown;

  constructor(options: unknown) {
    RecordingPolicy.constructedCount += 1;
    RecordingPolicy.lastOptions = options;
  }

  async release(): Promise<PluginWriteReleaseDecision> {
    return Object.freeze({ status: "approved" as const, approvalId: "3f0f8a1e-0000-4000-8000-000000000099" });
  }
}

describe("resolveOpenFangReleaseWrite (container wiring)", () => {
  it("returns unavailable with no OPENFANG_URL, without constructing the release policy at all", async () => {
    await withOpenFangEnv(undefined, undefined, async () => {
      RecordingPolicy.constructedCount = 0;
      const decision = await resolveOpenFangReleaseWrite(writeRequest, new AbortController().signal, {
        approvalWindowMs: 1_000, fetchImpl: neverFetch, OpenFangWriteReleasePolicyImpl: RecordingPolicy,
      });
      expect(decision).toEqual({ status: "unavailable" });
      expect(RecordingPolicy.constructedCount).toBe(0);
    });
  });

  it("returns unavailable for a blank or whitespace-only OPENFANG_URL too", async () => {
    for (const blank of ["", "   "]) {
      await withOpenFangEnv(blank, "key", async () => {
        RecordingPolicy.constructedCount = 0;
        const decision = await resolveOpenFangReleaseWrite(writeRequest, new AbortController().signal, {
          approvalWindowMs: 1_000, fetchImpl: neverFetch, OpenFangWriteReleasePolicyImpl: RecordingPolicy,
        });
        expect(decision).toEqual({ status: "unavailable" });
        expect(RecordingPolicy.constructedCount).toBe(0);
      });
    }
  });

  it("delegates to the injected policy and returns its decision when OPENFANG_URL is configured", async () => {
    await withOpenFangEnv("https://openfang.example.com", "key", async () => {
      RecordingPolicy.constructedCount = 0;
      const decision = await resolveOpenFangReleaseWrite(writeRequest, new AbortController().signal, {
        approvalWindowMs: 1_000, fetchImpl: neverFetch, OpenFangWriteReleasePolicyImpl: RecordingPolicy,
      });
      expect(decision).toEqual({ status: "approved", approvalId: "3f0f8a1e-0000-4000-8000-000000000099" });
      expect(RecordingPolicy.constructedCount).toBe(1);
      expect(RecordingPolicy.lastOptions).toMatchObject({ baseUrl: "https://openfang.example.com", timeoutMs: 1_000, pollIntervalMs: 1_000 });
    });
  });
});
