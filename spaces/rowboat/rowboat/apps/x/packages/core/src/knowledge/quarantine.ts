// Modified for VibeMind (Flissel/rowboat vibemind): new module (Teil E, fix
// round 4). The channel quarantine: runs started from a mobile channel
// (Telegram) may only write under knowledge/Kanal/, and nothing autonomous
// may read that folder — no background scanner (tagging, curation, graph
// building, knowledge index, live notes, inline tasks) and no run that is not
// a desktop user chat. A prompt injection that reaches a channel run (web
// pages, search results, GitHub content) therefore cannot plant instructions
// for the background agents, which hold far broader tool sets.
//
// The folder name is compared case-insensitively, so knowledge/kanal/ on a
// case-sensitive file system is quarantined as well.

import path from 'path';
import { WorkDir } from '../config/config.js';

export const QUARANTINE_FOLDER = 'Kanal';
// Workspace-relative, with a trailing slash — the form used in messages and
// in the channel run instructions.
export const QUARANTINE_REL = `knowledge/${QUARANTINE_FOLDER}/`;

export function isQuarantineFolderName(name: string): boolean {
    return name.toLowerCase() === QUARANTINE_FOLDER.toLowerCase();
}

// True when a path relative to knowledge/ (either separator) lies in the
// quarantine, the folder itself included.
export function isQuarantinedKnowledgeRel(relPath: string): boolean {
    const first = relPath.replace(/^[\\/]+/, '').split(/[\\/]/)[0] ?? '';
    return isQuarantineFolderName(first);
}

// True when an absolute path lies in <knowledgeDir>/Kanal (folder included).
export function isQuarantinedUnder(knowledgeDir: string, absPath: string): boolean {
    const rel = path.relative(knowledgeDir, absPath);
    if (!rel || rel.startsWith('..') || path.isAbsolute(rel)) return false;
    return isQuarantinedKnowledgeRel(rel);
}

// Same, against the live workspace (background scanners).
export function isQuarantinedPath(absPath: string): boolean {
    return isQuarantinedUnder(path.join(WorkDir, 'knowledge'), absPath);
}
