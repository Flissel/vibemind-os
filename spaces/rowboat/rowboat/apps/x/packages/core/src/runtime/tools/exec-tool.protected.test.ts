// Modified for VibeMind (Flissel/rowboat vibemind): execTool refuses protected writes without a human (ticket #330).
import fsSync from "node:fs";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterAll, beforeEach, describe, expect, it, vi } from "vitest";

const root = fsSync.realpathSync(fsSync.mkdtempSync(path.join(os.tmpdir(), "exec-protected-ws-")));
process.env.ROWBOAT_WORKDIR = root;

// No git commits of knowledge/ from a test workspace.
vi.mock("../../knowledge/version_history.js", () => ({
    commitAll: async () => undefined,
    initRepo: async () => undefined,
}));

// The catalog sweep table below must never start a live-note agent or
// upload to a space, even if the gate were missing.
vi.mock("../../knowledge/live-note/runner.js", () => ({
    runLiveNoteAgent: async () => ({ runId: "stub", action: "none", summary: "stub", contentAfter: "STUB-RAN", error: undefined }),
}));
vi.mock("../../spaces/orgs.js", () => ({
    getClient: () => ({ uploadBlob: async () => ({ hash: "stub", size: 0, mime: "text/plain" }) }),
}));

const { execTool } = await import("./exec-tool.js");
const { RealPermissionChecker } = await import("../turns/bridges/real-permission-checker.js");
const { EMPTY_SANDBOX } = await import("./headless-sandbox.js");
const { InMemoryAbortRegistry } = await import("../turns/abort-registry.js");
type Ctx = NonNullable<Parameters<typeof execTool>[2]>;

const ws = (rel: string) => path.join(root, rel);

function ctx(extra: Partial<Ctx> = {}): Ctx {
    return {
        runId: "t1",
        toolCallId: "c1",
        signal: new AbortController().signal,
        abortRegistry: new InMemoryAbortRegistry(),
        publish: async () => undefined,
        ...extra,
    };
}

const builtin = (name: string) => ({ type: "builtin" as const, name });

beforeEach(async () => {
    await fs.rm(ws("config"), { recursive: true, force: true });
    await fs.mkdir(ws("config"), { recursive: true });
    await fs.writeFile(ws("config/mcp.json"), "{\"orig\":true}");
    await fs.writeFile(ws("config/security.json"), "[\"ls\"]");
    await fs.mkdir(ws("knowledge"), { recursive: true });
    await fs.writeFile(ws("knowledge/x.md"), "# x\n");
});

afterAll(async () => {
    await fs.rm(root, { recursive: true, force: true });
});

async function refused(name: string, input: Record<string, unknown>, extra: Partial<Ctx> = {}): Promise<string | undefined> {
    const result = (await execTool(builtin(name), input, ctx(extra))) as { error?: string } | null;
    return result?.error;
}

