import { PINNED_PLUGIN_CATALOG_DIGEST } from "@rowboat/openai-plugin-runtime";
import { classifyPluginOperation } from "@/src/application/services/plugin-operation-classifier";
import type { PluginCatalogController } from "@/src/interface-adapters/controllers/plugins/plugin-catalog.controller";
import type { PluginInstallationController } from "@/src/interface-adapters/controllers/plugins/plugin-installation.controller";
import type { PluginToolRuntime } from "@/src/application/services/plugin-tool-runtime";
import type { PluginToolAuthorizationContext } from "@/src/application/services/plugin-tool-runtime";
import type { PluginApiIdentity } from "@/src/application/policies/plugin-api-authorization.policy";
import type { PluginPreviewEnvelope } from "@/src/interface-adapters/actions/plugin-preview-envelope";
import type { PluginSessionController } from "@/src/interface-adapters/controllers/plugins/plugin-session.controller";

type PluginReplayLookupInput = PluginPreviewEnvelope;

// The runtime's own hard ceiling -- PluginToolRuntime's constructor caps
// timeoutMilliseconds there -- and the margin left, after a release decision
// returns, for the rest of the call (the release call's own request/poll
// overhead, then admission/credential/provider work).
const RUNTIME_DEADLINE_CEILING_MS = 300_000;
const RUNTIME_DEADLINE_MARGIN_MS = 60_000;
// The largest approval window the runtime can actually cover. Past this,
// window + RUNTIME_DEADLINE_MARGIN_MS would exceed RUNTIME_DEADLINE_CEILING_MS,
// so the runtime's own deadline would have to be capped at or below the
// window it is supposed to cover -- reproducing the exact defect this guard
// exists to prevent (an operator approving after the runtime has already
// timed the call out). Derived, not a bare literal, so the two stay tied if
// either changes.
const MAX_OPENFANG_APPROVAL_WINDOW_MS = RUNTIME_DEADLINE_CEILING_MS - RUNTIME_DEADLINE_MARGIN_MS;
const MIN_OPENFANG_APPROVAL_WINDOW_MS = 1_000;
const DEFAULT_OPENFANG_APPROVAL_WINDOW_MS = 120_000;
// Whole, non-negative digits only: a naive Number.parseInt("1500.75", 10)
// truncates to 1500 and would silently accept a fractional value.
const STRICT_INTEGER = /^[0-9]+$/;

/**
 * Resolves OPENFANG_APPROVAL_TIMEOUT_MS once, at composition, guarded: input
 * that is not a valid whole-millisecond duration -- missing, fractional,
 * zero, negative, empty, or not a number at all -- falls back to the default
 * rather than propagating NaN into arithmetic downstream (an unguarded NaN
 * here would make setTimeout(..., NaN) fire immediately, silently refusing
 * every write with no diagnostic). A valid duration above what the runtime
 * can actually cover is clamped down to the max instead of dropped to the
 * default: an operator who asked for a longer window should get as much of
 * it as the runtime allows, not have their configuration silently
 * overridden by an unrelated value.
 */
export function resolveOpenFangApprovalWindowMs(raw: string | undefined): number {
  if (raw === undefined || !STRICT_INTEGER.test(raw)) return DEFAULT_OPENFANG_APPROVAL_WINDOW_MS;
  const parsed = Number.parseInt(raw, 10);
  if (!Number.isSafeInteger(parsed) || parsed < MIN_OPENFANG_APPROVAL_WINDOW_MS) return DEFAULT_OPENFANG_APPROVAL_WINDOW_MS;
  return Math.min(parsed, MAX_OPENFANG_APPROVAL_WINDOW_MS);
}

/**
 * The runtime's own deadline for a call that may wait on a human release:
 * the approval window plus RUNTIME_DEADLINE_MARGIN_MS for the release
 * call's own overhead after a decision returns, capped at
 * RUNTIME_DEADLINE_CEILING_MS. Because resolveOpenFangApprovalWindowMs never
 * returns a window above MAX_OPENFANG_APPROVAL_WINDOW_MS, this is always
 * strictly greater than the window it is derived from.
 */
export function deriveRuntimeDeadlineMs(approvalWindowMs: number): number {
  return Math.min(RUNTIME_DEADLINE_CEILING_MS, approvalWindowMs + RUNTIME_DEADLINE_MARGIN_MS);
}

