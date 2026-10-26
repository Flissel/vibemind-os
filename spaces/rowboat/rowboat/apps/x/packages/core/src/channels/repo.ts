// Modified for VibeMind (Flissel/rowboat vibemind): readChannelReadOnlyMcpTools (Teil E).
import fs from 'node:fs/promises';
import path from 'node:path';
import type { z } from 'zod';
import { WorkDir } from '../config/config.js';
import {
    ChannelsConfig,
    DEFAULT_CHANNELS_CONFIG,
    DEFAULT_CHANNEL_READ_ONLY_MCP_TOOLS,
} from '@x/shared/dist/channels.js';

// Modified for VibeMind (Flissel/rowboat vibemind): the MCP tools ("server:tool")
// auto-approved in channel runs. Key absent = defaults; file missing,
// unparseable or key malformed = EMPTY list (fail closed) — unlike getConfig,
// which falls back to defaults.
export async function readChannelReadOnlyMcpTools(
    configPath: string = path.join(WorkDir, 'config', 'channels.json'),
): Promise<string[]> {
    try {
        const raw: unknown = JSON.parse(await fs.readFile(configPath, 'utf8'));
        if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return [];
        if (!('readOnlyMcpTools' in raw)) return [...DEFAULT_CHANNEL_READ_ONLY_MCP_TOOLS];
        const list = (raw as { readOnlyMcpTools: unknown }).readOnlyMcpTools;
        return Array.isArray(list) && list.every((entry) => typeof entry === 'string')
            ? [...list]
            : [];
    } catch {
        return [];
    }
}

export interface IChannelsConfigRepo {
    getConfig(): Promise<z.infer<typeof ChannelsConfig>>;
    setConfig(config: z.infer<typeof ChannelsConfig>): Promise<void>;
}

export class FSChannelsConfigRepo implements IChannelsConfigRepo {
    private readonly configPath = path.join(WorkDir, 'config', 'channels.json');

    constructor() {
        this.ensureConfigFile();
    }

    private async ensureConfigFile(): Promise<void> {
        try {
            await fs.access(this.configPath);
        } catch {
            await fs.writeFile(this.configPath, JSON.stringify(DEFAULT_CHANNELS_CONFIG, null, 2));
        }
    }

    async getConfig(): Promise<z.infer<typeof ChannelsConfig>> {
        try {
            const content = await fs.readFile(this.configPath, 'utf8');
            return ChannelsConfig.parse(JSON.parse(content));
        } catch {
            return DEFAULT_CHANNELS_CONFIG;
        }
    }

    async setConfig(config: z.infer<typeof ChannelsConfig>): Promise<void> {
        const validated = ChannelsConfig.parse(config);
        await fs.writeFile(this.configPath, JSON.stringify(validated, null, 2));
    }
}
