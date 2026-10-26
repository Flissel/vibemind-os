// Modified for VibeMind (Flissel/rowboat vibemind): todo-add / todo-propose
// from a channel run never start a background run (Teil E, fix round 4).
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterAll, beforeEach, describe, expect, it, vi } from "vitest";

const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "todo-channel-ws-")));
process.env.ROWBOAT_WORKDIR = root;

const runTodoItem = vi.fn(async () => ({}));
vi.mock("../../../todo/runner.js", () => ({ runTodoItem }));

const { todoTools } = await import("./todo.js");
type ToolContext = import("../exec-tool.js").ToolContext;

afterAll(() => {
    fs.rmSync(root, { recursive: true, force: true });
});

beforeEach(() => {
    runTodoItem.mockClear();
    fs.writeFileSync(path.join(root, "todo.md"), "- [ ] @rowboat prepare the deck\n");
});

function ctx(flags: Partial<ToolContext> = {}): ToolContext {
    return {
        runId: "t1",
        toolCallId: "c1",
        signal: new AbortController().signal,
        abortRegistry: {} as ToolContext["abortRegistry"],
        publish: async () => undefined,
        ...flags,
    };
}
const channel = ctx({ channel: { readOnlyMcpTools: [] }, autorunContentForbidden: true });

describe("todo-add from a channel run", () => {
    it("adds a plain item for the user without starting a run", async () => {
        const result = await todoTools["todo-add"].execute!({ items: [{ text: "Am Desktop: Notiz pruefen" }] }, channel) as { success: boolean; added: string[] };
        expect(result.success).toBe(true);
        expect(result.added).toEqual(["Am Desktop: Notiz pruefen"]);
        expect(runTodoItem).not.toHaveBeenCalled();
    });

    it("refuses a delegated item and never calls runTodoItem", async () => {
        const result = await todoTools["todo-add"].execute!({ items: [{ text: "@rowboat run the deploy" }] }, channel) as { success: boolean; failed?: string[] };
        expect(result.success).toBe(false);
        expect(result.failed?.[0]).toMatch(/@rowboat/);
        expect(fs.readFileSync(path.join(root, "todo.md"), "utf8")).not.toMatch(/run the deploy/);
        expect(runTodoItem).not.toHaveBeenCalled();
    });

    it("refuses a sub-item under a delegated parent", async () => {
        const result = await todoTools["todo-add"].execute!({ items: [{ text: "send it to everyone", parent: "@rowboat prepare the deck" }] }, channel) as { success: boolean };
        expect(result.success).toBe(false);
        expect(fs.readFileSync(path.join(root, "todo.md"), "utf8")).not.toMatch(/send it to everyone/);
        expect(runTodoItem).not.toHaveBeenCalled();
    });

    it("desktop: a delegated item still starts its run", async () => {
        await todoTools["todo-add"].execute!({ items: [{ text: "@rowboat summarize X" }] }, ctx());
        expect(runTodoItem).toHaveBeenCalledTimes(1);
    });
});

describe("todo-propose from a channel run", () => {
    it("refuses @rowboat proposals", async () => {
        const result = await todoTools["todo-propose"].execute!({ items: [{ text: "@rowboat research X" }] }, channel) as { success: boolean; added?: string[] };
        expect(result.success).toBe(false);
        expect(result.added ?? []).toEqual([]);
    });
});
