// Modified for VibeMind (Flissel/rowboat vibemind): a stdio MCP entry starts a
// process only when a human approved it in this process lifetime (ticket #330,
// Task 17). Real getClient path up to the transport: the transports and the
// MCP client are recording fakes, so no process and no connection is ever
// started. Temp workspace with the real FSMcpConfigRepo.
import fsSync from "node:fs";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterAll, beforeEach, describe, expect, it, vi } from "vitest";

const root = fsSync.realpathSync(fsSync.mkdtempSync(path.join(os.tmpdir(), "mcp-stdio-gate-ws-")));
process.env.ROWBOAT_WORKDIR = root;

const state = vi.hoisted(() => ({
    transports: [] as Array<{ kind: string; target: string; args?: string[]; env?: Record<string, string> }>,
    calls: [] as Array<{ name: string }>,
}));

vi.mock("../knowledge/version_history.js", () => ({
    commitAll: async () => undefined,
    initRepo: async () => undefined,
}));
vi.mock("../di/container.js", async () => {
    const { FSMcpConfigRepo } = await import("./repo.js");
    const mcpRepo = new FSMcpConfigRepo();
    const stub = { ensureConfig: async () => undefined, ensureState: async () => undefined };
    return {
        default: {
            resolve: (name: string) => {
                if (name === "mcpConfigRepo") return mcpRepo;
                if (name === "modelConfigRepo" || name === "agentScheduleRepo" || name === "agentScheduleStateRepo") return stub;
                throw new Error(`unexpected resolve ${name}`);
            },
        },
    };
});
vi.mock("../spaces/orgs.js", () => ({ spacesMcpServers: async () => ({}) }));
vi.mock("@modelcontextprotocol/sdk/client/streamableHttp.js", () => ({
    StreamableHTTPClientTransport: class {
        constructor(url: URL) {
            state.transports.push({ kind: "http", target: url.href });
        }
        close() {}
    },
}));
vi.mock("@modelcontextprotocol/sdk/client/sse.js", () => ({
    SSEClientTransport: class {
        constructor(url: URL) {
            state.transports.push({ kind: "sse", target: url.href });
        }
        close() {}
    },
}));
vi.mock("@modelcontextprotocol/sdk/client/stdio.js", () => ({
    StdioClientTransport: class {
        constructor(opts: { command: string; args?: string[]; env?: Record<string, string> }) {
            state.transports.push({ kind: "stdio", target: opts.command, args: opts.args, env: opts.env });
        }
        close() {}
    },
}));
vi.mock("@modelcontextprotocol/sdk/client", () => ({
    Client: class {
        async connect() {}
        async close() {}
        async listTools() {
            return { tools: [] };
        }
        async callTool(req: { name: string }) {
            state.calls.push(req);
            return { content: [] };
        }
    },
}));

const { cleanup, executeTool } = await import("./mcp.js");
const approvals = await import("./stdio-approvals.js");
const { initConfigs } = await import("../config/initConfigs.js");
const { applyHumanWorkspaceOp } = await import("../config/human-config-writes.js");
const workspace = await import("../workspace/workspace.js");
const { mcpTools } = await import("../runtime/tools/domains/mcp.js");

// The handler path: the operation runs inside applyHumanWorkspaceOp; `after`
// is injected between the operation and the approval.
const humanWrite = (relPath: string, data: string, after?: () => Promise<void>) =>
    applyHumanWorkspaceOp({ kind: "writeFile", path: relPath, data }, async () => {
        await workspace.writeFile(relPath, data);
        await after?.();
    });
const humanRename = (from: string, to: string, after?: () => Promise<void>) =>
    applyHumanWorkspaceOp({ kind: "rename", from, to }, async () => {
        await workspace.rename(from, to, true);
        await after?.();
    });

type Ctx = Parameters<(typeof mcpTools)["addMcpServer"]["execute"]>[1];

const mcpFile = path.join(root, "config", "mcp.json");
const OUTSIDE = "was added or changed outside the app";

async function writeRaw(servers: Record<string, unknown>): Promise<void> {
    await fs.writeFile(mcpFile, JSON.stringify({ mcpServers: servers }, null, 2));
}

