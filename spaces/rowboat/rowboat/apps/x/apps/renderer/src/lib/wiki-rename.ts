import { konzept, wikiRewrite } from '@x/shared'

/**
 * [[…]]-Verweise in einem geöffneten Dokument nachziehen, wenn die Datei `fromRelPath` nach `toRelPath` umbenannt wurde.
 * Gleiche Logik wie im Kern (Notizen und HTML-Konzepte unter knowledge/). Das Dokument ist hier die umbenannte Datei
 * selbst, daher gelten Verweise nur mit ihrem Namen als Verweis auf sich. Ob eine Notiz gleichen Namens existiert, ist
 * hier unbekannt: Verweise ohne Endung auf ein Konzept werden deshalb nicht umgeschrieben.
 */
export function rewriteWikiLinksForRenamedFileInMarkdown(markdown: string, fromRelPath: string, toRelPath: string): string {
  if (!wikiRewrite.isRewritableKnowledgePath(fromRelPath) || !wikiRewrite.isRewritableKnowledgePath(toRelPath)) {
    return markdown
  }
  const fromWikiPath = wikiRewrite.stripKnowledgePrefix(fromRelPath)
  const toWikiPath = wikiRewrite.stripKnowledgePrefix(toRelPath)
  return wikiRewrite.rewriteWikiLinksInMarkdown(markdown, fromWikiPath, toWikiPath, {
    allowBareSelfNameMatch: true,
    mdExists: () => true,
  })
}

/**
 * Geöffnete Dokumente (Editor-Zwischenspeicher) beim Umbenennen im Baum. Ordner und HTML-Konzepte lassen sie unberührt:
 * HTML liegt nie im Editor, ein Zwischenspeicher gehört dann zur zuletzt geöffneten Notiz und darf nicht umgeschrieben werden.
 */
export function rewriteOpenDocumentForRename(content: string, oldPath: string, newPath: string, isDir: boolean): string {
  if (isDir || konzept.isHtmlPath(oldPath)) return content
  return rewriteWikiLinksForRenamedFileInMarkdown(content, oldPath, newPath)
}

/** Neuer Dateiname beim Umbenennen im Baum: Notizen enden auf `.md`, HTML-Konzepte behalten ihre Endung. */
export function renamedFileName(oldPath: string, newName: string, isDir: boolean): string {
  if (isDir) return newName
  if (konzept.isHtmlPath(oldPath)) {
    if (konzept.isHtmlPath(newName)) return newName
    const ext = /\.[^./\\]+$/.exec(oldPath)?.[0] ?? '.html'
    return `${newName}${ext}`
  }
  return newName.endsWith('.md') ? newName : `${newName}.md`
}
