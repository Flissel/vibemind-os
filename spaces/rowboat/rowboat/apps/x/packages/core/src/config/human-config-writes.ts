// Modified for VibeMind (Flissel/rowboat vibemind): new module (ticket #330,
// part 2, Task 17). The human write paths into the workspace (the Settings
// dialog and every other workspace:* call, apps/main ipc.ts and apps/server
// core-deps.ts with the owner key) run their operation through
// applyHumanWorkspaceOp. When it touches config/mcp.json or
// config/security.json, the consumers follow the human:
//   - only the exact content the human sent is applied, never a re-read of
//     the file after the operation: the written data (writeFile), or the
//     SOURCE of a rename/copy onto the file, read and parsed before the
//     operation;
//   - an operation on a folder that holds the file, a remove, or moving the
//     file away applies nothing new, it only revokes (mcp: approvals of entries
//     no longer present are dropped; security: see
//     revokeSecurityConfigToDefaults).
// Every other path changes nothing. workspaceOpTouchesProtected is the check
// for callers that are NOT a human path (scoped server keys, the embed bridge).

import fs from "node:fs/promises";
import path from "node:path";
import type z from "zod";
import { McpServerConfig } from "@x/shared/dist/mcp.js";
import { approveAllFromConfig, retainApprovalsPresentIn } from "../mcp/stdio-approvals.js";
import { isProtectedPath } from "../runtime/tools/protected-paths.js";
import { WorkDir } from "./config.js";
import { reloadSecurityConfig, revokeSecurityConfigToDefaults, SECURITY_CONFIG_PATH } from "./security.js";

const MCP_CONFIG_PATH = path.join(WorkDir, "config", "mcp.json");

export type HumanWorkspaceOp =
    // encoding as in workspace:writeFile: "utf8" (default), "base64" or "binary" (base64 data).
    | { kind: "writeFile"; path: string; data: string; encoding?: string }
    | { kind: "mkdir"; path: string }
    | { kind: "rename"; from: string; to: string }
    | { kind: "copy"; from: string; to: string }
    | { kind: "remove"; path: string };

type Target = "mcp" | "security";
// apply: the content the human sent (null: unreadable, approve nothing);
// revoke: apply nothing new.
type Effect = { apply: string | null } | "revoke";

const TARGET_FILES: Record<Target, string> = { mcp: MCP_CONFIG_PATH, security: SECURITY_CONFIG_PATH };

// Windows compares case-insensitively and ignores trailing dots and spaces
// in a name; elsewhere the path is compared as is.
function canonical(p: string): string {
    const resolved = path.resolve(p);
    if (process.platform !== "win32") return resolved;
    return resolved
        .split(path.sep)
        .map((segment, i) => (i === 0 ? segment : segment.replace(/[. ]+$/, "")))
        .join(path.sep)
        .toLowerCase();
}

// "exact": the path is the file; "container": a folder that holds it.
function touches(relPath: string, target: Target): "exact" | "container" | null {
    const p = canonical(path.resolve(WorkDir, relPath));
    const file = canonical(TARGET_FILES[target]);
    if (p === file) return "exact";
    const prefix = p.endsWith(path.sep) ? p : p + path.sep;
    return file.startsWith(prefix) ? "container" : null;
}

function decode(data: string, encoding: string | undefined): string {
    return (encoding ?? "utf8") === "utf8" ? data : Buffer.from(data, "base64").toString("utf8");
}

async function readOrNull(file: string): Promise<string | null> {
    try {
        return await fs.readFile(file, "utf8");
    } catch {
        return null;
    }
}

function parseMcp(content: string | null): z.infer<typeof McpServerConfig> | null {
    if (content === null) return null;
    try {
        const parsed = McpServerConfig.safeParse(JSON.parse(content));
        return parsed.success ? parsed.data : null;
    } catch {
        return null;
    }
}

// Decided BEFORE the operation runs (a rename/copy source is read now).
async function planFor(op: HumanWorkspaceOp, target: Target): Promise<Effect | null> {
    switch (op.kind) {
        case "writeFile": {
            const t = touches(op.path, target);
            if (t === "exact") return { apply: decode(op.data, op.encoding) };
            return t ? "revoke" : null;
        }
        case "mkdir":
        case "remove":
            return touches(op.path, target) ? "revoke" : null;
        case "rename":
        case "copy": {
            const to = touches(op.to, target);
            if (to === "exact") return { apply: await readOrNull(path.resolve(WorkDir, op.from)) };
            if (to === "container") return "revoke";
            return op.kind === "rename" && touches(op.from, target) ? "revoke" : null;
        }
    }
}

async function applyEffect(target: Target, effect: Effect): Promise<void> {
    if (target === "mcp") {
        if (effect === "revoke") {
            retainApprovalsPresentIn(parseMcp(await readOrNull(MCP_CONFIG_PATH)));
        } else {
            // Invalid or unreadable content approves nothing (getClient cannot
            // parse it either): the set becomes empty.
            approveAllFromConfig(parseMcp(effect.apply) ?? { mcpServers: {} });
        }
        return;
    }
    if (effect === "revoke") {
        revokeSecurityConfigToDefaults();
    } else if (effect.apply === null) {
        revokeSecurityConfigToDefaults();
    } else {
        reloadSecurityConfig(effect.apply);
    }
}

/**
 * Runs a human workspace operation and then lets the consumers follow it.
 * `perform` is the operation itself; when it throws, nothing changes.
 */
export async function applyHumanWorkspaceOp<T>(op: HumanWorkspaceOp, perform: () => Promise<T>): Promise<T> {
    const plans: Array<[Target, Effect]> = [];
    for (const target of ["mcp", "security"] as const) {
        const effect = await planFor(op, target);
        if (effect) plans.push([target, effect]);
    }
    const result = await perform();
    for (const [target, effect] of plans) await applyEffect(target, effect);
    return result;
}

function opPaths(op: HumanWorkspaceOp): string[] {
    switch (op.kind) {
        case "writeFile":
        case "mkdir":
        case "remove":
            return [op.path];
        case "rename":
        case "copy":
            return [op.from, op.to];
    }
}

// True when the operation reads from, writes, moves or removes a protected
// workspace entry (Task 16 list: config/, keys, channels/, ...) or the
// workspace itself. Fails closed: an unresolvable path counts as protected.
export async function workspaceOpTouchesProtected(op: HumanWorkspaceOp): Promise<boolean> {
    for (const relPath of opPaths(op)) {
        try {
            if (relPath === "" || (await isProtectedPath(relPath))) return true;
        } catch {
            return true;
        }
    }
    return false;
}
