// Modified for VibeMind (Flissel/rowboat vibemind): trust on boot for mcp.json stdio entries and security.json (ticket #330, Task 17).
import container from "../di/container.js";
import type { IModelConfigRepo } from "../models/repo.js";
import type { IMcpConfigRepo } from "../mcp/repo.js";
import type { IAgentScheduleRepo } from "../agent-schedule/repo.js";
import type { IAgentScheduleStateRepo } from "../agent-schedule/state-repo.js";
import { approveAllFromConfig } from "../mcp/stdio-approvals.js";
import { ensureSecurityConfig, getSecurityAllowList, reloadSecurityConfig } from "./security.js";

/**
 * Initialize all config files at app startup.
 * Ensures config files exist before the UI might access them.
 */
export async function initConfigs(): Promise<void> {
    // Resolve repos and explicitly call their ensureConfig methods
    const modelConfigRepo = container.resolve<IModelConfigRepo>("modelConfigRepo");
    const mcpConfigRepo = container.resolve<IMcpConfigRepo>("mcpConfigRepo");
    const agentScheduleRepo = container.resolve<IAgentScheduleRepo>("agentScheduleRepo");
    const agentScheduleStateRepo = container.resolve<IAgentScheduleStateRepo>("agentScheduleStateRepo");

    await Promise.all([
        modelConfigRepo.ensureConfig(),
        mcpConfigRepo.ensureConfig(),
        agentScheduleRepo.ensureConfig(),
        agentScheduleStateRepo.ensureState(),
        ensureSecurityConfig(),
    ]);

    await trustConfigsOnBoot(mcpConfigRepo);
}

// Trust on boot (ticket #330, Task 17): what config/mcp.json and
// config/security.json hold at startup is approved — the stdio entries may
// start their process, the allow-lists are loaded once. A later change to the
// files takes effect only through a human write path or after a restart
// (also on the headless server, where mcp.json is edited by hand and the
// container restarted). An unreadable mcp.json approves nothing.
// Fix round 1 (review m-5): logs what it approved — one line per stdio entry
// (server name and command basename only: args and env values can hold
// secrets) and one line with the number of allow-listed commands and whether
// "*" is among them. `log` and `warn` are injectable for tests.
// Fix round 2 (review m-2): only the basename of the first whitespace-
// separated token of `command` is logged (a command can carry its args, e.g.
// "@scope/srv --token=X"), and a read or parse failure is logged without its
// error text (a JSON parse error can quote the file).
export async function trustConfigsOnBoot(
    mcpConfigRepo: IMcpConfigRepo,
    log: (line: string) => void = (line) => console.log(line),
    warn: (line: string) => void = (line) => console.warn(line),
): Promise<void> {
    try {
        const config = await mcpConfigRepo.getConfig();
        approveAllFromConfig(config);
        for (const [name, entry] of Object.entries(config.mcpServers)) {
            if (!("command" in entry)) continue;
            log(`[config] trust on boot: stdio MCP server ${JSON.stringify(name)} approved (command ${commandBasename(entry.command)})`);
        }
    } catch {
        warn("[config] trust on boot: config/mcp.json is not valid JSON; no stdio MCP server is approved");
    }
    reloadSecurityConfig();
    const commands = getSecurityAllowList();
    const star = commands.includes("*") ? "\"*\" present (unrestricted shell)" : "\"*\" not present";
    log(`[config] trust on boot: ${commands.length} allow-listed commands, ${star}`);
}

function commandBasename(command: string): string {
    const first = command.trim().split(/\s+/)[0] ?? "";
    return first.split(/[\\/]/).pop() || "(empty)";
}
