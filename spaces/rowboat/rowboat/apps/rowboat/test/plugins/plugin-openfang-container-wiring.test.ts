import { describe, expect, it } from "vitest";
import { DEFAULT_POLICY, type PluginPolicy, type ProviderResolution } from "@rowboat/openai-plugin-runtime";
import { resolveOpenFangComposedProvider, resolveOpenFangProvider, resolveOpenFangReleaseWrite } from "@/di/plugins-container";
import { UnreleasedCredentialResolver } from "@/src/infrastructure/plugins/provider-resolution";
import { OpenFangCredentialResolver } from "@/src/infrastructure/plugins/openfang-credential-resolver";
import type { PluginProviderResolutionDependencies, PluginProviderResolutionRequest } from "@/src/infrastructure/plugins/provider-resolution";
import type { PluginProviderResolutionInput } from "@/src/application/services/plugin-tool-runtime";
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
  const previousIssueKey = process.env.OPENFANG_ISSUE_KEY;
  process.env.OPENFANG_ISSUE_KEY = "test-issue-key";
  if (url === undefined) delete process.env.OPENFANG_URL; else process.env.OPENFANG_URL = url;
  if (apiKey === undefined) delete process.env.OPENFANG_API_KEY; else process.env.OPENFANG_API_KEY = apiKey;
  try {
    return await fn();
  } finally {
    if (previousUrl === undefined) delete process.env.OPENFANG_URL; else process.env.OPENFANG_URL = previousUrl;
    if (previousKey === undefined) delete process.env.OPENFANG_API_KEY; else process.env.OPENFANG_API_KEY = previousKey;
    if (previousIssueKey === undefined) delete process.env.OPENFANG_ISSUE_KEY; else process.env.OPENFANG_ISSUE_KEY = previousIssueKey;
  }
}

