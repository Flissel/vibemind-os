// Modified for VibeMind (Flissel/rowboat vibemind): new module (ticket #330, Task 17).
// Which kind of key authenticated the request a handler is running for. The auth middleware
// (server.ts) runs the rest of the request inside runWithAuth; handlers read currentAuthKind().
// Outside a request (a direct call) it is null, which callers treat like a scoped key (fail closed).
import { AsyncLocalStorage } from 'node:async_hooks';

export type AuthKind = 'owner' | 'scoped';

const storage = new AsyncLocalStorage<AuthKind>();

export function runWithAuth<T>(kind: AuthKind, fn: () => T): T {
  return storage.run(kind, fn);
}

export function currentAuthKind(): AuthKind | null {
  return storage.getStore() ?? null;
}
