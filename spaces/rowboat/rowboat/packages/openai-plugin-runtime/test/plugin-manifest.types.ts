import type { PluginManifest } from "../src/schema/plugin-manifest.js";

declare const manifest: PluginManifest;

// @ts-expect-error Plugin manifest properties are deeply readonly.
manifest.name = "other-plugin";

const capabilities = manifest.interface.capabilities;
if (capabilities !== undefined) {
  // @ts-expect-error Plugin manifest arrays are deeply readonly.
  capabilities.push("Read");
}
