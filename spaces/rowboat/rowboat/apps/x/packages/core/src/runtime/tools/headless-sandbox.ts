// Modified for VibeMind (Flissel/rowboat vibemind): new module (Teil E, fix
// round 3). Sandbox for runs no user is watching: headless turns
// (sessionId null — note tagging, curation, graph building, live-note,
// inline-task, background-task, pre-built and scheduled agents), legacy
// engine runs (app-initiated) and channel runs. Their input is untrusted
// (notes, mail, web pages) and they run with automatic permission, so a
// prompt injection must not turn them into a way to read secrets or to plant
// work for other background agents.
//
//   Reads:  denied for credentials and app state (config/, keys, channels/,
//           .electron-data, .trash, storage/, bg-tasks/, *.env*, *token*),
//           except the run's own readDirs / ownDirs / ownFiles.
//   Writes: only under knowledge/ (not Agent Notes, no dot segments, no
//           *.env / *token* names), plus the run's ownDirs / ownFiles.
//   Autorun notes: a file that already carries a trigger (live:, live_note,
//           task / tell-rowboat fence) may only be changed by the run whose
//           ownFiles name it (the live-note / inline-task agent's target).
//
// Enforced centrally in execTool for the file builtins and via a read
// filter in file-grep / file-glob. Desktop user turns never carry a sandbox.

import fs from "node:fs/promises";
import path from "node:path";
import { WorkDir } from "../../config/config.js";
import { isPathInside, resolveFilePathForPermission } from "../../filesystem/files.js";
import { hasAutorunTrigger } from "../../knowledge/autorun-triggers.js";

export interface HeadlessSandbox {
    // Workspace-relative files this run may write even outside knowledge/
    // and even when they carry an autorun trigger (its own target note).
    ownFiles: string[];
    // Workspace-relative directories this run may read and write.
    ownDirs: string[];
    // Workspace-relative directories this run may read although they are
    // otherwise protected (a bg-task's own folder).
    readDirs: string[];
}

export const EMPTY_SANDBOX: HeadlessSandbox = { ownFiles: [], ownDirs: [], readDirs: [] };

const SECRET_ENTRIES = [
    "config",
    "server-key",
    "cipher-key",
    "embed.json",
    ".electron-data",
    "channels",
    ".trash",
    "storage",
    "bg-tasks",
];

const WRITE_ROOT = "knowledge";
const AGENT_NOTES = "agent notes";

function isSecretName(segment: string): boolean {
    const s = segment.toLowerCase();
    return s.endsWith(".env") || s.startsWith(".env") || s.includes("token");
}

type Op = "read" | "write";

function targetsOf(toolName: string, args: Record<string, unknown>): Array<{ op: Op; path: string }> | null {
    const str = (v: unknown) => (typeof v === "string" && v.length > 0 ? v : undefined);
    switch (toolName) {
        case "file-exists":
        case "file-stat":
        case "file-readText":
        case "parseFile":
        case "LLMParse":
            return [{ op: "read", path: str(args.path) ?? "" }];
        case "file-list":
            return [{ op: "read", path: str(args.path) ?? "." }];
        case "file-glob":
            return [{ op: "read", path: str(args.cwd) ?? "." }];
        case "file-grep":
            return [{ op: "read", path: str(args.searchPath) ?? "." }];
        case "file-writeText":
        case "file-editText":
        case "file-mkdir":
        case "file-remove":
            return [{ op: "write", path: str(args.path) ?? "" }];
        case "file-rename":
            return [
                { op: "write", path: str(args.from) ?? "" },
                { op: "write", path: str(args.to) ?? "" },
            ];
        case "file-copy":
            return [
                { op: "read", path: str(args.from) ?? "" },
                { op: "write", path: str(args.to) ?? "" },
            ];
        default:
            return null;
    }
}

