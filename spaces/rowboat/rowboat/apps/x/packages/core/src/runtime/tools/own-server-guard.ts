// Modified for VibeMind (Flissel/rowboat vibemind): new module (ticket #330,
// fix round 1, review C1). fetch-url must never reach Rowboat's own local
// server (apps/server, POST /rpc/<channel>): with its key an agent could write
// config/ through workspace:writeFile, or answer its own human-only request
// through sessions:respondToPermission (recorded as source "human"). Refused
// in every run, whatever the method, with no approval path. Other loopback
// targets are unchanged here (ticket #325).
// Fix round 2 (DNS rebinding): the URL is checked before each hop, and the
// transport checks the address it really connects to (its own lookup runs
// the same predicate on every resolved address for the requested port).

import http from "node:http";
import https from "node:https";
import net, { type LookupFunction } from "node:net";
import { pipeline, Readable, type Transform } from "node:stream";
import zlib from "node:zlib";
import {
    defaultOwnServerDeps,
    isOwnServerAddress,
    isOwnServerTarget,
    type OwnServerDeps,
} from "../../security/own-server-target.js";
import { readLimited } from "./channel-fetch.js";

// The host+port predicate lives in security/own-server-target.ts (fix round 2,
// review N2): the embedded browser's session uses the same one.
export { defaultOwnServerDeps, type OwnServerDeps };

function refusalFor(host: string): string {
    return `fetch-url may not call Rowboat's own local server (${host}). This is refused in every run, without an approval path.`;
}

// A message when the URL points at Rowboat's own server, else null.
export async function ownServerRefusal(url: URL, deps: OwnServerDeps = defaultOwnServerDeps): Promise<string | null> {
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    if (!(await isOwnServerTarget(url, deps))) return null;
    return refusalFor(url.host);
}

export type TransportInit = {
    method: string;
    headers?: Record<string, string>;
    body?: string;
    redirect: "manual";
    signal?: AbortSignal;
};
export type Transport = (url: string, init: TransportInit) => Promise<Response>;

export interface GuardedFetchDeps extends OwnServerDeps {
    // The HTTP transport (tests inject a stub). Default: connectGuardedFetch
    // over the same deps.
    fetch?: Transport;
}

export const defaultGuardedFetchDeps: GuardedFetchDeps = { ...defaultOwnServerDeps };

// A message when the transport must not connect to `address` on `port`
// (the URL is the request's), else null.
export type AddressRefusal = (address: string, port: number, url: URL) => Promise<string | null>;

const IDLE_TIMEOUT_MS = 300_000; // undici's headers/body timeout
const NULL_BODY_STATUS = new Set([101, 204, 205, 304]);

// Fix round 3 (review I-A): the agent never sets these. Host and Origin come
// from the URL only (undici ignored a Host override, so this restores the old
// behaviour; a Host override addressed other local virtual hosts such as
// <slug>.apps.localhost:3210). Hop-by-hop headers and framing are the
// transport's own. Behaviour change (review m-N4): undici sent an
// agent-supplied Origin; fetch-url no longer sends one at all.
const DROPPED_REQUEST_HEADERS = new Set([
    "host",
    "origin",
    "connection",
    "keep-alive",
    "proxy-connection",
    "proxy-authorization",
    "transfer-encoding",
    "te",
    "trailer",
    "upgrade",
    "content-length",
]);

export function sanitizedRequestHeaders(headers: Record<string, string> | undefined): Record<string, string> {
    const out: Record<string, string> = {};
    for (const [name, value] of Object.entries(headers ?? {})) {
        if (DROPPED_REQUEST_HEADERS.has(name.trim().toLowerCase())) continue;
        out[name] = value;
    }
    return out;
}

function hasHeader(headers: Record<string, string>, name: string): boolean {
    return Object.keys(headers).some((k) => k.toLowerCase() === name);
}

// At most this many codings are undone; a longer chain is returned raw (no
// decoder stages are created for it).
export const MAX_CONTENT_CODINGS = 5;

type Stage = "gzip" | "deflate" | "br" | "zstd";

type ZstdFactory = () => Transform;
const createZstd = (zlib as unknown as { createZstdDecompress?: ZstdFactory }).createZstdDecompress;

// null: nothing to undo (identity); undefined: unknown or unsupported.
function stageFor(coding: string): Stage | null | undefined {
    switch (coding) {
        case "identity":
            return null;
        case "gzip":
        case "x-gzip":
            return "gzip";
        case "deflate":
            return "deflate";
        case "br":
            return "br";
        case "zstd":
            return createZstd ? "zstd" : undefined;
        default:
            return undefined;
    }
}

