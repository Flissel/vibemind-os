// Die Fläche (Konzept-HTML neben dem Chat): reine Entscheidungen ohne DOM, getestet in konzept-flaeche.test.ts.
import { konzept } from '@x/shared'

/** Änderungen am selben Pfad werden so lange gebündelt, bevor der Rahmen neu eingehängt wird. */
export const reloadDebounceMs = 300

const MAX_LINK_LENGTH = 2000
const MAX_NOTE_PATH_LENGTH = 1000

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null
}

/** Pfad des Wissens-HTMLs, das ein abgeschlossener file-writeText/file-editText-Aufruf geschrieben hat; sonst null. */
export function konzeptPathFromTool(tool: { name: string; status: string; result?: unknown }): string | null {
  if (tool.name !== 'file-writeText' && tool.name !== 'file-editText') return null
  if (tool.status !== 'completed') return null
  if (!isRecord(tool.result)) return null
  const rel = tool.result.workspaceRelPath
  if (typeof rel !== 'string') return null
  if (!konzept.isKnowledgeHtmlPath(rel) || konzept.isKanalPath(rel)) return null
  return rel
}

/** Von selbst öffnen nur beim ersten Schreiben eines Pfads im Zug und nur, wenn er nicht schon angezeigt wird. */
export function shouldAutoOpen(args: { path: string; openedThisTurn: Set<string>; currentPath: string | null }): boolean {
  if (args.openedThisTurn.has(args.path)) return false
  return args.currentPath !== args.path
}

/** Was sich die Fläche je Chat merkt: der Zug der zuletzt gesehenen Schreibaufrufe und die darin schon geöffneten Pfade. */
export type ZugGedaechtnis = { turn: string | null; opened: Set<string> }

type GespraechsEintrag = { id: string; role?: unknown; kind?: unknown; name?: unknown; status?: unknown; result?: unknown }

/**
 * Geht die Unterhaltung in Reihenfolge durch. Ein Zug beginnt mit jeder Nutzernachricht (auch einer nachgereichten)
 * und nach dem Verbrauchseintrag (`turn-usage`), der einen Zug abschließt -- so zählt auch ein Zug ohne Nutzertext
 * für sich. Für jeden noch nicht verarbeiteten, abgeschlossenen Schreibaufruf eines Wissens-HTMLs (nicht Kanal):
 * Pfad und ob die Fläche aufgehen soll -- nur beim ersten Schreiben dieses Pfads im Zug und nur, wenn er nicht schon
 * angezeigt wird (spec 5.4: geschlossen bleibt sie zu, bis der Agent das nächste Mal schreibt). `processed` und
 * `memory` werden fortgeschrieben.
 */
export function konzeptSchreibvorgaenge(
  items: readonly GespraechsEintrag[],
  processed: Set<string>,
  memory: ZugGedaechtnis,
  currentPath: string | null,
): Array<{ path: string; open: boolean }> {
  const out: Array<{ path: string; open: boolean }> = []
  let turn = ''
  for (const item of items) {
    if (item.role === 'user') {
      turn = item.id
      continue
    }
    if (item.kind === 'turn-usage') {
      turn = `${item.id}:danach`
      continue
    }
    if (typeof item.name !== 'string' || typeof item.status !== 'string') continue
    if (processed.has(item.id)) continue
    if (item.status !== 'completed') continue
    processed.add(item.id)
    const path = konzeptPathFromTool({ name: item.name, status: item.status, result: item.result })
    if (!path) continue
    if (memory.turn !== turn) {
      memory.turn = turn
      memory.opened = new Set()
    }
    const open = shouldAutoOpen({ path, openedThisTurn: memory.opened, currentPath })
    memory.opened.add(path)
    out.push({ path, open })
  }
  return out
}

/** Notiz-Anfrage der Seite: `{ type: 'rowboat:notiz-oeffnen', pfad }` mit einer Datei unter `knowledge/` (nicht Kanal), sonst null. */
export function noteOpenRequest(msg: unknown): string | null {
  if (!isRecord(msg) || msg.type !== 'rowboat:notiz-oeffnen') return null
  const raw = msg.pfad
  if (typeof raw !== 'string' || raw.length === 0 || raw.length > MAX_NOTE_PATH_LENGTH) return null
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f]/.test(raw)) return null
  if (raw.startsWith('/') || raw.startsWith('\\')) return null
  const segs = raw.replace(/\\/g, '/').split('/').filter((s) => s !== '' && s !== '.')
  if (segs.includes('..')) return null
  if (segs.length < 2 || segs[0] !== 'knowledge') return null
  if (segs.some((seg) => seg.startsWith('.'))) return null
  if (!/\.[A-Za-z0-9]{1,8}$/.test(segs[segs.length - 1])) return null
  const norm = segs.join('/')
  if (konzept.isKanalPath(norm)) return null
  return norm
}

