// Modified for VibeMind (Flissel/rowboat vibemind): channel MCP allowlist reader (Teil E, fix round 1).
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { DEFAULT_CHANNEL_READ_ONLY_MCP_TOOLS } from "@x/shared/dist/channels.js";
import { readChannelReadOnlyMcpTools } from "./repo.js";

let dir: string;
const file = (name: string) => path.join(dir, name);

beforeAll(async () => {
    dir = await fs.mkdtemp(path.join(os.tmpdir(), "channels-"));
});

afterAll(async () => {
    await fs.rm(dir, { recursive: true, force: true });
});

describe("readChannelReadOnlyMcpTools", () => {
    it("uses the configured list", async () => {
        await fs.writeFile(file("a.json"), JSON.stringify({ readOnlyMcpTools: ["wissen:wissen_suchen"] }));
        await expect(readChannelReadOnlyMcpTools(file("a.json"))).resolves.toEqual(["wissen:wissen_suchen"]);
    });

    it("falls back to the defaults when the key is absent", async () => {
        await fs.writeFile(file("b.json"), JSON.stringify({ telegram: {} }));
        await expect(readChannelReadOnlyMcpTools(file("b.json"))).resolves.toEqual([...DEFAULT_CHANNEL_READ_ONLY_MCP_TOOLS]);
    });

    it("fails closed (empty) when the file is missing, broken or the key malformed", async () => {
        await expect(readChannelReadOnlyMcpTools(file("missing.json"))).resolves.toEqual([]);
        await fs.writeFile(file("c.json"), "{ not json");
        await expect(readChannelReadOnlyMcpTools(file("c.json"))).resolves.toEqual([]);
        await fs.writeFile(file("d.json"), JSON.stringify({ readOnlyMcpTools: "wissen" }));
        await expect(readChannelReadOnlyMcpTools(file("d.json"))).resolves.toEqual([]);
        await fs.writeFile(file("e.json"), JSON.stringify({ readOnlyMcpTools: ["ok:x", 3] }));
        await expect(readChannelReadOnlyMcpTools(file("e.json"))).resolves.toEqual([]);
    });
});
