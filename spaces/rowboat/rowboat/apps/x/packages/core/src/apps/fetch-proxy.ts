// Modified for VibeMind (Flissel/rowboat vibemind): the §7.5 fetch proxy moved
// out of host-api.ts (ticket #330, fix round 3, review I-A). It was a
// prompt-free relay to Rowboat's own server: its SSRF check missed 100.64/10
// (Tailscale) and the machine's own public/LAN interface addresses, resolved
// again at fetch time, and passed an Authorization header through. Now:
// - every hop is refused for loopback/private/link-local/ULA/CGNAT/multicast
//   addresses and for every address of this machine, by name and by literal,
// - the transport checks the address it really connects to (same predicate),
// - a header or URL that carries a key of the own server is refused (defence
//   in depth only, see carriesOwnServerCredential),
// - Host, Origin, Cookie and hop-by-hop headers are never forwarded.
// Behaviour change (review m-N4): 100.64/10 (Tailscale peers) and the
// machine's own public/LAN addresses are refused too, so an app that used the
// proxy for a Tailscale peer now gets 403 address_forbidden.

import net from "node:net";
import type express from "express";
import type { RowboatAppManifest } from "@x/shared/dist/rowboat-app.js";
import { MAX_LLM_REQUEST_BYTES, MAX_PROXY_RESPONSE_BYTES, PROXY_TIMEOUT_MS } from "./constants.js";
import { readBody, sendError } from "./server.js";
import { isPrivateAddress } from "../runtime/tools/channel-fetch.js";
import { connectGuardedFetch, nextRedirectRequest, sanitizedRequestHeaders, type Transport } from "../runtime/tools/own-server-guard.js";
import {
    carriesOwnServerCredential,
    defaultOwnServerDeps,
    isOwnMachineAddress,
    isOwnServerTarget,
    type OwnServerDeps,
} from "../security/own-server-target.js";

export interface FetchProxyDeps {
    lookup: OwnServerDeps["lookup"];
    // The machine's own interface addresses (default: os.networkInterfaces()).
    interfaces?: () => string[];
    // The HTTP transport (tests inject a stub). Default: connectGuardedFetch
    // refusing every forbidden connect address.
    transport?: Transport;
}

export const defaultFetchProxyDeps: FetchProxyDeps = { lookup: defaultOwnServerDeps.lookup };

const FORBIDDEN_PREFIX = "address_forbidden: ";

// Loopback, private, link-local, ULA, CGNAT 100.64/10 (Tailscale), multicast
// and reserved space — and any address of this machine (LAN, Tailscale, a
// public interface), whatever the port.
export function isForbiddenProxyAddress(ip: string, deps: Pick<FetchProxyDeps, "interfaces"> = {}): boolean {
    return isPrivateAddress(ip) || isOwnMachineAddress(ip, deps);
}

/** Reject URLs that point at this machine or at loopback/private space. */
export async function ssrfCheck(url: URL, deps: FetchProxyDeps = defaultFetchProxyDeps): Promise<string | null> {
    const host = url.hostname.toLowerCase().replace(/^\[|\]$/g, "");
    if (host === "localhost" || host.endsWith(".localhost")) return "localhost addresses are forbidden";
    if (await isOwnServerTarget(url, { lookup: deps.lookup, ...(deps.interfaces ? { interfaces: deps.interfaces } : {}) })) {
        return `${url.host} is Rowboat's own local server`;
    }
    if (net.isIP(host)) {
        return isForbiddenProxyAddress(host, deps) ? `address ${host} is forbidden` : null;
    }
    let addresses: string[];
    try {
        addresses = await deps.lookup(host);
    } catch {
        return `cannot resolve host ${host}`;
    }
    if (addresses.length === 0) return `cannot resolve host ${host}`;
    for (const address of addresses) {
        if (isForbiddenProxyAddress(address, deps)) return `${host} resolves to forbidden address ${address}`;
    }
    return null;
}

function defaultTransport(deps: FetchProxyDeps): Transport {
    const ownDeps: OwnServerDeps = { lookup: deps.lookup, ...(deps.interfaces ? { interfaces: deps.interfaces } : {}) };
    return connectGuardedFetch(ownDeps, async (address, _port, url) =>
        isForbiddenProxyAddress(address, deps) ? `${FORBIDDEN_PREFIX}${url.hostname} connects to forbidden address ${address}` : null,
    );
}

async function readJsonBody(req: express.Request, res: express.Response, limit: number): Promise<Record<string, unknown> | null> {
    const body = await readBody(req, limit);
    if (body === null) {
        sendError(res, 413, "too_large", `request body exceeds ${limit} bytes`);
        return null;
    }
    try {
        const parsed: unknown = JSON.parse(body.toString("utf-8"));
        if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("not an object");
        return parsed as Record<string, unknown>;
    } catch {
        sendError(res, 400, "bad_request", "body must be a JSON object");
        return null;
    }
}

