// Modified for VibeMind (Flissel/rowboat vibemind): config/security.json takes
// effect only at load, through the human write paths and through
// addToSecurityConfig / addFileAccessGrant (ticket #330, Task 17). A raw write
// to the file after load (a shell command, another program) changes nothing.
// Temp workspace; nothing is executed (isBlocked only decides).
import fsSync from "node:fs";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterAll, beforeEach, describe, expect, it, vi } from "vitest";

const root = fsSync.realpathSync(fsSync.mkdtempSync(path.join(os.tmpdir(), "security-tamper-ws-")));
process.env.ROWBOAT_WORKDIR = root;

vi.mock("../knowledge/version_history.js", () => ({
    commitAll: async () => undefined,
    initRepo: async () => undefined,
}));

const security = await import("./security.js");
const { isBlocked } = await import("../application/lib/command-executor.js");
const { applyHumanWorkspaceOp, workspaceOpTouchesProtected } = await import("./human-config-writes.js");
const workspace = await import("../workspace/workspace.js");

const file = path.join(root, "config", "security.json");
const outside = path.join(root, "..", "elsewhere");

async function writeRaw(payload: unknown): Promise<void> {
    await fs.writeFile(file, typeof payload === "string" ? payload : JSON.stringify(payload));
    // A later mtime than the load, so an mtime-based re-read would see it.
    const later = new Date(Date.now() + 5_000);
    await fs.utimes(file, later, later);
}

beforeEach(async () => {
    await fs.mkdir(path.join(root, "config"), { recursive: true });
    await fs.writeFile(file, JSON.stringify({ allowedCommands: ["ls"], allowedFileAccess: [] }));
    security.resetSecurityAllowListCache();
});

afterAll(async () => {
    await fs.rm(root, { recursive: true, force: true });
});

describe("security.json tampering after load", () => {
    it("a raw [\"*\"] after load leaves a blocked command blocked", async () => {
        security.reloadSecurityConfig();
        expect(isBlocked("powershell -c x")).toBe(true);
        await writeRaw(["*"]);
        expect(security.getSecurityAllowList()).toEqual(["ls"]);
        expect(isBlocked("powershell -c x")).toBe(true);
        expect(isBlocked("ls")).toBe(false);
    });

    it("file grants written raw after load are ignored", async () => {
        security.reloadSecurityConfig();
        await writeRaw({ allowedCommands: ["ls"], allowedFileAccess: [{ operation: "write", pathPrefix: outside }] });
        expect(security.getFileAccessAllowList()).toEqual([]);
    });

    it("the first use loads the file once; later raw writes are not re-read", async () => {
        expect(security.getSecurityAllowList()).toEqual(["ls"]);
        await writeRaw(["*"]);
        expect(security.getSecurityAllowList()).toEqual(["ls"]);
    });
});

