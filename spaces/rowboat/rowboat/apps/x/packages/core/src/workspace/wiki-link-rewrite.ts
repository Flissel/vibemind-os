import fs from 'node:fs/promises';
import path from 'node:path';

import { konzept, wikiRewrite } from '@x/shared';
import { withFileLock } from '../knowledge/file-lock.js';

const { isRewritableKnowledgePath, rewriteWikiLinksInFile, stripKnowledgePrefix, wikiStemLower } = wikiRewrite;

function normalizeRelPath(relPath: string): string {
  return relPath.replace(/\\/g, '/');
}

async function collectKnowledgeLinkFiles(workspaceRoot: string): Promise<string[]> {
  const knowledgeRoot = path.join(workspaceRoot, 'knowledge');
  try {
    const stat = await fs.lstat(knowledgeRoot);
    if (!stat.isDirectory()) return [];
  } catch {
    return [];
  }

  const linkFiles: string[] = [];
  const pendingDirectories: string[] = [knowledgeRoot];

  while (pendingDirectories.length > 0) {
    const currentDirectory = pendingDirectories.pop();
    if (!currentDirectory) continue;

    const entries = await fs.readdir(currentDirectory, { withFileTypes: true });
    for (const entry of entries) {
      if (entry.name.startsWith('.')) continue;

      const absolutePath = path.join(currentDirectory, entry.name);
      if (entry.isDirectory()) {
        pendingDirectories.push(absolutePath);
        continue;
      }
      if (!entry.isFile()) continue;
      const relativePath = normalizeRelPath(path.relative(workspaceRoot, absolutePath));
      if (!isRewritableKnowledgePath(relativePath)) continue;
      linkFiles.push(relativePath);
    }
  }

  return linkFiles;
}

export async function rewriteWikiLinksForRenamedKnowledgeFile(
  workspaceRoot: string,
  fromRelPath: string,
  toRelPath: string
): Promise<number> {
  const normalizedFrom = normalizeRelPath(fromRelPath);
  const normalizedTo = normalizeRelPath(toRelPath);

  if (!isRewritableKnowledgePath(normalizedFrom) || !isRewritableKnowledgePath(normalizedTo)) {
    return 0;
  }

  const fromWikiPath = stripKnowledgePrefix(normalizedFrom);
  const toWikiPath = stripKnowledgePrefix(normalizedTo);
  if (fromWikiPath.toLowerCase() === toWikiPath.toLowerCase()) return 0;

  const linkFiles = await collectKnowledgeLinkFiles(workspaceRoot);
  let rewrittenFiles = 0;

  // Stämme nach dem Umbenennen (ohne Präfix und Endung, klein): entscheiden, ob ein Verweis ohne Endung noch das Konzept meint.
  const noteStems = new Set<string>();
  const htmlStemCounts = new Map<string, number>();
  for (const relativePath of linkFiles) {
    const stem = wikiStemLower(relativePath);
    if (relativePath.toLowerCase().endsWith('.md')) noteStems.add(stem);
    else htmlStemCounts.set(stem, (htmlStemCounts.get(stem) ?? 0) + 1);
  }
  const mdExists = (stem: string) => noteStems.has(stem);
  const htmlStemCount = (stem: string) => htmlStemCounts.get(stem) ?? 0;

  const utf8 = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true });
  const normalizedToLower = normalizedTo.toLowerCase();
  for (const relativePath of linkFiles) {
    const absolutePath = path.join(workspaceRoot, ...relativePath.split('/'));
    try {
      // HTML über dem Limit der Ansicht wird nicht angefasst.
      if (konzept.isHtmlPath(relativePath) && (await fs.stat(absolutePath)).size > konzept.KONZEPT_HTML_MAX_BYTES) continue;

      const bytes = await fs.readFile(absolutePath);
      // Nur gültiges UTF-8, das sich byte-genau zurückkodieren lässt (BOM bleibt erhalten); alles andere bleibt unberührt.
      let content: string;
      try {
        content = utf8.decode(bytes);
      } catch {
        continue;
      }
      if (!content.includes('[[')) continue;
      if (!Buffer.from(content, 'utf8').equals(bytes)) continue;

      const isRenamedFile = normalizeRelPath(relativePath).toLowerCase() === normalizedToLower;
      const rewritten = rewriteWikiLinksInFile(relativePath, content, fromWikiPath, toWikiPath, {
        allowBareSelfNameMatch: isRenamedFile,
        mdExists,
        htmlStemCount,
      });
      if (rewritten === content) continue;

      // Atomar (Temp-Datei + Rename) unter demselben Dateisperre-Schlüssel wie workspace.writeFile.
      await withFileLock(absolutePath, async () => {
        const tempPath = `${absolutePath}.tmp.${Date.now()}${Math.random().toString(36).slice(2)}`;
        try {
          await fs.writeFile(tempPath, rewritten, 'utf8');
          await fs.rename(tempPath, absolutePath);
        } catch (error) {
          await fs.rm(tempPath, { force: true }).catch(() => undefined);
          throw error;
        }
      });
      rewrittenFiles += 1;
    } catch (error) {
      console.error('Failed to rewrite wiki links in file:', relativePath, error);
    }
  }

  return rewrittenFiles;
}
