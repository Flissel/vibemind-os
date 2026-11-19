// Modified for VibeMind (Flissel/rowboat vibemind): RPC accepts only Content-Type application/json (#330, fix round 2).
import { Hono } from 'hono';
import { z } from 'zod';
import { ipc } from '@x/shared';
import { isRpcChannel, type RpcHandlers } from './channels.js';

// POST /rpc/{channel} — body and response are the channel's existing Zod
// req/res schemas from @x/shared ipcSchemas, so the wire contract is the IPC
// contract. The router is generic; only the handler map knows the channels.
export function createRpcRoutes(handlers: RpcHandlers): Hono {
  const app = new Hono();

  app.post('/rpc/:channel', async (c) => {
    // VibeMind (#330, fix round 2, review N2): only JSON clients. A browser
    // form (text/plain, urlencoded, multipart) or a cross-origin request
    // without a CORS preflight cannot send application/json, so a page the
    // agent opens can never drive RPC even when it knows a key. Every
    // legitimate client (packages/client rpc.ts, main's rpc-forwarder and
    // server-host, the VibeMind bots) sends application/json.
    if (!isJsonContentType(c.req.header('content-type'))) {
      return c.json(
        { error: { code: 'unsupported_media_type', message: 'POST /rpc/* needs Content-Type: application/json' } },
        415,
      );
    }
    const channel = c.req.param('channel');
    // Unexposed channels 404 like unknown ones — don't enumerate the surface.
    if (!isRpcChannel(channel) || !(channel in handlers)) {
      return c.json({ error: { code: 'unknown_channel', message: `unknown channel: ${channel}` } }, 404);
    }

    let body: unknown = null;
    const raw = await c.req.text();
    if (raw.length > 0) {
      try {
        body = JSON.parse(raw);
      } catch {
        return c.json({ error: { code: 'invalid_request', message: 'body is not valid JSON' } }, 400);
      }
    }

    let args;
    try {
      args = ipc.validateRequest(channel, body);
    } catch (err) {
      const issues = err instanceof z.ZodError ? err.issues : undefined;
      return c.json({ error: { code: 'invalid_request', message: 'request failed validation', issues } }, 400);
    }

    try {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const result = await (handlers[channel] as (a: unknown) => Promise<unknown>)(args as any);
      return c.json(ipc.validateResponse(channel, result) as object);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      // Only the message crosses the wire; the cause chain (an errno, a
      // nested socket error) is worth its line here, where it dies otherwise.
      const cause = err instanceof Error && err.cause !== undefined ? ` — cause: ${describeCause(err.cause)}` : '';
      console.error(`[server] rpc ${channel} failed: ${message}${cause}`);
      return c.json({ error: { code: 'internal', message } }, 500);
    }
  });

  return app;
}

// The media type alone, parameters (charset) ignored, case-insensitive.
export function isJsonContentType(header: string | undefined): boolean {
  if (!header) return false;
  return header.split(';')[0].trim().toLowerCase() === 'application/json';
}

function describeCause(cause: unknown): string {
  if (cause instanceof AggregateError) return cause.errors.map(describeCause).join('; ');
  if (cause instanceof Error) {
    const code = (cause as { code?: unknown }).code;
    return typeof code === 'string' ? `${code} ${cause.message}`.trim() : cause.message;
  }
  return String(cause);
}
