// Modified for VibeMind (Flissel/rowboat vibemind): the apps host API fetch
// proxy never relays to Rowboat's own server (ticket #330, fix round 3,
// review I-A). The real handler behind a loopback express app; upstreams are
// a stub transport or refused before any connect. No outside network.
import crypto from "node:crypto";
import fsSync from "node:fs";
import fs from "node:fs/promises";
import http from "node:http";
import type { AddressInfo } from "node:net";
import os from "node:os";
import path from "node:path";
import express from "express";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

const root = fsSync.realpathSync(fsSync.mkdtempSync(path.join(os.tmpdir(), "fetch-proxy-ws-")));
process.env.ROWBOAT_WORKDIR = root;

const { handleFetchProxy } = await import("./fetch-proxy.js");
type Deps = NonNullable<Parameters<typeof handleFetchProxy>[4]>;
type Manifest = Parameters<typeof handleFetchProxy>[1];

const SERVER_KEY = "server-key-for-the-test-0123456789abcdef";
const SCOPED_TOKEN = "scoped-token-for-the-test-ABCDEFGHIJKLMNOP";
const OWN_INTERFACES = ["127.0.0.1", "::1", "192.168.1.50", "100.101.102.103", "203.0.113.7"];

type Call = { url: string; method: string; headers: Record<string, string>; body?: string };

function stubTransport(calls: Call[]): Deps["transport"] {
    return async (url, init) => {
        calls.push({ url, method: init.method, headers: { ...(init.headers ?? {}) }, body: init.body });
        return new Response("hello", { status: 200, statusText: "OK" });
    };
}

let base = "";
let currentDeps: Deps;
let close: () => Promise<void> = async () => undefined;

beforeAll(async () => {
    await fs.writeFile(path.join(root, "server-key"), `${SERVER_KEY}\n`);
    await fs.writeFile(
        path.join(root, "scoped-keys.json"),
        JSON.stringify({ keys: [{ name: "t", sha256: crypto.createHash("sha256").update(SCOPED_TOKEN).digest("hex"), channels: ["todo:get"] }] }),
    );
    const app = express();
    app.post("/f", (req, res) => {
        void handleFetchProxy("demo", {} as unknown as Manifest, req, res, currentDeps);
    });
    const server = await new Promise<ReturnType<typeof app.listen>>((resolve) => {
        const s = app.listen(0, "127.0.0.1", () => resolve(s));
    });
    base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
    close = () => new Promise<void>((resolve) => server.close(() => resolve()));
});

