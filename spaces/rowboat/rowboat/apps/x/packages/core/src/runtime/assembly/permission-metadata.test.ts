// Modified for VibeMind (Flissel/rowboat vibemind): humanOnly requests for protected paths (ticket #330).
import fsSync from "node:fs";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterAll, beforeEach, describe, expect, it } from "vitest";

const root = fsSync.realpathSync(fsSync.mkdtempSync(path.join(os.tmpdir(), "perm-meta-ws-")));
process.env.ROWBOAT_WORKDIR = root;

const { RealPermissionChecker } = await import("../turns/bridges/real-permission-checker.js");
const { resetSecurityAllowListCache } = await import("../../config/security.js");

async function writeSecurity(payload: unknown): Promise<void> {
    await fs.mkdir(path.join(root, "config"), { recursive: true });
    await fs.writeFile(path.join(root, "config", "security.json"), JSON.stringify(payload));
    resetSecurityAllowListCache();
}

beforeEach(async () => {
    await writeSecurity({ allowedCommands: ["dir", "type", "echo", "awk", "cat"], allowedFileAccess: [] });
});

afterAll(async () => {
    await fs.rm(root, { recursive: true, force: true });
});

const checker = new RealPermissionChecker();
const check = (toolName: string, input: Record<string, unknown>) =>
    checker.check({ turnId: "t", toolCallId: "c", toolId: `builtin:${toolName}`, toolName, input });

describe("file tools on protected paths", () => {
    it("file-writeText config/mcp.json needs a human-only file request", async () => {
        const result = await check("file-writeText", { path: "config/mcp.json", data: "{}" });
        expect(result).toMatchObject({
            required: true,
            request: { kind: "file", operation: "write", humanOnly: true },
        });
        const request = (result as unknown as { request: { paths: string[] } }).request;
        expect(request.paths).toEqual([path.join(root, "config", "mcp.json")]);
    });

    it("stays human-only although a stored grant covers the whole workspace", async () => {
        await writeSecurity({
            allowedCommands: [],
            allowedFileAccess: [{ operation: "write", pathPrefix: path.dirname(root) }],
        });
        expect(await check("file-writeText", { path: "config/slack.json", data: "{}" })).toMatchObject({
            required: true,
            request: { humanOnly: true },
        });
    });

    it("file-remove config/security.json is a human-only delete request", async () => {
        expect(await check("file-remove", { path: "config/security.json" })).toMatchObject({
            required: true,
            request: { kind: "file", operation: "delete", humanOnly: true },
        });
    });

    it("fix round 1: scoped-keys.json, code-mode/ and extensions/ writes are human-only", async () => {
        for (const p of ["scoped-keys.json", "code-mode/sessions-meta/s1.json", "extensions/x/index.js"]) {
            expect(await check("file-writeText", { path: p, data: "x" }), p).toMatchObject({
                required: true,
                request: { kind: "file", operation: "write", humanOnly: true },
            });
        }
    });

    // Task 17 fix round 3 (review I-5): engines/ holds the coding-agent
    // executable the next code run spawns; mcp-servers/ holds MCP server code
    // that approved stdio entries start (the approval is keyed on the command).
    it.each(["engines/x", "engines/claude/0.3.287/cli.js", "mcp-servers/x", "mcp-servers/srv/index.js"])(
        "fix round 3: file-writeText %s is human-only",
        async (p) => {
            expect(await check("file-writeText", { path: p, data: "x" })).toMatchObject({
                required: true,
                request: { kind: "file", operation: "write", humanOnly: true },
            });
        },
    );

    it("fix round 1 (C1): reading a protected entry is a human-only read request", async () => {
        expect(await check("file-readText", { path: "server-key" })).toMatchObject({
            required: true,
            request: { kind: "file", operation: "read", humanOnly: true },
        });
        expect(await check("file-copy", { from: "config/mcp.json", to: "knowledge/m.json" })).toMatchObject({
            required: true,
            request: { kind: "file", humanOnly: true },
        });
        expect(await check("file-readText", { path: "knowledge/x.md" })).toEqual({ required: false });
        expect(await check("file-grep", { pattern: "x" })).toEqual({ required: false });
    });

    it("knowledge/x.md stays prompt-free", async () => {
        expect(await check("file-writeText", { path: "knowledge/x.md", data: "# x" })).toEqual({ required: false });
    });
});

