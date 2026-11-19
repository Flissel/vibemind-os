// Modified for VibeMind (Flissel/rowboat vibemind): moved here from
// apps/main/src/ipc.ts (ticket #330, Task 17 fix round 2) so the shell:*
// channels and the restricted-caller gate resolve a path the same way.
import os from 'node:os';
import path from 'node:path';
import { resolveWorkspacePath } from './workspace.js';

// The path a shell:* channel (previewFile, readFileBase64, openPath,
// showItemInFolder, dialog defaultPath) acts on: "~…" under the home folder
// (every "~" prefix, as before), an absolute path as given, anything else
// inside the workspace (no "..").
export function resolveShellPath(filePath: string): string {
  if (filePath.startsWith('~')) {
    return path.join(os.homedir(), filePath.slice(1));
  }

  if (path.isAbsolute(filePath)) {
    return filePath;
  }

  return resolveWorkspacePath(filePath);
}

// "~", "~/x" and "~\x" are the home folder; "~name" and "~.x" are not a home
// expansion anyone means (resolveShellPath still joins them to the home
// folder). The restricted-caller gate refuses those.
export function isPlainHomePrefix(filePath: string): boolean {
  if (!filePath.startsWith('~')) return true;
  return filePath === '~' || filePath.startsWith('~/') || filePath.startsWith('~\\');
}
