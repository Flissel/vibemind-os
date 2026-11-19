// Modified for VibeMind (Flissel/rowboat vibemind): ticket #330, Task 17 fix
// round 1 (review I-1). A restricted caller (a scoped server key, the embed
// bridge) may neither read nor change protected workspace entries through any
// channel that takes a path. Listing the workspace root stays allowed.
// Temp workspace; nothing is executed.
import fsSync from "node:fs";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

const root = fsSync.realpathSync(fsSync.mkdtempSync(path.join(os.tmpdir(), "restricted-principal-ws-")));
process.env.ROWBOAT_WORKDIR = root;

const {
    restrictedCallRefused,
    filterRestrictedReaddir,
    restrictedResult,
    RESTRICTED_PATH_CHANNELS,
    RESTRICTED_DENIED_CHANNELS,
    RESTRICTED_CONDITIONAL_CHANNELS,
    RESTRICTED_MASKED_CHANNELS,
    RESTRICTED_GUARDED_CHANNELS,
    EMBED_IS_TRUSTED,
    originIsRestricted,
    answeredByForOrigin,
    forwardHeadersForOrigin,
    embedMayServe,
} = await import("./restricted-principal.js");
const { ipcSchemas } = await import("@x/shared/dist/ipc.js");

const deps = { codeSessionCwd: async (id: string) => (id === "ws" ? root : id === "other" ? path.join(root, "knowledge") : null) };
const refused = (channel: string, args: unknown) => restrictedCallRefused(channel, args, deps);

beforeAll(async () => {
    await fs.mkdir(path.join(root, "config"), { recursive: true });
    await fs.mkdir(path.join(root, "knowledge"), { recursive: true });
    await fs.writeFile(path.join(root, "server-key"), "k");
    await fs.writeFile(path.join(root, "knowledge", "a.md"), "# a");
});

afterAll(async () => {
    await fs.rm(root, { recursive: true, force: true });
});

describe("restricted caller: reads of protected entries", () => {
    it.each([
        ["workspace:readFile", { path: "server-key" }],
        ["workspace:readFile", { path: "config/mcp.json" }],
        ["workspace:readFile", { path: "CONFIG./security.json" }],
        ["workspace:stat", { path: "cipher-key" }],
        ["workspace:exists", { path: "embed.json" }],
        ["workspace:readdir", { path: "config" }],
        ["workspace:readdir", { path: "channels" }],
        ["workspace:exportCopy", { path: "scoped-keys.json" }],
        ["shell:readFileBase64", { path: "server-key" }],
        ["shell:readFileBase64", { path: path.join(root, "server-key") }],
        ["shell:previewFile", { path: "config/mcp.json" }],
        ["spreadsheet:load", { path: "config/x.csv", offset: 0, limit: 1 }],
        ["spreadsheet:find", { path: "config/x.csv", query: "a" }],
        ["spaces:uploadBlob", { orgId: "o", spaceId: "s", filePath: path.join(root, "server-key") }],
        ["knowledge:fileAtCommit", { path: "../server-key", oid: "x" }],
        ["knowledge:history", { path: "../config/mcp.json" }],
        ["live-note:get", { filePath: "../config/mcp.json" }],
        ["google-docs:sync", { path: "config/x.docx" }],
        ["google-docs:getLink", { path: "config/x.docx" }],
        ["google-docs:import", { fileId: "f", targetFolder: "config" }],
        ["codeSession:readFile", { sessionId: "ws", relPath: "server-key" }],
        ["codeSession:readdir", { sessionId: "ws", relPath: "config" }],
        ["codeSession:fileDiff", { sessionId: "ws", path: "config/mcp.json" }],
    ])("%s %j is refused", async (channel, args) => {
        expect(await refused(channel, args)).toBe(true);
    });

    it.each([
        ["workspace:readdir", { path: "" }],
        ["workspace:readdir", { path: "knowledge" }],
        ["workspace:readFile", { path: "knowledge/a.md" }],
        ["workspace:stat", { path: "knowledge" }],
        ["shell:readFileBase64", { path: "knowledge/a.md" }],
        ["spreadsheet:load", { path: "config/x.csv", attachment: { orgId: "o", spaceId: "s", hash: "a".repeat(64) }, offset: 0, limit: 1 }],
        ["knowledge:fileAtCommit", { path: "a.md", oid: "x" }],
        ["live-note:get", { filePath: "a.md" }],
        ["google-docs:import", { fileId: "f", targetFolder: "knowledge" }],
        ["codeSession:readdir", { sessionId: "ws", relPath: "" }],
        ["codeSession:readFile", { sessionId: "other", relPath: "a.md" }],
        ["sessions:list", {}],
    ])("%s %j stays allowed", async (channel, args) => {
        expect(await refused(channel, args)).toBe(false);
    });

    it("an unknown code session fails closed", async () => {
        expect(await refused("codeSession:readFile", { sessionId: "nope", relPath: "a.md" })).toBe(true);
    });

    it("missing or malformed path arguments fail closed", async () => {
        expect(await refused("workspace:readFile", {})).toBe(true);
        expect(await refused("workspace:readFile", null)).toBe(true);
        expect(await refused("shell:readFileBase64", { path: 42 })).toBe(true);
    });
});

