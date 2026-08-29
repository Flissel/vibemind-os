/**
 * How a workflow tool presents itself in the tool panel.
 *
 * A tool bound to a plugin is server owned: the binding, not the panel, decides
 * what runs, and it is revalidated against the installation and the pinned
 * catalog on every call. Without this the panel falls through to its last
 * branch and calls a plugin tool "a placeholder tool that should be mocked",
 * which is the opposite of the truth.
 */
export interface PluginToolSummary {
  readonly pluginName: string;
  readonly providerBindingId: string;
  /** Shortened for display; the full digest stays in the workflow document. */
  readonly componentDigestShort: string;
  readonly capability: "read" | "write";
  readonly origin: "migration" | "native";
  readonly originLabel: string;
  readonly capabilityLabel: string;
}

const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const DIGEST = /^[a-f0-9]{64}$/;

function readString(source: Record<string, unknown>, key: string, pattern: RegExp): string | null {
  const value = source[key];
  return typeof value === "string" && pattern.test(value) ? value : null;
}

/**
 * Returns the summary only for a structurally valid binding. A malformed one
 * describes nothing, so the panel says nothing about it rather than inventing
 * a provenance.
 */
export function pluginToolSummary(tool: unknown): PluginToolSummary | null {
  if (tool === null || typeof tool !== "object") return null;
  const binding = (tool as { pluginBinding?: unknown }).pluginBinding;
  if (binding === null || typeof binding !== "object" || Array.isArray(binding)) return null;
  const source = binding as Record<string, unknown>;
  const pluginName = readString(source, "pluginName", IDENTIFIER);
  const providerBindingId = readString(source, "providerBindingId", IDENTIFIER);
  const componentDigest = readString(source, "componentDigest", DIGEST);
  const capability = source.capability === "read" ? "read" as const : source.capability === "write" ? "write" as const : null;
  if (pluginName === null || providerBindingId === null || componentDigest === null || capability === null) return null;
  // An absent or unreadable origin is a migrated binding, matching the gate.
  const origin = source.origin === "native" ? "native" as const : "migration" as const;
  return Object.freeze({
    pluginName,
    providerBindingId,
    componentDigestShort: componentDigest.slice(0, 12),
    capability,
    origin,
    originLabel: origin === "native" ? "Added from the plugin catalog" : "Migrated from a legacy tool",
    capabilityLabel: capability === "read" ? "Read-only" : "Write-capable",
  });
}
