import type { LiveNoteAgentEventType } from '@x/shared/dist/live-note.js';

type Handler = (event: LiveNoteAgentEventType) => void;

class LiveNoteBus {
    private subs: Handler[] = [];

    publish(event: LiveNoteAgentEventType): void {
        for (const handler of this.subs) {
            handler(event);
        }
    }

    subscribe(handler: Handler): () => void {
        this.subs.push(handler);
        return () => {
            const idx = this.subs.indexOf(handler);
            if (idx >= 0) this.subs.splice(idx, 1);
        };
    }
}

export const liveNoteBus = new LiveNoteBus();
