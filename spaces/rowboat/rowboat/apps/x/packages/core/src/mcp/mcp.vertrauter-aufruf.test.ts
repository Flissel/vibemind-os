// Modified for VibeMind (Flissel/rowboat vibemind): executeTool binds a prompt-free (trusted) call to the
// entry it actually connects with (vertraute-mcp-werkzeuge.ts, fix round 1 of task 14).
import { beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
    file: {} as Record<string, unknown>,
    transports: [] as Array<{ kind: string; target: string }>,
    calls: [] as Array<{ name: string; arguments: unknown }>,
}));

vi.mock("../di/container.js", () => ({
    default: {
        resolve: (name: string) => {
            if (name !== "mcpConfigRepo") throw new Error(`unexpected resolve ${name}`);
            return { getConfig: async () => ({ mcpServers: state.file }) };
        },
    },
}));
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
        constructor(opts: { command: string }) {
            state.transports.push({ kind: "stdio", target: opts.command });
        }
        close() {}
    },
}));
vi.mock("@modelcontextprotocol/sdk/client", () => ({
    Client: class {
        async connect() {}
        async close() {}
        async callTool(req: { name: string; arguments: unknown }) {
            state.calls.push(req);
            return { content: [{ type: "text", text: "ok" }] };
        }
    },
}));

const { executeTool, cleanup } = await import("./mcp.js");

const SERVER = "rowboat-auftraege";
const TOOL = "video_auftrag_aus_konzept";
const URL_OK = "http://127.0.0.1:8156/mcp";
const vertraut = { serverName: SERVER, toolName: TOOL, url: URL_OK };
const GEAENDERT = "Der Eintrag rowboat-auftraege hat sich seit der Prüfung geändert. Aufruf abgebrochen.";

describe("executeTool with a trusted (prompt-free) call", () => {
    beforeEach(async () => {
        await cleanup();
        state.file = {};
        state.transports = [];
        state.calls = [];
    });

    it("(c) unchanged entry: connects to the checked URL and calls the tool", async () => {
        state.file = { [SERVER]: { url: URL_OK } };
        await executeTool(SERVER, TOOL, { pfad: "x" }, { vertraut });
        expect(state.transports).toEqual([{ kind: "http", target: URL_OK }]);
        expect(state.calls).toEqual([{ name: TOOL, arguments: { pfad: "x" } }]);
    });

    it("(a) entry changed to another URL after the check: refused, nothing connected or sent", async () => {
        state.file = { [SERVER]: { url: "http://evil.example.com:8156/mcp", headers: { A: "b" } } };
        await expect(executeTool(SERVER, TOOL, { pfad: "x" }, { vertraut })).rejects.toThrow(GEAENDERT);
        expect(state.transports).toEqual([]);
        expect(state.calls).toEqual([]);
    });

    it("(b) entry changed to a stdio command after the check: refused, no process started", async () => {
        state.file = { [SERVER]: { command: "powershell", args: ["-c", "x"] } };
        await expect(executeTool(SERVER, TOOL, {}, { vertraut })).rejects.toThrow(GEAENDERT);
        expect(state.transports).toEqual([]);
        expect(state.calls).toEqual([]);
    });

    it("entry removed after the check: refused (no entry), nothing connected", async () => {
        await expect(executeTool(SERVER, TOOL, {}, { vertraut })).rejects.toThrow();
        expect(state.transports).toEqual([]);
    });

    it("refuses a cached client once the entry changed (no reuse of a trusted connection for a changed entry)", async () => {
        state.file = { [SERVER]: { url: URL_OK } };
        await executeTool(SERVER, TOOL, {}, { vertraut });
        state.file = { [SERVER]: { url: "http://127.0.0.1:9999/mcp" } };
        await expect(executeTool(SERVER, TOOL, {}, { vertraut })).rejects.toThrow(GEAENDERT);
        expect(state.calls).toHaveLength(1);
    });

    it("a marker for another server or tool than the executed one is refused", async () => {
        state.file = { [SERVER]: { url: URL_OK }, andere: { url: URL_OK } };
        await expect(executeTool("andere", TOOL, {}, { vertraut })).rejects.toThrow("Aufruf abgebrochen");
        await expect(executeTool(SERVER, "anderes", {}, { vertraut })).rejects.toThrow("Aufruf abgebrochen");
        expect(state.calls).toEqual([]);
    });

    it("(d) a call WITHOUT the marker (human-approved) to a differently configured entry runs as before", async () => {
        state.file = { [SERVER]: { url: "http://192.168.1.65:8156/mcp" } };
        await executeTool(SERVER, TOOL, { pfad: "x" });
        expect(state.transports).toEqual([{ kind: "http", target: "http://192.168.1.65:8156/mcp" }]);
        expect(state.calls).toEqual([{ name: TOOL, arguments: { pfad: "x" } }]);
    });
});
