// Modified for VibeMind (Flissel/rowboat vibemind): new module (ticket #330,
// fix round 2, review N2/m2). The one predicate "does this URL reach Rowboat's
// own local server (apps/server, POST /rpc/<channel>)?", shared by
// - fetch-url (runtime/tools/own-server-guard.ts), and
// - the agent-driven embedded browser (apps/main/src/browser/view.ts), whose
//   session cancels such requests at network level (installOwnServerBlock).
// With the server key an agent could write config/ through
// workspace:writeFile or answer its own human-only request through
// sessions:respondToPermission; every prompt-free route there is closed where
// the request is made.
// Residual: a host name is resolved again by the network stack itself (DNS
// rebinding between this check and the connect is not prevented).

import crypto from "node:crypto";
import dns from "node:dns/promises";
import fs from "node:fs/promises";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { APPS_PORT } from "../apps/constants.js";
import { WorkDir } from "../config/config.js";
import { expandV6, parseV4 } from "../runtime/tools/channel-fetch.js";

// apps/server/src/config.ts: DEFAULT_PORT 3220, overridable by
// config/server.json "port" and ROWBOAT_SERVER_PORT; server.ts tries up to
// 10 consecutive ports when one is taken. Core does not import apps/server,
// so the same sources are read here and every fallback port counts.
const DEFAULT_SERVER_PORT = 3220;
const PORT_FALLBACK_ATTEMPTS = 10;

export interface OwnServerDeps {
    // All addresses a host name resolves to.
    lookup: (host: string) => Promise<string[]>;
    // The ports the own server may listen on (default: read per call).
    ports?: () => Promise<Set<number>>;
    // The machine's own interface addresses (default: os.networkInterfaces()).
    interfaces?: () => string[];
    // The machine's host name (default: os.hostname()).
    hostname?: () => string;
    // The Rowboat Apps server's port(s) (default: APPS_PORT, 3210). Fix round
    // 3 (review I-A): its host API (/_rowboat/fetch, /tools/execute,
    // /copilot/run, PUT /data) is a relay, so it counts as own too.
    appsPorts?: () => Set<number>;
}

export const defaultOwnServerDeps: OwnServerDeps = {
    lookup: async (host) => (await dns.lookup(host, { all: true, verbatim: true })).map((a) => a.address),
};

async function configuredPort(): Promise<number | undefined> {
    try {
        const raw = await fs.readFile(path.join(WorkDir, "config", "server.json"), "utf8");
        const port = Number((JSON.parse(raw) as { port?: unknown }).port);
        return Number.isInteger(port) && port > 0 ? port : undefined;
    } catch {
        return undefined;
    }
}

export async function ownServerPorts(): Promise<Set<number>> {
    const env = Number(process.env.ROWBOAT_SERVER_PORT);
    const bases = [DEFAULT_SERVER_PORT, Number.isInteger(env) && env > 0 ? env : undefined, await configuredPort()];
    const ports = new Set<number>();
    for (const base of bases) {
        if (base === undefined) continue;
        for (let i = 0; i < PORT_FALLBACK_ATTEMPTS; i++) ports.add(base + i);
    }
    return ports;
}

// The browser asks for every request; config/server.json is read at most once
// per interval there.
export function memoizePorts(
    read: () => Promise<Set<number>>,
    ttlMs: number,
    now: () => number = Date.now,
): () => Promise<Set<number>> {
    let cached: { at: number; ports: Promise<Set<number>> } | null = null;
    return () => {
        const t = now();
        if (!cached || t - cached.at >= ttlMs) {
            const ports = read();
            cached = { at: t, ports };
            // A failed read is not kept.
            ports.catch(() => {
                if (cached?.ports === ports) cached = null;
            });
        }
        return cached.ports;
    };
}

const WEB_PROTOCOLS = new Set(["http:", "https:", "ws:", "wss:"]);

function effectivePort(url: URL): number {
    if (url.port) return Number(url.port);
    return url.protocol === "https:" || url.protocol === "wss:" ? 443 : 80;
}

function loopbackOrUnspecifiedV4([a]: number[]): boolean {
    return a === 127 || a === 0;
}

// 127.0.0.0/8, 0.0.0.0/8, ::1, :: and their IPv4-mapped / -compatible forms.
function isLoopbackOrUnspecified(ip: string): boolean {
    const bare = ip.replace(/^\[|\]$/g, "");
    if (net.isIPv4(bare)) {
        const o = parseV4(bare);
        return !o || loopbackOrUnspecifiedV4(o);
    }
    if (net.isIPv6(bare)) {
        const h = expandV6(bare);
        if (!h) return true;
        if (h.slice(0, 7).every((g) => g === 0) && (h[7] === 0 || h[7] === 1)) return true;
        const v4 = [h[6] >> 8, h[6] & 0xff, h[7] >> 8, h[7] & 0xff];
        if (h.slice(0, 5).every((g) => g === 0) && (h[5] === 0xffff || h[5] === 0)) return loopbackOrUnspecifiedV4(v4);
        return false;
    }
    return false;
}

