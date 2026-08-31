/**
 * Only an explicit, structurally valid read-only declaration makes an operation
 * a read. Anything else - absent, malformed, or unknown - is a write, because a
 * wrong read classification is the one that runs a side effect without release.
 */
export function classifyPluginOperation(input: Readonly<{
  pluginName: string;
  component: Readonly<{ metadata: Readonly<Record<string, unknown>> }>;
  operationName: string;
}>): "read" | "write" {
  const declared = input.component.metadata.readOnlyOperations;
  if (!Array.isArray(declared)) return "write";
  return declared.every(entry => typeof entry === "string") && declared.includes(input.operationName) ? "read" : "write";
}
