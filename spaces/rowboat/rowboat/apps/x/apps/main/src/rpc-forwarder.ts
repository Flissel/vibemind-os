import { isRpcChannel } from '@x/server';
import { whenServerReady } from './server-host.js';

// Strangler-fig seam (RFC SERVER_CLIENT_SPEC.md Q4/Q15): channels that have
// migrated to rowboat-server are forwarded over real localhost HTTP instead
// of calling core in-process, so the network API is exercised by the desktop
// app on every keystroke — it cannot rot. Unmigrated channels are untouched.
//
// Forwarding is ON everywhere, packaged builds included — an HTTP path only
// dev traffic exercises is the API-rot trap Q2 exists to prevent.
// ROWBOAT_FORWARD_MIGRATED=0 is the emergency kill switch.

export function forwardingEnabled(): boolean {
  const env = process.env.ROWBOAT_FORWARD_MIGRATED;
  if (env !== undefined) {
    return env !== '0' && env.toLowerCase() !== 'false';
  }
  return true;
}

export function shouldForwardChannel(channel: string): boolean {
  return forwardingEnabled() && isRpcChannel(channel);
}

// opts.headers (VibeMind #330, Task 17 fix round 1; fix round 3): the caller's
// principal headers from core's forwardHeadersForOrigin. For a restricted
// caller they lower the owner-key request to a scoped one on the server: no
// protected workspace entries, no permission answer as the human
// (apps/server server.ts, core-deps.ts). The trusted embed sends none.
export async function forwardRpc(channel: string, args: unknown, opts?: { headers?: Record<string, string> }): Promise<unknown> {
  const server = await whenServerReady();
  const res = await fetch(`${server.baseUrl}/rpc/${channel}`, {
    method: 'POST',
    headers: {
      ...(opts?.headers ?? {}),
      'content-type': 'application/json',
      authorization: `Bearer ${server.key}`,
    },
    body: JSON.stringify(args ?? null),
  });
  const body = (await res.json().catch(() => null)) as
    | { error?: { code?: string; message?: string } }
    | Record<string, unknown>
    | null;
  if (!res.ok) {
    const message =
      (body as { error?: { message?: string } } | null)?.error?.message ??
      `rpc ${channel} failed with status ${res.status}`;
    throw new Error(message);
  }
  return body;
}
