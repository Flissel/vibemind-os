// HTML-Konzepte im Wissensgraphen: Text für den [[…]]-Leser, Knotennamen, HTML-Dateien, Verweise ohne Endung.
import { konzept } from '@x/shared'
import { splitWikiLinkTargets, wikiLinkTargets } from './graph-hierarchy'
import { ensureMarkdownExtension, normalizeWikiPath, splitWikiFragment, toKnowledgePath, wikiLabel } from './wiki-links'

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
 * Schlüssel der Kurzform `[[x]]`: Dateiname ohne Ordner und ohne `.md`- bzw. HTML-Endung, klein geschrieben. Gleiche
 * Regel wie der Namensabgleich des Editors (`resolveWikiFilePath`: `toLowerCase`, keine Unicode-Normalisierung).
 */
function namensSchluessel(path: string): string {
  const name = path.replace(/\\/g, '/').split('/').pop() ?? path
  return name.replace(/\.(md|html?|xhtml)$/i, '').toLowerCase()
}

/** Name -> alle Pfade dieses Namens (Notizen und HTML, auch Meetings). Einmal je Graph-Aufbau bzw. Dateiliste bauen. */
export type NamensIndex = ReadonlyMap<string, readonly string[]>

export function namensIndex(pfade: Iterable<string>): NamensIndex {
  const index = new Map<string, string[]>()
  for (const p of pfade) {
    const schluessel = namensSchluessel(p)
    const liste = index.get(schluessel)
    if (liste) liste.push(p)
    else index.set(schluessel, [p])
  }
  return index
}

/**
 * Kurzform `[[x]]` (kein Ordner, keine Endung) -> das eine Wissens-Konzept dieses Namens, wenn im ganzen Wissensordner
 * genau EINE Datei (Notiz oder HTML) so heißt und sie ein Konzept ist; sonst null (mehrdeutig, Notiz, nichts).
 */
export function konzeptPerName(base: string, namen: NamensIndex): string | null {
  if (base === '' || base.includes('/') || base.includes('\\')) return null
  const treffer = namen.get(base.toLowerCase())
  return treffer && treffer.length === 1 && konzept.isKnowledgeHtmlPath(treffer[0]) ? treffer[0] : null
}

/**
 * Verweis -> Knoten-Pfad. Mit Endung wie bisher (toKnowledgePath). Ohne Endung: `x.md`, wenn es existiert; sonst
 * genau eine von `x.html`/`x.htm`/`x.xhtml`, wenn nur eine existiert. Nur wenn das nichts findet und `namen` gegeben
 * ist: {@link konzeptPerName} (ein Nachschlagen, O(1)). Sonst wie bisher (`x.md`, unaufgelöst, wenn es fehlt).
 */
export function resolveWikiTarget(
  raw: string,
  exists: (knowledgePath: string) => boolean,
  namen?: NamensIndex,
): string | null {
  const standard = toKnowledgePath(raw)
  if (!standard) return null
  const base = splitWikiFragment(normalizeWikiPath(raw)).path
  if (standard !== `knowledge/${base}.md` || base.toLowerCase().endsWith('.md')) return standard
  if (exists(standard)) return standard
  const hits = HTML_ENDUNGEN.map((e) => `knowledge/${base}${e}`).filter(exists)
  if (hits.length === 1) return hits[0]
  if (hits.length === 0 && namen) {
    const perName = konzeptPerName(base, namen)
    if (perName !== null && exists(perName)) return perName
  }
  return standard
}

/**
 * Die Auflöser eines Graph-Aufbaus. `alleWissensPfade`: alle Notizen und HTML-Dateien des Wissensordners (auch
 * Meetings, sie zählen für die Eindeutigkeit des Namens), einmal gelesen; der Namensindex entsteht nur, wenn darunter
 * ein Wissens-Konzept ist. `body`: Verweise im Rumpf und in `bezuege` (mit Kurzform per Name, Ergebnis je Verweis
 * gemerkt). `frontMatter`: Verweise im Frontmatter (parent, Relationen), nie per Name. Beide liefern nur Knoten aus
 * `nodeSet`, sonst null.
 */
