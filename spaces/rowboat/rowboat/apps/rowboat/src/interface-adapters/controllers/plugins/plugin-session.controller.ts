import type { IPluginApiAuthorizationPolicy } from "@/src/application/policies/plugin-api-authorization.policy";

export class PluginSessionController {
  constructor(private readonly authorization: IPluginApiAuthorizationPolicy) {}

  async execute(request: Request): Promise<Readonly<{ kind: "user" | "project_api_key"; id: string }>> {
    const identity = await this.authorization.authenticate(request);
    return Object.freeze({
      kind: identity.kind,
      id: identity.kind === "user" ? identity.userId : identity.projectId,
    });
  }
}
