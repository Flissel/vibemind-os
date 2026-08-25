import type { PluginCatalogController } from "@/src/interface-adapters/controllers/plugins/plugin-catalog.controller";
import type { PluginInstallationController } from "@/src/interface-adapters/controllers/plugins/plugin-installation.controller";

interface PluginControllers {
  readonly catalog: PluginCatalogController;
  readonly installation: PluginInstallationController;
}

let controllers: Promise<PluginControllers> | undefined;

async function createPluginControllers(): Promise<PluginControllers> {
  const [database, pluginRepositoryModule, policyModule, usersModule, apiKeysModule, membersModule, catalogUseCaseModule,
    previewUseCaseModule, installUseCaseModule, enableUseCaseModule, listProjectUseCaseModule, catalogControllerModule,
    installationControllerModule] = await Promise.all([
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

  return Object.freeze({
    catalog: new catalogControllerModule.PluginCatalogController({ pluginApiAuthorizationPolicy: authorization, listPluginCatalogUseCase }),
    installation: new installationControllerModule.PluginInstallationController({
      pluginApiAuthorizationPolicy: authorization, previewPluginInstallationUseCase, installPluginUseCase,
      setPluginEnabledUseCase, listProjectPluginsUseCase,
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

export async function resolvePluginInstallationController(): Promise<PluginInstallationController> {
  return (await composition()).installation;
}
