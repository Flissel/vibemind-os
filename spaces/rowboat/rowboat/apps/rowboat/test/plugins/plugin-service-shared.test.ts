import { describe, expect, it } from "vitest";
import type { PluginCatalogEntry } from "@/src/application/repositories/plugins.repository.interface";
import {
  admissionsFrom, assertSelectionAdmitted, entryComponentDigests, installationFrom, requiredCredentialNames,
} from "@/src/application/use-cases/plugins/plugin-service.shared";
import { componentSelectionDigest, requestedComponentSelection } from "@/src/application/use-cases/plugins/plugin-component-selection";
import {
  OPENAI_API_KEY_CREDENTIAL_REFERENCE,
  deriveConnectorReference,
  deriveOAuthBearerReference,
} from "@rowboat/openai-plugin-runtime";
import catalogLockFixture from "../../../../config/openai-plugin-catalog.lock.json";

// `requiredCredentialNames` feeds the install dialog's "credentials required"
// list directly (see `PreviewPluginInstallationUseCase`). It must not surface
// a slot from a component the catalog does not admit, or from a non-`mcp`
// component, and it must not miss an oauth-shaped credential requirement just
// because it only reads env-var-style slot names. The names it reports must
// be the exact names OpenFang resolves - the same derivations the OpenFang
// credential resolver and the connector-bridge provider use - never the raw
// oauth resource URL or a silent "nothing" for a component that in fact
// needs a credential.

/** Fails the test loudly rather than smuggling `undefined` into an
 *  `.toEqual()` expectation - these URLs are fixture data known to derive. */
function mustDeriveOAuthBearerReference(resourceUrl: string): string {
  const derived = deriveOAuthBearerReference(resourceUrl);
  if (derived === undefined) throw new Error(`test fixture: "${resourceUrl}" was expected to derive an oauth bearer reference`);
  return derived;
}

interface RawCatalogLock {
  readonly entries: readonly PluginCatalogEntry[];
}

const pinnedLock = catalogLockFixture as unknown as RawCatalogLock;

function pinnedEntry(pluginName: string): PluginCatalogEntry {
  const found = pinnedLock.entries.find((entry) => entry.pluginName === pluginName);
  if (found === undefined) throw new Error(`fixture catalog is missing plugin "${pluginName}"`);
  return found;
}

const POLICY_VERSION = "rowboat-plugin-policy-v1";
const DIGEST = "a".repeat(64);

interface SyntheticComponentSpec {
  readonly kind: "mcp" | "skill" | "app";
  readonly admissionStatus: "admitted" | "review_required" | "rejected";
  readonly transport?: "http" | "process";
  readonly credentialSlots?: readonly string[];
  readonly oauthResource?: string;
  readonly bearerTokenEnvVar?: string;
  /** `app` components only: the id the catalog's `AppDeclarationSchema`
   *  validates - `connector_...`, `asdk_app_...`, or `templated_apps_...`. */
  readonly connectorId?: string;
  readonly componentName?: string;
}

