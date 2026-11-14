// Gemeinsame Bausteine der Konzept-Fläche: reine Funktionen ohne DOM (Node und Browser).

/**
 * Kein-Netz-Regel für Wissens-HTML (R40): CSP plus `sandbox allow-scripts` (das Dokument ist auch dann
 * abgeschottet, wenn das iframe-Attribut fehlt). Gilt nur für HTML unter `knowledge/` (R38). `webrtc 'block'` entfiel:
 * Chromium 142 kennt die Anweisung nicht; WebRTC sperrt der Gastbereich (Proxy der Partition plus WebRTC-Vorgabe).
 */
export const WORKSPACE_HTML_CSP =
  "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; media-src data: blob:; form-action 'none'; base-uri 'none'; sandbox allow-scripts"

export const KONZEPT_BLOCK_ID = 'rowboat-konzept'
export const KONZEPT_MAX_BYTES = 1_048_576
export const KONZEPT_LESE_MAX = 1_048_576
/** Größte HTML-Datei, die Ansicht, Umbenennen (Verweise nachziehen) und Versionsverlauf noch anfassen. */
export const KONZEPT_HTML_MAX_BYTES = 5 * 1024 * 1024
/** Längster Datenblock (Zeichen), den Leser und Umschreiben noch beachten. */
export const KONZEPT_BLOCK_MAX = 20_000

export const BILDARTEN = ['slide', 'avatar_slide', 'avatar_gross', 'clip'] as const

export type VideoSzene = { kurz: string; bildart: (typeof BILDARTEN)[number]; notiz?: string }
export type VideoStufe = { name: string; szenen: VideoSzene[] }
export type VideoKonzept = {
  thema: string
  zielgruppe: string
  minuten: number
  hook: string
  kernaussage: string
  stufen: VideoStufe[]
  abschluss: string
  auftrag: null
  firma?: string
}
export type KonzeptDaten = { art: 'konzept' | 'videokonzept'; bezuege: string[]; video?: VideoKonzept }

/** Pfad in Segmente zerlegen: Trenner `\` und `/`, leere und `.`-Segmente entfallen. */
function segmente(relPath: string): string[] {
  return relPath.replace(/\\/g, '/').split('/').filter((s) => s !== '' && s !== '.')
}

/** Windows ignoriert Punkte und Leerzeichen am Segmentende. */
function segName(s: string): string {
  return s.replace(/[. ]+$/, '').toLowerCase()
}

export function isHtmlPath(relPath: string): boolean {
  return /\.(html?|xhtml)$/i.test(relPath)
}

export function isKnowledgeHtmlPath(relPath: string): boolean {
  const segs = segmente(relPath)
  if (segs.includes('..')) return false
  return segs.length >= 2 && segName(segs[0]) === 'knowledge' && isHtmlPath(segs[segs.length - 1])
}

/** Quarantäne-Ordner `knowledge/Kanal/`. Bei `..` im Pfad wird sicherheitshalber `true` geliefert. */
export function isKanalPath(relPath: string): boolean {
  const segs = segmente(relPath)
  if (segs.includes('..')) return true
  return segs.length >= 2 && segName(segs[0]) === 'knowledge' && segName(segs[1]) === 'kanal'
}

function codepoint(n: number, roh: string): string {
  return n >= 1 && n <= 0x10ffff && !(n >= 0xd800 && n <= 0xdfff) ? String.fromCodePoint(n) : roh
}