export async function handleFetchProxy(
    _slug: string,
    _manifest: RowboatAppManifest,
    req: express.Request,
    res: express.Response,
    deps: FetchProxyDeps = defaultFetchProxyDeps,
): Promise<void> {
    const body = await readJsonBody(req, res, MAX_LLM_REQUEST_BYTES);
    if (!body) return;
    const rawUrl = typeof body.url === "string" ? body.url : "";
    const method = (typeof body.method === "string" ? body.method : "GET").toUpperCase();
    if (method !== "GET" && method !== "POST") return sendError(res, 400, "bad_request", "method must be GET or POST");

    let target: URL;
    try {
        target = new URL(rawUrl);
    } catch {
        return sendError(res, 400, "invalid_url", "url must be a valid absolute URL");
    }
    if (target.protocol !== "http:" && target.protocol !== "https:") {
        return sendError(res, 400, "invalid_url", "only http(s) URLs are allowed");
    }

    // Strip credential-bearing / routing / hop-by-hop headers; pass the rest.
    const given: Record<string, string> = {};
    if (body.headers && typeof body.headers === "object") {
        for (const [k, v] of Object.entries(body.headers as Record<string, unknown>)) {
            if (typeof v !== "string") continue;
            if (k.trim().toLowerCase() === "cookie") continue;
            given[k] = v;
        }
    }
    const headers = sanitizedRequestHeaders(given);
    if (await carriesOwnServerCredential([...Object.values(headers), rawUrl])) {
        return sendError(res, 403, "credential_forbidden", "the request carries a key of Rowboat's own server");
    }
    const requestBody = typeof body.body === "string" ? body.body : undefined;
    const transport = deps.transport ?? defaultTransport(deps);

    // Follow redirects manually so every hop passes the SSRF check (§7.5).
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), PROXY_TIMEOUT_MS);
    res.on("close", () => {
        if (!res.writableEnded) controller.abort();
    });
    try {
        let current = target;
        let hopMethod = method;
        let hopHeaders: Record<string, string> | undefined = headers;
        let hopBody = method === "POST" ? requestBody : undefined;
        for (let hop = 0; hop < 5; hop++) {
            const violation = await ssrfCheck(current, deps);
            if (violation) return sendError(res, 403, "address_forbidden", violation);
            if (await carriesOwnServerCredential([current.toString()])) {
                return sendError(res, 403, "credential_forbidden", "the request carries a key of Rowboat's own server");
            }

            const upstream = await transport(current.toString(), {
                method: hopMethod,
                ...(hopHeaders ? { headers: hopHeaders } : {}),
                ...(hopBody !== undefined ? { body: hopBody } : {}),
                redirect: "manual",
                signal: controller.signal,
            });

            if (upstream.status >= 300 && upstream.status < 400) {
                const location = upstream.headers.get("location");
                await upstream.body?.cancel().catch(() => undefined);
                if (!location) break;
                // Same rules as fetch-url (fix round 3): cross-origin drops
                // credentials; 303, and 301/302 after POST, become GET.
                const next = nextRedirectRequest(upstream.status, location, {
                    url: current,
                    method: hopMethod,
                    headers: hopHeaders,
                    body: hopBody,
                });
                current = next.url;
                hopMethod = next.method;
                hopHeaders = next.headers;
                hopBody = next.body;
                continue;
            }

            // Stream with the response-size cap (on decoded bytes).
            const reader = upstream.body?.getReader();
            let text = "";
            let truncated = false;
            if (reader) {
                const decoder = new TextDecoder();
                let received = 0;
                for (;;) {
                    const { done, value } = await reader.read();
                    if (done) break;
                    received += value.byteLength;
                    if (received > MAX_PROXY_RESPONSE_BYTES) {
                        truncated = true;
                        text += decoder.decode(value.subarray(0, value.byteLength - (received - MAX_PROXY_RESPONSE_BYTES)));
                        void reader.cancel();
                        break;
                    }
                    text += decoder.decode(value, { stream: true });
                }
            }
            res.json({ ok: upstream.ok, status: upstream.status, statusText: upstream.statusText, text, truncated });
            return;
        }
        sendError(res, 502, "too_many_redirects", "redirect chain too long or missing location");
    } catch (e) {
        const message = e instanceof Error ? e.message : String(e);
        if (message.startsWith(FORBIDDEN_PREFIX)) {
            return sendError(res, 403, "address_forbidden", message.slice(FORBIDDEN_PREFIX.length));
        }
        if (controller.signal.aborted) return sendError(res, 504, "upstream_timeout", `upstream did not respond within ${PROXY_TIMEOUT_MS}ms`);
        sendError(res, 502, "fetch_failed", message);
    } finally {
        clearTimeout(timeout);
    }
}