afterAll(async () => {
    await close();
    await fs.rm(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
});

async function proxy(body: Record<string, unknown>, deps: Deps): Promise<{ status: number; json: Record<string, unknown> }> {
    currentDeps = deps;
    const res = await fetch(`${base}/f`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    return { status: res.status, json: (await res.json()) as Record<string, unknown> };
}

const lookupTable = (table: Record<string, string[]>) => async (host: string) => {
    const hit = table[host];
    if (!hit) throw new Error(`ENOTFOUND ${host}`);
    return hit;
};

describe("apps fetch proxy (fix round 3, review I-A)", () => {
    it("refuses the own server via loopback, LAN, Tailscale (100.64/10) and any own interface address", async () => {
        const calls: Call[] = [];
        const deps: Deps = {
            lookup: lookupTable({ "tailnet.test": ["100.101.102.103"], "me.test": ["203.0.113.7"] }),
            interfaces: () => OWN_INTERFACES,
            transport: stubTransport(calls),
        };
        for (const url of [
            "http://127.0.0.1:3220/rpc/workspace:writeFile",
            "http://localhost:3220/rpc/workspace:writeFile",
            "http://[::1]:3220/rpc/workspace:writeFile",
            "http://[::ffff:7f00:1]:3220/rpc/workspace:writeFile",
            "http://192.168.1.50:3220/rpc/workspace:writeFile",
            "http://[::ffff:c0a8:132]:3220/rpc/workspace:writeFile",
            "http://100.101.102.103:3220/rpc/workspace:writeFile",
            "http://100.64.0.1:3220/rpc/workspace:writeFile",
            "http://203.0.113.7:3220/rpc/workspace:writeFile",
            "http://tailnet.test:3220/rpc/workspace:writeFile",
            "http://me.test:3220/rpc/workspace:writeFile",
            "http://127.0.0.1:3210/_rowboat/fetch",
        ]) {
            const { status, json } = await proxy({ url, method: "POST", body: "{}" }, deps);
            expect(status, url).toBe(403);
            expect((json.error as { code: string }).code, url).toBe("address_forbidden");
        }
        expect(calls).toEqual([]);
    });

    // Task 17 step 0 (review m-N2): a non-own port, so the pre-check does one
    // lookup (ssrfCheck's) and the flip lands on the transport's lookup. The
    // flipped address is a loopback test server: without the transport's own
    // check the request would reach it (hits > 0, status 200) — no outside
    // network either way.
    it("checks the address it connects to (a name that turns into loopback after the check)", async () => {
        let hits = 0;
        const target = http.createServer((_req, res) => {
            hits++;
            res.end("reached");
        });
        await new Promise<void>((resolve) => target.listen(0, "127.0.0.1", () => resolve()));
        const port = (target.address() as AddressInfo).port;
        try {
            let n = 0;
            const deps: Deps = {
                lookup: async (host) => {
                    if (host !== "rebind.test") throw new Error(`ENOTFOUND ${host}`);
                    return n++ === 0 ? ["93.184.216.34"] : ["127.0.0.1"];
                },
                interfaces: () => OWN_INTERFACES,
            };
            const { status, json } = await proxy({ url: `http://rebind.test:${port}/x`, method: "GET" }, deps);
            expect(hits).toBe(0);
            expect(status).toBe(403);
            expect((json.error as { code: string; message: string }).code).toBe("address_forbidden");
            expect((json.error as { code: string; message: string }).message).toContain("connects to forbidden address");
            expect(n).toBe(2);
        } finally {
            await new Promise<void>((resolve) => target.close(() => resolve()));
        }
    });

    it("does not pass a key that authenticates against the own server", async () => {
        const calls: Call[] = [];
        const deps: Deps = { lookup: lookupTable({ "public.test": ["93.184.216.34"] }), interfaces: () => OWN_INTERFACES, transport: stubTransport(calls) };
        for (const request of [
            { url: "http://public.test/x", headers: { Authorization: `Bearer ${SERVER_KEY}` } },
            { url: "http://public.test/x", headers: { authorization: `bearer ${SCOPED_TOKEN}` } },
            { url: "http://public.test/x", headers: { "X-Api-Key": SERVER_KEY } },
            { url: `http://public.test/x?token=${SERVER_KEY}` },
        ]) {
            const { status, json } = await proxy({ ...request, method: "GET" }, deps);
            expect(status, JSON.stringify(request)).toBe(403);
            expect((json.error as { code: string }).code).toBe("credential_forbidden");
        }
        expect(calls).toEqual([]);
    });

    it("follows redirects with fetch-url's rules: cross-origin drops credentials, 303 after POST becomes GET without body", async () => {
        const calls: Call[] = [];
        const replies: Response[] = [
            new Response(null, { status: 302, headers: { location: "https://other.test/landing" } }),
            new Response("cross", { status: 200 }),
        ];
        const deps: Deps = {
            lookup: lookupTable({ "public.test": ["93.184.216.34"], "other.test": ["93.184.216.35"] }),
            interfaces: () => OWN_INTERFACES,
            transport: async (url, init) => {
                calls.push({ url, method: init.method, headers: { ...(init.headers ?? {}) }, body: init.body });
                return replies.shift() ?? new Response("done", { status: 200 });
            },
        };
        const cross = await proxy(
            {
                url: "https://public.test/start",
                method: "GET",
                headers: { Authorization: "Bearer someone-elses-token", "X-Api-Key": "k", "X-Other": "1" },
            },
            deps,
        );
        expect(cross).toMatchObject({ status: 200, json: { text: "cross" } });
        expect(calls.map((c) => c.url)).toEqual(["https://public.test/start", "https://other.test/landing"]);
        expect(Object.keys(calls[1].headers).map((h) => h.toLowerCase())).toEqual(["x-other"]);

        calls.length = 0;
        replies.push(new Response(null, { status: 303, headers: { location: "/done" } }), new Response("after", { status: 200 }));
        const seeOther = await proxy(
            { url: "https://public.test/form", method: "POST", body: "{\"a\":1}", headers: { "Content-Type": "application/json", Authorization: "Bearer x" } },
            deps,
        );
        expect(seeOther).toMatchObject({ status: 200, json: { text: "after" } });
        expect(calls[1]).toMatchObject({ url: "https://public.test/done", method: "GET" });
        expect(calls[1].body).toBeUndefined();
        expect(Object.keys(calls[1].headers).map((h) => h.toLowerCase())).toEqual(["authorization"]);
    });

    it("forwards to a public host unchanged (minus Host, Origin, Cookie and hop-by-hop headers)", async () => {
        const calls: Call[] = [];
        const deps: Deps = { lookup: lookupTable({ "public.test": ["93.184.216.34"] }), interfaces: () => OWN_INTERFACES, transport: stubTransport(calls) };
        const { status, json } = await proxy(
            {
                url: "https://public.test/api?q=1",
                method: "POST",
                body: "{\"a\":1}",
                headers: {
                    Authorization: "Bearer someone-elses-token",
                    "Content-Type": "application/json",
                    "X-Custom": "1",
                    Host: "evil.test",
                    Origin: "http://evil.test",
                    Cookie: "a=b",
                    Connection: "upgrade",
                },
            },
            deps,
        );
        expect(status).toBe(200);
        expect(json).toEqual({ ok: true, status: 200, statusText: "OK", text: "hello", truncated: false });
        expect(calls).toHaveLength(1);
        expect(calls[0]).toMatchObject({ url: "https://public.test/api?q=1", method: "POST", body: "{\"a\":1}" });
        expect(Object.keys(calls[0].headers).map((h) => h.toLowerCase()).sort()).toEqual(["authorization", "content-type", "x-custom"]);
    });
});