export const ACCEPT_ENCODING = createZstd ? "gzip, deflate, br, zstd" : "gzip, deflate, br";

// "deflate" is meant to be zlib-wrapped, but servers also send raw deflate:
// the first two bytes decide, the way browsers and undici do.
function isZlibWrapped(head: Buffer): boolean {
    return head.length >= 2 && (head[0] & 0x0f) === 8 && ((head[0] << 8) | head[1]) % 31 === 0;
}

// The first `n` bytes of a stream (fewer if it ends first), left in the
// stream. A stream that already ended is replaced by one holding those bytes.
function peekHead(stream: Readable, n: number): Promise<{ head: Buffer; stream: Readable }> {
    return new Promise((resolve, reject) => {
        const chunks: Buffer[] = [];
        let length = 0;
        const cleanup = () => {
            stream.off("readable", onReadable);
            stream.off("end", onEnd);
            stream.off("error", onError);
            stream.off("close", onClose);
        };
        const finish = (ended: boolean) => {
            cleanup();
            const head = Buffer.concat(chunks);
            if (ended) {
                resolve({ head, stream: Readable.from(head.length > 0 ? [head] : []) });
                return;
            }
            stream.unshift(head);
            resolve({ head, stream });
        };
        const onReadable = () => {
            let chunk: Buffer | null;
            while (length < n && (chunk = stream.read() as Buffer | null) !== null) {
                chunks.push(chunk);
                length += chunk.length;
            }
            if (length >= n) finish(false);
        };
        const onEnd = () => finish(true);
        const onError = (error: Error) => {
            cleanup();
            reject(error);
        };
        const onClose = () => {
            cleanup();
            reject(new Error("The response closed before its body started."));
        };
        stream.on("readable", onReadable);
        stream.once("end", onEnd);
        stream.once("error", onError);
        stream.once("close", onClose);
    });
}

// Sync flush, as undici decodes (Task 17 step 0, review m-N1): an empty or
// trailer-truncated body yields what was decoded instead of Z_BUF_ERROR.
const ZLIB_SYNC = { flush: zlib.constants.Z_SYNC_FLUSH, finishFlush: zlib.constants.Z_SYNC_FLUSH };
const BROTLI_SYNC = {
    flush: zlib.constants.BROTLI_OPERATION_FLUSH,
    finishFlush: zlib.constants.BROTLI_OPERATION_FLUSH,
};

function createStage(stage: Exclude<Stage, "deflate">): Transform {
    switch (stage) {
        case "gzip":
            return zlib.createGunzip(ZLIB_SYNC);
        case "br":
            return zlib.createBrotliDecompress(BROTLI_SYNC);
        case "zstd":
            return createZstd!();
    }
}

// Undoes Content-Encoding: a comma-separated chain of at most
// MAX_CONTENT_CODINGS, applied in order, so undone in reverse. An unknown
// coding anywhere, or a longer chain: the raw body, header intact. Every
// decoder is a stage of `pipeline` (backpressure, and an error anywhere
// destroys all of them); destroying the returned stream (a cancelled web
// stream, the size limit) destroys the response and with it the socket
// (review m3). Limits apply to what the caller reads, i.e. decompressed bytes.
export async function decodedBody(res: Readable & { headers?: http.IncomingHttpHeaders }): Promise<Readable> {
    const header = String(res.headers?.["content-encoding"] ?? "");
    const codings = header
        .split(",")
        .map((c) => c.trim().toLowerCase())
        .filter((c) => c.length > 0);
    if (codings.length > MAX_CONTENT_CODINGS) return res;
    const stages: Stage[] = [];
    for (const coding of [...codings].reverse()) {
        const stage = stageFor(coding);
        if (stage === undefined) return res;
        if (stage) stages.push(stage);
    }
    if (stages.length === 0) return res;
    let current: Readable = res;
    for (const stage of stages) {
        let next: Transform;
        if (stage === "deflate") {
            const peeked = await peekHead(current, 2);
            current = peeked.stream;
            next = isZlibWrapped(peeked.head) ? zlib.createInflate(ZLIB_SYNC) : zlib.createInflateRaw(ZLIB_SYNC);
        } else {
            next = createStage(stage);
        }
        current = pipeline(current, next, () => undefined);
    }
    const last = current;
    last.once("close", () => {
        if (!res.destroyed) res.destroy();
    });
    return last;
}

