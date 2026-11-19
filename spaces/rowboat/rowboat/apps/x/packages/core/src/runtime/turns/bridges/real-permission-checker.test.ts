import { describe, expect, it } from "vitest";
import type { z } from "zod";
import type { getToolPermissionMetadata } from "../../assembly/permission-metadata.js";
import type { BuiltinToolPermission } from "../../tools/types.js";
import { RealPermissionChecker } from "./real-permission-checker.js";

type MetadataFn = typeof getToolPermissionMetadata;
type MetadataCall = {
    toolCall: Parameters<MetadataFn>[0];
    attachment: Parameters<MetadataFn>[1];
};
type Policy = z.infer<typeof BuiltinToolPermission>;

function makeChecker(
    result: Awaited<ReturnType<MetadataFn>> | Error,
    policies: Record<string, Policy> = { executeCommand: "command-allowlist" },
) {
    const calls: MetadataCall[] = [];
    const checker = new RealPermissionChecker({
        getMetadata: (async (toolCall, attachment) => {
            calls.push({ toolCall, attachment });
            if (result instanceof Error) {
                throw result;
            }
            return result;
        }) as MetadataFn,
        getPolicy: (name) => policies[name],
    });
    return { checker, calls };
}

const input = {
    turnId: "turn-1",
    toolCallId: "tc-1",
    toolId: "builtin:executeCommand",
    toolName: "executeCommand",
    input: { command: "rm -rf /" },
};

