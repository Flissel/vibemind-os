import z from 'zod';
import { useSyncExternalStore } from 'react';
import { LiveNoteAgentEvent } from '@x/shared/dist/live-note.js';

export type LiveNoteAgentStatus = 'idle' | 'running' | 'done' | 'error';

export interface LiveNoteAgentState {
    status: LiveNoteAgentStatus;
    runId?: string;
    summary?: string | null;
    error?: string | null;
}

// Module-level store — shared across all hook consumers, subscribed once.
// We replace the Map on every mutation so useSyncExternalStore detects the change.
let store = new Map<string, LiveNoteAgentState>();
const listeners = new Set<() => void>();
let subscribed = false;

function updateStore(fn: (prev: Map<string, LiveNoteAgentState>) => void) {
    store = new Map(store);
    fn(store);
    for (const listener of listeners) listener();
}

function ensureSubscription() {
    if (subscribed) return;
    subscribed = true;
    window.ipc.on('live-note-agent:events', ((event: z.infer<typeof LiveNoteAgentEvent>) => {
        const key = event.filePath;

        if (event.type === 'live_note_agent_start') {
            updateStore(s => s.set(key, { status: 'running', runId: event.runId }));
        } else if (event.type === 'live_note_agent_complete') {
            updateStore(s => s.set(key, {
                status: event.error ? 'error' : 'done',
                runId: event.runId,
                summary: event.summary ?? null,
                error: event.error ?? null,
            }));
            // Auto-clear after 5 seconds
            setTimeout(() => {
                updateStore(s => s.delete(key));
            }, 5000);
        }
    }) as (event: z.infer<typeof LiveNoteAgentEvent>) => void);
}

function subscribe(onStoreChange: () => void): () => void {
    ensureSubscription();
    listeners.add(onStoreChange);
    return () => { listeners.delete(onStoreChange); };
}

function getSnapshot(): Map<string, LiveNoteAgentState> {
    return store;
}

/**
 * Returns a Map of all live-note agent run states, keyed by `filePath`.
 *
 * Usage in a panel:
 *   const status = useLiveNoteAgentStatus();
 *   const state = status.get(filePath) ?? { status: 'idle' };
 *
 * Usage for a global indicator:
 *   const status = useLiveNoteAgentStatus();
 *   const anyRunning = [...status.values()].some(s => s.status === 'running');
 */
export function useLiveNoteAgentStatus(): Map<string, LiveNoteAgentState> {
    return useSyncExternalStore(subscribe, getSnapshot);
}
