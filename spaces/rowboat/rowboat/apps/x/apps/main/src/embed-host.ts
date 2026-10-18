import fsSync from 'node:fs';
import fs from 'node:fs/promises';
import path from 'node:path';
import type { IpcMainInvokeEvent } from 'electron';
import { WorkDir } from '@x/core/dist/config/config.js';
import { generateEmbedToken, startEmbedBridge, type EmbedBridge } from '@x/server';
import { isEmbedUnsupportedInvoke } from '@x/shared/dist/embed-channels.js';
import { invokeIpcChannel, listInvokeChannels } from './ipc.js';
import { setEmbedSink } from './embed-sink.js';

// VibeMind-Einbettung (Spec vibemind-os 2026-10-17-rowboat-einbettung): startet den lokalen
// Zugang und schreibt embed.json, ueber die sich die Multiverse-Ansicht der voice-App verbindet.

export const EMBED_INFO_FILE = 'embed.json';

export function embedRequested(
  argv: readonly string[] = process.argv,
  env: NodeJS.ProcessEnv = process.env,
): boolean {
  return argv.includes('--embed') || env.ROWBOAT_EMBED === '1';
}

let bridge: EmbedBridge | null = null;

export async function startEmbedHost(options: {
  showApp: () => void;
  preloadPath: string;
  serveApp: (request: Request) => Promise<Response>;
}): Promise<void> {
  if (bridge) return;
  const token = generateEmbedToken();
  const live = await startEmbedBridge({
    token,
    invoke: (channel, sender, args) =>
      invokeIpcChannel(channel, { sender } as unknown as IpcMainInvokeEvent, args),
    invokeChannels: listInvokeChannels,
    isUnsupported: isEmbedUnsupportedInvoke,
    serveApp: options.serveApp,
    onShowApp: options.showApp,
    // app:openUrl ist ein Fenster-Kanal: nur an die anderen Einbettungs-Verbindungen, nie ueber
    // mirrorToEmbed und nie an Rowboats eigenes Fenster.
    onOpenUrl: (url, sender) => live.broadcastExcept('app:openUrl', { url }, sender),
    log: (message) => console.warn(message),
  });
  bridge = live;
  setEmbedSink((channel, payload) => live.broadcast(channel, payload));
  const file = path.join(WorkDir, EMBED_INFO_FILE);
  const tmp = `${file}.${process.pid}.tmp`;
  const info = { v: 1, port: live.port, token, pid: process.pid, preloadPath: options.preloadPath };
  await fs.writeFile(tmp, JSON.stringify(info), { mode: 0o600 });
  await fs.rename(tmp, file);
  console.log(`[embed] Einbettungszugang auf 127.0.0.1:${live.port} (${EMBED_INFO_FILE})`);
}

/** Beim Beenden: embed.json nur loeschen, wenn sie von diesem Prozess stammt. */
export function stopEmbedHost(): void {
  if (!bridge) return;
  setEmbedSink(null);
  const file = path.join(WorkDir, EMBED_INFO_FILE);
  try {
    const info = JSON.parse(fsSync.readFileSync(file, 'utf8')) as { pid?: unknown };
    if (info.pid === process.pid) fsSync.rmSync(file, { force: true });
  } catch {
    // weg oder fremd: nichts zu tun
  }
  void bridge.close();
  bridge = null;
}
