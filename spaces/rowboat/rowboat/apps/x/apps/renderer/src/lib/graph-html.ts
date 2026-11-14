// HTML-Konzepte im Wissensgraphen: Text für den [[…]]-Leser, Knotennamen, HTML-Dateien, Verweise ohne Endung.
import { konzept } from '@x/shared'
import { normalizeWikiPath, splitWikiFragment, toKnowledgePath, wikiLabel } from './wiki-links'

/**
 * Text einer HTML-Datei für den vorhandenen [[…]]-Leser des Graphen: die bezuege des Datenblocks plus der sichtbare
 * Text (ohne Skripte, Stile, Kommentare). Beides höchstens KONZEPT_LESE_MAX Zeichen aus dem Dateitext.
 */
export function graphHtmlData(raw: string): string {
  const capped = raw.length > konzept.KONZEPT_LESE_MAX ? raw.slice(0, konzept.KONZEPT_LESE_MAX) : raw
  const bezuege = konzept.readKonzept(capped).daten?.bezuege.join(' ') ?? ''
  return `${bezuege} ${konzept.htmlVisibleText(capped)}`
}

/** Anzeigename eines Knotens (reiner Text, kein Markup): bei HTML der <title> oder der Dateiname ohne Endung. */
export function graphNodeLabel(path: string, data: string): string {
  if (konzept.isHtmlPath(path)) {
    const name = path.replace(/\\/g, '/').split('/').pop() ?? path
    return konzept.htmlTitle(data) || name.replace(/\.(html?|xhtml)$/i, '') || path
  }
  return wikiLabel(path) || path
}

/**
 * Eine HTML-Datei für den Graphen: Link-Text (graphHtmlData) und Name zusammen aus dem ROHEN, gekappten HTML.
 * Der Name kommt nie aus dem tag-losen Link-Text (dort wären entschlüsselte Entitäten als <title> lesbar).
 */
export function graphHtmlNode(path: string, raw: string): { data: string; label: string } {
  const capped = raw.length > konzept.KONZEPT_LESE_MAX ? raw.slice(0, konzept.KONZEPT_LESE_MAX) : raw
  return { data: graphHtmlData(capped), label: graphNodeLabel(path, capped) }
}

/** HTML-Dateien unter knowledge/ aus der Baumliste, ohne Präfix und mit Endung (damit [[…x.html]] auflöst). */
export function knowledgeHtmlFiles(treePaths: string[]): string[] {
  return Array.from(new Set(
    treePaths.filter((p) => konzept.isKnowledgeHtmlPath(p)).map((p) => p.replace(/^knowledge\//, '')),
  ))
}

export function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

/** Beschriftung für Senken, die HTML erwarten (react-force-graph `nodeLabel`/`linkLabel`). */
export function graphLabelHtml(name: string): string {
  return escapeHtml(name)
}

/** Ein Link auf eine nicht vorhandene HTML-Datei darf keine Markdown-Notiz in einen HTML-Pfad schreiben. */
export function canCreateWikiTarget(path: string): boolean {
  return !konzept.isHtmlPath(path)
}

const HTML_ENDUNGEN = ['.html', '.htm', '.xhtml']

/**
 * Verweis -> Knoten-Pfad. Mit Endung wie bisher (toKnowledgePath). Ohne Endung: `x.md`, wenn es existiert; sonst
 * genau eine von `x.html`/`x.htm`/`x.xhtml`, wenn nur eine existiert; sonst wie bisher (`x.md`).
 */
export function resolveWikiTarget(raw: string, exists: (knowledgePath: string) => boolean): string | null {
  const standard = toKnowledgePath(raw)
  if (!standard) return null
  const base = splitWikiFragment(normalizeWikiPath(raw)).path
  if (standard !== `knowledge/${base}.md` || base.toLowerCase().endsWith('.md')) return standard
  if (exists(standard)) return standard
  const hits = HTML_ENDUNGEN.map((e) => `knowledge/${base}${e}`).filter(exists)
  return hits.length === 1 ? hits[0] : standard
}
