import { describe, expect, it } from "vitest";
import type {
  IPluginsRepository,
  PluginCatalogEntry,
  PluginCatalogSnapshot,
  PluginComponentAdmission,
  PluginCredentialSlot,
  PluginIdempotentInstall,
  PluginIdempotentInstallResult,
  PluginIdempotentEnable,
  PluginIdempotentEnableResult,
  PluginInstallation,
  PluginMigrationRecord,
  PluginReceipt,
} from "@/src/application/repositories/plugins.repository.interface";
import type { IPluginApiAuthorizationPolicy, PluginApiIdentity } from "@/src/application/policies/plugin-api-authorization.policy";
import { InstallPluginUseCase } from "@/src/application/use-cases/plugins/install-plugin.use-case";
import { PreviewPluginInstallationUseCase } from "@/src/application/use-cases/plugins/preview-plugin-installation.use-case";
import { SetPluginEnabledUseCase } from "@/src/application/use-cases/plugins/set-plugin-enabled.use-case";
import { Auth0PluginApiAuthorizationPolicy } from "@/src/infrastructure/policies/auth0.plugin-api-authorization.policy";
import { PluginInstallationController } from "@/src/interface-adapters/controllers/plugins/plugin-installation.controller";
import { PINNED_OPENAI_PLUGINS_COMMIT, PINNED_PLUGIN_CATALOG_DIGEST } from "@rowboat/openai-plugin-runtime";

const digest = (value: string): string => value.repeat(64);
const identity: PluginApiIdentity = Object.freeze({ kind: "user", userId: "user-1" });
const snapshot: PluginCatalogSnapshot = Object.freeze({
  sourceUrl: "https://github.com/openai/plugins.git", sourceCommit: PINNED_OPENAI_PLUGINS_COMMIT, importedAt: "2026-08-24T00:00:00.000Z",
  schemaVersion: "rowboat-plugin-schema-v1", policyVersion: "policy-v1", inventory: {
    pluginsWithSkills: 1, pluginsWithApps: 0, pluginsWithAgents: 0, pluginsWithCommands: 0, pluginsWithMcp: 0, pluginsWithCommandHooks: 0,
  }, licenseDeclarations: { MIT: 1 }, catalogDigest: PINNED_PLUGIN_CATALOG_DIGEST,
});
const entry: PluginCatalogEntry = Object.freeze({
  catalogDigest: snapshot.catalogDigest, name: "github", sourceUrl: snapshot.sourceUrl, sourceCommit: snapshot.sourceCommit,
  pluginName: "github", pluginVersion: "1.0.0", manifestDigest: digest("b"), treeDigest: digest("c"),
  importedAt: snapshot.importedAt, schemaVersion: snapshot.schemaVersion, policyVersion: snapshot.policyVersion,
  admission: { status: "admitted" as const, policyVersion: snapshot.policyVersion },
  components: [{
    component: { id: "skill:github", name: "GitHub", kind: "skill" as const, status: "available" as const, metadata: { digest: digest("d"), credentialSlots: ["GITHUB_PAT_TOKEN"] } },
    admission: { status: "admitted" as const, policyVersion: snapshot.policyVersion },
  }],
});

class FakeAuthorization implements IPluginApiAuthorizationPolicy {
  reject = false;
  async authenticate(): Promise<PluginApiIdentity> { return identity; }
  async authorizeProject(_identity: PluginApiIdentity, _projectId: string): Promise<void> {
    if (this.reject) throw new Error("forbidden");
  }
}

class FakeRepository implements IPluginsRepository {
  installationWrites = 0;
  private result: PluginIdempotentInstallResult | null = null;
  async putCatalogSnapshot(): Promise<void> {}
  async getCatalogSnapshot(value: string): Promise<PluginCatalogSnapshot | null> { return value === snapshot.catalogDigest ? snapshot : null; }
  async listCatalogEntries(value: string): Promise<readonly PluginCatalogEntry[]> { return value === snapshot.catalogDigest ? [entry] : []; }
  async getInstallation(): Promise<PluginInstallation | null> { return null; }
  async putCatalogEntries(): Promise<void> {}
  async putInstallation(): Promise<void> { this.installationWrites += 1; }
  async listInstallations(): Promise<readonly PluginInstallation[]> { return []; }
  async setInstallationEnabled(): Promise<PluginInstallation> { throw new Error("unused"); }
  async putAdmissions(): Promise<void> {}
  async listAdmissions(): Promise<readonly PluginComponentAdmission[]> { return []; }
  async listCredentialSlots(): Promise<readonly PluginCredentialSlot[]> { return []; }
  async putCredentialSlot(): Promise<void> {}
  async putMigrationRecord(_record: PluginMigrationRecord): Promise<void> {}
  async putReceipt(_receipt: PluginReceipt): Promise<void> {}
  async getIdempotentReceipt(_scope: string, fingerprintValue: string): Promise<PluginReceipt | null> {
    if (this.result === null) return null;
    if (this.result.fingerprint !== fingerprintValue) throw new Error("idempotency_conflict");
    return this.result.receipt;
  }
  async installIdempotently(request: PluginIdempotentInstall): Promise<PluginIdempotentInstallResult> {
    if (this.result !== null) {
      if (this.result.fingerprint !== request.fingerprint) throw new Error("idempotency_conflict");
      return this.result;
    }
    await Promise.resolve();
    if (this.result !== null) return this.result;
    this.installationWrites += 1;
    this.result = Object.freeze({ receipt: request.receipt, fingerprint: request.fingerprint, replayed: false });
    return this.result;
  }
  async setInstallationEnabledIdempotently(_request: PluginIdempotentEnable): Promise<PluginIdempotentEnableResult> { throw new Error("unused"); }
}

