// Modified for VibeMind (Flissel/rowboat vibemind): protected workspace paths (ticket #330).
import fsSync from "node:fs";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

// WorkDir is read once at import: point it at a scratch workspace first.
const root = fsSync.realpathSync(fsSync.mkdtempSync(path.join(os.tmpdir(), "protected-ws-")));
process.env.ROWBOAT_WORKDIR = root;

const {
    PROTECTED_WORKSPACE_ENTRIES,
    commandNamesProtectedEntry,
    isProtectedRead,
    isProtectedWrite,
    nativeCanonicalPath,
    needsHumanForProtectedPath,
    setNativeRealpathForTests,
} = await import("./protected-paths.js");

beforeAll(async () => {
    await fs.mkdir(path.join(root, "config"), { recursive: true });
    await fs.writeFile(path.join(root, "config", "mcp.json"), "{}");
    await fs.mkdir(path.join(root, "knowledge"), { recursive: true });
    await fs.writeFile(path.join(root, "knowledge", "x.md"), "# x\n");
    // A link inside knowledge/ that points into config/.
    await fs.symlink(
        path.join(root, "config"),
        path.join(root, "knowledge", "link"),
        process.platform === "win32" ? "junction" : "dir",
    );
});

afterAll(async () => {
    await fs.rm(root, { recursive: true, force: true });
});

describe("protected workspace entries", () => {
    it("lists the six entries of the ticket, the three of fix round 1 and the two of Task 17 fix round 3", () => {
        expect([...PROTECTED_WORKSPACE_ENTRIES]).toEqual([
            "config",
            "server-key",
            "cipher-key",
            "embed.json",
            ".electron-data",
            "channels",
            // Fix round 1 (C1/I3): server auth keys and code-session policy.
            "scoped-keys.json",
            "code-mode",
            // Unpacked Chrome extensions, loaded into the browser session at start.
            "extensions",
            // Task 17 fix round 3 (review I-5): code the app starts.
            "engines",
            "mcp-servers",
        ]);
    });
});

