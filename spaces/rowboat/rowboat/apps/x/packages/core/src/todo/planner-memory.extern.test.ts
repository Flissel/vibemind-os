import { afterAll, describe, expect, it } from 'vitest';
import fs from 'fs';
import os from 'os';
import path from 'path';

// WorkDir is read from the env at module load, so set it before the
// dynamic imports (static imports are hoisted).
const workDir = fs.mkdtempSync(path.join(os.tmpdir(), 'x-planner-extern-test-'));
process.env.ROWBOAT_WORKDIR = workDir;
const {
    addExternalSuggestions, withdrawSuggestion, listPlannerSignals, listSuggestions, recordPlannerSignal,
} = await import('./planner-memory.js');

const FEEDBACK = path.join(workDir, 'todo', 'planner_feedback.json');

afterAll(() => {
    fs.rmSync(workDir, { recursive: true, force: true });
});

describe('external suggestions', () => {
    it('adds deduped texts without a proposed signal', async () => {
        const added = await addExternalSuggestions(['A ⟶ x', 'A ⟶ x']);
        expect(added).toEqual(['A ⟶ x']);
        expect(await listSuggestions()).toEqual(['A ⟶ x']);
        const signals = await listPlannerSignals();
        expect(signals.some((s) => s.kind === 'proposed')).toBe(false);
    });

    it('skips texts the user dismissed before', async () => {
        await recordPlannerSignal('dismissed', 'B');
        expect(await addExternalSuggestions(['b'])).toEqual([]);
    });

    it('skips texts already on the todo list', async () => {
        fs.writeFileSync(path.join(workDir, 'todo.md'), '- [ ] C\n', 'utf-8');
        expect(await addExternalSuggestions(['C'])).toEqual([]);
    });

    it('withdraws a suggestion without any signal', async () => {
        const before = await listPlannerSignals();
        expect(await withdrawSuggestion('A ⟶ x')).toBe(true);
        expect(await listSuggestions()).not.toContain('A ⟶ x');
        expect(await listPlannerSignals()).toEqual(before);
        expect(await withdrawSuggestion('A ⟶ x')).toBe(false);
    });

    it('returns an empty signal list for a corrupt ledger', async () => {
        fs.writeFileSync(FEEDBACK, '{kaputt', 'utf-8');
        expect(await listPlannerSignals()).toEqual([]);
    });
});
