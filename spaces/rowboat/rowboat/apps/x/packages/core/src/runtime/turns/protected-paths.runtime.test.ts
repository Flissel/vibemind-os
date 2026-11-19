// Modified for VibeMind (Flissel/rowboat vibemind): protected paths through the whole turn loop (ticket #330).
// Real RealPermissionChecker, real RealToolRegistry -> execTool -> builtins, a temp workspace.
import fsSync from "node:fs";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { z } from "zod";
import type { JsonValue, ResolvedAgent, ToolDescriptor, TurnEvent } from "@x/shared/dist/turns.js";
import type { TurnExecution, TurnOutcome } from "./api.js";
import type { IModelRegistry, LlmStreamEvent, ModelStreamRequest, ResolvedModel } from "./model-registry.js";
import type {
    IPermissionChecker,
    IPermissionClassifier,
    PermissionCheckInput,
    PermissionClassification,
    PermissionClassificationBatch,
    PermissionClassificationInput,
} from "./permission.js";

const root = fsSync.realpathSync(fsSync.mkdtempSync(path.join(os.tmpdir(), "protected-turn-ws-")));
process.env.ROWBOAT_WORKDIR = root;

vi.mock("../../config/turn_limits.js", () => ({
    loadTurnLimitsSettings: async () => ({ maxModelCalls: 50 }),
}));
// No git commits of knowledge/ from a test workspace.
vi.mock("../../knowledge/version_history.js", () => ({
    commitAll: async () => undefined,
    initRepo: async () => undefined,
}));

const { TurnRuntime } = await import("./runtime.js");
const { InMemoryTurnRepo } = await import("./in-memory-turn-repo.js");
const { TurnRepoContextResolver } = await import("./context-resolver.js");
const { RealPermissionChecker } = await import("./bridges/real-permission-checker.js");
const { RealToolRegistry } = await import("./bridges/real-tool-registry.js");
const { resetSecurityAllowListCache } = await import("../../config/security.js");
const { isStdioEntryApproved, resetStdioApprovalsForTests } = await import("../../mcp/stdio-approvals.js");

type TEvent = z.infer<typeof TurnEvent>;
type CreateInput = Parameters<InstanceType<typeof TurnRuntime>["createTurn"]>[0];

const ws = (rel: string) => path.join(root, rel);
const SECURITY = JSON.stringify({ allowedCommands: ["echo", "awk", "dir", "type"], allowedFileAccess: [] });

function builtinDescriptor(name: string): z.infer<typeof ToolDescriptor> {
    return { toolId: `builtin:${name}`, name, description: name, inputSchema: {}, execution: "sync", requiresHuman: false };
}

const agent: z.infer<typeof ResolvedAgent> = {
    agentId: "copilot",
    systemPrompt: "SYS",
    model: { provider: "fake", model: "m" },
    tools: [
        builtinDescriptor("file-writeText"),
        builtinDescriptor("executeCommand"),
        builtinDescriptor("file-readText"),
        builtinDescriptor("addMcpServer"),
    ],
};

type Call = { id: string; name: string; args: JsonValue };

function respondCalls(...calls: Call[]) {
    return async function* (): AsyncGenerator<LlmStreamEvent, void, void> {
        yield {
            type: "completed",
            message: {
                role: "assistant",
                content: calls.map((c) => ({ type: "tool-call" as const, toolCallId: c.id, toolName: c.name, arguments: c.args })),
            },
            finishReason: "tool-calls",
            usage: { inputTokens: 1, outputTokens: 1, totalTokens: 2 },
        };
    };
}

function respondText(text: string) {
    return async function* (): AsyncGenerator<LlmStreamEvent, void, void> {
        yield {
            type: "completed",
            message: { role: "assistant", content: text },
            finishReason: "stop",
            usage: { inputTokens: 1, outputTokens: 1, totalTokens: 2 },
        };
    };
}

