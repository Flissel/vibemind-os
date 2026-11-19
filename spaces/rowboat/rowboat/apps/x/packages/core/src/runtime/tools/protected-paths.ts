// Modified for VibeMind (Flissel/rowboat vibemind): new module (ticket #330,
// part 1). Workspace entries whose content turns into process start or an
// unrestricted shell (config/mcp.json, config/security.json, ...) or that
// hold keys and channel credentials. An agent write to them needs a human:
//   - permission-metadata.ts turns such a call into a `humanOnly` request
//     (never classified, denied without a human, never persisted as a grant),
//   - execTool refuses it unless the turn runtime marks the call as approved
//     by a human (ToolContext.humanApproved).
// headless-sandbox.ts and channel-run-guard.ts build their own (longer)
// lists on top of this one.

import fsCallback from "node:fs";
import fs from "node:fs/promises";
import path from "node:path";
import { hasOutputRedirect } from "../../application/lib/command-executor.js";
import { WorkDir } from "../../config/config.js";
import { expandHomePath, isPathInside, resolveFilePathForPermission } from "../../filesystem/files.js";
import { filePermissionTargets } from "../assembly/permission-metadata.js";
import { commandMentionsOwnServer } from "../../security/own-server-target.js";

export const PROTECTED_WORKSPACE_ENTRIES: readonly string[] = [
    "config",
    "server-key",
    "cipher-key",
    "embed.json",
    ".electron-data",
    "channels",
    // Fix round 1 (review C1/I3): scoped bearer keys of the local server
    // (apps/server/src/auth.ts SCOPED_KEYS_FILE, re-read per request) and the
    // code-session state (code-mode/sessions-meta/<id>.json holds the pinned
    // cwd and the approval policy, e.g. "yolo").
    "scoped-keys.json",
    "code-mode",
    // Unpacked Chrome extensions: apps/main/src/browser/extensions.ts loads
    // every extensions/<name>/manifest.json into the browser session at start,
    // with no install step (their scripts then run in every browser page).
    "extensions",
    // Task 17 fix round 3 (review I-5): code the app starts. engines/ holds
    // the coding-agent executable (code-mode/acp/engine-provisioner.ts,
    // ENGINES_ROOT = ~/.rowboat/engines), spawned by the next code run without
    // an integrity check; mcp-servers/ holds MCP server code that approved
    // stdio entries of config/mcp.json start (the approval is keyed on the
    // command, not on the file content). The provisioner writes engines/ with
    // fs directly, not through execTool, so provisioning is unaffected.
    "engines",
    "mcp-servers",
];

// Windows ignores trailing dots and spaces in a name ("config." is
// "config") and compares case-insensitively; the check does both everywhere.
function normalizeName(name: string): string {
    return name.replace(/[. ]+$/, "").toLowerCase();
}

export function isProtectedEntryName(name: string): boolean {
    const normalized = normalizeName(name);
    return normalized.length > 0 && PROTECTED_WORKSPACE_ENTRIES.includes(normalized);
}

// Fix round 1 (M3): the 8.3 short-name shape — a name character, "~", a digit.
const SHORT_NAME_SHAPE = /[^\s\\/]~\d/;

// A ":" after the drive letter is an NTFS stream ("server-key::$DATA",
// "config:x") or another device syntax: treated as protected.
function hasStreamSyntax(inputPath: string): boolean {
    const rest = /^[a-zA-Z]:/.test(inputPath) ? inputPath.slice(2) : inputPath;
    return rest.includes(":");
}

// "entry": a protected entry or a path inside one (or a path no check can
// map back: stream, 8.3 or UNC syntax). "container": the workspace itself or
// one of its ancestors — removing or moving it takes the entries along, but
// listing or searching it does not read them.
type ProtectedKind = "entry" | "container" | null;

function protectedUnder(root: string, target: string): ProtectedKind {
    if (isPathInside(target, root)) return "container";
    if (!isPathInside(root, target)) return null;
    const first = path.relative(root, target).split(path.sep)[0] ?? "";
    return isProtectedEntryName(first) ? "entry" : null;
}

