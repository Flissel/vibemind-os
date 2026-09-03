import { describe, expect, it } from "vitest";
import { z } from "zod";
import { UpdateDraftWorkflowUseCase } from "@/src/application/use-cases/projects/update-draft-workflow.use-case";
import { UpdateLiveWorkflowUseCase } from "@/src/application/use-cases/projects/update-live-workflow.use-case";
import type { IProjectsRepository } from "@/src/application/repositories/projects.repository.interface";
import type { IProjectActionAuthorizationPolicy } from "@/src/application/policies/project-action-authorization.policy";
import type { IUsageQuotaPolicy } from "@/src/application/policies/usage-quota.policy.interface";
import { pluginToolName } from "@/src/application/use-cases/plugins/add-plugin-tool.use-case";
import { Workflow, WorkflowTool } from "@/app/lib/types/workflow_types";
import type { Project } from "@/src/entities/models/project";

// The name a plugin-bound tool carries is not a display label: the agent
// runtime addresses it by that exact string, it feeds the read/write
// classifier, and it is the `toolName` a human reads and approves in
// OpenFang. These tests pin the server-side lock in
// `update-draft-workflow.use-case.ts` that refuses a save which renames a
// plugin-bound tool while leaving its `pluginBinding` untouched -- the actual
// enforcement point, since the UI is not the only writer.

const projectId = "11111111-1111-4111-8111-111111111111";
const userId = "user-1";
const componentDigest = "a".repeat(64);

// Deliberately not the same string, so a name derived from one can never
// collide with a name derived from the other by accident.
const pluginBinding = Object.freeze({
    installationId: "33333333-3333-4333-8333-333333333333",
    pluginName: "github",
    componentDigest,
    providerBindingId: "mcp.github",
    capability: "write" as const,
    origin: "native" as const,
});

// The real component name a catalog entry would carry -- never the same
// string as `pluginBinding.pluginName` (a real catalog component can differ
// from its plugin's own name, e.g. `cloudflare-api` under plugin
// `cloudflare`), and never hardcoded as the resulting tool name literal: both
// names below are always derived through the exported `pluginToolName(...)`,
// the same function `add-plugin-tool.use-case.ts` uses to name a tool in the
// first place.
const correctName = pluginToolName(pluginBinding.pluginName, "Create Issue", componentDigest);
const renamedName = pluginToolName(pluginBinding.pluginName, "Something Else Entirely", componentDigest);

function pluginTool(name: string): z.infer<typeof WorkflowTool> {
    return {
        name,
        description: "github: Create Issue",
        parameters: { type: "object", properties: {}, required: [] },
        pluginBinding,
    };
}

function plainTool(name: string): z.infer<typeof WorkflowTool> {
    return { name, description: "a plain tool", parameters: { type: "object", properties: {}, required: [] } };
}

function workflowOf(tools: readonly z.infer<typeof WorkflowTool>[]): z.infer<typeof Workflow> {
    return {
        agents: [], prompts: [], pipelines: [], startAgent: "a",
        lastUpdatedAt: "2026-08-01T10:00:00.000Z", tools: [...tools],
    };
}

interface Harness {
    readonly useCase: UpdateDraftWorkflowUseCase;
    readonly writes: readonly unknown[];
}

function harness(persistedDraftWorkflow: z.infer<typeof Workflow>): Harness {
    const writes: unknown[] = [];
    const projectsRepository: Pick<IProjectsRepository, "fetch" | "updateDraftWorkflow"> = {
        fetch: async () => ({
            id: projectId, name: "p", createdAt: "2026-08-01T10:00:00.000Z", createdByUserId: userId,
            secret: "s", draftWorkflow: persistedDraftWorkflow, liveWorkflow: persistedDraftWorkflow,
        }) as z.infer<typeof Project>,
        updateDraftWorkflow: async (_projectId, workflow) => { writes.push(workflow); return {} as z.infer<typeof Project>; },
    };
    const projectActionAuthorizationPolicy: IProjectActionAuthorizationPolicy = { authorize: async () => undefined };
    const usageQuotaPolicy: IUsageQuotaPolicy = {
        assertAndConsumeProjectAction: async () => undefined,
        assertAndConsumeRunJobAction: async () => undefined,
    };
    const useCase = new UpdateDraftWorkflowUseCase({
        projectsRepository: projectsRepository as IProjectsRepository,
        projectActionAuthorizationPolicy,
        usageQuotaPolicy,
    });
    return { useCase, writes };
}