function defaultRefusal(deps: OwnServerDeps): AddressRefusal {
    return async (address, port, url) => ((await isOwnServerAddress(address, port, deps)) ? refusalFor(url.host) : null);
}

// A Response for any status the server sends: Response itself only takes
// 200-599 and a token status text, fetch returned the rest too (review I-B).
function responseFor(
    body: ReadableStream<Uint8Array> | null,
    status: number,
    statusText: string,
    headers: Headers,
): Response {
    const inRange = status >= 200 && status <= 599;
    let response: Response;
    let textKept = true;
    try {
        response = new Response(body, { status: inRange ? status : 599, statusText, headers });
    } catch {
        response = new Response(body, { status: inRange ? status : 599, headers });
        textKept = false;
    }
    if (!inRange) {
        Object.defineProperty(response, "status", { value: status });
        Object.defineProperty(response, "ok", { value: false });
    }
    if (!textKept) Object.defineProperty(response, "statusText", { value: statusText });
    return response;
}

// An HTTP(S) transport with fetch's Response, built on node:http(s) so the
// connect address can be checked: the request's own lookup resolves the host
// and refuses the connection when any resolved address is refused (default:
// the own server on the requested port). Host names only — an IP literal is
// not looked up, the caller's URL pre-check covers it. Not followed:
// redirects (redirect "manual" — the caller re-checks each hop), proxies.
export function connectGuardedFetch(deps: OwnServerDeps = defaultOwnServerDeps, refuse: AddressRefusal = defaultRefusal(deps)): Transport {
    return (rawUrl, init) =>
        new Promise<Response>((resolve, reject) => {
            const url = new URL(rawUrl);
            const port = url.port ? Number(url.port) : url.protocol === "https:" ? 443 : 80;
            const lookup: LookupFunction = (hostname, options, callback) => {
                (async () => {
                    const addresses = await deps.lookup(hostname);
                    if (addresses.length === 0) throw new Error(`Host ${hostname} could not be resolved.`);
                    for (const address of addresses) {
                        const refusal = await refuse(address, port, url);
                        if (refusal) throw new Error(refusal);
                    }
                    const family = typeof options.family === "number" ? options.family : 0;
                    const entries = addresses
                        .map((address) => ({ address, family: net.isIPv6(address) ? 6 : 4 }))
                        .filter((e) => family === 0 || e.family === family);
                    if (entries.length === 0) throw new Error(`Host ${hostname} has no IPv${family} address.`);
                    if (options.all) callback(null, entries);
                    else callback(null, entries[0].address, entries[0].family);
                })().catch((error: unknown) => {
                    callback(error instanceof Error ? error : new Error(String(error)), "", 0);
                });
            };
            const headers = sanitizedRequestHeaders(init.headers);
            if (!hasHeader(headers, "accept")) headers.accept = "*/*";
            if (!hasHeader(headers, "user-agent")) headers["user-agent"] = "node";
            if (!hasHeader(headers, "accept-encoding")) headers["accept-encoding"] = ACCEPT_ENCODING;
            if (init.body !== undefined) {
                // fetch labels a string body text/plain unless told otherwise.
                if (!hasHeader(headers, "content-type")) headers["content-type"] = "text/plain;charset=UTF-8";
                headers["content-length"] = String(Buffer.byteLength(init.body));
            }
            const lib = url.protocol === "https:" ? https : http;
            const req = lib.request(
                url,
                { method: init.method, headers, lookup, agent: false, ...(init.signal ? { signal: init.signal } : {}) },
                (res) => {
                    const status = res.statusCode ?? 0;
                    const responseHeaders = new Headers();
                    for (const [name, value] of Object.entries(res.headers)) {
                        if (value === undefined) continue;
                        for (const v of Array.isArray(value) ? value : [value]) responseHeaders.append(name, String(v));
                    }
                    const finish = (body: ReadableStream<Uint8Array> | null) => {
                        try {
                            resolve(responseFor(body, status, res.statusMessage ?? "", responseHeaders));
                        } catch (error) {
                            res.destroy();
                            reject(error);
                        }
                    };
                    if (init.method === "HEAD" || NULL_BODY_STATUS.has(status)) {
                        res.resume();
                        finish(null);
                        return;
                    }
                    decodedBody(res).then(
                        (stream) => finish(Readable.toWeb(stream) as unknown as ReadableStream<Uint8Array>),
                        (error: unknown) => {
                            res.destroy();
                            reject(error);
                        },
                    );
                },
            );
            req.setTimeout(IDLE_TIMEOUT_MS, () => req.destroy(new Error("fetch-url timed out.")));
            req.on("error", reject);
            req.end(init.body);
        });
}