async function canonicalWorkspaceRoot(): Promise<string> {
    try {
        return await fs.realpath(WorkDir);
    } catch {
        return path.resolve(WorkDir);
    }
}

// Fix round 1 (review I2): the JS realpath resolves symlinks and junctions
// but not a subst / mapped drive. The native one (GetFinalPathNameByHandle on
// Windows) maps those back to the volume path. Injectable for tests, because
// creating a real alias drive changes machine state.
type RealpathFn = (p: string) => Promise<string>;
// node:fs/promises has no realpath.native: the callback form of node:fs.
const defaultNativeRealpath: RealpathFn = (p) =>
    new Promise((resolve, reject) => {
        fsCallback.realpath.native(p, (error, resolved) => (error ? reject(error) : resolve(resolved)));
    });
let nativeRealpath: RealpathFn = defaultNativeRealpath;

export function setNativeRealpathForTests(fn: RealpathFn | undefined): void {
    nativeRealpath = fn ?? defaultNativeRealpath;
}

// Native real path of the nearest existing ancestor, plus the missing rest.
// Exported for tests only.
export async function nativeCanonicalPath(resolvedPath: string): Promise<string> {
    return nativeCanonical(resolvedPath);
}

async function nativeCanonical(resolvedPath: string): Promise<string> {
    const missing: string[] = [];
    let current = resolvedPath;
    for (;;) {
        try {
            return path.join(await nativeRealpath(current), ...missing.reverse());
        } catch {
            const parent = path.dirname(current);
            if (parent === current) return resolvedPath;
            missing.push(path.basename(current));
            current = parent;
        }
    }
}

// "\\server\share", "//server/share", "\\?\..." and "\\.\...": a UNC or
// device path can name the workspace through a share (\\localhost\C$\...)
// that no check maps back. Treated as protected (fails closed).
function isUncOrDevicePath(p: string): boolean {
    return /^[\\/]{2}/.test(p);
}

// True when the path (relative to the workspace root, or absolute) is a
// protected entry, lies inside one, or contains the workspace. The lexical
// path, the real path (symlinks / junctions resolved where the target
// exists) and the native real path (alias drives) are checked.
export async function isProtectedPath(inputPath: string): Promise<boolean> {
    return (await protectedPathKind(inputPath)) !== null;
}

// Task 17 fix round 1 (review I-1): true only when the path IS a protected
// entry or lies inside one — not for the workspace itself or an ancestor
// (reading one file or listing one folder there does not read the entries).
export async function isProtectedEntryPath(inputPath: string): Promise<boolean> {
    return (await protectedPathKind(inputPath)) === "entry";
}

// "entry" wins over "container": the strongest answer of the three checks.
async function protectedPathKind(inputPath: string): Promise<ProtectedKind> {
    if (hasStreamSyntax(inputPath)) return "entry";
    // An 8.3 short name (SERVER~1, CONFIG~1) is not resolved by the lexical
    // check: the 8.3 shape counts as protected.
    if (SHORT_NAME_SHAPE.test(inputPath)) return "entry";
    if (isUncOrDevicePath(inputPath.trim()) || isUncOrDevicePath(expandHomePath(inputPath))) return "entry";
    const resolved = await resolveFilePathForPermission(inputPath);
    if (isUncOrDevicePath(resolved.canonicalPath) && !isUncOrDevicePath(path.resolve(WorkDir))) return "entry";
    const [nativeRoot, nativeTarget] = await Promise.all([
        nativeCanonical(path.resolve(WorkDir)),
        nativeCanonical(resolved.resolvedPath),
    ]);
    if (isUncOrDevicePath(nativeTarget) && !isUncOrDevicePath(nativeRoot)) return "entry";
    const kinds = [
        protectedUnder(path.resolve(WorkDir), resolved.resolvedPath),
        protectedUnder(await canonicalWorkspaceRoot(), resolved.canonicalPath),
        protectedUnder(nativeRoot, nativeTarget),
    ];
    if (kinds.includes("entry")) return "entry";
    return kinds.includes("container") ? "container" : null;
}

