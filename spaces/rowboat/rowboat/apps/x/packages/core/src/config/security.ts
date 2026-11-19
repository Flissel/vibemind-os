// Modified for VibeMind (Flissel/rowboat vibemind): in-memory allow-lists, no mtime re-read (ticket #330, Task 17).
import path from "path";
import fs from "fs";
import fsPromises from "fs/promises";
import { WorkDir } from "./config.js";

export const SECURITY_CONFIG_PATH = path.join(WorkDir, "config", "security.json");

const DEFAULT_ALLOW_LIST = [
    "agent-slack",
    "awk",
    "basename",
    "cat",
    "cut",
    "date",
    "df",
    "diff",
    "dirname",
    "du",
    "echo",
    "env",
    "file",
    "find",
    "grep",
    "head",
    "hostname",
    "jq",
    "ls",
    "printenv",
    "printf",
    "pwd",
    "readlink",
    "realpath",
    "sort",
    "stat",
    "tail",
    "tree",
    "uname",
    "uniq",
    "wc",
    "which",
    "whoami",
    "yq"
]

export type FileAccessOperation = "read" | "list" | "search" | "write" | "delete";

export type FileAccessGrant = {
    operation: FileAccessOperation;
    pathPrefix: string;
};

type SecurityState = { allowedCommands: string[]; allowedFileAccess: FileAccessGrant[] };

// Modified for VibeMind (Flissel/rowboat vibemind): ticket #330, Task 17.
// The allow-lists the app enforces live in memory, loaded once (at startup,
// or at first use). They change only through addToSecurityConfig /
// addFileAccessGrant (a human "always allow") and reloadSecurityConfig (the
// human write paths: Settings / workspace:* handlers). The file is no longer
// re-read when its mtime changes: content written to it any other way (a
// shell command, another program) takes effect only after a restart or a
// Settings save.
let state: SecurityState | null = null;

function loadedState(): SecurityState {
    if (state === null) state = readSecurityConfig();
    return state;
}

export async function addToSecurityConfig(commands: string[]): Promise<void> {
    ensureSecurityConfigSync();
    const live = loadedState();
    // The file keeps what it holds (as before); only the human's commands
    // are added to the live state, never a raw write since load.
    const current = readSecurityConfig();
    const added: string[] = [];
    const merged = new Set(current.allowedCommands);
    for (const cmd of commands) {
        const normalized = cmd.trim().toLowerCase();
        if (normalized) {
            merged.add(normalized);
            added.push(normalized);
        }
    }
    await fsPromises.writeFile(
        SECURITY_CONFIG_PATH,
        JSON.stringify({
            allowedCommands: Array.from(merged).sort(),
            allowedFileAccess: current.allowedFileAccess,
        }, null, 2) + "\n",
        "utf8",
    );
    state = {
        allowedCommands: Array.from(new Set([...live.allowedCommands, ...added])),
        allowedFileAccess: live.allowedFileAccess,
    };
}

export async function addFileAccessGrant(grant: FileAccessGrant): Promise<void> {
    ensureSecurityConfigSync();
    const live = loadedState();
    const current = readSecurityConfig();
    const normalizedGrant = normalizeFileAccessGrant(grant);
    const exists = current.allowedFileAccess.some(existing =>
        existing.operation === normalizedGrant.operation
        && existing.pathPrefix === normalizedGrant.pathPrefix
    );
    const allowedFileAccess = exists
        ? current.allowedFileAccess
        : [...current.allowedFileAccess, normalizedGrant].sort((a, b) =>
            `${a.operation}:${a.pathPrefix}`.localeCompare(`${b.operation}:${b.pathPrefix}`)
        );
    await fsPromises.writeFile(
        SECURITY_CONFIG_PATH,
        JSON.stringify({
            allowedCommands: current.allowedCommands,
            allowedFileAccess,
        }, null, 2) + "\n",
        "utf8",
    );
    const known = live.allowedFileAccess.some(existing =>
        existing.operation === normalizedGrant.operation
        && existing.pathPrefix === normalizedGrant.pathPrefix
    );
    state = {
        allowedCommands: live.allowedCommands,
        allowedFileAccess: known ? live.allowedFileAccess : [...live.allowedFileAccess, normalizedGrant],
    };
}

/**
 * Async function to ensure security config file exists.
 * Called explicitly at app startup via initConfigs().
 */
export async function ensureSecurityConfig(): Promise<void> {
    try {
        await fsPromises.access(SECURITY_CONFIG_PATH);
    } catch {
        await fsPromises.writeFile(
            SECURITY_CONFIG_PATH,
            JSON.stringify(DEFAULT_ALLOW_LIST, null, 2) + "\n",
            "utf8",
        );
    }
}

/**
 * Sync version for internal use by readSecurityConfig().
 */
function ensureSecurityConfigSync() {
    if (!fs.existsSync(SECURITY_CONFIG_PATH)) {
        fs.writeFileSync(
            SECURITY_CONFIG_PATH,
            JSON.stringify(DEFAULT_ALLOW_LIST, null, 2) + "\n",
            "utf8",
        );
    }
}

