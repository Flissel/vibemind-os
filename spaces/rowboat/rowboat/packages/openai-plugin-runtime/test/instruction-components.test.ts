import { mkdir, symlink, writeFile } from "node:fs/promises";
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

async function createDirectoryLink(target: string, link: string): Promise<void> {
  await symlink(target, link, process.platform === "win32" ? "junction" : "dir");
}

function validPng(): Buffer {
  const signature = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  const ihdr = Buffer.alloc(25);
  ihdr.writeUInt32BE(13, 0);
  ihdr.write("IHDR", 4, "ascii");
  ihdr.writeUInt32BE(1, 8);
  ihdr.writeUInt32BE(1, 12);
  ihdr[16] = 8;
  ihdr[17] = 6;
  const iend = Buffer.alloc(12);
  iend.write("IEND", 4, "ascii");
  return Buffer.concat([signature, ihdr, iend]);
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

  it("rejects a skill root, SKILL file, or resource that crosses a link namespace", async () => {
    const pluginRoot = await createPluginRoot("skill-links");
    const skillsRoot = join(pluginRoot, "skills");
    const hiddenSkill = join(pluginRoot, "hidden-skill");
    await mkdir(skillsRoot);
    await mkdir(hiddenSkill);
    await writeFile(
      join(hiddenSkill, "SKILL.md"),
      "---\nname: hidden\ndescription: Hidden.\n---\n# Hidden\n",
      "utf8",
    );
    const linkedRoot = join(skillsRoot, "linked");
    await createDirectoryLink(hiddenSkill, linkedRoot);
    await expect(normalizeSkill(linkedRoot, pluginRoot)).rejects.toMatchObject({ code: "path_escape" });

    const fileLinkedSkill = join(skillsRoot, "file-linked");
    await mkdir(fileLinkedSkill);
    await writeFile(
      join(fileLinkedSkill, "actual.md"),
      "---\nname: file-linked\ndescription: Linked.\n---\n# Linked\n",
      "utf8",
    );
    await symlink(join(fileLinkedSkill, "actual.md"), join(fileLinkedSkill, "SKILL.md"), "file");
    await expect(normalizeSkill(fileLinkedSkill, pluginRoot)).rejects.toMatchObject({ code: "path_escape" });

    const resourceSkill = join(skillsRoot, "resource-linked");
    await mkdir(join(resourceSkill, "references"), { recursive: true });
    await writeFile(
      join(resourceSkill, "SKILL.md"),
      "---\nname: resource-linked\ndescription: Linked resource.\n---\nRead references/rules.md.\n",
      "utf8",
    );
    await writeFile(join(resourceSkill, "references", "actual.md"), "rules", "utf8");
    await symlink(
      join(resourceSkill, "references", "actual.md"),
      join(resourceSkill, "references", "rules.md"),
      "file",
    );
    await expect(normalizeSkill(resourceSkill, pluginRoot)).rejects.toMatchObject({ code: "path_escape" });
  });

  it.each(["../escape", "/absolute"])("rejects unsafe normalized skill name %s", async (name) => {
    const pluginRoot = await createPluginRoot("skill-unsafe-name");
    const skillRoot = join(pluginRoot, "skills", "review");
    await mkdir(skillRoot, { recursive: true });
    await writeFile(
      join(skillRoot, "SKILL.md"),
      `---\nname: ${JSON.stringify(name)}\ndescription: Unsafe name.\n---\n# Skill\n`,
      "utf8",
    );

    await expect(normalizeSkill(skillRoot, pluginRoot)).rejects.toMatchObject({ code: "path_escape" });
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

  it("rejects command directory junctions and linked command files, including in-root targets", async () => {
    const pluginRoot = await createPluginRoot("command-links");
    const hiddenCommands = join(pluginRoot, "hidden-commands");
    await mkdir(hiddenCommands);
    await writeFile(join(hiddenCommands, "review.md"), "# Hidden command", "utf8");
    const linkedCommands = join(pluginRoot, "commands-linked");
    await createDirectoryLink(hiddenCommands, linkedCommands);
    await expect(normalizeCommands(linkedCommands, pluginRoot)).rejects.toMatchObject({ code: "path_escape" });

    const commandsRoot = join(pluginRoot, "commands");
    await mkdir(commandsRoot);
    await symlink(join(hiddenCommands, "review.md"), join(commandsRoot, "review.md"), "file");
    await expect(normalizeCommands(commandsRoot, pluginRoot)).rejects.toMatchObject({ code: "path_escape" });
  });

  it("rejects agent root junctions and linked agent files, including in-root targets", async () => {
    const pluginRoot = await createPluginRoot("agent-links");
    const hiddenAgents = join(pluginRoot, "hidden-agents");
    await mkdir(hiddenAgents);
    await writeFile(join(hiddenAgents, "reviewer.md"), "# Hidden agent", "utf8");
    const linkedAgents = join(pluginRoot, "agents-linked");
    await createDirectoryLink(hiddenAgents, linkedAgents);
    await expect(normalizeAgents(linkedAgents, pluginRoot)).rejects.toMatchObject({ code: "path_escape" });

    const agentsRoot = join(pluginRoot, "agents");
    await mkdir(agentsRoot);
    await symlink(join(hiddenAgents, "reviewer.md"), join(agentsRoot, "reviewer.md"), "file");
    await expect(normalizeAgents(agentsRoot, pluginRoot)).rejects.toMatchObject({ code: "path_escape" });
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
      validPng(),
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

  it.each([
    ["truncated.png", "image/png", Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])],
    ["truncated.jpg", "image/jpeg", Buffer.from([0xff, 0xd8, 0xff])],
    ["truncated.gif", "image/gif", Buffer.from("GIF89a", "ascii")],
    ["truncated.webp", "image/webp", Buffer.from("RIFF\x04\x00\x00\x00WEBP", "binary")],
  ])("rejects structurally truncated raster %s", async (name, mime, bytes) => {
    const pluginRoot = await createPluginRoot(`asset-truncated-${name.replace(".", "-")}`);
    const asset = join(pluginRoot, name);
    await writeFile(asset, bytes);

    await expect(normalizeAsset(asset, pluginRoot, { mime })).rejects.toThrow("asset_unsafe");
  });

  it.each([
    "[run](javascript:alert(1))",
    "![payload](data:image/svg+xml,%3Csvg%3E%3C/svg%3E)",
    "<vbscript:msgbox(1)>",
    "[local](file:///etc/passwd)",
    "[obfuscated](java&#x73;cript:alert(1))",
    "[encoded](javascript%3Aalert(1))",
  ])("rejects active Markdown destination %s", async (markdown) => {
    const pluginRoot = await createPluginRoot("asset-markdown-active");
    const asset = join(pluginRoot, "notice.md");
    await writeFile(asset, markdown, "utf8");

    await expect(normalizeAsset(asset, pluginRoot, { mime: "text/markdown" }))
      .rejects.toThrow("asset_unsafe");
  });

  it("admits passive Markdown destinations", async () => {
    const pluginRoot = await createPluginRoot("asset-markdown-passive");
    const asset = join(pluginRoot, "notice.md");
    await writeFile(
      asset,
      "[docs](https://example.com/docs) [mail](mailto:team@example.com) [local](./guide.txt)\n",
      "utf8",
    );

    await expect(normalizeAsset(asset, pluginRoot, { mime: "text/markdown" }))
      .resolves.toMatchObject({ mime: "text/markdown", placeholder: false });
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

  it("rejects an optional missing asset beneath a linked ancestor", async () => {
    const owner = await createOwnedTestRoot("asset-placeholder-link");
    const pluginRoot = join(owner, "plugin");
    const outside = join(owner, "outside");
    await mkdir(pluginRoot);
    await mkdir(outside);
    const linkedAssets = join(pluginRoot, "assets");
    await createDirectoryLink(outside, linkedAssets);

    await expect(normalizeAsset(join(linkedAssets, "missing.png"), pluginRoot, {
      mime: "image/png",
      optional: true,
    })).rejects.toMatchObject({ code: "path_escape" });
  });
});