describe("isProtectedWrite — file tools", () => {
    const write = (p: string) => isProtectedWrite("file-writeText", { path: p, data: "x" });

    it("covers every protected entry and paths inside them", async () => {
        for (const p of [
            "config/mcp.json",
            "config/slack.json",
            "server-key",
            "cipher-key",
            "embed.json",
            ".electron-data/Local State",
            "channels/telegram-state.json",
            "scoped-keys.json",
            "Scoped-Keys.JSON",
            "code-mode/sessions-meta/s1.json",
            "code-mode",
            "extensions/x/index.js",
            "extensions/x/manifest.json",
            "engines/claude/0.3.287/cli.js",
            "Engines/x",
            "mcp-servers/srv/index.js",
        ]) {
            expect(await write(p), p).toBe(true);
        }
    });

    it("path variants: case, .., trailing dot, stream syntax, absolute, symlink", async () => {
        for (const p of [
            "CONFIG/mcp.json",
            "Config\\MCP.json",
            "knowledge/../config/mcp.json",
            "config./mcp.json",
            "server-key::$DATA",
            "knowledge/x.md:hidden",
            path.join(root, "config", "mcp.json"),
            "knowledge/link/mcp.json",
            "CONFIG~1/mcp.json",
            "SERVER~1",
        ]) {
            expect(await write(p), p).toBe(true);
        }
    });

    it("fix round 1 (I2): every UNC path counts, including a loopback share of the workspace", async () => {
        const noDrive = root.replace(/^[a-zA-Z]:/, "");
        const drive = /^([a-zA-Z]):/.exec(root)?.[1] ?? "C";
        for (const p of [
            `\\\\localhost\\${drive}$${noDrive}\\config\\mcp.json`,
            `//localhost/${drive}$${noDrive.split("\\").join("/")}/config/mcp.json`,
            `\\\\127.0.0.1\\${drive}$${noDrive}\\knowledge\\x.md`,
            "\\\\server\\share\\notes.txt",
            "\\\\?\\C:\\elsewhere.txt",
        ]) {
            expect(await write(p), p).toBe(true);
        }
    });

    it.skipIf(process.platform !== "win32")("fix round 1 (I2): an alias drive is mapped back with the native realpath", async () => {
        // subst / mapped drives would change machine state: the native
        // realpath is injected instead. X:\ is an alias of the workspace.
        setNativeRealpathForTests(async (p) => {
            if (/^x:/i.test(p)) return path.join(root, p.slice(2));
            return fs.realpath(p);
        });
        try {
            expect(await write("X:\\config\\mcp.json")).toBe(true);
            expect(await write("X:\\server-key")).toBe(true);
            expect(await write("X:\\knowledge\\x.md")).toBe(false);
        } finally {
            setNativeRealpathForTests(undefined);
        }
        // Without the alias, X:\ is just some other drive.
        expect(await write("X:\\config\\mcp.json")).toBe(false);
    });

    it("fix round 1 (I2): the default native realpath really resolves (a junction, plus a missing rest)", async () => {
        expect(await nativeCanonicalPath(path.join(root, "knowledge", "link", "new", "a.json"))).toBe(
            path.join(fsSync.realpathSync.native(path.join(root, "config")), "new", "a.json"),
        );
    });

    it("leaves ordinary workspace and outside writes alone", async () => {
        for (const p of [
            "knowledge/x.md",
            "knowledge/config/notes.md",
            "configuration.md",
            "config.bak",
            path.join(os.tmpdir(), "elsewhere.txt"),
            "~/elsewhere.txt",
            // Fix round 1 (M3): a "~" that is not the 8.3 shape (~ + digit).
            "knowledge/a~b.md",
        ]) {
            expect(await write(p), p).toBe(false);
        }
    });

    it("covers rename (source and destination), copy (destination only), remove, mkdir", async () => {
        expect(await isProtectedWrite("file-rename", { from: "config/mcp.json", to: "knowledge/m.json" })).toBe(true);
        expect(await isProtectedWrite("file-rename", { from: "knowledge/x.md", to: "config/mcp.json" })).toBe(true);
        expect(await isProtectedWrite("file-copy", { from: "knowledge/x.md", to: "config/mcp.json" })).toBe(true);
        expect(await isProtectedWrite("file-copy", { from: "config/mcp.json", to: "knowledge/m.json" })).toBe(false);
        expect(await isProtectedWrite("file-remove", { path: "config/security.json" })).toBe(true);
        expect(await isProtectedWrite("file-mkdir", { path: "config" })).toBe(true);
        expect(await isProtectedWrite("file-editText", { path: "config/mcp.json", oldString: "a", newString: "b" })).toBe(true);
    });

    it("removing or moving the workspace itself counts", async () => {
        expect(await isProtectedWrite("file-remove", { path: "." })).toBe(true);
        expect(await isProtectedWrite("file-rename", { from: root, to: path.join(os.tmpdir(), "moved") })).toBe(true);
    });

    it("covers text-to-speech outputPath, deck-* and spreadsheet-*", async () => {
        expect(await isProtectedWrite("text-to-speech", { text: "hi", outputPath: "config/a.mp3" })).toBe(true);
        expect(await isProtectedWrite("text-to-speech", { text: "hi" })).toBe(false);
        expect(await isProtectedWrite("deck-create", { path: "config/d.pptx" })).toBe(true);
        expect(await isProtectedWrite("deck-restyle", { path: "config/d.pptx" })).toBe(true);
        expect(await isProtectedWrite("spreadsheet-create", { path: "config/s.csv" })).toBe(true);
        expect(await isProtectedWrite("spreadsheet-edit", { path: "config/s.csv" })).toBe(true);
    });

    it("reads are not writes", async () => {
        expect(await isProtectedWrite("file-readText", { path: "config/mcp.json" })).toBe(false);
        expect(await isProtectedWrite("file-list", { path: "config" })).toBe(false);
    });
});

