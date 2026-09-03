import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { PINNED_PLUGIN_CATALOG_DIGEST } from "@rowboat/openai-plugin-runtime";

// The Desktop renderer keeps its own copy of the pinned catalog digest as a
// literal string (it cannot import the kernel package) -- see
// apps/x/apps/renderer/src/lib/rowboat-plugin-api.ts. A kernel re-pin has
// silently left this literal stale before: the kernel's own
// PINNED_PLUGIN_CATALOG_DIGEST moved to a new value while the renderer's
// DESKTOP_PLUGIN_CATALOG_PIN.catalogDigest still carried the old one. This
// guard makes the NEXT re-pin fail here instead of silently shipping a
// mismatched desktop UI.
const RENDERER_PLUGIN_API_URL = new URL(
  "../../../x/apps/renderer/src/lib/rowboat-plugin-api.ts",
  import.meta.url,
);
const DIGEST_LITERAL = /[a-f0-9]{64}/g;

describe("desktop renderer catalog digest pin", () => {
  it("pins the desktop catalog digest literal to the kernel's PINNED_PLUGIN_CATALOG_DIGEST", () => {
    const source = readFileSync(RENDERER_PLUGIN_API_URL, "utf8");

    expect(source).toContain(PINNED_PLUGIN_CATALOG_DIGEST);

    const literals = source.match(DIGEST_LITERAL) ?? [];
    expect(literals.length).toBeGreaterThan(0);
    for (const literal of literals) {
      expect(literal).toBe(PINNED_PLUGIN_CATALOG_DIGEST);
    }
  });
});