function saveRequest(workflow: z.infer<typeof Workflow>): Parameters<UpdateDraftWorkflowUseCase["execute"]>[0] {
    return Object.freeze({ caller: "user", userId, projectId, workflow });
}

describe("locking a plugin-bound tool's name against a rename", () => {
    it("refuses a save that renames a plugin-bound tool while its binding is unchanged, before the repository write", async () => {
        const persisted = workflowOf([pluginTool(correctName)]);
        const { useCase, writes } = harness(persisted);

        const renamed = workflowOf([pluginTool(renamedName)]);
        await expect(useCase.execute(saveRequest(renamed))).rejects.toThrow("plugin_tool_renamed");
        expect(writes).toEqual([]);
    });

    it("writes the same workflow back unchanged when the plugin tool's name matches its binding's persisted name", async () => {
        const persisted = workflowOf([pluginTool(correctName)]);
        const { useCase, writes } = harness(persisted);

        await useCase.execute(saveRequest(persisted));
        expect(writes).toHaveLength(1);
    });

    it("leaves a tool without a pluginBinding free to be named anything, no regression for normal tools", async () => {
        const persisted = workflowOf([]);
        const { useCase, writes } = harness(persisted);

        const withNewPlainTool = workflowOf([plainTool(renamedName)]);
        await useCase.execute(saveRequest(withNewPlainTool));
        expect(writes).toHaveLength(1);
    });

    it("refuses a plugin-bound tool that has no counterpart in the persisted draft at all", async () => {
        const { useCase, writes } = harness(workflowOf([]));

        const freshlyBound = workflowOf([pluginTool(correctName)]);
        await expect(useCase.execute(saveRequest(freshlyBound))).rejects.toThrow("plugin_tool_renamed");
        expect(writes).toEqual([]);
    });
});

// A second tool bound to the SAME component. Two shipped legacy-migration
// recipes really do map two distinct legacy actions onto one catalog
// component (`legacy-plugin-recipes.ts`: github page-views and
// repository-clones both target GITHUB; sheets batch-get and batch-update
// both target SHEETS), and `plugin-binding-materialization.ts` resolves both
// to the same provider binding -- so two legitimate tools can carry a
// byte-identical `pluginBinding`. The lock must survive that: it may not
// lock the pair out of saving, and it may not let one of them take the
// other's name.
const secondName = pluginToolName(pluginBinding.pluginName, "Repository Clones", componentDigest);

interface LiveHarness {
    readonly useCase: UpdateLiveWorkflowUseCase;
    readonly writes: readonly unknown[];
}

function liveHarness(
    persistedDraftWorkflow: z.infer<typeof Workflow>,
    persistedLiveWorkflow: z.infer<typeof Workflow>,
): LiveHarness {
    const writes: unknown[] = [];
    const projectsRepository: Pick<IProjectsRepository, "fetch" | "updateLiveWorkflow"> = {
        fetch: async () => ({
            id: projectId, name: "p", createdAt: "2026-08-01T10:00:00.000Z", createdByUserId: userId,
            secret: "s", draftWorkflow: persistedDraftWorkflow, liveWorkflow: persistedLiveWorkflow,
        }) as z.infer<typeof Project>,
        updateLiveWorkflow: async (_projectId, workflow) => { writes.push(workflow); return {} as z.infer<typeof Project>; },
    };
    const useCase = new UpdateLiveWorkflowUseCase({
        projectsRepository: projectsRepository as IProjectsRepository,
        projectActionAuthorizationPolicy: { authorize: async () => undefined },
        usageQuotaPolicy: {
            assertAndConsumeProjectAction: async () => undefined,
            assertAndConsumeRunJobAction: async () => undefined,
        },
    });
    return { useCase, writes };
}