const MAX_REDIRECTS = 20; // fetch's own limit
const MAX_BODY = 200_000;
// Fix round 3: one set of redirect rules for fetch-url and the apps fetch
// proxy (the way fetch follows redirects, with a wider credential filter).
const CREDENTIAL_HEADERS = new Set(["authorization", "proxy-authorization", "cookie", "cookie2"]);
// Any other header that looks like it carries a credential.
const CREDENTIAL_LIKE = /(^|[-_])(auth|authorization|token|api[-_]?key|apikey|secret|password|session|signature)([-_]|$)/i;
// Headers that describe a request body (dropped with the body).
const BODY_HEADERS = new Set(["content-type", "content-encoding", "content-language", "content-location"]);

export interface RedirectRequest {
    url: URL;
    method: string;
    headers: Record<string, string> | undefined;
    body: string | undefined;
}

// The request for the next hop after a 3xx with a Location header: 303, and
// 301/302 after anything but GET/HEAD, become GET without a body (and
// without body headers); a cross-origin hop drops every credential header.
export function nextRedirectRequest(status: number, location: string, current: RedirectRequest): RedirectRequest {
    const url = new URL(location, current.url);
    let { method, headers, body } = current;
    if (status === 303 || ((status === 301 || status === 302) && method !== "GET" && method !== "HEAD")) {
        if (method !== "HEAD") method = "GET";
        body = undefined;
        if (headers) {
            headers = Object.fromEntries(Object.entries(headers).filter(([name]) => !BODY_HEADERS.has(name.trim().toLowerCase())));
        }
    }
    if (url.origin !== current.url.origin && headers) {
        headers = Object.fromEntries(
            Object.entries(headers).filter(([name]) => {
                const lower = name.trim().toLowerCase();
                return !CREDENTIAL_HEADERS.has(lower) && !CREDENTIAL_LIKE.test(lower);
            }),
        );
    }
    return { url, method, headers, body };
}

type FetchUrlResult =
    | { ok: boolean; status: number; statusText: string; body: string; truncated: boolean }
    | { ok: false; status: 0; error: string };

// fetch-url outside channel runs: the request and every redirect hop are
// checked against the own server; redirects are followed by hand the way
// fetch follows them (303, and 301/302 after POST, become GET without a
// body; credentials are dropped when the origin changes).
export async function guardedFetch(
    rawUrl: string,
    rawMethod: string | undefined,
    headers: Record<string, string> | undefined,
    body: string | undefined,
    deps: GuardedFetchDeps = defaultGuardedFetchDeps,
): Promise<FetchUrlResult> {
    let url: URL;
    try {
        url = new URL(rawUrl);
    } catch (e) {
        return { ok: false, status: 0, error: e instanceof Error ? e.message : String(e) };
    }
    if (url.protocol !== "http:" && url.protocol !== "https:") {
        return { ok: false, status: 0, error: "Only http(s) URLs are allowed." };
    }
    const transport = deps.fetch ?? connectGuardedFetch(deps);
    let method = (rawMethod || "GET").toUpperCase();
    let sendHeaders = headers;
    let sendBody = method === "GET" || method === "HEAD" ? undefined : body;
    try {
        for (let hop = 0; ; hop++) {
            const refusal = await ownServerRefusal(url, deps);
            if (refusal) return { ok: false, status: 0, error: refusal };
            const res = await transport(url.toString(), {
                method,
                ...(sendHeaders ? { headers: sendHeaders } : {}),
                ...(sendBody !== undefined ? { body: sendBody } : {}),
                redirect: "manual",
            });
            const location = res.headers.get("location");
            if (res.status >= 300 && res.status < 400 && location) {
                await res.body?.cancel().catch(() => undefined);
                if (hop >= MAX_REDIRECTS) return { ok: false, status: 0, error: "Too many redirects." };
                const next = nextRedirectRequest(res.status, location, { url, method, headers: sendHeaders, body: sendBody });
                url = next.url;
                method = next.method;
                sendHeaders = next.headers;
                sendBody = next.body;
                continue;
            }
            // Read at most MAX_BODY characters, then cancel: the transport
            // destroys the socket instead of downloading the rest (review m3).
            const { text, truncated } = await readLimited(res, MAX_BODY);
            return { ok: res.ok, status: res.status, statusText: res.statusText, body: text, truncated };
        }
    } catch (e) {
        return { ok: false, status: 0, error: e instanceof Error ? e.message : String(e) };
    }
}