describe("restricted caller: writes of protected entries", () => {
    it.each([
        ["workspace:writeFile", { path: "config/mcp.json", data: "{}" }],
        ["workspace:copy", { from: "server-key", to: "knowledge/k" }],
        ["workspace:remove", { path: "" }],
        ["knowledge:restore", { path: "../config/mcp.json", oid: "x" }],
        ["live-note:set", { filePath: "../config/mcp.json", live: {} }],
        ["google-docs:refreshSnapshot", { path: "config/x.docx" }],
    ])("%s %j is refused", async (channel, args) => {
        expect(await refused(channel, args)).toBe(true);
    });

    it("an unprotected write stays allowed", async () => {
        expect(await refused("workspace:writeFile", { path: "knowledge/b.md", data: "x" })).toBe(false);
    });
});

describe("restricted caller: recursive listing of the workspace", () => {
    it("drops entries inside protected entries, keeps the rest", async () => {
        const entries = [
            { name: "config", path: "config", kind: "dir" as const },
            { name: "mcp.json", path: "config/mcp.json", kind: "file" as const },
            { name: "x", path: "Channels/x", kind: "file" as const },
            { name: "a.md", path: "knowledge/a.md", kind: "file" as const },
        ];
        const kept = await filterRestrictedReaddir({ path: "", opts: { recursive: true } }, entries);
        expect(kept.map((e) => e.path)).toEqual(["config", "knowledge/a.md"]);
    });

    it("a non-recursive listing is unchanged", async () => {
        const entries = [{ name: "config", path: "config", kind: "dir" as const }];
        expect(await filterRestrictedReaddir({ path: "" }, entries)).toEqual(entries);
    });

    // Fix round 2 (automated review): each entry is checked by its resolved
    // path, not by its first lexical segment.
    it("checks each entry by its resolved path: a listing requested as knowledge/.. or with backslashes", async () => {
        const entries = [
            { name: "config", path: "knowledge/../config", kind: "dir" as const },
            { name: "mcp.json", path: "knowledge/../config/mcp.json", kind: "file" as const },
            { name: "server-key", path: "knowledge/../server-key", kind: "file" as const },
            { name: "mcp.json", path: "knowledge\\..\\config\\mcp.json", kind: "file" as const },
            { name: "a.md", path: "knowledge/../knowledge/a.md", kind: "file" as const },
        ];
        const forward = await filterRestrictedReaddir({ path: "knowledge/..", opts: { recursive: true } }, entries);
        expect(forward.map((e) => e.path)).toEqual(["knowledge/../config", "knowledge/../server-key", "knowledge/../knowledge/a.md"]);
        const back = await filterRestrictedReaddir({ path: "knowledge\\..", opts: { recursive: true } }, entries);
        expect(back.map((e) => e.path)).toEqual(forward.map((e) => e.path));
    });
});

