// Modified for VibeMind (Flissel/rowboat vibemind): new module (Teil E, fix
// round 4). Execution-time enforcement of the channel quarantine
// knowledge/Kanal/ (see knowledge/quarantine.ts):
//
//   channel  — a run started from a mobile channel: its file writes must stay
//              inside knowledge/Kanal/ (both sides of a rename, the target of
//              a copy). Reads are untouched (the channel guard rules apply).
//   blocked  — every other run that is not a desktop user chat (headless
//              agents, todo / code sessions, scheduled agents, legacy engine):
//              no builtin may touch knowledge/Kanal/ or the turn transcripts
//              in storage/turns/ (channel turns live there too); grep / glob /
//              list results drop such entries.
//   null     — a desktop user chat (or a child it spawned): unrestricted.
//
// The mode is derived from ToolContext: `channel` marks channel runs, and the
// runtime sets `autorunContentForbidden` exactly for runs that are not a
// desktop user chat.

import fs from "node:fs/promises";
import path from "node:path";
import { WorkDir } from "../../config/config.js";
import { isPathInside, resolveFilePathForPermission } from "../../filesystem/files.js";
import { isQuarantinedUnder, QUARANTINE_FOLDER, QUARANTINE_REL } from "../../knowledge/quarantine.js";

export type QuarantineMode = "channel" | "blocked";

export function quarantineMode(ctx?: {
    channel?: unknown;
    autorunContentForbidden?: boolean;
}): QuarantineMode | null {
    if (ctx?.channel) return "channel";
    if (ctx?.autorunContentForbidden) return "blocked";
    return null;
}

// Blocked runs fail closed. Every string anywhere in a builtin's input
// (top-level, nested objects, arrays) is examined:
//   - Path arguments (isPathKey: path, from, to, cwd, searchPath, outputPath,
//     filePath, file, files[], paths[], projectDir, toPath, ... — the guard
//     test derives them from every builtin schema) are ALWAYS checked: a value
//     with control / invisible characters or longer than MAX_PATH_LENGTH is
//     refused outright; otherwise the raw, trimmed and NFKC-normalised forms
//     are resolved (realpath) and matched lexically.
//   - Every other string, at any length, is trimmed, NFKC-normalised and
//     split into tokens; a token with a `kanal` path segment or a
//     `storage/turns` segment refuses the call (no resolve). Short single-line
//     values are resolved as well (symlinks).
//   - Inputs nested deeper than MAX_DEPTH or with more than MAX_STRINGS
//     strings are refused instead of being skipped.
const MAX_PATH_LENGTH = 4096;
const MAX_DEPTH = 10;
const MAX_STRINGS = 50_000;

const PATH_KEY_NAMES = new Set([
    "from", "to", "cwd", "target", "source", "destination", "dest",
    "skillname", "agentname", "fileglob", "projectdir", "topath", "appfolder",
    // bg-task folder name under bg-tasks/.
    "slug", "taskslug",
]);
const PATH_KEY_SUFFIX_RE = /(path|paths|dir|dirs|directory|directories|folder|folders|file|files|filename|filenames)$/i;

// True for an argument name that carries a file system path.
export function isPathKey(key: string): boolean {
    return PATH_KEY_NAMES.has(key.toLowerCase()) || PATH_KEY_SUFFIX_RE.test(key);
}

// C0/C1 controls, zero-width and bidi characters.
// eslint-disable-next-line no-control-regex
const UNSAFE_CHARS_RE = /[\u0000-\u001f\u007f-\u009f\u200b-\u200f\u2028\u2029\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff]/;
// eslint-disable-next-line no-control-regex
const UNSAFE_CHARS_GLOBAL_RE = /[\u0000-\u001f\u007f-\u009f\u200b-\u200f\u2028\u2029\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff]/g;

function normalizeText(value: string): string {
    // Invisible characters vanish (a consumer could strip them), controls and
    // whitespace become token breaks.
    return value
        .normalize("NFKC")
        .replace(/[\u200b-\u200d\u2060\ufeff]/g, "")
        .replace(UNSAFE_CHARS_GLOBAL_RE, " ")
        .trim();
}

