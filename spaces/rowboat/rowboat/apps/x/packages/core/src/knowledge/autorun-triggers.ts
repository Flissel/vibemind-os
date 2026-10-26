// Modified for VibeMind (Flissel/rowboat vibemind): new module (Teil E, fix
// round 2). Note content that makes a background scheduler start an agent
// with (nearly) every tool — the "autorun triggers":
//   - `live:` frontmatter key        → live-note scheduler (knowledge/live-note)
//   - `live_note` frontmatter field  → inline-task runner (inline_tasks.ts)
//   - a ```task / ```tell-rowboat fence → inline-task runner
// inline_tasks.ts imports its frontmatter parsing and fence detection from
// here, so the write guards and the runner cannot drift apart.

import { splitFrontmatter as splitYamlFrontmatter } from "../application/lib/parse-frontmatter.js";

// ---------------------------------------------------------------------------
// inline_tasks' own (non-YAML) frontmatter helpers — the runner's parser.
// ---------------------------------------------------------------------------

export function splitInlineTaskFrontmatter(content: string): { raw: string | null; body: string } {
    if (!content.startsWith('---')) {
        return { raw: null, body: content };
    }
    const endIndex = content.indexOf('\n---', 3);
    if (endIndex === -1) {
        return { raw: null, body: content };
    }
    const closingEnd = endIndex + 4;
    const raw = content.slice(0, closingEnd);
    let body = content.slice(closingEnd);
    if (body.startsWith('\n')) {
        body = body.slice(1);
    }
    return { raw, body };
}

export function extractAllFrontmatterValues(raw: string | null): Record<string, string | string[]> {
    const result: Record<string, string | string[]> = {};
    if (!raw) return result;

    const lines = raw.split('\n');
    let currentKey: string | null = null;

    for (const line of lines) {
        if (line === '---' || line.trim() === '') {
            currentKey = null;
            continue;
        }
        const topMatch = line.match(/^(\w[\w\s]*\w|\w+):\s*(.*)$/);
        if (topMatch) {
            const key = topMatch[1];
            const value = topMatch[2].trim();
            if (value) {
                result[key] = value;
                currentKey = null;
            } else {
                currentKey = key;
                result[key] = [];
            }
            continue;
        }
        if (currentKey) {
            const itemMatch = line.match(/^\s+-\s+(.+)$/);
            if (itemMatch) {
                const arr = result[currentKey];
                if (Array.isArray(arr)) {
                    arr.push(itemMatch[1].trim());
                }
            }
        }
    }
    return result;
}

// The opening line of an inline-task block, as the runner detects it
// (called with the trimmed line).
export function isInlineTaskFenceStart(trimmedLine: string): boolean {
    return trimmedLine.startsWith('```task') || trimmedLine.startsWith('```tell-rowboat');
}

// ---------------------------------------------------------------------------
// Detection
// ---------------------------------------------------------------------------

// `live:` key as the live-note scheduler parses it (YAML splitFrontmatter),
// plus a textual scan of the leading frontmatter block so YAML tricks still
// count.
export function hasLiveFrontmatter(content: string): boolean {
    try {
        if (Object.prototype.hasOwnProperty.call(splitYamlFrontmatter(content).frontmatter, "live")) {
            return true;
        }
    } catch {
        // fall through to the textual scan
    }
    return frontmatterKeyIn(content, "live");
}

// Textual check for `key:` inside a leading frontmatter block, tolerant of a
// BOM / leading whitespace and flow-style or quoted keys.
function frontmatterKeyIn(content: string, key: string): boolean {
    const block = /^\uFEFF?\s*---[^\n]*\n([\s\S]*?)(?:\r?\n[ \t]*---|$)/.exec(content);
    if (!block) return false;
    return new RegExp(`(^|[\\s{,?])["']?${key}["']?\\s*:`, "m").test(block[1]);
}

// Any `live_note` field (whatever its value — the runner flips it itself)
// or any task / tell-rowboat fence anywhere in the content.
export function hasInlineTaskTrigger(content: string): boolean {
    const { raw } = splitInlineTaskFrontmatter(content);
    if (Object.prototype.hasOwnProperty.call(extractAllFrontmatterValues(raw), 'live_note')) {
        return true;
    }
    // Also a live_note key the runner would not parse today (BOM, leading
    // whitespace, quoted/flow keys), so a parser change cannot reopen it.
    if (frontmatterKeyIn(content, "live_note")) {
        return true;
    }
    if (content.split('\n').some((line) => isInlineTaskFenceStart(line.trim()))) {
        return true;
    }
    // Variants the runner does not parse today but the desktop editor
    // normalizes back to ```task on the next save (~~~ fences, 4+ backticks,
    // `language-task` code classes) — refused so a later save cannot arm them.
    return LOOSE_TASK_FENCE.test(content) || TASK_CODE_CLASS.test(content);
}

const LOOSE_TASK_FENCE = /^[ \t]*(?:`{3,}|~{3,})[ \t]*(?:task|tell-rowboat)/m;
const TASK_CODE_CLASS = /language-(?:task|tell-rowboat)\b/;

export function hasAutorunTrigger(content: string): boolean {
    return hasLiveFrontmatter(content) || hasInlineTaskTrigger(content);
}