async function withResponsesEnv<T>(model: string | undefined, baseUrl: string | undefined, fn: () => Promise<T>): Promise<T> {
  const previousModel = process.env.OPENAI_RESPONSES_MODEL;
  const previousBaseUrl = process.env.OPENAI_BASE_URL;
  if (model === undefined) delete process.env.OPENAI_RESPONSES_MODEL; else process.env.OPENAI_RESPONSES_MODEL = model;
  if (baseUrl === undefined) delete process.env.OPENAI_BASE_URL; else process.env.OPENAI_BASE_URL = baseUrl;
  try {
    return await fn();
  } finally {
    if (previousModel === undefined) delete process.env.OPENAI_RESPONSES_MODEL; else process.env.OPENAI_RESPONSES_MODEL = previousModel;
    if (previousBaseUrl === undefined) delete process.env.OPENAI_BASE_URL; else process.env.OPENAI_BASE_URL = previousBaseUrl;
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

  it("falls back to UnreleasedCredentialResolver when OPENFANG_ISSUE_KEY is missing", async () => {
    await withOpenFangEnv("https://openfang.example.com", "test-key", async () => {
      delete process.env.OPENFANG_ISSUE_KEY;
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

  /**
   * OPENAI_RESPONSES_MODEL/OPENAI_BASE_URL are the connector-bridge
   * counterpart of OPENFANG_URL/OPENFANG_API_KEY above: read fresh from
   * process.env inside resolveOpenFangProvider and forwarded into
   * PluginProviderResolutionDependencies as responsesModel/openAiBaseUrl so
   * Task 3's app-component branch in provider-resolution.ts can pick them
   * up. This pins the exact call (di/plugins-container.ts's
   * `resolvePluginProviderImpl({ component, entry, binding }, {
   * credentialResolver, policy, responsesModel, openAiBaseUrl })`) --  if
   * either field were dropped there, this assertion would fail while every
   * other test in this file (which never sets these two variables) stayed
   * green, exactly the failure shape the Task 7 regression this file
   * otherwise guards against had.
   */
  it("forwards OPENAI_RESPONSES_MODEL and OPENAI_BASE_URL as responsesModel/openAiBaseUrl", async () => {
    await withOpenFangEnv(undefined, undefined, async () => {
      await withResponsesEnv("gpt-5.7-mini", "https://byo-openai.example.com", async () => {
        let received: PluginProviderResolutionDependencies | undefined;
        await resolveOpenFangProvider(
          { ...providerRequest, policy: DEFAULT_POLICY },
          {
            resolvePluginProviderImpl: (_request, dependencies) => { received = dependencies; return UNAVAILABLE_RESOLUTION; },
            credentialTimeoutMs: 5_000, fetchImpl: neverFetch,
          },
        );
        expect(received?.responsesModel).toBe("gpt-5.7-mini");
        expect(received?.openAiBaseUrl).toBe("https://byo-openai.example.com");
      });
    });
  });

  it("collapses a blank or whitespace-only OPENAI_RESPONSES_MODEL/OPENAI_BASE_URL to undefined", async () => {
    await withOpenFangEnv(undefined, undefined, async () => {
      for (const blank of ["", "   "]) {
        await withResponsesEnv(blank, blank, async () => {
          let received: PluginProviderResolutionDependencies | undefined;
          await resolveOpenFangProvider(
            { ...providerRequest, policy: DEFAULT_POLICY },
            {
              resolvePluginProviderImpl: (_request, dependencies) => { received = dependencies; return UNAVAILABLE_RESOLUTION; },
              credentialTimeoutMs: 5_000, fetchImpl: neverFetch,
            },
          );
          expect(received?.responsesModel).toBeUndefined();
          expect(received?.openAiBaseUrl).toBeUndefined();
        });
      }
    });
  });

  it("leaves responsesModel/openAiBaseUrl undefined when the variables are absent entirely", async () => {
    await withOpenFangEnv(undefined, undefined, async () => {
      await withResponsesEnv(undefined, undefined, async () => {
        let received: PluginProviderResolutionDependencies | undefined;
        await resolveOpenFangProvider(
          { ...providerRequest, policy: DEFAULT_POLICY },
          {
            resolvePluginProviderImpl: (_request, dependencies) => { received = dependencies; return UNAVAILABLE_RESOLUTION; },
            credentialTimeoutMs: 5_000, fetchImpl: neverFetch,
          },
        );
        expect(received?.responsesModel).toBeUndefined();
        expect(received?.openAiBaseUrl).toBeUndefined();
      });
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

  /**
   * The release calls must authenticate the same way the credential path
   * already does. The live end-to-end proof found this composition handing
   * the policy no key at all, so every create POST hit OpenFang's auth
   * middleware -- which makes /api/approvals public for GET only -- and came
   * back 401, leaving every write under review no matter who approved it.
   * OPENFANG_API_KEY is already in scope here for resolveOpenFangCredentialSource;
   * these pin that it reaches the release policy too.
   */
  it("passes OPENFANG_API_KEY through to the release policy", async () => {
    await withOpenFangEnv("https://openfang.example.com", "release-key-value", async () => {
      RecordingPolicy.constructedCount = 0;
      RecordingPolicy.lastOptions = undefined;
      await resolveOpenFangReleaseWrite(writeRequest, new AbortController().signal, {
        approvalWindowMs: 1_000, fetchImpl: neverFetch, OpenFangWriteReleasePolicyImpl: RecordingPolicy,
      });
      expect(RecordingPolicy.lastOptions).toMatchObject({ apiKey: "release-key-value" });
    });
  });

  it("trims the key it passes through, exactly as resolveOpenFangCredentialSource trims its own", async () => {
    await withOpenFangEnv("https://openfang.example.com", "  release-key-value  ", async () => {
      RecordingPolicy.lastOptions = undefined;
      await resolveOpenFangReleaseWrite(writeRequest, new AbortController().signal, {
        approvalWindowMs: 1_000, fetchImpl: neverFetch, OpenFangWriteReleasePolicyImpl: RecordingPolicy,
      });
      expect(RecordingPolicy.lastOptions).toMatchObject({ apiKey: "release-key-value" });
    });
  });

  it("passes no key when OPENFANG_API_KEY is absent or blank -- never an empty bearer, and never a skipped release", async () => {
    for (const key of [undefined, "", "   "]) {
      await withOpenFangEnv("https://openfang.example.com", key, async () => {
        RecordingPolicy.constructedCount = 0;
        RecordingPolicy.lastOptions = undefined;
        const decision = await resolveOpenFangReleaseWrite(writeRequest, new AbortController().signal, {
          approvalWindowMs: 1_000, fetchImpl: neverFetch, OpenFangWriteReleasePolicyImpl: RecordingPolicy,
        });
        // The release still happens -- an absent key is not a reason to skip it.
        expect(RecordingPolicy.constructedCount).toBe(1);
        expect(decision).toEqual({ status: "approved", approvalId: "3f0f8a1e-0000-4000-8000-000000000099" });
        expect((RecordingPolicy.lastOptions as { apiKey?: unknown }).apiKey).toBeUndefined();
      });
    }
  });
});

describe("resolveOpenFangComposedProvider (the exact composition wired into PluginToolRuntimeDependencies.resolveProvider)", () => {
  // A real MCP declaration, not a spy: this proves the policy handed to the
  // *composed* function (the one createToolRuntime actually wires up, with
  // no destructuring/rebuilding left in front of it) reaches the real,
  // dynamically-imported resolvePluginProvider and the real HttpMcpProvider
  // it constructs -- the exact five lines where Task 7's regression
  // happened, closed off rather than merely spied on.
  const componentDigest = "a".repeat(64);
  // 127.0.0.1:1 refuses the connection immediately (nothing listens there),
  // so this stays fast and needs no network access -- it only has to prove
  // which policy the constructed provider was built with, not complete a
  // real call. Mirrors provider-resolution.test.ts's own technique.
  const localComponent = Object.freeze({
    id: "mcp:.mcp.json#github", name: "github", kind: "mcp",
    metadata: Object.freeze({
      digest: "b".repeat(64), bindingDigest: componentDigest, transport: "http",
      mcpServer: Object.freeze({ type: "http", url: "https://127.0.0.1:1/mcp" }),
    }),
  });
  const localBinding = Object.freeze({ id: "mcp.github", providerKind: "mcp-http" as const, componentDigest });
  const localEntry = Object.freeze({ licenseDeclaration: "MIT" });
  const invocationRequest = Object.freeze({
    projectId: "project-1", pluginName: "github", componentName: "github",
    operationName: "search", capability: "write" as const, arguments: {},
  });

  // Only component/entry/binding/policy are ever read by this composition
  // boundary (see resolveOpenFangProvider's own doc comment) -- catalog,
  // installation, credentialSlots, and signal are typed-but-unused filler
  // PluginProviderResolutionInput otherwise requires. `unknown` first, per
  // this repo's own rule against `any`.
  const composedInput = (policy: PluginPolicy): PluginProviderResolutionInput => ({
    component: localComponent, entry: localEntry, binding: localBinding, policy,
  } as unknown as PluginProviderResolutionInput);

  it("forwards the unelevated default policy through to the real provider, which then refuses the write outright", async () => {
    await withOpenFangEnv(undefined, undefined, async () => {
      const resolution = await resolveOpenFangComposedProvider(composedInput(DEFAULT_POLICY), { credentialTimeoutMs: 5_000 });
      expect(resolution.status).toBe("available");
      if (resolution.status !== "available") return;
      // DEFAULT_POLICY refuses every write outright, before any network attempt.
      await expect(resolution.provider.invoke(invocationRequest, { requestId: "req-1" }))
        .rejects.toThrow("write_review_required");
    });
  });

  it("forwards an elevated policy through to the real provider, which then admits the write and reaches the network", async () => {
    await withOpenFangEnv(undefined, undefined, async () => {
      const resolution = await resolveOpenFangComposedProvider(composedInput(elevatedPolicy), { credentialTimeoutMs: 5_000 });
      expect(resolution.status).toBe("available");
      if (resolution.status !== "available") return;
      const result = await resolution.provider.invoke(invocationRequest, { requestId: "req-2" });
      expect(result).toMatchObject({ status: "failed" });
      if (result.status !== "failed") return;
      // The elevated policy clears the same admission check and lets the
      // call reach the network attempt instead, which then fails for an
      // unrelated, local reason -- never for write_review_required again.
      // This is the observable proof that policy reached all the way
      // through: a dropped policy here would make this assertion fail,
      // reporting write_review_required instead.
      expect(result.reason).not.toBe("write_review_required");
    });
  });
});