class ScriptedModels implements IModelRegistry {
    private next = 0;
    constructor(private readonly calls: Array<(r: ModelStreamRequest) => AsyncGenerator<LlmStreamEvent, void, void>>) {}
    async resolve(descriptor: ResolvedModel["descriptor"]): Promise<ResolvedModel> {
        return {
            descriptor,
            encodeMessages: (messages) => messages as unknown as JsonValue[],
            stream: (request) => {
                const call = this.calls[this.next++];
                if (!call) throw new Error("no scripted model call remaining");
                return call(request);
            },
        };
    }
}

// Records every batch; decides with impl (default: allow everything).
class SpyClassifier implements IPermissionClassifier {
    batches: PermissionClassificationInput[][] = [];
    constructor(private readonly decide: (r: PermissionClassificationInput) => PermissionClassification["decision"] = () => "allow") {}
    async classify(batch: PermissionClassificationBatch): Promise<PermissionClassification[]> {
        this.batches.push(batch.requests);
        return batch.requests.map((r) => ({ toolCallId: r.toolCallId, decision: this.decide(r), reason: "spy" }));
    }
}

let ids = 0;
function makeRuntime(models: ScriptedModels, classifier: IPermissionClassifier, checker?: IPermissionChecker) {
    const repo = new InMemoryTurnRepo();
    const runtime = new TurnRuntime({
        turnRepo: repo,
        idGenerator: { next: async () => `2026-11-16T10-00-00Z-${String(++ids).padStart(7, "0")}-000` },
        clock: { now: () => "2026-11-16T10:00:00Z" },
        agentResolver: { resolve: async () => agent },
        modelRegistry: models,
        toolRegistry: new RealToolRegistry(),
        contextResolver: new TurnRepoContextResolver({ turnRepo: repo }),
        permissionChecker: checker ?? new RealPermissionChecker(),
        permissionClassifier: classifier,
        lifecycleBus: { publish: () => undefined },
        turnEventBus: { publish: () => undefined, subscribe: () => () => undefined, subscribeAll: () => () => undefined },
        usageReporter: { reportModelUsage: () => undefined },
    });
    return { runtime, repo };
}

async function settle(execution: TurnExecution): Promise<TurnOutcome> {
    const drain = (async () => {
        try {
            for await (const _ of execution.events) { /* drain */ }
        } catch { /* outcome carries the error */ }
    })();
    const outcome = await execution.outcome;
    await drain;
    return outcome;
}

const DM: Partial<CreateInput> = { sessionId: "s1", analytics: { useCase: "copilot_chat" }, config: { humanAvailable: true } };
const DA: Partial<CreateInput> = { sessionId: "s1", analytics: { useCase: "copilot_chat" }, config: { humanAvailable: true, autoPermission: true } };
const SS: Partial<CreateInput> = { sessionId: "s1", analytics: { useCase: "todo_item_agent" }, config: { humanAvailable: false, autoPermission: true } };
const HL: Partial<CreateInput> = { sessionId: null, config: { humanAvailable: false, autoPermission: true } };

async function startTurn(runtime: InstanceType<typeof TurnRuntime>, mode: Partial<CreateInput>): Promise<string> {
    return runtime.createTurn({
        agent: { agentId: "copilot" },
        context: [],
        input: { role: "user", content: "go" },
        config: { humanAvailable: true },
        ...mode,
    } as CreateInput);
}

function required(log: TEvent[], toolCallId: string) {
    return log.find(
        (e): e is Extract<TEvent, { type: "tool_permission_required" }> =>
            e.type === "tool_permission_required" && e.toolCallId === toolCallId,
    );
}

function result(log: TEvent[], toolCallId: string) {
    return log.find(
        (e): e is Extract<TEvent, { type: "tool_result" }> => e.type === "tool_result" && e.toolCallId === toolCallId,
    );
}