// Lower case, no brackets, no zone; IPv6 as eight hex groups; an IPv4-mapped
// IPv6 address (::ffff:a.b.c.d, ::ffff:xxxx:xxxx) as its dotted IPv4 form, so
// it compares equal to the interface's IPv4 address (review m2).
export function normalizeAddress(ip: string): string {
    const bare = ip.replace(/^\[|\]$/g, "").toLowerCase();
    const zone = bare.indexOf("%");
    const noZone = zone >= 0 ? bare.slice(0, zone) : bare;
    if (net.isIPv6(noZone)) {
        const h = expandV6(noZone);
        if (!h) return noZone;
        if (h.slice(0, 5).every((g) => g === 0) && h[5] === 0xffff) {
            return [h[6] >> 8, h[6] & 0xff, h[7] >> 8, h[7] & 0xff].join(".");
        }
        return h.map((g) => g.toString(16)).join(":");
    }
    return noZone;
}

function defaultInterfaces(): string[] {
    const own: string[] = [];
    for (const infos of Object.values(os.networkInterfaces())) {
        for (const info of infos ?? []) own.push(info.address);
    }
    return own;
}

function isOwnAddress(ip: string, interfaces: Set<string>): boolean {
    return isLoopbackOrUnspecified(ip) || interfaces.has(normalizeAddress(ip));
}

function isOwnName(hostname: string, ownHostname: string): boolean {
    const h = hostname.toLowerCase().replace(/\.$/, "");
    const own = ownHostname.toLowerCase();
    return h === "localhost" || h.endsWith(".localhost") || h === own || h === `${own}.local`;
}

function hostIsOwn(host: string, deps: OwnServerDeps): Promise<boolean> | boolean {
    if (isOwnName(host, (deps.hostname ?? os.hostname)())) return true;
    const interfaces = new Set((deps.interfaces ?? defaultInterfaces)().map(normalizeAddress));
    if (net.isIP(host)) return isOwnAddress(host, interfaces);
    return deps.lookup(host).then(
        (addresses) => addresses.length === 0 || addresses.some((a) => isOwnAddress(a, interfaces)),
        // Cannot tell where it goes: fails closed.
        () => true,
    );
}

function appsPortsOf(deps: OwnServerDeps): Set<number> {
    return (deps.appsPorts ?? (() => new Set([APPS_PORT])))();
}

// Which own local server the URL goes to: "server" (apps/server, /rpc/*),
// "apps" (the Rowboat Apps server and its host API) or null. An http(s)/ws(s)
// URL on one of their ports whose host is a loopback form, the machine's own
// name, one of its own addresses, or a name that resolves to one of these (or
// cannot be resolved: fails closed).
export async function ownTargetKind(url: URL, deps: OwnServerDeps = defaultOwnServerDeps): Promise<"server" | "apps" | null> {
    if (!WEB_PROTOCOLS.has(url.protocol)) return null;
    const port = effectivePort(url);
    const ports = await (deps.ports ?? ownServerPorts)();
    const kind = ports.has(port) ? "server" : appsPortsOf(deps).has(port) ? "apps" : null;
    if (kind === null) return null;
    const host = url.hostname.replace(/^\[|\]$/g, "");
    return (await hostIsOwn(host, deps)) ? kind : null;
}

// fetch-url: the own server and the whole apps server (fetch-url has no
// legitimate use for app pages; on Windows *.apps.localhost does not even
// resolve outside Chromium).
export async function isOwnServerTarget(url: URL, deps: OwnServerDeps = defaultOwnServerDeps): Promise<boolean> {
    return (await ownTargetKind(url, deps)) !== null;
}

// The apps server's host API: /_rowboat and everything below it. Checked on
// the decoded, lower-cased path and anywhere in it (fails closed for
// %5Frowboat, //_rowboat and the like).
export function isAppsHostApiPath(url: URL): boolean {
    let decoded: string;
    try {
        decoded = decodeURIComponent(url.pathname);
    } catch {
        return true;
    }
    return decoded.toLowerCase().includes("/_rowboat");
}

// The agent browser session: the own server always; on the apps server only
// the host API — app pages themselves still load (the browser pane is the
// user's browser too, and an app opened there is a page like any other).
export async function isAgentBrowserBlocked(url: URL, deps: OwnServerDeps = defaultOwnServerDeps): Promise<boolean> {
    const kind = await ownTargetKind(url, deps);
    return kind === "server" || (kind === "apps" && isAppsHostApiPath(url));
}

// Loopback/unspecified or one of the machine's own interface addresses (LAN,
// Tailscale, ...), whatever the port.
export function isOwnMachineAddress(ip: string, deps: Pick<OwnServerDeps, "interfaces"> = {}): boolean {
    const interfaces = new Set((deps.interfaces ?? defaultInterfaces)().map(normalizeAddress));
    return isOwnAddress(ip, interfaces);
}

// The connect-time half (fix round 2, DNS rebinding): is this resolved
// address, on this port, the own server (or the apps server)? Used by
// fetch-url's transport on the address it is about to connect to.
export async function isOwnServerAddress(ip: string, port: number, deps: OwnServerDeps = defaultOwnServerDeps): Promise<boolean> {
    const ports = await (deps.ports ?? ownServerPorts)();
    if (!ports.has(port) && !appsPortsOf(deps).has(port)) return false;
    return isOwnMachineAddress(ip, deps);
}