// cmd.exe drops "^", the shells drop quotes, bash drops "\" escapes: check
// the command both with and without backslashes, split at every shell and
// path separator.
const COMMAND_PIECE_SPLIT = /[\s/\\;&|<>()=,:*?$%!@{}[\]+]+/;

export function commandNamesProtectedEntry(command: string): boolean {
    // An 8.3 short name (CONFIG~1) is resolved by no text check: its shape
    // counts as naming a protected entry. "cd ~" and "~/x" do not match;
    // "HEAD~3" does (accepted, fail closed). Tested on the same normalized
    // variants as the names, so CONFIG~^1 and CONFIG~"1" match too.
    const variants = [
        command.replace(/[\^"'`]/g, ""),
        command.replace(/[\^"'`\\]/g, ""),
    ];
    return variants.some((variant) =>
        SHORT_NAME_SHAPE.test(variant) || variant.split(COMMAND_PIECE_SPLIT).some(isProtectedEntryName),
    );
}

// The one resolution of a code_agent_run working directory, shared by the
// permission request (permission-metadata.ts) and the tool (domains/code.ts):
// home expansion, an absolute path only (the tool resolves against the
// process, the permission check against the workspace — a relative cwd is
// therefore rejected), then the canonical (real) path. Returns null for a
// relative path.
export async function canonicalCodeAgentCwd(cwd: string): Promise<string | null> {
    const expanded = expandHomePath(cwd);
    if (!path.isAbsolute(expanded)) return null;
    return (await resolveFilePathForPermission(path.resolve(expanded))).canonicalPath;
}

function asString(value: unknown): string | undefined {
    return typeof value === "string" && value.length > 0 ? value : undefined;
}

// True when the tool call writes, moves, copies onto or removes a protected
// path, or (executeCommand) names a protected entry, runs inside one, or
// redirects output into a file at all.
export async function isProtectedWrite(toolName: string, args: Record<string, unknown>): Promise<boolean> {
    if (toolName === "executeCommand") {
        const command = typeof args.command === "string" ? args.command : String(args.command ?? "");
        if (commandNamesProtectedEntry(command)) return true;
        // Fix round 2 (review N2 ruling): a command naming the own server
        // (loopback + own port, or /rpc/) is for the human. Second layer
        // only — text matching is evadable.
        if (await commandMentionsOwnServer(command)) return true;
        const cwd = asString(args.cwd);
        if (cwd !== undefined && (await isProtectedPath(cwd))) return true;
        // Controller's final ruling (#330): ANY output redirect other than
        // the harmless ones (2>&1, nul, /dev/null) is for a human, whatever
        // its target — no attempt to parse what the shell will write where.
        // Agents write files with file-writeText.
        return hasOutputRedirect(command);
    }
    if (toolName === "code_agent_run") {
        // The coding agent writes files itself: a cwd that is the workspace
        // (or contains it), a protected entry or a path inside one counts.
        // The cwd the run really uses (session pin, chip, default project)
        // is checked again in code.ts.
        // A relative cwd is rejected by the tool itself (code.ts).
        const cwd = asString(args.cwd);
        const canonical = cwd === undefined ? null : await canonicalCodeAgentCwd(cwd);
        return canonical !== null && (await isProtectedPath(canonical));
    }
    const targets = filePermissionTargets(toolName, args);
    if (!targets || (targets.operation !== "write" && targets.operation !== "delete")) {
        return false;
    }
    // file-copy only reads its source (see isProtectedRead).
    const writePaths = toolName === "file-copy" ? targets.paths.slice(1) : targets.paths;
    for (const target of writePaths) {
        if (await isProtectedPath(target)) return true;
    }
    return false;
}

// Fix round 1 (review C1, catalog sweep): builtins outside
// filePermissionTargets that read a local file's CONTENT. They are kept out
// of filePermissionTargets on purpose: that table also drives the ordinary
// outside-workspace prompts (legacy engine), which these tools never had.
//   spaces-upload-blob  filePath           uploads the bytes to a space
//   app-read-data       apps/<appFolder>/data/<file>  (appFolder is not
//                       validated by the tool; a junction under apps/ can
//                       point data/ anywhere)
//   analyzeAgent        agents/<agentName>.md          returned in full
//   run-live-note-agent knowledge/<filePath>           returned as contentAfter
export function extraProtectedReadTargets(toolName: string, args: Record<string, unknown>): string[] | null {
    switch (toolName) {
        case "spaces-upload-blob": {
            const filePath = asString(args.filePath);
            return filePath ? [filePath] : null;
        }
        case "app-read-data": {
            const appFolder = asString(args.appFolder);
            const file = asString(args.file);
            return appFolder && file ? [path.join("apps", appFolder, "data", file)] : null;
        }
        case "analyzeAgent": {
            const agentName = asString(args.agentName);
            return agentName ? [path.join("agents", `${agentName}.md`)] : null;
        }
        case "run-live-note-agent": {
            const filePath = asString(args.filePath);
            return filePath ? [path.join("knowledge", filePath.replace(/^knowledge\//, ""))] : null;
        }
        default:
            return null;
    }
}

// The paths a protected-access request names: the file-tool targets, else
// the extra readers (as a read). Null when the tool touches no local path.
export function protectedAccessTargets(
    toolName: string,
    args: Record<string, unknown>,
): { operation: "read" | "list" | "search" | "write" | "delete"; paths: string[] } | null {
    const targets = filePermissionTargets(toolName, args);
    if (targets) return targets;
    const extra = extraProtectedReadTargets(toolName, args);
    return extra ? { operation: "read", paths: extra } : null;
}

// Fix round 1 (review C1): reading a protected entry needs the human as well
// — server-key opens every RPC of the local server. True when the call reads
// the content of a protected path (file-readText, parseFile, LLMParse,
// deck-review, transcribe-audio, and file-exists / file-stat on it), copies
// FROM one, or lists / searches inside one. Listing or searching the
// workspace itself (or an ancestor) does not count; a workspace-wide
// file-grep drops matches in protected files instead (protectedContentFilter).
export async function isProtectedRead(toolName: string, args: Record<string, unknown>): Promise<boolean> {
    const extra = extraProtectedReadTargets(toolName, args);
    if (extra) {
        for (const target of extra) {
            if (await isProtectedPath(target)) return true;
        }
        return false;
    }
    const targets = filePermissionTargets(toolName, args);
    if (!targets) return false;
    if (toolName === "file-copy") {
        const from = targets.paths[0];
        return from !== undefined && (await isProtectedPath(from));
    }
    if (targets.operation === "read") {
        // Fix round 2 (review m1): these ops read one file or its metadata,
        // so only a target that IS a protected entry or lies inside one
        // counts — not the workspace root or a parent folder of it.
        for (const target of targets.paths) {
            if ((await protectedPathKind(target)) === "entry") return true;
        }
        return false;
    }
    if (targets.operation === "list" || targets.operation === "search") {
        for (const target of targets.paths) {
            if ((await protectedPathKind(target)) === "entry") return true;
        }
    }
    return false;
}

// The one gate for the permission request and execTool: a protected write
// or a protected read.
export async function needsHumanForProtectedPath(toolName: string, args: Record<string, unknown>): Promise<boolean> {
    return (await isProtectedWrite(toolName, args)) || (await isProtectedRead(toolName, args));
}

// Per-file filter for a search that was not approved by a human: keeps a
// file only when it is not protected (checked before the file is read).
export async function protectedContentFilter(absPath: string): Promise<boolean> {
    return !(await isProtectedPath(absPath));
}
