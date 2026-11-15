// Modified for VibeMind (Flissel/rowboat vibemind): both MCP call paths hand the trusted-call marker to
// mcp.ts executeTool, which then binds the call to the checked entry.
import { beforeEach, describe, expect, it, vi } from "vitest";

const calls = vi.hoisted(() => [] as Array<{ server: string; tool: string; opts: unknown }>);

vi.mock("../../mcp/mcp.js", async (importOriginal) => ({
    ...(await importOriginal<typeof import("../../mcp/mcp.js")>()),
    executeTool: async (server: string, tool: string, _input: unknown, opts?: unknown) => {
        calls.push({ server, tool, opts });
        return { ok: true };
    },
}));

const { execTool } = await import("./exec-tool.js");
const { InMemoryAbortRegistry } = await import("../turns/abort-registry.js");

const trustedMcp = {
    serverName: "rowboat-auftraege",
    toolName: "video_auftrag_aus_konzept",
    url: "http://127.0.0.1:8156/mcp",
};

function ctx(extra: Record<string, unknown> = {}) {
    return {
        runId: "turn-1",
        toolCallId: "tc-1",
        signal: new AbortController().signal,
        abortRegistry: new InMemoryAbortRegistry(),
        publish: async () => {},
        ...extra,
    };
}

describe("trusted-call marker on both MCP paths", () => {
    beforeEach(() => {
        calls.length = 0;
    });

    it("mcp:<server>:<tool> attachment forwards the marker, and nothing without it", async () => {
        const attachment = {
            type: "mcp" as const,
            name: "video_auftrag_aus_konzept",
            mcpServerName: "rowboat-auftraege",
            description: "",
            inputSchema: {},
        };
        await execTool(attachment, { pfad: "x" }, ctx({ trustedMcp }));
        await execTool(attachment, { pfad: "x" }, ctx());
        expect(calls).toEqual([
            { server: "rowboat-auftraege", tool: "video_auftrag_aus_konzept", opts: { vertraut: trustedMcp } },
            { server: "rowboat-auftraege", tool: "video_auftrag_aus_konzept", opts: {} },
        ]);
    });

    it("builtin executeMcpTool forwards the marker, and nothing without it", async () => {
        const input = { serverName: "rowboat-auftraege", toolName: "video_auftrag_aus_konzept", arguments: {} };
        await execTool({ type: "builtin", name: "executeMcpTool" }, input, ctx({ trustedMcp }));
        await execTool({ type: "builtin", name: "executeMcpTool" }, input, ctx());
        expect(calls.map((c) => c.opts)).toEqual([{ vertraut: trustedMcp }, {}]);
    });
});