const installRequest = Object.freeze({
  identity, projectId: "project-1", pluginName: "github", catalogDigest: snapshot.catalogDigest,
  idempotencyKey: "install-key-1", expectedRevision: 0,
});

describe("authorized plugin services", () => {
  it("does not write when project authorization fails", async () => {
    const repository = new FakeRepository();
    const authorization = new FakeAuthorization();
    authorization.reject = true;
    const useCase = new InstallPluginUseCase({ pluginsRepository: repository, pluginApiAuthorizationPolicy: authorization });
    await expect(useCase.execute(installRequest)).rejects.toThrow("forbidden");
    expect(repository.installationWrites).toBe(0);
  });

  it("returns the exact existing receipt for repeated and concurrent identical idempotency requests", async () => {
    const repository = new FakeRepository();
    const useCase = new InstallPluginUseCase({ pluginsRepository: repository, pluginApiAuthorizationPolicy: new FakeAuthorization() });
    const [first, second] = await Promise.all([useCase.execute(installRequest), useCase.execute(installRequest)]);
    expect(second).toEqual(first);
    expect(repository.installationWrites).toBe(1);
  });

  it("rejects an idempotency key reused with a different payload", async () => {
    const useCase = new InstallPluginUseCase({ pluginsRepository: new FakeRepository(), pluginApiAuthorizationPolicy: new FakeAuthorization() });
    await useCase.execute(installRequest);
    await expect(useCase.execute({ ...installRequest, expectedRevision: 1 })).rejects.toThrow("idempotency_conflict");
  });

  it("fails digest and optimistic revision validation before an installation write", async () => {
    const repository = new FakeRepository();
    const useCase = new InstallPluginUseCase({ pluginsRepository: repository, pluginApiAuthorizationPolicy: new FakeAuthorization() });
    await expect(useCase.execute({ ...installRequest, catalogDigest: digest("f") })).rejects.toThrow("catalog_digest_mismatch");
    repository.getInstallation = async () => Object.freeze({
      id: "installation-1", projectId: "project-1", pluginName: "github", pluginVersion: entry.pluginVersion,
      sourceCommit: entry.sourceCommit, manifestDigest: entry.manifestDigest, treeDigest: entry.treeDigest,
      policyVersion: entry.policyVersion, enabled: true, revision: 2,
    });
    await expect(useCase.execute(installRequest)).rejects.toThrow("installation_conflict");
    expect(repository.installationWrites).toBe(0);
  });

  it("rejects a non-admitted license before an installation write", async () => {
    const repository = new FakeRepository();
    repository.listCatalogEntries = async () => [{ ...entry, admission: { status: "rejected", reason: "license_rejected", policyVersion: snapshot.policyVersion } }];
    const useCase = new InstallPluginUseCase({ pluginsRepository: repository, pluginApiAuthorizationPolicy: new FakeAuthorization() });
    await expect(useCase.execute(installRequest)).rejects.toThrow("license_rejected");
    expect(repository.installationWrites).toBe(0);
  });

  it("returns credential slot names/configuration only and never reference values", async () => {
    const repository = new FakeRepository();
    repository.getInstallation = async () => Object.freeze({
      id: "installation-1", projectId: "project-1", pluginName: "github", pluginVersion: entry.pluginVersion,
      sourceCommit: entry.sourceCommit, manifestDigest: entry.manifestDigest, treeDigest: entry.treeDigest,
      policyVersion: entry.policyVersion, enabled: true, revision: 1,
    });
    repository.listCredentialSlots = async () => [Object.freeze({
      id: "slot-1", projectId: "project-1", installationId: "installation-1", name: "GITHUB_PAT_TOKEN",
      reference: { kind: "environment" as const, reference: "secret-reference" },
    })];
    const useCase = new PreviewPluginInstallationUseCase({ pluginsRepository: repository, pluginApiAuthorizationPolicy: new FakeAuthorization() });
    const preview = await useCase.execute({ identity, projectId: "project-1", pluginName: "github", catalogDigest: snapshot.catalogDigest });
    expect(preview.credentialSlots).toEqual([{ name: "GITHUB_PAT_TOKEN", configured: true }]);
    expect(JSON.stringify(preview)).not.toContain("secret-reference");
  });

  it("idempotently enables the exact authorized installation with optimistic revision", async () => {
    const repository = new FakeRepository();
    const current: PluginInstallation = Object.freeze({
      id: "installation-1", projectId: "project-1", pluginName: "github", pluginVersion: entry.pluginVersion,
      sourceCommit: entry.sourceCommit, manifestDigest: entry.manifestDigest, treeDigest: entry.treeDigest,
      policyVersion: entry.policyVersion, enabled: true, revision: 0,
    });
    repository.getInstallation = async () => current;
    let writes = 0;
    let saved: PluginIdempotentEnableResult | null = null;
    repository.setInstallationEnabledIdempotently = async (request) => {
      if (saved !== null) return saved;
      writes += 1;
      saved = Object.freeze({
        receipt: request.receipt, fingerprint: request.fingerprint, replayed: false,
        installation: Object.freeze({ ...current, enabled: request.enabled, revision: 1 }),
      });
      return saved;
    };
    const useCase = new SetPluginEnabledUseCase({ pluginsRepository: repository, pluginApiAuthorizationPolicy: new FakeAuthorization() });
    const request = { identity, projectId: "project-1", pluginName: "github", catalogDigest: snapshot.catalogDigest, enabled: false, expectedRevision: 0, idempotencyKey: "enable-key-1" };
    const first = await useCase.execute(request);
    const second = await useCase.execute(request);
    expect(second).toEqual(first);
    expect(first).toMatchObject({ enabled: false, revision: 1 });
    expect(writes).toBe(1);
  });

  it("rejects proxied/controller accessor input without invoking traps or getters", async () => {
    let calls = 0;
    const target = { projectId: "project-1", pluginName: "github", catalogDigest: snapshot.catalogDigest };
    Object.defineProperty(target, "idempotencyKey", { enumerable: true, get: () => { calls += 1; return "secret-value"; } });
    const proxied = new Proxy(target, { ownKeys: (value) => { calls += 1; return Reflect.ownKeys(value); } });
    const controller = new PluginInstallationController({
      pluginApiAuthorizationPolicy: new FakeAuthorization(),
      previewPluginInstallationUseCase: new PreviewPluginInstallationUseCase({ pluginsRepository: new FakeRepository(), pluginApiAuthorizationPolicy: new FakeAuthorization() }),
      installPluginUseCase: {} as InstallPluginUseCase,
      setPluginEnabledUseCase: {} as never,
    });
    await expect(controller.preview(new Request("https://example.invalid"), proxied)).rejects.toThrow("request_invalid");
    expect(calls).toBe(0);
  });
});

