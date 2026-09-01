import { z } from "zod";
import type { IPluginApiAuthorizationPolicy } from "@/src/application/policies/plugin-api-authorization.policy";
import type { InstallPluginUseCase } from "@/src/application/use-cases/plugins/install-plugin.use-case";
import type { PreviewPluginInstallationUseCase } from "@/src/application/use-cases/plugins/preview-plugin-installation.use-case";
import type { SetPluginEnabledUseCase } from "@/src/application/use-cases/plugins/set-plugin-enabled.use-case";
import type { ListProjectPluginsUseCase } from "@/src/application/use-cases/plugins/list-project-plugins.use-case";
import { captureRecord } from "./plugin-controller.shared";

const Base = { projectId: z.string(), pluginName: z.string() };
const Preview = z.object({ ...Base, catalogDigest: z.string() }).strict();
const List = z.object({ projectId: z.string(), catalogDigest: z.string() }).strict();
const Install = z.object({
  ...Base, catalogDigest: z.string(), idempotencyKey: z.string(), expectedRevision: z.number().int().nonnegative(),
  componentDigests: z.array(z.string()).min(1).max(512).optional(),
}).strict();
const Enabled = z.object({ ...Base, catalogDigest: z.string(), enabled: z.boolean(), expectedRevision: z.number().int().nonnegative(), idempotencyKey: z.string() }).strict();

export class PluginInstallationController {
  constructor(private readonly dependencies: {
    readonly pluginApiAuthorizationPolicy: IPluginApiAuthorizationPolicy;
    readonly previewPluginInstallationUseCase: PreviewPluginInstallationUseCase;
    readonly installPluginUseCase: InstallPluginUseCase;
    readonly setPluginEnabledUseCase: SetPluginEnabledUseCase;
    readonly listProjectPluginsUseCase: ListProjectPluginsUseCase;
  }) {}
  async list(request: Request, input: unknown) {
    const parsed = List.safeParse(captureRecord(input, ["projectId", "catalogDigest"]));
    if (!parsed.success) throw new Error("request_invalid");
    const identity = await this.dependencies.pluginApiAuthorizationPolicy.authenticate(request);
    return this.dependencies.listProjectPluginsUseCase.execute({ identity, ...parsed.data });
  }
  async preview(request: Request, input: unknown) {
    const parsed = Preview.safeParse(captureRecord(input, ["projectId", "pluginName", "catalogDigest"]));
    if (!parsed.success) throw new Error("request_invalid");
    const identity = await this.dependencies.pluginApiAuthorizationPolicy.authenticate(request);
    return this.dependencies.previewPluginInstallationUseCase.execute({ identity, ...parsed.data });
  }
  async install(request: Request, input: unknown) {
    const parsed = Install.safeParse(captureRecord(input, ["projectId", "pluginName", "catalogDigest", "idempotencyKey", "expectedRevision", "componentDigests"]));
    if (!parsed.success) throw new Error("request_invalid");
    const identity = await this.dependencies.pluginApiAuthorizationPolicy.authenticate(request);
    return this.dependencies.installPluginUseCase.execute({ identity, ...parsed.data });
  }
  async setEnabled(request: Request, input: unknown) {
    const parsed = Enabled.safeParse(captureRecord(input, ["projectId", "pluginName", "catalogDigest", "enabled", "expectedRevision", "idempotencyKey"]));
    if (!parsed.success) throw new Error("request_invalid");
    const identity = await this.dependencies.pluginApiAuthorizationPolicy.authenticate(request);
    return this.dependencies.setPluginEnabledUseCase.execute({ identity, ...parsed.data });
  }
}
