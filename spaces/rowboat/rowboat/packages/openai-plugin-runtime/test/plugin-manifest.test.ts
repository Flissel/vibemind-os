import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  parsePluginManifest,
  PluginManifestValidationError,
} from "../src/schema/plugin-manifest.js";

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

function parseValidationError(input: unknown): PluginManifestValidationError {
  try {
    parsePluginManifest(input);
  } catch (error: unknown) {
    if (error instanceof PluginManifestValidationError) {
      return error;
    }

    throw error;
  }

  throw new Error("Expected plugin manifest validation to fail.");
}

describe("parsePluginManifest", () => {
  it("parses and deeply freezes every supported manifest field", async () => {
    const manifest = parsePluginManifest(await readFixture());

    expect(manifest).toStrictEqual({
      name: "github",
      version: "0.1.6",
      description: "GitHub workflows",
      author: {
        name: "GitHub",
        email: "plugins@github.com",
        url: "https://github.com",
      },
      homepage: "https://github.com/features/actions",
      repository: "github/github-plugin",
      license: "MIT",
      keywords: ["github", "automation"],
      skills: "./skills/",
      agents: "./agents/",
      commands: "./commands/",
      hooks: "./hooks/",
      apps: "./.app.json",
      mcpServers: "./.mcp.json",
      interface: {
        displayName: "GitHub",
        shortDescription: "GitHub actions",
        longDescription: "Run GitHub workflows from ChatGPT.",
        developerName: "GitHub",
        category: "Developer tools",
        capabilities: ["Interactive", "Read", "Write"],
        defaultPrompt: ["Help me review this pull request."],
        brandColor: "#24292f",
        composerIcon: "./assets/composer.svg",
        logo: "./assets/logo.svg",
        screenshots: ["./assets/overview.png", "./assets/review.png"],
        websiteURL: "https://github.com",
        privacyPolicyURL:
          "https://docs.github.com/site-policy/privacy-policies/github-privacy-statement",
        termsOfServiceURL:
          "https://docs.github.com/site-policy/github-terms/github-terms-of-service",
      },
    });

    const capabilities = manifest.interface.capabilities;
    if (capabilities === undefined) {
      throw new Error("Fixture must define interface capabilities.");
    }

    expect(Object.isFrozen(manifest)).toBe(true);
    expect(Object.isFrozen(manifest.interface)).toBe(true);
    expect(Object.isFrozen(capabilities)).toBe(true);
    expect(() => Object.defineProperty(manifest, "name", { value: "other" })).toThrow(
      TypeError,
    );
    expect(() => Object.defineProperty(capabilities, "0", { value: "Read" })).toThrow(
      TypeError,
    );
  });

  it("removes optional undefined own-properties from parsed output", async () => {
    const fixture = requireRecord(await readFixture());
    const manifest = parsePluginManifest({ ...fixture, author: undefined });

    expect(Object.hasOwn(manifest, "author")).toBe(false);
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

  it("returns a structured, sorted validation error independent of insertion order", async () => {
    const fixture = requireRecord(await readFixture());
    const interfaceMetadata = requireRecord(fixture["interface"]);
    const remainingFields = Object.fromEntries(
      Object.entries(fixture).filter(
        ([key]) => key !== "interface" && key !== "name" && key !== "version",
      ),
    );
    const invalidInNameOrder = {
      ...remainingFields,
      name: "Git Hub",
      version: "",
      interface: { ...interfaceMetadata, displayName: "" },
    };
    const invalidInVersionOrder = {
      version: "",
      interface: { ...interfaceMetadata, displayName: "" },
      name: "Git Hub",
      ...remainingFields,
    };

    const firstError = parseValidationError(invalidInNameOrder);
    const secondError = parseValidationError(invalidInVersionOrder);

    expect(firstError.code).toBe("manifest_invalid");
    expect(firstError.issuePaths).toStrictEqual([
      "interface.displayName",
      "name",
      "version",
    ]);
    expect(firstError.message).toBe(
      "manifest_invalid: interface.displayName, name, version",
    );
    expect(secondError.code).toBe(firstError.code);
    expect(secondError.issuePaths).toStrictEqual(firstError.issuePaths);
    expect(secondError.message).toBe(firstError.message);
  });
});
