// Modified for VibeMind (Flissel/rowboat vibemind): ticket #330, Task 17 fix
// round 2 (review F-1). A coding agent's mid-run permission ask answered by a
// restricted caller (scoped server key, embed bridge) may only be rejected or
// allowed for this one request: "allow_always" is refused, and a one-time
// allow never selects the agent's own "always" option. No process is started.
import { describe, expect, it } from "vitest";
import type { PermissionOption, RequestPermissionRequest } from "@agentclientprotocol/sdk";
import { CodePermissionRegistry } from "./permission-registry.js";
import { PermissionBroker } from "./permission-broker.js";

const FULL: PermissionOption[] = [
    { optionId: "aa", name: "Always", kind: "allow_always" },
    { optionId: "ao", name: "Once", kind: "allow_once" },
    { optionId: "ro", name: "Reject", kind: "reject_once" },
];
const NO_ONCE: PermissionOption[] = [
    { optionId: "aa", name: "Always", kind: "allow_always" },
    { optionId: "ro", name: "Reject", kind: "reject_once" },
];

function request(options: PermissionOption[]): RequestPermissionRequest {
    return {
        sessionId: "s",
        toolCall: { toolCallId: "t", title: "Run shell", kind: "execute" },
        options,
    } as RequestPermissionRequest;
}

// A broker whose ask goes through the registry, as code_agent_run wires it.
function wired(registry: CodePermissionRegistry) {
    const ids: string[] = [];
    const resolved: string[] = [];
    const broker = new PermissionBroker({
        policy: "ask",
        ask: () => registry.request("run", (id) => ids.push(id)),
        onResolved: (_ask, decision) => resolved.push(decision),
    });
    return { broker, ids, resolved };
}

async function nextId(ids: string[]): Promise<string> {
    for (let i = 0; i < 50 && ids.length === 0; i++) await new Promise((r) => setTimeout(r, 1));
    const id = ids.shift();
    if (!id) throw new Error("no permission ask was registered");
    return id;
}

describe("code-run permission answers from a restricted caller", () => {
    it("allow_always from a delegated caller is refused and the ask stays pending; the human can still answer", async () => {
        const registry = new CodePermissionRegistry();
        const { broker, ids, resolved } = wired(registry);
        const answer = broker.resolve(request(FULL));
        const id = await nextId(ids);
        expect(() => registry.resolve(id, "allow_always", "delegated")).toThrow(/human's own answer/);
        registry.resolve(id, "allow_always", "human");
        expect(await answer).toEqual({ outcome: { outcome: "selected", optionId: "aa" } });
        expect(resolved).toEqual(["allow_always"]);
    });

    it("a delegated one-time allow selects allow_once and leaves no standing approval", async () => {
        const registry = new CodePermissionRegistry();
        const { broker, ids, resolved } = wired(registry);
        const first = broker.resolve(request(FULL));
        registry.resolve(await nextId(ids), "allow_once", "delegated");
        expect(await first).toEqual({ outcome: { outcome: "selected", optionId: "ao" } });
        // The next ask of the same kind is asked again (no sticky memory).
        const second = broker.resolve(request(FULL));
        registry.resolve(await nextId(ids), "reject", "delegated");
        expect(await second).toEqual({ outcome: { outcome: "selected", optionId: "ro" } });
        expect(resolved).toEqual(["allow_once", "reject"]);
    });

    it("a delegated one-time allow never falls back to the agent's allow_always option: it rejects", async () => {
        const registry = new CodePermissionRegistry();
        const { broker, ids, resolved } = wired(registry);
        const answer = broker.resolve(request(NO_ONCE));
        registry.resolve(await nextId(ids), "allow_once", "delegated");
        expect(await answer).toEqual({ outcome: { outcome: "selected", optionId: "ro" } });
        expect(resolved).toEqual(["reject"]);
    });

    it("the human's one-time allow keeps the existing fallback to allow_always", async () => {
        const registry = new CodePermissionRegistry();
        const { broker, ids } = wired(registry);
        const answer = broker.resolve(request(NO_ONCE));
        registry.resolve(await nextId(ids), "allow_once", "human");
        expect(await answer).toEqual({ outcome: { outcome: "selected", optionId: "aa" } });
    });

    it("an unknown request id is a no-op for every caller", () => {
        const registry = new CodePermissionRegistry();
        expect(() => registry.resolve("cpr-x-1", "allow_once", "delegated")).not.toThrow();
        expect(() => registry.resolve("cpr-x-1", "reject", "delegated")).not.toThrow();
    });
});
