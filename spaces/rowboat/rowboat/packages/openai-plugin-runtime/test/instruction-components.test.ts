import { mkdir, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";
import {
  normalizeAgents,
  normalizeAsset,
  normalizeCommands,
  normalizeSkill,
} from "../src/index.js";
import { cleanupRegisteredTestRoots, createOwnedTestRoot } from "./test-temp.js";

const fixtureRoot = join(
  dirname(fileURLToPath(import.meta.url)),
  "fixtures",
  "complete-plugin",
);

afterEach(cleanupRegisteredTestRoots);

async function createPluginRoot(label: string): Promise<string> {
  const owner = await createOwnedTestRoot(label);
  const pluginRoot = join(owner, "plugin");
  await mkdir(pluginRoot, { recursive: true });
  return pluginRoot;
}

describe("instruction component normalizers", () => {
  it("loads complete skill instructions and binds every directly referenced local resource", async () => {
    const pluginRoot = await createPluginRoot("skill-normalizer");
    const skillRoot = join(pluginRoot, "skills", "review");
    await mkdir(join(skillRoot, "references"), { recursive: true });
    await writeFile(
      join(skillRoot, "SKILL.md"),
      [
        "---",
        "name: review",
        "description: Review a change.",
        "trigger_rules:",
        "  - when review is requested",
        "---",
        "",
        "# Skill",
        "",
        "Read [the rules](references/rules.md) before reviewing.",
        "",
      ].join("\n"),
      "utf8",
    );
    await writeFile(join(skillRoot, "references", "rules.md"), "# Rules\n", "utf8");

    const skill = await normalizeSkill(skillRoot, pluginRoot);

    expect(skill).toMatchObject({
      name: "review",
      description: "Review a change.",
      triggerRules: ["when review is requested"],
    });
    expect(skill.instructions).toContain("# Skill");
    expect(skill.instructions).toContain("Read [the rules](references/rules.md)");
    expect(skill.resources.map((item) => item.path)).toEqual(["references/rules.md"]);
    expect(skill.resources[0]?.digest).toMatch(/^[a-f0-9]{64}$/);
    expect(Object.isFrozen(skill)).toBe(true);
    expect(Object.isFrozen(skill.resources)).toBe(true);
    expect(Object.isFrozen(skill.resources[0])).toBe(true);
  });

  it("rejects a skill resource that leaves the plugin root", async () => {
    const owner = await createOwnedTestRoot("skill-escape");
    const pluginRoot = join(owner, "plugin");
    const skillRoot = join(pluginRoot, "skills", "review");
    await mkdir(skillRoot, { recursive: true });
    await writeFile(join(owner, "outside.md"), "outside", "utf8");
    await writeFile(
      join(skillRoot, "SKILL.md"),
      "---\nname: review\ndescription: Review.\n---\n[escape](../../../outside.md)\n",
      "utf8",
    );

    await expect(normalizeSkill(skillRoot, pluginRoot)).rejects.toMatchObject({
      code: "path_escape",
    });
  });

  it("binds pinned-style inline-code and prose resource references once in code-point order", async () => {
    const pluginRoot = await createPluginRoot("skill-prose-resources");
    const skillRoot = join(pluginRoot, "skills", "database-review");
    await mkdir(join(skillRoot, "references"), { recursive: true });
    await writeFile(
      join(skillRoot, "SKILL.md"),
      [
        "---",
        "name: database-review",
        "description: Review database queries.",
        "---",
        "",
        "Read `references/cache-optimization.md` before starting.",
        "Then inspect references/query-missing-indexes.md.",
        "Read references/cache-optimization.md again.",
        "The equivalent references/./cache-optimization.md is not a second resource.",
        "Do not treat https://example.com/references/remote.md as local.",
        "",
      ].join("\n"),
      "utf8",
    );
    await writeFile(join(skillRoot, "references", "cache-optimization.md"), "cache", "utf8");
    await writeFile(join(skillRoot, "references", "query-missing-indexes.md"), "queries", "utf8");

    const skill = await normalizeSkill(skillRoot, pluginRoot);

    expect(skill.resources.map(({ path }) => path)).toEqual([
      "references/cache-optimization.md",
      "references/query-missing-indexes.md",
    ]);
    expect(new Set(skill.resources.map(({ digest }) => digest)).size).toBe(2);
  });

  it("fails closed for missing and escaping prose resource references", async () => {
    const owner = await createOwnedTestRoot("skill-prose-invalid");
    const pluginRoot = join(owner, "plugin");
    const skillRoot = join(pluginRoot, "skills", "review");
    await mkdir(join(skillRoot, "references"), { recursive: true });
    await writeFile(join(owner, "outside.md"), "outside", "utf8");
    const skillFile = join(skillRoot, "SKILL.md");
    const frontmatter = "---\nname: review\ndescription: Review.\n---\n";
    await writeFile(skillFile, `${frontmatter}Read references/missing.md.\n`, "utf8");
    await expect(normalizeSkill(skillRoot, pluginRoot)).rejects.toThrow();

    await writeFile(
      skillFile,
      `${frontmatter}Read references/../../../../outside.md.\n`,
      "utf8",
    );
    await expect(normalizeSkill(skillRoot, pluginRoot)).rejects.toMatchObject({
      code: "path_escape",
    });
  });

  it("normalizes composer metadata and markdown agents as immutable non-authoritative templates", async () => {
    const agents = await normalizeAgents(join(fixtureRoot, "agents"), fixtureRoot);

    expect(agents.map((agent) => `${agent.surface}:${agent.name}`)).toEqual([
      "composer_metadata:openai",
      "agent_template:reviewer",
    ]);
    expect(agents.every((agent) => agent.executionAuthority === false)).toBe(true);
    const composer = agents.find(({ surface }) => surface === "composer_metadata");
    const template = agents.find(({ surface }) => surface === "agent_template");
    expect(composer?.surface).toBe("composer_metadata");
    expect(template?.surface).toBe("agent_template");
    if (composer?.surface !== "composer_metadata" || template?.surface !== "agent_template") {
      throw new Error("expected both agent surfaces");
    }
    expect(composer.metadata).toEqual({ interface: { display_name: "Fixture agents" } });
    expect(template.instructions).toContain("# Reviewer");
    expect(Object.isFrozen(agents)).toBe(true);
    expect(Object.isFrozen(composer.metadata)).toBe(true);
  });

  it("turns only markdown command files into explicitly user-invoked actions", async () => {
    const commands = await normalizeCommands(join(fixtureRoot, "commands"), fixtureRoot);

    expect(commands.map(({ name }) => name)).toEqual(["review"]);
    expect(commands.every(({ invocation }) => invocation === "explicit_user")).toBe(true);
    expect(commands[0]?.instructions).toContain("# Review command");
    expect(commands[0]?.resources.map(({ path }) => path)).toEqual([
      "commands/_conventions.md",
      "commands/review.md.tmpl",
    ]);
    expect(Object.isFrozen(commands)).toBe(true);
    expect(Object.isFrozen(commands[0]?.resources)).toBe(true);
  });

  it("orders commands and resources by Unicode code point", async () => {
    const pluginRoot = await createPluginRoot("command-order");
    const commandsRoot = join(pluginRoot, "commands");
    await mkdir(commandsRoot);
    const astral = "\u{10000}";
    const bmp = "\uE000";
    await writeFile(join(commandsRoot, `${astral}.md`), "astral", "utf8");
    await writeFile(join(commandsRoot, `${bmp}.md`), "bmp", "utf8");
    await writeFile(join(commandsRoot, `${astral}.tmpl`), "resource", "utf8");
    await writeFile(join(commandsRoot, `${bmp}.tmpl`), "resource", "utf8");

    const commands = await normalizeCommands(commandsRoot, pluginRoot);

    expect(commands.map(({ name }) => name)).toEqual([bmp, astral]);
    expect(commands[0]?.resources.map(({ path }) => path)).toEqual([
      `commands/${bmp}.tmpl`,
      `commands/${astral}.tmpl`,
    ]);
  });
});

describe("asset normalizer", () => {
  it.each(["text/html", "image/svg+xml"])("rejects active asset MIME %s", async (mime) => {
    await expect(
      normalizeAsset(join(fixtureRoot, "assets", "logo.svg"), fixtureRoot, { mime }),
    ).rejects.toThrow("asset_unsafe");
  });

  it("admits bounded raster and plain-text assets with contained paths and digests", async () => {
    const pluginRoot = await createPluginRoot("asset-safe");
    const assetsRoot = join(pluginRoot, "assets");
    await mkdir(assetsRoot);
    await writeFile(
      join(assetsRoot, "logo.png"),
      Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    );
    await writeFile(join(assetsRoot, "notice.txt"), "plain text", "utf8");

    const image = await normalizeAsset(join(assetsRoot, "logo.png"), pluginRoot, {
      mime: "image/png",
    });
    const text = await normalizeAsset(join(assetsRoot, "notice.txt"), pluginRoot, {
      mime: "text/plain",
    });

    expect(image).toMatchObject({ path: "assets/logo.png", mime: "image/png", placeholder: false });
    expect(text).toMatchObject({ path: "assets/notice.txt", mime: "text/plain", placeholder: false });
    expect(image.digest).toMatch(/^[a-f0-9]{64}$/);
    expect(Object.isFrozen(image)).toBe(true);
  });

  it("rejects assets over the configured bound", async () => {
    const pluginRoot = await createPluginRoot("asset-size");
    const asset = join(pluginRoot, "large.txt");
    await writeFile(asset, "too large", "utf8");

    await expect(normalizeAsset(asset, pluginRoot, { mime: "text/plain", maxBytes: 3 }))
      .rejects.toThrow("asset_unsafe");
  });

  it("does not permit callers to remove the global asset bound", async () => {
    const pluginRoot = await createPluginRoot("asset-bound-cap");
    const asset = join(pluginRoot, "notice.txt");
    await writeFile(asset, "plain text", "utf8");

    await expect(normalizeAsset(asset, pluginRoot, {
      mime: "text/plain",
      maxBytes: Number.MAX_SAFE_INTEGER,
    })).rejects.toThrow("asset_unsafe");
  });

  it.each([
    ["svg.txt", "<svg xmlns=\"http://www.w3.org/2000/svg\"></svg>"],
    ["html.txt", "<!doctype html><html><body>active</body></html>"],
    ["script.txt", "<script>alert('active')</script>"],
  ])("rejects active UTF-8 content in %s even when declared text/plain", async (name, content) => {
    const pluginRoot = await createPluginRoot(`asset-active-${name.replace(".txt", "")}`);
    const asset = join(pluginRoot, name);
    await writeFile(asset, content, "utf8");

    await expect(normalizeAsset(asset, pluginRoot, { mime: "text/plain" }))
      .rejects.toThrow("asset_unsafe");
  });

  it.each([
    ["comment-svg.txt", "<!-- benign comment -->\n   <SVG xmlns=\"http://www.w3.org/2000/svg\"></SVG>"],
    ["prefix-script.txt", "Operational notes only.\n<  SCRIPT type=\"text/javascript\">alert(1)</SCRIPT>"],
    ["prefix-xml-svg.txt", "Generated illustration follows.\n<?xml version=\"1.0\"?>\n<svg></svg>"],
    ["prefix-iframe.txt", "Documentation prefix.\n<iframe src=\"https://example.com\"></iframe>"],
  ])("rejects embedded active markup in bounded text asset %s", async (name, content) => {
    const pluginRoot = await createPluginRoot(`asset-embedded-${name.replace(".txt", "")}`);
    const asset = join(pluginRoot, name);
    await writeFile(asset, content, "utf8");

    await expect(normalizeAsset(asset, pluginRoot, { mime: "text/plain" }))
      .rejects.toThrow("asset_unsafe");
  });

  it("rejects a supplied MIME that disagrees with the file extension and magic", async () => {
    const pluginRoot = await createPluginRoot("asset-mime-mismatch");
    const asset = join(pluginRoot, "logo.png");
    const markdown = join(pluginRoot, "notice.md");
    await writeFile(
      asset,
      Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    );
    await writeFile(markdown, "# Notice\n", "utf8");

    await expect(normalizeAsset(asset, pluginRoot, { mime: "image/jpeg" }))
      .rejects.toThrow("asset_unsafe");
    await expect(normalizeAsset(markdown, pluginRoot, { mime: "text/plain" }))
      .rejects.toThrow("asset_unsafe");
  });

  it("returns a Rowboat-owned placeholder for a missing optional asset", async () => {
    const pluginRoot = await createPluginRoot("asset-placeholder");

    const asset = await normalizeAsset(join(pluginRoot, "missing.png"), pluginRoot, {
      mime: "image/png",
      optional: true,
    });

    expect(asset).toEqual({
      path: "rowboat://assets/plugin-placeholder",
      mime: "image/png",
      digest: expect.stringMatching(/^[a-f0-9]{64}$/),
      placeholder: true,
      owner: "rowboat",
    });
    expect(Object.isFrozen(asset)).toBe(true);
  });

  it("does not turn an optional path escape into a placeholder", async () => {
    const owner = await createOwnedTestRoot("asset-placeholder-escape");
    const pluginRoot = join(owner, "plugin");
    await mkdir(pluginRoot);

    await expect(normalizeAsset(join(owner, "outside.png"), pluginRoot, {
      mime: "image/png",
      optional: true,
    })).rejects.toMatchObject({ code: "path_escape" });
  });
});