describe("RealPermissionChecker", () => {
    it("gates builtins through getToolPermissionMetadata with empty session grants", async () => {
        const metadata = { kind: "command" as const, commandNames: ["rm"] };
        const { checker, calls } = makeChecker(metadata);
        const result = await checker.check(input);
        expect(result).toEqual({ required: true, request: metadata });
        expect(calls[0].toolCall).toMatchObject({
            type: "tool-call",
            toolCallId: "tc-1",
            toolName: "executeCommand",
            arguments: { command: "rm -rf /" },
        });
        expect(calls[0].attachment).toEqual({
            type: "builtin",
            name: "executeCommand",
        });
    });

    it("returns not-required when metadata is null", async () => {
        const { checker } = makeChecker(null);
        expect(await checker.check(input)).toEqual({ required: false });
    });

    it("allows builtins declared permission none without consulting metadata", async () => {
        const { checker, calls } = makeChecker(new Error("must not be called"), {
            "web-search": "none",
        });
        expect(
            await checker.check({
                ...input,
                toolId: "builtin:web-search",
                toolName: "web-search",
                input: { query: "hi" },
            }),
        ).toEqual({ required: false });
        expect(calls).toHaveLength(0);
    });

    it("always gates prompt-policy builtins with a generic request", async () => {
        const { checker } = makeChecker(new Error("must not be called"), {
            addMcpServer: "prompt",
        });
        expect(
            await checker.check({
                ...input,
                toolId: "builtin:addMcpServer",
                toolName: "addMcpServer",
                input: { name: "kb" },
            }),
        ).toEqual({
            required: true,
            request: {
                kind: "tool",
                toolId: "builtin:addMcpServer",
                toolName: "addMcpServer",
            },
        });
    });

    // VibeMind (ticket #330, Task 17): a stdio entry starts a process, so the
    // request is for a human only (never classified, denied headless).
    it("addMcpServer with a command raises a human-only request; a URL entry the generic one", async () => {
        const { checker } = makeChecker(new Error("must not be called"), {
            addMcpServer: "prompt",
        });
        const add = (config: unknown) =>
            checker.check({
                ...input,
                toolId: "builtin:addMcpServer",
                toolName: "addMcpServer",
                input: { serverName: "s", config } as never,
            });
        expect(await add({ command: "calc.exe" })).toEqual({
            required: true,
            request: { kind: "tool", toolId: "builtin:addMcpServer", toolName: "addMcpServer", humanOnly: true },
        });
        expect(await add({ command: "node", url: "http://x" })).toMatchObject({ request: { humanOnly: true } });
        expect(await add({ url: "http://127.0.0.1:8156/mcp" })).toEqual({
            required: true,
            request: { kind: "tool", toolId: "builtin:addMcpServer", toolName: "addMcpServer" },
        });
    });

    it("gates composio-execute with a toolkit/tool request", async () => {
        const { checker } = makeChecker(new Error("must not be called"), {
            "composio-execute-tool": "composio-execute",
        });
        expect(
            await checker.check({
                ...input,
                toolId: "builtin:composio-execute-tool",
                toolName: "composio-execute-tool",
                input: {
                    toolkitSlug: "gmail",
                    toolSlug: "GMAIL_SEND_EMAIL",
                    arguments: { to: "a@b.c" },
                },
            }),
        ).toEqual({
            required: true,
            request: {
                kind: "composio",
                toolkitSlug: "gmail",
                toolSlug: "GMAIL_SEND_EMAIL",
            },
        });
    });

    it("gates composio-execute with malformed input via the generic request", async () => {
        const { checker } = makeChecker(new Error("must not be called"), {
            "composio-execute-tool": "composio-execute",
        });
        expect(
            await checker.check({
                ...input,
                toolId: "builtin:composio-execute-tool",
                toolName: "composio-execute-tool",
                input: { nonsense: true },
            }),
        ).toEqual({
            required: true,
            request: {
                kind: "tool",
                toolId: "builtin:composio-execute-tool",
                toolName: "composio-execute-tool",
            },
        });
    });

    it("gates executeMcpTool with a server/tool request", async () => {
        const { checker } = makeChecker(new Error("must not be called"), {
            executeMcpTool: "mcp-execute",
        });
        expect(
            await checker.check({
                ...input,
                toolId: "builtin:executeMcpTool",
                toolName: "executeMcpTool",
                input: { serverName: "kb", toolName: "search", arguments: {} },
            }),
        ).toEqual({
            required: true,
            request: { kind: "mcp", serverName: "kb", toolName: "search" },
        });
    });

    it("gates mcp:* attachments on user agents", async () => {
        const { checker, calls } = makeChecker(new Error("must not be called"));
        expect(
            await checker.check({
                ...input,
                toolId: "mcp:kb:search",
                toolName: "search",
            }),
        ).toEqual({
            required: true,
            request: { kind: "mcp", serverName: "kb", toolName: "search" },
        });
        expect(calls).toHaveLength(0);
    });

    it("fails closed for undeclared builtins and unknown toolId families", async () => {
        const { checker } = makeChecker(new Error("must not be called"), {});
        expect(
            await checker.check({
                ...input,
                toolId: "builtin:brand-new-tool",
                toolName: "brand-new-tool",
            }),
        ).toEqual({
            required: true,
            request: {
                kind: "tool",
                toolId: "builtin:brand-new-tool",
                toolName: "brand-new-tool",
            },
        });
        expect(
            await checker.check({
                ...input,
                toolId: "future:whatever",
                toolName: "whatever",
            }),
        ).toEqual({
            required: true,
            request: {
                kind: "tool",
                toolId: "future:whatever",
                toolName: "whatever",
            },
        });
    });

    describe("vertrautes MCP-Werkzeug (Übergabe an die Video-Werkbank)", () => {
        const SERVER = "rowboat-auftraege";
        const TOOL = "video_auftrag_aus_konzept";
        const URL_OK = "http://127.0.0.1:8156/mcp";

        function checkerWithServers(servers: Record<string, string | null>) {
            const lookups: string[] = [];
            const checker = new RealPermissionChecker({
                getMetadata: (async () => {
                    throw new Error("must not be called");
                }) as MetadataFn,
                getPolicy: (name) =>
                    ({ executeMcpTool: "mcp-execute", "web-search": "none" } as Record<string, Policy>)[name],
                getMcpServerUrl: async (serverName) => {
                    lookups.push(serverName);
                    return Object.prototype.hasOwnProperty.call(servers, serverName)
                        ? servers[serverName]
                        : null;
                },
            });
            return { checker, lookups };
        }

        function viaExecuteMcpTool(serverName: string, toolName: string) {
            return {
                ...input,
                toolId: "builtin:executeMcpTool",
                toolName: "executeMcpTool",
                input: { serverName, toolName, arguments: { pfad: "knowledge/Konzepte/x.html" } },
            };
        }

        function viaAttachment(serverName: string, toolName: string) {
            return { ...input, toolId: `mcp:${serverName}:${toolName}`, toolName, input: {} };
        }

        const ALLOWED = {
            required: false,
            trustedMcp: { serverName: SERVER, toolName: TOOL, url: URL_OK },
        };

        it("braucht keine Rückfrage über executeMcpTool, wenn der ausgeführte Eintrag die vertraute URL hat", async () => {
            const { checker, lookups } = checkerWithServers({ [SERVER]: URL_OK });
            expect(await checker.check(viaExecuteMcpTool(SERVER, TOOL))).toEqual(ALLOWED);
            expect(lookups).toEqual([SERVER]);
        });

        it("braucht keine Rückfrage über die Anbindung mcp:<server>:<tool>", async () => {
            const { checker, lookups } = checkerWithServers({ [SERVER]: URL_OK });
            expect(await checker.check(viaAttachment(SERVER, TOOL))).toEqual(ALLOWED);
            expect(lookups).toEqual([SERVER]);
        });

        it("gibt die geprüfte Schreibweise der URL weiter (die Ausführung vergleicht normalisiert)", async () => {
            const { checker } = checkerWithServers({ [SERVER]: "HTTP://127.1:8156/mcp" });
            expect(await checker.check(viaExecuteMcpTool(SERVER, TOOL))).toEqual({
                required: false,
                trustedMcp: { serverName: SERVER, toolName: TOOL, url: "HTTP://127.1:8156/mcp" },
            });
        });

        // E4: executeMcpTool mit den vertrauten Namen in ungültiger Form -> generische Rückfrage, kein Nachschlagen.
        it("fragt generisch bei ungültiger executeMcpTool-Eingabe mit den vertrauten Namen", async () => {
            const { checker, lookups } = checkerWithServers({ [SERVER]: URL_OK });
            const generic = {
                required: true,
                request: { kind: "tool", toolId: "builtin:executeMcpTool", toolName: "executeMcpTool" },
            };
            for (const odd of [
                { serverName: [SERVER], toolName: TOOL },
                { serverName: { name: SERVER }, toolName: TOOL },
                { serverName: SERVER },
                { serverName: SERVER, toolName: [TOOL] },
                null,
                [],
                `${SERVER}:${TOOL}`,
            ]) {
                expect(
                    await checker.check({ ...viaExecuteMcpTool(SERVER, TOOL), input: odd }),
                    JSON.stringify(odd),
                ).toEqual(generic);
            }
            expect(lookups).toEqual([]);
        });

        it("bleibt vertraut mit zusätzlichen Feldern in der executeMcpTool-Eingabe", async () => {
            const { checker } = checkerWithServers({ [SERVER]: URL_OK });
            expect(
                await checker.check({
                    ...viaExecuteMcpTool(SERVER, TOOL),
                    input: { serverName: SERVER, toolName: TOOL, arguments: {}, x: 1 },
                }),
            ).toEqual(ALLOWED);
        });

        // E5: kaputte toolIds -> Rückfrage ohne Nachschlagen.
        it("fragt bei unvollständigen mcp:-toolIds ohne nachzuschlagen", async () => {
            const { checker, lookups } = checkerWithServers({ [SERVER]: URL_OK });
            for (const toolId of [`mcp:${SERVER}:`, `mcp::${TOOL}`, `mcp:${SERVER}`, "mcp:", "mcp::"]) {
                expect(await checker.check({ ...input, toolId, toolName: TOOL, input: {} }), toolId).toMatchObject({
                    required: true,
                });
            }
            expect(lookups).toEqual([]);
        });

        it("fragt, wenn derselbe Servername auf eine andere URL zeigt", async () => {
            for (const url of ["http://127.0.0.1:9999/mcp", "http://evil.example.com:8156/mcp", "http://localhost:8156/mcp"]) {
                const { checker } = checkerWithServers({ [SERVER]: url });
                expect(await checker.check(viaExecuteMcpTool(SERVER, TOOL)), url).toEqual({
                    required: true,
                    request: { kind: "mcp", serverName: SERVER, toolName: TOOL },
                });
                expect(await checker.check(viaAttachment(SERVER, TOOL)), url).toEqual({
                    required: true,
                    request: { kind: "mcp", serverName: SERVER, toolName: TOOL },
                });
            }
        });

        it("fragt, wenn der Eintrag ein stdio-Server ist oder fehlt (URL null)", async () => {
            const { checker } = checkerWithServers({ [SERVER]: null });
            expect(await checker.check(viaExecuteMcpTool(SERVER, TOOL))).toMatchObject({ required: true });
            expect(await checker.check(viaAttachment(SERVER, TOOL))).toMatchObject({ required: true });
        });

        it("fragt (fail closed), wenn das Nachschlagen des Eintrags scheitert", async () => {
            const checker = new RealPermissionChecker({
                getPolicy: (name) => (name === "executeMcpTool" ? "mcp-execute" : undefined),
                getMcpServerUrl: async () => {
                    throw new Error("mcp.json kaputt");
                },
            });
            expect(await checker.check(viaExecuteMcpTool(SERVER, TOOL))).toEqual({
                required: true,
                request: { kind: "mcp", serverName: SERVER, toolName: TOOL },
            });
            expect(await checker.check(viaAttachment(SERVER, TOOL))).toMatchObject({ required: true });
        });

        it("fragt bei einem anderen Werkzeug auf dem vertrauten Server, ohne nachzuschlagen", async () => {
            const { checker, lookups } = checkerWithServers({ [SERVER]: URL_OK });
            expect(await checker.check(viaExecuteMcpTool(SERVER, "video_auftrag_loeschen"))).toEqual({
                required: true,
                request: { kind: "mcp", serverName: SERVER, toolName: "video_auftrag_loeschen" },
            });
            expect(await checker.check(viaAttachment(SERVER, "video_auftrag_loeschen"))).toMatchObject({
                required: true,
            });
            expect(lookups).toEqual([]);
        });

        it("fragt bei einem unbekannten Server, auch wenn er die vertraute URL hat", async () => {
            const { checker, lookups } = checkerWithServers({ andere: URL_OK });
            expect(await checker.check(viaExecuteMcpTool("andere", TOOL))).toEqual({
                required: true,
                request: { kind: "mcp", serverName: "andere", toolName: TOOL },
            });
            expect(await checker.check(viaAttachment("andere", TOOL))).toMatchObject({ required: true });
            expect(lookups).toEqual([]);
        });

        it("nimmt bei mcp:<server>:<tool> Server und Werkzeug aus der toolId wie die Ausführung", async () => {
            const { checker } = checkerWithServers({ [SERVER]: URL_OK });
            // Die Registry führt das Werkzeug der toolId aus (Rest nach dem ersten ":"), nicht toolName.
            expect(
                await checker.check({ ...viaAttachment(SERVER, "anderes"), toolName: TOOL }),
            ).toMatchObject({ required: true });
            expect(
                await checker.check({ ...input, toolId: `mcp:${SERVER}:${TOOL}:x`, toolName: TOOL, input: {} }),
            ).toMatchObject({ required: true });
        });

        it("ändert nichts für Nicht-MCP-Werkzeuge", async () => {
            const { checker, lookups } = checkerWithServers({ [SERVER]: URL_OK });
            expect(
                await checker.check({ ...input, toolId: "builtin:web-search", toolName: "web-search", input: {} }),
            ).toEqual({ required: false });
            expect(
                await checker.check({ ...input, toolId: "builtin:addMcpServer", toolName: "addMcpServer", input: {} }),
            ).toEqual({
                required: true,
                request: { kind: "tool", toolId: "builtin:addMcpServer", toolName: "addMcpServer" },
            });
            expect(lookups).toEqual([]);
        });
    });

    it("propagates metadata errors so the loop fails closed", async () => {
        const { checker } = makeChecker(new Error("policy exploded"));
        await expect(checker.check(input)).rejects.toThrowError("policy exploded");
    });
});
