// Modified for VibeMind (Flissel/rowboat vibemind): new module — fetch-url for
// channel-origin turns (Teil E, fix round 1). GET/HEAD only, no custom
// headers, no internal hosts (names, literals and every DNS answer are
// checked), redirects followed manually with the same check per hop, a 20 s
// deadline for the whole chain, body reading stops at the truncation limit.
// Residual: the address is resolved again by fetch itself (DNS rebinding
// between the check and the connect is not prevented).

import dns from "node:dns/promises";
import net from "node:net";

export interface ChannelFetchDeps {
    // All addresses a host name resolves to.
    lookup: (host: string) => Promise<string[]>;
    fetch: (
        url: string,
        init: { method: string; redirect: "manual"; signal: AbortSignal },
    ) => Promise<Response>;
    // Deadline for the whole request chain (default 20 s).
    timeoutMs?: number;
}

const DEFAULT_TIMEOUT_MS = 20_000;

export const defaultChannelFetchDeps: ChannelFetchDeps = {
    lookup: async (host) =>
        (await dns.lookup(host, { all: true, verbatim: true })).map((a) => a.address),
    fetch: (url, init) => fetch(url, init),
};

const MAX_REDIRECTS = 5;
const MAX_BODY = 200_000;

type FetchResult =
    | { ok: boolean; status: number; statusText: string; body: string; truncated: boolean }
    | { ok: false; status: 0; error: string };

function parseV4(ip: string): number[] | null {
    const parts = ip.split(".");
    if (parts.length !== 4) return null;
    const octets = parts.map((p) => (/^\d{1,3}$/.test(p) ? Number(p) : NaN));
    return octets.every((o) => Number.isInteger(o) && o >= 0 && o <= 255) ? octets : null;
}

function privateV4([a, b]: number[]): boolean {
    return (
        a === 0 || // "this" network, 0.0.0.0
        a === 10 ||
        a === 127 ||
        (a === 169 && b === 254) ||
        (a === 172 && b >= 16 && b <= 31) ||
        (a === 192 && b === 168) ||
        (a === 100 && b >= 64 && b <= 127) || // CGNAT / Tailscale 100.64.0.0/10
        a >= 224 // multicast, reserved, broadcast
    );
}

function expandV6(input: string): number[] | null {
    let s = input.toLowerCase();
    const zone = s.indexOf("%");
    if (zone >= 0) s = s.slice(0, zone);
    let tail: number[] = [];
    const v4 = /(\d+\.\d+\.\d+\.\d+)$/.exec(s);
    if (v4) {
        const o = parseV4(v4[1]);
        if (!o) return null;
        tail = [(o[0] << 8) | o[1], (o[2] << 8) | o[3]];
        s = s.slice(0, -v4[1].length);
        if (s.endsWith(":") && !s.endsWith("::")) s = s.slice(0, -1);
    }
    const halves = s.split("::");
    if (halves.length > 2) return null;
    const toNums = (part: string) => (part ? part.split(":").map((h) => parseInt(h, 16)) : []);
    const head = toNums(halves[0]);
    const rest = halves.length === 2 ? toNums(halves[1]) : [];
    const width = 8 - tail.length;
    let groups: number[];
    if (halves.length === 2) {
        const zeros = width - head.length - rest.length;
        if (zeros < 0) return null;
        groups = [...head, ...new Array<number>(zeros).fill(0), ...rest];
    } else {
        if (head.length !== width) return null;
        groups = head;
    }
    const all = [...groups, ...tail];
    return all.length === 8 && all.every((g) => Number.isInteger(g) && g >= 0 && g <= 0xffff) ? all : null;
}

function privateV6(h: number[]): boolean {
    const v4of = (hi: number, lo: number) => [hi >> 8, hi & 0xff, lo >> 8, lo & 0xff];
    if (h.slice(0, 6).every((g) => g === 0)) {
        // ::, ::1 and IPv4-compatible ::a.b.c.d
        return h[6] === 0 || privateV4(v4of(h[6], h[7]));
    }
    if (h.slice(0, 5).every((g) => g === 0) && h[5] === 0xffff) {
        return privateV4(v4of(h[6], h[7])); // IPv4-mapped
    }
    if (h[0] === 0x64 && h[1] === 0xff9b) {
        return privateV4(v4of(h[6], h[7])); // NAT64
    }
    return (
        (h[0] & 0xffc0) === 0xfe80 || // link-local
        (h[0] & 0xffc0) === 0xfec0 || // site-local (deprecated)
        (h[0] & 0xfe00) === 0xfc00 || // ULA fc00::/7 (incl. Tailscale fd7a:115c:a1e0::/48)
        (h[0] & 0xff00) === 0xff00 // multicast
    );
}