describe("isProtectedRead (fix round 1, review C1)", () => {
    it("content reads of a protected entry count", async () => {
        for (const [name, input] of [
            ["file-readText", { path: "server-key" }],
            ["file-readText", { path: "config/mcp.json" }],
            ["file-readText", { path: "scoped-keys.json" }],
            ["file-readText", { path: "knowledge/link/mcp.json" }],
            ["parseFile", { path: "cipher-key" }],
            ["LLMParse", { path: "config/mcp.json" }],
            ["deck-review", { path: "config/d.pptx" }],
            ["transcribe-audio", { path: "channels/a.ogg" }],
            ["file-exists", { path: "server-key" }],
            ["file-stat", { path: "server-key" }],
            ["file-copy", { from: "config/mcp.json", to: "knowledge/m.json" }],
            ["file-readText", { path: "\\\\localhost\\share\\x.md" }],
        ] as Array<[string, Record<string, unknown>]>) {
            expect(await isProtectedRead(name, input), `${name} ${JSON.stringify(input)}`).toBe(true);
            expect(await needsHumanForProtectedPath(name, input), name).toBe(true);
        }
    });

    it("listing or searching inside a protected entry counts; the workspace root does not", async () => {
        expect(await isProtectedRead("file-list", { path: "config" })).toBe(true);
        expect(await isProtectedRead("file-glob", { pattern: "*", cwd: "channels" })).toBe(true);
        expect(await isProtectedRead("file-grep", { pattern: "x", searchPath: "config" })).toBe(true);
        expect(await isProtectedRead("file-grep", { pattern: "x", searchPath: "server-key" })).toBe(true);
        expect(await isProtectedRead("file-list", { path: "." })).toBe(false);
        expect(await isProtectedRead("file-grep", { pattern: "x" })).toBe(false);
        expect(await isProtectedRead("file-glob", { pattern: "config/**" })).toBe(false);
    });

    it("fix round 2 (m1): exists/stat/read on the workspace root or a parent folder do not count; on or inside an entry they do", async () => {
        const parent = path.dirname(root);
        for (const [name, input] of [
            ["file-exists", { path: "." }],
            ["file-exists", { path: root }],
            ["file-stat", { path: "." }],
            ["file-stat", { path: parent }],
            ["file-exists", { path: path.parse(root).root }],
            ["file-readText", { path: "." }],
            ["file-list", { path: parent }],
            ["file-list", { path: root }],
            ["file-glob", { pattern: "*", cwd: parent }],
            ["file-grep", { pattern: "x", searchPath: parent }],
        ] as Array<[string, Record<string, unknown>]>) {
            expect(await isProtectedRead(name, input), `${name} ${JSON.stringify(input)}`).toBe(false);
            expect(await needsHumanForProtectedPath(name, input), `${name} ${JSON.stringify(input)}`).toBe(false);
        }
        for (const [name, input] of [
            ["file-exists", { path: "config" }],
            ["file-stat", { path: "config" }],
            ["file-exists", { path: "config/mcp.json" }],
            ["file-stat", { path: path.join(root, "channels", "x.json") }],
            ["file-exists", { path: "extensions" }],
        ] as Array<[string, Record<string, unknown>]>) {
            expect(await isProtectedRead(name, input), `${name} ${JSON.stringify(input)}`).toBe(true);
        }
    });

    it("leaves ordinary reads alone", async () => {
        for (const [name, input] of [
            ["file-readText", { path: "knowledge/x.md" }],
            ["file-readText", { path: "configuration.md" }],
            ["file-copy", { from: "knowledge/x.md", to: "knowledge/y.md" }],
            ["file-writeText", { path: "config/mcp.json", data: "x" }],
        ] as Array<[string, Record<string, unknown>]>) {
            expect(await isProtectedRead(name, input), name).toBe(false);
        }
        expect(await needsHumanForProtectedPath("file-writeText", { path: "config/mcp.json", data: "x" })).toBe(true);
    });
});

