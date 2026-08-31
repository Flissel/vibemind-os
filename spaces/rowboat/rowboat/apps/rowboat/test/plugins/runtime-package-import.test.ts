import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import {
  RUNTIME_SCHEMA_VERSION,
  type PluginCatalogEntry,
} from "@rowboat/openai-plugin-runtime";

describe("OpenAI plugin runtime package boundary", () => {
  it("resolves the public runtime value and type", () => {
    const entry: PluginCatalogEntry | null = null;
    expect(entry).toBeNull();
    expect(RUNTIME_SCHEMA_VERSION).toBe("rowboat-openai-plugin-runtime-v1");
  });

  it("exports reproducibly built JavaScript and declarations", async () => {
    const manifestPath = resolve(process.cwd(), "../../packages/openai-plugin-runtime/package.json");
    const manifest = JSON.parse(await readFile(manifestPath, "utf8")) as {
      readonly exports?: { readonly "."?: { readonly import?: string; readonly types?: string } };
      readonly scripts?: { readonly build?: string; readonly prepare?: string };
    };
    expect(manifest.exports?.["."]).toEqual({
      import: "./dist/index.js",
      types: "./dist/index.d.ts",
    });
    expect(manifest.scripts?.build).toBe("tsc -p tsconfig.build.json");
    expect(manifest.scripts?.prepare).toBe("npm run build");
  });
});
