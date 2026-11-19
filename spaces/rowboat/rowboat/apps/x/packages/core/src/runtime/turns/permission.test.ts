// Modified for VibeMind (Flissel/rowboat vibemind): human-only requests (ticket #330).
import { describe, expect, it } from "vitest";
import { isHumanOnlyRequest, partitionHumanOnly } from "./permission.js";

describe("human-only permission requests", () => {
    it("recognises only an object with humanOnly === true", () => {
        expect(isHumanOnlyRequest({ kind: "file", humanOnly: true })).toBe(true);
        for (const request of [{ kind: "file" }, { humanOnly: "true" }, { humanOnly: 1 }, [true], null, undefined, "humanOnly"]) {
            expect(isHumanOnlyRequest(request), JSON.stringify(request)).toBe(false);
        }
    });

    it("splits legacy candidates: human-only ones never go to the classifier", () => {
        const a = { id: "a", permission: { kind: "file", humanOnly: true } };
        const b = { id: "b", permission: { kind: "command", commandNames: ["rm"] } };
        const c = { id: "c", permission: { kind: "command", commandNames: ["echo"], humanOnly: true } };
        expect(partitionHumanOnly([a, b, c])).toEqual({ humanOnly: [a, c], classifiable: [b] });
    });
});
