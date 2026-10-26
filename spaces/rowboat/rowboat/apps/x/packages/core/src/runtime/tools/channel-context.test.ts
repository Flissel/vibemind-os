// Modified for VibeMind (Flissel/rowboat vibemind): execution-time channel layers (Teil E, fix round 1).
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import * as files from "../../filesystem/files.js";
import { BuiltinTools } from "./catalog.js";
import type { ToolContext } from "./exec-tool.js";

let dir: string;

beforeAll(async () => {
    dir = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), "teil-e-")));
    await fs.mkdir(path.join(dir, "notes"));
    await fs.writeFile(path.join(dir, "notes", "a.md"), "secret line\n");
    await fs.writeFile(path.join(dir, "notes", "b.env"), "secret=1\n");
});

afterAll(async () => {
    await fs.rm(dir, { recursive: true, force: true });
});

function ctx(channel?: { readOnlyMcpTools: string[] }): ToolContext {
    return {
        runId: "t1",
        toolCallId: "c1",
        signal: new AbortController().signal,
        abortRegistry: {} as ToolContext["abortRegistry"],
        publish: async () => undefined,
        ...(channel ? { channel } : {}),
    };
}

describe("files.grep / files.glob pathFilter", () => {
    it("filters every candidate before reading or listing", async () => {
        const notEnv = async (p: string) => !p.endsWith(".env");
        const all = await files.grep({ pattern: "secret", searchPath: dir });
        expect(all.matches.map((m) => path.basename(m.resolvedPath)).sort()).toEqual(["a.md", "b.env"]);
        const filtered = await files.grep({ pattern: "secret", searchPath: dir, pathFilter: notEnv });
        expect(filtered.matches.map((m) => path.basename(m.resolvedPath))).toEqual(["a.md"]);

        const globbed = await files.glob("**/*", dir, notEnv);
        expect(globbed.resolvedFiles.map((f) => path.basename(f))).toEqual(["a.md"]);
        expect(globbed.count).toBe(1);
    });
});

describe("builtins under a channel context", () => {
    const channel = { readOnlyMcpTools: [] };

    it("file-grep / file-glob drop matches outside the readable workspace in channel runs only", async () => {
        const grep = BuiltinTools["file-grep"].execute;
        const desktop = (await grep({ pattern: "secret", searchPath: dir }, ctx())) as { count: number };
        expect(desktop.count).toBe(2);
        // The temp dir is outside the Rowboat workspace: the channel read
        // filter drops every match even though the guard was bypassed here.
        const channelRun = (await grep({ pattern: "secret", searchPath: dir }, ctx(channel))) as { count: number };
        expect(channelRun.count).toBe(0);

        const glob = BuiltinTools["file-glob"].execute;
        expect(((await glob({ pattern: "**/*", cwd: dir }, ctx())) as { count: number }).count).toBe(2);
        expect(((await glob({ pattern: "**/*", cwd: dir }, ctx(channel))) as { count: number }).count).toBe(0);
    });

    it("fetch-url refuses internal hosts and headers in channel runs without a request", async () => {
        const fetchUrl = BuiltinTools["fetch-url"].execute;
        await expect(fetchUrl({ url: "http://127.0.0.1:9/" }, ctx(channel))).resolves.toMatchObject({
            ok: false,
            error: expect.stringContaining("not allowed"),
        });
        await expect(fetchUrl({ url: "http://localhost:9/" }, ctx(channel))).resolves.toMatchObject({ ok: false });
        await expect(
            fetchUrl({ url: "https://example.com/", headers: { Authorization: "x" } }, ctx(channel)),
        ).resolves.toMatchObject({ ok: false, error: expect.stringContaining("headers") });
        await expect(
            fetchUrl({ url: "https://example.com/", method: "POST", body: "x" }, ctx(channel)),
        ).resolves.toMatchObject({ ok: false });
    });

    it("desktop fetch-url keeps the old path (no channel host policy)", async () => {
        const fetchUrl = BuiltinTools["fetch-url"].execute;
        // Port 9 (discard) on loopback: refused quickly, no external traffic.
        const result = (await fetchUrl({ url: "http://127.0.0.1:9/" }, ctx())) as { error?: string };
        expect(result.error ?? "").not.toContain("not allowed in channel runs");
    });
});
