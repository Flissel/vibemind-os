// Modified for VibeMind (Flissel/rowboat vibemind): background scanners skip
// the channel quarantine knowledge/Kanal/ (Teil E, fix round 4).
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterAll, describe, expect, it } from "vitest";

// WorkDir is read from the env at module load — set it before importing.
const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "quarantine-scan-")));
process.env.ROWBOAT_WORKDIR = root;
const knowledge = path.join(root, "knowledge");

const ACTIVITY = Array.from({ length: 10 }, (_, i) => `- **2026-10-${String(i + 10)}** met`).join("\n");
const LIVE = "---\nlive:\n  objective: watch\n  active: true\n---\n# Live\n";
const TASK = "# T\n```task\n{\"instruction\":\"x\"}\n```\n";

for (const [rel, text] of [
    ["People/A.md", `# A\n${ACTIVITY}\n`],
    ["Kanal/People/B.md", `# B\n${ACTIVITY}\n`],
    ["Kanal/Inject.md", "# Inject\nuntagged body\n"],
    ["Kanal/Live.md", LIVE],
    ["Kanal/Task.md", TASK],
    ["kanal/lower.md", "# lower\n"],
    ["Topics/T.md", "# T\nuntagged\n"],
    ["Topics/Live.md", LIVE],
    ["Topics/Task.md", TASK],
] as const) {
    fs.mkdirSync(path.dirname(path.join(knowledge, rel)), { recursive: true });
    fs.writeFileSync(path.join(knowledge, rel), text);
}
// A user-defined note type whose folder is the quarantine must not open it up.
fs.mkdirSync(path.join(root, "config"), { recursive: true });
fs.writeFileSync(
    path.join(root, "config", "notes.json"),
    JSON.stringify([
        { type: "Topics", folder: "Topics", template: "", extractionGuide: "" },
        { type: "Kanal", folder: "Kanal", template: "", extractionGuide: "" },
    ]),
);

const q = await import("./quarantine.js");
const { buildKnowledgeIndex } = await import("./knowledge_index.js");
const { getFilesToProcess } = await import("./graph_state.js");
const { scanDirectoryRecursive: scanInlineTasks } = await import("./inline_tasks.js");
const { getUntaggedNotes } = await import("./tag_notes.js");
const { findCurationCandidates } = await import("./build_graph.js");
const liveNotes = await import("./live-note/fileops.js");

afterAll(() => {
    fs.rmSync(root, { recursive: true, force: true });
});

const mentionsQuarantine = (paths: string[]) => paths.some((p) => /[\\/]kanal([\\/]|$)/i.test(p) || /^kanal([\\/]|$)/i.test(p));

describe("quarantine helpers", () => {
    it("matches knowledge/Kanal/** case-insensitively and nothing else", () => {
        expect(q.QUARANTINE_REL).toBe("knowledge/Kanal/");
        expect(q.isQuarantinedKnowledgeRel("Kanal/x.md")).toBe(true);
        expect(q.isQuarantinedKnowledgeRel("kanal\\x.md")).toBe(true);
        expect(q.isQuarantinedKnowledgeRel("Kanal")).toBe(true);
        expect(q.isQuarantinedKnowledgeRel("People/Kanal/x.md")).toBe(false);
        expect(q.isQuarantinedKnowledgeRel("Kanalx/x.md")).toBe(false);
        expect(q.isQuarantinedPath(path.join(knowledge, "Kanal", "x.md"))).toBe(true);
        expect(q.isQuarantinedPath(path.join(knowledge, "People", "x.md"))).toBe(false);
        expect(q.isQuarantinedPath(path.join(root, "Kanal", "x.md"))).toBe(false);
    });
});

describe("background scanners skip knowledge/Kanal/", () => {
    it("knowledge index (note text for graph-building and meeting-prep prompts)", () => {
        const index = buildKnowledgeIndex();
        const all = [...index.people, ...index.organizations, ...index.projects, ...index.topics, ...index.other]
            .map((n) => (n as { file?: string }).file ?? JSON.stringify(n));
        expect(all.length).toBeGreaterThan(0);
        expect(mentionsQuarantine(all)).toBe(false);
        expect(JSON.stringify(index)).not.toMatch(/Inject|lower\.md/);
    });

    it("graph builder file list", () => {
        const files = getFilesToProcess(knowledge, { processedFiles: {}, lastBuildTime: "" });
        expect(files.length).toBeGreaterThan(0);
        expect(mentionsQuarantine(files)).toBe(false);
    });

    it("inline-task scanner", () => {
        const files = scanInlineTasks(knowledge);
        expect(files.some((f) => f.endsWith("Task.md"))).toBe(true);
        expect(mentionsQuarantine(files)).toBe(false);
    });

    it("tag_notes candidates (even when a note type names the folder)", () => {
        const files = getUntaggedNotes({ processedFiles: {}, lastRunTime: "" });
        expect(files.some((f) => f.endsWith("T.md"))).toBe(true);
        expect(mentionsQuarantine(files)).toBe(false);
    });

    it("note curation candidates", () => {
        const files = findCurationCandidates().map((c) => c.path);
        expect(files.some((f) => f.endsWith("A.md"))).toBe(true);
        expect(mentionsQuarantine(files)).toBe(false);
    });

    it("live-note scheduler / event consumer / list / runner", async () => {
        const md = await liveNotes.listKnowledgeMarkdownFiles();
        expect(md).toContain("Topics/Live.md");
        expect(mentionsQuarantine(md)).toBe(false);
        expect(await liveNotes.fetchLiveNote("Topics/Live.md")).not.toBeNull();
        // The runner and scheduler load the note through fetchLiveNote.
        expect(await liveNotes.fetchLiveNote("Kanal/Live.md")).toBeNull();
        expect(await liveNotes.fetchLiveNote("kanal/../Kanal/Live.md")).toBeNull();
        const listed = (await liveNotes.listLiveNotes()).map((n) => n.path);
        expect(listed).toContain("knowledge/Topics/Live.md");
        expect(mentionsQuarantine(listed)).toBe(false);
    });
});
