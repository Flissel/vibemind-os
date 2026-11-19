// Modified for VibeMind (Flissel/rowboat vibemind): fetch-url never reaches Rowboat's own server (ticket #330, fix round 1, review C1).
// No network: fetch is a stub everywhere (global stub for the execTool path), DNS is a stub.
import fsSync from "node:fs";
import fs from "node:fs/promises";
import http from "node:http";
import type { AddressInfo } from "node:net";
import os from "node:os";
import zlib from "node:zlib";
import path from "node:path";
import { afterAll, afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const root = fsSync.realpathSync(fsSync.mkdtempSync(path.join(os.tmpdir(), "own-server-ws-")));
process.env.ROWBOAT_WORKDIR = root;

vi.mock("../../knowledge/version_history.js", () => ({
    commitAll: async () => undefined,
    initRepo: async () => undefined,
}));

const { connectGuardedFetch, guardedFetch, ownServerRefusal } = await import("./own-server-guard.js");
const { execTool } = await import("./exec-tool.js");
const { InMemoryAbortRegistry } = await import("../turns/abort-registry.js");
type Ctx = NonNullable<Parameters<typeof execTool>[2]>;

type Init = { method: string; headers?: Record<string, string>; body?: string; redirect: "manual"; signal?: AbortSignal };

function stubFetch(responder: (url: string, init: Init) => Response = () => new Response("ok", { status: 200 })) {
    const calls: Array<{ url: string; init: Init }> = [];
    const fetch = async (url: string, init: Init) => {
        calls.push({ url, init });
        return responder(url, init);
    };
    return { calls, fetch };
}

const noDns = async (host: string): Promise<string[]> => {
    throw new Error(`unexpected DNS lookup for ${host}`);
};

const RPC_TARGETS = [
    "http://127.0.0.1:3220/rpc/workspace:writeFile",
    "http://localhost:3220/rpc/sessions:respondToPermission",
    "http://[::1]:3220/rpc/workspace:writeFile",
    "http://127.1.2.3:3220/rpc/workspace:writeFile",
    "http://0.0.0.0:3220/rpc/workspace:writeFile",
    "http://[::ffff:127.0.0.1]:3220/rpc/workspace:writeFile",
    "http://2130706433:3220/rpc/workspace:writeFile",
    "http://LOCALHOST.:3220/rpc/workspace:writeFile",
    "http://app.localhost:3220/rpc/workspace:writeFile",
    // The port fallback of the server (3220..3229).
    "http://127.0.0.1:3225/rpc/workspace:writeFile",
];

afterAll(async () => {
    await fs.rm(root, { recursive: true, force: true });
});

describe("ownServerRefusal", () => {
    it("refuses every loopback form of the own server's port", async () => {
        for (const target of RPC_TARGETS) {
            expect(await ownServerRefusal(new URL(target), { lookup: noDns }), target).toMatch(/own (local )?server/i);
        }
    });

    it("refuses the machine's own host name and its interface addresses", async () => {
        expect(await ownServerRefusal(new URL(`http://${os.hostname()}:3220/rpc/x`), { lookup: noDns })).toMatch(/own/i);
        const ownAddress = Object.values(os.networkInterfaces())
            .flat()
            .find((info) => info && info.family === "IPv4" && !info.internal)?.address;
        if (ownAddress) {
            expect(await ownServerRefusal(new URL(`http://${ownAddress}:3220/rpc/x`), { lookup: noDns })).toMatch(/own/i);
        }
    });

    it("refuses a name that resolves to loopback", async () => {
        const lookup = async () => ["127.0.0.1"];
        expect(await ownServerRefusal(new URL("http://rebind.example:3220/rpc/x"), { lookup })).toMatch(/own/i);
    });

    it("uses the port from config/server.json and ROWBOAT_SERVER_PORT", async () => {
        await fs.mkdir(path.join(root, "config"), { recursive: true });
        await fs.writeFile(path.join(root, "config", "server.json"), JSON.stringify({ port: 4555 }));
        process.env.ROWBOAT_SERVER_PORT = "4777";
        try {
            expect(await ownServerRefusal(new URL("http://127.0.0.1:4555/rpc/x"), { lookup: noDns })).toMatch(/own/i);
            expect(await ownServerRefusal(new URL("http://127.0.0.1:4777/rpc/x"), { lookup: noDns })).toMatch(/own/i);
        } finally {
            delete process.env.ROWBOAT_SERVER_PORT;
            await fs.rm(path.join(root, "config", "server.json"), { force: true });
        }
    });

    it("leaves other loopback ports and other hosts alone (ticket #325 covers them)", async () => {
        const lookup = async () => ["93.184.215.14"];
        expect(await ownServerRefusal(new URL("http://127.0.0.1:8080/x"), { lookup: noDns })).toBeNull();
        expect(await ownServerRefusal(new URL("http://localhost:3211/x"), { lookup: noDns })).toBeNull();
        expect(await ownServerRefusal(new URL("https://example.com/x"), { lookup })).toBeNull();
        expect(await ownServerRefusal(new URL("http://example.com:3220/x"), { lookup })).toBeNull();
    });

    it("fix round 3 (review I-A): refuses the apps server and its host API (the fetch proxy relay)", async () => {
        for (const target of [
            "http://127.0.0.1:3210/_rowboat/fetch?url=http://127.0.0.1:3220/rpc/workspace:writeFile",
            "http://localhost:3210/_rowboat/fetch",
            "http://demo.apps.localhost:3210/_rowboat/fetch",
            "http://[::1]:3210/",
        ]) {
            expect(await ownServerRefusal(new URL(target), { lookup: noDns }), target).toMatch(/own/i);
        }
    });

    it("fix round 3: fetch-url POST to 127.0.0.1:3210/_rowboat/fetch is refused before any request", async () => {
        const stub = stubFetch();
        const result = await guardedFetch(
            "http://127.0.0.1:3210/_rowboat/fetch",
            "POST",
            { "X-Rowboat-App": "1", "Content-Type": "application/json" },
            JSON.stringify({ url: "http://127.0.0.1:3220/rpc/workspace:writeFile", method: "POST" }),
            { fetch: stub.fetch, lookup: noDns },
        );
        expect(result).toMatchObject({ ok: false, status: 0 });
        expect(String((result as { error?: string }).error)).toMatch(/own/i);
        expect(stub.calls).toEqual([]);
    });
});

describe("guardedFetch", () => {
    it("refuses the own server before any request, for every method", async () => {
        for (const method of ["GET", "POST"]) {
            for (const target of RPC_TARGETS) {
                const stub = stubFetch();
                const result = await guardedFetch(target, method, { Authorization: "Bearer x" }, "{}", { fetch: stub.fetch, lookup: noDns });
                expect(result, target).toMatchObject({ ok: false, status: 0 });
                expect(stub.calls, target).toEqual([]);
            }
        }
    });

    it("refuses a redirect to the own server", async () => {
        const stub = stubFetch((url) =>
            url.startsWith("http://127.0.0.1:8080")
                ? new Response(null, { status: 307, headers: { location: "http://127.0.0.1:3220/rpc/workspace:writeFile" } })
                : new Response("should not happen"),
        );
        const result = await guardedFetch("http://127.0.0.1:8080/x", "POST", undefined, "{}", { fetch: stub.fetch, lookup: noDns });
        expect(result).toMatchObject({ ok: false, status: 0 });
        expect(String((result as { error?: string }).error)).toMatch(/own/i);
        expect(stub.calls.map((c) => c.url)).toEqual(["http://127.0.0.1:8080/x"]);
    });

    it("follows ordinary redirects like fetch did (303 becomes GET, cross-origin drops Authorization)", async () => {
        const stub = stubFetch((url) => {
            if (url === "http://127.0.0.1:8080/a") {
                return new Response(null, { status: 303, headers: { location: "http://127.0.0.1:8081/b" } });
            }
            return new Response("done", { status: 200 });
        });
        const result = await guardedFetch("http://127.0.0.1:8080/a", "POST", { Authorization: "Bearer x", "X-Other": "1" }, "{}", {
            fetch: stub.fetch,
            lookup: noDns,
        });
        expect(result).toMatchObject({ ok: true, status: 200, body: "done" });
        expect(stub.calls[1].init.method).toBe("GET");
        expect(stub.calls[1].init.body).toBeUndefined();
        expect(Object.keys(stub.calls[1].init.headers ?? {}).map((h) => h.toLowerCase())).toEqual(["x-other"]);
    });

    it("fix round 3: the shared redirect rules (credential-like headers cross-origin, body headers on a switch to GET)", async () => {
        const stub = stubFetch((url) => {
            if (url === "http://127.0.0.1:8080/a") {
                return new Response(null, { status: 302, headers: { location: "http://127.0.0.1:8081/b" } });
            }
            return new Response("done", { status: 200 });
        });
        const result = await guardedFetch(
            "http://127.0.0.1:8080/a",
            "POST",
            { "X-Api-Key": "k", "X-Auth-Token": "t", Cookie: "a=b", "Content-Type": "application/json", "X-Other": "1" },
            "{}",
            { fetch: stub.fetch, lookup: noDns },
        );
        expect(result).toMatchObject({ ok: true, body: "done" });
        expect(stub.calls[1].init.method).toBe("GET");
        expect(stub.calls[1].init.body).toBeUndefined();
        expect(Object.keys(stub.calls[1].init.headers ?? {}).map((h) => h.toLowerCase())).toEqual(["x-other"]);
    });

    it("passes an ordinary request through unchanged", async () => {
        const stub = stubFetch(() => new Response("{\"a\":1}", { status: 200 }));
        const result = await guardedFetch("http://127.0.0.1:8080/api", "POST", { "Content-Type": "application/json" }, "{}", {
            fetch: stub.fetch,
            lookup: noDns,
        });
        expect(result).toMatchObject({ ok: true, status: 200, body: "{\"a\":1}", truncated: false });
        expect(stub.calls[0].init).toMatchObject({ method: "POST", body: "{}", headers: { "Content-Type": "application/json" } });
    });
});

describe("fetch-url through execTool (global fetch stubbed)", () => {
    const spy = vi.fn(async () => new Response("ok", { status: 200 }));
    beforeEach(() => {
        spy.mockClear();
        vi.stubGlobal("fetch", spy);
    });
    afterEach(() => {
        vi.unstubAllGlobals();
    });

    const ctx = (extra: Partial<Ctx> = {}): Ctx => ({
        runId: "t1",
        toolCallId: "c1",
        signal: new AbortController().signal,
        abortRegistry: new InMemoryAbortRegistry(),
        publish: async () => undefined,
        ...extra,
    });
    const fetchUrl = (input: Record<string, unknown>, extra: Partial<Ctx> = {}) =>
        execTool({ type: "builtin", name: "fetch-url" }, input, ctx(extra)) as Promise<{ ok: boolean; error?: string }>;

    it("POST to the own /rpc is refused in a desktop run, even marked human-approved", async () => {
        for (const url of [
            "http://127.0.0.1:3220/rpc/workspace:writeFile",
            "http://localhost:3220/rpc/sessions:respondToPermission",
            "http://[::1]:3220/rpc/workspace:writeFile",
        ]) {
            const result = await fetchUrl({ url, method: "POST", headers: { Authorization: "Bearer x" }, body: "{}" }, { humanApproved: true });
            expect(result, url).toMatchObject({ ok: false });
            expect(result.error, url).toMatch(/own/i);
        }
        expect(spy).not.toHaveBeenCalled();
    });

    it("is refused in a channel run as well", async () => {
        const result = await fetchUrl({ url: "http://127.0.0.1:3220/rpc/workspace:writeFile" }, { channel: { readOnlyMcpTools: [] } });
        expect(result).toMatchObject({ ok: false });
        expect(spy).not.toHaveBeenCalled();
    });

    it("another loopback port is unchanged (served by a local test server, not the global fetch)", async () => {
        const local = await startServer((_req, res) => res.end("local"));
        try {
            const result = await fetchUrl({ url: `http://127.0.0.1:${local.port}/api` });
            expect(result).toMatchObject({ ok: true, body: "local" });
            expect(local.hits).toHaveLength(1);
            expect(spy).not.toHaveBeenCalled();
        } finally {
            await local.close();
        }
    });
});

// A loopback HTTP server on an OS-chosen port; it records every request it gets.
async function startServer(handler: (req: http.IncomingMessage, res: http.ServerResponse) => void) {
    const hits: string[] = [];
    const server = http.createServer((req, res) => {
        hits.push(`${req.method} ${req.url}`);
        handler(req, res);
    });
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    const port = (server.address() as AddressInfo).port;
    return {
        port,
        hits,
        close: () =>
            new Promise<void>((resolve) => {
                server.close(() => resolve());
                server.closeAllConnections();
            }),
    };
}

// Fix round 2 (DNS rebinding): the address fetch-url really connects to is
// checked, on every hop. Only loopback test servers; the ".test" names cannot
// resolve through the system DNS, so a transport that ignored the injected
// lookup would fail instead of going anywhere.
describe("guardedFetch connect-time check (no stubbed transport)", () => {
    it("a name that resolves public for the check and to 127.0.0.1 at connect never reaches the own-server port", async () => {
        const own = await startServer((_req, res) => res.end("own server reached"));
        try {
            const answers: string[][] = [["93.184.216.34"], ["127.0.0.1"], ["127.0.0.1"]];
            const lookups: string[] = [];
            const lookup = async (host: string) => {
                lookups.push(host);
                return answers.shift() ?? ["127.0.0.1"];
            };
            const deps = { lookup, ports: async () => new Set([own.port]), interfaces: () => [], hostname: () => "testhost" };
            const result = await guardedFetch(`http://rebind.test:${own.port}/rpc/workspace:writeFile`, "POST", { "Content-Type": "application/json" }, "{}", deps);
            expect(result).toMatchObject({ ok: false, status: 0 });
            expect(String((result as { error?: string }).error)).toMatch(/own/i);
            expect(own.hits).toEqual([]);
            // The pre-check and the connect each resolved the name.
            expect(lookups.length).toBeGreaterThanOrEqual(2);
        } finally {
            await own.close();
        }
    });

    it("checks the connect address on a redirect hop too", async () => {
        const own = await startServer((_req, res) => res.end("own server reached"));
        const outside = await startServer((_req, res) => {
            res.writeHead(307, { location: `http://rebind.test:${own.port}/rpc/workspace:writeFile` });
            res.end();
        });
        try {
            let rebindCalls = 0;
            const lookup = async (host: string) => {
                if (host === "rebind.test") return rebindCalls++ === 0 ? ["93.184.216.34"] : ["127.0.0.1"];
                return ["127.0.0.1"];
            };
            const deps = { lookup, ports: async () => new Set([own.port]), interfaces: () => [], hostname: () => "testhost" };
            const result = await guardedFetch(`http://plain.test:${outside.port}/start`, "GET", undefined, undefined, deps);
            expect(result).toMatchObject({ ok: false, status: 0 });
            expect(String((result as { error?: string }).error)).toMatch(/own/i);
            expect(outside.hits).toEqual(["GET /start"]);
            expect(own.hits).toEqual([]);
        } finally {
            await own.close();
            await outside.close();
        }
    });

    it("connects through the checked address when it is not the own server (headers, body, gzip)", async () => {
        const server = await startServer((req, res) => {
            let body = "";
            req.on("data", (c) => (body += c));
            req.on("end", () => {
                res.writeHead(200, { "content-type": "text/plain", "content-encoding": "gzip" });
                res.end(zlib.gzipSync(`${req.headers["x-probe"]}:${req.headers["content-type"]}:${body}`));
            });
        });
        try {
            const deps = { lookup: async () => ["127.0.0.1"], ports: async () => new Set([1]), interfaces: () => [], hostname: () => "testhost" };
            const result = await guardedFetch(`http://plain.test:${server.port}/api`, "POST", { "X-Probe": "p" }, "hello", deps);
            expect(result).toMatchObject({ ok: true, status: 200, body: "p:text/plain;charset=UTF-8:hello" });
            expect(server.hits).toEqual(["POST /api"]);
        } finally {
            await server.close();
        }
    });
});

// Fix round 3 (review I-A, I-B, m2, m3): the transport against loopback test
// servers only. ".test" names resolve through the injected lookup.
const LOCAL = { lookup: async () => ["127.0.0.1"], ports: async () => new Set([1]), interfaces: () => [] as string[], hostname: () => "testhost" };

function closedWithin(ms: number, closed: Promise<void>): Promise<"closed" | "open"> {
    return Promise.race([closed.then(() => "closed" as const), new Promise<"open">((r) => setTimeout(() => r("open"), ms))]);
}

// A server that streams without end until the client goes away.
async function endlessServer(encoding: "identity" | "gzip") {
    let resolveClosed: () => void = () => undefined;
    const closed = new Promise<void>((r) => (resolveClosed = r));
    const server = await startServer((req, res) => {
        req.socket.once("close", () => resolveClosed());
        const chunk = Buffer.alloc(64 * 1024, "a");
        if (encoding === "gzip") {
            res.writeHead(200, { "content-type": "text/plain", "content-encoding": "gzip" });
            const gz = zlib.createGzip();
            gz.pipe(res);
            const timer = setInterval(() => {
                gz.write(chunk);
                gz.flush();
            }, 5);
            req.socket.once("close", () => clearInterval(timer));
        } else {
            res.writeHead(200, { "content-type": "text/plain" });
            const timer = setInterval(() => res.write(chunk), 5);
            req.socket.once("close", () => clearInterval(timer));
        }
    });
    return { ...server, closed };
}

describe("fix round 3: fetch-url transport", () => {
    it("never sends an agent-supplied Host, Origin or hop-by-hop header", async () => {
        let seen: http.IncomingHttpHeaders = {};
        const server = await startServer((req, res) => {
            seen = req.headers;
            res.end("ok");
        });
        try {
            const result = await guardedFetch(
                `http://plain.test:${server.port}/x`,
                "GET",
                {
                    Host: "demo.apps.localhost:3210",
                    Origin: "http://demo.apps.localhost:3210",
                    Connection: "keep-alive, x-secret",
                    "Keep-Alive": "timeout=5",
                    "Transfer-Encoding": "chunked",
                    Upgrade: "websocket",
                    TE: "trailers",
                    "Proxy-Authorization": "Basic eA==",
                    "X-Keep": "1",
                },
                undefined,
                LOCAL,
            );
            expect(result).toMatchObject({ ok: true, body: "ok" });
            expect(seen.host).toBe(`plain.test:${server.port}`);
            for (const name of ["origin", "keep-alive", "transfer-encoding", "upgrade", "te", "proxy-authorization", "x-secret"]) {
                expect(seen[name], name).toBeUndefined();
            }
            expect(String(seen.connection ?? "")).not.toMatch(/x-secret|keep-alive/i);
            expect(seen["x-keep"]).toBe("1");
        } finally {
            await server.close();
        }
    });

    it("returns a status outside 200-599 (999) with its body instead of throwing", async () => {
        const server = await startServer((_req, res) => {
            res.writeHead(999, "Request denied", { "content-type": "text/plain" });
            res.end("nope");
        });
        try {
            const result = await guardedFetch(`http://plain.test:${server.port}/x`, "GET", undefined, undefined, LOCAL);
            expect(result).toEqual({ ok: false, status: 999, statusText: "Request denied", body: "nope", truncated: false });
        } finally {
            await server.close();
        }
    });

    it.each([
        ["gzip", (b: Buffer) => zlib.gzipSync(b)],
        ["x-gzip", (b: Buffer) => zlib.gzipSync(b)],
        ["deflate", (b: Buffer) => zlib.deflateSync(b)],
        ["deflate (raw)", (b: Buffer) => zlib.deflateRawSync(b)],
        ["br", (b: Buffer) => zlib.brotliCompressSync(b)],
        ["gzip, br", (b: Buffer) => zlib.brotliCompressSync(zlib.gzipSync(b))],
        ["deflate,gzip", (b: Buffer) => zlib.gzipSync(zlib.deflateSync(b))],
        ["identity", (b: Buffer) => b],
    ] as Array<[string, (b: Buffer) => Buffer]>)("decodes Content-Encoding %s", async (label, encode) => {
        const header = label === "deflate (raw)" ? "deflate" : label;
        const text = "hello ".repeat(2000);
        const server = await startServer((_req, res) => {
            res.writeHead(200, { "content-type": "text/plain", "content-encoding": header });
            res.end(encode(Buffer.from(text)));
        });
        try {
            const result = await guardedFetch(`http://plain.test:${server.port}/x`, "GET", undefined, undefined, LOCAL);
            expect(result).toMatchObject({ ok: true, status: 200, body: text });
        } finally {
            await server.close();
        }
    });

    // Task 17 step 0 (review m-N1): an empty or truncated coded body gives the
    // status with what was decoded, as undici did (sync flush), not an error.
    it.each([
        ["gzip", 200],
        ["gzip", 201],
        ["deflate", 200],
        ["deflate", 201],
        ["br", 200],
        ["br", 201],
    ] as Array<[string, number]>)("an empty body labelled %s (status %i) returns the status and an empty body", async (coding, status) => {
        const server = await startServer((_req, res) => {
            res.writeHead(status, { "content-type": "text/plain", "content-encoding": coding });
            res.end();
        });
        try {
            const result = await guardedFetch(`http://plain.test:${server.port}/x`, "GET", undefined, undefined, LOCAL);
            expect(result).toMatchObject({ ok: true, status, body: "" });
        } finally {
            await server.close();
        }
    });

    it.each([
        ["gzip", (b: Buffer) => zlib.gzipSync(b)],
        ["deflate", (b: Buffer) => zlib.deflateSync(b)],
        ["br", (b: Buffer) => zlib.brotliCompressSync(b)],
    ] as Array<[string, (b: Buffer) => Buffer]>)("a %s body with a truncated trailer returns the decoded part", async (coding, encode) => {
        const full = encode(Buffer.from("hello world"));
        const server = await startServer((_req, res) => {
            res.writeHead(200, { "content-type": "text/plain", "content-encoding": coding });
            res.end(full.subarray(0, full.length - 4));
        });
        try {
            const result = await guardedFetch(`http://plain.test:${server.port}/x`, "GET", undefined, undefined, LOCAL);
            expect(result).toMatchObject({ ok: true, status: 200 });
            expect("hello world".startsWith((result as { body: string }).body)).toBe(true);
        } finally {
            await server.close();
        }
    });

    it("returns the raw body with the header intact for an unknown coding", async () => {
        for (const header of ["x-custom", "gzip, x-custom"]) {
            const server = await startServer((_req, res) => {
                res.writeHead(200, { "content-type": "text/plain", "content-encoding": header });
                res.end("raw-bytes");
            });
            try {
                const res = await connectGuardedFetch(LOCAL)(`http://plain.test:${server.port}/x`, { method: "GET", redirect: "manual" });
                expect(res.headers.get("content-encoding"), header).toBe(header);
                expect(await res.text(), header).toBe("raw-bytes");
            } finally {
                await server.close();
            }
        }
    });

    it.each(["identity", "gzip"] as const)("destroys the socket after the size limit (%s)", async (encoding) => {
        const server = await endlessServer(encoding);
        try {
            const result = await guardedFetch(`http://plain.test:${server.port}/x`, "GET", undefined, undefined, LOCAL);
            expect(result).toMatchObject({ ok: true, truncated: true });
            expect((result as { body: string }).body).toHaveLength(200_000);
            expect(await closedWithin(3000, server.closed)).toBe("closed");
        } finally {
            await server.close();
        }
    }, 15_000);

    it.each(["identity", "gzip"] as const)("destroys the socket when the body is cancelled (%s)", async (encoding) => {
        const server = await endlessServer(encoding);
        try {
            const res = await connectGuardedFetch(LOCAL)(`http://plain.test:${server.port}/x`, { method: "GET", redirect: "manual" });
            const reader = res.body!.getReader();
            await reader.read();
            await reader.cancel();
            expect(await closedWithin(3000, server.closed)).toBe("closed");
        } finally {
            await server.close();
        }
    }, 15_000);

    it.each(["identity", "gzip"] as const)("destroys the socket on abort (%s)", async (encoding) => {
        const server = await endlessServer(encoding);
        try {
            const controller = new AbortController();
            const res = await connectGuardedFetch(LOCAL)(`http://plain.test:${server.port}/x`, {
                method: "GET",
                redirect: "manual",
                signal: controller.signal,
            });
            const reader = res.body!.getReader();
            await reader.read();
            controller.abort();
            await reader.read().catch(() => undefined);
            expect(await closedWithin(3000, server.closed)).toBe("closed");
        } finally {
            await server.close();
        }
    }, 15_000);
});

describe("fix round 3: fetch-url through execTool drops a Host override", () => {
    it("sends the URL's host, not the agent's", async () => {
        let seenHost: string | undefined;
        const server = await startServer((req, res) => {
            seenHost = req.headers.host;
            res.end("ok");
        });
        try {
            const result = (await execTool(
                { type: "builtin", name: "fetch-url" },
                { url: `http://127.0.0.1:${server.port}/x`, headers: { Host: "demo.apps.localhost:3210" } },
                {
                    runId: "t1",
                    toolCallId: "c1",
                    signal: new AbortController().signal,
                    abortRegistry: new InMemoryAbortRegistry(),
                    publish: async () => undefined,
                },
            )) as { ok: boolean };
            expect(result).toMatchObject({ ok: true });
            expect(seenHost).toBe(`127.0.0.1:${server.port}`);
        } finally {
            await server.close();
        }
    });
});

// Fix round 3, coordinator correction: decoders are pipeline stages (deflate
// auto-detect included), the size limit applies to decompressed bytes, and a
// chain of more than 5 codings is returned raw without creating decoders.
describe("fix round 3: decompression limits", () => {
    it.each([
        ["gzip", () => zlib.createGzip()],
        ["deflate", () => zlib.createDeflate()],
        ["deflate (raw)", () => zlib.createDeflateRaw()],
    ] as Array<[string, () => zlib.Gzip | zlib.Deflate | zlib.DeflateRaw]>)(
        "a small %s body that expands past the limit is cut at the limit and the socket destroyed",
        async (label, encoder) => {
            let compressed = 0;
            let resolveClosed: () => void = () => undefined;
            const closed = new Promise<void>((r) => (resolveClosed = r));
            const server = await startServer((req, res) => {
                req.socket.once("close", () => resolveClosed());
                res.writeHead(200, { "content-type": "text/plain", "content-encoding": label.startsWith("deflate") ? "deflate" : "gzip" });
                const enc = encoder();
                enc.on("data", (c: Buffer) => (compressed += c.length));
                enc.pipe(res);
                enc.write(Buffer.alloc(5_000_000, "a"));
                enc.flush();
                // The response is never ended: only the client can close it.
            });
            try {
                const result = await guardedFetch(`http://plain.test:${server.port}/x`, "GET", undefined, undefined, LOCAL);
                expect(result).toMatchObject({ ok: true, truncated: true });
                expect((result as { body: string }).body).toHaveLength(200_000);
                expect(compressed).toBeLessThan(64 * 1024);
                expect(await closedWithin(3000, closed)).toBe("closed");
            } finally {
                await server.close();
            }
        },
        15_000,
    );

    it("a chain of 6 codings creates no decoders and returns the raw body; 5 are decoded", async () => {
        const spy = vi.spyOn(zlib, "createGunzip");
        try {
            const six = "gzip, gzip, gzip, gzip, gzip, gzip";
            let server = await startServer((_req, res) => {
                res.writeHead(200, { "content-type": "text/plain", "content-encoding": six });
                res.end("raw-bytes");
            });
            try {
                const res = await connectGuardedFetch(LOCAL)(`http://plain.test:${server.port}/x`, { method: "GET", redirect: "manual" });
                expect(res.headers.get("content-encoding")).toBe(six);
                expect(await res.text()).toBe("raw-bytes");
                expect(spy).not.toHaveBeenCalled();
            } finally {
                await server.close();
            }

            let body = Buffer.from("five");
            for (let i = 0; i < 5; i++) body = zlib.gzipSync(body);
            server = await startServer((_req, res) => {
                res.writeHead(200, { "content-type": "text/plain", "content-encoding": "gzip,gzip,gzip,gzip,gzip" });
                res.end(body);
            });
            try {
                const result = await guardedFetch(`http://plain.test:${server.port}/x`, "GET", undefined, undefined, LOCAL);
                expect(result).toMatchObject({ ok: true, body: "five" });
                expect(spy).toHaveBeenCalledTimes(5);
            } finally {
                await server.close();
            }
        } finally {
            spy.mockRestore();
        }
    });
});