const writeConfig = (id: string, rel = "config/mcp.json"): Call => ({
    id,
    name: "file-writeText",
    args: { path: rel, data: "{\"mcpServers\":{\"pwn\":{\"command\":\"calc\"}}}" },
});
const shell = (id: string, command: string): Call => ({ id, name: "executeCommand", args: { command } });

beforeEach(async () => {
    await fs.mkdir(ws("config"), { recursive: true });
    await fs.writeFile(ws("config/mcp.json"), "{\"orig\":true}");
    await fs.writeFile(ws("config/security.json"), SECURITY);
    resetSecurityAllowListCache();
    await fs.rm(ws("config/slack.json"), { force: true });
    await fs.rm(ws("config/slack2.json"), { force: true });
});

afterAll(async () => {
    await fs.rm(root, { recursive: true, force: true });
});

const unchangedConfig = async () => {
    expect(await fs.readFile(ws("config/mcp.json"), "utf8")).toBe("{\"orig\":true}");
    expect(await fs.readFile(ws("config/security.json"), "utf8")).toBe(SECURITY);
};

describe("protected paths in the turn loop", () => {
    it("DM: file-writeText config/mcp.json waits for a human-only request", async () => {
        const { runtime, repo } = makeRuntime(new ScriptedModels([respondCalls(writeConfig("A"))]), new SpyClassifier());
        const turnId = await startTurn(runtime, DM);
        const outcome = await settle(runtime.advanceTurn(turnId));
        expect(outcome.status).toBe("suspended");
        expect(required(await repo.read(turnId), "A")?.request).toMatchObject({ kind: "file", humanOnly: true });
        await unchangedConfig();
    });

    it("DA: the classifier never sees a human-only request; it reaches the human", async () => {
        const classifier = new SpyClassifier(() => "defer");
        const { runtime, repo } = makeRuntime(
            new ScriptedModels([respondCalls(writeConfig("A"), shell("B", "rm notes.txt"), shell("C", "echo hi > notes.txt"))]),
            classifier,
        );
        const turnId = await startTurn(runtime, DA);
        const outcome = await settle(runtime.advanceTurn(turnId));
        expect(classifier.batches.map((b) => b.map((r) => r.toolCallId))).toEqual([["B"]]);
        expect(outcome).toMatchObject({ status: "suspended" });
        const pending = (outcome as Extract<TurnOutcome, { status: "suspended" }>).pendingPermissions.map((p) => p.toolCallId);
        expect(pending).toEqual(expect.arrayContaining(["A", "B", "C"]));
        const log = await repo.read(turnId);
        expect(required(log, "A")?.request).toMatchObject({ humanOnly: true });
        // Any file redirect is human-only (controller's final ruling).
        expect(required(log, "C")?.request).toMatchObject({ kind: "command", humanOnly: true });
        // A command that is merely not allow-listed is a normal request (classifier may decide).
        expect(required(log, "B")?.request).toMatchObject({ kind: "command" });
        expect(required(log, "B")?.request).not.toHaveProperty("humanOnly");
        await unchangedConfig();
    });

    for (const [label, mode] of [["SS (to-do run)", SS], ["HL (headless)", HL]] as const) {
        it(`${label}: protected writes and shell writes into config/ are denied, classifier untouched`, async () => {
            const classifier = new SpyClassifier();
            const calls = [
                writeConfig("A"),
                shell("B", "awk 'BEGIN{print 1}' > config/mcp.json"),
                shell("C", "echo x > config\\security.json"),
                shell("D", "echo x > c%X%onfig\\security.json"),
                shell("E", "echo x > conf*\\mcp.json"),
                shell("F", "echo x > con^fig\\mcp.json"),
                shell("G", "echo x > \"con\"fig\\mcp.json"),
            ];
            const { runtime, repo } = makeRuntime(new ScriptedModels([respondCalls(...calls), respondText("done")]), classifier);
            const turnId = await startTurn(runtime, mode);
            const outcome = await settle(runtime.advanceTurn(turnId));
            expect(outcome.status).toBe("completed");
            expect(classifier.batches).toEqual([]);
            const log = await repo.read(turnId);
            for (const call of calls) {
                expect(required(log, call.id)?.request, call.id).toMatchObject({ humanOnly: true });
                expect(result(log, call.id)?.result, call.id).toMatchObject({ isError: true });
                expect(JSON.stringify(result(log, call.id)?.result), call.id).toMatch(/no human is available/);
            }
            await unchangedConfig();
        });
    }

    it("a classifier allow on a request that lost humanOnly still cannot write: execTool refuses", async () => {
        const real = new RealPermissionChecker();
        // Simulates a regression upstream: the checker forgets humanOnly.
        const forgetful: IPermissionChecker = {
            async check(input: PermissionCheckInput) {
                const verdict = await real.check(input);
                if (verdict.required && verdict.request && typeof verdict.request === "object" && !Array.isArray(verdict.request)) {
                    const { humanOnly: _dropped, ...rest } = verdict.request as Record<string, JsonValue>;
                    return { required: true as const, request: rest };
                }
                return verdict;
            },
        };
        const classifier = new SpyClassifier();
        const { runtime, repo } = makeRuntime(
            new ScriptedModels([respondCalls(writeConfig("A"), shell("B", "echo x > config\\security.json")), respondText("done")]),
            classifier,
            forgetful,
        );
        const turnId = await startTurn(runtime, DA);
        await settle(runtime.advanceTurn(turnId));
        expect(classifier.batches.flat().map((r) => r.toolCallId).sort()).toEqual(["A", "B"]);
        const log = await repo.read(turnId);
        for (const id of ["A", "B"]) {
            expect(JSON.stringify(result(log, id)?.result), id).toMatch(/protected workspace path/);
        }
        await unchangedConfig();
    });

    it("DM: the human approves file-writeText config/slack.json and it is written", async () => {
        const { runtime, repo } = makeRuntime(
            new ScriptedModels([respondCalls(writeConfig("A", "config/slack.json")), respondText("done")]),
            new SpyClassifier(),
        );
        const turnId = await startTurn(runtime, DM);
        expect((await settle(runtime.advanceTurn(turnId))).status).toBe("suspended");
        const outcome = await settle(
            runtime.advanceTurn(turnId, { type: "permission_decision", toolCallId: "A", decision: "allow", answeredBy: "human" }),
        );
        expect(outcome.status).toBe("completed");
        expect(result(await repo.read(turnId), "A")?.result).toMatchObject({ isError: false });
        expect(await fs.readFile(ws("config/slack.json"), "utf8")).toContain("mcpServers");
    });

    it("\"always allow\" on a human-only request persists nothing; the next call asks again", async () => {
        const { runtime, repo } = makeRuntime(
            new ScriptedModels([
                respondCalls(writeConfig("A", "config/slack.json")),
                respondText("done"),
                respondCalls(writeConfig("B", "config/slack2.json")),
            ]),
            new SpyClassifier(),
        );
        const first = await startTurn(runtime, DM);
        await settle(runtime.advanceTurn(first));
        await settle(
            runtime.advanceTurn(first, {
                type: "permission_decision",
                toolCallId: "A",
                decision: "allow",
                metadata: { scope: "always" },
                answeredBy: "human",
            }),
        );
        expect(await fs.readFile(ws("config/security.json"), "utf8")).toBe(SECURITY);
        const second = await startTurn(runtime, DM);
        expect((await settle(runtime.advanceTurn(second))).status).toBe("suspended");
        expect(required(await repo.read(second), "B")?.request).toMatchObject({ humanOnly: true });
        expect(fsSync.existsSync(ws("config/slack2.json"))).toBe(false);
    });

    describe("fix round 1 (review C1): reading server-key", () => {
        const readKey = (id: string): Call => ({ id, name: "file-readText", args: { path: "server-key" } });
        beforeEach(async () => {
            await fs.writeFile(ws("server-key"), "KEYVALUE-not-a-real-key\n");
        });

        it("DM: waits for a human-only read request", async () => {
            const { runtime, repo } = makeRuntime(new ScriptedModels([respondCalls(readKey("A"))]), new SpyClassifier());
            const turnId = await startTurn(runtime, DM);
            expect(await settle(runtime.advanceTurn(turnId))).toMatchObject({ status: "suspended" });
            expect(required(await repo.read(turnId), "A")?.request).toMatchObject({
                kind: "file",
                operation: "read",
                humanOnly: true,
            });
            expect(JSON.stringify(await repo.read(turnId))).not.toContain("KEYVALUE");
        });

        it("DA: the classifier is never asked", async () => {
            const classifier = new SpyClassifier();
            const { runtime, repo } = makeRuntime(new ScriptedModels([respondCalls(readKey("A"))]), classifier);
            const turnId = await startTurn(runtime, DA);
            expect((await settle(runtime.advanceTurn(turnId))).status).toBe("suspended");
            expect(classifier.batches).toEqual([]);
            expect(required(await repo.read(turnId), "A")?.request).toMatchObject({ humanOnly: true });
        });

        for (const [label, mode] of [["SS (to-do run)", SS], ["HL (headless)", HL]] as const) {
            it(`${label}: denied, the key never reaches the log`, async () => {
                const classifier = new SpyClassifier();
                const { runtime, repo } = makeRuntime(new ScriptedModels([respondCalls(readKey("A")), respondText("done")]), classifier);
                const turnId = await startTurn(runtime, mode);
                expect((await settle(runtime.advanceTurn(turnId))).status).toBe("completed");
                expect(classifier.batches).toEqual([]);
                const log = await repo.read(turnId);
                expect(result(log, "A")?.result).toMatchObject({ isError: true });
                expect(JSON.stringify(log)).not.toContain("KEYVALUE");
            });
        }
    });

    it("DM: file-writeText knowledge/x.md is unchanged — no prompt, written", async () => {
        const { runtime, repo } = makeRuntime(
            new ScriptedModels([respondCalls({ id: "A", name: "file-writeText", args: { path: "knowledge/x.md", data: "# x\n" } }), respondText("done")]),
            new SpyClassifier(),
        );
        const turnId = await startTurn(runtime, DM);
        expect((await settle(runtime.advanceTurn(turnId))).status).toBe("completed");
        const log = await repo.read(turnId);
        expect(required(log, "A")).toBeUndefined();
        expect(await fs.readFile(ws("knowledge/x.md"), "utf8")).toBe("# x\n");
    });
});

