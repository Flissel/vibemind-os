import { describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { readFileSync } from "node:fs";
import { PINNED_PLUGIN_CATALOG_DIGEST } from "@rowboat/openai-plugin-runtime";
import {
  createCatalogCollectionRoute, createCatalogItemRoute, createPluginSessionRoute, createProjectPluginsRoute, createProjectPluginRoute,
} from "@/src/interface-adapters/http/plugins/plugin-routes";
import { ListProjectPluginsUseCase } from "@/src/application/use-cases/plugins/list-project-plugins.use-case";
import type { IPluginApiAuthorizationPolicy, PluginApiIdentity } from "@/src/application/policies/plugin-api-authorization.policy";
import type { IPluginsRepository, PluginInstallation } from "@/src/application/repositories/plugins.repository.interface";
import catalogLockFixture from "../../../../config/openai-plugin-catalog.lock.json";
import type { PluginCatalogLock } from "@rowboat/openai-plugin-runtime";
import { Auth0PluginApiAuthorizationPolicy } from "@/src/infrastructure/policies/auth0.plugin-api-authorization.policy";
import { PluginCatalogController } from "@/src/interface-adapters/controllers/plugins/plugin-catalog.controller";
import { PluginInstallationController } from "@/src/interface-adapters/controllers/plugins/plugin-installation.controller";
import { ListPluginCatalogUseCase } from "@/src/application/use-cases/plugins/list-plugin-catalog.use-case";
import type { PreviewPluginInstallationUseCase } from "@/src/application/use-cases/plugins/preview-plugin-installation.use-case";
import type { InstallPluginUseCase } from "@/src/application/use-cases/plugins/install-plugin.use-case";
import type { SetPluginEnabledUseCase } from "@/src/application/use-cases/plugins/set-plugin-enabled.use-case";
import { PluginSessionController } from "@/src/interface-adapters/controllers/plugins/plugin-session.controller";

const digest = (character: string): string => character.repeat(64);
const catalogDigest = PINNED_PLUGIN_CATALOG_DIGEST;
const componentDigest = digest("a");
const catalogItem = Object.freeze({
  name: "airtable",
  pluginName: "airtable",
  pluginVersion: "1.0.0",
  catalogDigest,
  sourceCommit: "1".repeat(40),
  policyVersion: "policy-v1",
  license: Object.freeze({ declaration: "MIT", decision: "admitted" as const }),
  admission: "admitted" as const,
  components: Object.freeze([
    Object.freeze({
      componentDigest,
      name: "Airtable",
      kind: "app" as const,
      admission: Object.freeze({ status: "admitted" as const, policyVersion: "policy-v1" }),
      availability: Object.freeze({ status: "unavailable" as const, reason: "provider_unavailable" as const }),
    }),
  ]),
});

function request(path: string, init?: ConstructorParameters<typeof NextRequest>[1]): NextRequest {
  return new NextRequest(`https://rowboat.invalid${path}`, init);
}

function requestWithUnnormalizedRawUrl(path: string): NextRequest {
  const candidate = request(`/api/v1/plugins?catalogDigest=${catalogDigest}`);
  const state = Object.getOwnPropertySymbols(candidate)
    .map((symbol) => Object.getOwnPropertyDescriptor(candidate, symbol))
    .find((descriptor) => descriptor !== undefined && "value" in descriptor && descriptor.value !== null
      && typeof descriptor.value === "object" && Object.prototype.hasOwnProperty.call(descriptor.value, "url"));
  if (state === undefined || !("value" in state) || !Reflect.set(state.value as object, "url", `https://rowboat.invalid${path}`)) {
    throw new Error("next_request_internal_url_unavailable");
  }
  return candidate;
}

function proxyNextRequestEquivalent(target: NextRequest): NextRequest {
  const cloneSymbol = Symbol("clone");
  const handler: ProxyHandler<NextRequest> = {
    get(candidate, property) {
      if (property === "clone") {
        const existing = Reflect.getOwnPropertyDescriptor(candidate, cloneSymbol);
        if (existing !== undefined && "value" in existing) return existing.value;
        const clone = () => new Proxy(candidate.clone(), handler);
        Reflect.defineProperty(candidate, cloneSymbol, { value: clone, writable: true, configurable: true });
        return clone;
      }
      return Reflect.get(candidate, property, candidate);
    },
  };
  return new Proxy(target, handler);
}

async function json(response: Response): Promise<unknown> { return response.json(); }

describe("versioned plugin catalog routes", () => {
  it("keeps Next route modules limited to supported handler exports", async () => {
    const [catalogCollection, catalogSelected, projectCollection, projectSelected, pluginSession] = await Promise.all([
      import("@/app/api/v1/plugins/route"), import("@/app/api/v1/plugins/[pluginName]/route"),
      import("@/app/api/v1/projects/[projectId]/plugins/route"), import("@/app/api/v1/projects/[projectId]/plugins/[pluginName]/route"),
      import("@/app/api/v1/plugin-session/route"),
    ]);
    expect(Object.keys(catalogCollection).sort()).toEqual(["GET", "dynamic"]);
    expect(Object.keys(catalogSelected).sort()).toEqual(["GET", "dynamic"]);
    expect(Object.keys(projectCollection).sort()).toEqual(["GET", "POST", "dynamic"]);
    expect(Object.keys(projectSelected).sort()).toEqual(["GET", "PATCH", "dynamic"]);
    expect(Object.keys(pluginSession).sort()).toEqual(["GET", "dynamic"]);
    expect([catalogCollection.dynamic, catalogSelected.dynamic, projectCollection.dynamic, projectSelected.dynamic])
      .toEqual(["force-dynamic", "force-dynamic", "force-dynamic", "force-dynamic"]);
    expect(pluginSession.dynamic).toBe("force-dynamic");
  });

  it("returns only a server-verified stable plugin actor identity before any plugin reads", async () => {
    for (const identity of [{ kind: "user" as const, userId: "user-1" }, { kind: "project_api_key" as const, projectId: "project-1" }]) {
      let authentications = 0;
      const authorization: IPluginApiAuthorizationPolicy = {
        authenticate: async () => { authentications += 1; return identity; },
        authorizeProject: async () => { throw new Error("unexpected_project_read"); },
      };
      const response = await createPluginSessionRoute(new PluginSessionController(authorization))(
        request("/api/v1/plugin-session", { headers: { authorization: "Bearer opaque" } }),
      );
      expect(response.status).toBe(200);
      expect(await json(response)).toEqual({ kind: identity.kind, id: identity.kind === "user" ? identity.userId : identity.projectId });
      expect(authentications).toBe(1);
      expect(response.headers.get("cache-control")).toBe("no-store");
    }
  });

  it("rejects forged or expired plugin session tokens with no identity or raw auth error", async () => {
    for (const token of ["forged-token", "expired-token"]) {
      let verifierCalls = 0;
      const authorization = new Auth0PluginApiAuthorizationPolicy({
        pluginUserSessionProvider: { getUserId: async () => null },
        pluginProjectApiKeyVerifier: { verify: async () => null },
        pluginUserTokenVerifier: { verify: async (candidate) => { verifierCalls += 1; expect(candidate).toBe(token); return null; } },
        projectMembersRepository: { exists: async () => { throw new Error("unexpected_read"); } } as never,
        pluginAuthEnabled: true,
      });
      const response = await createPluginSessionRoute(new PluginSessionController(authorization))(
        request("/api/v1/plugin-session", { headers: { authorization: `Bearer ${token}` } }),
      );
      expect(response.status).toBe(401);
      expect(await json(response)).toEqual({ error: "unauthenticated" });
      expect(verifierCalls).toBe(1);
    }
  });

  it("returns project scope only from a verified existing project API key", async () => {
    const authorization = new Auth0PluginApiAuthorizationPolicy({
      pluginUserSessionProvider: { getUserId: async () => null },
      pluginProjectApiKeyVerifier: { verify: async (token) => token === "valid-project-key" ? "project-1" : null },
      pluginUserTokenVerifier: { verify: async () => null },
      projectMembersRepository: { exists: async () => { throw new Error("unexpected_membership_read"); } } as never,
      pluginAuthEnabled: true,
    });
    const response = await createPluginSessionRoute(new PluginSessionController(authorization))(
      request("/api/v1/plugin-session", { headers: { authorization: "Bearer valid-project-key" } }),
    );
    expect(response.status).toBe(200);
    expect(await json(response)).toEqual({ kind: "project_api_key", id: "project-1" });
  });

  it("rejects plugin session query, proxy, wrong method and invalid output before unsafe resolution", async () => {
    let resolutions = 0;
    const route = createPluginSessionRoute(async () => { resolutions += 1; return { execute: async () => ({ kind: "user", id: "user-1" }) }; });
    for (const candidate of [
      request("/api/v1/plugin-session?extra=x"),
      request("/api/v1/plugin-session", { method: "POST" }),
      new Request("https://rowboat.invalid/api/v1/plugin-session"),
      new Proxy(request("/api/v1/plugin-session"), {}),
    ]) {
      const response = await route(candidate);
      expect(response.status).toBe(400);
      expect(await json(response)).toEqual({ error: "request_invalid" });
    }
    expect(resolutions).toBe(0);
    const invalid = await createPluginSessionRoute({ execute: async () => ({ kind: "user", id: "user-1", token: "secret" }) })(request("/api/v1/plugin-session"));
    expect(invalid.status).toBe(500);
    expect(await json(invalid)).toEqual({ error: "internal_error" });
  });

  it("imports production routes and rejects invalid input without loading external composition modules", async () => {
    vi.resetModules();
    for (const moduleName of ["@/app/lib/mongodb", "@/app/lib/redis", "@/app/lib/auth0", "@/di/container"]) {
      vi.doMock(moduleName, () => { throw new Error(`eager_import:${moduleName}`); });
    }
    try {
      const production = await import("@/app/api/v1/plugins/route");
      const response = await production.GET(new Request(`https://rowboat.invalid/api/v1/plugins?catalogDigest=${catalogDigest}`));
      expect(response.status).toBe(400);
      expect(await json(response)).toEqual({ error: "request_invalid" });
      const identityProduction = await import("@/app/api/v1/plugin-session/route");
      const identityResponse = await identityProduction.GET(new Request("https://rowboat.invalid/api/v1/plugin-session"));
      expect(identityResponse.status).toBe(400);
      expect(await json(identityResponse)).toEqual({ error: "request_invalid" });
    } finally {
      for (const moduleName of ["@/app/lib/mongodb", "@/app/lib/redis", "@/app/lib/auth0", "@/di/container"]) vi.doUnmock(moduleName);
      vi.resetModules();
    }
  });

  it("accepts only genuine exact NextRequests before resolving a controller", async () => {
    let resolutions = 0;
    const route = createCatalogCollectionRoute(async () => { resolutions += 1; return { execute: async () => [catalogItem] }; });
    class Subclass extends NextRequest {}
    const fake = Object.create(NextRequest.prototype) as NextRequest;
    let proxyCalls = 0;
    const proxied = new Proxy(request(`/api/v1/plugins?catalogDigest=${catalogDigest}`), {
      get: (target, key, receiver) => { proxyCalls += 1; return Reflect.get(target, key, receiver); },
    });
    for (const candidate of [
      new Request(`https://rowboat.invalid/api/v1/plugins?catalogDigest=${catalogDigest}`),
      new Subclass(`https://rowboat.invalid/api/v1/plugins?catalogDigest=${catalogDigest}`),
      fake,
      proxied,
    ]) {
      const response = await route(candidate);
      expect(response.status).toBe(400);
      expect(await json(response)).toEqual({ error: "request_invalid" });
    }
    expect(resolutions).toBe(0);
    expect(proxyCalls).toBe(0);
  });

  it("uses the force-dynamic AppRoute path with an exact request and rejects clone-handshake and header-forging proxies", async () => {
    const installedAppRoute = readFileSync("node_modules/next/dist/server/route-modules/app-route/module.js", "utf8");
    expect(installedAppRoute).toMatch(/let request = req;[\s\S]{0,500}case 'force-dynamic':[\s\S]{0,500}break;[\s\S]{0,1500}request = proxyNextRequest\(req/);
    let resolutions = 0;
    let controllerCalls = 0;
    const route = createCatalogCollectionRoute(async () => {
      resolutions += 1;
      return { execute: async () => { controllerCalls += 1; return [catalogItem]; } };
    });
    const exactResponse = await route(request(`/api/v1/plugins?catalogDigest=${catalogDigest}`));
    expect(exactResponse.status).toBe(200);
    expect(resolutions).toBe(1);
    expect(controllerCalls).toBe(1);

    let forgedAccessorCalls = 0;
    const forgedHeaders = new Headers();
    Object.defineProperty(forgedHeaders, "get", {
      get: () => { forgedAccessorCalls += 1; return Headers.prototype.get; },
    });
    const cloneHandshake = proxyNextRequestEquivalent(request(`/api/v1/plugins?catalogDigest=${catalogDigest}`));
    const headerForging = new Proxy(request(`/api/v1/plugins?catalogDigest=${catalogDigest}`), {
      get: (target, property) => property === "headers" ? forgedHeaders : Reflect.get(target, property, target),
    });
    for (const candidate of [cloneHandshake, headerForging]) {
      const rejected = await route(candidate);
      expect(rejected.status).toBe(400);
      expect(await json(rejected)).toEqual({ error: "request_invalid" });
    }
    expect(resolutions).toBe(1);
    expect(controllerCalls).toBe(1);
    expect(forgedAccessorCalls).toBe(0);
  });

  it("rejects own value and accessor Headers.get overrides without invoking them or resolving dependencies", async () => {
    let resolutions = 0;
    let valueCalls = 0;
    let accessorCalls = 0;
    const route = createProjectPluginsRoute(async () => {
      resolutions += 1;
      return { install: async () => { throw new Error("controller_must_not_run"); } };
    });
    const init = {
      method: "POST",
      headers: { "content-type": "application/json", "Idempotency-Key": "install-1" },
      body: JSON.stringify({ pluginName: "airtable", catalogDigest, expectedRevision: 0 }),
    };
    const valueRequest = request(`/api/v1/projects/project-1/plugins`, init);
    Object.defineProperty(valueRequest.headers, "get", {
      configurable: true,
      value: () => { valueCalls += 1; return catalogDigest; },
    });
    const accessorRequest = request(`/api/v1/projects/project-1/plugins`, init);
    Object.defineProperty(accessorRequest.headers, "get", {
      configurable: true,
      get: () => { accessorCalls += 1; return Headers.prototype.get; },
    });
    for (const candidate of [valueRequest, accessorRequest]) {
      const response = await route.POST(candidate, { params: Promise.resolve({ projectId: "project-1" }) });
      expect(response.status).toBe(400);
      expect(await json(response)).toEqual({ error: "request_invalid" });
    }
    expect(valueCalls).toBe(0);
    expect(accessorCalls).toBe(0);
    expect(resolutions).toBe(0);
  });

  it("authenticates through the controller and reports partial availability exactly", async () => {
    const execute = vi.fn(async (candidate: Request, input: unknown) => {
      expect(candidate).toBeInstanceOf(NextRequest);
      expect(input).toEqual({ catalogDigest });
      return [catalogItem];
    });
    const response = await createCatalogCollectionRoute({ execute })(request(`/api/v1/plugins?catalogDigest=${catalogDigest}`));
    expect(response.status).toBe(200);
    expect(await json(response)).toMatchObject({
      items: [{ status: "partially_available", components: [{ availability: { status: "unavailable", reason: "provider_unavailable" } }] }],
    });
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(response.headers.get("x-content-type-options")).toBe("nosniff");
  });

  it("never serializes rejected admission as available even when provider availability is available", async () => {
    const rejected = {
      ...catalogItem,
      license: { declaration: "MIT", decision: "rejected" as const, reason: "license_rejected" as const },
      admission: "rejected" as const,
      reason: "license_rejected" as const,
      components: [{
        ...catalogItem.components[0],
        admission: { status: "rejected" as const, reason: "license_rejected" as const, policyVersion: "policy-v1" },
        availability: { status: "available" as const },
      }],
    };
    const response = await createCatalogCollectionRoute({ execute: async () => [rejected] })(request(`/api/v1/plugins?catalogDigest=${catalogDigest}`));
    expect(response.status).toBe(200);
    expect(await json(response)).toMatchObject({
      items: [{ status: "rejected", reason: "license_rejected", components: [{ status: "rejected", reason: "license_rejected" }] }],
    });
  });

  it.each([
    ["available", undefined], ["installed", undefined], ["review_required", "write_review_required"],
    ["partially_available", "provider_unavailable"], ["unavailable", "provider_unavailable"],
    ["unsupported", "component_unsupported"], ["migration_required", "migration_conflict"],
    ["error", "provider_unavailable"], ["invalid", "manifest_invalid"],
  ] as const)("serializes the authoritative component status/reason family %s", async (status, reason) => {
    const selected = { ...catalogItem, components: [{ ...catalogItem.components[0], availability: { status, ...(reason === undefined ? {} : { reason }) } }] };
    const response = await createCatalogCollectionRoute({ execute: async () => [selected] })(request(`/api/v1/plugins?catalogDigest=${catalogDigest}`));
    expect(response.status).toBe(200);
  });

  it.each([
    ["available", "provider_unavailable"], ["installed", "license_rejected"], ["review_required", "migration_conflict"],
    ["unavailable", "license_rejected"], ["unsupported", "provider_unavailable"], ["migration_required", "provider_unavailable"],
    ["error", "migration_conflict"], ["invalid", "write_review_required"],
  ] as const)("rejects contradictory component status/reason %s + %s", async (status, reason) => {
    const selected = { ...catalogItem, components: [{ ...catalogItem.components[0], availability: { status, reason } }] };
    const response = await createCatalogCollectionRoute({ execute: async () => [selected] })(request(`/api/v1/plugins?catalogDigest=${catalogDigest}`));
    expect(response.status).toBe(500);
    expect(await json(response)).toEqual({ error: "internal_error" });
  });

  it.each([
    "source_mismatch", "manifest_invalid", "path_escape", "digest_mismatch", "provider_unavailable", "credential_missing", "parity_failed", "rollback_unavailable",
  ] as const)("serializes the authoritative error reason family %s", async (reason) => {
    const selected = { ...catalogItem, components: [{ ...catalogItem.components[0], availability: { status: "error" as const, reason } }] };
    const response = await createCatalogCollectionRoute({ execute: async () => [selected] })(request(`/api/v1/plugins?catalogDigest=${catalogDigest}`));
    expect(response.status).toBe(200);
  });

  it.each([
    ["review_required", "license_review_required"], ["review_required", "write_review_required"],
    ["rejected", "license_rejected"], ["rejected", "http_mcp_not_admitted"], ["rejected", "process_not_admitted"],
    ["rejected", "hook_not_admitted"], ["rejected", "component_unsupported"],
  ] as const)("serializes the authoritative admission reason family %s + %s", async (admission, reason) => {
    const selected = { ...catalogItem, admission, reason, license: { declaration: "MIT", decision: admission, reason }, components: [] };
    const response = await createCatalogCollectionRoute({ execute: async () => [selected] })(request(`/api/v1/plugins?catalogDigest=${catalogDigest}`));
    expect(response.status).toBe(200);
  });

  it("validates and serializes all 180 entries from the pinned full catalog contract", async () => {
    const lock = catalogLockFixture as unknown as PluginCatalogLock;
    const authorization: IPluginApiAuthorizationPolicy = { authenticate: async () => ({ kind: "user", userId: "user-1" }), authorizeProject: async () => undefined };
    const useCase = new ListPluginCatalogUseCase({
      pluginApiAuthorizationPolicy: authorization,
      pluginsRepository: { getCatalog: async () => lock } as unknown as IPluginsRepository,
    });
    const response = await createCatalogCollectionRoute(new PluginCatalogController({ pluginApiAuthorizationPolicy: authorization, listPluginCatalogUseCase: useCase }))(
      request(`/api/v1/plugins?catalogDigest=${catalogDigest}`),
    );
    expect(response.status).toBe(200);
    const body = await response.json() as { items: unknown[] };
    expect(body.items).toHaveLength(180);
  });

  it("awaits Next 15 params and returns one schema-validated catalog item", async () => {
    const execute = vi.fn(async () => [catalogItem]);
    const response = await createCatalogItemRoute({ execute })(
      request(`/api/v1/plugins/airtable?catalogDigest=${catalogDigest}`),
      { params: Promise.resolve({ pluginName: "airtable" }) },
    );
    expect(response.status).toBe(200);
    expect(await json(response)).toMatchObject({ pluginName: "airtable", status: "partially_available" });
  });

  it("rejects duplicate, unknown and malformed query input without calling the controller", async () => {
    const execute = vi.fn();
    const route = createCatalogCollectionRoute({ execute });
    for (const suffix of [
      `catalogDigest=${catalogDigest}&catalogDigest=${catalogDigest}`,
      `catalogDigest=${catalogDigest}&extra=x`,
      "catalogDigest=not-a-digest",
    ]) {
      const response = await route(request(`/api/v1/plugins?${suffix}`));
      expect(response.status).toBe(400);
      expect(await json(response)).toEqual({ error: "request_invalid" });
    }
    expect(execute).not.toHaveBeenCalled();
  });

  it("rejects a wrong HTTP method or noncanonical route path before the controller", async () => {
    const execute = vi.fn();
    const route = createCatalogCollectionRoute({ execute });
    for (const candidate of [
      request(`/api/v1/plugins?catalogDigest=${catalogDigest}`, { method: "POST" }),
      request(`/api/v1/not-plugins?catalogDigest=${catalogDigest}`),
    ]) {
      const response = await route(candidate);
      expect(response.status).toBe(400);
      expect(await json(response)).toEqual({ error: "request_invalid" });
    }
    expect(execute).not.toHaveBeenCalled();
  });

  it("rejects encoded, double-encoded, case-shifted and noncanonical raw URLs before resolution", async () => {
    let resolutions = 0;
    const route = createCatalogCollectionRoute(async () => { resolutions += 1; return { execute: async () => [catalogItem] }; });
    for (const raw of [
      `/api/v1/%70lugins?catalogDigest=${catalogDigest}`,
      `/api/v1/%252e/plugins?catalogDigest=${catalogDigest}`,
      `/api/v1/plugins%00?catalogDigest=${catalogDigest}`,
      `/api/v1%2fplugins?catalogDigest=${catalogDigest}`,
      `/api/v1/plugins?%63atalogDigest=${catalogDigest}`,
      `/api/v1/plugins?catalogDigest=%2561${catalogDigest.slice(2)}`,
      `/api/V1/plugins?catalogDigest=${catalogDigest}`,
      `/api//v1/plugins?catalogDigest=${catalogDigest}`,
    ]) {
      const response = await route(request(raw));
      expect(response.status).toBe(400);
      expect(await json(response)).toEqual({ error: "request_invalid" });
    }
    for (const raw of [
      `/api/v1/%2e/plugins?catalogDigest=${catalogDigest}`,
      `/api/v1/x/../plugins?catalogDigest=${catalogDigest}`,
      `/api\\v1\\plugins?catalogDigest=${catalogDigest}`,
    ]) {
      const response = await route(requestWithUnnormalizedRawUrl(raw));
      expect(response.status).toBe(400);
      expect(await json(response)).toEqual({ error: "request_invalid" });
    }
    expect(resolutions).toBe(0);
  });

  it("turns invalid controller output and malicious exceptions into one safe internal error", async () => {
    const leaked = "Bearer secret-token C:\\private\\plugins Error: database exploded";
    for (const execute of [vi.fn(async () => [{ ...catalogItem, sourcePath: "C:\\private" }]), vi.fn(async () => { throw new Error(leaked); })]) {
      const response = await createCatalogCollectionRoute({ execute })(request(`/api/v1/plugins?catalogDigest=${catalogDigest}`));
      expect(response.status).toBe(500);
      const text = await response.text();
      expect(JSON.parse(text)).toEqual({ error: "internal_error" });
      expect(text).not.toContain("secret-token");
    }
  });

  it("rejects accessor and proxied success output without invoking traps", async () => {
    let accessorCalls = 0;
    let proxyCalls = 0;
    const accessor = { ...catalogItem } as Record<string, unknown>;
    Object.defineProperty(accessor, "name", { enumerable: true, get: () => { accessorCalls += 1; return "airtable"; } });
    const proxied = new Proxy([catalogItem], { get: (target, key, receiver) => { proxyCalls += 1; return Reflect.get(target, key, receiver); } });
    for (const output of [[accessor], proxied]) {
      const response = await createCatalogCollectionRoute({ execute: async () => output })(request(`/api/v1/plugins?catalogDigest=${catalogDigest}`));
      expect(response.status).toBe(500);
      expect(await json(response)).toEqual({ error: "internal_error" });
    }
    expect(accessorCalls).toBe(0);
    // Promise resolution performs the single mandatory thenable probe; route validation performs no additional proxy reads.
    expect(proxyCalls).toBe(1);
  });

  it("maps stable authentication, authorization and not-found errors without leaking messages", async () => {
    for (const [reason, status] of [["unauthenticated", 401], ["forbidden", 403], ["plugin_not_found", 404]] as const) {
      const execute = vi.fn(async () => { throw new Error(reason); });
      const response = await createCatalogCollectionRoute({ execute })(request(`/api/v1/plugins?catalogDigest=${catalogDigest}`));
      expect(response.status).toBe(status);
      expect(await json(response)).toEqual({ error: reason });
    }
  });

  it("uses genuine NextRequest cookie and bearer authentication before the catalog use case", async () => {
    for (const mode of ["cookie", "bearer"] as const) {
      const calls: PluginApiIdentity[] = [];
      const authorization = new Auth0PluginApiAuthorizationPolicy({
        pluginUserSessionProvider: { getUserId: async () => mode === "cookie" ? "user-1" : null },
        pluginProjectApiKeyVerifier: { verify: async () => null },
        pluginUserTokenVerifier: { verify: async (token) => token === "valid-user-token" ? "user-1" : null },
        projectMembersRepository: { exists: async () => true } as never,
        pluginAuthEnabled: true,
      });
      const controller = new PluginCatalogController({
        pluginApiAuthorizationPolicy: authorization,
        listPluginCatalogUseCase: { execute: async ({ identity }: { identity: PluginApiIdentity }) => { calls.push(identity); return [catalogItem]; } } as unknown as ListPluginCatalogUseCase,
      });
      const response = await createCatalogCollectionRoute(controller)(request(`/api/v1/plugins?catalogDigest=${catalogDigest}`, {
        headers: mode === "cookie" ? { cookie: "appSession=opaque" } : { authorization: "Bearer valid-user-token" },
      }));
      expect(response.status).toBe(200);
      expect(calls).toEqual([{ kind: "user", userId: "user-1" }]);
    }

    const useCase = vi.fn();
    const deniedController = new PluginCatalogController({
      pluginApiAuthorizationPolicy: new Auth0PluginApiAuthorizationPolicy({
        pluginUserSessionProvider: { getUserId: async () => null }, pluginProjectApiKeyVerifier: { verify: async () => null },
        pluginUserTokenVerifier: { verify: async () => null }, projectMembersRepository: { exists: async () => false } as never,
        pluginAuthEnabled: true,
      }),
      listPluginCatalogUseCase: { execute: useCase } as unknown as ListPluginCatalogUseCase,
    });
    const denied = await createCatalogCollectionRoute(deniedController)(request(`/api/v1/plugins?catalogDigest=${catalogDigest}`));
    expect(denied.status).toBe(401);
    expect(useCase).not.toHaveBeenCalled();
  });
});

describe("versioned project plugin routes", () => {
  it("requires exactly one valid idempotency key before installation controller invocation", async () => {
    const install = vi.fn();
    const route = createProjectPluginsRoute({ install });
    for (const headers of [
      undefined,
      { "Idempotency-Key": "one,two" },
      { "Idempotency-Key": "one:two" },
      { "Idempotency-Key": "one=two" },
      { "Idempotency-Key": "contains space" },
    ]) {
      const response = await route.POST(request("/api/v1/projects/project-1/plugins", {
        method: "POST", headers: { "content-type": "application/json", ...headers },
        body: JSON.stringify({ pluginName: "airtable", catalogDigest, expectedRevision: 0 }),
      }), { params: Promise.resolve({ projectId: "project-1" }) });
      expect(response.status).toBe(400);
      expect(await json(response)).toEqual({ error: headers === undefined ? "idempotency_key_required" : "idempotency_key_invalid" });
    }
    expect(install).not.toHaveBeenCalled();
  });

  it("parses a strict bounded JSON installation request and emits a typed receipt", async () => {
    const receipt = Object.freeze({ type: "install" as const, receiptId: "receipt-1", projectId: "project-1", pluginName: "airtable", status: "success" as const, redactions: Object.freeze([]) });
    const install = vi.fn(async (_request: Request, input: unknown) => {
      expect(input).toEqual({ projectId: "project-1", pluginName: "airtable", catalogDigest, expectedRevision: 0, idempotencyKey: "install-1" });
      return receipt;
    });
    const response = await createProjectPluginsRoute({ install }).POST(request("/api/v1/projects/project-1/plugins", {
      method: "POST", headers: { "content-type": "application/json", "Idempotency-Key": "install-1" },
      body: JSON.stringify({ pluginName: "airtable", catalogDigest, expectedRevision: 0 }),
    }), { params: Promise.resolve({ projectId: "project-1" }) });
    expect(response.status).toBe(201);
    expect(await json(response)).toEqual(receipt);
  });

  it("rejects content type, unknown fields, prototype keys and oversized bodies before the controller", async () => {
    const install = vi.fn();
    const route = createProjectPluginsRoute({ install });
    const cases = [
      { headers: { "content-type": "text/plain", "Idempotency-Key": "install-1" }, body: "{}" },
      { headers: { "content-type": "application/json", "Idempotency-Key": "install-1" }, body: JSON.stringify({ pluginName: "airtable", catalogDigest, expectedRevision: 0, extra: true }) },
      { headers: { "content-type": "application/json", "Idempotency-Key": "install-1" }, body: `{"pluginName":"airtable","catalogDigest":"${catalogDigest}","expectedRevision":0,"__proto__":{}}` },
      { headers: { "content-type": "application/json", "Idempotency-Key": "install-1" }, body: `{"pluginName":"${"a".repeat(70_000)}","catalogDigest":"${catalogDigest}","expectedRevision":0}` },
    ];
    for (const init of cases) {
      const response = await route.POST(request("/api/v1/projects/project-1/plugins", { method: "POST", ...init }), { params: Promise.resolve({ projectId: "project-1" }) });
      expect(response.status).toBe(400);
      expect(await json(response)).toEqual({ error: "request_invalid" });
    }
    expect(install).not.toHaveBeenCalled();
  });

  it("rejects duplicate JSON keys at every depth including escaped-equivalent keys", async () => {
    const install = vi.fn();
    const route = createProjectPluginsRoute({ install });
    const bodies = [
      `{"pluginName":"airtable","pluginName":"other","catalogDigest":"${catalogDigest}","expectedRevision":0}`,
      `{"pluginName":"airtable","catalogDigest":"${catalogDigest}","expectedRevision":0,"nested":{"x":1,"x":2}}`,
      `{"pluginName":"airtable","\\u0070luginName":"other","catalogDigest":"${catalogDigest}","expectedRevision":0}`,
    ];
    for (const body of bodies) {
      const response = await route.POST(request("/api/v1/projects/project-1/plugins", {
        method: "POST", headers: { "content-type": "application/json", "Idempotency-Key": "install-1" }, body,
      }), { params: Promise.resolve({ projectId: "project-1" }) });
      expect(response.status).toBe(400);
      expect(await json(response)).toEqual({ error: "request_invalid" });
    }
    expect(install).not.toHaveBeenCalled();
  });

  it("bounds a hanging request body by deadline and cancels it without resolving dependencies", async () => {
    let cancels = 0;
    let resolutions = 0;
    const stream = new ReadableStream<Uint8Array>({ pull: () => new Promise<void>(() => undefined), cancel: () => { cancels += 1; } });
    const hanging = new NextRequest("https://rowboat.invalid/api/v1/projects/project-1/plugins", {
      method: "POST", headers: { "content-type": "application/json", "Idempotency-Key": "install-1" }, body: stream,
      duplex: "half",
    } as unknown as ConstructorParameters<typeof NextRequest>[1]);
    const route = createProjectPluginsRoute(async () => { resolutions += 1; return { install: vi.fn() }; }, { bodyReadTimeoutMs: 20 });
    const response = await route.POST(hanging, { params: Promise.resolve({ projectId: "project-1" }) });
    expect(response.status).toBe(408);
    expect(await json(response)).toEqual({ error: "request_timeout" });
    expect(resolutions).toBe(0);
    expect(cancels).toBe(1);
  });

  it("stops a hanging body on request abort with no controller call or unhandled rejection", async () => {
    const aborter = new AbortController();
    let cancels = 0;
    const unhandled: unknown[] = [];
    const onUnhandled = (reason: unknown) => { unhandled.push(reason); };
    process.on("unhandledRejection", onUnhandled);
    try {
      const stream = new ReadableStream<Uint8Array>({ pull: () => new Promise<void>(() => undefined), cancel: () => { cancels += 1; } });
      const pending = createProjectPluginsRoute({ install: vi.fn() }, { bodyReadTimeoutMs: 1_000 }).POST(new NextRequest(
        "https://rowboat.invalid/api/v1/projects/project-1/plugins",
        { method: "POST", headers: { "content-type": "application/json", "Idempotency-Key": "install-1" }, body: stream, signal: aborter.signal, duplex: "half" } as unknown as ConstructorParameters<typeof NextRequest>[1],
      ), { params: Promise.resolve({ projectId: "project-1" }) });
      setTimeout(() => aborter.abort(), 5);
      const response = await pending;
      expect(response.status).toBe(400);
      expect(await json(response)).toEqual({ error: "request_aborted" });
      await new Promise<void>((resolve) => setTimeout(resolve, 0));
      expect(cancels).toBe(1);
      expect(unhandled).toEqual([]);
    } finally { process.off("unhandledRejection", onUnhandled); }
  });

  it("previews a project plugin with async params and does not accept body/path identity fields", async () => {
    const preview = vi.fn(async (_request: Request, input: unknown) => {
      expect(input).toEqual({ projectId: "project-1", pluginName: "airtable", catalogDigest });
      return {
        pluginName: "airtable", catalogDigest, sourceCommit: "1".repeat(40), policyVersion: "policy-v1",
        license: { declaration: "MIT", decision: "admitted" }, admission: "admitted", components: catalogItem.components,
        credentialSlots: [{ name: "AIRTABLE_TOKEN", configured: false }],
      };
    });
    const response = await createProjectPluginRoute({ preview, setEnabled: vi.fn() }).GET(
      request(`/api/v1/projects/project-1/plugins/airtable?catalogDigest=${catalogDigest}`),
      { params: Promise.resolve({ projectId: "project-1", pluginName: "airtable" }) },
    );
    expect(response.status).toBe(200);
    expect(await json(response)).toMatchObject({ pluginName: "airtable", status: "partially_available", license: { declaration: "MIT" } });
  });

  it("requires idempotency before PATCH and sends only path identity plus strict body fields", async () => {
    const installation = Object.freeze({
      id: "installation-1", projectId: "project-1", pluginName: "airtable", pluginVersion: "1.0.0",
      sourceCommit: "1".repeat(40), manifestDigest: digest("b"), treeDigest: digest("c"), policyVersion: "policy-v1",
      enabled: false, revision: 2,
    });
    const setEnabled = vi.fn(async (_request: Request, input: unknown) => {
      expect(input).toEqual({ projectId: "project-1", pluginName: "airtable", catalogDigest, enabled: false, expectedRevision: 1, idempotencyKey: "toggle-1" });
      return installation;
    });
    const route = createProjectPluginRoute({ preview: vi.fn(), setEnabled });
    const missing = await route.PATCH(request("/api/v1/projects/project-1/plugins/airtable", {
      method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify({ catalogDigest, enabled: false, expectedRevision: 1 }),
    }), { params: Promise.resolve({ projectId: "project-1", pluginName: "airtable" }) });
    expect(missing.status).toBe(400);
    expect(await json(missing)).toEqual({ error: "idempotency_key_required" });
    const response = await route.PATCH(request("/api/v1/projects/project-1/plugins/airtable", {
      method: "PATCH", headers: { "content-type": "application/json", "Idempotency-Key": "toggle-1" }, body: JSON.stringify({ catalogDigest, enabled: false, expectedRevision: 1 }),
    }), { params: Promise.resolve({ projectId: "project-1", pluginName: "airtable" }) });
    expect(response.status).toBe(200);
    expect(await json(response)).toEqual({
      projectId: "project-1", pluginName: "airtable", pluginVersion: "1.0.0", catalogDigest,
      policyVersion: "policy-v1", enabled: false, revision: 2,
    });
  });

  it("lists project plugins through the authorized controller", async () => {
    const list = vi.fn(async (_request: Request, input: unknown) => {
      expect(input).toEqual({ projectId: "project-1", catalogDigest });
      return [];
    });
    const route = createProjectPluginsRoute({ install: vi.fn(), list });
    const response = await route.GET(request(`/api/v1/projects/project-1/plugins?catalogDigest=${catalogDigest}`), { params: Promise.resolve({ projectId: "project-1" }) });
    expect(response.status).toBe(200);
    expect(await json(response)).toEqual({ items: [] });
  });

  it("accepts a genuine matching project bearer only through controller and use-case authorization", async () => {
    const lock = catalogLockFixture as unknown as PluginCatalogLock;
    const authorization = new Auth0PluginApiAuthorizationPolicy({
      pluginUserSessionProvider: { getUserId: async () => null },
      pluginProjectApiKeyVerifier: { verify: async (token) => token === "project-token" ? "project-1" : null },
      pluginUserTokenVerifier: { verify: async () => null }, projectMembersRepository: { exists: async () => false } as never,
      pluginAuthEnabled: true,
    });
    const listProjectPluginsUseCase = new ListProjectPluginsUseCase({
      pluginApiAuthorizationPolicy: authorization,
      pluginsRepository: { getCatalog: async () => lock, listInstallations: async () => [] } as unknown as IPluginsRepository,
    });
    const controller = new PluginInstallationController({
      pluginApiAuthorizationPolicy: authorization, listProjectPluginsUseCase,
      previewPluginInstallationUseCase: {} as PreviewPluginInstallationUseCase,
      installPluginUseCase: {} as InstallPluginUseCase,
      setPluginEnabledUseCase: {} as SetPluginEnabledUseCase,
    });
    const response = await createProjectPluginsRoute(controller).GET(request(`/api/v1/projects/project-1/plugins?catalogDigest=${catalogDigest}`, {
      headers: { authorization: "Bearer project-token" },
    }), { params: Promise.resolve({ projectId: "project-1" }) });
    expect(response.status).toBe(200);
    expect(await json(response)).toEqual({ items: [] });
  });
});

describe("project plugin list service boundary", () => {
  const lock = catalogLockFixture as unknown as PluginCatalogLock;
  const user: PluginApiIdentity = Object.freeze({ kind: "user", userId: "user-1" });

  function dependencies(options: { reject?: boolean; installations?: readonly PluginInstallation[] } = {}) {
    let reads = 0;
    const authorization: IPluginApiAuthorizationPolicy = {
      authenticate: async () => user,
      authorizeProject: async () => { if (options.reject) throw new Error("forbidden"); },
    };
    const repository = {
      getCatalog: async () => { reads += 1; return lock; },
      listInstallations: async () => { reads += 1; return options.installations ?? []; },
    } as unknown as IPluginsRepository;
    return { authorization, repository, reads: () => reads };
  }

  it("authorizes before any project or catalog repository read", async () => {
    const selected = dependencies({ reject: true });
    const useCase = new ListProjectPluginsUseCase({ pluginsRepository: selected.repository, pluginApiAuthorizationPolicy: selected.authorization });
    await expect(useCase.execute({ identity: user, projectId: "project-1", catalogDigest: lock.catalogDigest })).rejects.toThrow("forbidden");
    expect(selected.reads()).toBe(0);
  });

  it("returns deeply frozen deterministic empty and nonempty safe project lists", async () => {
    const empty = dependencies();
    await expect(new ListProjectPluginsUseCase({ pluginsRepository: empty.repository, pluginApiAuthorizationPolicy: empty.authorization })
      .execute({ identity: user, projectId: "project-1", catalogDigest: lock.catalogDigest })).resolves.toEqual([]);

    const entry = lock.entries.find((candidate) => candidate.name === "airtable")!;
    const installation: PluginInstallation = Object.freeze({
      id: "server-owned-installation-id", projectId: "project-1", pluginName: entry.pluginName,
      pluginVersion: entry.pluginVersion, sourceCommit: entry.sourceCommit, manifestDigest: entry.manifestDigest,
      treeDigest: entry.treeDigest, policyVersion: entry.policyVersion, enabled: true, revision: 3,
    });
    const populated = dependencies({ installations: [installation] });
    const result = await new ListProjectPluginsUseCase({ pluginsRepository: populated.repository, pluginApiAuthorizationPolicy: populated.authorization })
      .execute({ identity: user, projectId: "project-1", catalogDigest: lock.catalogDigest });
    expect(result).toHaveLength(1);
    expect(result[0]).toMatchObject({ pluginName: "airtable", enabled: true, revision: 3, catalogDigest: lock.catalogDigest });
    expect(JSON.stringify(result)).not.toContain("server-owned-installation-id");
    expect(JSON.stringify(result)).not.toContain("sourceCommit");
    expect(Object.isFrozen(result)).toBe(true);
    expect(Object.isFrozen(result[0]?.components)).toBe(true);
  });
});

// The install body carries an optional component selection. Leaving it out
// keeps every existing client working and means "every component".

describe("component-scoped installation over the versioned route", () => {
  function post(body: string, install: (request: Request, input: unknown) => Promise<unknown>) {
    return createProjectPluginsRoute({ install }).POST(request("/api/v1/projects/project-1/plugins", {
      method: "POST", headers: { "content-type": "application/json", "Idempotency-Key": "install-1" }, body,
    }), { params: Promise.resolve({ projectId: "project-1" }) });
  }

  it("forwards a selected component digest to the installation controller", async () => {
    const receipt = Object.freeze({ type: "install" as const, receiptId: "receipt-1", projectId: "project-1", pluginName: "airtable", status: "success" as const, redactions: Object.freeze([]) });
    const install = vi.fn(async (_request: Request, input: unknown) => {
      expect(input).toEqual({
        projectId: "project-1", pluginName: "airtable", catalogDigest, expectedRevision: 0,
        idempotencyKey: "install-1", componentDigests: [componentDigest],
      });
      return receipt;
    });
    const response = await post(JSON.stringify({ pluginName: "airtable", catalogDigest, expectedRevision: 0, componentDigests: [componentDigest] }), install);
    expect(response.status).toBe(201);
    expect(await json(response)).toEqual(receipt);
  });

  it("still rejects an unknown field beside the optional selection", async () => {
    const install = vi.fn();
    const response = await post(JSON.stringify({ pluginName: "airtable", catalogDigest, expectedRevision: 0, componentDigests: [componentDigest], extra: true }), install);
    expect(response.status).toBe(400);
    expect(await json(response)).toEqual({ error: "request_invalid" });
    expect(install).not.toHaveBeenCalled();
  });

  it("still requires every mandatory field when a selection is supplied", async () => {
    const install = vi.fn();
    const response = await post(JSON.stringify({ pluginName: "airtable", catalogDigest, componentDigests: [componentDigest] }), install);
    expect(response.status).toBe(400);
    expect(await json(response)).toEqual({ error: "request_invalid" });
    expect(install).not.toHaveBeenCalled();
  });
});
