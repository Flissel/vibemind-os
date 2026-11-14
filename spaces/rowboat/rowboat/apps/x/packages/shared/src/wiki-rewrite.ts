// [[…]]-Verweise nachziehen, wenn eine Notiz (.md) oder ein HTML-Konzept unter knowledge/ umbenannt wird.
// Reine Funktionen; Kern und Renderer benutzen dieselbe Logik.
import { KONZEPT_BLOCK_MAX, decodeEntities, htmlUmschreibBereiche, isHtmlPath, isKnowledgeHtmlPath } from './konzept.js'

const WIKI_LINK_REGEX = /\[\[([^[\]]+)\]\]/g
const KNOWLEDGE_PREFIX = 'knowledge/'
const MARKDOWN_EXTENSION = '.md'
const HTML_EXTENSION_REGEX = /\.(html?|xhtml)$/i

export type RewriteOptions = {
  /** Verweise nur mit dem Dateinamen (ohne Ordner) gelten als Verweis auf die Datei selbst. */
  allowBareSelfNameMatch?: boolean
  /** Gibt es unter knowledge/ eine Notiz mit diesem Pfad (klein geschrieben, ohne Präfix und ohne `.md`)? Standard: nein. */
  mdExists?: (wikiStemLower: string) => boolean
  /**
   * Wie viele HTML-Dateien (`.html`/`.htm`/`.xhtml`) tragen nach dem Umbenennen diesen Stamm (klein, ohne Präfix und Endung)?
   * Ein Verweis ohne Endung löst nur bei genau einer auf. Standard: nur die umbenannte Datei.
   */
  htmlStemCount?: (wikiStemLower: string) => number
}

function normalizeRelPath(relPath: string): string {
  return relPath.replace(/\\/g, '/')
}

/** Notiz (.md) oder HTML-Konzept unter knowledge/. */
export function isRewritableKnowledgePath(relPath: string): boolean {
  const normalized = normalizeRelPath(relPath).replace(/^\/+/, '')
  const lower = normalized.toLowerCase()
  if (lower.startsWith(KNOWLEDGE_PREFIX) && lower.endsWith(MARKDOWN_EXTENSION)) return true
  return isKnowledgeHtmlPath(normalized)
}

export function stripKnowledgePrefix(relPath: string): string {
  const normalized = normalizeRelPath(relPath).replace(/^\/+/, '')
  if (!normalized.toLowerCase().startsWith(KNOWLEDGE_PREFIX)) return normalized
  return normalized.slice(KNOWLEDGE_PREFIX.length)
}

function stripMarkdownExtension(wikiPath: string): string {
  return wikiPath.toLowerCase().endsWith(MARKDOWN_EXTENSION) ? wikiPath.slice(0, -MARKDOWN_EXTENSION.length) : wikiPath
}

function stripAnyExtension(wikiPath: string): string {
  return isHtmlPath(wikiPath) ? wikiPath.replace(HTML_EXTENSION_REGEX, '') : stripMarkdownExtension(wikiPath)
}

/** Stamm einer Datei unter knowledge/ (ohne Präfix und Endung, klein): Schlüssel für `mdExists`/`htmlStemCount`. */
export function wikiStemLower(relPath: string): string {
  return stripAnyExtension(stripKnowledgePrefix(relPath)).toLowerCase()
}

function lastSegment(p: string): string {
  return p.split('/').pop() ?? p
}

