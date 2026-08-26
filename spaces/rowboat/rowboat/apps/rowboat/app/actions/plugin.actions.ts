"use server";

import { randomUUID } from "node:crypto";
import { types as utilTypes } from "node:util";
import { createPluginActionRuntime } from "@/src/interface-adapters/actions/plugin-action-runtime";

const SAFE_ERRORS = new Set([
  "request_invalid", "response_invalid", "unauthenticated", "user_authentication_required", "forbidden",
  "plugin_not_found", "installation_conflict", "catalog_digest_mismatch", "provider_unavailable",
  "license_rejected", "component_not_admitted", "idempotency_conflict",
  "source_mismatch", "manifest_invalid", "path_escape", "digest_mismatch", "license_review_required",
  "credential_missing", "http_mcp_not_admitted", "process_not_admitted", "hook_not_admitted",
  "write_review_required", "component_unsupported", "migration_conflict", "parity_failed", "rollback_unavailable",
]);

function runtime() {
  return createPluginActionRuntime({
    resolveControllers: async () => {
      const { resolvePluginCatalogController, resolvePluginInstallationController } = await import("@/di/plugins-container");
      const [catalog, installation] = await Promise.all([
        resolvePluginCatalogController(), resolvePluginInstallationController(),
      ]);
      return Object.freeze({ catalog, installation });
    },
    createRequest: () => new Request("https://rowboat.invalid/internal/plugin-action"),
    createIdempotencyKey: () => `ui-${randomUUID()}`,
  });
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