describe("two tools legitimately sharing one plugin binding", () => {
    it("saves both unchanged -- the pair must not be locked out of its own workflow", async () => {
        const persisted = workflowOf([pluginTool(correctName), pluginTool(secondName)]);
        const { useCase, writes } = harness(persisted);

        await useCase.execute(saveRequest(persisted));
        expect(writes).toHaveLength(1);
    });

    it("refuses giving one of the pair the other's name -- the swap a single-name map would wave through", async () => {
        const persisted = workflowOf([pluginTool(correctName), pluginTool(secondName)]);
        const { useCase, writes } = harness(persisted);

        const swapped = workflowOf([pluginTool(secondName), pluginTool(secondName)]);
        await expect(useCase.execute(saveRequest(swapped))).rejects.toThrow("plugin_tool_renamed");
        expect(writes).toEqual([]);
    });

    it("allows removing one of the pair -- a deletion is not a rename", async () => {
        const persisted = workflowOf([pluginTool(correctName), pluginTool(secondName)]);
        const { useCase, writes } = harness(persisted);

        await useCase.execute(saveRequest(workflowOf([pluginTool(secondName)])));
        expect(writes).toHaveLength(1);
    });
});

describe("publishing a workflow live", () => {
    it("refuses a publish that renames a plugin-bound tool -- the live workflow is what actually executes", async () => {
        const persisted = workflowOf([pluginTool(correctName)]);
        const { useCase, writes } = liveHarness(persisted, persisted);

        const renamed = workflowOf([pluginTool(renamedName)]);
        await expect(useCase.execute(saveRequest(renamed))).rejects.toThrow("plugin_tool_renamed");
        expect(writes).toEqual([]);
    });

    it("publishes a tool that exists only in the persisted draft -- the normal add-then-publish flow", async () => {
        const draft = workflowOf([pluginTool(correctName)]);
        const live = workflowOf([]);
        const { useCase, writes } = liveHarness(draft, live);

        await useCase.execute(saveRequest(draft));
        expect(writes).toHaveLength(1);
    });

    it("refuses publishing a plugin-bound tool that exists in neither persisted workflow", async () => {
        const empty = workflowOf([]);
        const { useCase, writes } = liveHarness(empty, empty);

        await expect(useCase.execute(saveRequest(workflowOf([pluginTool(correctName)])))).rejects.toThrow("plugin_tool_renamed");
        expect(writes).toEqual([]);
    });
});

describe("a project that is gone", () => {
    // A deleted or missing project has no persisted names at all, so every
    // plugin-bound tool is refused rather than written against an empty
    // baseline. Plain tools stay unaffected.
    function missingProjectHarness(): { readonly draft: UpdateDraftWorkflowUseCase; readonly live: UpdateLiveWorkflowUseCase; readonly writes: readonly unknown[] } {
        const writes: unknown[] = [];
        const projectsRepository: Pick<IProjectsRepository, "fetch" | "updateDraftWorkflow" | "updateLiveWorkflow"> = {
            fetch: async () => null,
            updateDraftWorkflow: async (_projectId, workflow) => { writes.push(workflow); return {} as z.infer<typeof Project>; },
            updateLiveWorkflow: async (_projectId, workflow) => { writes.push(workflow); return {} as z.infer<typeof Project>; },
        };
        const dependencies = {
            projectsRepository: projectsRepository as IProjectsRepository,
            projectActionAuthorizationPolicy: { authorize: async () => undefined } as IProjectActionAuthorizationPolicy,
            usageQuotaPolicy: {
                assertAndConsumeProjectAction: async () => undefined,
                assertAndConsumeRunJobAction: async () => undefined,
            } as IUsageQuotaPolicy,
        };
        return {
            draft: new UpdateDraftWorkflowUseCase(dependencies),
            live: new UpdateLiveWorkflowUseCase(dependencies),
            writes,
        };
    }

    it("refuses a plugin-bound tool on both the draft and the live path", async () => {
        const { draft, live, writes } = missingProjectHarness();
        const bound = workflowOf([pluginTool(correctName)]);

        await expect(draft.execute(saveRequest(bound))).rejects.toThrow("plugin_tool_renamed");
        await expect(live.execute(saveRequest(bound))).rejects.toThrow("plugin_tool_renamed");
        expect(writes).toEqual([]);
    });

    it("still lets a workflow without plugin tools through", async () => {
        const { draft, writes } = missingProjectHarness();

        await draft.execute(saveRequest(workflowOf([plainTool("anything")])));
        expect(writes).toHaveLength(1);
    });
});
