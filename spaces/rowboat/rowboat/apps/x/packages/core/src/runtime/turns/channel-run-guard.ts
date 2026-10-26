// Modified for VibeMind (Flissel/rowboat vibemind): new module — the central
// guard for channel-origin turns (Telegram / WhatsApp bridge, "Teil E").
//
// Operator rule: a turn started from a mobile channel gets NO tool with
// external effect. It may read the workspace (knowledge, todo, notes) and
// write notes only into the quarantine knowledge/Kanal/ plus plain to-dos,
// nothing else. The bridge marks such turns with
// turn_created.config.channel; the runtime consults this guard
//   1. at createTurn, to drop every tool the run may not use from the
//      persisted snapshot (and from loadSkill tool additions), so the model
//      is never offered them, and
//   2. before the permission checker for every tool call, so a call that
//      slips through anyway (a stale/inherited snapshot, a hallucinated
//      name) is refused with a clear message instead of executing.
// Execution-time layers ride ToolContext.channel: file-grep / file-glob
// filter every match through readFilter(), fetch-url uses channel-fetch.ts
// (no private hosts, manual redirects).
// The guard is an ALLOWLIST: a builtin not named below is unavailable in
// channel runs — new catalog entries stay locked out until reviewed here.
// Desktop (non-channel) turns never reach this module.

import fs from "node:fs/promises";
import path from "node:path";
import type { z } from "zod";
import type { ToolDescriptor } from "@x/shared/dist/turns.js";
import { WorkDir } from "../../config/config.js";
import { isPathInside, resolveFilePathForPermission } from "../../filesystem/files.js";
import { hasAutorunTrigger, hasLiveFrontmatter } from "../../knowledge/autorun-triggers.js";
import { resultingWriteContent } from "../../knowledge/autorun-write-check.js";
import { isQuarantineFolderName, QUARANTINE_REL } from "../../knowledge/quarantine.js";

export { hasLiveFrontmatter };

export interface ChannelRunPolicy {
    // MCP tools ("server:tool") treated as read-only: auto-approved in
    // channel runs without the LLM permission classifier. Every other MCP
    // tool is refused.
    readOnlyMcpTools: string[];
}

export interface ChannelToolCall {
    toolId: string;
    toolName: string;
    input: unknown;
}

export type ChannelGuardDecision =
    // Refused outright: the runtime records an error result, nothing runs.
    | { kind: "deny"; reason: string }
    // Approved by the guard itself: no checker, no classifier.
    | { kind: "allow" }
    // Allowed by the guard; the regular permission checker still applies.
    | { kind: "default" };

export interface IChannelRunGuard {
    offers(descriptor: z.infer<typeof ToolDescriptor>, policy: ChannelRunPolicy): boolean;
    check(call: ChannelToolCall, policy: ChannelRunPolicy): Promise<ChannelGuardDecision>;
}

// Kept in sync with real-agent-resolver's ASK_HUMAN_TOOL (not imported: that
// module pulls the whole agent assembly into the runtime's import graph).
const ASK_HUMAN = "ask-human";

// Builtins a channel run may use. Everything else — executeCommand,
// addMcpServer, listMcpServers (prints server env/headers), code agents,
// background tasks, browser, composio, spawn-agent (its child turn would
// not carry the channel flag), save-to-memory (lands in every desktop
// system prompt), image/voice/deck/spreadsheet writers, ... — is not offered
// and refused if called.
const CHANNEL_BUILTINS = new Set<string>([
    "loadSkill",
    ASK_HUMAN,
    "file-getRoot",
    "file-exists",
    "file-stat",
    "file-list",
    "file-readText",
    "file-writeText",
    "file-editText",
    "file-mkdir",
    "file-rename",
    "file-copy",
    "file-glob",
    "file-grep",
    "parseFile",
    "LLMParse",
    "web-search",
    "fetch-url",
    "listMcpTools",
    "executeMcpTool",
    "list-models",
    "todo-add",
    "home-status",
    "whiteboard-read",
    "list_scheduled",
]);

// Workspace entries a channel run may never touch (read, list, search or
// write): credentials, app-internal state, deleted files.
const SENSITIVE_ENTRIES = [
    "config",
    "server-key",
    "cipher-key",
    "embed.json",
    ".electron-data",
    // telegram-state.json and the WhatsApp linked-device credentials.
    "channels",
    ".trash",
];