function normalizeList(commands: unknown[]): string[] {
    const seen = new Set<string>();
    for (const entry of commands) {
        if (typeof entry !== "string") continue;
        const normalized = entry.trim().toLowerCase();
        if (!normalized) continue;
        seen.add(normalized);
    }

    return Array.from(seen);
}

function normalizeFileAccessGrant(grant: FileAccessGrant): FileAccessGrant {
    return {
        operation: grant.operation,
        pathPrefix: path.resolve(grant.pathPrefix),
    };
}

function normalizeFileAccessList(grants: unknown[]): FileAccessGrant[] {
    const seen = new Set<string>();
    const normalized: FileAccessGrant[] = [];
    for (const entry of grants) {
        if (!entry || typeof entry !== "object") continue;
        const maybeGrant = entry as Record<string, unknown>;
        const operation = maybeGrant.operation;
        const pathPrefix = maybeGrant.pathPrefix;
        if (
            operation !== "read"
            && operation !== "list"
            && operation !== "search"
            && operation !== "write"
            && operation !== "delete"
        ) {
            continue;
        }
        if (typeof pathPrefix !== "string" || !pathPrefix.trim()) continue;
        const grant = normalizeFileAccessGrant({ operation, pathPrefix });
        const key = `${grant.operation}:${grant.pathPrefix}`;
        if (seen.has(key)) continue;
        seen.add(key);
        normalized.push(grant);
    }
    return normalized;
}

function parseSecurityPayload(payload: unknown): { allowedCommands: string[]; allowedFileAccess: FileAccessGrant[] } {
    if (Array.isArray(payload)) {
        return { allowedCommands: normalizeList(payload), allowedFileAccess: [] };
    }

    if (payload && typeof payload === "object") {
        const maybeObject = payload as Record<string, unknown>;
        const allowedFileAccess = Array.isArray(maybeObject.allowedFileAccess)
            ? normalizeFileAccessList(maybeObject.allowedFileAccess)
            : [];

        if (Array.isArray(maybeObject.allowedCommands) || Array.isArray(maybeObject.allowedFileAccess)) {
            return {
                allowedCommands: Array.isArray(maybeObject.allowedCommands)
                    ? normalizeList(maybeObject.allowedCommands)
                    : [],
                allowedFileAccess,
            };
        }

        const dynamicList = Object.entries(maybeObject)
            .filter(([, value]) => Boolean(value))
            .map(([key]) => key);

        return {
            allowedCommands: normalizeList(dynamicList),
            allowedFileAccess,
        };
    }

    return { allowedCommands: [], allowedFileAccess: [] };
}

// A broken file (or broken content) falls back to the defaults.
function parseSecurityContent(configContent: string): SecurityState {
    try {
        return parseSecurityPayload(JSON.parse(configContent));
    } catch (error) {
        console.warn(`Failed to read security config at ${SECURITY_CONFIG_PATH}: ${error instanceof Error ? error.message : error}`);
        return { allowedCommands: DEFAULT_ALLOW_LIST, allowedFileAccess: [] };
    }
}

function readSecurityConfig(): SecurityState {
    let configContent: string;
    try {
        // Inside the try: a missing config/ folder (removed by the human)
        // falls back to the defaults instead of throwing.
        ensureSecurityConfigSync();
        configContent = fs.readFileSync(SECURITY_CONFIG_PATH, "utf8");
    } catch (error) {
        console.warn(`Failed to read security config at ${SECURITY_CONFIG_PATH}: ${error instanceof Error ? error.message : error}`);
        return { allowedCommands: DEFAULT_ALLOW_LIST, allowedFileAccess: [] };
    }
    return parseSecurityContent(configContent);
}

export function getSecurityAllowList(): string[] {
    return loadedState().allowedCommands;
}

export function getFileAccessAllowList(): FileAccessGrant[] {
    return loadedState().allowedFileAccess;
}

/**
 * Replaces the live allow-lists. Only for startup and the human write paths
 * (human-config-writes.ts): with `content` (what the human just wrote) that
 * content is applied, otherwise the file as it is now.
 */
export function reloadSecurityConfig(content?: string): void {
    state = content === undefined ? readSecurityConfig() : parseSecurityContent(content);
}

/**
 * Revocation only (the human removed or moved security.json, or changed a
 * folder that holds it): no new content is applied. The live commands shrink
 * to those that are both live now and in the default list, and every file
 * grant is dropped. That is never wider than the current state nor than what
 * a restart would load (the re-created default file), so it fails closed in
 * both directions: `*` and grants the human removed with the file are gone,
 * and a default command the human had taken out does not come back.
 */
export function revokeSecurityConfigToDefaults(): void {
    const live = loadedState();
    state = {
        allowedCommands: live.allowedCommands.filter((cmd) => DEFAULT_ALLOW_LIST.includes(cmd)),
        allowedFileAccess: [],
    };
}

/** Drops the live state; the next use loads the file again (tests). */
export function resetSecurityAllowListCache() {
    state = null;
}