interface PluginControllers {
  readonly authenticate: (request: Request) => Promise<PluginApiIdentity>;
  readonly catalog: PluginCatalogController;
  readonly installation: PluginInstallationController;
  readonly findInstallReplay: (request: Request, input: PluginReplayLookupInput) => Promise<unknown | null>;
  readonly createToolRuntime: (authorizationContext: PluginToolAuthorizationContext | undefined) => PluginToolRuntime;
  readonly addPluginTool: (input: Readonly<{ identity: PluginApiIdentity; projectId: string; pluginName: string; componentDigest: string }>) => Promise<unknown>;
}

let controllers: Promise<PluginControllers> | undefined;
let sessionController: Promise<PluginSessionController> | undefined;

async function createPluginSessionController(): Promise<PluginSessionController> {
  const [policyModule, usersModule, apiKeysModule, membersModule, controllerModule] = await Promise.all([
    import("@/src/infrastructure/policies/auth0.plugin-api-authorization.policy"),
    import("@/src/infrastructure/repositories/mongodb.users.repository"),
    import("@/src/infrastructure/repositories/mongodb.api-keys.repository"),
    import("@/src/infrastructure/repositories/mongodb.project-members.repository"),
    import("@/src/interface-adapters/controllers/plugins/plugin-session.controller"),
  ]);
  const usersRepository = new usersModule.MongoDBUsersRepository();
  const authorization = new policyModule.Auth0PluginApiAuthorizationPolicy({
    pluginUserSessionProvider: new policyModule.Auth0PluginUserSessionProvider({ usersRepository }),
    pluginProjectApiKeyVerifier: new policyModule.ExistingProjectApiKeyVerifier({ apiKeysRepository: new apiKeysModule.MongoDBApiKeysRepository() }),
    pluginUserTokenVerifier: new policyModule.JoseAuth0UserTokenVerifier({ usersRepository }),
    projectMembersRepository: new membersModule.MongoDBProjectMembersRepository(),
    pluginAuthEnabled: process.env.USE_AUTH === "true",
  });
  return new controllerModule.PluginSessionController(authorization);
}