describe("restricted caller: shell path channels resolve the path as the consumer does", () => {
    it.each([
        ["shell:readFileBase64", { path: "~.rowboat/server-key" }],
        ["shell:readFileBase64", { path: "~server-key" }],
        ["shell:previewFile", { path: "~x" }],
        ["shell:readFileBase64", { path: "knowledge/../server-key" }],
        ["shell:readFileBase64", { path: "config\\mcp.json" }],
    ])("%s %j is refused", async (channel, args) => {
        expect(await refused(channel, args)).toBe(true);
    });

    it("~, ~/ and ~\\ resolve to the home folder as in main", async () => {
        expect(await refused("shell:readFileBase64", { path: "~/x.txt" })).toBe(false);
        expect(await refused("shell:readFileBase64", { path: "~\\x.txt" })).toBe(false);
    });
});

describe("restricted caller: config and authority channels (review F-2)", () => {
    it("every denied channel is refused whatever its arguments", async () => {
        for (const channel of RESTRICTED_DENIED_CHANNELS.keys()) {
            expect(await refused(channel, {}), channel).toBe(true);
            expect(await refused(channel, null), channel).toBe(true);
        }
    });

    it("names real channels only, each with what it writes", () => {
        const known = new Set(Object.keys(ipcSchemas));
        for (const channel of [...RESTRICTED_DENIED_CHANNELS.keys(), ...RESTRICTED_CONDITIONAL_CHANNELS, ...RESTRICTED_MASKED_CHANNELS]) {
            expect(known.has(channel), channel).toBe(true);
        }
        for (const [channel, writes] of RESTRICTED_DENIED_CHANNELS) {
            expect(writes.length, channel).toBeGreaterThan(0);
        }
        for (const channel of [...RESTRICTED_DENIED_CHANNELS.keys(), ...RESTRICTED_CONDITIONAL_CHANNELS, ...RESTRICTED_MASKED_CHANNELS, ...RESTRICTED_PATH_CHANNELS]) {
            expect(RESTRICTED_GUARDED_CHANNELS.has(channel), channel).toBe(true);
        }
    });

    it("covers the channels the ruling names", () => {
        for (const channel of [
            "channels:setConfig", "codeMode:setConfig", "agent-schedule:updateAgent",
            "server:connectRemote", "server:rotateKey", "server:setLanEnabled",
            "models:setProvider", "models:updateConfig", "slack:setConfig", "granola:setConfig",
            "notifications:setSettings", "turnLimits:setSettings", "retention:setSettings", "knowledgeSources:upsert",
        ]) {
            expect(RESTRICTED_DENIED_CHANNELS.has(channel), channel).toBe(true);
        }
    });

    it.each([
        ["codeSession:update", { sessionId: "s", patch: { policy: "yolo" } }],
        ["codeSession:update", { sessionId: "s", patch: { policy: "ask" } }],
        ["codeSession:update", { sessionId: "s", patch: { clearPolicy: true } }],
        ["codeSession:update", { sessionId: "s" }],
        ["codeSession:create", { projectId: "p", agent: "claude", isolation: "in-repo", policy: "yolo" }],
        ["runs:createMessage", { runId: "r", message: "hi", codePolicy: "yolo" }],
    ])("%s %j is refused", async (channel, args) => {
        expect(await refused(channel, args)).toBe(true);
    });

    it.each([
        ["codeSession:update", { sessionId: "s", patch: { title: "t", agentModel: "opus" } }],
        ["codeSession:create", { projectId: "p", agent: "claude", isolation: "in-repo" }],
        ["runs:createMessage", { runId: "r", message: "hi" }],
        ["sessions:sendMessage", { sessionId: "s", input: { role: "user", content: "hi" } }],
    ])("%s %j stays allowed", async (channel, args) => {
        expect(await refused(channel, args)).toBe(false);
    });
});