/** Link-Anfrage der Seite: `{ type: 'rowboat:link-oeffnen', url }` mit http(s)-Adresse ohne Zugangsdaten, sonst null. */
export function linkOpenRequest(msg: unknown): string | null {
  if (!isRecord(msg) || msg.type !== 'rowboat:link-oeffnen') return null
  const raw = msg.url
  if (typeof raw !== 'string' || raw.length === 0 || raw.length > MAX_LINK_LENGTH) return null
  let u: URL
  try {
    u = new URL(raw)
  } catch {
    return null
  }
  if (u.protocol !== 'http:' && u.protocol !== 'https:') return null
  if (u.username !== '' || u.password !== '') return null
  if (u.href.length > MAX_LINK_LENGTH) return null
  return u.href
}

/** Hinweise für den Streifen unter der Kopfzeile. Fremde Seiten ohne Datenblock: keine. `sizeBytes` = echte Dateigröße. */
export function konzeptHinweise(html: string, sizeBytes?: number): string[] {
  const { daten, fehler } = konzept.readKonzept(html)
  const hinweise = [...fehler]
  const hatBlock = daten !== null || fehler.length > 0
  if (daten?.art === 'videokonzept') hinweise.push(...konzept.checkVideoKonzept(daten.video))
  if (hatBlock && (sizeBytes ?? html.length) > konzept.KONZEPT_MAX_BYTES) {
    hinweise.push('Die Datei ist größer als 1 MB. Graph und Suche lesen nur den Anfang.')
  }
  return hinweise
}

/** Betrifft ein `workspace:didChange`-Ereignis diesen Pfad (Neuladen nötig)? Ohne Pfadliste (bulkChanged) im Zweifel ja. */
export function workspaceEventTouches(event: unknown, path: string): boolean {
  if (!isRecord(event)) return false
  switch (event.type) {
    case 'changed':
    case 'created':
      return event.path === path
    case 'moved':
      return event.to === path
    case 'bulkChanged':
      return !Array.isArray(event.paths) || event.paths.includes(path)
    default:
      return false
  }
}

// ---- Gastbereich: Konzepte im <webview> der Partition `konzept` ----

/** Gibt es den Gastbereich? Nur wenn der Hauptprozess ihn bestätigt hat (Kennzeichen genau `true`) und die Laufzeit lokal ist. */
export function guestAvailable(flag: unknown, localRuntime: boolean): boolean {
  return flag === true && localRuntime
}

/** Mehr als Adresse und Partition bekommt das Tag nicht: Rechte, Vorschalt-Skript und Sitzung setzt der Hauptprozess. */
export function guestElementProps(src: string): { src: string; partition: 'konzept' } {
  return { src, partition: konzept.KONZEPT_PARTITION }
}

/**
 * Kanonische Gast-Adresse: `app://workspace/<Segmente einzeln mit encodeURIComponent>?v=<n>`. `rel` ist der
 * Arbeitsbereichspfad (`\` wird zu `/`), beginnt mit `knowledge`, ohne führendes `/` und ohne leere, `.`- oder
 * `..`-Segmente. Null, wenn daraus keine Adresse wird, die der Hauptprozess anhängt (Prüfung mit derselben
 * Funktion wie dort).
 */
export function guestSrc(rel: string, n: number): string | null {
  if (!Number.isInteger(n) || n < 0) return null
  const segs = rel.replace(/\\/g, '/').split('/')
  if (segs.length < 2 || segs[0] !== 'knowledge') return null
  if (segs.some((s) => s === '' || s === '.' || s === '..')) return null
  let src: string
  try {
    src = `app://workspace/${segs.map(encodeURIComponent).join('/')}?v=${n}`
  } catch {
    return null // z. B. ein einzelnes Ersatzzeichen: encodeURIComponent wirft
  }
  return konzept.guestAttachAllowed(src) ? src : null
}

/** So lange (ms) nach dem Einhängen darf der Gast ohne `dom-ready`/`did-stop-loading` bleiben; danach gilt das Anhängen als verweigert. */
export const guestMountTimeoutMs = 5000