async function createPluginControllers(): Promise<PluginControllers> {
  const [database, pluginRepositoryModule, policyModule, usersModule, apiKeysModule, membersModule, catalogUseCaseModule,
    previewUseCaseModule, installUseCaseModule, enableUseCaseModule, listProjectUseCaseModule, catalogControllerModule,
    installationControllerModule, toolRuntimeModule, projectAuthorizationModule, pluginSharedModule] = await Promise.all([
    import("@/app/lib/mongodb"),
    import("@/src/infrastructure/repositories/mongodb.plugins.repository"),
    import("@/src/infrastructure/policies/auth0.plugin-api-authorization.policy"),
    import("@/src/infrastructure/repositories/mongodb.users.repository"),
    import("@/src/infrastructure/repositories/mongodb.api-keys.repository"),
    import("@/src/infrastructure/repositories/mongodb.project-members.repository"),
    import("@/src/application/use-cases/plugins/list-plugin-catalog.use-case"),
    import("@/src/application/use-cases/plugins/preview-plugin-installation.use-case"),
    import("@/src/application/use-cases/plugins/install-plugin.use-case"),
    import("@/src/application/use-cases/plugins/set-plugin-enabled.use-case"),
    import("@/src/application/use-cases/plugins/list-project-plugins.use-case"),
    import("@/src/interface-adapters/controllers/plugins/plugin-catalog.controller"),
    import("@/src/interface-adapters/controllers/plugins/plugin-installation.controller"),
    import("@/src/application/services/plugin-tool-runtime"),
    import("@/src/application/policies/project-action-authorization.policy"),
    import("@/src/application/use-cases/plugins/plugin-service.shared"),
  ]);

  const transactionRunner = new pluginRepositoryModule.MongoPluginTransactionRunner({ pluginsMongoClient: database.mongoClient });
  const pluginsRepository = new pluginRepositoryModule.MongodbPluginsRepository({
    pluginsDatabase: database.db,
    pluginTransactionRunner: transactionRunner,
  });
  const usersRepository = new usersModule.MongoDBUsersRepository();
  const apiKeysRepository = new apiKeysModule.MongoDBApiKeysRepository();
  const projectMembersRepository = new membersModule.MongoDBProjectMembersRepository();
  const authorization = new policyModule.Auth0PluginApiAuthorizationPolicy({
    pluginUserSessionProvider: new policyModule.Auth0PluginUserSessionProvider({ usersRepository }),
    pluginProjectApiKeyVerifier: new policyModule.ExistingProjectApiKeyVerifier({ apiKeysRepository }),
    pluginUserTokenVerifier: new policyModule.JoseAuth0UserTokenVerifier({ usersRepository }),
    projectMembersRepository,
    pluginAuthEnabled: process.env.USE_AUTH === "true",
  });
  const listPluginCatalogUseCase = new catalogUseCaseModule.ListPluginCatalogUseCase({ pluginsRepository, pluginApiAuthorizationPolicy: authorization });
  const previewPluginInstallationUseCase = new previewUseCaseModule.PreviewPluginInstallationUseCase({ pluginsRepository, pluginApiAuthorizationPolicy: authorization });
  const installPluginUseCase = new installUseCaseModule.InstallPluginUseCase({ pluginsRepository, pluginApiAuthorizationPolicy: authorization });
  const setPluginEnabledUseCase = new enableUseCaseModule.SetPluginEnabledUseCase({ pluginsRepository, pluginApiAuthorizationPolicy: authorization });
  const listProjectPluginsUseCase = new listProjectUseCaseModule.ListProjectPluginsUseCase({ pluginsRepository, pluginApiAuthorizationPolicy: authorization });
  const projectActionAuthorizationPolicy = new projectAuthorizationModule.ProjectActionAuthorizationPolicy({ projectMembersRepository, apiKeysRepository });
  // Read once, at composition, and reused by both the runtime's own deadline
  // and the OpenFang adapter below: the two are tied by construction so an
  // operator can never approve a write after the runtime has already timed
  // the call out (which would write a timed_out receipt while leaving an
  // approved-but-orphaned approval in OpenFang for a retry to duplicate).
  const openFangApprovalWindowMs = resolveOpenFangApprovalWindowMs(process.env.OPENFANG_APPROVAL_TIMEOUT_MS);

  return Object.freeze({
    authenticate: (request: Request) => authorization.authenticate(request),
    catalog: new catalogControllerModule.PluginCatalogController({ pluginApiAuthorizationPolicy: authorization, listPluginCatalogUseCase }),
    installation: new installationControllerModule.PluginInstallationController({
      pluginApiAuthorizationPolicy: authorization, previewPluginInstallationUseCase, installPluginUseCase,
      setPluginEnabledUseCase, listProjectPluginsUseCase,
    }),
    findInstallReplay: async (request: Request, input: PluginReplayLookupInput) => {
      const identity = await authorization.authenticate(request);
      const actorType = identity.kind === "user" ? "user" : "project_api_key";
      const actorId = identity.kind === "user" ? identity.userId : identity.projectId;
      if (input.actorType !== actorType || input.actorId !== actorId || input.operation !== "install") {
        throw new Error("preview_invalid");
      }
      await authorization.authorizeProject(identity, input.projectId);
      if (input.installationPresent) return null;
      return pluginsRepository.getIdempotentReceipt({
        scope: pluginSharedModule.fingerprint({ projectId: input.projectId, operation: "install", idempotencyKey: input.idempotencyKey }),
        fingerprint: pluginSharedModule.fingerprint({
          projectId: input.projectId, pluginName: input.pluginName, catalogDigest: input.catalogDigest, expectedRevision: input.expectedRevision,
        }),
        projectId: input.projectId, pluginName: input.pluginName, catalogDigest: input.catalogDigest, operation: "install",
      });
    },
    addPluginTool: async (input: Readonly<{ identity: PluginApiIdentity; projectId: string; pluginName: string; componentDigest: string }>) => {
      const [useCaseModule, projectsModule, membersModule] = await Promise.all([
        import("@/src/application/use-cases/plugins/add-plugin-tool.use-case"),
        import("@/src/infrastructure/repositories/mongodb.projects.repository"),
        import("@/src/infrastructure/repositories/mongodb.project-members.repository"),
      ]);
      const projectsRepository = new projectsModule.MongodbProjectsRepository({ projectMembersRepository: new membersModule.MongoDBProjectMembersRepository() });
      const useCase = new useCaseModule.AddPluginToolUseCase({
        // The same project authorization the versioned plugin API uses.
        authorizeProject: (actor, projectId) => authorization.authorizeProject(actor, projectId),
        loadInstallation: (projectId, pluginName) => pluginsRepository.getInstallation(projectId, pluginName),
        loadCatalogEntry: async pluginName => {
          const catalog = await pluginsRepository.getCatalog(PINNED_PLUGIN_CATALOG_DIGEST);
          if (catalog === null || catalog.catalogDigest !== PINNED_PLUGIN_CATALOG_DIGEST) throw new Error("catalog_digest_mismatch");
          const selected = catalog.entries.find(candidate => candidate.name === pluginName);
          return selected === undefined ? null : { ...selected, catalogDigest: PINNED_PLUGIN_CATALOG_DIGEST };
        },
        loadDraftWorkflow: async projectId => {
          const project = await projectsRepository.fetch(projectId);
          if (project === null) throw new Error("project_not_found");
          return project.draftWorkflow;
        },
        saveDraftWorkflow: async (projectId, workflow) => {
          await projectsRepository.updateDraftWorkflow(projectId, workflow as Parameters<typeof projectsRepository.updateDraftWorkflow>[1]);
        },
      });
      return useCase.execute(input);
    },
    createToolRuntime: (authorizationContext: PluginToolAuthorizationContext | undefined) => new toolRuntimeModule.PluginToolRuntime({
      pluginsRepository,
      authorizationContext,
      authorizeProject: async (actor, projectId) => projectActionAuthorizationPolicy.authorize({ ...actor, projectId }),
      // Operations declared as read-only in their component metadata are classified
      // as reads; everything else defaults to writes to keep the policy fail-closed.
      classifyOperation: input => classifyPluginOperation(input),
      // Bounded by the same openFangApprovalWindowMs the adapter below is
      // given, via deriveRuntimeDeadlineMs -- which always exceeds it. This
      // is the fix for the invariant this whole dependency exists to uphold:
      // the call that waits for a human release must not be timed out by
      // the runtime before that human could plausibly have decided.
      timeoutMilliseconds: deriveRuntimeDeadlineMs(openFangApprovalWindowMs),
      // Provider implementations come through the hardened runtime registry.
      // No legacy Composio adapter is reachable from this composition boundary,
      // and credentials are not released yet, so an HTTP MCP call reaches its
      // provider and then fails with a missing credential rather than running
      // unauthenticated.
      resolveProvider: async ({ component, entry, binding }) => {
        const { resolvePluginProvider, UnreleasedCredentialResolver } = await import("@/src/infrastructure/plugins/provider-resolution");
        return resolvePluginProvider(
          { component, entry, binding },
          { credentialResolver: new UnreleasedCredentialResolver() },
        );
      },
      // OpenFang is the release authority for writes: a write stays under
      // review unless it is reachable and a human has approved this exact
      // call there. No OpenFang URL configured means no release is possible.
      releaseWrite: async (request, signal) => {
        const url = process.env.OPENFANG_URL;
        if (url === undefined || url.length === 0) return { status: "unavailable" as const };
        const { OpenFangWriteReleasePolicy } = await import("@/src/infrastructure/policies/openfang.plugin-write-release.policy");
        return new OpenFangWriteReleasePolicy({
          baseUrl: url,
          fetch,
          // Same window the runtime's own timeoutMilliseconds above was
          // derived from -- never re-parsed per call.
          timeoutMs: openFangApprovalWindowMs,
          pollIntervalMs: 1_000,
        }).release(request, signal);
      },
    }),
  });
}

function composition(): Promise<PluginControllers> {
  controllers ??= createPluginControllers();
  return controllers;
}

export async function resolvePluginCatalogController(): Promise<PluginCatalogController> {
  return (await composition()).catalog;
}

export function resolvePluginSessionController(): Promise<PluginSessionController> {
  sessionController ??= createPluginSessionController();
  return sessionController;
}

export async function resolvePluginInstallationController(): Promise<PluginInstallationController> {
  return (await composition()).installation;
}

export async function resolvePluginActionIdentity(request: Request): Promise<PluginApiIdentity> {
  return (await composition()).authenticate(request);
}

export async function resolvePluginInstallReplay(request: Request, input: PluginReplayLookupInput): Promise<unknown | null> {
  return (await composition()).findInstallReplay(request, input);
}

export async function resolveAddPluginTool(input: Readonly<{ identity: PluginApiIdentity; projectId: string; pluginName: string; componentDigest: string }>): Promise<unknown> {
  return (await composition()).addPluginTool(input);
}

export async function resolvePluginToolRuntime(authorizationContext?: PluginToolAuthorizationContext): Promise<PluginToolRuntime> {
  return (await composition()).createToolRuntime(authorizationContext);
}