// The only workspace subtree a channel run may modify is the quarantine
// knowledge/Kanal/ (fix round 4): nothing autonomous reads it, so a note a
// prompt-injected channel run writes cannot steer a background agent. To-dos
// are written through todo-add, never with file tools. todo-propose is not
// offered: its entries feed the scheduled Morning planner (round-4 review I1).
const WRITE_ROOT = "knowledge";

// Appended to the system prompt of every channel turn (runtime createTurn).
export const CHANNEL_RUN_INSTRUCTIONS = [
    "# Mobile channel run",
    `This turn came from a mobile channel. Save every note you create under ${QUARANTINE_REL} (e.g. ${QUARANTINE_REL}Docs/<Topic> <YYYY-MM-DD>.md or ${QUARANTINE_REL}Dashboards/…) — writes anywhere else, including edits to existing notes, are refused.`,
    "Notes there are not read by background agents; the user reviews and moves them on the desktop. For work that needs the desktop, add a plain to-do (\"Am Desktop: …\") without @rowboat.",
].join("\n");
// Loaded into every desktop system prompt — never writable from a channel.
const AGENT_NOTES = "agent notes";

// Same pattern the todo runner uses to decide that an item is delegated
// (todo/fileops.ts ROWBOAT_MENTION_RE): such an item starts a background
// run with the full desktop tool set.
const ROWBOAT_MENTION_RE = /(^|\s)@rowboat\b/i;

function isSensitiveSegment(segment: string): boolean {
    const s = segment.toLowerCase();
    return s.endsWith(".env") || s.startsWith(".env") || s.includes("token");
}

