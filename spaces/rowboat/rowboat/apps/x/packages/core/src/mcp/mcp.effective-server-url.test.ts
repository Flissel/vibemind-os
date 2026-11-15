// Modified for VibeMind (Flissel/rowboat vibemind): effectiveServerUrl resolves like getClient.
import { beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
    file: {} as Record<string, unknown>,
    spaces: {} as Record<string, unknown>,
}));

vi.mock("../di/container.js", () => ({
    default: {
        resolve: (name: string) => {
            if (name !== "mcpConfigRepo") throw new Error(`unexpected resolve ${name}`);
            return { getConfig: async () => ({ mcpServers: state.file }) };
        },
    },
}));
vi.mock("../spaces/orgs.js", () => ({ spacesMcpServers: async () => state.spaces }));

const { effectiveServerUrl } = await import("./mcp.js");

describe("effectiveServerUrl", () => {
    beforeEach(() => {
        state.file = {};
        state.spaces = {};
    });

    it("returns the url of an http entry from mcp.json", async () => {
        state.file = { "rowboat-auftraege": { url: "http://127.0.0.1:8156/mcp" } };
        await expect(effectiveServerUrl("rowboat-auftraege")).resolves.toBe("http://127.0.0.1:8156/mcp");
    });

    it("returns null for stdio entries and unknown names (incl. prototype keys)", async () => {
        state.file = {
            "rowboat-auftraege": { command: "node", args: ["x.js"], url: "http://127.0.0.1:8156/mcp" },
        };
        await expect(effectiveServerUrl("rowboat-auftraege")).resolves.toBeNull();
        for (const name of ["fehlt", "constructor", "__proto__", "toString"]) {
            await expect(effectiveServerUrl(name), name).resolves.toBeNull();
        }
    });

    // E6: through the real McpServerConfig.parse (what FSMcpConfigRepo.getConfig applies to mcp.json).
    it("after the real config parse: {command,url} is stdio (null), {type:http,command,url} is http", async () => {
        const { McpServerConfig } = await import("@x/shared/dist/mcp.js");
        state.file = McpServerConfig.parse({
            mcpServers: {
                a: { command: "node", url: "http://127.0.0.1:8156/mcp" },
                b: { type: "http", command: "node", url: "http://127.0.0.1:8156/mcp" },
            },
        }).mcpServers;
        await expect(effectiveServerUrl("a")).resolves.toBeNull();
        await expect(effectiveServerUrl("b")).resolves.toBe("http://127.0.0.1:8156/mcp");
    });

    it("after the real config parse: {type:stdio,url} does not parse (the checker then prompts)", async () => {
        const { McpServerConfig } = await import("@x/shared/dist/mcp.js");
        expect(() =>
            McpServerConfig.parse({ mcpServers: { a: { type: "stdio", url: "http://127.0.0.1:8156/mcp" } } }),
        ).toThrow();
    });

    it("uses the entry getClient uses: a derived spaces entry wins over mcp.json", async () => {
        state.file = { "rowboat-auftraege": { url: "http://127.0.0.1:8156/mcp" } };
        state.spaces = { "rowboat-auftraege": { url: "https://spaces.example.com/mcp" } };
        await expect(effectiveServerUrl("rowboat-auftraege")).resolves.toBe("https://spaces.example.com/mcp");
    });
});