function syntheticEntry(components: readonly SyntheticComponentSpec[]): PluginCatalogEntry {
  return {
    name: "synthetic", pluginName: "synthetic", pluginVersion: "1.0.0",
    sourceUrl: "https://github.com/openai/plugins.git", sourceCommit: "0".repeat(40),
    manifestDigest: DIGEST, treeDigest: DIGEST, importedAt: "2026-08-31T00:00:00.000Z",
    schemaVersion: "rowboat-plugin-schema-v1", policyVersion: POLICY_VERSION,
    admission: { status: "admitted", policyVersion: POLICY_VERSION },
    components: components.map((spec) => {
      const metadata: Record<string, unknown> = { digest: DIGEST, bindingDigest: DIGEST };
      if (spec.transport !== undefined) metadata.transport = spec.transport;
      // Mirrors `credentialSlotNames` in `component-discovery.ts`: the real
      // importer derives `credentialSlots` from `bearer_token_env_var` (and
      // `env_vars` for a process server), it is never authored separately.
      const derivedSlots = spec.credentialSlots
        ?? (spec.bearerTokenEnvVar === undefined ? undefined : [spec.bearerTokenEnvVar]);
      if (derivedSlots !== undefined) metadata.credentialSlots = derivedSlots;
      // A real `mcp`/`http` component always carries `mcpServer` (`url` is
      // required by `HttpMcpSchema`), whether or not it declares a
      // credential - so the synthetic fixture does too, to exercise the
      // "declares neither" fallback the same way a real cloudflare-shaped
      // entry does.
      if (spec.kind === "mcp" && (spec.transport ?? "http") === "http") {
        metadata.mcpServer = {
          type: "http",
          url: "https://example.invalid/mcp",
          ...(spec.oauthResource === undefined ? {} : { oauth_resource: spec.oauthResource }),
          ...(spec.bearerTokenEnvVar === undefined ? {} : { bearer_token_env_var: spec.bearerTokenEnvVar }),
        };
      }
      if (spec.kind === "app" && spec.connectorId !== undefined) {
        metadata.appDeclaration = { id: spec.connectorId };
      }
      return {
        component: { id: `${spec.kind}:synthetic`, name: spec.componentName ?? "Synthetic", kind: spec.kind, status: "available", metadata },
        admission: {
          status: spec.admissionStatus, policyVersion: POLICY_VERSION,
          ...(spec.admissionStatus === "admitted" ? {} : { reason: "process_not_admitted" }),
        },
      };
    }),
  } as unknown as PluginCatalogEntry;
}

describe("requiredCredentialNames", () => {
  it("yields nothing for codex-security: its only mcp component is rejected and process-transport", () => {
    const entry = pinnedEntry("codex-security");
    expect(requiredCredentialNames(entry)).toEqual([]);
  });

  it("yields the name OpenFang actually resolves for linear's oauth requirement, not the raw resource URL", () => {
    // linear's mcp component declares `oauth_resource` explicitly - see the
    // pinned lock - and has no bearer slot at all.
    const entry = pinnedEntry("linear");
    expect(requiredCredentialNames(entry)).toEqual([mustDeriveOAuthBearerReference("https://mcp.linear.app/mcp")]);
  });

  it("still yields the bearer credential slot name for github", () => {
    const entry = pinnedEntry("github");
    expect(requiredCredentialNames(entry)).toEqual(["GITHUB_PAT_TOKEN"]);
  });

  it("also yields the derived oauth reference for notion, the same shape as linear", () => {
    expect(requiredCredentialNames(pinnedEntry("notion"))).toEqual([mustDeriveOAuthBearerReference("https://mcp.notion.com")]);
  });

  it("yields the derived oauth reference for cloudflare, even though its mcp component declares neither oauth_resource nor a bearer slot", () => {
    // Per mcp-normalizer.ts: a server that declares neither credential is
    // still an OAuth-protected resource, at its own URL - "declares nothing"
    // must never read as "needs nothing" (D6 / the cloudflare gap in the
    // spec's gap table).
    const entry = pinnedEntry("cloudflare");
    expect(requiredCredentialNames(entry)).toEqual([mustDeriveOAuthBearerReference("https://mcp.cloudflare.com/mcp")]);
  });

  it("yields both the OpenAI API key and the per-app connector reference for canva, which has no mcp component at all", () => {
    // canva's only executable component is its `app` component (a
    // `connector_...` id); the connector-bridge provider resolves two
    // credentials for every call, `OPENAI_API_KEY` and `CONNECTOR_<APP>`.
    const entry = pinnedEntry("canva");
    expect(requiredCredentialNames(entry)).toEqual(
      [OPENAI_API_KEY_CREDENTIAL_REFERENCE, deriveConnectorReference("canva")].sort(),
    );
  });

  it("derives the oauth reference from the mcp server's own url when neither oauth_resource nor a bearer slot is declared", () => {
    const entry = syntheticEntry([
      { kind: "mcp", admissionStatus: "admitted", transport: "http" },
    ]);
    expect(requiredCredentialNames(entry)).toEqual([mustDeriveOAuthBearerReference("https://example.invalid/mcp")]);
  });

  it("yields both credential names for an admitted connector app", () => {
    const entry = syntheticEntry([
      { kind: "app", admissionStatus: "admitted", connectorId: "connector_deadbeef", componentName: "acme" },
    ]);
    expect(requiredCredentialNames(entry)).toEqual(
      [OPENAI_API_KEY_CREDENTIAL_REFERENCE, deriveConnectorReference("acme")].sort(),
    );
  });

  it("contributes nothing from an admitted app component that is not a connector id (asdk_app_/templated_apps_)", () => {
    const entry = syntheticEntry([
      { kind: "app", admissionStatus: "admitted", connectorId: "asdk_app_deadbeef", componentName: "acme" },
    ]);
    expect(requiredCredentialNames(entry)).toEqual([]);
  });

  it("contributes nothing from a review_required connector app", () => {
    const entry = syntheticEntry([
      { kind: "app", admissionStatus: "review_required", connectorId: "connector_deadbeef", componentName: "acme" },
    ]);
    expect(requiredCredentialNames(entry)).toEqual([]);
  });

  it("yields nothing for figma: its mcp component is itself still review_required", () => {
    // figma's mcp component declares the same oauth_resource shape as
    // linear's and notion's (see mcpServer.oauth_resource in the pinned
    // lock), but the whole plugin - including this component - is still
    // under license review, so it is not admitted yet and contributes
    // nothing, same as any other unadmitted component.
    expect(requiredCredentialNames(pinnedEntry("figma"))).toEqual([]);
  });

  it("contributes nothing from a rejected mcp/http component, even though it declares a bearer slot", () => {
    const entry = syntheticEntry([
      { kind: "mcp", admissionStatus: "rejected", transport: "http", credentialSlots: ["SHOULD_NOT_APPEAR"], bearerTokenEnvVar: "SHOULD_NOT_APPEAR" },
    ]);
    expect(requiredCredentialNames(entry)).toEqual([]);
  });

  it("contributes nothing from an admitted non-mcp component, even though it declares a credential slot", () => {
    const entry = syntheticEntry([
      { kind: "skill", admissionStatus: "admitted", credentialSlots: ["SHOULD_NOT_APPEAR"] },
    ]);
    expect(requiredCredentialNames(entry)).toEqual([]);
  });

  it("contributes nothing from an admitted mcp component whose transport is process, not http", () => {
    const entry = syntheticEntry([
      { kind: "mcp", admissionStatus: "admitted", transport: "process", credentialSlots: ["SHOULD_NOT_APPEAR"] },
    ]);
    expect(requiredCredentialNames(entry)).toEqual([]);
  });

  it("unions slots across multiple admitted mcp/http components, bearer and oauth alike", () => {
    const entry = syntheticEntry([
      { kind: "mcp", admissionStatus: "admitted", transport: "http", bearerTokenEnvVar: "SLOT_A" },
      { kind: "mcp", admissionStatus: "admitted", transport: "http", oauthResource: "https://example.invalid/oauth" },
    ]);
    expect(requiredCredentialNames(entry)).toEqual(
      ["SLOT_A", mustDeriveOAuthBearerReference("https://example.invalid/oauth")].sort(),
    );
  });
});