describe("plugin API authentication", () => {
  function policy(options: { project?: string | null; user?: string | null; authEnabled?: boolean } = {}) {
    return new Auth0PluginApiAuthorizationPolicy({
      pluginUserSessionProvider: { getUserId: async () => options.user ?? "user-1" },
      pluginProjectApiKeyVerifier: { verify: async () => options.project ?? null },
      pluginUserTokenVerifier: { verify: async () => null },
      projectMembersRepository: { exists: async () => true } as never,
      pluginAuthEnabled: options.authEnabled ?? false,
    });
  }

  it("uses the existing project API-key verifier before any user-token verifier", async () => {
    const identityResult = await policy({ project: "project-1" }).authenticate(new Request("https://example.invalid", { headers: { authorization: "Bearer exact-key" } }));
    expect(identityResult).toEqual({ kind: "project_api_key", projectId: "project-1" });
  });

  it.each(["opaque-arbitrary", "expired.jwt.value", "wrong-issuer.jwt.value", "wrong-audience.jwt.value"])(
    "rejects unverified bearer %s without producing an identity",
    async (token) => {
      await expect(policy({ authEnabled: true }).authenticate(new Request("https://example.invalid", { headers: { authorization: `Bearer ${token}` } })))
        .rejects.toThrow("unauthenticated");
    },
  );

  it("rejects an arbitrary bearer while authentication is disabled instead of mapping it to guest", async () => {
    await expect(policy({ authEnabled: false }).authenticate(new Request("https://example.invalid", { headers: { authorization: "Bearer arbitrary" } })))
      .rejects.toThrow("unauthenticated");
  });

  it("rejects malformed and multiple authorization values fail closed", async () => {
    await expect(policy().authenticate(new Request("https://example.invalid", { headers: { authorization: "Basic abc" } }))).rejects.toThrow("authorization_invalid");
    await expect(policy().authenticate(new Request("https://example.invalid", { headers: { authorization: "Bearer one, Bearer two" } }))).rejects.toThrow("authorization_invalid");
  });

  it("rejects a proxied Request without invoking proxy traps", async () => {
    let calls = 0;
    const request = new Proxy(new Request("https://example.invalid"), { get: (target, key, receiver) => { calls += 1; return Reflect.get(target, key, receiver); } });
    await expect(policy().authenticate(request)).rejects.toThrow("request_invalid");
    expect(calls).toBe(0);
  });
});
