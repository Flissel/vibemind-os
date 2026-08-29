import { Workflow } from "@/app/lib/types/workflow_types";
import { z } from "zod";

export const ComposioConnectedAccount = z.object({
    id: z.string(),
    authConfigId: z.string(),
    status: z.enum([
        'INITIATED',
        'ACTIVE',
        'FAILED',
    ]),
    createdAt: z.string().datetime(),
    lastUpdatedAt: z.string().datetime(),
});

export const CustomMcpServer = z.object({
    serverUrl: z.string(),
});

/**
 * Per-project authority of the plugin runtime.
 *
 * `legacy` and `shadow` keep the existing workflow tools authoritative; only
 * `openai` executes plugin components. The state is written by compare-and-swap
 * on `revision`, and the cutover evidence stays addressable for rollback.
 */
export const PluginRuntimeState = z.object({
    mode: z.enum(["legacy", "shadow", "openai"]),
    revision: z.number().int().nonnegative(),
    migrationRecordId: z.string().uuid().optional(),
    parityReceiptId: z.string().min(1).max(128).optional(),
    rollbackSnapshotDigest: z.string().regex(/^[a-f0-9]{64}$/).optional(),
    cutoverAt: z.string().datetime().optional(),
    rolledBackAt: z.string().datetime().optional(),
}).strict();

export type PluginRuntimeStateValue = z.infer<typeof PluginRuntimeState>;

/**
 * The documented default for every project stored before the plugin runtime
 * existed. Reads resolve it in memory; no read rewrites a project document.
 */
export const DEFAULT_PLUGIN_RUNTIME_STATE: PluginRuntimeStateValue = Object.freeze({ mode: "legacy", revision: 0 });

export function parsePluginRuntimeState(value: unknown): PluginRuntimeStateValue {
    return value === undefined || value === null
        ? DEFAULT_PLUGIN_RUNTIME_STATE
        : PluginRuntimeState.parse(value);
}

export const Project = z.object({
    id: z.string().uuid(),
    name: z.string(),
    createdAt: z.string().datetime(),
    lastUpdatedAt: z.string().datetime().optional(),
    createdByUserId: z.string(),
    secret: z.string(),
    draftWorkflow: Workflow,
    liveWorkflow: Workflow,
    webhookUrl: z.string().optional(),
    composioConnectedAccounts: z.record(z.string(), ComposioConnectedAccount).optional(),
    customMcpServers: z.record(z.string(), CustomMcpServer).optional(),
    pluginRuntime: PluginRuntimeState.optional(),
});