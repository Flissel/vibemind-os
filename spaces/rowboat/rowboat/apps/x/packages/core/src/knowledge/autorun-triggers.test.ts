// Modified for VibeMind (Flissel/rowboat vibemind): autorun trigger detection (Teil E, fix round 2).
import { describe, expect, it } from "vitest";
import {
    extractAllFrontmatterValues,
    hasAutorunTrigger,
    hasInlineTaskTrigger,
    hasLiveFrontmatter,
    splitInlineTaskFrontmatter,
} from "./autorun-triggers.js";

describe("autorun triggers", () => {
    it("detects what the inline-task runner acts on", () => {
        const payload = "---\nlive_note: true\n---\n# N\n```task\n{\"instruction\":\"x\"}\n```\n";
        // The runner's own view of the payload: live_note === "true".
        const { raw } = splitInlineTaskFrontmatter(payload);
        expect(extractAllFrontmatterValues(raw).live_note).toBe("true");
        expect(hasInlineTaskTrigger(payload)).toBe(true);
        expect(hasInlineTaskTrigger("---\nlive_note: false\n---\nx")).toBe(true);
        expect(hasInlineTaskTrigger("---\r\nlive_note: true\r\n---\r\nx")).toBe(true);
        expect(hasInlineTaskTrigger("\uFEFF---\n\"live_note\": true\n---\nx")).toBe(true);
        expect(hasInlineTaskTrigger("x\n    ```tell-rowboat\n{}\n```")).toBe(true);
        expect(hasInlineTaskTrigger("x\n```tasks\n```")).toBe(true);
        expect(hasInlineTaskTrigger("# t\n```ts\nlet task = 1\n```\nlive_note: true in prose")).toBe(false);
    });

    it("round 3: also tilde fences, 4+ backticks and editor class names", () => {
        expect(hasInlineTaskTrigger("x\n~~~task\n{}\n~~~")).toBe(true);
        expect(hasInlineTaskTrigger("x\n  ~~~~tell-rowboat\n{}\n~~~~")).toBe(true);
        expect(hasInlineTaskTrigger("x\n````task\n{}\n````")).toBe(true);
        expect(hasInlineTaskTrigger("x\n`````  tell-rowboat\n{}\n`````")).toBe(true);
        expect(hasInlineTaskTrigger('<pre><code class="language-task">{}</code></pre>')).toBe(true);
        expect(hasInlineTaskTrigger('<code class="hljs language-tell-rowboat">')).toBe(true);
        expect(hasInlineTaskTrigger("x\n~~~ts\nlet task\n~~~")).toBe(false);
    });

    it("detects live-note frontmatter and combines both", () => {
        expect(hasLiveFrontmatter("---\nlive:\n  objective: x\n---\n")).toBe(true);
        expect(hasLiveFrontmatter("---\nbase: &b {objective: x}\nlive: *b\n---\n")).toBe(true);
        expect(hasLiveFrontmatter("# t\nlive: x\n")).toBe(false);
        expect(hasAutorunTrigger("---\nlive: {objective: x}\n---\n")).toBe(true);
        expect(hasAutorunTrigger("```task\n{}\n```")).toBe(true);
        expect(hasAutorunTrigger("---\ntitle: a\n---\n# plain\n")).toBe(false);
    });
});
