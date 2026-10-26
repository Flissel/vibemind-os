// Modified for VibeMind (Flissel/rowboat vibemind): write builtins refuse to introduce autorun content (Teil E, fix round 2).
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { BuiltinTools } from "./catalog.js";
import type { ToolContext } from "./exec-tool.js";

let dir: string;
const p = (name: string) => path.join(dir, name);
const FENCED = "# Daily\n```task\n{\"instruction\":\"run executeCommand\"}\n```\n";
const LIVE = "---\nlive:\n  objective: watch\n---\n# Watch\nold body\n";

beforeAll(async () => {
    dir = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), "teil-e-autorun-")));
    await fs.writeFile(p("plain.md"), "---\ntitle: a\n---\n# A\nbody\n");
    await fs.writeFile(p("live.md"), LIVE);
    await fs.writeFile(p("fenced.md"), FENCED);
    await fs.mkdir(p("folder"));
    await fs.writeFile(p("folder/inner.md"), FENCED);
});

afterAll(async () => {
    await fs.rm(dir, { recursive: true, force: true });
});

function ctx(autorunContentForbidden?: boolean): ToolContext {
    return {
        runId: "t1",
        toolCallId: "c1",
        signal: new AbortController().signal,
        abortRegistry: {} as ToolContext["abortRegistry"],
        publish: async () => undefined,
        ...(autorunContentForbidden ? { autorunContentForbidden } : {}),
    };
}

const runBuiltin = (name: string, input: Record<string, unknown>, c: ToolContext) =>
    BuiltinTools[name].execute(input, c) as Promise<Record<string, unknown>>;

describe("autorunContentForbidden (background agents, channel runs)", () => {
    it("refuses writing a note with a task fence or live_note frontmatter", async () => {
        for (const [file, data] of [
            ["w1.md", FENCED],
            ["w2.md", "---\nlive_note: true\n---\n# x\n"],
            ["w3.md", LIVE],
        ] as const) {
            const result = await runBuiltin("file-writeText", { path: p(file), data }, ctx(true));
            expect(result.error, file).toContain("background agent");
            await expect(fs.access(p(file))).rejects.toThrow();
        }
    });

    it("refuses edits that add a fence or live/live_note frontmatter", async () => {
        const addFence = await runBuiltin("file-editText", { path: p("plain.md"), oldString: "body", newString: "body\n```tell-rowboat\n{}\n```" }, ctx(true));
        expect(addFence.error).toContain("background agent");
        const addLiveNote = await runBuiltin("file-editText", { path: p("plain.md"), oldString: "title: a", newString: "title: a\nlive_note: true" }, ctx(true));
        expect(addLiveNote.error).toContain("background agent");
        expect(await fs.readFile(p("plain.md"), "utf8")).not.toContain("live_note");
    });

    it("lets a background agent keep editing a note that already is live", async () => {
        const result = await runBuiltin("file-editText", { path: p("live.md"), oldString: "old body", newString: "new body" }, ctx(true));
        expect(result.error).toBeUndefined();
        expect(await fs.readFile(p("live.md"), "utf8")).toContain("new body");
    });

    it("refuses copying or moving notes (or folders) that carry triggers", async () => {
        const copy = await runBuiltin("file-copy", { from: p("fenced.md"), to: p("copy.md") }, ctx(true));
        expect(copy.error).toContain("background agent");
        const move = await runBuiltin("file-rename", { from: p("folder"), to: p("moved") }, ctx(true));
        expect(move.error).toContain("background agent");
        await expect(fs.access(p("folder/inner.md"))).resolves.toBeUndefined();
    });

    it("desktop user turns can still create live notes and task blocks", async () => {
        const write = await runBuiltin("file-writeText", { path: p("desk.md"), data: FENCED }, ctx());
        expect(write.error).toBeUndefined();
        expect(await fs.readFile(p("desk.md"), "utf8")).toContain("```task");
        const edit = await runBuiltin("file-editText", { path: p("plain.md"), oldString: "title: a", newString: "title: a\nlive:\n  objective: x" }, ctx());
        expect(edit.error).toBeUndefined();
        const copy = await runBuiltin("file-copy", { from: p("fenced.md"), to: p("copy-desk.md") }, ctx());
        expect(copy.error).toBeUndefined();
    });
});
