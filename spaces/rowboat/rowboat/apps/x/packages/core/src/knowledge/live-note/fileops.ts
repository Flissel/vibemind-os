import fs from 'fs/promises';
import path from 'path';
import { LiveNoteSchema, type LiveNote } from '@x/shared/dist/live-note.js';
import { WorkDir } from '../../config/config.js';
import { withFileLock } from '../file-lock.js';
import { splitFrontmatter, joinFrontmatter } from '../../application/lib/parse-frontmatter.js';

const KNOWLEDGE_DIR = path.join(WorkDir, 'knowledge');

function absPath(filePath: string): string {
    return path.join(KNOWLEDGE_DIR, filePath);
}

function getLiveBlock(fm: Record<string, unknown>): unknown {
    return fm.live ?? null;
}

function setLiveBlock(fm: Record<string, unknown>, live: unknown): Record<string, unknown> {
    const next = { ...fm };
    if (live === null || live === undefined) {
        delete next.live;
    } else {
        next.live = live;
    }
    return next;
}

// ---------------------------------------------------------------------------
// Read
// ---------------------------------------------------------------------------

export async function fetchLiveNote(filePath: string): Promise<LiveNote | null> {
    let content: string;
    try {
        content = await fs.readFile(absPath(filePath), 'utf-8');
    } catch {
        return null;
    }
    const { frontmatter } = splitFrontmatter(content);
    const raw = getLiveBlock(frontmatter);
    if (!raw) return null;
    const parsed = LiveNoteSchema.safeParse(raw);
    return parsed.success ? parsed.data : null;
}

export async function readNoteBody(filePath: string): Promise<string> {
    let content: string;
    try {
        content = await fs.readFile(absPath(filePath), 'utf-8');
    } catch {
        return '';
    }
    return splitFrontmatter(content).body;
}

// ---------------------------------------------------------------------------
// Write
// ---------------------------------------------------------------------------

/**
 * Replace (or create) the entire `live:` block. The renderer's structured
 * editor calls this with the complete object; runtime patches go through
 * {@link patchLiveNote}.
 */
export async function setLiveNote(filePath: string, live: LiveNote): Promise<void> {
    const validated = LiveNoteSchema.parse(live);
    return withFileLock(absPath(filePath), async () => {
        const content = await fs.readFile(absPath(filePath), 'utf-8');
        const { frontmatter, body } = splitFrontmatter(content);
        const nextFm = setLiveBlock(frontmatter, validated);
        await fs.writeFile(absPath(filePath), joinFrontmatter(nextFm, body), 'utf-8');
    });
}

/**
 * Merge a partial update into the `live:` block. Used by the runner to
 * write `lastRunAt` / `lastRunId` / `lastRunSummary` without round-tripping
 * the rest of the user-authored config through schema validation.
 */
export async function patchLiveNote(
    filePath: string,
    updates: Partial<LiveNote>,
): Promise<void> {
    return withFileLock(absPath(filePath), async () => {
        const content = await fs.readFile(absPath(filePath), 'utf-8');
        const { frontmatter, body } = splitFrontmatter(content);
        const existing = getLiveBlock(frontmatter);
        if (!existing || typeof existing !== 'object') {
            throw new Error(`No live: block in ${filePath}`);
        }
        const merged = { ...(existing as Record<string, unknown>), ...updates };
        const nextFm = setLiveBlock(frontmatter, merged);
        await fs.writeFile(absPath(filePath), joinFrontmatter(nextFm, body), 'utf-8');
    });
}

export async function deleteLiveNote(filePath: string): Promise<void> {
    return withFileLock(absPath(filePath), async () => {
        const content = await fs.readFile(absPath(filePath), 'utf-8');
        const { frontmatter, body } = splitFrontmatter(content);
        if (!getLiveBlock(frontmatter)) return; // already passive
        const nextFm = setLiveBlock(frontmatter, null);
        await fs.writeFile(absPath(filePath), joinFrontmatter(nextFm, body), 'utf-8');
    });
}

export async function setLiveNoteActive(
    filePath: string,
    active: boolean,
): Promise<LiveNoteSummary | null> {
    return withFileLock(absPath(filePath), async () => {
        const content = await fs.readFile(absPath(filePath), 'utf-8');
        const { frontmatter, body } = splitFrontmatter(content);
        const existing = getLiveBlock(frontmatter);
        if (!existing || typeof existing !== 'object') return null;

        const current = existing as Record<string, unknown>;
        const currentlyActive = current.active !== false;
        if (currentlyActive !== active) {
            const merged = { ...current, active };
            const nextFm = setLiveBlock(frontmatter, merged);
            await fs.writeFile(absPath(filePath), joinFrontmatter(nextFm, body), 'utf-8');
        }

        const validated = await fetchLiveNote(filePath);
        return validated ? buildSummary(filePath, validated) : null;
    });
}

// ---------------------------------------------------------------------------
// Note-level summaries (background-agents view)
// ---------------------------------------------------------------------------

export type LiveNoteSummary = {
    path: string;
    createdAt: string | null;
    lastRunAt: string | null;
    isActive: boolean;
    objective: string;
};

function buildSummaryFromStat(filePath: string, live: LiveNote, createdMs: number): LiveNoteSummary {
    return {
        path: `knowledge/${filePath}`,
        createdAt: createdMs > 0 ? new Date(createdMs).toISOString() : null,
        lastRunAt: live.lastRunAt ?? null,
        isActive: live.active !== false,
        objective: live.objective,
    };
}

async function buildSummary(filePath: string, live: LiveNote): Promise<LiveNoteSummary> {
    const stats = await fs.stat(absPath(filePath));
    const createdMs = stats.birthtimeMs > 0 ? stats.birthtimeMs : stats.ctimeMs;
    return buildSummaryFromStat(filePath, live, createdMs);
}

export async function listLiveNotes(): Promise<LiveNoteSummary[]> {
    async function walk(relativeDir = ''): Promise<string[]> {
        const dirPath = absPath(relativeDir);
        try {
            const entries = await fs.readdir(dirPath, { withFileTypes: true });
            const files: string[] = [];
            for (const entry of entries) {
                if (entry.name.startsWith('.')) continue;
                const childRelPath = relativeDir
                    ? path.posix.join(relativeDir, entry.name)
                    : entry.name;
                if (entry.isDirectory()) {
                    files.push(...await walk(childRelPath));
                    continue;
                }
                if (entry.isFile() && entry.name.toLowerCase().endsWith('.md')) {
                    files.push(childRelPath);
                }
            }
            return files;
        } catch {
            return [];
        }
    }

    const markdownFiles = await walk();
    const summaries = await Promise.all(markdownFiles.map(async (relativePath) => {
        try {
            const live = await fetchLiveNote(relativePath);
            if (!live) return null;
            return await buildSummary(relativePath, live);
        } catch {
            return null;
        }
    }));

    return summaries
        .filter((note): note is LiveNoteSummary => note !== null)
        .sort((a, b) => {
            const aName = path.basename(a.path, '.md').toLowerCase();
            const bName = path.basename(b.path, '.md').toLowerCase();
            if (aName !== bName) return aName.localeCompare(bName);
            return a.path.localeCompare(b.path);
        });
}
