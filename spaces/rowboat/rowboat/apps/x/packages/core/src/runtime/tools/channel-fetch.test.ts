// Modified for VibeMind (Flissel/rowboat vibemind): tests for channel-run fetch-url (Teil E, fix round 1).
import { describe, expect, it, vi } from "vitest";
import { channelFetch, isPrivateAddress, type ChannelFetchDeps } from "./channel-fetch.js";

function response(status: number, body = "", headers: Record<string, string> = {}): Response {
    return new Response(status === 204 || (status >= 300 && status < 400) ? null : body, { status, headers });
}

function deps(
    dnsTable: Record<string, string[]>,
    responses: Array<Response> = [],
): ChannelFetchDeps & { fetch: ReturnType<typeof vi.fn> } {
    const queue = [...responses];
    return {
        lookup: async (host: string) => {
            const hit = dnsTable[host];
            if (!hit) throw new Error(`ENOTFOUND ${host}`);
            return hit;
        },
        fetch: vi.fn(async () => {
            const next = queue.shift();
            if (!next) throw new Error("no scripted response");
            return next;
        }),
    };
}

describe("isPrivateAddress", () => {
    it("flags loopback, RFC1918, link-local, CGNAT, ULA, Tailscale, mapped and unspecified", () => {
        for (const ip of [
            "127.0.0.1",
            "10.1.2.3",
            "172.16.0.1",
            "172.31.255.255",
            "192.168.1.1",
            "169.254.169.254",
            "100.64.0.1",
            "100.127.255.254",
            "0.0.0.0",
            "::1",
            "::",
            "fe80::1",
            "fc00::1",
            "fd7a:115c:a1e0::1",
            "::ffff:127.0.0.1",
            "::ffff:7f00:1",
            "::ffff:10.0.0.1",
        ]) {
            expect(isPrivateAddress(ip), ip).toBe(true);
        }
    });

    it("lets public addresses through", () => {
        for (const ip of ["93.184.216.34", "1.1.1.1", "172.32.0.1", "100.128.0.1", "2606:4700:4700::1111"]) {
            expect(isPrivateAddress(ip), ip).toBe(false);
        }
    });
});

describe("channelFetch", () => {
    it("refuses internal host names without resolving them", async () => {
        for (const url of [
            "http://localhost:8080/",
            "http://LOCALHOST./x",
            "http://printer.local/",
            "http://svc.internal/",
            "https://pc.tailnet-x.ts.net/",
            "http://127.0.0.1/",
            "http://[::1]/",
            "http://[::ffff:127.0.0.1]/",
            "http://2130706433/",
        ]) {
            const d = deps({});
            const result = await channelFetch(url, "GET", d);
            expect(result, url).toMatchObject({ ok: false, error: expect.stringContaining("not allowed") });
            expect(d.fetch).not.toHaveBeenCalled();
        }
    });

    it("refuses a public name that resolves to any private address", async () => {
        const d = deps({ "evil.example": ["93.184.216.34", "10.0.0.5"] });
        const result = await channelFetch("https://evil.example/", "GET", d);
        expect(result).toMatchObject({ ok: false });
        expect(d.fetch).not.toHaveBeenCalled();
    });

    it("refuses when the name does not resolve", async () => {
        const d = deps({});
        await expect(channelFetch("https://nowhere.example/", "GET", d)).resolves.toMatchObject({ ok: false });
    });

    it("fetches public hosts with redirect: manual and no extra headers", async () => {
        const d = deps({ "docs.example": ["93.184.216.34"] }, [response(200, "hello")]);
        const result = await channelFetch("https://docs.example/a", "GET", d);
        expect(result).toMatchObject({ ok: true, status: 200, body: "hello" });
        expect(d.fetch).toHaveBeenCalledWith("https://docs.example/a", {
            method: "GET",
            redirect: "manual",
            signal: expect.any(AbortSignal),
        });
    });

    it("re-checks every redirect hop", async () => {
        const d = deps(
            { "docs.example": ["93.184.216.34"], "cdn.example": ["1.1.1.1"] },
            [
                response(302, "", { location: "https://cdn.example/b" }),
                response(301, "", { location: "http://169.254.169.254/latest/meta-data" }),
            ],
        );
        const result = await channelFetch("https://docs.example/a", "GET", d);
        expect(result).toMatchObject({ ok: false, error: expect.stringContaining("169.254.169.254") });
        expect(d.fetch).toHaveBeenCalledTimes(2);
    });

    it("follows public redirects up to 5 hops", async () => {
        const hop = (n: number) => response(302, "", { location: `/hop${n}` });
        const ok = deps({ "docs.example": ["93.184.216.34"] }, [hop(1), hop(2), hop(3), hop(4), hop(5), response(200, "done")]);
        await expect(channelFetch("https://docs.example/", "GET", ok)).resolves.toMatchObject({ ok: true, body: "done" });

        const tooMany = deps({ "docs.example": ["93.184.216.34"] }, [hop(1), hop(2), hop(3), hop(4), hop(5), hop(6), response(200, "x")]);
        await expect(channelFetch("https://docs.example/", "GET", tooMany)).resolves.toMatchObject({
            ok: false,
            error: expect.stringContaining("redirects"),
        });
    });

    it("round 2: aborts after the timeout", async () => {
        const d: ChannelFetchDeps = {
            lookup: async () => ["93.184.216.34"],
            fetch: (_url, init) =>
                new Promise<Response>((_resolve, reject) => {
                    init.signal.addEventListener("abort", () => reject(init.signal.reason), { once: true });
                }),
            timeoutMs: 30,
        };
        await expect(channelFetch("https://slow.example/", "GET", d)).resolves.toMatchObject({
            ok: false,
            error: expect.stringContaining("timed out"),
        });
    });

    it("round 2: stops reading the body at the truncation limit", async () => {
        let pulled = 0;
        const chunk = "x".repeat(50_000);
        const stream = new ReadableStream<Uint8Array>({
            pull(controller) {
                pulled++;
                if (pulled > 100) {
                    controller.close();
                    return;
                }
                controller.enqueue(new TextEncoder().encode(chunk));
            },
        });
        const d = deps({ "big.example": ["93.184.216.34"] }, [new Response(stream, { status: 200 })]);
        const result = await channelFetch("https://big.example/", "GET", d);
        expect(result).toMatchObject({ ok: true, truncated: true });
        expect((result as { body: string }).body.length).toBe(200_000);
        expect(pulled).toBeLessThan(10);
        expect(d.fetch.mock.calls[0][1].signal).toBeInstanceOf(AbortSignal);
    });

    it("refuses non-http(s) URLs and non-GET/HEAD methods", async () => {
        await expect(channelFetch("file:///etc/passwd", "GET", deps({}))).resolves.toMatchObject({ ok: false });
        await expect(channelFetch("https://docs.example/", "POST", deps({ "docs.example": ["1.1.1.1"] }))).resolves.toMatchObject({ ok: false });
    });
});
