// Modified for VibeMind (Flissel/rowboat vibemind): E3 — the checker built with no deps (as di/container.ts
// does) consults mcp.ts effectiveServerUrl, the resolver the executor uses.
import { describe, expect, it, vi } from "vitest";

const lookups = vi.hoisted(() => ({ names: [] as string[], url: null as string | null }));

vi.mock("../../../mcp/mcp.js", async (importOriginal) => ({
    ...(await importOriginal<typeof import("../../../mcp/mcp.js")>()),
    effectiveServerUrl: async (serverName: string) => {
        lookups.names.push(serverName);
        return lookups.url;
    },
}));

const { RealPermissionChecker } = await import("./real-permission-checker.js");

const call = {
    turnId: "turn-1",
    toolCallId: "tc-1",
    toolId: "builtin:executeMcpTool",
    toolName: "executeMcpTool",
    input: { serverName: "rowboat-auftraege", toolName: "video_auftrag_aus_konzept" },
};

describe("RealPermissionChecker default wiring", () => {
    it("looks the trusted server up through effectiveServerUrl and trusts only the trusted URL", async () => {
        const checker = new RealPermissionChecker();
        lookups.url = "http://127.0.0.1:8156/mcp";
        expect(await checker.check(call)).toEqual({
            required: false,
            trustedMcp: {
                serverName: "rowboat-auftraege",
                toolName: "video_auftrag_aus_konzept",
                url: "http://127.0.0.1:8156/mcp",
            },
        });
        lookups.url = "http://127.0.0.1:9999/mcp";
        expect(await checker.check(call)).toMatchObject({ required: true });
        expect(lookups.names).toEqual(["rowboat-auftraege", "rowboat-auftraege"]);
    });
});
