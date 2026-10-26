// Modified for VibeMind (Flissel/rowboat vibemind): readOnlyMcpTools for channel runs (Teil E).
import { z } from "zod";

// ---------------------------------------------------------------------------
// Mobile messaging channels (WhatsApp / Telegram bridges).
//
// Each channel links the user's OWN account/bot to the local app: WhatsApp
// connects as a linked device (QR pairing), Telegram long-polls the user's
// own bot token. There is no central relay — the desktop app must be running.
// ---------------------------------------------------------------------------

export const WhatsAppChannelConfig = z.object({
    enabled: z.boolean(),
    // Extra sender phone numbers (digits only, no '+') allowed to drive the
    // bridge. The linked account itself (self-chat) is always allowed.
    allowFrom: z.array(z.string()),
});

export const TelegramChannelConfig = z.object({
    enabled: z.boolean(),
    botToken: z.string(),
    // Telegram chat ids (numeric strings) allowed to drive the bridge.
    // Unknown senders are told their chat id so it can be added here.
    allowFrom: z.array(z.string()),
});

// Modified for VibeMind (Flissel/rowboat vibemind): MCP tools treated as
// read-only for channel runs, as "server:tool" — they are auto-approved
// without the permission classifier; every other MCP tool is refused in
// channel runs. Key absent = DEFAULT_CHANNEL_READ_ONLY_MCP_TOOLS; an
// unreadable channels.json = empty list (fail closed).
export const DEFAULT_CHANNEL_READ_ONLY_MCP_TOOLS: readonly string[] = [
    "wissen:wissen_suchen",
    "wissen:wissen_stand",
    // vibemind-os spaces/rowboat/deploy/headless/werkzeuge/server.py
    "vibemind-werkzeuge:github_repos",
    "vibemind-werkzeuge:github_issues",
    "vibemind-werkzeuge:github_prs",
    "vibemind-werkzeuge:github_commits",
    "vibemind-werkzeuge:github_datei",
    "vibemind-werkzeuge:github_ueberblick",
    "vibemind-werkzeuge:pc_suchen",
    "exa:web_search_exa",
    "exa:web_fetch_exa",
];

export const ChannelsConfig = z.object({
    whatsapp: WhatsAppChannelConfig,
    telegram: TelegramChannelConfig,
    readOnlyMcpTools: z.array(z.string()).optional(),
});

export const DEFAULT_CHANNELS_CONFIG: z.infer<typeof ChannelsConfig> = {
    whatsapp: { enabled: false, allowFrom: [] },
    telegram: { enabled: false, botToken: "", allowFrom: [] },
};

export const WhatsAppChannelStatus = z.object({
    state: z.enum(["disabled", "starting", "qr", "connected", "error"]),
    // PNG data URL of the pairing QR (present while state === "qr").
    qrDataUrl: z.string().optional(),
    // Linked account phone number (digits) once connected.
    self: z.string().optional(),
    error: z.string().optional(),
});

export const TelegramChannelStatus = z.object({
    state: z.enum(["disabled", "starting", "polling", "error"]),
    botUsername: z.string().optional(),
    error: z.string().optional(),
});

export const ChannelsStatus = z.object({
    whatsapp: WhatsAppChannelStatus,
    telegram: TelegramChannelStatus,
});
