import { z } from "zod";
import { IProjectsRepository } from "../../repositories/projects.repository.interface";
import { IProjectActionAuthorizationPolicy } from "../../policies/project-action-authorization.policy";
import { IUsageQuotaPolicy } from "../../policies/usage-quota.policy.interface";
import { Workflow } from "@/app/lib/types/workflow_types";

export const InputSchema = z.object({
    caller: z.enum(["user", "api"]),
    userId: z.string().optional(),
    apiKey: z.string().optional(),
    projectId: z.string(),
    workflow: Workflow,
});

export interface IUpdateDraftWorkflowUseCase {
    execute(request: z.infer<typeof InputSchema>): Promise<void>;
}

/**
 * A workflow tool's `pluginBinding.installationId` + `componentDigest` +
 * `providerBindingId` triple identifies one real, installed plugin component.
 * Used as a map key, so a tool can be found again across a save regardless of
 * where it sits in the `tools` array or what its `name` currently is.
 *
 * A binding missing any of these (a malformed or hand-crafted object that
 * only structurally resembles one) has no identity to key by, so it never
 * matches anything here -- which is exactly the fail-closed reading: no
 * identity, no Sollname, no write.
 */
function pluginBindingIdentity(binding: Readonly<Record<string, unknown>>): string | null {
    const { installationId, componentDigest, providerBindingId } = binding;
    if (typeof installationId !== "string" || installationId.length === 0) return null;
    if (typeof componentDigest !== "string" || componentDigest.length === 0) return null;
    if (typeof providerBindingId !== "string" || providerBindingId.length === 0) return null;
    return `${installationId}\0${componentDigest}\0${providerBindingId}`;
}

/**
 * Maps every plugin-bound tool's binding identity to the `name` it is
 * currently persisted under, reading defensively since the caller hands this
 * whatever is already stored (not itself guaranteed to satisfy the current
 * `Workflow` schema).
 */
function persistedPluginToolNames(draftWorkflow: unknown): ReadonlyMap<string, string> {
    const byIdentity = new Map<string, string>();
    if (draftWorkflow === null || typeof draftWorkflow !== "object" || Array.isArray(draftWorkflow)) return byIdentity;
    const tools = (draftWorkflow as Record<string, unknown>).tools;
    if (!Array.isArray(tools)) return byIdentity;
    for (const candidate of tools) {
        if (candidate === null || typeof candidate !== "object" || Array.isArray(candidate)) continue;
        const record = candidate as Record<string, unknown>;
        if (typeof record.name !== "string") continue;
        const binding = record.pluginBinding;
        if (binding === null || typeof binding !== "object" || Array.isArray(binding)) continue;
        const identity = pluginBindingIdentity(binding as Record<string, unknown>);
        if (identity === null) continue;
        byIdentity.set(identity, record.name);
    }
    return byIdentity;
}

function pluginToolRenamed(): never {
    throw new Error("plugin_tool_renamed");
}

/**
 * Refuses a save that renames a plugin-bound tool.
 *
 * A plugin tool's `name` is not a display label: the agent runtime addresses
 * it by that exact string (`context.operationName`), it is the input to the
 * read/write classifier, and it is the `toolName` a human reads and approves
 * in OpenFang. Silently renaming it -- while keeping the same `pluginBinding`
 * -- would let a call be approved under one identity and run under another.
 *
 * `pluginBinding` does not retain the human-readable component name
 * `pluginToolName(...)` (`add-plugin-tool.use-case.ts`) was originally
 * derived from -- only `pluginName` and `componentDigest`, which is not
 * enough to reproduce that exact name from scratch (a real catalog component
 * name, e.g. `cloudflare-api` for the `cloudflare` plugin, need not equal
 * `pluginName`, so the two are not interchangeable inputs). Both legitimate
 * writers of a `pluginBinding` -- `AddPluginToolUseCase` and
 * `plugin-binding-materialization.ts` -- already write through their own
 * repository calls and never through this use case, so a plugin-bound tool
 * only legitimately reaches here as an unmodified echo of what is already
 * persisted. The check this function makes is exactly that: every
 * plugin-bound tool in the incoming workflow must keep the same `name` its
 * binding identity is already persisted under. A binding identity with no
 * persisted counterpart -- a new one appearing through this path -- has no
 * name to hold it to, so it is refused the same way; there is no legitimate
 * route by which one would appear here.
 */
function assertNoPluginToolRenamed(incomingWorkflow: Readonly<{ tools: readonly Readonly<{ name: string; pluginBinding?: unknown }>[] }>, persistedDraftWorkflow: unknown): void {
    const persisted = persistedPluginToolNames(persistedDraftWorkflow);
    for (const tool of incomingWorkflow.tools) {
        const binding = tool.pluginBinding;
        if (binding === undefined) continue;
        if (binding === null || typeof binding !== "object" || Array.isArray(binding)) pluginToolRenamed();
        const identity = pluginBindingIdentity(binding as Record<string, unknown>);
        if (identity === null) pluginToolRenamed();
        const persistedName = persisted.get(identity);
        if (persistedName === undefined || persistedName !== tool.name) pluginToolRenamed();
    }
}

export class UpdateDraftWorkflowUseCase implements IUpdateDraftWorkflowUseCase {
    private readonly projectsRepository: IProjectsRepository;
    private readonly projectActionAuthorizationPolicy: IProjectActionAuthorizationPolicy;
    private readonly usageQuotaPolicy: IUsageQuotaPolicy;

    constructor({
        projectsRepository,
        projectActionAuthorizationPolicy,
        usageQuotaPolicy,
    }: {
        projectsRepository: IProjectsRepository,
        projectActionAuthorizationPolicy: IProjectActionAuthorizationPolicy,
        usageQuotaPolicy: IUsageQuotaPolicy,
    }) {
        this.projectsRepository = projectsRepository;
        this.projectActionAuthorizationPolicy = projectActionAuthorizationPolicy;
        this.usageQuotaPolicy = usageQuotaPolicy;
    }

    async execute(request: z.infer<typeof InputSchema>): Promise<void> {
        const { projectId } = request;
        await this.projectActionAuthorizationPolicy.authorize({
            caller: request.caller,
            userId: request.userId,
            apiKey: request.apiKey,
            projectId,
        });
        await this.usageQuotaPolicy.assertAndConsumeProjectAction(projectId);

        const existingProject = await this.projectsRepository.fetch(projectId);
        assertNoPluginToolRenamed(request.workflow, existingProject === null ? null : existingProject.draftWorkflow);

        const workflow = { ...request.workflow, lastUpdatedAt: new Date().toISOString() } as z.infer<typeof Workflow>;
        await this.projectsRepository.updateDraftWorkflow(projectId, workflow);
    }
}