/** Hinweis statt Seite: fehlender Gastbereich und nie geladener Gast zeigen denselben Text, ein beendeter Gast einen eigenen. */
export function guestNoticeText(kind: 'missing' | 'timeout' | 'gone'): string {
  return kind === 'gone' ? 'Dieses Konzept wurde angehalten.' : 'Konzepte lassen sich hier nicht anzeigen.'
}

/**
 * Eine offene Rückfrage der Seite (Bestätigung in der Kopfzeile): Web-Link oder Notiz. `id` ist je Anfrage
 * eindeutig, `at` (Date.now) der Zeitpunkt der Anfrage für die 10-s-Regel.
 */
export type PendingRequest = { id: number; kind: 'link' | 'note'; value: string; at: number }
export const pendingMaxAgeMs = 10_000
/** So lange bleibt "Öffnen" nach dem ersten gemalten Bild der Bestätigung gesperrt (gegen Zeitdruck-Klicks). */
export const confirmDelayMs = 700

/**
 * Zustandsübergänge der Rückfrage (Links und Notizen zusammen, eine offene Anfrage). Eine neue Anfrage ersetzt
 * die offene nur, wenn keine offen ist oder die offene älter als 10 s ist. Abbrechen, Öffnen und Zurücksetzen
 * (Neuladen, Ansicht verborgen) leeren sie.
 */
export function nextPendingRequest(
  state: PendingRequest | null,
  event:
    | { type: 'request'; id: number; kind: 'link' | 'note'; value: string; now: number }
    | { type: 'cancel' | 'open' | 'reset' },
): PendingRequest | null {
  if (event.type !== 'request') return null
  if (state === null || event.now - state.at > pendingMaxAgeMs) {
    return { id: event.id, kind: event.kind, value: event.value, at: event.now }
  }
  return state
}

/** So lange (Date.now) nach "Abbrechen" ignoriert die Brücke alle Anfragen der Seite (gegen Dauerschleifen). */
export const cancelQuietMs = 10_000

/**
 * Nimmt die Brücke eine neue Anfrage der Seite an? Nein, solange eine Existenzprüfung läuft; nein bis 10 s nach
 * "Abbrechen" (`cancelledAt`, Date.now; null = nicht abgebrochen); nein, solange eine Rückfrage offen ist, außer sie
 * ist älter als 10 s (dann darf sie ersetzt werden). Es wird nichts eingereiht.
 */
export function canAcceptRequest(
  state: PendingRequest | null,
  checkInFlight: boolean,
  now: number,
  cancelledAt: number | null,
): boolean {
  if (checkInFlight) return false
  // Springt die Uhr zurück (now < cancelledAt), verlängert das die Ruhezeit nicht.
  if (cancelledAt !== null && now >= cancelledAt && now - cancelledAt <= cancelQuietMs) return false
  return state === null || now - state.at > pendingMaxAgeMs
}

/** Moment (performance.now), ab dem die Bestätigung einer bestimmten Anfrage tatsächlich gemalt war. */
export type Armed = { id: number; armedAt: number }
/** Moment (performance.now), in dem der Nutzer "Öffnen" für eine Anfrage angesteuert hat (Zeiger hinein / Tastaturfokus). */
export type Engaged = { id: number; at: number }

/**
 * Zeitpunkt eines Eingabe-Ereignisses: sein `timeStamp` (wann es geschah), nie später als `now` (performance.now bei
 * der Behandlung). So rückt ein Hänger des Hauptfadens das Ereignis nicht hinter die Sperre; das `min` schützt vor
 * einer Plattform, auf der `timeStamp` einen anderen Zeitursprung hat.
 */
export function eventTime(timeStamp: number, now: number): number {
  return Math.min(timeStamp, now)
}

/**
 * "Öffnen" gilt nur für die Anfrage, die gemalt wurde, erst `confirmDelayMs` nach dem ersten Bild, und nur, wenn der
 * Nutzer den Knopf NACH Ablauf dieser Sperre angesteuert hat. Ein Zeiger, der schon dort ruhte, muss hinaus und zurück.
 * `now` ist der Zeitpunkt des Klicks (beim Klick: `eventTime` des Klick-Ereignisses); er muss nach dem Ansteuern liegen.
 */
export function canConfirm(state: PendingRequest | null, armed: Armed | null, engaged: Engaged | null, now: number): boolean {
  if (state === null || armed === null || engaged === null) return false
  if (armed.id !== state.id || engaged.id !== state.id) return false
  if (engaged.at < armed.armedAt + confirmDelayMs) return false
  if (now <= engaged.at) return false
  return now - armed.armedAt >= confirmDelayMs
}