// True for every address a channel run must not reach. Unparseable input
// counts as private (fail closed).
export function isPrivateAddress(ip: string): boolean {
    const bare = ip.replace(/^\[|\]$/g, "");
    if (net.isIPv4(bare)) {
        const o = parseV4(bare);
        return !o || privateV4(o);
    }
    if (net.isIPv6(bare)) {
        const groups = expandV6(bare);
        return !groups || privateV6(groups);
    }
    return true;
}

function blockedName(hostname: string): boolean {
    const h = hostname.toLowerCase().replace(/\.$/, "");
    return (
        h === "localhost" ||
        h.endsWith(".localhost") ||
        h.endsWith(".local") ||
        h.endsWith(".internal") ||
        h === "ts.net" ||
        h.endsWith(".ts.net")
    );
}

async function checkHost(url: URL, deps: ChannelFetchDeps): Promise<string | null> {
    if (url.protocol !== "http:" && url.protocol !== "https:") {
        return `Only http(s) URLs are allowed (got ${url.protocol}).`;
    }
    const host = url.hostname.replace(/^\[|\]$/g, "");
    if (blockedName(host)) {
        return `Host ${host} is not allowed in channel runs (internal name).`;
    }
    if (net.isIP(host)) {
        return isPrivateAddress(host) ? `Host ${host} is not allowed in channel runs (private address).` : null;
    }
    let addresses: string[];
    try {
        addresses = await deps.lookup(host);
    } catch (error) {
        return `Host ${host} could not be resolved: ${error instanceof Error ? error.message : String(error)}`;
    }
    if (addresses.length === 0) {
        return `Host ${host} could not be resolved.`;
    }
    const internal = addresses.find(isPrivateAddress);
    return internal ? `Host ${host} is not allowed in channel runs (resolves to private address ${internal}).` : null;
}

export async function channelFetch(
    rawUrl: string,
    rawMethod: string | undefined,
    deps: ChannelFetchDeps = defaultChannelFetchDeps,
): Promise<FetchResult> {
    const method = (rawMethod || "GET").toUpperCase();
    if (method !== "GET" && method !== "HEAD") {
        return { ok: false, status: 0, error: `fetch-url with method ${method} is not allowed in channel runs.` };
    }
    let url: URL;
    try {
        url = new URL(rawUrl);
    } catch {
        return { ok: false, status: 0, error: "Invalid URL." };
    }
    // One deadline for the whole chain: every hop and the body read.
    const timeoutMs = deps.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    const signal = AbortSignal.timeout(timeoutMs);
    try {
        for (let hop = 0; ; hop++) {
            const blocked = await checkHost(url, deps);
            if (blocked) {
                return { ok: false, status: 0, error: blocked };
            }
            const res = await deps.fetch(url.toString(), { method, redirect: "manual", signal });
            const location = res.headers.get("location");
            if (res.status >= 300 && res.status < 400 && location) {
                await res.body?.cancel().catch(() => undefined);
                if (hop >= MAX_REDIRECTS) {
                    return { ok: false, status: 0, error: `Too many redirects (more than ${MAX_REDIRECTS}).` };
                }
                url = new URL(location, url);
                continue;
            }
            const { text, truncated } = await readLimited(res, MAX_BODY);
            return { ok: res.ok, status: res.status, statusText: res.statusText, body: text, truncated };
        }
    } catch (e) {
        if (signal.aborted) {
            return { ok: false, status: 0, error: `fetch-url timed out after ${Math.round(timeoutMs / 1000)} s.` };
        }
        return { ok: false, status: 0, error: e instanceof Error ? e.message : String(e) };
    }
}

// Reads at most `limit` characters, then cancels the stream instead of
// downloading the rest.
async function readLimited(res: Response, limit: number): Promise<{ text: string; truncated: boolean }> {
    if (!res.body) {
        return { text: "", truncated: false };
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let text = "";
    for (;;) {
        const { done, value } = await reader.read();
        if (done) {
            text += decoder.decode();
            break;
        }
        text += decoder.decode(value, { stream: true });
        if (text.length > limit) {
            await reader.cancel().catch(() => undefined);
            return { text: text.slice(0, limit), truncated: true };
        }
    }
    return text.length > limit
        ? { text: text.slice(0, limit), truncated: true }
        : { text, truncated: false };
}