// Task 17: addMcpServer with a command asks a human only. Nothing connects or
// starts a process here (the tool only writes mcp.json and records the approval).
describe("addMcpServer with a command in the turn loop", () => {
    const EMPTY = "{\"mcpServers\":{}}";
    const addStdio = (id: string): Call => ({ id, name: "addMcpServer", args: { serverName: "pwn", config: { command: "calc.exe" } } });
    const addUrl = (id: string): Call => ({ id, name: "addMcpServer", args: { serverName: "web", config: { url: "http://127.0.0.1:9000/mcp" } } });

    beforeEach(async () => {
        await fs.writeFile(ws("config/mcp.json"), EMPTY);
        resetStdioApprovalsForTests();
    });

    it("DM: a human-only request; after the human's allow the entry is written and approved", async () => {
        const { runtime, repo } = makeRuntime(new ScriptedModels([respondCalls(addStdio("A")), respondText("done")]), new SpyClassifier());
        const turnId = await startTurn(runtime, DM);
        expect((await settle(runtime.advanceTurn(turnId))).status).toBe("suspended");
        expect(required(await repo.read(turnId), "A")?.request).toMatchObject({ kind: "tool", toolName: "addMcpServer", humanOnly: true });
        expect(await fs.readFile(ws("config/mcp.json"), "utf8")).toBe(EMPTY);
        const outcome = await settle(runtime.advanceTurn(turnId, { type: "permission_decision", toolCallId: "A", decision: "allow", answeredBy: "human" }));
        expect(outcome.status).toBe("completed");
        expect(result(await repo.read(turnId), "A")?.result).toMatchObject({ isError: false });
        expect(JSON.parse(await fs.readFile(ws("config/mcp.json"), "utf8"))).toEqual({ mcpServers: { pwn: { command: "calc.exe" } } });
        expect(isStdioEntryApproved("pwn", { command: "calc.exe" })).toBe(true);
    });

    it("DA: the classifier never sees it; it reaches the human (a URL entry is still classified)", async () => {
        const classifier = new SpyClassifier(() => "defer");
        const { runtime, repo } = makeRuntime(new ScriptedModels([respondCalls(addStdio("A"), addUrl("B"))]), classifier);
        const turnId = await startTurn(runtime, DA);
        expect((await settle(runtime.advanceTurn(turnId))).status).toBe("suspended");
        expect(classifier.batches.map((b) => b.map((r) => r.toolCallId))).toEqual([["B"]]);
        const log = await repo.read(turnId);
        expect(required(log, "A")?.request).toMatchObject({ humanOnly: true });
        expect(required(log, "B")?.request).not.toHaveProperty("humanOnly");
        expect(await fs.readFile(ws("config/mcp.json"), "utf8")).toBe(EMPTY);
    });

    for (const [label, mode] of [["SS (to-do run)", SS], ["HL (headless)", HL]] as const) {
        it(`${label}: denied without a human, classifier untouched, file unchanged`, async () => {
            const classifier = new SpyClassifier();
            const { runtime, repo } = makeRuntime(new ScriptedModels([respondCalls(addStdio("A")), respondText("done")]), classifier);
            const turnId = await startTurn(runtime, mode);
            expect((await settle(runtime.advanceTurn(turnId))).status).toBe("completed");
            expect(classifier.batches).toEqual([]);
            const log = await repo.read(turnId);
            expect(result(log, "A")?.result).toMatchObject({ isError: true });
            expect(await fs.readFile(ws("config/mcp.json"), "utf8")).toBe(EMPTY);
            expect(isStdioEntryApproved("pwn", { command: "calc.exe" })).toBe(false);
        });
    }

    // Task 17 fix round 1 (review I-2): an answer from a restricted caller (a
    // scoped server key, the embed bridge) is "delegated", never "human".
    describe("answers from a restricted caller", () => {
        const resolvedOf = (log: TEvent[], toolCallId: string) =>
            log.find(
                (e): e is Extract<TEvent, { type: "tool_permission_resolved" }> =>
                    e.type === "tool_permission_resolved" && e.toolCallId === toolCallId,
            );

        it("allowing a human-only request is refused: nothing written, nothing approved, still pending", async () => {
            const { runtime, repo } = makeRuntime(new ScriptedModels([respondCalls(addStdio("A")), respondText("done")]), new SpyClassifier());
            const turnId = await startTurn(runtime, DM);
            expect((await settle(runtime.advanceTurn(turnId))).status).toBe("suspended");
            await expect(
                settle(runtime.advanceTurn(turnId, { type: "permission_decision", toolCallId: "A", decision: "allow", answeredBy: "delegated" })),
            ).rejects.toThrow(/human/);
            expect(resolvedOf(await repo.read(turnId), "A")).toBeUndefined();
            expect(await fs.readFile(ws("config/mcp.json"), "utf8")).toBe(EMPTY);
            expect(isStdioEntryApproved("pwn", { command: "calc.exe" })).toBe(false);
            // The human can still answer it.
            const outcome = await settle(runtime.advanceTurn(turnId, { type: "permission_decision", toolCallId: "A", decision: "allow", answeredBy: "human" }));
            expect(outcome.status).toBe("completed");
            expect(resolvedOf(await repo.read(turnId), "A")).toMatchObject({ source: "human" });
            expect(isStdioEntryApproved("pwn", { command: "calc.exe" })).toBe(true);
        });

        it("denying a human-only request is accepted and recorded as delegated", async () => {
            const { runtime, repo } = makeRuntime(new ScriptedModels([respondCalls(addStdio("A")), respondText("done")]), new SpyClassifier());
            const turnId = await startTurn(runtime, DM);
            await settle(runtime.advanceTurn(turnId));
            const outcome = await settle(
                runtime.advanceTurn(turnId, { type: "permission_decision", toolCallId: "A", decision: "deny", answeredBy: "delegated" }),
            );
            expect(outcome.status).toBe("completed");
            const log = await repo.read(turnId);
            expect(resolvedOf(log, "A")).toMatchObject({ decision: "deny", source: "delegated" });
            expect(await fs.readFile(ws("config/mcp.json"), "utf8")).toBe(EMPTY);
        });

        it("a one-time allow of an ordinary request works and is recorded as delegated", async () => {
            const { runtime, repo } = makeRuntime(new ScriptedModels([respondCalls(addUrl("B")), respondText("done")]), new SpyClassifier());
            const turnId = await startTurn(runtime, DM);
            expect((await settle(runtime.advanceTurn(turnId))).status).toBe("suspended");
            const outcome = await settle(
                runtime.advanceTurn(turnId, { type: "permission_decision", toolCallId: "B", decision: "allow", answeredBy: "delegated" }),
            );
            expect(outcome.status).toBe("completed");
            expect(resolvedOf(await repo.read(turnId), "B")).toMatchObject({ decision: "allow", source: "delegated" });
        });

        for (const scope of ["always", "session"] as const) {
            it(`an answer with scope "${scope}" is refused, even for an ordinary request`, async () => {
                const { runtime, repo } = makeRuntime(new ScriptedModels([respondCalls(addUrl("B")), respondText("done")]), new SpyClassifier());
                const turnId = await startTurn(runtime, DM);
                await settle(runtime.advanceTurn(turnId));
                await expect(
                    settle(runtime.advanceTurn(turnId, {
                        type: "permission_decision",
                        toolCallId: "B",
                        decision: "allow",
                        metadata: { scope },
                        answeredBy: "delegated",
                    })),
                ).rejects.toThrow(/scope/);
                expect(resolvedOf(await repo.read(turnId), "B")).toBeUndefined();
                expect(await fs.readFile(ws("config/mcp.json"), "utf8")).toBe(EMPTY);
            });
        }

        // Task 17 fix round 3 (review m-1): answeredBy has no default. An input
        // that does not say who answered is not the human's (fails closed).
        it("an answer that does not say who answered is treated as delegated", async () => {
            const { runtime, repo } = makeRuntime(new ScriptedModels([respondCalls(addStdio("A")), respondText("done")]), new SpyClassifier());
            const turnId = await startTurn(runtime, DM);
            await settle(runtime.advanceTurn(turnId));
            const unnamed = { type: "permission_decision", toolCallId: "A", decision: "allow" } as unknown as Parameters<typeof runtime.advanceTurn>[1];
            await expect(settle(runtime.advanceTurn(turnId, unnamed))).rejects.toThrow(/human/);
            expect(isStdioEntryApproved("pwn", { command: "calc.exe" })).toBe(false);
            const denied = { type: "permission_decision", toolCallId: "A", decision: "deny" } as unknown as Parameters<typeof runtime.advanceTurn>[1];
            expect((await settle(runtime.advanceTurn(turnId, denied))).status).toBe("completed");
            expect(resolvedOf(await repo.read(turnId), "A")).toMatchObject({ decision: "deny", source: "delegated" });
        });
    });
});