function splitWikiPathPrefix(rawPath: string): { pathWithoutPrefix: string; hadKnowledgePrefix: boolean } {
  let normalized = rawPath.trim().replace(/^\/+/, '').replace(/^\.\//, '')
  const hadKnowledgePrefix = /^knowledge\//i.test(normalized)
  if (hadKnowledgePrefix) normalized = normalized.slice(KNOWLEDGE_PREFIX.length)
  return { pathWithoutPrefix: normalized, hadKnowledgePrefix }
}

/** `sep`: trennt Pfad von Anker/Alias im ROHEN Text. Im sichtbaren Text gehört `#` hinter `&` zu einer Entität (`&#x27;`). */
type Codec = { decode: (s: string) => string | null; encode: (s: string) => string; sep: RegExp }

const IDENTITY: Codec = { decode: (s) => s, encode: (s) => s, sep: /[|#]/ }

/** Im Datenblock stehen Namen als JSON-Zeichenketten: `"` und `\` bleiben maskiert, `<`, `>`, `&` sind auch in (X)HTML sicher. */
const JSON_CODEC: Codec = {
  sep: /[|#]/,
  decode: (s) => {
    try {
      const v: unknown = JSON.parse(`"${s}"`)
      return typeof v === 'string' ? v : null
    } catch {
      return null
    }
  },
  encode: (s) =>
    JSON.stringify(s)
      .slice(1, -1)
      .replace(/</g, '\\u003c')
      .replace(/>/g, '\\u003e')
      .replace(/&/g, '\\u0026'),
}

const TEXT_CODEC: Codec = {
  sep: /\||(?<!&)#/,
  decode: (s) => decodeEntities(s),
  encode: (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'),
}

/**
 * Wendet `fn` auf jede JSON-Zeichenkette an, die ein WERT ist (kein Objektschlüssel: dahinter folgt kein `:`).
 * Ein Durchlauf, linear. Die erste nicht abgeschlossene Zeichenkette beendet ihn (Rest unverändert).
 * Blöcke über KONZEPT_BLOCK_MAX Zeichen werden nicht angefasst.
 */
function mapJsonStringValues(block: string, fn: (literal: string) => string): string {
  const n = block.length
  if (n > KONZEPT_BLOCK_MAX) return block
  let out = ''
  let pos = 0
  let i = 0
  while (i < n) {
    const q = block.indexOf('"', i)
    if (q === -1) break
    let j = q + 1
    while (j < n && block[j] !== '"') j += block[j] === '\\' ? 2 : 1
    if (j >= n) break
    let k = j + 1
    while (k < n && /\s/.test(block[k])) k++
    if (block[k] !== ':') {
      out += block.slice(pos, q) + fn(block.slice(q, j + 1))
      pos = j + 1
    }
    i = j + 1
  }
  return out + block.slice(pos)
}

function makeRewriter(fromWikiPath: string, toWikiPath: string, opts: RewriteOptions | undefined) {
  const mdExists = opts?.mdExists ?? (() => false)
  const fromIsHtml = isHtmlPath(fromWikiPath)
  const fromLower = fromWikiPath.toLowerCase()
  const fromStem = stripAnyExtension(fromLower)
  const fromBaseLower = lastSegment(fromLower)
  const fromBaseStem = lastSegment(fromStem)

  const toIsHtml = isHtmlPath(toWikiPath)
  const toStem = stripAnyExtension(toWikiPath)
  const toBase = lastSegment(toWikiPath)
  const toBaseStem = lastSegment(toStem)
  const htmlStemCount = opts?.htmlStemCount ?? ((stem: string) => (toIsHtml && stem === toStem.toLowerCase() ? 1 : 0))
  // Nach dem Umbenennen gibt es unter dem alten Stamm keine HTML-Datei mehr und keine Notiz: nur dann meinte der Verweis das Konzept.
  const fromHasNoSibling = !mdExists(fromStem) && htmlStemCount(fromStem) === 0
  // Ohne Endung nur, wenn der Verweis dann noch auf diese Datei zeigt (keine gleichnamige Notiz, keine zweite HTML-Datei).
  const toStemResolves = !toIsHtml || (!mdExists(toStem.toLowerCase()) && htmlStemCount(toStem.toLowerCase()) === 1)

  const matches = (pathWithoutPrefix: string, bare: boolean): boolean => {
    const lower = pathWithoutPrefix.toLowerCase()
    const explicitHtml = isHtmlPath(lower)
    const explicitMd = lower.endsWith(MARKDOWN_EXTENSION)
    if (!fromIsHtml) {
      if (explicitHtml) return false
      return stripMarkdownExtension(lower) === (bare ? fromBaseStem : fromStem)
    }
    if (explicitHtml) return lower === (bare ? fromBaseLower : fromLower)
    if (explicitMd) return false
    return lower === (bare ? fromBaseStem : fromStem) && fromHasNoSibling
  }

  /** `pathPart`: der Pfadteil eines Verweises (vor Anker und Alias), entschlüsselt. Null: nichts zu tun. */
  return (pathPart: string): string | null => {
    const leadingWhitespace = pathPart.match(/^\s*/)?.[0] ?? ''
    const trailingWhitespace = pathPart.match(/\s*$/)?.[0] ?? ''
    const rawPath = pathPart.trim()
    if (!rawPath) return null

    const { pathWithoutPrefix, hadKnowledgePrefix } = splitWikiPathPrefix(rawPath)
    if (!pathWithoutPrefix) return null

    const matchesFullPath = matches(pathWithoutPrefix, false)
    const matchesBareSelfName = Boolean(
      opts?.allowBareSelfNameMatch && !pathWithoutPrefix.includes('/') && matches(pathWithoutPrefix, true),
    )
    if (!matchesFullPath && !matchesBareSelfName) return null

    const explicitExtension = rawPath.toLowerCase().endsWith(MARKDOWN_EXTENSION) || isHtmlPath(rawPath)
    const withExtension = matchesBareSelfName && !matchesFullPath ? toBase : toWikiPath
    const withoutExtension = matchesBareSelfName && !matchesFullPath ? toBaseStem : toStem
    const rewrittenPath = explicitExtension || !toStemResolves ? withExtension : withoutExtension
    const finalPath = hadKnowledgePrefix ? `${KNOWLEDGE_PREFIX}${rewrittenPath}` : rewrittenPath

    return `${leadingWhitespace}${finalPath}${trailingWhitespace}`
  }
}

/** Nur der Pfadteil eines Verweises wird entschlüsselt und neu geschrieben; Anker und Alias bleiben Byte für Byte. */
function rewriteLinks(text: string, rewritePath: (pathPart: string) => string | null, codec: Codec): string {
  return text.replace(WIKI_LINK_REGEX, (fullMatch, innerRaw: string) => {
    const cut = innerRaw.search(codec.sep)
    const pathRaw = cut >= 0 ? innerRaw.slice(0, cut) : innerRaw
    const rest = cut >= 0 ? innerRaw.slice(cut) : ''
    const pathPart = codec.decode(pathRaw)
    if (pathPart === null) return fullMatch
    const rewritten = rewritePath(pathPart)
    return rewritten === null ? fullMatch : `[[${codec.encode(rewritten)}${rest}]]`
  })
}

/** Verweise in Markdown. Pfade ohne `knowledge/`-Präfix. */
export function rewriteWikiLinksInMarkdown(
  markdown: string,
  fromWikiPath: string,
  toWikiPath: string,
  opts?: RewriteOptions,
): string {
  return rewriteLinks(markdown, makeRewriter(fromWikiPath, toWikiPath, opts), IDENTITY)
}

/**
 * Verweise in einer HTML-Datei: im Datenblock (nur innerhalb von JSON-Zeichenketten, JSON-sicher maskiert) und im
 * sichtbaren Text. Tags, andere script-Blöcke, style und Kommentare bleiben unberührt (`a[[0]]` im Skript ist Code,
 * `[[0]]` als JSON-Array im Block auch).
 */
export function rewriteWikiLinksInHtml(
  html: string,
  fromWikiPath: string,
  toWikiPath: string,
  opts?: RewriteOptions,
): string {
  if (!html.includes('[[')) return html
  const rewritePath = makeRewriter(fromWikiPath, toWikiPath, opts)
  let out = ''
  let pos = 0
  for (const b of htmlUmschreibBereiche(html)) {
    out += html.slice(pos, b.start)
    const teil = html.slice(b.start, b.end)
    out +=
      b.art === 'json'
        ? mapJsonStringValues(teil, (literal) => rewriteLinks(literal, rewritePath, JSON_CODEC))
        : rewriteLinks(teil, rewritePath, TEXT_CODEC)
    pos = b.end
  }
  return out + html.slice(pos)
}

/** Je nach Dateiendung der Datei, in der die Verweise stehen. */
export function rewriteWikiLinksInFile(
  filePath: string,
  content: string,
  fromWikiPath: string,
  toWikiPath: string,
  opts?: RewriteOptions,
): string {
  return isHtmlPath(filePath)
    ? rewriteWikiLinksInHtml(content, fromWikiPath, toWikiPath, opts)
    : rewriteWikiLinksInMarkdown(content, fromWikiPath, toWikiPath, opts)
}