// Lexical match: a token with a `kanal` path segment, or storage/turns.
export function mentionsQuarantine(value: string): boolean {
    const tokens = normalizeText(value).split(/[\s"'`<>()[\]{},;|=]+/);
    for (const token of tokens) {
        if (!/[\\/]/.test(token)) continue;
        const norm = path.posix.normalize(token.replace(/\\/g, "/"));
        const segments = norm.toLowerCase().split("/");
        if (segments.includes("kanal")) return true;
        const turns = segments.indexOf("turns");
        if (turns > 0 && segments[turns - 1] === "storage") return true;
    }
    return false;
}

interface Collected {
    at: string;
    key: string;
    value: string;
}

class ArgumentShapeError extends Error {}

function collectStrings(value: unknown, at: string, key: string, out: Collected[], depth = 0): void {
    if (depth > MAX_DEPTH) {
        throw new ArgumentShapeError(`Argumente zu tief verschachtelt (mehr als ${MAX_DEPTH} Ebenen) — refused for background runs (channel quarantine).`);
    }
    if (typeof value === "string") {
        if (out.length >= MAX_STRINGS) {
            throw new ArgumentShapeError(`Zu viele Argumente (mehr als ${MAX_STRINGS} Zeichenketten) — refused for background runs (channel quarantine).`);
        }
        out.push({ at, key, value });
        return;
    }
    if (Array.isArray(value)) {
        // Array elements inherit the array's name (files[], paths[]).
        value.forEach((item, index) => collectStrings(item, `${at}[${index}]`, key, out, depth + 1));
        return;
    }
    if (value && typeof value === "object") {
        for (const [childKey, item] of Object.entries(value as Record<string, unknown>)) {
            collectStrings(item, at ? `${at}.${childKey}` : childKey, childKey, out, depth + 1);
        }
    }
}

// Channel runs: the arguments a write builtin modifies.
const CHANNEL_WRITE_ARGS: Record<string, string[]> = {
    "file-writeText": ["path"],
    "file-editText": ["path"],
    "file-mkdir": ["path"],
    "file-remove": ["path"],
    "file-rename": ["from", "to"],
    "file-copy": ["to"],
};

// Shell commands naming the quarantine or the transcripts. A heuristic only:
// globs / find / grep -r over knowledge/ can still reach the folder (see the
// round-4 residuals); the file builtins above are the real boundary.
const SHELL_QUARANTINE_RE = new RegExp(`${QUARANTINE_FOLDER}|storage[\\\\/]+turns`, "i");

export interface QuarantineDeps {
    workspaceRoot?: () => Promise<string>;
    resolvePath?: (inputPath: string) => Promise<string>;
}

async function defaultWorkspaceRoot(): Promise<string> {
    try {
        return await fs.realpath(WorkDir);
    } catch {
        return path.resolve(WorkDir);
    }
}

async function defaultResolvePath(inputPath: string): Promise<string> {
    return (await resolveFilePathForPermission(inputPath)).canonicalPath;
}

export interface QuarantineChecker {
    // Error text when the call breaks the quarantine rule for `mode`, else null.
    toolError(toolName: string, args: Record<string, unknown>, mode: QuarantineMode): Promise<string | null>;
    // For "blocked" runs: true when a grep / glob / list entry may be shown.
    readFilter(): (absPath: string) => Promise<boolean>;
}

export function createQuarantineChecker(deps: QuarantineDeps = {}): QuarantineChecker {
    const workspaceRoot = deps.workspaceRoot ?? defaultWorkspaceRoot;
    const resolvePath = deps.resolvePath ?? defaultResolvePath;

    const quarantined = (root: string, abs: string) =>
        isQuarantinedUnder(path.join(root, "knowledge"), abs);
    const transcript = (root: string, abs: string) =>
        isPathInside(path.join(root, "storage", "turns"), abs);

    async function blockedError(toolName: string, args: Record<string, unknown>): Promise<string | null> {
        if (toolName === "executeCommand" && typeof args.command === "string" && SHELL_QUARANTINE_RE.test(args.command)) {
            return `Commands touching ${QUARANTINE_REL} or storage/turns/ are not allowed in background runs (channel quarantine).`;
        }
        const strings: Collected[] = [];
        try {
            collectStrings(args, "", "", strings);
        } catch (error) {
            if (error instanceof ArgumentShapeError) return error.message;
            throw error;
        }
        const root = await workspaceRoot();
        const quarantineMessage = (value: string, at: string) =>
            `"${value.slice(0, 200)}" (${at}) points into the channel quarantine ${QUARANTINE_REL} or storage/turns/; only the desktop chat may open it.`;
        const seen = new Set<string>();
        for (const { at, key, value } of strings) {
            const pathArg = isPathKey(key);
            if (pathArg && (UNSAFE_CHARS_RE.test(value) || value.length > MAX_PATH_LENGTH)) {
                return `Path argument ${at} contains control characters or is longer than ${MAX_PATH_LENGTH} characters — refused for background runs (channel quarantine).`;
            }
            if (mentionsQuarantine(value)) {
                return quarantineMessage(value, at);
            }
            // Resolve path arguments always; other strings only when they
            // could be a path at all (single line, bounded).
            if (!pathArg && (value.length > MAX_PATH_LENGTH || UNSAFE_CHARS_RE.test(value))) continue;
            for (const candidate of new Set([value, value.trim(), normalizeText(value)])) {
                if (!candidate || seen.has(candidate)) continue;
                seen.add(candidate);
                let resolved: string;
                try {
                    resolved = await resolvePath(candidate);
                } catch {
                    continue; // not resolvable as a path
                }
                if (quarantined(root, resolved)) {
                    return `"${candidate}" (${at}) is in the channel quarantine ${QUARANTINE_REL}; only the desktop chat may open it.`;
                }
                if (transcript(root, resolved)) {
                    return `"${candidate}" (${at}) is in storage/turns/ (chat transcripts, including channel runs); background runs may not read transcripts.`;
                }
            }
        }
        return null;
    }

    async function channelError(toolName: string, args: Record<string, unknown>): Promise<string | null> {
        const keys = CHANNEL_WRITE_ARGS[toolName];
        if (!keys) return null;
        const root = await workspaceRoot();
        for (const key of keys) {
            const value = args[key];
            if (typeof value !== "string" || value.length === 0) {
                return "A path is required.";
            }
            if (!quarantined(root, await resolvePath(value))) {
                return `Channel runs may only write under ${QUARANTINE_REL}; "${value}" is outside.`;
            }
        }
        return null;
    }

    return {
        toolError(toolName, args, mode) {
            return mode === "channel" ? channelError(toolName, args) : blockedError(toolName, args);
        },
        readFilter() {
            return async (absPath) => {
                try {
                    const root = await workspaceRoot();
                    const resolved = await resolvePath(absPath);
                    return !quarantined(root, resolved) && !transcript(root, resolved);
                } catch {
                    return false;
                }
            };
        },
    };
}

let defaultChecker: QuarantineChecker | null = null;

export function quarantineChecker(): QuarantineChecker {
    defaultChecker ??= createQuarantineChecker();
    return defaultChecker;
}