// A glob that could leave its cwd or that glob v13 would expand into other
// directories: braces (brace expansion), "..", absolute / UNC / drive paths,
// home expansion, backslashes, character classes and extglobs ([, ().
// Channel runs reject all of them outright (the read filter is the second
// layer).
export function isUnsafeChannelGlob(pattern: string | undefined): boolean {
    if (!pattern) return false;
    return (
        /[{}\\[(]/.test(pattern) ||
        pattern.includes("..") ||
        /^[\\/]/.test(pattern) ||
        pattern.startsWith("~") ||
        /^[a-zA-Z]:/.test(pattern)
    );
}

type FileOp = "read" | "list" | "search" | "write";

interface FileTarget {
    op: FileOp;
    path: string;
}

function str(value: unknown): string | undefined {
    return typeof value === "string" && value.length > 0 ? value : undefined;
}

function fileTargets(toolName: string, args: Record<string, unknown>): FileTarget[] | null {
    switch (toolName) {
        case "file-exists":
        case "file-stat":
        case "file-readText":
        case "parseFile":
        case "LLMParse":
            return [{ op: "read", path: str(args.path) ?? "" }];
        case "file-list":
            return [{
                op: args.recursive === true ? "search" : "list",
                path: str(args.path) ?? ".",
            }];
        case "file-glob":
            return [{ op: "search", path: str(args.cwd) ?? "." }];
        case "file-grep":
            return [{ op: "search", path: str(args.searchPath) ?? "." }];
        case "file-writeText":
        case "file-editText":
        case "file-mkdir":
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

function asRecord(input: unknown): Record<string, unknown> {
    return input && typeof input === "object" && !Array.isArray(input)
        ? (input as Record<string, unknown>)
        : {};
}

export interface ChannelRunGuardDeps {
    // Canonical (realpath'd) workspace root.
    workspaceRoot?: () => Promise<string>;
    // Canonical absolute path for a tool-supplied path (relative paths
    // resolve against the workspace, symlinks are followed).
    resolvePath?: (inputPath: string) => Promise<string>;
    // Current text of a canonical path; null when missing/unreadable.
    readText?: (absPath: string) => Promise<string | null>;
    // True when the canonical path is an existing directory.
    isDirectory?: (absPath: string) => Promise<boolean>;
}

async function defaultIsDirectory(absPath: string): Promise<boolean> {
    try {
        return (await fs.stat(absPath)).isDirectory();
    } catch {
        return false;
    }
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

export class ChannelRunGuard implements IChannelRunGuard {
    private readonly workspaceRoot: () => Promise<string>;
    private readonly resolvePath: (inputPath: string) => Promise<string>;
    private readonly readText: (absPath: string) => Promise<string | null>;
    private readonly isDirectory: (absPath: string) => Promise<boolean>;

    constructor(deps: ChannelRunGuardDeps = {}) {
        this.workspaceRoot = deps.workspaceRoot ?? defaultWorkspaceRoot;
        this.resolvePath = deps.resolvePath ?? defaultResolvePath;
        this.readText = deps.readText ?? defaultReadText;
        this.isDirectory = deps.isDirectory ?? defaultIsDirectory;
    }

    offers(descriptor: z.infer<typeof ToolDescriptor>, policy: ChannelRunPolicy): boolean {
        const { toolId } = descriptor;
        if (toolId.startsWith("builtin:")) {
            return CHANNEL_BUILTINS.has(toolId.slice("builtin:".length));
        }
        if (toolId.startsWith("mcp:")) {
            const { server, tool } = parseMcpToolId(toolId);
            return isReadOnlyTool(server, tool, policy);
        }
        return false;
    }

    async check(call: ChannelToolCall, policy: ChannelRunPolicy): Promise<ChannelGuardDecision> {
        const { toolId } = call;
        if (toolId.startsWith("mcp:")) {
            const { server, tool } = parseMcpToolId(toolId);
            return isReadOnlyTool(server, tool, policy)
                ? { kind: "allow" }
                : deny(`MCP tool "${server ?? "?"}:${tool ?? "?"}" is not on the read-only list for channel runs.`);
        }
        if (!toolId.startsWith("builtin:")) {
            return deny(`Tool "${call.toolName}" is not available in channel runs.`);
        }
        const name = toolId.slice("builtin:".length);
        if (!CHANNEL_BUILTINS.has(name)) {
            return deny(
                `Tool "${name}" is not available in runs started from a mobile channel (no external effects). Use the desktop app for this.`,
            );
        }
        const args = asRecord(call.input);
        switch (name) {
            case "fetch-url": {
                const method = (str(args.method) ?? "GET").toUpperCase();
                if (method !== "GET" && method !== "HEAD") {
                    return deny(`fetch-url with method ${method} is not allowed in channel runs — only GET and HEAD.`);
                }
                if (Object.keys(asRecord(args.headers)).length > 0) {
                    return deny("fetch-url with custom headers is not allowed in channel runs.");
                }
                // Host / redirect checks happen at execution (channel-fetch.ts).
                return { kind: "default" };
            }
            case "executeMcpTool": {
                const server = str(args.serverName);
                const tool = str(args.toolName);
                return isReadOnlyTool(server, tool, policy)
                    ? { kind: "allow" }
                    : deny(`MCP tool "${server ?? "?"}:${tool ?? "?"}" is not on the read-only list for channel runs.`);
            }
            case "listMcpTools": {
                const server = str(args.serverName);
                return server && policy.readOnlyMcpTools.some((entry) => entry.startsWith(`${server}:`))
                    ? { kind: "allow" }
                    : deny(`MCP server "${server ?? "?"}" has no read-only tools for channel runs.`);
            }
            case "todo-add":
            case "todo-propose": {
                // An @rowboat item starts (or, once accepted, offers) a
                // background run; a sub-item under a delegated parent becomes
                // a step of that parent's run.
                const items = Array.isArray(args.items) ? args.items : [];
                const delegated = items.some((item) => {
                    const { text, parent } = asRecord(item);
                    return (
                        (typeof text === "string" && ROWBOAT_MENTION_RE.test(text)) ||
                        (typeof parent === "string" && ROWBOAT_MENTION_RE.test(parent))
                    );
                });
                return delegated
                    ? deny("To-dos with @rowboat (or under an @rowboat item) start a background run and cannot be added from a mobile channel. Add a plain item without @rowboat, e.g. \"Am Desktop: …\".")
                    : { kind: "default" };
            }
            default:
                break;
        }
        const targets = fileTargets(name, args);
        if (!targets) {
            return { kind: "default" };
        }
        if (name === "file-glob" && isUnsafeChannelGlob(str(args.pattern))) {
            return deny("Glob patterns with braces, \"..\", absolute paths or ~ are not allowed in channel runs.");
        }
        if (name === "file-grep" && isUnsafeChannelGlob(str(args.fileGlob))) {
            return deny("File globs with braces, \"..\", absolute paths or ~ are not allowed in channel runs.");
        }
        return this.checkFileTargets(name, args, targets);
    }

    // Second layer for search tools: every match must pass before it is
    // read or listed (symlinks inside an allowed root, *.env inside
    // knowledge/, ...). Errors count as "not readable".
    readFilter(): (absPath: string) => Promise<boolean> {
        return async (absPath) => {
            try {
                const root = await this.workspaceRoot();
                const resolved = await this.resolvePath(absPath);
                return this.protectedReason(root, resolved) === null;
            } catch {
                return false;
            }
        };
    }

    private protectedReason(root: string, resolved: string): string | null {
        if (!isPathInside(root, resolved)) {
            return "outside";
        }
        const rel = path.relative(root, resolved);
        const segments = rel === "" ? [] : rel.split(path.sep);
        const sensitive = SENSITIVE_ENTRIES.map((entry) => path.join(root, entry));
        if (
            sensitive.some((entry) => isPathInside(entry, resolved)) ||
            segments.some(isSensitiveSegment)
        ) {
            return "protected";
        }
        return null;
    }

    private async checkFileTargets(
        name: string,
        args: Record<string, unknown>,
        targets: FileTarget[],
    ): Promise<ChannelGuardDecision> {
        const root = await this.workspaceRoot();
        const sensitive = SENSITIVE_ENTRIES.map((entry) => path.join(root, entry));
        for (const target of targets) {
            if (!target.path) {
                return deny("A path is required.");
            }
            const resolved = await this.resolvePath(target.path);
            const reason = this.protectedReason(root, resolved);
            if (reason === "outside") {
                return deny(`Path "${target.path}" is outside the workspace; channel runs may only use workspace files.`);
            }
            if (reason === "protected") {
                return deny(`Path "${target.path}" is protected (credentials / app state) and cannot be used from a mobile channel.`);
            }
            if (
                target.op === "search" &&
                sensitive.some((entry) => isPathInside(resolved, entry))
            ) {
                return deny(`Searching "${target.path}" would include protected files; search a subfolder such as knowledge/ instead.`);
            }
            if (target.op === "write") {
                const writeRoot = path.join(root, WRITE_ROOT);
                const inner = path.relative(writeRoot, resolved);
                const innerSegments = inner === "" ? [] : inner.split(path.sep);
                if (
                    !isPathInside(writeRoot, resolved) ||
                    !isQuarantineFolderName(innerSegments[0] ?? "")
                ) {
                    return deny(`Channel runs may only write notes under ${QUARANTINE_REL} (to-dos via todo-add); "${target.path}" is outside.`);
                }
                if (innerSegments[0]?.toLowerCase() === AGENT_NOTES) {
                    return deny("knowledge/Agent Notes is loaded into the desktop agent's instructions and cannot be written from a mobile channel.");
                }
                if (innerSegments.some((segment) => segment.startsWith("."))) {
                    return deny(`Hidden files and folders ("${target.path}") cannot be written from a mobile channel.`);
                }
            }
            // Directories: a copy/move would carry notes we have not looked at.
            if (
                (name === "file-rename" || name === "file-copy") &&
                target === targets[0] &&
                (await this.isDirectory(resolved))
            ) {
                return deny(`"${target.path}" is a directory; channel runs may only copy or move single notes.`);
            }
            // Autorun notes: the live-note scheduler (`live:`) and the inline
            // task runner (`live_note`, task / tell-rowboat fences) start a
            // background agent with (nearly) every tool — never create,
            // change, copy or move one from a channel.
            if (target.op === "write" || (name === "file-copy" && target.op === "read")) {
                const current = await this.readText(resolved);
                if (current !== null && hasAutorunTrigger(current)) {
                    return deny(`"${target.path}" is a live note or contains a task block; such notes cannot be changed, copied or moved from a mobile channel.`);
                }
            }
        }
        const liveError = await this.resultingLiveContent(name, args, targets);
        return liveError ?? { kind: "default" };
    }

    private async resultingLiveContent(
        name: string,
        args: Record<string, unknown>,
        targets: FileTarget[],
    ): Promise<ChannelGuardDecision | null> {
        if (name !== "file-writeText" && name !== "file-editText") {
            return null;
        }
        const current = name === "file-editText"
            ? await this.readText(await this.resolvePath(targets[0].path))
            : null;
        const next = resultingWriteContent(name, args, current);
        if (next !== null && hasAutorunTrigger(next)) {
            return deny("Notes with `live:` / `live_note` frontmatter or task / tell-rowboat blocks start a background agent and cannot be written from a mobile channel.");
        }
        return null;
    }
}

function deny(reason: string): ChannelGuardDecision {
    return { kind: "deny", reason };
}

function parseMcpToolId(toolId: string): { server?: string; tool?: string } {
    const rest = toolId.slice("mcp:".length);
    const separator = rest.indexOf(":");
    if (separator <= 0) return {};
    return { server: rest.slice(0, separator), tool: rest.slice(separator + 1) };
}

function isReadOnlyTool(
    server: string | undefined,
    tool: string | undefined,
    policy: ChannelRunPolicy,
): boolean {
    return !!server && !!tool && policy.readOnlyMcpTools.includes(`${server}:${tool}`);
}
