// Modified for VibeMind (Flissel/rowboat vibemind): new module (ticket #330,
// part 2, Task 17). The stdio MCP entries a human approved in this process
// lifetime. getClient (mcp.ts) starts the `command` of a stdio entry only when
// its exact (name, command, args, env) is the approved one for that name, so
// an entry that got into config/mcp.json any other way (a shell command,
// another program) starts no process.
// The approvals mirror the latest human-approved config: one key per server
// name. Filled by:
//   - initConfigs at startup, from the file as it is then (trust on boot;
//     a hand edit takes effect after a restart) — replaces the whole set,
//   - the human write paths (Settings / workspace:* handlers of apps/main and
//     apps/server, human-config-writes.ts) — replace the whole set with what
//     the human wrote, or only revoke,
//   - addMcpServer, only when the call was approved by a human — replaces
//     that name's key.
// URL entries are not gated here.

import crypto from "node:crypto";
import type z from "zod";
import type { McpServerConfig, StdioMcpServerConfig } from "@x/shared/dist/mcp.js";

export type StdioEntry = Pick<z.infer<typeof StdioMcpServerConfig>, "command" | "args" | "env">;
type Config = z.infer<typeof McpServerConfig>;

const approved = new Map<string, string>();

// Stable serialisation: env as key-sorted pairs (key order does not matter),
// an absent args/env distinct from an empty one (fails closed).
function entryKey(name: string, entry: StdioEntry): string {
    const env = entry.env === undefined
        ? null
        : Object.keys(entry.env).sort().map((key) => [key, entry.env![key]]);
    const payload = JSON.stringify([name, entry.command, entry.args ?? null, env]);
    return crypto.createHash("sha256").update(payload).digest("hex");
}

// Sets (replaces) the approved definition of this server name.
export function approveStdioEntry(name: string, entry: StdioEntry): void {
    approved.set(name, entryKey(name, entry));
}

export function revokeStdioEntry(name: string): void {
    approved.delete(name);
}

export function isStdioEntryApproved(name: string, entry: StdioEntry): boolean {
    return approved.get(name) === entryKey(name, entry);
}

// Replaces the whole set with the stdio entries of a parsed mcp.json (URL
// entries are skipped). Startup and a human write of the file.
export function approveAllFromConfig(config: Config): void {
    approved.clear();
    for (const [name, entry] of Object.entries(config.mcpServers)) {
        if ("command" in entry) approveStdioEntry(name, entry);
    }
}

// Revocation only (a human removed or moved the file, or changed a folder that
// holds it): keeps an approval only while the config still holds exactly the
// approved entry under that name. Never approves anything. null: no readable
// config, every approval is dropped.
export function retainApprovalsPresentIn(config: Config | null): void {
    for (const name of [...approved.keys()]) {
        const entry = config?.mcpServers[name];
        if (!entry || !("command" in entry) || !isStdioEntryApproved(name, entry)) approved.delete(name);
    }
}

// Tests only.
export function resetStdioApprovalsForTests(): void {
    approved.clear();
}