// Installation is component-scoped: the caller names the components it wants,
// and only those are admitted, bound and written. `github` is the case the
// whole-plugin gate could never install - exactly one of its eight components
// (`mcp .mcp.json#github`) is admitted, the other seven are `review_required`.

function pinnedComponentDigest(pluginName: string, componentId: string): string {
  const found = pinnedEntry(pluginName).components.find(({ component }) => component.id === componentId);
  if (found === undefined) throw new Error(`fixture catalog is missing "${componentId}" in "${pluginName}"`);
  const value = found.component.metadata.bindingDigest;
  if (typeof value !== "string") throw new Error(`fixture component "${componentId}" has no binding digest`);
  return value;
}

const GITHUB_MCP = pinnedComponentDigest("github", "mcp:.mcp.json#github");
const GITHUB_SKILL = pinnedComponentDigest("github", "skill:skills/github");
const GITHUB_APP = pinnedComponentDigest("github", "app:.app.json#github");
const LINEAR_MCP = pinnedComponentDigest("linear", "mcp:.mcp.json#linear");

describe("requestedComponentSelection", () => {
  it("canonicalises a caller selection to a frozen sorted list", () => {
    const selection = requestedComponentSelection([GITHUB_APP, GITHUB_MCP]);
    expect(selection).toEqual([GITHUB_APP, GITHUB_MCP].sort());
    expect(Object.isFrozen(selection)).toBe(true);
  });

  it.each([
    ["an empty selection", []],
    ["a duplicated digest", [GITHUB_MCP, GITHUB_MCP]],
    ["an uppercase digest", [GITHUB_MCP.toUpperCase()]],
    ["a truncated digest", [GITHUB_MCP.slice(0, 63)]],
    ["a non-string entry", [1]],
    ["a non-array selection", GITHUB_MCP],
  ])("rejects %s as request_invalid", (_case, value) => {
    expect(() => requestedComponentSelection(value)).toThrow("request_invalid");
  });
});

