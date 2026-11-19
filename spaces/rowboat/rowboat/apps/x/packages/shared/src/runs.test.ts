// Modified for VibeMind (Flissel/rowboat vibemind): humanOnly permission requests (ticket #330).
import { describe, expect, it } from "vitest";
import { ToolPermissionMetadata } from "./runs.js";

describe("ToolPermissionMetadata humanOnly", () => {
    it("keeps humanOnly on file and command requests", () => {
        const file = ToolPermissionMetadata.parse({
            kind: "file",
            operation: "write",
            paths: ["/w/config/mcp.json"],
            pathPrefix: "/w/config",
            humanOnly: true,
        });
        const command = ToolPermissionMetadata.parse({
            kind: "command",
            commandNames: ["echo"],
            humanOnly: true,
        });
        expect(file).toMatchObject({ humanOnly: true });
        expect(command).toMatchObject({ humanOnly: true });
    });

    it("keeps humanOnly on a generic tool request (addMcpServer with a command, Task 17)", () => {
        const tool = ToolPermissionMetadata.parse({
            kind: "tool",
            toolId: "builtin:addMcpServer",
            toolName: "addMcpServer",
            humanOnly: true,
        });
        expect(tool).toMatchObject({ humanOnly: true });
    });

    it("leaves humanOnly absent on ordinary requests", () => {
        const plain = ToolPermissionMetadata.parse({ kind: "command", commandNames: ["rm"] });
        expect(plain).not.toHaveProperty("humanOnly");
    });
});
