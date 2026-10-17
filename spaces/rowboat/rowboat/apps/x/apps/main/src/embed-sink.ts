import { BrowserWindow } from 'electron';
import { isEmbedSender } from '@x/server';
import { isEmbedMirrored } from '@x/shared/dist/embed-channels.js';

// VibeMind-Einbettung (Spec vibemind-os 2026-10-17-rowboat-einbettung). Ohne --embed bleibt der
// sink leer und alles verhaelt sich wie bisher.
let sink: ((channel: string, payload: unknown) => void) | null = null;

export function setEmbedSink(next: ((channel: string, payload: unknown) => void) | null): void {
  sink = next;
}

/** An alle Fenster Gesendetes zusaetzlich an jede Einbettungs-Verbindung (nur EMBED_MIRRORED_PUSH). */
export function mirrorToEmbed(channel: string, payload: unknown): void {
  if (sink && isEmbedMirrored(channel)) sink(channel, payload);
}

/** Fenster des Absenders; null fuer die Einbettung — Dialoge oeffnen dann ohne Elternfenster. */
export function senderWindow(sender: Electron.WebContents): BrowserWindow | null {
  return isEmbedSender(sender) ? null : BrowserWindow.fromWebContents(sender);
}
