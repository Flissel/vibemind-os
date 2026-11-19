// Modified for VibeMind (Flissel/rowboat vibemind): the one "is this Rowboat's
// own local server" predicate, shared by fetch-url (core) and the agent-driven
// browser session (apps/main) — ticket #330, fix round 2 (review N2, m2).
// No network: DNS, ports, interfaces and the host name are injected stubs.
import fsSync from "node:fs";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterAll, describe, expect, it } from "vitest";

const root = fsSync.realpathSync(fsSync.mkdtempSync(path.join(os.tmpdir(), "own-server-target-")));
process.env.ROWBOAT_WORKDIR = root;

const { commandMentionsOwnServer, installOwnServerBlock, isOwnServerTarget, memoizePorts } = await import("./own-server-target.js");
type Deps = NonNullable<Parameters<typeof isOwnServerTarget>[1]>;

afterAll(async () => {
    await fs.rm(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
});

const PORTS = new Set(Array.from({ length: 10 }, (_, i) => 3220 + i));

function deps(overrides: Partial<Deps> = {}): Deps {
    return {
        lookup: async (host) => {
            if (host === "rebind.example") return ["127.0.0.1"];
            if (host === "lan-alias.example") return ["192.168.1.50"];
            if (host === "example.com") return ["93.184.216.34"];
            throw new Error(`ENOTFOUND ${host}`);
        },
        ports: async () => PORTS,
        interfaces: () => ["127.0.0.1", "::1", "192.168.1.50", "fe80::1%eth0"],
        hostname: () => "MyHost",
        appsPorts: () => new Set([3210]),
        ...overrides,
    };
}

// [url, is the own server]
const TABLE: Array<[string, boolean]> = [
    // Loopback forms on the server's port and its fallbacks.
    ["http://127.0.0.1:3220/rpc/workspace:writeFile?token=x", true],
    ["https://localhost:3221/", true],
    ["http://LOCALHOST.:3220/rpc/x", true],
    ["http://app.localhost:3220/", true],
    ["http://127.9.9.9:3229/", true],
    ["http://0.0.0.0:3220/", true],
    ["http://[::1]:3220/", true],
    ["http://[::]:3220/", true],
    ["http://[::ffff:127.0.0.1]:3220/", true],
    ["http://2130706433:3220/", true],
    ["http://0x7f000001:3220/", true],
    // WebSockets.
    ["ws://127.0.0.1:3220/socket", true],
    ["wss://[::1]:3229/", true],
    // Own host name and own LAN address, incl. the v4-mapped forms (m2).
    ["http://myhost:3220/", true],
    ["http://MyHost.local:3220/", true],
    ["http://192.168.1.50:3220/", true],
    ["http://[::ffff:192.168.1.50]:3220/", true],
    ["http://[::ffff:c0a8:132]:3220/", true],
    ["http://[fe80::1]:3220/", true],
    // Names that resolve to the own machine, or cannot be resolved.
    ["http://rebind.example:3220/", true],
    ["http://lan-alias.example:3220/", true],
    ["http://unresolvable.example:3220/", true],
    // Not the own server.
    // Fix round 3 (review I-A): the apps server (host API /_rowboat/* incl.
    // the fetch proxy) counts as own for fetch-url, in every host form.
    ["http://127.0.0.1:3210/_rowboat/fetch", true],
    ["http://demo.apps.localhost:3210/", true],
    ["http://[::1]:3210/index.html", true],
    ["http://192.168.1.50:3210/_rowboat/fetch", true],
    ["http://rebind.example:3210/", true],
    ["http://example.com:3210/", false],
    ["http://127.0.0.1:3211/", false],
    ["http://example.com:3220/", false],
    ["http://127.0.0.1:3230/", false],
    ["http://127.0.0.1:5173/", false],
    ["http://localhost/", false],
    ["https://localhost/", false],
    ["http://192.168.1.51:3220/", false],
    ["http://[::ffff:192.168.1.51]:3220/", false],
    ["https://example.com/", false],
    ["data:text/html,<form action='http://127.0.0.1:3220/rpc/x'>", false],
    ["blob:http://127.0.0.1:3220/0c1f", false],
    ["file:///C:/Users/x/.rowboat/server-key", false],
    ["chrome-extension://abc/page.html", false],
];

describe("isOwnServerTarget", () => {
    it.each(TABLE)("%s -> %s", async (raw, expected) => {
        expect(await isOwnServerTarget(new URL(raw), deps())).toBe(expected);
    });

    it("never looks up a name on a port outside the own-server set", async () => {
        const lookups: string[] = [];
        const d = deps({ lookup: async (host) => (lookups.push(host), ["127.0.0.1"]) });
        expect(await isOwnServerTarget(new URL("http://anything.example:8080/"), d)).toBe(false);
        expect(lookups).toEqual([]);
    });
});

describe("commandMentionsOwnServer (second layer for executeCommand, evadable)", () => {
    const ports = async () => PORTS;
    it.each([
        "curl -X POST http://127.0.0.1:3220/rpc/workspace:writeFile",
        "curl http://localhost:3221",
        "Invoke-WebRequest -Uri http://LOCALHOST:3229 -Method Post",
        "curl -d @x http://[::1]:3220",
        "curl http://0.0.0.0:3225/x",
        "curl http://0x7f000001:3220/",
        "curl http://2130706433:3220/",
        "curl http://[::ffff:7f00:1]:3220/",
        "curl http://app.localhost:3220/",
        "curl 127.0.0.1 --port 3220",
        "curl ht\"\"tp://127.0.0.1:32^20/",
        // The literal /rpc/ path alone counts, whatever the host.
        "curl http://myhost:9999/rpc/x",
        "wget -qO- http://192.168.1.50:3220/RPC/sessions:respondToPermission",
    ])("flags %s", async (command) => {
        expect(await commandMentionsOwnServer(command, ports)).toBe(true);
    });

    it.each([
        "curl http://127.0.0.1:5173/",
        "curl http://localhost:8080/api",
        "npm run dev -- --port 3220",
        "echo 3220",
        "ping 127.0.0.1",
        "curl https://example.com:3220/",
        "git log --oneline -5",
    ])("leaves %s alone", async (command) => {
        expect(await commandMentionsOwnServer(command, ports)).toBe(false);
    });
});

describe("memoizePorts", () => {
    it("reads the port set once per interval", async () => {
        let reads = 0;
        let now = 1000;
        const ports = memoizePorts(async () => (reads++, PORTS), 2000, () => now);
        await ports();
        await ports();
        now += 1999;
        await ports();
        expect(reads).toBe(1);
        now += 1;
        await ports();
        expect(reads).toBe(2);
    });
});

type Listener = (details: { url: string; resourceType?: string; method?: string }, callback: (response: { cancel?: boolean }) => void) => void;

function fakeSession() {
    const listeners: Listener[] = [];
    return {
        listeners,
        webRequest: {
            onBeforeRequest(listener: Listener) {
                listeners.push(listener);
            },
        },
    };
}

function decide(listener: Listener, details: { url: string; resourceType?: string; method?: string }): Promise<{ cancel?: boolean }> {
    return new Promise((resolve) => listener(details, resolve));
}

describe("installOwnServerBlock", () => {
    it("registers exactly one onBeforeRequest listener on the given session", () => {
        const ses = fakeSession();
        installOwnServerBlock(ses, deps());
        expect(ses.listeners).toHaveLength(1);
    });

    it("cancels every resource type that goes to the own server", async () => {
        const ses = fakeSession();
        installOwnServerBlock(ses, deps());
        const [listener] = ses.listeners;
        const requests = [
            // A form POST from a data: page (review N2 scenario).
            { url: "http://127.0.0.1:3220/rpc/workspace:writeFile?token=k", resourceType: "mainFrame", method: "POST" },
            { url: "http://localhost:3220/rpc/sessions:respondToPermission", resourceType: "subFrame", method: "POST" },
            { url: "http://[::1]:3221/rpc/x", resourceType: "xhr", method: "POST" },
            { url: "http://127.0.0.1:3220/x.png", resourceType: "image", method: "GET" },
            { url: "ws://127.0.0.1:3220/events", resourceType: "webSocket", method: "GET" },
            // The hop after a redirect from an outside page arrives as its own request.
            { url: "http://rebind.example:3220/rpc/x", resourceType: "mainFrame", method: "GET" },
        ];
        for (const details of requests) {
            expect(await decide(listener, details), details.url).toEqual({ cancel: true });
        }
    });

    it("fix round 3: on the apps server only the host API (/_rowboat/...) is cancelled, app pages load", async () => {
        const ses = fakeSession();
        installOwnServerBlock(ses, deps());
        const [listener] = ses.listeners;
        for (const url of [
            "http://demo.apps.localhost:3210/_rowboat/fetch",
            "http://demo.apps.localhost:3210/_rowboat/data/x.json",
            "http://demo.apps.localhost:3210/_rowboat/events",
            "http://demo.apps.localhost:3210/_rowboat",
            "http://demo.apps.localhost:3210/%5Frowboat/fetch",
            "http://demo.apps.localhost:3210/_ROWBOAT/fetch",
            "http://demo.apps.localhost:3210//_rowboat/fetch",
            "http://127.0.0.1:3210/_rowboat/copilot/run",
            "http://[::1]:3210/_rowboat/tools/execute",
        ]) {
            expect(await decide(listener, { url, resourceType: "xhr" }), url).toEqual({ cancel: true });
        }
        for (const url of [
            "http://demo.apps.localhost:3210/",
            "http://demo.apps.localhost:3210/index.html",
            "http://demo.apps.localhost:3210/assets/app.js",
            "http://demo.apps.localhost:3210/?__rowboat_embed=1",
        ]) {
            expect(await decide(listener, { url, resourceType: "mainFrame" }), url).toEqual({});
        }
    });

    it("lets every other request through", async () => {
        const ses = fakeSession();
        installOwnServerBlock(ses, deps());
        const [listener] = ses.listeners;
        for (const url of ["https://example.com/", "http://127.0.0.1:5173/", "data:text/html,hi", "http://example.com:3220/"]) {
            expect(await decide(listener, { url, resourceType: "mainFrame" }), url).toEqual({});
        }
    });

    it("cancels when the decision itself fails (fails closed)", async () => {
        const ses = fakeSession();
        installOwnServerBlock(ses, deps({ ports: async () => { throw new Error("boom"); } }));
        const [listener] = ses.listeners;
        expect(await decide(listener, { url: "https://example.com/", resourceType: "mainFrame" })).toEqual({ cancel: true });
        expect(await decide(listener, { url: "not a url", resourceType: "other" })).toEqual({ cancel: true });
    });
});