describe("execTool protected writes", () => {
    it("refuses file-writeText config/mcp.json without humanApproved and leaves the file", async () => {
        expect(await refused("file-writeText", { path: "config/mcp.json", data: "{\"pwn\":1}" })).toMatch(
            /protected .*human/i,
        );
        expect(await fs.readFile(ws("config/mcp.json"), "utf8")).toBe("{\"orig\":true}");
    });

    it("refuses when humanApproved is anything but true", async () => {
        const sneaky = { humanApproved: "yes" } as unknown as Partial<Ctx>;
        expect(await refused("file-writeText", { path: "config/mcp.json", data: "x" }, sneaky)).toMatch(/protected/i);
    });

    it("writes config/slack.json when a human approved the call", async () => {
        expect(await refused("file-writeText", { path: "config/slack.json", data: "{}" }, { humanApproved: true })).toBeUndefined();
        expect(await fs.readFile(ws("config/slack.json"), "utf8")).toBe("{}");
    });

    it("refuses every path variant and every write tool", async () => {
        const cases: Array<[string, Record<string, unknown>]> = [
            ["file-writeText", { path: "CONFIG/mcp.json", data: "x" }],
            ["file-writeText", { path: "knowledge/../config/mcp.json", data: "x" }],
            ["file-writeText", { path: "server-key::$DATA", data: "x" }],
            ["file-editText", { path: "config/mcp.json", oldString: "orig", newString: "pwn" }],
            ["file-rename", { from: "config/mcp.json", to: "knowledge/m.json" }],
            ["file-rename", { from: "knowledge/x.md", to: "config/mcp.json" }],
            ["file-copy", { from: "knowledge/x.md", to: "config/mcp.json" }],
            ["file-remove", { path: "config/security.json" }],
            ["file-mkdir", { path: "config/sub" }],
            ["text-to-speech", { text: "hi", outputPath: "config/a.mp3" }],
            ["deck-create", { path: "config/d.pptx", title: "t", slides: [] }],
            ["spreadsheet-create", { path: "config/s.csv" }],
        ];
        for (const [name, input] of cases) {
            expect(await refused(name, input), `${name} ${JSON.stringify(input)}`).toMatch(/protected/i);
        }
        expect(await fs.readFile(ws("config/mcp.json"), "utf8")).toBe("{\"orig\":true}");
        expect(await fs.readFile(ws("config/security.json"), "utf8")).toBe("[\"ls\"]");
        expect(fsSync.existsSync(ws("knowledge/m.json"))).toBe(false);
        expect(fsSync.existsSync(ws("config/sub"))).toBe(false);
        expect(fsSync.existsSync(ws("config/a.mp3"))).toBe(false);
    });

    it("refuses an executeCommand that names a protected entry (legacy engine ctx: never humanApproved)", async () => {
        const legacy = { autorunContentForbidden: true, headlessSandbox: EMPTY_SANDBOX };
        expect(await refused("executeCommand", { command: "echo [\"*\"] > config\\security.json" }, legacy)).toMatch(/protected/i);
        expect(await refused("executeCommand", { command: "echo x > security.json", cwd: "config" }, legacy)).toMatch(/protected/i);
        expect(await fs.readFile(ws("config/security.json"), "utf8")).toBe("[\"ls\"]");
    });

    it("fix round 2: refuses an executeCommand that names the own server without humanApproved (never runs)", async () => {
        const legacy = { autorunContentForbidden: true, headlessSandbox: EMPTY_SANDBOX };
        // echo, not curl: should the gate ever fail, nothing reaches the network.
        for (const command of ["echo http://127.0.0.1:3220/rpc/workspace:writeFile", "echo localhost:3221"]) {
            expect(await refused("executeCommand", { command }, legacy), command).toMatch(/own local server/i);
        }
    });

    it("code_agent_run refuses the workspace root or config/ as cwd without humanApproved", async () => {
        for (const cwd of [root, ws("config")]) {
            expect(await refused("code_agent_run", { agent: "claude", cwd, prompt: "edit mcp.json" }), cwd).toMatch(/protected/i);
        }
    });

    it("code_agent_run also checks the effective cwd (session / chip cwd the model did not name)", async () => {
        await expect(
            execTool(builtin("code_agent_run"), { agent: "claude", prompt: "edit mcp.json" }, ctx({ codeCwd: root })),
        ).rejects.toThrow(/protected/i);
    });

    it("fix round 1 (M2): without a cwd argument the error says that passing the directory as cwd asks the human", async () => {
        await expect(
            execTool(builtin("code_agent_run"), { agent: "claude", prompt: "x" }, ctx({ codeCwd: root })),
        ).rejects.toThrow(/pass .*as the `cwd` argument.*permission prompt/i);
    });

    it("code_agent_run: an approval binds to the directory the request named, not the one that would run", async () => {
        const elsewhere = fsSync.realpathSync(fsSync.mkdtempSync(path.join(os.tmpdir(), "code-cwd-a-")));
        try {
            // The request was computed from cwd A (unprotected: no prompt at all) ...
            const { getToolPermissionMetadata } = await import("../assembly/permission-metadata.js");
            const call = { type: "tool-call" as const, toolCallId: "c1", toolName: "code_agent_run", arguments: { agent: "claude", cwd: elsewhere, prompt: "x" } };
            expect(await getToolPermissionMetadata(call, builtin("code_agent_run"), new Set(), [])).toBeNull();
            // ... but the chip / session would run it in the workspace root: refused, even marked approved.
            await expect(
                execTool(builtin("code_agent_run"), { agent: "claude", cwd: elsewhere, prompt: "x" }, ctx({ codeCwd: root, humanApproved: true })),
            ).rejects.toThrow(/protected/i);
            // Approved for config/, run in the root: refused as well.
            await expect(
                execTool(builtin("code_agent_run"), { agent: "claude", cwd: ws("config"), prompt: "x" }, ctx({ codeCwd: root, humanApproved: true })),
            ).rejects.toThrow(/protected/i);
        } finally {
            await fs.rm(elsewhere, { recursive: true, force: true });
        }
    });

    it("code_agent_run rejects a relative cwd argument outright", async () => {
        await expect(
            execTool(builtin("code_agent_run"), { agent: "claude", cwd: "knowledge", prompt: "x" }, ctx({ humanApproved: true })),
        ).rejects.toThrow(/absolute/i);
    });

    it("leaves ordinary writes alone", async () => {
        expect(await refused("file-writeText", { path: "knowledge/y.md", data: "# y\n" })).toBeUndefined();
        expect(await fs.readFile(ws("knowledge/y.md"), "utf8")).toBe("# y\n");
    });
});

