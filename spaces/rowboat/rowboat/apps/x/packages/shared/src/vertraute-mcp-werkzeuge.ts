// Vertraute MCP-Werkzeuge: Aufrufe, die im Rowboat-Chat ohne manuelle Rückfrage laufen dürfen.
// Reine Funktionen ohne DOM (Node und Browser).

type VertrautesWerkzeug = { readonly serverName: string; readonly toolName: string; readonly url: string }

/**
 * Was die Rechteprüfung der Ausführung mitgibt, wenn sie einen Aufruf OHNE Rückfrage zulässt: Server, Werkzeug
 * und die geprüfte Adresse. Die Ausführung verbindet nur, wenn der Eintrag, den sie gleich benutzt, noch passt
 * ({@link vertrauterAufrufPasst}); so kann ein zwischen Prüfung und Aufruf geändertes `mcp.json` den Aufruf
 * nicht umlenken.
 */
export type VertrauterMcpAufruf = { serverName: string; toolName: string; url: string }

/**
 * Die Liste steht absichtlich im Code und wird aus keiner Datei gelesen, die der Agent schreiben kann
 * (`mcp.json`, Kanal-Einstellungen o. ä.): sonst könnte er sich selbst Rückfragen ersparen.
 *
 * Einziger Eintrag: die Übergabe eines Videokonzepts an die Video-Werkbank. `video_auftrag_aus_konzept`
 * BEREITET die Übergabe nur VOR; den verbindlichen Auftrag legt erst das Ja des Betreibers in Sales an
 * (eigene Oberfläche, eigene Anmeldung). Eine zweite Rückfrage in Rowboat schützt also nichts zusätzlich.
 * Das gilt auch für Läufe ohne Menschen (Hintergrund-Agenten): sie dürfen das Werkzeug jetzt rufen und
 * erzeugen damit höchstens eine Übergabe, die ohne das Ja in Sales folgenlos bleibt. Kanal-Läufe
 * (Telegram/WhatsApp) sperrt weiterhin der Kanal-Wächter: das Werkzeug steht nicht auf `readOnlyMcpTools`.
 *
 * Wer hier ein Werkzeug ergänzt, braucht dasselbe Argument: es darf für sich allein nichts Verbindliches
 * auslösen, das Ja muss anderswo von einem angemeldeten Menschen kommen.
 */
const VERTRAUTE_MCP_WERKZEUGE: readonly VertrautesWerkzeug[] = Object.freeze([
  Object.freeze({
    serverName: 'rowboat-auftraege',
    toolName: 'video_auftrag_aus_konzept',
    url: 'http://127.0.0.1:8156/mcp',
  }),
])

/**
 * Nur der Namensteil der Prüfung: Servername und Werkzeug stehen (exakt, Groß-/Kleinschreibung zählt) in
 * der Liste. Allein KEIN Vertrauensbeweis, siehe {@link vertrautesMcpWerkzeug}; dient dem Aufrufer als
 * Vorfilter, damit er die Server-Konfiguration nur für diese Aufrufe nachschlägt.
 */
export function istVertrauterMcpName(serverName: string, toolName: string): boolean {
  return VERTRAUTE_MCP_WERKZEUGE.some((w) => w.serverName === serverName && w.toolName === toolName)
}

