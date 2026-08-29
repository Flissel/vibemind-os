import { describe, expect, it } from "vitest";
import type { PluginInstallation } from "@/src/application/repositories/plugins.repository.interface";
import { LEGACY_PLUGIN_RECIPES } from "@/src/application/services/legacy-plugin-recipes";
import { materializeWorkflowBindings, stripWorkflowBindings } from "@/src/application/services/plugin-binding-materialization";
import githubIssueToSlack from "@/app/lib/prebuilt-cards/github-issue-to-slack.json";

const recipe = LEGACY_PLUGIN_RECIPES["github-issue-to-slack"];
const target = recipe.capabilities[0]!.target!;
const installationId = "33333333-3333-4333-8333-333333333333";
const componentDigest = "a".repeat(64);

const installation = (overrides: Partial<PluginInstallation> = {}): PluginInstallation => Object.freeze({
  id: installationId, projectId: "11111111-1111-4111-8111-111111111111", pluginName: target.pluginName,
  pluginVersion: "1.0.0", sourceCommit: "1".repeat(40), manifestDigest: "b".repeat(64), treeDigest: "c".repeat(64),
  policyVersion: "rowboat-plugin-policy-v1", enabled: true, revision: 1,
  providerBindings: [{ componentId: target.componentId, binding: { id: "github.mcp", providerKind: target.providerKind, componentDigest } }],
  ...overrides,
}) as PluginInstallation;

const workflow = () => structuredClone(githubIssueToSlack) as Record<string, unknown>;

describe("plugin binding materialization", () => {
  it("binds the exact tool the recipe mapped and leaves every other tool untouched", () => {
    const source = workflow();
    const result = materializeWorkflowBindings({ workflow: source, recipeId: recipe.recipeId, installations: [installation()] });
    const tools = result.workflow.tools as readonly Record<string, unknown>[];
    const ordinal = recipe.capabilities[0]!.legacyAction.ordinal;

    expect(result.bound).toEqual([{ ordinal, toolName: (source.tools as Record<string, unknown>[])[ordinal]!.name, pluginName: target.pluginName, componentId: target.componentId }]);
    expect(tools[ordinal]!.pluginBinding).toEqual({
      installationId, pluginName: target.pluginName, componentDigest, providerBindingId: "github.mcp",
      // A provider binding carries no read/write classification, so the tool is
      // bound write-capable: an unknown effect is never treated as read-only.
      capability: "write",
    });
    for (let index = 0; index < tools.length; index += 1) {
      if (index !== ordinal) expect(tools[index]).not.toHaveProperty("pluginBinding");
    }
  });

  it("never mutates the caller workflow", () => {
    const source = workflow();
    const snapshot = structuredClone(source);
    materializeWorkflowBindings({ workflow: source, recipeId: recipe.recipeId, installations: [installation()] });
    expect(source).toEqual(snapshot);
  });

  it("refuses to bind when the mapped tool drifted from the recipe source", () => {
    const drifted = workflow();
    const tools = drifted.tools as Record<string, unknown>[];
    const ordinal = recipe.capabilities[0]!.legacyAction.ordinal;
    tools[ordinal] = { ...tools[ordinal]!, name: "renamed_tool" };
    expect(() => materializeWorkflowBindings({ workflow: drifted, recipeId: recipe.recipeId, installations: [installation()] }))
      .toThrow("materialization_source_drift");

    const shortened = workflow();
    (shortened.tools as Record<string, unknown>[]).length = 0;
    expect(() => materializeWorkflowBindings({ workflow: shortened, recipeId: recipe.recipeId, installations: [installation()] }))
      .toThrow("materialization_source_drift");
  });

  it("refuses to bind without an installation that carries the exact component binding", () => {
    for (const broken of [
      [],
      [installation({ providerBindings: [] })],
      [installation({ providerBindings: [{ componentId: "mcp:.mcp.json#other", binding: { id: "x", providerKind: target.providerKind, componentDigest } }] })],
      [installation({ pluginName: "gitlab" })],
      [installation({ enabled: false })],
    ] as PluginInstallation[][]) {
      expect(() => materializeWorkflowBindings({ workflow: workflow(), recipeId: recipe.recipeId, installations: broken }))
        .toThrow("materialization_binding_missing");
    }
  });

  it("refuses an unknown recipe and a workflow that is not a legacy workflow", () => {
    expect(() => materializeWorkflowBindings({ workflow: workflow(), recipeId: "legacy-card:does-not-exist:v1", installations: [installation()] }))
      .toThrow("materialization_recipe_unknown");
    expect(() => materializeWorkflowBindings({ workflow: { tools: "nope" }, recipeId: recipe.recipeId, installations: [installation()] }))
      .toThrow();
  });

  it("strips every binding back out and restores the exact legacy shape", () => {
    const source = workflow();
    const bound = materializeWorkflowBindings({ workflow: source, recipeId: recipe.recipeId, installations: [installation()] });
    expect(stripWorkflowBindings(bound.workflow)).toEqual(source);
    // Stripping an unbound workflow is a no-op rather than an error.
    expect(stripWorkflowBindings(source)).toEqual(source);
  });
});