export interface SandboxDeps {
    workspaceRoot?: () => Promise<string>;
    resolvePath?: (inputPath: string) => Promise<string>;
    readText?: (absPath: string) => Promise<string | null>;
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

async function defaultReadText(absPath: string): Promise<string | null> {
    try {
        return await fs.readFile(absPath, "utf8");
    } catch {
        return null;
    }
}

export interface SandboxChecker {
    // Error text when the tool call is outside the sandbox, else null.
    toolError(toolName: string, args: Record<string, unknown>, sandbox: HeadlessSandbox): Promise<string | null>;
    // For grep / glob results: true when the match may be read.
    readFilter(sandbox: HeadlessSandbox): (absPath: string) => Promise<boolean>;
}

export function createSandboxChecker(deps: SandboxDeps = {}): SandboxChecker {
    const workspaceRoot = deps.workspaceRoot ?? defaultWorkspaceRoot;
    const resolvePath = deps.resolvePath ?? defaultResolvePath;
    const readText = deps.readText ?? defaultReadText;

    const under = (root: string, rels: string[], target: string) =>
        rels.some((rel) => isPathInside(path.resolve(root, rel), target));
    const isOwnFile = (root: string, sandbox: HeadlessSandbox, target: string) =>
        sandbox.ownFiles.some((rel) => path.relative(path.resolve(root, rel), target) === "");

    function readable(root: string, target: string, sandbox: HeadlessSandbox): boolean {
        if (isOwnFile(root, sandbox, target) || under(root, [...sandbox.ownDirs, ...sandbox.readDirs], target)) {
            return true;
        }
        if (!isPathInside(root, target)) {
            // Outside the workspace the permission checker decides; only
            // secret-looking names are refused here.
            return !isSecretName(path.basename(target));
        }
        const rel = path.relative(root, target);
        const segments = rel === "" ? [] : rel.split(path.sep);
        if (SECRET_ENTRIES.some((entry) => isPathInside(path.join(root, entry), target))) return false;
        return !segments.some(isSecretName);
    }

    function writable(root: string, target: string, sandbox: HeadlessSandbox): boolean {
        if (isOwnFile(root, sandbox, target) || under(root, sandbox.ownDirs, target)) {
            return true;
        }
        const writeRoot = path.join(root, WRITE_ROOT);
        if (!isPathInside(writeRoot, target) || target === writeRoot) return false;
        const inner = path.relative(writeRoot, target).split(path.sep);
        if (inner[0]?.toLowerCase() === AGENT_NOTES) return false;
        return !inner.some((segment) => segment.startsWith(".") || isSecretName(segment));
    }

    return {
        async toolError(toolName, args, sandbox) {
            const targets = targetsOf(toolName, args);
            if (!targets) return null;
            const root = await workspaceRoot();
            for (const target of targets) {
                if (!target.path) return "A path is required.";
                const resolved = await resolvePath(target.path);
                if (target.op === "read" && !readable(root, resolved, sandbox)) {
                    return `Path "${target.path}" is not readable for background runs (credentials / app state).`;
                }
                if (target.op === "write") {
                    if (!writable(root, resolved, sandbox)) {
                        return `Path "${target.path}" is not writable for background runs — they may only write notes under knowledge/.`;
                    }
                    if (!isOwnFile(root, sandbox, resolved)) {
                        const current = await readText(resolved);
                        if (current !== null && hasAutorunTrigger(current)) {
                            return `"${target.path}" starts a background agent (live / live_note frontmatter or a task block); only its own agent may change it.`;
                        }
                    }
                }
            }
            return null;
        },
        readFilter(sandbox) {
            return async (absPath) => {
                try {
                    const root = await workspaceRoot();
                    return readable(root, await resolvePath(absPath), sandbox);
                } catch {
                    return false;
                }
            };
        },
    };
}

let defaultChecker: SandboxChecker | null = null;

export function sandboxChecker(): SandboxChecker {
    defaultChecker ??= createSandboxChecker();
    return defaultChecker;
}
