// Modified for VibeMind (Flissel/rowboat vibemind): new module (Teil E, fix
// round 2). Execution-time check used by the file write builtins when the
// ToolContext says autorun content is forbidden (channel runs and every turn
// that is not a desktop user chat — background agents such as note tagging
// or curation). Such a run may not INTRODUCE an autorun trigger (see
// autorun-triggers.ts): a write whose result has a trigger the target did
// not already have is refused. Existing live notes stay editable by their
// own agents (the trigger is already there).

import fs from "node:fs/promises";
import path from "node:path";
import { resolveFilePath } from "../filesystem/files.js";
import { hasAutorunTrigger } from "./autorun-triggers.js";

const MAX_SCAN_FILES = 2000;

// The text a write/edit would leave in the file. For an edit whose target
// cannot be read the inserted text is judged on its own — once as a
// frontmatter block, once as body — so neither kind of trigger slips by.
export function resultingWriteContent(
    toolName: string,
    args: Record<string, unknown>,
    current: string | null,
): string | null {
    if (toolName === "file-writeText") {
        return typeof args.data === "string" ? args.data : null;
    }
    if (toolName === "file-editText") {
        const oldString = typeof args.oldString === "string" ? args.oldString : "";
        const newString = typeof args.newString === "string" ? args.newString : "";
        if (current !== null && oldString) {
            return args.replaceAll === true
                ? current.split(oldString).join(newString)
                : current.replace(oldString, () => newString);
        }
        return `---\n${newString}\n---\n${newString}`;
    }
    return null;
}

async function readOrNull(absPath: string): Promise<string | null> {
    try {
        return await fs.readFile(absPath, "utf8");
    } catch {
        return null;
    }
}

// True when the path (file, or any file below a directory) carries a
// trigger. Unreadable or oversized trees count as carrying one (fail closed).
async function treeHasTrigger(absPath: string): Promise<boolean> {
    let stat;
    try {
        stat = await fs.stat(absPath);
    } catch {
        return false;
    }
    if (!stat.isDirectory()) {
        const text = await readOrNull(absPath);
        return text !== null && hasAutorunTrigger(text);
    }
    let seen = 0;
    const stack = [absPath];
    while (stack.length > 0) {
        const dir = stack.pop() as string;
        let entries;
        try {
            entries = await fs.readdir(dir, { withFileTypes: true });
        } catch {
            return true;
        }
        for (const entry of entries) {
            const full = path.join(dir, entry.name);
            if (entry.isDirectory()) {
                stack.push(full);
                continue;
            }
            if (++seen > MAX_SCAN_FILES) return true;
            const text = await readOrNull(full);
            if (text !== null && hasAutorunTrigger(text)) return true;
        }
    }
    return false;
}

// Error message when the write would introduce an autorun trigger, else null.
export async function autorunIntroductionError(
    toolName: string,
    args: Record<string, unknown>,
): Promise<string | null> {
    const refuse = (what: string) =>
        `Refused: ${what} would create a note that starts a background agent (live: / live_note frontmatter or a task / tell-rowboat block). Only the user can create such notes from the desktop chat.`;
    if (toolName === "file-writeText" || toolName === "file-editText") {
        if (typeof args.path !== "string" || !args.path) return null;
        const target = resolveFilePath(args.path).resolvedPath;
        const current = await readOrNull(target);
        const next = resultingWriteContent(toolName, args, current);
        if (next === null || !hasAutorunTrigger(next)) return null;
        // Keeping a trigger the file already had is not an introduction.
        if (current !== null && hasAutorunTrigger(current)) return null;
        return refuse(toolName === "file-writeText" ? "this write" : "this edit");
    }
    if (toolName === "file-copy" || toolName === "file-rename") {
        if (typeof args.from !== "string" || typeof args.to !== "string") return null;
        const from = resolveFilePath(args.from).resolvedPath;
        if (await treeHasTrigger(from)) {
            return refuse(toolName === "file-copy" ? "this copy" : "this move");
        }
    }
    return null;
}