describe("restricted caller: getters that return credentials (review F-2)", () => {
    it("channels:getConfig drops the Telegram bot token, keeps the rest", async () => {
        const config = {
            whatsapp: { enabled: true, allowFrom: ["49"] },
            telegram: { enabled: true, botToken: "123:SECRET", allowFrom: ["7"] },
            readOnlyMcpTools: ["a:b"],
        };
        const masked = await restrictedResult("channels:getConfig", null, config);
        expect(masked).toEqual({ ...config, telegram: { ...config.telegram, botToken: "" } });
        expect(JSON.stringify(masked)).not.toContain("SECRET");
        expect(config.telegram.botToken).toBe("123:SECRET");
    });

    it("channels:getStatus drops the WhatsApp pairing QR", async () => {
        const status = { whatsapp: { state: "qr", qrDataUrl: "data:image/png;base64,QR" }, telegram: { state: "polling", botUsername: "b" } };
        expect(await restrictedResult("channels:getStatus", null, status)).toEqual({ whatsapp: { state: "qr" }, telegram: status.telegram });
    });

    it("voice:getConfig, account:getRowboat and server:getPairingInfo return no key or token", async () => {
        expect(await restrictedResult("voice:getConfig", null, { deepgram: { apiKey: "dg" }, elevenlabs: { apiKey: "el", voiceId: "v" } }))
            .toEqual({ deepgram: null, elevenlabs: null });
        expect(await restrictedResult("account:getRowboat", null, { signedIn: true, accessToken: "tok", config: { a: 1 } }))
            .toEqual({ signedIn: true, accessToken: null, config: { a: 1 } });
        expect(await restrictedResult("server:getPairingInfo", null, { url: "http://x", token: "owner" }))
            .toEqual({ url: "http://x", token: null });
    });

    it("other results pass through unchanged", async () => {
        const result = { a: 1 };
        expect(await restrictedResult("sessions:list", null, result)).toBe(result);
    });
});

// Task 17 fix round 3 (operator decision 19.11.2026): the embedded view is a
// trusted caller for now. Every origin helper takes the switch as a parameter,
// so both values are tested; the default is EMBED_IS_TRUSTED.
describe("embed trust switch", () => {
    it("is on for now (operator decision 19.11.2026)", () => {
        expect(EMBED_IS_TRUSTED).toBe(true);
        expect(originIsRestricted("embed")).toBe(!EMBED_IS_TRUSTED);
    });

    it.each([true, false])("trusted=%s: the renderer is never restricted, a call with no recorded origin always is", (trusted) => {
        expect(originIsRestricted("renderer", trusted)).toBe(false);
        expect(originIsRestricted("unknown", trusted)).toBe(true);
        expect(originIsRestricted("embed", trusted)).toBe(!trusted);
    });

    it.each([true, false])("trusted=%s: who answered a permission request", (trusted) => {
        expect(answeredByForOrigin("renderer", trusted)).toBe("human");
        expect(answeredByForOrigin("unknown", trusted)).toBe("delegated");
        expect(answeredByForOrigin("embed", trusted)).toBe(trusted ? "human" : "delegated");
    });

    it.each([true, false])("trusted=%s: the principal header on calls forwarded to the server", (trusted) => {
        const restrictedHeader = { "x-rowboat-principal": "restricted" };
        expect(forwardHeadersForOrigin("renderer", trusted)).toEqual({});
        expect(forwardHeadersForOrigin("unknown", trusted)).toEqual(restrictedHeader);
        expect(forwardHeadersForOrigin("embed", trusted)).toEqual(trusted ? {} : restrictedHeader);
    });

    it.each([true, false])("trusted=%s: app://workspace serves protected entries to the embed only when trusted", async (trusted) => {
        expect(await embedMayServe("app://workspace/server-key", trusted)).toBe(trusted);
        expect(await embedMayServe("app://workspace/config/mcp.json", trusted)).toBe(trusted);
        expect(await embedMayServe("not a url", trusted)).toBe(trusted);
        expect(await embedMayServe("app://workspace/knowledge/a.md", trusted)).toBe(true);
        expect(await embedMayServe("app://renderer/index.html", trusted)).toBe(true);
    });
});