export function decodeEntities(s: string): string {
  return s
    .replace(/&#(\d{1,7});/g, (m, d: string) => codepoint(parseInt(d, 10), m))
    .replace(/&#x([0-9a-f]{1,6});/gi, (m, h: string) => codepoint(parseInt(h, 16), m))
    .replace(/&nbsp;/g, ' ')
    .replace(/&apos;/g, "'")
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&amp;/g, '&')
}

function begrenzt(html: string): string {
  return html.length > KONZEPT_LESE_MAX ? html.slice(0, KONZEPT_LESE_MAX) : html
}

/** `s`/`e`: Offsets von Text bzw. Rohinhalt (script/style) im Eingabestring. */
type Token =
  | { k: 'text'; v: string; s: number; e: number }
  | { k: 'open' | 'close'; name: string; attrs: Record<string, string> }
  | { k: 'raw'; name: string; attrs: Record<string, string>; body: string; s: number; e: number }

const WS = /\s/

/** Liest ein Tag ab `start` (zeigt auf `<`). Liefert null, wenn es nicht abgeschlossen ist. Linear. */
function parseTag(
  h: string,
  start: number,
): { closing: boolean; name: string; attrs: Record<string, string>; end: number } | null {
  const n = h.length
  let j = start + 1
  const closing = h[j] === '/'
  if (closing) j++
  const ns = j
  while (j < n && !WS.test(h[j]) && h[j] !== '/' && h[j] !== '>') j++
  const name = h.slice(ns, j).toLowerCase()
  const attrs: Record<string, string> = {}
  for (;;) {
    while (j < n && (WS.test(h[j]) || h[j] === '/')) j++
    if (j >= n) return null
    if (h[j] === '>') return { closing, name, attrs, end: j + 1 }
    const as = j
    j++ // mindestens ein Zeichen (auch ein führendes `=`)
    while (j < n && !WS.test(h[j]) && h[j] !== '=' && h[j] !== '>' && h[j] !== '/') j++
    const an = h.slice(as, j).toLowerCase()
    let k = j
    while (k < n && WS.test(h[k])) k++
    let wert = ''
    if (h[k] === '=') {
      k++
      while (k < n && WS.test(h[k])) k++
      if (k >= n) return null
      const q = h[k]
      if (q === '"' || q === "'") {
        const e = h.indexOf(q, k + 1)
        if (e === -1) return null
        wert = h.slice(k + 1, e)
        j = e + 1
      } else {
        const vs = k
        while (k < n && !WS.test(h[k]) && h[k] !== '>') k++
        wert = h.slice(vs, k)
        j = k
      }
    }
    if (!Object.prototype.hasOwnProperty.call(attrs, an)) attrs[an] = wert
  }
}

/**
 * Ein linearer Durchlauf über höchstens KONZEPT_LESE_MAX Zeichen. Kommentare entfallen,
 * script/style kommen als `raw`-Token mit Inhalt. Nicht abgeschlossene Konstrukte beenden den Lauf.
 * `ohneDeckel` liest die ganze Datei (Umschreiben von Verweisen), sonst höchstens KONZEPT_LESE_MAX Zeichen.
 */
function scan(html: string, ohneDeckel = false): Token[] {
  const h = ohneDeckel ? html : begrenzt(html)
  const n = h.length
  const out: Token[] = []
  let textStart = 0
  let i = 0
  const flush = (end: number) => {
    if (end > textStart) out.push({ k: 'text', v: h.slice(textStart, end), s: textStart, e: end })
  }
  while (i < n) {
    const lt = h.indexOf('<', i)
    if (lt === -1) break
    const c = h[lt + 1]
    if (h.startsWith('<!--', lt)) {
      flush(lt)
      const e = h.indexOf('-->', lt + 4)
      if (e === -1) return out
      i = textStart = e + 3
    } else if (c === '!' || c === '?') {
      flush(lt)
      const e = h.indexOf('>', lt + 2)
      if (e === -1) return out
      i = textStart = e + 1
    } else if (c !== undefined && (c === '/' || /[A-Za-z]/.test(c))) {
      flush(lt)
      const t = parseTag(h, lt)
      if (t === null) return out
      i = textStart = t.end
      if (t.closing) {
        out.push({ k: 'close', name: t.name, attrs: t.attrs })
      } else if (t.name === 'script' || t.name === 'style') {
        const re = new RegExp(`</${t.name}(?=[\\s/>])`, 'gi')
        re.lastIndex = t.end
        const m = re.exec(h)
        if (m === null) {
          out.push({ k: 'raw', name: t.name, attrs: t.attrs, body: h.slice(t.end), s: t.end, e: n })
          return out
        }
        out.push({ k: 'raw', name: t.name, attrs: t.attrs, body: h.slice(t.end, m.index), s: t.end, e: m.index })
        const e = h.indexOf('>', m.index)
        if (e === -1) return out
        i = textStart = e + 1
      } else {
        out.push({ k: 'open', name: t.name, attrs: t.attrs })
      }
    } else {
      i = lt + 1 // einzelnes `<` ist Text
    }
  }
  flush(n)
  return out
}

export function htmlTitle(html: string): string | null {
  const toks = scan(html)
  for (let i = 0; i < toks.length; i++) {
    const t = toks[i]
    if (t.k === 'open' && t.name === 'title') {
      let text = ''
      for (let j = i + 1; j < toks.length; j++) {
        const u = toks[j]
        if (u.k !== 'text') break
        text += u.v
      }
      const r = decodeEntities(text).trim()
      return r === '' ? null : r
    }
  }
  return null
}

export function htmlVisibleText(html: string): string {
  const teile: string[] = []
  for (const t of scan(html)) if (t.k === 'text') teile.push(t.v)
  return decodeEntities(teile.join(' ')).replace(/\s+/g, ' ').trim()
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

/** Rohtext des ersten Blocks (`<script type="application/json" id="rowboat-konzept">`) in Dokumentreihenfolge. */
function istDatenblock(t: Token): t is Extract<Token, { k: 'raw' }> {
  if (t.k !== 'raw' || t.name !== 'script' || t.attrs.id !== KONZEPT_BLOCK_ID) return false
  return (t.attrs.type ?? '').split(';')[0].trim().toLowerCase() === 'application/json'
}

function findBlock(html: string): string | null {
  for (const t of scan(html)) if (istDatenblock(t)) return t.body
  return null
}

export type UmschreibBereich = { start: number; end: number; art: 'json' | 'text' }

/**
 * Bereiche einer HTML-Datei, in denen `[[...]]` als Verweis gilt: der Inhalt des ersten Datenblocks
 * (`art: 'json'`) und Text außerhalb von Tags, script, style und Kommentaren (`art: 'text'`).
 * Gleicher Scanner wie die Leser (`readKonzept`, `htmlVisibleText`), aber ohne 1-MB-Deckel.
 */
export function htmlUmschreibBereiche(html: string): UmschreibBereich[] {
  const out: UmschreibBereich[] = []
  let blockGefunden = false
  for (const t of scan(html, true)) {
    if (t.k === 'text') out.push({ start: t.s, end: t.e, art: 'text' })
    else if (!blockGefunden && istDatenblock(t)) {
      blockGefunden = true
      if (t.e > t.s) out.push({ start: t.s, end: t.e, art: 'json' })
    }
  }
  return out
}

/**
 * Liest den ersten Datenblock. Ein unbrauchbarer Block liefert `daten: null` und genau einen Satz.
 * `bezuege` darf beliebige Zeichenketten enthalten; Einträge ohne `[[...]]` ergeben nur einen Hinweis
 * in `fehler` (bei weiterhin gesetztem `daten`). Bei `videokonzept` wird `video` UNGEPRÜFT durchgereicht
 * (nur als Objekt bestätigt); Verbraucher müssen es mit `checkVideoKonzept` prüfen.
 */
export function readKonzept(html: string): { daten: KonzeptDaten | null; fehler: string[] } {
  const roh = findBlock(html)
  if (roh === null) return { daten: null, fehler: [] }
  const fehler = (satz: string) => ({ daten: null, fehler: [satz] })
  if (roh.length > KONZEPT_BLOCK_MAX) {
    return fehler(`Der Datenblock ist länger als ${KONZEPT_BLOCK_MAX} Zeichen und wird ignoriert.`)
  }
  let parsed: unknown
  try {
    parsed = JSON.parse(roh)
  } catch {
    return fehler('Der Datenblock enthält kein gültiges JSON und wird ignoriert.')
  }
  if (!isRecord(parsed)) return fehler('Der Datenblock muss ein JSON-Objekt sein und wird ignoriert.')
  if (parsed.art !== 'konzept' && parsed.art !== 'videokonzept') {
    return fehler('Im Datenblock fehlt "art" mit dem Wert "konzept" oder "videokonzept".')
  }
  const bezuege = parsed.bezuege
  if (!Array.isArray(bezuege) || !bezuege.every((b): b is string => typeof b === 'string')) {
    return fehler('Im Datenblock muss "bezuege" eine Liste von Zeichenketten sein.')
  }
  const daten: KonzeptDaten = { art: parsed.art, bezuege }
  if (parsed.art === 'videokonzept') {
    if (!isRecord(parsed.video)) return fehler('Beim Videokonzept fehlt im Datenblock das Objekt "video".')
    daten.video = parsed.video as unknown as VideoKonzept
  }
  const hinweise: string[] = []
  if (!bezuege.every((b) => /^\[\[[\s\S]+\]\]$/.test(b.trim()))) {
    hinweise.push(
      'Mindestens ein Eintrag in "bezuege" steht nicht in doppelten eckigen Klammern, z. B. "[[Marke/Marke]]", und ergibt keine Verknüpfung.',
    )
  }
  return { daten, fehler: hinweise }
}

function textLaenge(v: unknown, min: number, max: number): boolean {
  return typeof v === 'string' && v.trim().length >= min && v.length <= max
}

export function checkVideoKonzept(v: unknown): string[] {
  if (!isRecord(v)) return ['Das Videokonzept fehlt oder ist kein Objekt.']
  const f: string[] = []
  if (!textLaenge(v.thema, 3, 300)) f.push('Das Thema muss 3 bis 300 Zeichen lang sein.')
  if (!textLaenge(v.zielgruppe, 1, Infinity)) f.push('Die Zielgruppe fehlt.')
  if (typeof v.minuten !== 'number' || !(v.minuten >= 3 && v.minuten <= 5)) f.push('Die Länge muss zwischen 3 und 5 Minuten liegen.')
  if (!textLaenge(v.hook, 1, Infinity)) f.push('Der Hook fehlt.')
  if (!textLaenge(v.kernaussage, 1, Infinity)) f.push('Die Kernaussage fehlt.')
  if (!textLaenge(v.abschluss, 1, Infinity)) f.push('Der Abschluss fehlt.')
  if (v.auftrag !== null) f.push('Der Auftrag muss leer (null) sein.')
  if (v.firma !== undefined && typeof v.firma !== 'string') f.push('Die Firma muss ein Text sein.')
  if (!Array.isArray(v.stufen)) {
    f.push('Die Stufen fehlen oder sind keine Liste.')
    return f
  }
  if (v.stufen.length > 8) f.push('Es sind höchstens 8 Stufen erlaubt.')
  let szenenGesamt = 0
  let stufenOk = true
  let kurzOk = true
  let bildartOk = true
  let notizOk = true
  for (const s of v.stufen) {
    if (!isRecord(s) || typeof s.name !== 'string' || !Array.isArray(s.szenen)) {
      stufenOk = false
      continue
    }
    for (const sz of s.szenen) {
      szenenGesamt++
      if (!isRecord(sz)) {
        kurzOk = false
        bildartOk = false
        continue
      }
      if (typeof sz.kurz !== 'string' || sz.kurz.length > 300) kurzOk = false
      if (typeof sz.bildart !== 'string' || !(BILDARTEN as readonly string[]).includes(sz.bildart)) bildartOk = false
      if (sz.notiz !== undefined && typeof sz.notiz !== 'string') notizOk = false
    }
  }
  if (!stufenOk) f.push('Jede Stufe braucht einen Namen und eine Liste von Szenen.')
  if (szenenGesamt < 8 || szenenGesamt > 15) f.push(`Es sind insgesamt 8 bis 15 Szenen nötig, gefunden wurden ${szenenGesamt}.`)
  if (!kurzOk) f.push('Jede Szene braucht eine Kurzbeschreibung mit höchstens 300 Zeichen.')
  if (!bildartOk) f.push('Die Bildart jeder Szene muss slide, avatar_slide, avatar_gross oder clip sein.')
  if (!notizOk) f.push('Eine Notiz muss ein Text sein.')
  return f
}

const ohneEndpunkt = (x: string) => x.replace(/[. ]+$/, '')

/**
 * Liegt ein reiner, arbeitsverzeichnis-relativer Pfad im Wissensordner (erstes Segment `knowledge`, Groß/Klein und
 * Endpunkt egal)? Bei `..` sicherheitshalber `true`: dann gilt die strengere Regel.
 */
export function isKnowledgeScopePath(relPath: string): boolean {
  const segs = segmente(relPath)
  if (segs.includes('..')) return true
  return segs.length >= 2 && segName(segs[0]) === 'knowledge'
}

/**
 * Rahmen-Schutz (R43): zeigt ein Rahmen ein Dokument unter `app://workspace/knowledge/`, gleich welcher Endung? Ein
 * Nicht-HTML-Dokument führt kein Skript aus, es einzusperren kostet nichts; HTML unter einem seltsamen Namen ist so
 * mit erfasst. Schema und Host ohne Groß/Klein; der Pfad (ohne Query/Fragment) zählt roh und prozent-dekodiert,
 * `\` trennt wie `/`, `.`-Segmente entfallen. Ungültige Kodierung oder `..`: eingesperrt. Adressen außerhalb von
 * app://workspace (auch leer oder about:blank beim ersten Laden eines Rahmens): nicht eingesperrt.
 * Endung und Content-Type zählen nur beim Ausliefern ({@link workspaceResponseDecision}).
 */
export function isKnowledgeFrameUrl(url: string): boolean {
  const m = /^app:\/\/workspace(?=[/?#]|$)/i.exec(url)
  if (m === null) return false
  const roh = url.slice(m[0].length).split(/[?#]/)[0]
  let dekodiert: string
  try {
    dekodiert = decodeURIComponent(roh)
  } catch {
    return true
  }
  const formen = [segmente(roh), segmente(dekodiert)]
  if (formen.some((segs) => segs.includes('..'))) return true
  if (!formen.some((segs) => segs.length >= 1 && segName(segs[0]) === 'knowledge')) return false
  // R49/R51: PDFs zeigt Chromiums eingebauter Betrachter (chrome-extension:) im Rahmen der Ansicht; sie bleiben wie vor
  // der Konzept-Fläche. Es zählt der genaue, einmal dekodierte letzte Name (ohne Kürzen von Punkten/Leerzeichen, ohne
  // Steuerzeichen), so wie der Server den Typ wählt: x.pdf geht als application/pdf hinaus; käme sie je als text/html,
  // bekäme sie die Regel und stünde im Ausgeliefert-Register.
  const teile = dekodiert.split(/[\\/]/)
  const letzte = teile[teile.length - 1]
  // eslint-disable-next-line no-control-regex
  return !(formen[1].length >= 2 && /\.pdf$/i.test(letzte) && !/[\u0000-\u001f\u007f]/.test(letzte))
}

/** Schlüssel, unter dem ein ausgeliefertes Dokument wiedererkannt wird: die Adresse ohne Fragment. */
export function konzeptDocumentKey(url: string): string {
  const i = url.indexOf('#')
  return i === -1 ? url : url.slice(0, i)
}

function htmlAntwortKoepfe(): Record<string, string> {
  return {
    'Content-Type': 'text/html; charset=utf-8',
    'Content-Security-Policy': WORKSPACE_HTML_CSP,
    'X-Content-Type-Options': 'nosniff',
    'X-DNS-Prefetch-Control': 'off',
  }
}

function mimeOhneParameter(contentType: string | null): string {
  return (contentType ?? '').split(';')[0].trim().toLowerCase()
}

/**
 * Sieht EINER der Namen nach HTML aus oder ist der Content-Type HTML/XHTML? Die Pfade sind REINE Namen (bereits
 * dekodierter Anfragepfad ohne Query/Fragment, aufgelöster Dateisystempfad): nur das letzte Segment zählt, `#`, `?`,
 * `%` sind gewöhnliche Zeichen.
 */
export function shouldApplyHtmlHeaders(plainPaths: string[], contentType: string | null): boolean {
  const mime = mimeOhneParameter(contentType)
  if (mime === 'text/html' || mime === 'application/xhtml+xml') return true
  return plainPaths.some((p) => {
    const segs = p.split(/[\\/]/)
    return isHtmlPath(ohneEndpunkt(segs[segs.length - 1]))
  })
}

/** Die Köpfe für eine Antwort, die {@link workspaceResponseDecision} als `'konzept'` einstuft. */
export function htmlResponseHeaders(): Record<string, string> {
  return htmlAntwortKoepfe()
}

/** Content-Type fehlt oder ist generisch: Chromium könnte den Inhalt als Dokument erraten. */
export function needsNosniffOnly(contentType: string | null): boolean {
  const mime = mimeOhneParameter(contentType)
  return mime === '' || mime === 'application/octet-stream' || mime === 'text/plain'
}

/**
 * Was eine Antwort von app://workspace bzw. GET /workspace bekommt (R38, R9). Nur Wissens-HTML bekommt die Regel.
 * `relPaths` sind REINE arbeitsverzeichnis-relative Namen: der dekodierte Anfragepfad und, wo bekannt, das aufgelöste
 * Ziel relativ zur aufgelösten Wurzel. Liegt keiner davon unter `knowledge/`, bleibt die Antwort wie vor der
 * Konzept-Fläche (`'unveraendert'`), außer sie trägt die Regel schon (`csp`, vom Fern-Server entschieden). Im
 * Wissensordner: HTML nach einem der Namen oder nach Content-Type -> `'konzept'` (Fehlerantworten nur nach
 * Content-Type, ein 404-Text bleibt Text); fehlender oder generischer Typ -> `'nosniff'`.
 */
export function workspaceResponseDecision(args: {
  relPaths: string[]
  contentType: string | null
  ok?: boolean
  csp?: string | null
}): 'konzept' | 'nosniff' | 'unveraendert' {
  if (args.csp === WORKSPACE_HTML_CSP) return 'konzept'
  if (!args.relPaths.some(isKnowledgeScopePath)) return 'unveraendert'
  const html = args.ok === false
    ? shouldApplyHtmlHeaders([], args.contentType)
    : shouldApplyHtmlHeaders(args.relPaths, args.contentType)
  if (html) return 'konzept'
  return needsNosniffOnly(args.contentType) ? 'nosniff' : 'unveraendert'
}

/**
 * Darf ein Rahmen wegnavigieren? Nein, wenn er ein Dokument unter `app://workspace/knowledge/` zeigt (R43), der
 * Hauptprozess es als Wissens-HTML ausgeliefert hat (Symlink-Ziel, Content-Type), oder der Rahmen bzw. ein Vorfahr
 * laut Rückhalt-Zustand eingesperrt ist (`confinedByState`, R47, {@link isFrameChainConfined}): keine Navigation im
 * Rahmen, auch nicht zu app://. Der erste Ladevorgang eines neuen Rahmens ist nicht betroffen (Adresse noch
 * leer/about:blank, Knoten noch nicht im Zustand). Andere Rahmen entscheidet der Aufrufer wie vor der Konzept-Fläche.
 */
export function frameNavigationDecision(
  frameUrl: string,
  servedAsKnowledgeHtml = false,
  confinedByState = false,
): 'allow' | 'block' {
  return confinedByState || servedAsKnowledgeHtml || isKnowledgeFrameUrl(frameUrl) ? 'block' : 'allow'
}

function schema(url: string): string | null {
  const m = /^([a-z][a-z0-9+.-]*):/i.exec(url)
  return m ? m[1].toLowerCase() : null
}

/** Bleibt im Dokument, nie im Netz: für jede Anfrage frei (R47). */
const DOKUMENT_SCHEMATA = new Set(['data', 'blob', 'about'])

/**
 * Verkehr der App selbst, der bei unbekanntem Rahmen trotz offenem Konzept frei bleibt (R47): app:, devtools:.
 * Aus einer eingesperrten Kette ist keines davon frei; ein bekannter, nicht eingesperrter Rahmen darf alles.
 */
const UNBEKANNT_FREI = new Set(['app', 'devtools'])

function gleicherHost(a: string, b: string): boolean {
  try {
    const ha = new URL(a).host
    return ha !== '' && ha === new URL(b).host
  } catch {
    return false
  }
}

/** Adresse ohne Query und Fragment: so vergleicht der Rückhalt "eigenes Dokument" (R47). */
function ohneQueryUndFragment(url: string): string {
  return url.split(/[?#]/)[0]
}

/** So lange bleibt ein Rahmen nach dem Abbau noch eingesperrt (R46). */
export const CONFINED_GRACE_MS = 5000

/**
 * Zustand des Rückhalts je Ansicht (R46, R47), geschlüsselt nach der Rahmen-Knoten-Id (frameTreeNodeId): `confined`
 * hält die eingesperrten Rahmen (lebenslang), `recentlyConfined` die abgebauten mit dem Zeitpunkt des Abbaus.
 */
export type BackstopState = { confined: Set<number>; recentlyConfined: Map<number, number> }

export function createBackstopState(): BackstopState {
  return { confined: new Set(), recentlyConfined: new Map() }
}

function kuerzlich(recently: ReadonlyMap<number, number>, id: number, now: number): boolean {
  const t = recently.get(id)
  return t !== undefined && now - t <= CONFINED_GRACE_MS // Uhr zurückgesprungen: weiter eingesperrt
}

function abgelaufeneVergessen(state: BackstopState, now: number): void {
  for (const id of [...state.recentlyConfined.keys()]) {
    if (!kuerzlich(state.recentlyConfined, id, now)) state.recentlyConfined.delete(id)
  }
}

/** Ist der Rahmen oder ein Vorfahr (Knoten-Ids) eingesperrt oder vor höchstens CONFINED_GRACE_MS abgebaut? */
export function isFrameChainConfined(
  ids: readonly number[],
  confined: ReadonlySet<number>,
  recentlyConfined: ReadonlyMap<number, number>,
  now: number,
): boolean {
  return ids.some((id) => confined.has(id) || kuerzlich(recentlyConfined, id, now))
}

/** Sperrt die Ansicht gerade einen Rahmen, oder wurde einer vor höchstens CONFINED_GRACE_MS abgebaut? */
function konzeptInAnsicht(confined: ReadonlySet<number>, recentlyConfined: ReadonlyMap<number, number>, now: number): boolean {
  return confined.size > 0 || [...recentlyConfined.keys()].some((id) => kuerzlich(recentlyConfined, id, now))
}

/**
 * Wird ein Rahmen beim Übernehmen eines Dokuments eingesperrt? Ja, wenn die Adresse unter knowledge/ liegt
 * ({@link isKnowledgeFrameUrl}), der Hauptprozess sie als Wissens-HTML ausgeliefert hat, oder sein Elternrahmen
 * eingesperrt ist.
 */
export function isConfinedCommit(url: string, servedAsKnowledgeHtml: boolean, parentConfined: boolean): boolean {
  return parentConfined || servedAsKnowledgeHtml || isKnowledgeFrameUrl(url)
}

/**
 * Ein Unterrahmen hat ein Dokument übernommen (Dokumentwechsel oder In-Page). Eingesperrt -> in `confined`, auf
 * Lebenszeit des Rahmens (R47): keine Navigation entlässt ihn, auch keine, die er selbst ausgelöst hat.
 */
export function noteFrameCommit(
  state: BackstopState,
  c: { frameId: number; parentId: number | null; url: string; servedAsKnowledgeHtml?: boolean; now: number },
): void {
  abgelaufeneVergessen(state, c.now)
  const parentConfined = c.parentId !== null && state.confined.has(c.parentId)
  if (isConfinedCommit(c.url, c.servedAsKnowledgeHtml === true, parentConfined)) {
    state.confined.add(c.frameId)
    state.recentlyConfined.delete(c.frameId)
  }
}

/** Nur der Abbau entlässt (R47): Rahmen, die nicht mehr im Baum hängen, wandern für CONFINED_GRACE_MS nach recentlyConfined. */
export function noteFramesAttached(state: BackstopState, attachedIds: ReadonlySet<number>, now: number): void {
  for (const id of [...state.confined]) {
    if (!attachedIds.has(id)) {
      state.confined.delete(id)
      state.recentlyConfined.set(id, now)
    }
  }
  abgelaufeneVergessen(state, now)
}

/**
 * Entscheidung des Rückhalts (R44-R47), nur aus Werten. Der Verweiser (Referer) zählt nie.
 * 1. Laden eines Hauptrahmens, `data:`/`blob:`/`about:`: durchlassen.
 * 2. Eingesperrte Kette (der Rahmen oder ein Vorfahr zeigt eine Adresse unter knowledge/, `chainConfinedByUrl`, oder
 *    steht in `confined`/nicht abgelaufen in `recentlyConfined`): nur das eigene Dokument des Rahmens (`frameUrl`,
 *    ohne Query und Fragment verglichen, und nur wenn `frameUrl` unter knowledge/ liegt, R48) ist erlaubt, sonst
 *    nichts – auch keine andere app://-Adresse.
 * 3. Rahmen identifiziert, lesbar, im Baum und nicht eingesperrt: durchlassen.
 * 4. Sonst (nicht identifizierbar, nicht lesbar, nicht im Baum): `app:`, `devtools:` und der eigene
 *    Entwicklungsserver (`appUrl`, gleicher Host) bleiben frei; alles andere wird abgebrochen, solange die Ansicht
 *    einen Rahmen sperrt oder einer vor höchstens CONFINED_GRACE_MS abgebaut wurde.
 */
export function backstopDecision(r: {
  url: string
  resourceType: string
  frameId: number | null
  frameUrl: string | null
  ancestorIds: readonly number[]
  attached: boolean
  chainConfinedByUrl: boolean
  confined: ReadonlySet<number>
  recentlyConfined: ReadonlyMap<number, number>
  now: number
  appUrl?: string | null
}): boolean {
  if (r.resourceType === 'mainFrame') return false
  const s = schema(r.url)
  if (s !== null && DOKUMENT_SCHEMATA.has(s)) return false
  const kette = r.frameId === null ? [] : [r.frameId, ...r.ancestorIds]
  if (r.chainConfinedByUrl || isFrameChainConfined(kette, r.confined, r.recentlyConfined, r.now)) {
    // R48: "eigenes Dokument" nur für eine Rahmen-Adresse unter app://workspace/knowledge/, nie für eine Netz-Adresse
    if (r.frameUrl !== null && isKnowledgeFrameUrl(r.frameUrl) && ohneQueryUndFragment(r.url) === ohneQueryUndFragment(r.frameUrl)) return false
    return true
  }
  if (r.frameId !== null && r.attached) return false
  if (s !== null && UNBEKANNT_FREI.has(s)) return false
  if (r.appUrl && gleicherHost(r.url, r.appUrl)) return false
  return konzeptInAnsicht(r.confined, r.recentlyConfined, r.now)
}

// ---- Gastbereich: Konzepte in einem <webview> der Partition `konzept` (Hauptprozess, Vorschalt-Skript, Oberfläche) ----

/** Nicht dauerhafte Partition (ohne `persist:`) der Konzept-Gäste; nur sie bekommt Proxy und Sperren. */
export const KONZEPT_PARTITION = 'konzept'
/** Zusatzargument im App-Fenster (`additionalArguments`): der Hauptprozess hat den Gastbereich eingerichtet. */
export const KONZEPT_GAST_ARG = '--rowboat-konzept-gast'
/** Kanal, auf dem das Vorschalt-Skript des Gasts Nachrichten an das `<webview>`-Element reicht (`sendToHost`). */
export const KONZEPT_GAST_KANAL = 'konzept'
/** Längstes Feld (Zeichen) einer weiterzureichenden Gast-Nachricht. */
export const KONZEPT_GAST_FELD_MAX = 4000

/**
 * Steuerzeichen oder Leerraum in einer Adresse? Chromium entfernt Tab/Zeilenumbruch beim Einlesen (aus `.\t.` wird
 * `..`) und kodiert Leerzeichen; eine kanonische Adresse enthält keines davon. Alle Gast-Prüfungen lehnen solche
 * Adressen vor dem Zerlegen ab.
 */
function hatSteuerOderLeerzeichen(url: string): boolean {
  // eslint-disable-next-line no-control-regex
  return /[\u0000- \u007f]/.test(url)
}

/**
 * Darf ein `<webview>` mit dieser Adresse angehängt werden? Nur ein Dokument unter `app://workspace/knowledge/`
 * (Kanal-Quarantäne eingeschlossen). Schema und Host ohne Groß/Klein; der Pfad (ohne Query/Fragment) zählt roh UND
 * prozent-dekodiert, `\` trennt wie `/`, `.`-Segmente entfallen (wie {@link isKnowledgeFrameUrl}). Anders als dort
 * gilt hier "im Zweifel nein": Steuerzeichen/Leerraum, `..`, ungültige Kodierung, fehlender Dateiname, andere
 * Schemata/Hosts -> false. Die Oberfläche muss Pfadsegmente also kodieren (`encodeURIComponent`).
 */
export function guestAttachAllowed(src: string): boolean {
  if (hatSteuerOderLeerzeichen(src)) return false
  const m = /^app:\/\/workspace(?=[/?#]|$)/i.exec(src)
  if (m === null) return false
  const roh = src.slice(m[0].length).split(/[?#]/)[0]
  let dekodiert: string
  try {
    dekodiert = decodeURIComponent(roh)
  } catch {
    return false
  }
  // Dekodiert sind Leerzeichen normal (Dateinamen), Steuerzeichen nie.
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f]/.test(dekodiert)) return false
  // Nur die kanonische Form: `app` ist ein Standard-Schema, Chromium schriebe `.`-Segmente, `%2e` und `\` um, und der
  // Stolperdraht (Vergleich mit der angehängten Adresse) beendete den Gast beim ersten Laden. Also kein `\`, kein
  // kodiertes `.`, `/` oder `\`, kein `.`-Segment.
  if (roh.includes('\\') || /%(2e|2f|5c)/i.test(roh)) return false
  if (roh.split('/').some((s) => s === '.')) return false
  return [segmente(roh), segmente(dekodiert)].every(
    (segs) => !segs.includes('..') && segs.length >= 2 && segName(segs[0]) === 'knowledge',
  )
}

/** Darf `app://` in der Gast-Sitzung diese Adresse ausliefern? Nur Wissens-Dokumente, sonst 403. */
export function guestProtocolAllowed(url: string): boolean {
  return guestAttachAllowed(url)
}

/** Die Rechte, die der Hauptprozess jedem Konzept-Gast erzwingt (unabhängig von den Attributen des Tags). */
export function guestWebPreferences(): {
  nodeIntegration: false
  nodeIntegrationInSubFrames: false
  contextIsolation: true
  sandbox: true
  plugins: false
  webviewTag: false
  partition: 'konzept'
} {
  return {
    nodeIntegration: false,
    nodeIntegrationInSubFrames: false,
    contextIsolation: true,
    sandbox: true,
    plugins: false,
    webviewTag: false,
    partition: KONZEPT_PARTITION,
  }
}

/** Schema und Host klein, Pfad dekodiert, ohne Query und Fragment; null, wenn nicht lesbar. */
function dokumentSchluessel(url: string): string | null {
  const m = /^([a-z][a-z0-9+.-]*):\/\/([^/?#]*)([^?#]*)/i.exec(url)
  if (m === null) return null
  try {
    return `${m[1].toLowerCase()}://${m[2].toLowerCase()}${decodeURIComponent(m[3])}`
  } catch {
    return null
  }
}

/**
 * Anfrage, die im Dokument bleibt (`data:`, `blob:`, `about:`)? Nie Netz, nie Datei. Das Schema ist der rohe Text bis
 * zum ersten `:` (klein), ohne Zerlegen: `data:image/svg+xml,<svg …>` mit Leerzeichen bleibt erlaubt; Leerraum oder
 * Steuerzeichen vor dem `:` ergeben ein anderes Schema und damit nein.
 */
export function guestInDocumentRequest(requestUrl: string): boolean {
  const i = requestUrl.indexOf(':')
  return i > 0 && DOKUMENT_SCHEMATA.has(requestUrl.slice(0, i).toLowerCase())
}

/** Ist `zielUrl` dasselbe Wissens-Dokument wie `dokumentUrl` (ohne Query/Fragment, Schema/Host ohne Groß/Klein)? */
function gleichesWissensDokument(zielUrl: string, dokumentUrl: string): boolean {
  if (!guestAttachAllowed(zielUrl) || !guestAttachAllowed(dokumentUrl)) return false
  const a = dokumentSchluessel(zielUrl)
  return a !== null && a === dokumentSchluessel(dokumentUrl)
}

/**
 * Anfrage in der Gast-Sitzung erlauben? Zuerst: `data:`, `blob:`, `about:` bleiben im Dokument -> ja (auch mit
 * Leerzeichen, {@link guestInDocumentRequest}). Erst danach die strenge Vorprüfung (Steuerzeichen/Leerraum) und nur das eigene
 * Hauptdokument des Gasts (ohne Query/Fragment, Schema/Host ohne Groß/Klein verglichen), und nur wenn es eine
 * Wissens-Adresse ist ({@link guestAttachAllowed}). `guestDocumentUrl === null` (Dokument unbekannt): nur ein
 * Wissens-Dokument. Der Hauptprozess kennt die angehängte Adresse jedes Gasts und gibt für unbekannte Anfragen nur
 * {@link guestInDocumentRequest} frei.
 */
export function guestRequestAllowed(requestUrl: string, guestDocumentUrl: string | null): boolean {
  if (guestInDocumentRequest(requestUrl)) return true
  if (guestDocumentUrl === null) return guestAttachAllowed(requestUrl)
  return gleichesWissensDokument(requestUrl, guestDocumentUrl)
}

/**
 * Stolperdraht für Navigationen, die der Browser startet (`loadURL`, `src`-Wechsel, `reload`, Verlauf): sie lösen kein
 * `will-navigate` aus. Erlaubt ist nur die angehängte Wissens-Adresse selbst (ohne Query/Fragment verglichen, beide
 * müssen {@link guestAttachAllowed} bestehen). Alles andere, auch `data:`/`about:`/`file:`/`chrome:`/`devtools:`,
 * beendet den Gast.
 */
export function guestNavigationAllowed(targetUrl: string, attachedSrc: string | null): boolean {
  return attachedSrc !== null && gleichesWissensDokument(targetUrl, attachedSrc)
}

/**
 * Entscheidung des Anfrage-Filters der Gast-Sitzung, eine Tabelle: beendeter Gast -> nie; Proxy nicht bestätigt -> nie;
 * keine webContents oder unbekannte angehängte Adresse -> nur, was im Dokument bleibt ({@link guestInDocumentRequest});
 * sonst {@link guestRequestAllowed} gegen die angehängte Adresse. true = durchlassen.
 */
export function guestRequestDecision(r: {
  dead: boolean
  proxyConfirmed: boolean
  hasWebContents: boolean
  url: string
  documentUrl: string | null
}): boolean {
  if (r.dead || !r.proxyConfirmed) return false
  if (!r.hasWebContents || r.documentUrl === null) return guestInDocumentRequest(r.url)
  return guestRequestAllowed(r.url, r.documentUrl)
}

/**
 * Stolperdraht (`did-start-navigation` des Gasts): ein beendeter Gast -> jede Navigation `'beenden'`. Sonst nur
 * Dokumentwechsel des Hauptrahmens prüfen: erlaubt ist nur die angehängte Wissens-Adresse
 * ({@link guestNavigationAllowed}); Unterrahmen sperrt `will-frame-navigate`, Gleiche-Dokument-Navigationen laden nichts.
 * Fremdes Ziel, von der Seite ausgelöst (`pageInitiated`): `'ueberlassen'`, denn `did-start-navigation` kommt VOR
 * `will-frame-navigate`/`will-navigate`, und deren `preventDefault` hält die Navigation an, ohne den Gast zu beenden.
 * Ausnahme `about:`: dort feuert kein `will-*`, es würde festgeschrieben, also `'beenden'`. Alles andere (vom Browser
 * gestartet) -> `'beenden'`.
 */
export function guestTripwireDecision(n: {
  dead: boolean
  isMainFrame: boolean
  isSameDocument: boolean
  url: string
  attachedSrc: string | null
  pageInitiated: boolean
}): 'erlauben' | 'ueberlassen' | 'beenden' {
  if (n.dead) return 'beenden'
  if (!n.isMainFrame || n.isSameDocument) return 'erlauben'
  if (guestNavigationAllowed(n.url, n.attachedSrc)) return 'erlauben'
  if (n.pageInitiated && !/^[\u0000- \u007f]*about:/i.test(n.url)) return 'ueberlassen'
  return 'beenden'
}

export type GuestMessage = { type: 'rowboat:link-oeffnen'; url: string } | { type: 'rowboat:notiz-oeffnen'; pfad: string }

function feld(v: unknown): v is string {
  return typeof v === 'string' && v.length <= KONZEPT_GAST_FELD_MAX
}

/**
 * Form- und Längenprüfung einer Nachricht der Konzept-Seite (`parent.postMessage({ type, url | pfad }, '*')`), für das
 * Vorschalt-Skript des Gasts und die Oberfläche. Liefert nur `{ type, url }` bzw. `{ type, pfad }` als Zeichenketten
 * (höchstens KONZEPT_GAST_FELD_MAX Zeichen), sonst null.
 */
export function forwardableGuestMessage(data: unknown): GuestMessage | null {
  if (!isRecord(data)) return null
  // Jedes Feld genau einmal lesen: ein Getter darf zwischen Prüfen und Kopieren nichts anderes liefern.
  const type = data.type
  if (type === 'rowboat:link-oeffnen') {
    const url = data.url
    return feld(url) ? { type, url } : null
  }
  if (type === 'rowboat:notiz-oeffnen') {
    const pfad = data.pfad
    return feld(pfad) ? { type, pfad } : null
  }
  return null
}
