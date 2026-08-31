export type PluginApiIdentity =
  | Readonly<{ kind: "user"; userId: string }>
  | Readonly<{ kind: "project_api_key"; projectId: string }>;

export interface IPluginApiAuthorizationPolicy {
  authenticate(request: Request): Promise<PluginApiIdentity>;
  authorizeProject(identity: PluginApiIdentity, projectId: string): Promise<void>;
}