// Task 17 fix round 3 (review I-1, I-2, I-4, m-2): channels a scoped server
// key may not use (and the embed, when the switch is off).
describe("restricted caller: Spaces authority, tool execution, app consent (fix round 3)", () => {
    it("denies the channels the ruling names", () => {
        for (const channel of [
            "spaces:addAgent", "spaces:createInvite",
            "spaces:setMemberRole", "spaces:removeOrgMember", "spaces:setInvitePolicy", "spaces:addMembers",
            "spaces:setDefaultSpace", "spaces:setSpaceVisibility", "spaces:setAgentOptionDefaults", "spaces:acceptInvite",
            "mcp:executeTool", "composio:execute-tool",
            "apps:install", "apps:installFromUrl", "apps:publish", "apps:publishUpdate", "apps:registerExisting",
            "integrations:add", "integrations:abmelden", "integrations:entfernen", "integrations:neuVerbinden",
            "bg-task:create", "bg-task:patch", "todo:setPlanner",
            "oauth:deliverLoopbackCallback", "referral:claim",
        ]) {
            expect(RESTRICTED_DENIED_CHANNELS.has(channel), channel).toBe(true);
        }
    });

    it("leaves the todo channels the Freigabe and Werkbank bots use open", async () => {
        for (const channel of ["todo:get", "todo:plannerSignals", "todo:addSuggestions", "todo:withdrawSuggestion", "todo:setChecked"]) {
            expect(RESTRICTED_GUARDED_CHANNELS.has(channel), channel).toBe(false);
            expect(await refused(channel, null), channel).toBe(false);
        }
    });

    it.each(["allow_once", "allow_session", "allow_always"])("spaces:decideApproval %s is refused", async (decision) => {
        expect(await refused("spaces:decideApproval", { orgId: "o", spaceId: "s", approvalId: "a", decision })).toBe(true);
    });

    it("spaces:decideApproval deny stays allowed; a missing decision fails closed", async () => {
        expect(await refused("spaces:decideApproval", { orgId: "o", spaceId: "s", approvalId: "a", decision: "deny", note: "no" })).toBe(false);
        expect(await refused("spaces:decideApproval", { orgId: "o", spaceId: "s", approvalId: "a" })).toBe(true);
    });

    it("apps:update is refused only when it confirms new capabilities itself", async () => {
        expect(await refused("apps:update", { folder: "f", confirmNewCapabilities: true })).toBe(true);
        expect(await refused("apps:update", { folder: "f", confirmNewCapabilities: false })).toBe(true);
        expect(await refused("apps:update", { folder: "f" })).toBe(false);
    });
});

describe("channel table", () => {
    it("names every path-carrying channel it gates", () => {
        expect([...RESTRICTED_PATH_CHANNELS].sort()).toEqual([
            "codeSession:fileDiff", "codeSession:readFile", "codeSession:readdir",
            "google-docs:getLink", "google-docs:import", "google-docs:pickViaManaged",
            "google-docs:refreshSnapshot", "google-docs:sync",
            "knowledge:fileAtCommit", "knowledge:history", "knowledge:restore",
            "live-note:delete", "live-note:get", "live-note:run", "live-note:set",
            "live-note:setActive", "live-note:stop",
            "shell:previewFile", "shell:readFileBase64",
            "spaces:uploadBlob", "spreadsheet:find", "spreadsheet:load",
            "workspace:copy", "workspace:exists", "workspace:exportCopy", "workspace:mkdir",
            "workspace:readFile", "workspace:readdir", "workspace:remove", "workspace:rename",
            "workspace:stat", "workspace:writeFile",
        ].sort());
    });
});
