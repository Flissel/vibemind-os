import { PluginManifestSchema } from "../src/schema/plugin-manifest.js";

declare const schemaManifest: ReturnType<typeof PluginManifestSchema.parse>;

// @ts-expect-error Public schema output is deeply readonly.
schemaManifest.name = "other-plugin";

const capabilities = schemaManifest.interface.capabilities;
if (capabilities !== undefined) {
  // @ts-expect-error Public schema output arrays are deeply readonly.
  capabilities.push("Read");
}