describe("security.json legitimate flows", () => {
    it("\"*\" present at load is honoured", async () => {
        await writeRaw(["*"]);
        security.reloadSecurityConfig();
        expect(isBlocked("powershell -c x")).toBe(false);
    });

    it("a broken file at load falls back to the defaults", async () => {
        await writeRaw("{not json");
        security.reloadSecurityConfig();
        const list = security.getSecurityAllowList();
        expect(list).toContain("ls");
        expect(list).toContain("cat");
        expect(list).not.toContain("*");
    });

    it("Always allow (addToSecurityConfig) persists to the file and is effective immediately", async () => {
        security.reloadSecurityConfig();
        expect(isBlocked("git status")).toBe(true);
        await security.addToSecurityConfig(["git"]);
        expect(isBlocked("git status")).toBe(false);
        const saved = JSON.parse(await fs.readFile(file, "utf8")) as { allowedCommands: string[] };
        expect(saved.allowedCommands).toEqual(["git", "ls"]);
    });

    it("Always allow does not turn a raw write since load into live state", async () => {
        security.reloadSecurityConfig();
        await writeRaw({ allowedCommands: ["*"], allowedFileAccess: [{ operation: "write", pathPrefix: outside }] });
        await security.addToSecurityConfig(["git"]);
        expect(security.getSecurityAllowList().sort()).toEqual(["git", "ls"]);
        expect(isBlocked("powershell -c x")).toBe(true);
        expect(security.getFileAccessAllowList()).toEqual([]);
    });

    it("addFileAccessGrant persists and is effective immediately", async () => {
        security.reloadSecurityConfig();
        await security.addFileAccessGrant({ operation: "read", pathPrefix: outside });
        expect(security.getFileAccessAllowList()).toEqual([{ operation: "read", pathPrefix: path.resolve(outside) }]);
        const saved = JSON.parse(await fs.readFile(file, "utf8")) as { allowedFileAccess: unknown[] };
        expect(saved.allowedFileAccess).toEqual([{ operation: "read", pathPrefix: path.resolve(outside) }]);
    });

    const humanWrite = (relPath: string, data: string, after?: () => Promise<void>) =>
        applyHumanWorkspaceOp({ kind: "writeFile", path: relPath, data }, async () => {
            await workspace.writeFile(relPath, data);
            await after?.();
        });

    it("a Settings save of config/security.json (the handler path) takes effect immediately", async () => {
        security.reloadSecurityConfig();
        await humanWrite("config/security.json", JSON.stringify({ allowedCommands: ["*"], allowedFileAccess: [] }));
        expect(isBlocked("powershell -c x")).toBe(false);
    });

    it("the handler path applies what the human wrote, not a raw write right after it", async () => {
        security.reloadSecurityConfig();
        await humanWrite("config/security.json", JSON.stringify({ allowedCommands: ["ls", "git"], allowedFileAccess: [] }), () =>
            writeRaw(["*"]),
        );
        expect(security.getSecurityAllowList().sort()).toEqual(["git", "ls"]);
    });

    it("a human write to another config file does not reload security.json", async () => {
        security.reloadSecurityConfig();
        await writeRaw(["*"]);
        await humanWrite("config/tags.json", "{}");
        expect(isBlocked("powershell -c x")).toBe(true);
    });

    it("a human rename onto security.json applies the source read before the operation, not a swap after it", async () => {
        security.reloadSecurityConfig();
        await fs.writeFile(path.join(root, "staged.json"), JSON.stringify(["ls", "git"]));
        await applyHumanWorkspaceOp({ kind: "rename", from: "staged.json", to: "config/security.json" }, async () => {
            await workspace.rename("staged.json", "config/security.json", true);
            await writeRaw(["*"]); // injected: raw swap between operation and approval
        });
        expect(security.getSecurityAllowList().sort()).toEqual(["git", "ls"]);
    });

    it("removing security.json only revokes: live ∩ defaults, grants dropped, nothing from the file", async () => {
        await writeRaw({ allowedCommands: ["*", "ls", "cat", "git"], allowedFileAccess: [{ operation: "read", pathPrefix: outside }] });
        security.reloadSecurityConfig();
        await applyHumanWorkspaceOp({ kind: "remove", path: "config/security.json" }, async () => {
            await workspace.remove("config/security.json", { trash: false });
        });
        expect(security.getSecurityAllowList().sort()).toEqual(["cat", "ls"]);
        expect(security.getFileAccessAllowList()).toEqual([]);
    });

    it("an operation on the config folder (container) only revokes, never applies the new file", async () => {
        security.reloadSecurityConfig();
        const staged = path.join(root, "staged-config");
        await fs.mkdir(staged, { recursive: true });
        await fs.writeFile(path.join(staged, "security.json"), JSON.stringify(["*"]));
        await applyHumanWorkspaceOp({ kind: "rename", from: "staged-config", to: "config" }, async () => {
            await fs.rm(path.join(root, "config"), { recursive: true, force: true });
            await workspace.rename("staged-config", "config", true);
        });
        expect(security.getSecurityAllowList()).toEqual(["ls"]);
        expect(isBlocked("powershell -c x")).toBe(true);
    });

    it("workspaceOpTouchesProtected: config/, keys, the workspace root, both ends of a rename/copy", async () => {
        const yes = [
            { kind: "writeFile", path: "config/mcp.json", data: "{}" },
            { kind: "writeFile", path: "server-key", data: "x" },
            { kind: "writeFile", path: "CONFIG./mcp.json", data: "{}" },
            { kind: "mkdir", path: "extensions/x" },
            { kind: "remove", path: "config" },
            { kind: "remove", path: "" },
            { kind: "rename", from: "notes.md", to: "config/security.json" },
            { kind: "rename", from: "config", to: "old-config" },
            { kind: "copy", from: "server-key", to: "knowledge/k.txt" },
        ] as const;
        for (const op of yes) expect(await workspaceOpTouchesProtected(op), JSON.stringify(op)).toBe(true);
        const no = [
            { kind: "writeFile", path: "knowledge/x.md", data: "x" },
            { kind: "rename", from: "knowledge/a.md", to: "knowledge/b.md" },
            { kind: "remove", path: "knowledge/x.md" },
        ] as const;
        for (const op of no) expect(await workspaceOpTouchesProtected(op), JSON.stringify(op)).toBe(false);
    });

    it("a failed operation changes nothing", async () => {
        security.reloadSecurityConfig();
        await expect(
            applyHumanWorkspaceOp({ kind: "writeFile", path: "config/security.json", data: "[\"*\"]" }, async () => {
                throw new Error("disk full");
            }),
        ).rejects.toThrow("disk full");
        expect(security.getSecurityAllowList()).toEqual(["ls"]);
    });
});
