import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { parsePluginManifest } from "../src/schema/plugin-manifest.js";

const fixturePath = fileURLToPath(
  new URL("./fixtures/github/plugin.json", import.meta.url),
);

async function readFixture(): Promise<unknown> {
  return JSON.parse(await readFile(fixturePath, "utf8")) as unknown;
}

function requireRecord(value: unknown): Record<string, unknown> {
  if (typeof value === "object" && value !== null && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }

  throw new Error("Expected a JSON object fixture.");
}

describe("parsePluginManifest", () => {
  it("parses a valid manifest and preserves metadata", async () => {
    const manifest = parsePluginManifest(await readFixture());

    expect(manifest).toMatchObject({
      name: "github",
      version: "0.1.6",
      description: "GitHub workflows",
      license: "MIT",
      skills: "./skills/",
      apps: "./.app.json",
      mcpServers: "./.mcp.json",
      interface: {
        displayName: "GitHub",
        capabilities: ["Interactive", "Write"],
      },
    });
  });

  it.each(["Git Hub", "../github", "github/"])(
    "rejects unsafe plugin name %s",
    async (name) => {
      const manifest = requireRecord(await readFixture());

      expect(() => parsePluginManifest({ ...manifest, name })).toThrow(
        "manifest_invalid:",
      );
    },
  );

  it("rejects an unknown top-level executable field", async () => {
    const manifest = requireRecord(await readFixture());

    expect(() =>
      parsePluginManifest({ ...manifest, executable: "./run.sh" }),
    ).toThrow("manifest_invalid:");
  });

  it("rejects unknown interface fields with a deterministic issue path", async () => {
    const manifest = requireRecord(await readFixture());
    const interfaceMetadata = requireRecord(manifest["interface"]);

    expect(() =>
      parsePluginManifest({
        ...manifest,
        interface: { ...interfaceMetadata, executable: "./run.sh" },
      }),
    ).toThrow("manifest_invalid: interface");
  });
});