describe("execTool protected reads (fix round 1, review C1)", () => {
    beforeEach(async () => {
        await fs.writeFile(ws("server-key"), "KEYVALUE-not-a-real-key\n");
    });

    it("refuses reading server-key, config/ and copying out of config/ without humanApproved", async () => {
        const cases: Array<[string, Record<string, unknown>]> = [
            ["file-readText", { path: "server-key" }],
            ["file-readText", { path: "config/mcp.json" }],
            ["file-readText", { path: "Server-Key." }],
            ["parseFile", { path: "server-key" }],
            ["file-copy", { from: "config/mcp.json", to: "knowledge/m.json" }],
            ["file-list", { path: "config" }],
            ["file-grep", { pattern: ".", searchPath: "config" }],
            ["file-glob", { pattern: "*", cwd: "config" }],
        ];
        for (const [name, input] of cases) {
            expect(await refused(name, input), `${name} ${JSON.stringify(input)}`).toMatch(/protected/i);
        }
        expect(fsSync.existsSync(ws("knowledge/m.json"))).toBe(false);
    });

    it("reads server-key when a human approved the call", async () => {
        const result = (await execTool(builtin("file-readText"), { path: "server-key" }, ctx({ humanApproved: true }))) as {
            content?: string;
            error?: string;
        };
        expect(result.error).toBeUndefined();
        expect(JSON.stringify(result)).toContain("KEYVALUE");
    });

    it("a workspace-wide grep never returns lines of protected files", async () => {
        await fs.writeFile(ws("knowledge/k.md"), "KEYVALUE in a note\n");
        const result = (await execTool(builtin("file-grep"), { pattern: "KEYVALUE" }, ctx())) as {
            matches?: Array<{ file?: string; resolvedPath?: string }>;
            error?: string;
        };
        expect(result.error).toBeUndefined();
        const text = JSON.stringify(result);
        expect(text).toContain("k.md");
        expect(text).not.toContain("server-key");
    });

    // Fix round 1, catalog sweep: every builtin that reads a local file's
    // content. Directory listings without content (file-list / file-glob on
    // the root) stay unguarded.
    const LOCAL_READERS: Array<[string, Record<string, unknown>]> = [
        ["file-readText", { path: "server-key" }],
        ["parseFile", { path: "server-key" }],
        ["LLMParse", { path: "server-key" }],
        ["deck-review", { path: "server-key" }],
        ["transcribe-audio", { path: "server-key" }],
        ["file-exists", { path: "server-key" }],
        ["file-stat", { path: "server-key" }],
        ["file-copy", { from: "server-key", to: "knowledge/k.txt" }],
        ["file-rename", { from: "server-key", to: "knowledge/k.txt" }],
        ["file-grep", { pattern: ".", searchPath: "server-key" }],
        ["spaces-upload-blob", { spaceId: "s", filePath: "server-key" }],
        ["run-live-note-agent", { filePath: "knowledge/../server-key" }],
        // A junction apps/evil/data -> workspace root (set up below).
        ["app-read-data", { appFolder: "evil", file: "server-key" }],
        // analyzeAgent always appends ".md": the nearest protected target.
        ["analyzeAgent", { agentName: "../config/mcp" }],
    ];

    it("every local reader is human-only in the checker and refused by execTool for server-key", async () => {
        await fs.mkdir(ws("apps/evil"), { recursive: true });
        await fs.writeFile(ws("apps/evil/rowboat-app.json"), "{}");
        await fs.rm(ws("apps/evil/data"), { recursive: true, force: true });
        await fs.symlink(root, ws("apps/evil/data"), process.platform === "win32" ? "junction" : "dir");
        const checker = new RealPermissionChecker();
        for (const [name, input] of LOCAL_READERS) {
            const label = `${name} ${JSON.stringify(input)}`;
            expect(
                await checker.check({ turnId: "t", toolCallId: "c", toolId: `builtin:${name}`, toolName: name, input }),
                label,
            ).toMatchObject({ required: true, request: { humanOnly: true } });
            const result = await execTool(builtin(name), input, ctx());
            expect(JSON.stringify(result), label).toMatch(/protected workspace path/);
            expect(JSON.stringify(result), label).not.toMatch(/KEYVALUE|STUB-RAN/);
        }
        expect(fsSync.existsSync(ws("server-key"))).toBe(true);
        expect(fsSync.existsSync(ws("knowledge/k.txt"))).toBe(false);
    });

    it("leaves ordinary reads alone", async () => {
        expect(await refused("file-readText", { path: "knowledge/x.md" })).toBeUndefined();
        expect(await refused("file-list", { path: "." })).toBeUndefined();
        expect(await refused("file-copy", { from: "knowledge/x.md", to: "knowledge/x2.md" })).toBeUndefined();
    });
});
