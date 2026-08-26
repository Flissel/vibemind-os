import type { PluginCatalogController } from "@/src/interface-adapters/controllers/plugins/plugin-catalog.controller";
import type { PluginInstallationController } from "@/src/interface-adapters/controllers/plugins/plugin-installation.controller";
import type { PluginToolRuntime } from "@/src/application/services/plugin-tool-runtime";
import type { PluginToolAuthorizationContext } from "@/src/application/services/plugin-tool-runtime";
import type { PluginApiIdentity } from "@/src/application/policies/plugin-api-authorization.policy";
import type { PluginPreviewEnvelope } from "@/src/interface-adapters/actions/plugin-preview-envelope";
import type { PluginSessionController } from "@/src/interface-adapters/controllers/plugins/plugin-session.controller";

type PluginReplayLookupInput = PluginPreviewEnvelope;

interface PluginControllers {
  readonly authenticate: (request: Request) => Promise<PluginApiIdentity>;
  readonly catalog: PluginCatalogController;
  readonly installation: PluginInstallationController;
  readonly findInstallReplay: (request: Request, input: PluginReplayLookupInput) => Promise<unknown | null>;
  readonly createToolRuntime: (authorizationContext: PluginToolAuthorizationContext | undefined) => PluginToolRuntime;
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
    createToolRuntime: (authorizationContext: PluginToolAuthorizationContext | undefined) => new toolRuntimeModule.PluginToolRuntime({
      pluginsRepository,
      authorizationContext,
      authorizeProject: async (actor, projectId) => projectActionAuthorizationPolicy.authorize({ ...actor, projectId }),
      // Until a trusted per-operation classifier is registered, every plugin
      // tool is treated as mutating. DEFAULT_POLICY therefore keeps it fail-closed.
      classifyOperation: () => "write" as const,
      // Provider implementations must come through the hardened runtime registry.
      // No legacy Composio adapter is reachable from this composition boundary.
      resolveProvider: async () => Object.freeze({ status: "unavailable" as const, reason: "provider_unavailable" as const }),
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

export async function resolvePluginToolRuntime(authorizationContext?: PluginToolAuthorizationContext): Promise<PluginToolRuntime> {
  return (await composition()).createToolRuntime(authorizationContext);
}