async function listMcpTools(serverName: string): Promise<{ error?: string; count?: number }> {
    return (await mcpTools.listMcpTools.execute({ serverName })) as { error?: string; count?: number };
}

async function addMcpServer(serverName: string, config: Record<string, unknown>, ctx?: Partial<NonNullable<Ctx>>) {
    return (await mcpTools.addMcpServer.execute({ serverName, config }, ctx)) as { success?: boolean; error?: string; message?: string };
}

const STDIO = { command: "node", args: ["server.js"], env: { A: "1", B: "2" } };

beforeEach(async () => {
    await cleanup();
    approvals.resetStdioApprovalsForTests();
    state.transports = [];
    state.calls = [];
    await fs.mkdir(path.join(root, "config"), { recursive: true });
    await writeRaw({});
});

afterAll(async () => {
    await fs.rm(root, { recursive: true, force: true });
});

describe("stdio approvals: the key", () => {
    it("env key order does not change the key; command, args, env and name do", () => {
        approvals.approveStdioEntry("s", { command: "node", args: ["a"], env: { A: "1", B: "2" } });
        expect(approvals.isStdioEntryApproved("s", { command: "node", args: ["a"], env: { B: "2", A: "1" } })).toBe(true);
        expect(approvals.isStdioEntryApproved("s", { command: "nodex", args: ["a"], env: { A: "1", B: "2" } })).toBe(false);
        expect(approvals.isStdioEntryApproved("s", { command: "node", args: ["b"], env: { A: "1", B: "2" } })).toBe(false);
        expect(approvals.isStdioEntryApproved("s", { command: "node", args: ["a"], env: { A: "1", B: "3" } })).toBe(false);
        expect(approvals.isStdioEntryApproved("s", { command: "node", args: ["a"] })).toBe(false);
        expect(approvals.isStdioEntryApproved("t", { command: "node", args: ["a"], env: { A: "1", B: "2" } })).toBe(false);
    });
});

describe("attacks: mcp.json changed outside the app", () => {
    it("a stdio entry written raw after startup: listMcpTools creates no transport and names the reason", async () => {
        await initConfigs();
        await writeRaw({ pwn: { command: "calc.exe" } });
        const result = await listMcpTools("pwn");
        expect(result.error).toContain(OUTSIDE);
        expect(result.error).toMatch(/restart|Settings/);
        expect(state.transports).toEqual([]);
    });

    it("executeTool on such an entry starts nothing either", async () => {
        await initConfigs();
        await writeRaw({ pwn: { command: "calc.exe" } });
        await expect(executeTool("pwn", "x", {})).rejects.toThrow(OUTSIDE);
        expect(state.transports).toEqual([]);
        expect(state.calls).toEqual([]);
    });

    it.each([
        ["command", { ...STDIO, command: "powershell" }],
        ["args", { ...STDIO, args: ["-c", "evil"] }],
        ["env", { ...STDIO, env: { A: "1", B: "2", NODE_OPTIONS: "--require evil.js" } }],
    ])("changing %s of an approved entry raw: no transport", async (_label, changed) => {
        await writeRaw({ ok: STDIO });
        await initConfigs();
        await writeRaw({ ok: changed });
        const result = await listMcpTools("ok");
        expect(result.error).toContain(OUTSIDE);
        expect(state.transports).toEqual([]);
    });

    it("addMcpServer with a command and no humanApproved (legacy engine, classifier-allowed auto mode): refused, file unchanged", async () => {
        await initConfigs();
        const before = await fs.readFile(mcpFile, "utf8");
        for (const ctx of [undefined, {}, { humanApproved: false }, { humanApproved: "yes" as unknown as boolean }]) {
            const result = await addMcpServer("pwn", { command: "calc.exe" }, ctx);
            expect(result.success, JSON.stringify(ctx)).not.toBe(true);
            expect(JSON.stringify(result)).toMatch(/human/i);
        }
        expect(await fs.readFile(mcpFile, "utf8")).toBe(before);
        expect(await listMcpTools("pwn")).toMatchObject({ error: expect.stringContaining("not found") });
        expect(state.transports).toEqual([]);
    });
});

