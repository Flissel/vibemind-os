"use server";

import { randomUUID } from "node:crypto";
import { types as utilTypes } from "node:util";
import { createPluginActionRuntime, type PluginActionRuntimeDependencies } from "@/src/interface-adapters/actions/plugin-action-runtime";
import type { PluginPreviewEnvelope } from "@/src/interface-adapters/actions/plugin-preview-envelope";
import { PINNED_PLUGIN_CATALOG_DIGEST } from "@rowboat/openai-plugin-runtime";

const SAFE_ERRORS = new Set([
  "request_invalid", "response_invalid", "unauthenticated", "user_authentication_required", "forbidden",
  "plugin_not_found", "installation_conflict", "catalog_digest_mismatch", "provider_unavailable",
  "license_rejected", "component_not_admitted", "idempotency_conflict",
  "source_mismatch", "manifest_invalid", "path_escape", "digest_mismatch", "license_review_required",
  "credential_missing", "http_mcp_not_admitted", "process_not_admitted", "hook_not_admitted",
  "write_review_required", "component_unsupported", "migration_conflict", "parity_failed", "rollback_unavailable",
  "preview_configuration_invalid", "preview_invalid", "preview_expired", "stale_preview",
  "component_not_admitted", "installation_not_found", "project_not_found", "tool_name_conflict",
]);

function runtime() {
  const dependencies: PluginActionRuntimeDependencies = {
    resolveControllers: async () => {
      const { resolvePluginCatalogController, resolvePluginInstallationController } = await import("@/di/plugins-container");
      const [catalog, installation] = await Promise.all([
        resolvePluginCatalogController(), resolvePluginInstallationController(),
      ]);
      return Object.freeze({
        catalog,
        installation,
        authenticate: async (request: Request) => (await import("@/di/plugins-container")).resolvePluginActionIdentity(request),
        findInstallReplay: async (request: Request, input: PluginPreviewEnvelope) => (
          await import("@/di/plugins-container")
        ).resolvePluginInstallReplay(request, input),
      });
    },
    createRequest: () => new Request("https://rowboat.invalid/internal/plugin-action"),
    createIdempotencyKey: () => `ui-${randomUUID()}`,
    previewSecret: process.env.PLUGIN_UI_PREVIEW_SECRET,
    pinnedCatalogDigest: PINNED_PLUGIN_CATALOG_DIGEST,
  };
  return createPluginActionRuntime(dependencies);
}

async function redacted<T>(operation: () => Promise<T>): Promise<T> {
  try { return await operation(); } catch (error) {
    let message = "internal_error";
    if (!utilTypes.isProxy(error) && error instanceof Error && Object.getPrototypeOf(error) === Error.prototype) {
      const descriptor = Object.getOwnPropertyDescriptor(error, "message");
      if (descriptor !== undefined && "value" in descriptor && typeof descriptor.value === "string") message = descriptor.value;
    }
    throw new Error(SAFE_ERRORS.has(message) ? message : "internal_error");
  }
}

export async function listPluginCatalogAction(input: unknown) {
  return redacted(() => runtime().list(input));
}

export async function previewPluginInstallationAction(input: unknown) {
  return redacted(() => runtime().preview(input));
}

export async function installPluginAction(input: unknown) {
  return redacted(() => runtime().install(input));
}

const ADD_TOOL_KEYS = Object.freeze(["projectId", "pluginName", "componentDigest"]);

/**
 * Adds an installed, admitted plugin component to the project draft workflow as
 * a tool. The binding is native: the tool has no legacy counterpart, so the
 * migration runtime mode gate does not apply to it.
 */
export async function addPluginToolAction(input: unknown) {
  return redacted(async () => {
    if (input === null || typeof input !== "object" || utilTypes.isProxy(input)) throw new Error("request_invalid");
    const keys = Reflect.ownKeys(input);
    if (keys.length !== ADD_TOOL_KEYS.length || keys.some(key => typeof key !== "string" || !ADD_TOOL_KEYS.includes(key))) throw new Error("request_invalid");
    const selected = input as Record<string, unknown>;
    if (ADD_TOOL_KEYS.some(key => typeof selected[key] !== "string")) throw new Error("request_invalid");
    const { resolveAddPluginTool, resolvePluginActionIdentity } = await import("@/di/plugins-container");
    const identity = await resolvePluginActionIdentity(new Request("https://rowboat.invalid/internal/plugin-action"));
    return resolveAddPluginTool({
      identity,
      projectId: selected.projectId as string,
      pluginName: selected.pluginName as string,
      componentDigest: selected.componentDigest as string,
    });
  });
}