export function graphResolver(
  nodeSet: ReadonlySet<string>,
  alleWissensPfade: Iterable<string>,
): { body: (ziel: string) => string | null; frontMatter: (ziel: string) => string | null } {
  const pfade = Array.from(alleWissensPfade)
  const namen = pfade.some((p) => konzept.isKnowledgeHtmlPath(p)) ? namensIndex(pfade) : undefined
  const exists = (p: string) => nodeSet.has(p)
  const imKnoten = (p: string | null) => (p !== null && nodeSet.has(p) ? p : null)
  const gemerkt = new Map<string, string | null>()
  return {
    body: (ziel) => {
      const alt = gemerkt.get(ziel)
      if (alt !== undefined) return alt
      const neu = imKnoten(resolveWikiTarget(ziel, exists, namen))
      gemerkt.set(ziel, neu)
      return neu
    },
    frontMatter: (ziel) => imKnoten(resolveWikiTarget(ziel, exists)),
  }
}

/**
 * Pfad, den ein Klick auf `[[…]]` im Editor öffnet (oder anlegt). Reihenfolge wie bisher: genauer Pfad (ohne
 * Groß/Klein), dann eine einzige Notiz gleichen Namens, dann `x.html` am selben Pfad, dann ein eindeutiges Konzept per
 * Name ({@link konzeptPerName}), sonst `knowledge/<x>.md` (wird angelegt). `dateien` wie `knowledgeGraphFiles`
 * (ohne `knowledge/`), `namen` aus denselben Dateien mit `knowledge/`.
 */
export function wikiDateiPfad(wikiPath: string, dateien: readonly string[], namen: NamensIndex): string | null {
  const normalized = normalizeWikiPath(wikiPath)
  const { path: basePath } = splitWikiFragment(normalized)
  if (!basePath) return null

  const targetPath = ensureMarkdownExtension(basePath)
  const targetKey = targetPath.toLowerCase()
  const exactMatch = dateien.find((filePath) => normalizeWikiPath(filePath).toLowerCase() === targetKey)
  if (exactMatch) return toKnowledgePath(exactMatch)

  if (!basePath.includes('/')) {
    const targetBaseName = targetPath.split('/').pop()?.toLowerCase()
    const basenameMatches = dateien.filter((filePath) => {
      const normalizedFile = normalizeWikiPath(filePath)
      return normalizedFile.split('/').pop()?.toLowerCase() === targetBaseName
    })
    if (basenameMatches.length === 1) return toKnowledgePath(basenameMatches[0])
  }

  // Extensionless link without x.md: exactly one x.html/.htm/.xhtml wins, else a unique concept by name.
  // Exact-case comparison for the path, same as the graph edges (nodeSet.has).
  const known = new Set(dateien.map((f) => toKnowledgePath(f)))
  const htmlHit = resolveWikiTarget(basePath, (p) => known.has(p), namen)
  if (htmlHit && htmlHit !== toKnowledgePath(basePath)) return htmlHit

  return toKnowledgePath(basePath)
}

// ---- Zwei Graphen: Konzepte (mit Notizen als Bezug) und Notizen (mit Konzepten als Bezug) ----

export type KonzeptKnoten = { id: string; label: string; art: 'konzept' | 'videokonzept' | 'html' | 'notiz' }
export type KonzeptKante = { source: string; target: string; kind: 'verweis' }

/**
 * Der Konzepte-Graph. Hauptknoten: jede HTML-Datei aus `eintraege` (Name aus `<title>`, sonst Dateiname; Art aus dem
 * Datenblock, ohne lesbaren Block `html`; eine nicht gelesene Datei kommt mit leerem `html`). Nebenknoten (`notiz`):
 * jede Notiz, auf die ein Konzept verweist (bezuege oder Verweise im sichtbaren Text) oder die auf ein Konzept verweist
 * (`notizen[].ziele`, die schon gelesenen `[[…]]`-Ziele). Kanten `verweis` in Richtung des Verweises; doppelte
 * zusammengefasst, Selbstbezüge und unaufgelöste Ziele (`resolve` -> null) entfallen. Ordnung: Knoten nach id,
 * Kanten nach source, target, kind. Alle Namen sind reiner Text.
 */
