import type { PluginInstallation } from "../repositories/plugins.repository.interface";
import { buildLegacyExecutableInventory, LEGACY_PLUGIN_RECIPES, type LegacyCardId, type LegacyPluginRecipe } from "./legacy-plugin-recipes";
import { captureMigrationJson } from "./legacy-plugin-migration";

const DIGEST = /^[a-f0-9]{64}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;

export class PluginMaterializationError extends Error {
  readonly code: "materialization_recipe_unknown" | "materialization_source_drift" | "materialization_binding_missing" | "materialization_workflow_invalid";

  constructor(code: PluginMaterializationError["code"]) {
    super(code);
    this.name = "PluginMaterializationError";
    this.code = code;
  }
}

export interface MaterializedToolBinding {
  readonly ordinal: number;
  readonly toolName: string;
  readonly pluginName: string;
  readonly componentId: string;
}

export interface MaterializationInput {
  readonly workflow: unknown;
  readonly recipeId: string;
  readonly installations: readonly PluginInstallation[];
}

export interface MaterializationResult {
  readonly workflow: Record<string, unknown>;
  readonly bound: readonly MaterializedToolBinding[];
}

function fail(code: PluginMaterializationError["code"]): never {
  throw new PluginMaterializationError(code);
}

function recipeFor(recipeId: unknown): LegacyPluginRecipe {
  if (typeof recipeId !== "string") fail("materialization_recipe_unknown");
  for (const cardId of Object.keys(LEGACY_PLUGIN_RECIPES) as LegacyCardId[]) {
    const recipe = LEGACY_PLUGIN_RECIPES[cardId];
    if (recipe.recipeId === recipeId) return recipe;
  }
  return fail("materialization_recipe_unknown");
}

function capturedWorkflow(workflow: unknown): Record<string, unknown> {
  let captured: unknown;
  try {
    captured = captureMigrationJson(workflow);
  } catch {
    return fail("materialization_workflow_invalid");
  }
  if (captured === null || typeof captured !== "object" || Array.isArray(captured)) fail("materialization_workflow_invalid");
  const record = captured as Record<string, unknown>;
  if (!Array.isArray(record.tools)) fail("materialization_workflow_invalid");
  return record;
}

/**
 * Writes the plugin binding of an applied migration onto the exact legacy tools
 * the recipe mapped.
 *
 * The binding comes from the installation, which is where the kernel keeps the
 * admitted provider binding; the catalog does not publish one. Every mapped
 * tool must still be the tool the recipe resolved against, proven by the
 * executable inventory digest at the same ordinal, so a workflow edited after
 * the migration is refused instead of being bound to a different tool.
 */
export function materializeWorkflowBindings(input: MaterializationInput): MaterializationResult {
  const recipe = recipeFor(input.recipeId);
  const workflow = capturedWorkflow(input.workflow);
  const inventory = buildLegacyExecutableInventory(workflow);
  const tools = workflow.tools as Record<string, unknown>[];
  if (!Array.isArray(input.installations)) fail("materialization_binding_missing");
  const installations: readonly PluginInstallation[] = input.installations;
  const bound: MaterializedToolBinding[] = [];

  for (const capability of recipe.capabilities) {
    const target = capability.target;
    if (target === null) continue;
    const ordinal = capability.legacyAction.ordinal;
    const actual = inventory.actions[ordinal];
    if (actual === undefined || actual.identity !== capability.legacyAction.identity || actual.descriptorDigest !== capability.legacyAction.descriptorDigest) {
      fail("materialization_source_drift");
    }
    const installation = installations.find(candidate => candidate.pluginName === target.pluginName && candidate.enabled);
    const selected = installation?.providerBindings?.find(candidate => candidate.componentId === target.componentId);
    if (installation === undefined || selected === undefined) fail("materialization_binding_missing");
    if (!UUID.test(installation.id) || !IDENTIFIER.test(installation.pluginName)
      || !DIGEST.test(selected.binding.componentDigest) || !IDENTIFIER.test(selected.binding.id)) fail("materialization_binding_missing");

    const tool = tools[ordinal];
    if (tool === undefined || typeof tool.name !== "string") fail("materialization_source_drift");
    tools[ordinal] = {
      ...tool,
      pluginBinding: {
        installationId: installation.id,
        pluginName: installation.pluginName,
        componentDigest: selected.binding.componentDigest,
        providerBindingId: selected.binding.id,
        // A provider binding carries no read/write classification. An unknown
        // effect is write-capable everywhere else in this runtime, so a
        // materialized tool is bound write-capable too.
        capability: "write" as const,
      },
    };
    bound.push(Object.freeze({ ordinal, toolName: tool.name, pluginName: installation.pluginName, componentId: target.componentId }));
  }

  return Object.freeze({ workflow, bound: Object.freeze(bound) });
}

/**
 * Removes every plugin binding from a workflow. Used to return authority to the
 * legacy tools when a retained rollback snapshot is not available.
 */
export function stripWorkflowBindings(workflow: unknown): Record<string, unknown> {
  const captured = capturedWorkflow(workflow);
  const tools = captured.tools as Record<string, unknown>[];
  for (let index = 0; index < tools.length; index += 1) {
    const tool = tools[index]!;
    if (!Object.prototype.hasOwnProperty.call(tool, "pluginBinding")) continue;
    const { pluginBinding: removed, ...rest } = tool;
    void removed;
    tools[index] = rest;
  }
  return captured;
}
