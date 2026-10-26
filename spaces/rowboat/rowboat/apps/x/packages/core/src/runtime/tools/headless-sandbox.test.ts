// Modified for VibeMind (Flissel/rowboat vibemind): headless run sandbox (Teil E, fix round 3).
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { execTool } from "./exec-tool.js";
import type { ToolContext } from "./exec-tool.js";
import { createSandboxChecker, EMPTY_SANDBOX, type HeadlessSandbox } from "./headless-sandbox.js";

let root: string;
let outside: string;
const ws = (rel: string) => path.join(root, rel);

const FENCED = "# Daily\n```task\n{\"instruction\":\"x\"}\n```\n";
const LIVE_NOTE_FALSE = "---\nlive_note: false\n---\n# Old\n```tell-rowboat\n{\"instruction\":\"done\"}\n```\n";

beforeAll(async () => {
    root = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), "sandbox-ws-")));
    outside = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), "sandbox-out-")));
    for (const [rel, text] of [
        ["config/channels.json", "{\"telegram\":{\"botToken\":\"123:abc\"}}"],
        ["knowledge/People/X.md", "# X\nnotes\n"],
        ["knowledge/Daily.md", FENCED],
        ["knowledge/Old.md", LIVE_NOTE_FALSE],
        ["bg-tasks/news/task.yaml", "active: true\n"],
        ["bg-tasks/news/index.md", "# News\n"],
        ["storage/turns/t.jsonl", "{}\n"],
        ["todo.md", "- [ ] a\n"],
    ] as const) {
        await fs.mkdir(path.dirname(ws(rel)), { recursive: true });
        await fs.writeFile(ws(rel), text);
    }
});

afterAll(async () => {
    await fs.rm(root, { recursive: true, force: true });
    await fs.rm(outside, { recursive: true, force: true });
});

function checker() {
    return createSandboxChecker({
        workspaceRoot: async () => root,
        resolvePath: async (p) => path.resolve(root, p),
    });
}

const sb = (over: Partial<HeadlessSandbox> = {}): HeadlessSandbox => ({ ...EMPTY_SANDBOX, ...over });

describe("headless sandbox — reads", () => {
    it("Path A: config/channels.json and other secrets are not readable", async () => {
        const c = checker();
        for (const p of [
            "config/channels.json",
            "storage/turns/t.jsonl",
            "bg-tasks/news/task.yaml",
            "knowledge/.env",
            "knowledge/api-token.md",
            "server-key",
            "channels/telegram-state.json",
        ]) {
            await expect(c.toolError("file-readText", { path: p }, sb()), p).resolves.toMatch(/not readable/);
        }
        await expect(c.toolError("file-copy", { from: "config/channels.json", to: "knowledge/c.md" }, sb()))
            .resolves.toMatch(/not readable/);
        await expect(c.toolError("file-list", { path: "config" }, sb())).resolves.toMatch(/not readable/);
        await expect(c.toolError("parseFile", { path: "config/channels.json" }, sb())).resolves.toMatch(/not readable/);
    });

    it("normal notes stay readable; a bg-task reads its own folder", async () => {
        const c = checker();
        await expect(c.toolError("file-readText", { path: "knowledge/People/X.md" }, sb())).resolves.toBeNull();
        await expect(c.toolError("file-readText", { path: "todo.md" }, sb())).resolves.toBeNull();
        await expect(c.toolError("file-readText", { path: "bg-tasks/news/index.md" }, sb({ readDirs: ["bg-tasks/news/"] })))
            .resolves.toBeNull();
        await expect(c.toolError("file-readText", { path: "bg-tasks/other/index.md" }, sb({ readDirs: ["bg-tasks/news/"] })))
            .resolves.toMatch(/not readable/);
    });

    it("grep/glob read filter drops secret matches", async () => {
        const allowed = checker().readFilter(sb());
        expect(await allowed(ws("knowledge/People/X.md"))).toBe(true);
        expect(await allowed(ws("config/channels.json"))).toBe(false);
        expect(await allowed(ws("bg-tasks/news/task.yaml"))).toBe(false);
    });
});