export function buildKonzeptGraph(
  eintraege: { pfad: string; html: string }[],
  notizen: { pfad: string; ziele: string[] }[],
  resolve: (ziel: string) => string | null,
  istKonzept: (pfad: string) => boolean,
): { nodes: KonzeptKnoten[]; edges: KonzeptKante[] } {
  const knoten = new Map<string, KonzeptKnoten>()
  for (const { pfad, html } of eintraege) {
    const capped = html.length > konzept.KONZEPT_LESE_MAX ? html.slice(0, konzept.KONZEPT_LESE_MAX) : html
    const art = konzept.readKonzept(capped).daten?.art ?? 'html'
    knoten.set(pfad, { id: pfad, label: graphNodeLabel(pfad, capped), art })
  }
  const kanten = new Map<string, KonzeptKante>()
  const notizKnoten = (pfad: string) => {
    if (!knoten.has(pfad)) knoten.set(pfad, { id: pfad, label: graphNodeLabel(pfad, ''), art: 'notiz' })
  }
  const kante = (source: string, target: string) => {
    if (source !== target) kanten.set(`${source}\u0000${target}`, { source, target, kind: 'verweis' })
  }
  const konzeptPfade = new Set(eintraege.map((e) => e.pfad))
  for (const { pfad, html } of eintraege) {
    for (const roh of wikiLinkTargets(graphHtmlData(html))) {
      const ziel = resolve(roh)
      if (ziel === null || ziel === pfad) continue
      if (istKonzept(ziel)) {
        if (konzeptPfade.has(ziel)) kante(pfad, ziel)
      } else {
        notizKnoten(ziel)
        kante(pfad, ziel)
      }
    }
  }
  for (const { pfad, ziele } of notizen) {
    if (konzeptPfade.has(pfad)) continue
    for (const roh of ziele) {
      const ziel = resolve(roh)
      if (ziel === null || ziel === pfad || !istKonzept(ziel) || !konzeptPfade.has(ziel)) continue
      notizKnoten(pfad)
      kante(pfad, ziel)
    }
  }
  const vor = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0)
  return {
    nodes: [...knoten.values()].sort((a, b) => vor(a.id, b.id)),
    edges: [...kanten.values()].sort((a, b) => vor(a.source, b.source) || vor(a.target, b.target) || vor(a.kind, b.kind)),
  }
}

/**
 * `ziele` einer Notiz für {@link buildKonzeptGraph}: Verweise im Frontmatter schon streng aufgelöst (als Pfad mit
 * Endung, ohne Kurzform per Name; unaufgelöste entfallen), dann die rohen Verweise des Rumpfs.
 */
export function notizZiele(data: string, frontMatterResolve: (ziel: string) => string | null): string[] {
  const { frontMatter, body } = splitWikiLinkTargets(data)
  const kopf = frontMatter.map(frontMatterResolve).filter((p): p is string => p !== null)
  return [...kopf, ...body]
}

/** Konzepte, die im Notizen-Graphen als Nebenknoten bleiben: mit mindestens einer Kante zu einer Notiz (Richtung egal). */
export function konzepteMitNotizBezug(
  kanten: readonly { source: string; target: string }[],
  istKonzept: (pfad: string) => boolean,
): Set<string> {
  const out = new Set<string>()
  for (const { source, target } of kanten) {
    const s = istKonzept(source)
    const t = istKonzept(target)
    if (s && !t) out.add(source)
    if (t && !s) out.add(target)
  }
  return out
}

/**
 * Der Notizen-Graph aus dem heutigen Graphen: alle Notizen, Konzepte nur mit Notiz-Bezug ({@link konzepteMitNotizBezug}),
 * ohne Kanten zwischen zwei Konzepten. Reihenfolge der Eingabe bleibt.
 */
export function notizGraph<K extends { source: string; target: string }>(
  pfade: readonly string[],
  kanten: readonly K[],
  istKonzept: (pfad: string) => boolean,
): { pfade: string[]; kanten: K[] } {
  const bleiben = konzepteMitNotizBezug(kanten, istKonzept)
  return {
    pfade: pfade.filter((p) => !istKonzept(p) || bleiben.has(p)),
    kanten: kanten.filter((k) => !(istKonzept(k.source) && istKonzept(k.target))),
  }
}