describe("componentSelectionDigest", () => {
  it("separates an absent selection from every explicit one and ignores caller order", () => {
    const absent = componentSelectionDigest(undefined);
    expect(absent).toMatch(/^[a-f0-9]{64}$/);
    expect(componentSelectionDigest([GITHUB_MCP])).not.toBe(absent);
    expect(componentSelectionDigest(requestedComponentSelection([GITHUB_APP, GITHUB_MCP])))
      .toBe(componentSelectionDigest(requestedComponentSelection([GITHUB_MCP, GITHUB_APP])));
    expect(componentSelectionDigest([GITHUB_MCP])).not.toBe(componentSelectionDigest([GITHUB_MCP, GITHUB_APP]));
  });
});

describe("assertSelectionAdmitted", () => {
  it("admits github's one admitted component even though seven others are review_required", () => {
    expect(() => assertSelectionAdmitted(pinnedEntry("github"), [GITHUB_MCP])).not.toThrow();
  });

  it("rejects a selection that reaches a review_required component", () => {
    expect(() => assertSelectionAdmitted(pinnedEntry("github"), [GITHUB_MCP, GITHUB_SKILL])).toThrow("component_not_admitted");
  });

  it("rejects a digest that belongs to another plugin", () => {
    expect(() => assertSelectionAdmitted(pinnedEntry("github"), [LINEAR_MCP])).toThrow("request_invalid");
    expect(() => assertSelectionAdmitted(pinnedEntry("github"), [GITHUB_MCP, LINEAR_MCP])).toThrow("request_invalid");
  });

  it("refuses an empty selection on an entry that has components, whoever derived it", () => {
    expect(() => assertSelectionAdmitted(pinnedEntry("github"), [])).toThrow("request_invalid");
  });

  it("still rejects the whole plugin when its license is not admitted", () => {
    const entry = {
      ...syntheticEntry([{ kind: "mcp", admissionStatus: "admitted", transport: "http" }]),
      admission: { status: "rejected", reason: "license_rejected", policyVersion: POLICY_VERSION },
    } as unknown as PluginCatalogEntry;
    expect(() => assertSelectionAdmitted(entry, [DIGEST])).toThrow("license_rejected");
  });

  it("reports the availability reason of a selected component that cannot run", () => {
    const base = syntheticEntry([{ kind: "mcp", admissionStatus: "admitted", transport: "http" }]);
    const entry = {
      ...base,
      components: base.components.map((selected) => ({
        ...selected,
        component: { ...selected.component, status: "unavailable", reason: "provider_unavailable" },
      })),
    } as unknown as PluginCatalogEntry;
    expect(() => assertSelectionAdmitted(entry, [DIGEST])).toThrow("provider_unavailable");
  });
});

describe("installationFrom and admissionsFrom", () => {
  it("binds and admits only the selected component", () => {
    const entry = pinnedEntry("github");
    const installation = installationFrom(entry, "project-1", [GITHUB_MCP]);
    expect(installation.providerBindings).toHaveLength(1);
    const admissions = admissionsFrom(entry, "installation-1", [GITHUB_MCP]);
    expect(admissions.map((admission) => admission.componentDigest)).toEqual([GITHUB_MCP]);
    expect(admissions[0]).toMatchObject({ installationId: "installation-1", componentKind: "mcp", status: "admitted" });
  });

  it("binds every provider-backed component when the whole plugin is selected", () => {
    const entry = pinnedEntry("github");
    const all = entryComponentDigests(entry);
    expect(all).toHaveLength(8);
    expect(installationFrom(entry, "project-1", all).providerBindings).toHaveLength(2);
    expect(admissionsFrom(entry, "installation-1", all)).toHaveLength(8);
  });
});
