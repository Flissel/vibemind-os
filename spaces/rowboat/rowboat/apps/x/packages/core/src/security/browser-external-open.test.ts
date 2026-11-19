// Modified for VibeMind (Flissel/rowboat vibemind): what a page in the agent
// browser may hand to the OS (ticket #330, fix round 3, review m5).
import { describe, expect, it } from "vitest";
import { externalOpenDecision } from "./browser-external-open.js";

describe("externalOpenDecision", () => {
    it.each([
        "mailto:someone@example.com",
        "MAILTO:someone@example.com?subject=hi",
        " mailto:a@b.c",
    ])("opens %s externally", (url) => {
        expect(externalOpenDecision(url)).toBe("open");
    });

    it.each([
        "rowboat://action?type=take-meeting-notes",
        "rowboat://open?target=x",
        "file:///C:/Windows/System32/calc.exe",
        "ms-settings:privacy",
        "search-ms:query=x",
        "vscode://file/c:/x",
        "tel:+491234",
        "javascript:alert(1)",
        "data:text/html,hi",
        "slack://open",
        "not a url",
        "",
    ])("drops %s", (url) => {
        expect(externalOpenDecision(url)).toBe("drop");
    });
});