// Fix round 3 (review I-A): true when any of the strings carries a key that
// authenticates against the own server — the server key (anywhere in the
// string) or a scoped key (any token whose sha256 is in scoped-keys.json).
// Missing or unreadable key files count as no keys.
// Defence in depth only, NOT the barrier (review m-N3): the match is literal
// and is bypassed by percent-encoding one character, putting the key in the
// body, base64, or splitting it across headers. The barrier is the target
// check (no own-machine or private address, before the request and at
// connect); this only matters where the own server is reachable under another
// address (remote mode, a forwarder such as tailscale funnel or socat).
export async function carriesOwnServerCredential(values: string[], workDir: string = WorkDir): Promise<boolean> {
    let serverKey = "";
    try {
        serverKey = (await fs.readFile(path.join(workDir, "server-key"), "utf8")).trim();
    } catch {
        serverKey = "";
    }
    const scoped = new Set<string>();
    try {
        const raw = JSON.parse(await fs.readFile(path.join(workDir, "scoped-keys.json"), "utf8")) as { keys?: unknown };
        for (const k of Array.isArray(raw.keys) ? raw.keys : []) {
            const hash = (k as { sha256?: unknown } | null)?.sha256;
            if (typeof hash === "string") scoped.add(hash.toLowerCase());
        }
    } catch {
        // no scoped keys
    }
    if (!serverKey && scoped.size === 0) return false;
    for (const value of values) {
        if (serverKey && value.includes(serverKey)) return true;
        if (scoped.size === 0) continue;
        for (const token of value.split(/[\s,;&?=:"']+/)) {
            if (token.length < 16) continue;
            const digest = crypto.createHash("sha256").update(token).digest("hex");
            if (scoped.has(digest)) return true;
        }
    }
    return false;
}

// Second layer only, for executeCommand (fix round 2): a command whose text
// names a loopback host form together with an own-server port, or the literal
// "/rpc/" path, is for the human. Text matching is evadable (own LAN address
// or host name, a DNS name, ports or hosts assembled by the shell, a script
// file); the first layer is that the server key is protected and fetch-url /
// the browser session cannot reach the server at all.
const LOOPBACK_HOST_FORMS = [
    /localhost/i,
    /(?<![\d.])127\.\d/,
    /(?<![\d.])0\.0\.0\.0(?![\d.])/,
    /::1(?![\d:a-f])/i,
    /\[::\]/,
    /(?<![\da-f])(?:0{1,4}:){7}0{0,3}1(?![\da-f:])/i,
    /::ffff:7f/i,
    /0x7f/i,
    /2130706433/,
];

export async function commandMentionsOwnServer(
    command: string,
    ports: () => Promise<Set<number>> = ownServerPorts,
): Promise<boolean> {
    // Same normalisation as the protected-name check: cmd.exe drops "^", the
    // shells drop quotes and (bash) "\" escapes.
    const variants = [command.replace(/[\^"'`]/g, ""), command.replace(/[\^"'`\\]/g, "")];
    if (variants.some((v) => /\/rpc\//i.test(v))) return true;
    const ownPorts = await ports();
    return variants.some((v) => {
        if (!LOOPBACK_HOST_FORMS.some((form) => form.test(v))) return false;
        for (const match of v.matchAll(/(?<!\d)\d{2,5}(?!\d)/g)) {
            if (ownPorts.has(Number(match[0]))) return true;
        }
        return false;
    });
}

// The part of an Electron Session this module uses (kept structural so core
// does not depend on electron and tests can pass a fake).
export interface RequestDetailsLike {
    url: string;
    resourceType?: string;
}
export interface OwnServerBlockSession {
    webRequest: {
        onBeforeRequest(
            listener: (details: RequestDetailsLike, callback: (response: { cancel?: boolean }) => void) => void,
        ): void;
    };
}

// Network-level block for a session the agent can drive (the embedded
// browser): every request to the own server, and to the apps server's host
// API (/_rowboat/..., fix round 3), is cancelled — every resource
// type, form posts from data: pages, each redirect hop (Chromium asks again
// for the redirect target) and WebSockets. Only for the sessions it is
// installed on; the app's own renderer session must keep reaching its server.
// Electron keeps one onBeforeRequest listener per session: the caller makes
// sure nothing else registers one there.
export function installOwnServerBlock(session: OwnServerBlockSession, deps: OwnServerDeps = defaultOwnServerDeps): void {
    const blockDeps: OwnServerDeps = { ...deps, ports: memoizePorts(deps.ports ?? ownServerPorts, 2000) };
    session.webRequest.onBeforeRequest((details, callback) => {
        let decision: Promise<boolean>;
        try {
            decision = isAgentBrowserBlocked(new URL(details.url), blockDeps);
        } catch {
            decision = Promise.resolve(true);
        }
        decision.then(
            (own) => callback(own ? { cancel: true } : {}),
            () => callback({ cancel: true }),
        );
    });
}
