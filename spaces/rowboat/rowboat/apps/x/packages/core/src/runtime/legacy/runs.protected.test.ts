// Modified for VibeMind (Flissel/rowboat vibemind): no "always" grant for human-only requests (ticket #330).
import fsSync from "node:fs";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterAll, beforeEach, describe, expect, it, vi } from "vitest";

const root = fsSync.realpathSync(fsSync.mkdtempSync(path.join(os.tmpdir(), "runs-protected-ws-")));
process.env.ROWBOAT_WORKDIR = root;

// The DI container is the only seam replaced: the run log it returns and the
// runtime trigger. The grant persistence (config/security.js) is real.
const state = vi.hoisted(() => ({ log: [] as unknown[], appended: [] as unknown[], triggered: 0 }));
vi.mock("../../di/container.js", () => ({
    default: {
        resolve: (name: string) => {
            if (name === "runsRepo") {
                return {
                    fetch: async () => ({ log: state.log }),
                    appendEvents: async (_runId: string, events: unknown[]) => {
                        state.appended.push(...events);
                    },
                };
            }
            if (name === "agentRuntime") {
                return { trigger: () => { state.triggered += 1; } };
            }
            throw new Error(`unexpected resolve ${name}`);
        },
    },
}));

const { authorizePermission } = await import("./runs.js");
const { resetSecurityAllowListCache } = await import("../../config/security.js");

const SECURITY = JSON.stringify({ allowedCommands: ["ls"], allowedFileAccess: [] });
const securityPath = path.join(root, "config", "security.json");

function permissionRequest(toolCallId: string, args: Record<string, unknown>, permission: Record<string, unknown>) {
    return {
        runId: "r1",
        type: "tool-permission-request",
        subflow: [],
        toolCall: { type: "tool-call", toolCallId, toolName: "x", arguments: args },
        permission,
    };
}

beforeEach(async () => {
    await fs.mkdir(path.dirname(securityPath), { recursive: true });
    await fs.writeFile(securityPath, SECURITY);
    resetSecurityAllowListCache();
    state.log = [];
    state.appended = [];
});

afterAll(async () => {
    await fs.rm(root, { recursive: true, force: true });
});

const approveAlways = (toolCallId: string) =>
    authorizePermission("r1", { toolCallId, response: "approve", scope: "always", subflow: [] }, "human");

describe("legacy authorizePermission and human-only requests", () => {
    it("persists nothing for an \"always\" answer to a human-only file request", async () => {
        const dir = path.join(root, "config");
        state.log = [
            permissionRequest("A", { path: "config/mcp.json" }, {
                kind: "file", operation: "write", paths: [path.join(dir, "mcp.json")], pathPrefix: dir, humanOnly: true,
            }),
        ];
        await approveAlways("A");
        expect(await fs.readFile(securityPath, "utf8")).toBe(SECURITY);
        // The approval itself is still recorded for this call.
        expect(state.appended).toMatchObject([{ type: "tool-permission-response", toolCallId: "A", response: "approve" }]);
    });

    it("persists nothing for an \"always\" answer to a human-only command request", async () => {
        state.log = [
            permissionRequest("B", { command: "echo x > config\\security.json" }, {
                kind: "command", commandNames: ["echo"], humanOnly: true,
            }),
        ];
        await approveAlways("B");
        expect(await fs.readFile(securityPath, "utf8")).toBe(SECURITY);
    });

    it("still persists an \"always\" answer to an ordinary request (control)", async () => {
        state.log = [permissionRequest("C", { command: "rm x" }, { kind: "command", commandNames: ["rm"] })];
        await approveAlways("C");
        expect(JSON.parse(await fs.readFile(securityPath, "utf8")).allowedCommands).toContain("rm");
    });
});

// Task 17 fix round 1 (review I-2): an answer from a restricted caller (scoped
// server key, embed bridge).
describe("legacy authorizePermission from a restricted caller", () => {
    const ordinary = () => [permissionRequest("C", { command: "rm x" }, { kind: "command", commandNames: ["rm"] })];

    for (const scope of ["always", "session"] as const) {
        it(`refuses scope "${scope}": allow-list and run log unchanged`, async () => {
            state.log = ordinary();
            await expect(
                authorizePermission("r1", { toolCallId: "C", response: "approve", scope, subflow: [] }, "delegated"),
            ).rejects.toThrow(/scope/);
            expect(await fs.readFile(securityPath, "utf8")).toBe(SECURITY);
            expect(state.appended).toEqual([]);
        });
    }

    it("refuses approving a human-only request", async () => {
        state.log = [permissionRequest("B", { command: "echo x > config\\security.json" }, {
            kind: "command", commandNames: ["echo"], humanOnly: true,
        })];
        await expect(
            authorizePermission("r1", { toolCallId: "B", response: "approve", scope: "once", subflow: [] }, "delegated"),
        ).rejects.toThrow(/human/);
        expect(state.appended).toEqual([]);
    });

    it("accepts a one-time approve of an ordinary request and a deny of a human-only one", async () => {
        state.log = [
            ...ordinary(),
            permissionRequest("B", { command: "echo x > config\\security.json" }, { kind: "command", commandNames: ["echo"], humanOnly: true }),
        ];
        await authorizePermission("r1", { toolCallId: "C", response: "approve", scope: "once", subflow: [] }, "delegated");
        await authorizePermission("r1", { toolCallId: "B", response: "deny", subflow: [] }, "delegated");
        expect(state.appended).toMatchObject([
            { toolCallId: "C", response: "approve" },
            { toolCallId: "B", response: "deny" },
        ]);
        expect(await fs.readFile(securityPath, "utf8")).toBe(SECURITY);
    });

    // Task 17 fix round 3 (review m-1): answeredBy has no default.
    it("an answer that does not say who answered is treated as delegated", async () => {
        state.log = ordinary();
        const unnamed = undefined as unknown as "human";
        await expect(
            authorizePermission("r1", { toolCallId: "C", response: "approve", scope: "always", subflow: [] }, unnamed),
        ).rejects.toThrow(/scope/);
        expect(await fs.readFile(securityPath, "utf8")).toBe(SECURITY);
        expect(state.appended).toEqual([]);
    });

    it("a request missing from the run log fails closed for an approve", async () => {
        state.log = [];
        await expect(
            authorizePermission("r1", { toolCallId: "Z", response: "approve", subflow: [] }, "delegated"),
        ).rejects.toThrow(/human/);
    });
});