describe("legitimate flows", () => {
    it("entries present at startup spawn (trust on boot)", async () => {
        await writeRaw({ ok: STDIO });
        await initConfigs();
        expect(await listMcpTools("ok")).toMatchObject({ count: 0 });
        expect(state.transports).toEqual([{ kind: "stdio", target: "node", args: ["server.js"], env: { A: "1", B: "2" } }]);
    });

    it("the same entry with its env keys reordered on disk still spawns", async () => {
        await writeRaw({ ok: STDIO });
        await initConfigs();
        await writeRaw({ ok: { ...STDIO, env: { B: "2", A: "1" } } });
        expect(await listMcpTools("ok")).toMatchObject({ count: 0 });
        expect(state.transports.map((t) => t.kind)).toEqual(["stdio"]);
    });

    it("a human-approved addMcpServer stdio entry spawns right away", async () => {
        await initConfigs();
        expect(await addMcpServer("neu", STDIO, { humanApproved: true })).toMatchObject({ success: true });
        expect(await listMcpTools("neu")).toMatchObject({ count: 0 });
        expect(state.transports).toEqual([{ kind: "stdio", target: "node", args: ["server.js"], env: { A: "1", B: "2" } }]);
    });

    it("addMcpServer with a URL keeps today's behaviour without humanApproved", async () => {
        await initConfigs();
        expect(await addMcpServer("web", { url: "http://127.0.0.1:9000/mcp" })).toMatchObject({ success: true });
        expect(await listMcpTools("web")).toMatchObject({ count: 0 });
        expect(state.transports).toEqual([{ kind: "http", target: "http://127.0.0.1:9000/mcp" }]);
    });

    it("a Settings save of config/mcp.json (the handler path) takes effect immediately", async () => {
        await initConfigs();
        await humanWrite("config/mcp.json", JSON.stringify({ mcpServers: { neu: STDIO } }, null, 2));
        expect(await listMcpTools("neu")).toMatchObject({ count: 0 });
        expect(state.transports.map((t) => t.kind)).toEqual(["stdio"]);
    });

    it("the handler path approves what the human wrote, not a raw write right after it", async () => {
        await initConfigs();
        await humanWrite("config/mcp.json", JSON.stringify({ mcpServers: { neu: STDIO } }, null, 2), () =>
            writeRaw({ neu: { ...STDIO, command: "powershell" } }),
        );
        expect((await listMcpTools("neu")).error).toContain(OUTSIDE);
        expect(state.transports).toEqual([]);
    });

    it("a human rename onto config/mcp.json approves the SOURCE read before the operation", async () => {
        await initConfigs();
        await fs.writeFile(path.join(root, "staged.json"), JSON.stringify({ mcpServers: { neu: STDIO } }));
        await humanRename("staged.json", "config/mcp.json");
        expect(await listMcpTools("neu")).toMatchObject({ count: 0 });
    });

    it("rename-into-place with the file swapped raw between operation and approval: the swap is not approved", async () => {
        await initConfigs();
        await fs.writeFile(path.join(root, "staged.json"), JSON.stringify({ mcpServers: { neu: STDIO } }));
        await humanRename("staged.json", "config/mcp.json", () => writeRaw({ neu: { ...STDIO, command: "powershell" } }));
        expect((await listMcpTools("neu")).error).toContain(OUTSIDE);
        expect(state.transports).toEqual([]);
        // The human's content itself stays approved.
        expect(approvals.isStdioEntryApproved("neu", STDIO)).toBe(true);
    });

    it("rename-into-place with the SOURCE swapped raw after it was read: what lands in place does not spawn", async () => {
        await initConfigs();
        const staged = path.join(root, "staged.json");
        await fs.writeFile(staged, JSON.stringify({ mcpServers: { neu: STDIO } }));
        await applyHumanWorkspaceOp({ kind: "rename", from: "staged.json", to: "config/mcp.json" }, async () => {
            await fs.writeFile(staged, JSON.stringify({ mcpServers: { neu: { command: "powershell" } } }));
            await workspace.rename("staged.json", "config/mcp.json", true);
        });
        expect((await listMcpTools("neu")).error).toContain(OUTSIDE);
        expect(state.transports).toEqual([]);
    });

    it("an operation on the config folder (container) approves nothing new and revokes changed entries", async () => {
        await writeRaw({ keep: STDIO, change: STDIO });
        await initConfigs();
        const stagedDir = path.join(root, "staged-config");
        await fs.mkdir(stagedDir, { recursive: true });
        await fs.writeFile(
            path.join(stagedDir, "mcp.json"),
            JSON.stringify({ mcpServers: { keep: STDIO, change: { command: "powershell" }, neu: { command: "calc.exe" } } }),
        );
        await applyHumanWorkspaceOp({ kind: "rename", from: "staged-config", to: "config" }, async () => {
            await fs.rm(path.join(root, "config"), { recursive: true, force: true });
            await workspace.rename("staged-config", "config", true);
        });
        expect(approvals.isStdioEntryApproved("keep", STDIO)).toBe(true);
        expect(approvals.isStdioEntryApproved("change", STDIO)).toBe(false);
        expect((await listMcpTools("change")).error).toContain(OUTSIDE);
        expect((await listMcpTools("neu")).error).toContain(OUTSIDE);
        // Writing the old definition back raw does not bring it back.
        await writeRaw({ change: STDIO });
        expect((await listMcpTools("change")).error).toContain(OUTSIDE);
        expect(state.transports.map((t) => t.target)).toEqual([]);
    });

    it("removing mcp.json revokes every approval", async () => {
        await writeRaw({ ok: STDIO });
        await initConfigs();
        await applyHumanWorkspaceOp({ kind: "remove", path: "config/mcp.json" }, () =>
            workspace.remove("config/mcp.json", { trash: false }),
        );
        await writeRaw({ ok: STDIO });
        expect((await listMcpTools("ok")).error).toContain(OUTSIDE);
        expect(state.transports).toEqual([]);
    });

    it("approvals mirror the latest human config: an old definition written back raw no longer spawns", async () => {
        await writeRaw({ s: STDIO });
        await initConfigs();
        await humanWrite("config/mcp.json", JSON.stringify({ mcpServers: { s: { ...STDIO, command: "node2" } } }));
        await writeRaw({ s: STDIO });
        expect((await listMcpTools("s")).error).toContain(OUTSIDE);
        expect(state.transports).toEqual([]);
    });

    it("a human save that drops an entry revokes it", async () => {
        await writeRaw({ s: STDIO });
        await initConfigs();
        await humanWrite("config/mcp.json", JSON.stringify({ mcpServers: {} }));
        await writeRaw({ s: STDIO });
        expect((await listMcpTools("s")).error).toContain(OUTSIDE);
    });

    it("a human-approved addMcpServer replaces that name's approval", async () => {
        await writeRaw({ s: STDIO });
        await initConfigs();
        expect(await addMcpServer("s", { ...STDIO, command: "node2" }, { humanApproved: true })).toMatchObject({ success: true });
        await writeRaw({ s: STDIO });
        expect((await listMcpTools("s")).error).toContain(OUTSIDE);
        expect(state.transports).toEqual([]);
    });

    it("a human write to another file approves nothing", async () => {
        await initConfigs();
        await writeRaw({ pwn: { command: "calc.exe" } });
        await humanWrite("config/tags.json", "{}");
        expect((await listMcpTools("pwn")).error).toContain(OUTSIDE);
        expect(state.transports).toEqual([]);
    });

    it("URL entries are not gated: rowboat-auftraege written raw after startup connects (trusted call too)", async () => {
        await initConfigs();
        await writeRaw({ "rowboat-auftraege": { url: "http://127.0.0.1:8156/mcp" } });
        await executeTool("rowboat-auftraege", "video_auftrag_aus_konzept", { pfad: "x" }, {
            vertraut: { serverName: "rowboat-auftraege", toolName: "video_auftrag_aus_konzept", url: "http://127.0.0.1:8156/mcp" },
        });
        expect(state.transports).toEqual([{ kind: "http", target: "http://127.0.0.1:8156/mcp" }]);
        expect(state.calls).toEqual([{ name: "video_auftrag_aus_konzept", arguments: { pfad: "x" } }]);
    });
});