describe("isProtectedWrite — executeCommand", () => {
    it("flags a command that names a protected entry as a path segment", async () => {
        for (const command of [
            "echo [\"*\"] > config\\security.json",
            "awk 'BEGIN{print 1}' > config/mcp.json",
            "type server-key",
            "copy x C:\\Users\\u\\.rowboat\\Config\\mcp.json",
            "echo x > con^fig\\security.json",
            "echo x > \"con\"\"fig\"/security.json",
            "echo x > config.\\security.json",
        ]) {
            expect(commandNamesProtectedEntry(command), command).toBe(true);
            expect(await isProtectedWrite("executeCommand", { command }), command).toBe(true);
        }
    });

    it("flags every output redirect other than the harmless ones (controller's final ruling)", async () => {
        for (const command of [
            "echo x > c%X%onfig\\security.json",
            "echo x > conf*\\mcp.json",
            "echo x > \"con\"fig\\mcp.json",
            "echo x > (\"con\"+\"fig\\mcp.json\")",
            "echo hi > notes.txt",
            "echo hi >> notes.txt",
            "echo hi >",
            "echo x > mcp.json",
            "echo hi\necho x > a.txt",
        ]) {
            expect(await isProtectedWrite("executeCommand", { command }), command).toBe(true);
        }
        expect(await isProtectedWrite("executeCommand", { command: "echo x > mcp.json", cwd: "knowledge" })).toBe(true);
        // Without a redirect, expansion alone does not count.
        expect(await isProtectedWrite("executeCommand", { command: "echo %PATH%" })).toBe(false);
        // The 8.3 short-name shape (CONFIG~1) counts as naming a protected entry.
        expect(commandNamesProtectedEntry("type CONFIG~1\\mcp.json")).toBe(true);
        expect(await isProtectedWrite("executeCommand", { command: "dir CONFIG~1" })).toBe(true);
    });

    it("fix round 1 (M3): only the 8.3 shape of \"~\" counts, not the home shorthand", async () => {
        for (const command of [
            "type SERVER~1",
            "type EMBED~1.JSO",
            "dir ELECTR~1",
            "git log HEAD~3",
            // Same normalisation as the name check (escape and quotes removed).
            "type CONFIG~^1\\mcp.json",
            "type CONFIG~\"1\"\\mcp.json",
        ]) {
            expect(commandNamesProtectedEntry(command), command).toBe(true);
        }
        for (const command of ["cd ~", "ls ~/Downloads", "dir ~\\Documents", "echo a~b"]) {
            expect(commandNamesProtectedEntry(command), command).toBe(false);
            expect(await isProtectedWrite("executeCommand", { command }), command).toBe(false);
        }
    });

    it("flags a command whose cwd is a protected entry", async () => {
        expect(await isProtectedWrite("executeCommand", { command: "echo x > security.json", cwd: "config" })).toBe(true);
    });

    it("leaves other commands alone", async () => {
        for (const command of ["dir 2>&1", "type x 2>nul", "echo hi 1>nul", "ls knowledge", "cat configuration.md"]) {
            expect(await isProtectedWrite("executeCommand", { command }), command).toBe(false);
        }
    });

    it("fix round 2: a command that names the own server (loopback + own port, or /rpc/) is for the human", async () => {
        for (const command of [
            "curl -X POST http://127.0.0.1:3220/rpc/workspace:writeFile",
            "curl http://localhost:3221",
            "Invoke-RestMethod http://[::1]:3220/x -Method Post",
            "curl http://example.test/rpc/sessions:respondToPermission",
        ]) {
            expect(await isProtectedWrite("executeCommand", { command }), command).toBe(true);
            expect(await needsHumanForProtectedPath("executeCommand", { command }), command).toBe(true);
        }
        for (const command of ["curl http://localhost:5173/", "curl https://example.com/api"]) {
            expect(await isProtectedWrite("executeCommand", { command }), command).toBe(false);
        }
    });
});