describe("headless sandbox — writes", () => {
    it("Path B: bg-tasks, config, skills, todo.md, agents, storage, dotfiles and outside are not writable", async () => {
        const c = checker();
        for (const p of [
            "bg-tasks/pwn/task.yaml",
            "config/mcp.json",
            "skills/x/skill.md",
            "todo.md",
            "agents/evil.md",
            "storage/sessions/s.jsonl",
            "knowledge/.git/config",
            "knowledge/Agent Notes/user.md",
            "notes.md",
            path.join(outside, "x.md"),
        ]) {
            await expect(c.toolError("file-writeText", { path: p, data: "x" }, sb()), p).resolves.toMatch(/not writable/);
        }
        await expect(c.toolError("file-mkdir", { path: "bg-tasks/pwn" }, sb())).resolves.toMatch(/not writable/);
        await expect(c.toolError("file-rename", { from: "knowledge/People/X.md", to: "bg-tasks/pwn/task.yaml" }, sb()))
            .resolves.toMatch(/not writable/);
        await expect(c.toolError("file-remove", { path: "todo.md" }, sb())).resolves.toMatch(/not writable/);
    });

    it("notes under knowledge/ stay writable; own files and own dirs are allowed", async () => {
        const c = checker();
        await expect(c.toolError("file-editText", { path: "knowledge/People/X.md", oldString: "notes", newString: "more" }, sb()))
            .resolves.toBeNull();
        await expect(c.toolError("file-writeText", { path: "bg-tasks/news/index.md", data: "# N" }, sb({ ownFiles: ["bg-tasks/news/index.md"] })))
            .resolves.toBeNull();
        await expect(c.toolError("file-writeText", { path: "bg-tasks/news/task.yaml", data: "x" }, sb({ ownFiles: ["bg-tasks/news/index.md"] })))
            .resolves.toMatch(/not writable/);
        await expect(c.toolError("file-writeText", { path: "pre-built/email-draft/state.json", data: "{}" }, sb({ ownDirs: ["pre-built/email-draft/"] })))
            .resolves.toBeNull();
        await expect(c.toolError("file-writeText", { path: "knowledge/Agent Notes/inbox.md", data: "x" }, sb({ ownDirs: ["knowledge/Agent Notes/"] })))
            .resolves.toBeNull();
    });

    it("tagging may not edit a note that already carries a trigger; the inline-task agent may edit its own target", async () => {
        const c = checker();
        // note_tagging_agent: no own files.
        await expect(c.toolError("file-editText", { path: "knowledge/Old.md", oldString: "live_note: false", newString: "live_note: true" }, sb()))
            .resolves.toMatch(/background agent/);
        await expect(c.toolError("file-editText", { path: "knowledge/Daily.md", oldString: "# Daily", newString: "# D" }, sb()))
            .resolves.toMatch(/background agent/);
        await expect(c.toolError("file-writeText", { path: "knowledge/Daily.md", data: "# plain" }, sb()))
            .resolves.toMatch(/background agent/);
        await expect(c.toolError("file-rename", { from: "knowledge/Daily.md", to: "knowledge/D2.md" }, sb()))
            .resolves.toMatch(/background agent/);
        // inline-task / live-note agent on its own target note.
        await expect(c.toolError("file-editText", { path: "knowledge/Daily.md", oldString: "# Daily", newString: "# D" }, sb({ ownFiles: ["knowledge/Daily.md"] })))
            .resolves.toBeNull();
    });
});

describe("execTool enforces the sandbox for builtins", () => {
    function ctx(headlessSandbox?: HeadlessSandbox): ToolContext {
        return {
            runId: "t1",
            toolCallId: "c1",
            signal: new AbortController().signal,
            abortRegistry: {} as ToolContext["abortRegistry"],
            publish: async () => undefined,
            ...(headlessSandbox ? { headlessSandbox } : {}),
        };
    }

    it("refuses a sandboxed write outside the workspace before touching the disk; desktop unchanged", async () => {
        const target = path.join(outside, "pwn.md");
        const refused = (await execTool({ type: "builtin", name: "file-writeText" }, { path: target, data: "x" }, ctx(sb()))) as { error?: string };
        expect(refused.error).toMatch(/not writable/);
        await expect(fs.access(target)).rejects.toThrow();

        const desktop = (await execTool({ type: "builtin", name: "file-writeText" }, { path: target, data: "x" }, ctx())) as { error?: string };
        expect(desktop.error).toBeUndefined();
        expect(await fs.readFile(target, "utf8")).toBe("x");
    });

    it("filters grep / glob matches through the sandbox read rule", async () => {
        await fs.mkdir(path.join(outside, "g"), { recursive: true });
        await fs.writeFile(path.join(outside, "g", "note.md"), "needle\n");
        await fs.writeFile(path.join(outside, "g", "api-token.txt"), "needle\n");
        const grep = (c: ToolContext) =>
            execTool({ type: "builtin", name: "file-grep" }, { pattern: "needle", searchPath: path.join(outside, "g") }, c) as Promise<{ count: number }>;
        expect((await grep(ctx())).count).toBe(2);
        expect((await grep(ctx(sb()))).count).toBe(1);
        const glob = (c: ToolContext) =>
            execTool({ type: "builtin", name: "file-glob" }, { pattern: "*", cwd: path.join(outside, "g") }, c) as Promise<{ count: number }>;
        expect((await glob(ctx())).count).toBe(2);
        expect((await glob(ctx(sb()))).count).toBe(1);
    });

    it("refuses a sandboxed read of config/channels.json", async () => {
        const refused = (await execTool({ type: "builtin", name: "file-readText" }, { path: "config/channels.json" }, ctx(sb()))) as { error?: string };
        expect(refused.error).toMatch(/not readable/);
    });
});