describe("code_agent_run working directory", () => {
    it("the workspace root or a protected entry as cwd needs a human-only request", async () => {
        for (const cwd of [root, path.join(root, "config")]) {
            expect(await check("code_agent_run", { agent: "claude", cwd, prompt: "x" }), cwd).toMatchObject({
                required: true,
                request: { kind: "file", operation: "write", humanOnly: true },
            });
        }
    });

    it("a workspace subfolder without protected entries below it is unchanged", async () => {
        expect(await check("code_agent_run", { agent: "claude", cwd: path.join(root, "knowledge"), prompt: "x" })).toEqual({
            required: false,
        });
        expect(await check("code_agent_run", { agent: "claude", prompt: "x" })).toEqual({ required: false });
    });
});

describe("executeCommand fast path", () => {
    it("a redirect into config/ is not on the fast path and is human-only", async () => {
        for (const command of ["echo x > config\\security.json", "awk '{print}' x > config/mcp.json"]) {
            expect(await check("executeCommand", { command }), command).toMatchObject({
                required: true,
                request: { kind: "command", humanOnly: true },
            });
        }
    });

    it("an allow-listed command naming a protected entry is human-only", async () => {
        expect(await check("executeCommand", { command: "type server-key" })).toMatchObject({
            required: true,
            request: { kind: "command", commandNames: ["type"], humanOnly: true },
        });
    });

    it("fix round 2: a command naming the own server (loopback + own port, or /rpc/) is human-only", async () => {
        for (const command of ["curl -X POST http://127.0.0.1:3220/rpc/workspace:writeFile", "curl http://localhost:3221/x"]) {
            expect(await check("executeCommand", { command }), command).toMatchObject({
                required: true,
                request: { kind: "command", humanOnly: true },
            });
        }
    });

    it("every output redirect (other than the harmless ones) is human-only, whatever the target", async () => {
        for (const command of [
            // Controller's final ruling: plain file redirects too.
            "echo hi > notes.txt",
            "echo hi >> notes.txt",
            "echo hi 1>notes.txt",
            "echo hi > knowledge/out-1.txt 2>&1",
            "echo hi > C:\\temp\\out.txt",
            "echo hi\necho x > a.txt",
            "echo hi\r\necho x >> knowledge\\a.txt",
            ". .\\x.ps1 > a.txt",
            "echo x > c%X%onfig\\security.json",
            "echo x > conf*\\mcp.json",
            "echo x > con^fig\\mcp.json",
            "echo x > \"con\"fig\\mcp.json",
            "echo x > co$Xnfig/mcp.json",
            "echo x > c?nfig/mcp.json",
            "echo x > `printf co`nfig/mcp.json",
            "echo x > (\"con\"+\"fig\\mcp.json\")",
            // Allow shape: anything but one bare literal token after > / >>.
            "echo hi > \"notes.txt\"",
            "echo hi >",
            "echo hi >> -f",
            "echo hi > notes.txt&del x",
            // Whole-command rule: shell-built text anywhere, not only in the target.
            "cd c%X%onfig & echo x > mcp.json",
            "echo %X% > notes.txt",
            "echo hi* > notes.txt",
            "echo con\"fi\"g > notes.txt",
            // Directory change before the redirect: relative targets are not safe.
            "cd knowledge & echo x > a.txt",
            "pushd knowledge && echo x > a.txt",
            "chdir knowledge & echo x > a.txt",
            "Set-Location knowledge; echo x > a.txt",
            "sl knowledge; echo x > a.txt",
            "cd.. & echo x > a.txt",
            // Final allow shape for the whole command.
            "cd,config&echo x>mcp.json",
            "cmd /c \"echo x>mcp.json\"",
            "echo x > CONFIG~1\\mcp.json",
            "pushd knowledge > a.txt",
            "powershell -c x > a.txt",
            "echo a & echo b > a.txt",
            "echo x > a.txt:stream",
        ]) {
            expect(await check("executeCommand", { command }), command).toMatchObject({
                required: true,
                request: { kind: "command", humanOnly: true },
            });
        }
    });

    it("expansion without a redirect is not human-only (not allow-listed commands already need a request)", async () => {
        expect(await check("executeCommand", { command: "echo %PATH%" })).toEqual({ required: false });
    });

    it("a command that is not allow-listed and has no redirect stays an ordinary request", async () => {
        const result = await check("executeCommand", { command: "rm notes.txt" });
        expect(result).toMatchObject({ required: true, request: { kind: "command", commandNames: ["rm"] } });
        expect((result as { request: object }).request).not.toHaveProperty("humanOnly");
    });

    it("null-device redirects and 2>&1 stay on the fast path", async () => {
        for (const command of [
            "dir 2>&1",
            "type x 2>nul",
            "echo hi >nul",
            "echo hi >NUL",
            "echo hi 1>nul",
            "echo hi >/dev/null",
            "echo hi 1>/dev/null",
            "cat x 2>/dev/null",
            "echo hi",
        ]) {
            expect(await check("executeCommand", { command }), command).toEqual({ required: false });
        }
    });
});