/**
 * Darf dieser MCP-Aufruf ohne Rückfrage laufen? Nur wenn Servername, Werkzeug UND die URL des Server-Eintrags,
 * der tatsächlich ausgeführt wird, zu einem Eintrag der Liste passen.
 *
 * Warum die URL: der Servername ist nur ein frei wählbarer Alias in `mcp.json`. Ein Eintrag
 * `rowboat-auftraege` mit fremder Adresse (anderer Rechner, anderer Port, stdio-Befehl) bekommt kein
 * Vertrauen. Verlangt wird, NACH der Normalisierung durch den WHATWG-URL-Parser, genau
 * `http://127.0.0.1:8156/mcp`: Schema `http:`, Host `127.0.0.1` (nicht `localhost`, kein Name, nicht `[::1]`
 * oder `[::ffff:127.0.0.1]`, nicht `0.0.0.0`), Port 8156, Pfad `/mcp` (ohne Schrägstrich am Ende, ohne `;…`,
 * keine %-Kodierung wie `/%6Dcp`), keine Zugangsdaten, keine Query und kein Fragment (auch kein leeres `?`
 * oder `#`).
 *
 * Kein roher Textvergleich: Schreibweisen, die der Parser auf genau diese Adresse normalisiert, gelten
 * absichtlich als vertraut, weil der Transport dieselbe Zeichenkette mit demselben Parser öffnet und also
 * dieselbe Gegenstelle erreicht. Das sind u. a. Groß-/Kleinschreibung des Schemas (`HTTP://`), führende
 * Nullen im Port (`:08156`), kurze IPv4-Formen (`127.1`, `2130706433`, `0x7f.0.0.1`), ein Punkt am Ende des
 * Hosts (`127.0.0.1.`), Punkt-Segmente im Pfad (`/./mcp`, `/x/../mcp`), leere Zugangsdaten (`http://@…`,
 * `http://:@…`) und Leerraum am Anfang/Ende. Wer auf einen Textvergleich umstellt, ändert also Verhalten.
 *
 * Die Prüfung gilt nur für den ersten Sprung zu dieser Adresse (keine Aussage über Weiterleitungen) und nur
 * für die neue Laufzeit (`RealPermissionChecker`); Alt-Engine und IPC-Weg (`mcp:executeTool`) haben nie nach einem MCP-Aufruf gefragt; die Eintragsprüfung im
 * Ausführer greift dort nicht, weil diese Aufrufe keine Markierung (`vertraut`) tragen.
 *
 * @param n.serverUrl URL des Eintrags, den die Ausführung für `serverName` auflöst; `null` für stdio-Server
 *   oder wenn es keinen Eintrag gibt (dann nie vertraut).
 */
export function vertrautesMcpWerkzeug(n: { serverName: string; toolName: string; serverUrl: string | null }): boolean {
  if (n.serverUrl === null) return false
  let url: URL
  try {
    url = new URL(n.serverUrl)
  } catch {
    return false
  }
  return VERTRAUTE_MCP_WERKZEUGE.some((w) => {
    if (w.serverName !== n.serverName || w.toolName !== n.toolName) return false
    const soll = new URL(w.url)
    return (
      url.protocol === soll.protocol &&
      url.hostname === soll.hostname &&
      url.port === soll.port &&
      url.pathname === soll.pathname &&
      url.username === '' &&
      url.password === '' &&
      // `href` deckt zusätzlich ein leeres `?` / `#` ab, das `search` / `hash` als '' melden.
      url.href === soll.href
    )
  })
}

/**
 * Prüfung der Ausführung direkt vor dem Verbinden: passt der Eintrag, den sie JETZT benutzt (`serverUrl`), noch
 * zu dem Aufruf, den die Rechteprüfung ohne Rückfrage zugelassen hat? Gleicher Server, gleiches Werkzeug, der
 * jetzige Eintrag ist vertraut und die geprüfte Adresse war es auch (beide normalisieren also auf dieselbe
 * Adresse). Sonst: nicht verbinden.
 */
export function vertrauterAufrufPasst(
  aufruf: VertrauterMcpAufruf,
  jetzt: { serverName: string; toolName: string; serverUrl: string | null },
): boolean {
  return (
    aufruf.serverName === jetzt.serverName &&
    aufruf.toolName === jetzt.toolName &&
    vertrautesMcpWerkzeug(jetzt) &&
    vertrautesMcpWerkzeug({ serverName: aufruf.serverName, toolName: aufruf.toolName, serverUrl: aufruf.url })
  )
}
