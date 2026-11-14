import { describe, expect, it } from 'vitest'
import {
  KONZEPT_BLOCK_MAX,
  KONZEPT_LESE_MAX,
  WORKSPACE_HTML_CSP,
  checkVideoKonzept,
  frameNavigationDecision,
  htmlTitle,
  htmlVisibleText,
  htmlSearchText,
  konzeptBlockTexte,
  isKanalPath,
  isKnowledgeHtmlPath,
  readKonzept,
  htmlResponseHeaders,
  isHtmlPath,
  isKnowledgeFrameUrl,
  isKnowledgeScopePath,
  needsNosniffOnly,
  shouldApplyHtmlHeaders,
  backstopDecision,
  createBackstopState,
  isConfinedCommit,
  isFrameChainConfined,
  noteFrameCommit,
  noteFramesAttached,
  CONFINED_GRACE_MS,
  workspaceResponseDecision,
  KONZEPT_PARTITION,
  KONZEPT_GAST_ARG,
  KONZEPT_GAST_KANAL,
  guestAttachAllowed,
  guestWebPreferences,
  guestRequestAllowed,
  guestProtocolAllowed,
  guestNavigationAllowed,
  guestInDocumentRequest,
  guestRequestDecision,
  guestTripwireDecision,
  forwardableGuestMessage,
} from './konzept.js'

const SEITE = (block: string) => `<!doctype html><html><head><title>Mein &amp; Konzept</title>
<script type="application/json" id="rowboat-konzept">${block}</script><style>.x{color:red}</style></head>
<body><h1>Hallo</h1><script>var geheim = 1</script><!-- weg --><p>Welt</p></body></html>`
const VIDEO = { thema: 'KI im Alltag', zielgruppe: 'Inhaber', minuten: 4, hook: 'H', kernaussage: 'K', abschluss: 'A', auftrag: null,
  stufen: [{ name: 'Einstieg', szenen: Array.from({ length: 8 }, (_, i) => ({ kurz: `Szene ${i}`, bildart: 'slide' })) }] }

describe('konzept', () => {
  it('liest Titel, Text und Block', () => {
    const html = SEITE(JSON.stringify({ art: 'konzept', bezuege: ['[[Marke/Marke]]'] }))
    expect(htmlTitle(html)).toBe('Mein & Konzept')
    expect(htmlVisibleText(html)).toBe('Mein & Konzept Hallo Welt')
    expect(readKonzept(html)).toEqual({ daten: { art: 'konzept', bezuege: ['[[Marke/Marke]]'] }, fehler: [] })
  })
  it('Seite ohne Block: keine Daten, kein Fehler', () => {
    expect(readKonzept('<html><title>t</title></html>')).toEqual({ daten: null, fehler: [] })
  })
  it.each(['{kaputt', '{"art":"x","bezuege":[]}', '{"art":"konzept","bezuege":"a"}', '[]'])(
    'kaputter Block meldet genau einen Fehler statt zu werfen: %s', (block) => {
      const r = readKonzept(SEITE(block))
      expect(r.daten).toBeNull()
      expect(r.fehler).toHaveLength(1)
    })
  it('bei zwei Blöcken gilt der erste', () => {
    const html = SEITE(JSON.stringify({ art: 'konzept', bezuege: [] })) + SEITE(JSON.stringify({ art: 'videokonzept', bezuege: [] }))
    expect(readKonzept(html).daten?.art).toBe('konzept')
  })
  it('findet den Block unabhängig von Attributreihenfolge und Anführungszeichen', () => {
    const json = JSON.stringify({ art: 'konzept', bezuege: [] })
    for (const tag of [
      `<script id="rowboat-konzept" type="application/json">`,
      `<script id='rowboat-konzept' type='application/json'>`,
      `<SCRIPT type=application/json id=\"rowboat-konzept\">`,
    ]) {
      expect(readKonzept(`<html>${tag}${json}</script></html>`).daten?.art).toBe('konzept')
    }
  })
  it('liest </script> im JSON, das als <\/script> geschrieben ist', () => {
    const json = JSON.stringify({ art: 'konzept', bezuege: ['[[a</script>b]]'] }).replace(/<\/script>/g, String.raw`<\/script>`)
    expect(json).toContain(String.raw`<\/script>`)
    expect(readKonzept(SEITE(json)).daten?.bezuege).toEqual(['[[a</script>b]]'])
  })
  it('prüft die Grenzen des Videokonzepts', () => {
    expect(checkVideoKonzept(VIDEO)).toEqual([])
    expect(checkVideoKonzept({ ...VIDEO, minuten: 6 })).toHaveLength(1)
    expect(checkVideoKonzept({ ...VIDEO, stufen: [{ name: 'E', szenen: VIDEO.stufen[0].szenen.slice(0, 6) }] })).toHaveLength(1) // 6 Szenen
    expect(checkVideoKonzept({ ...VIDEO, stufen: [{ name: 'E', szenen: [...VIDEO.stufen[0].szenen, ...VIDEO.stufen[0].szenen] }] })).toHaveLength(1) // 16
    expect(checkVideoKonzept({ ...VIDEO, stufen: [{ name: 'E', szenen: VIDEO.stufen[0].szenen.map((s) => ({ ...s, bildart: 'film' })) }] }).length).toBeGreaterThan(0)
    expect(checkVideoKonzept(null)).toHaveLength(1)
  })
  it('readKonzept gibt video bei videokonzept unverändert zurück', () => {
    const r = readKonzept(SEITE(JSON.stringify({ art: 'videokonzept', bezuege: [], video: VIDEO })))
    expect(r.fehler).toEqual([])
    expect(r.daten?.video).toEqual(VIDEO)
  })
  it('kennt Pfade', () => {
    expect(isKnowledgeHtmlPath('knowledge/Konzepte/Ä b.HTM')).toBe(true)
    expect(isKnowledgeHtmlPath('config/x.html')).toBe(false)
    expect(isKanalPath('knowledge/kanal/x.html')).toBe(true)
    expect(isKanalPath('knowledge/Kanalarbeit/x.html')).toBe(false)
  })
  it('die Regel lautet wörtlich wie in R40', () => {
    expect(WORKSPACE_HTML_CSP).toBe(
      "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; media-src data: blob:; form-action 'none'; base-uri 'none'; sandbox allow-scripts",
    )
    expect(htmlResponseHeaders()).toEqual({
      'Content-Type': 'text/html; charset=utf-8',
      'Content-Security-Policy': WORKSPACE_HTML_CSP,
      'X-Content-Type-Options': 'nosniff',
      'X-DNS-Prefetch-Control': 'off',
    })
  })
  it('Navigationsentscheid: Rahmen unter knowledge/ gesperrt, alles andere wie vorher', () => {
    expect(frameNavigationDecision('app://workspace/knowledge/a.html')).toBe('block')
    // R43: ohne Blick auf die Endung
    expect(frameNavigationDecision('app://workspace/knowledge/seite')).toBe('block')
    expect(frameNavigationDecision('app://workspace/knowledge/a.png?f=a.html')).toBe('block')
    expect(frameNavigationDecision('https://www.youtube.com/embed/x')).toBe('allow')
    // Hintergrund-Bericht außerhalb von knowledge/: unberührt (R38)
    expect(frameNavigationDecision('app://workspace/bg-tasks/x/index.html')).toBe('allow')
    expect(frameNavigationDecision('app://workspace/knowledge.html')).toBe('allow')
    expect(frameNavigationDecision('app://space-document/o/s/a/x.html')).toBe('allow')
    // vom Hauptprozess als Wissens-HTML ausgeliefert (Symlink-Ziel, Content-Type)
    expect(frameNavigationDecision('app://workspace/bg-tasks/link.html', true)).toBe('block')
  })
  it('Pfade: Backslash, führender Slash, //, ., .. und Groß/Klein', () => {
    expect(isKnowledgeHtmlPath('\\knowledge\\a.html')).toBe(true)
    expect(isKnowledgeHtmlPath('/Knowledge/a.html')).toBe(true)
    expect(isKnowledgeHtmlPath('knowledge//./a.html')).toBe(true)
    expect(isKnowledgeHtmlPath('knowledge/../config/x.html')).toBe(false)
    expect(isKnowledgeHtmlPath('knowledge/a.txt')).toBe(false)
    expect(isKanalPath('knowledge//Kanal/x.html')).toBe(true)
    expect(isKanalPath('knowledge/./Kanal/x.html')).toBe(true)
    expect(isKanalPath('\\Knowledge\\KANAL\\x.html')).toBe(true)
    expect(isKanalPath('/knowledge/kanal./x.html')).toBe(true)
    expect(isKanalPath('knowledge/a/../Kanal/x.html')).toBe(true)
    expect(isKanalPath('knowledge/Konzepte/x.html')).toBe(false)
  })
  it('Titel stammt nicht aus Script, Style oder Kommentar', () => {
    expect(htmlTitle('<script>x="<title>fake</title>"</script><title>real</title>')).toBe('real')
    expect(htmlTitle('<!-- <title>fake</title> --><style>/*<title>f</title>*/</style><title>real</title>')).toBe('real')
    expect(htmlTitle('<script>x="<title>fake</title>"</script>')).toBeNull()
  })
  it('Block in Kommentar, Style oder fremdem Attribut gilt nicht', () => {
    const json = JSON.stringify({ art: 'konzept', bezuege: [] })
    const echt = `<script type="application/json" id="rowboat-konzept">${json}</script>`
    const falsch = `<script type="application/json" id="rowboat-konzept">{"art":"videokonzept","bezuege":[]}</script>`
    expect(readKonzept(`<!-- ${falsch} -->${echt}`).daten?.art).toBe('konzept')
    expect(readKonzept(`<style>${falsch}</style>${echt}`).daten?.art).toBe('konzept')
    expect(readKonzept(`<script data-x=" id=rowboat-konzept" type="application/json">${falsch}</script>`).daten).toBeNull()
    expect(readKonzept(`<script data-x="a>b" id="rowboat-konzept" type="application/json">${json}</script>`).daten?.art).toBe('konzept')
    expect(readKonzept(`<script id="rowboat-konzept">${json}</script>`).daten).toBeNull() // ohne type
  })
  it('Einträge ohne [[...]] geben Hinweis und behalten die Daten', () => {
    const r = readKonzept(SEITE(JSON.stringify({ art: 'konzept', bezuege: ['[[A/B]]', 'Marke'] })))
    expect(r.daten?.bezuege).toEqual(['[[A/B]]', 'Marke'])
    expect(r.fehler).toHaveLength(1)
  })
  it('isKnowledgeFrameUrl (R43): alles unter app://workspace/knowledge/, im Zweifel eingesperrt', () => {
    for (const p of [
      'knowledge/a.html', 'knowledge/a.HTML', 'knowledge/a.html?x=1', 'knowledge/a.html#y', 'knowledge/a%2Ehtml',
      'knowledge/a.html.', 'Knowledge/K/a.htm', '%6Bnowledge/a.html', 'knowledge%2Fa.html', 'knowledge%5Ca.html',
      '/knowledge/a.html', 'knowledge./a.html', 'knowledge/a.xhtml', 'knowledge/a.png', 'knowledge/seite',
      'knowledge', 'knowledge/', './knowledge/a', 'bg-tasks/../knowledge/a.html', 'bg-tasks/%2e%2e/x',
      'x/..', 'knowledge/%E0%A4%A', 'bg-tasks/%ZZ',
    ]) {
      expect(isKnowledgeFrameUrl(`app://workspace/${p}`), p).toBe(true)
      expect(frameNavigationDecision(`app://workspace/${p}`), p).toBe('block')
    }
    expect(isKnowledgeFrameUrl('APP://Workspace/knowledge/a.html')).toBe(true)
    expect(isKnowledgeFrameUrl('app://workspace\\knowledge\\a.html')).toBe(false) // kein Host app://workspace
    for (const u of [
      'app://workspace/bg-tasks/x/index.html', 'app://workspace/knowledge.html', 'app://workspace/wissen/a.html',
      'app://workspace/index.html', 'app://workspace', 'app://workspace/', 'app://workspacex/knowledge/a.html',
      'app://workspace@evil/knowledge/a.html', 'app://file-preview/knowledge/a.html', 'app://-/index.html',
      'app://space-document/o/s/knowledge/a.html', 'about:blank', 'about:srcdoc', '', 'https://example.com/knowledge/a.html',
    ]) {
      expect(isKnowledgeFrameUrl(u), u).toBe(false)
    }
  })
  it('PDFs unter knowledge/ bleiben frei (R49/R51), Umgehungen über den Namen nicht', () => {
    for (const p of ['knowledge/a.pdf', 'knowledge/A.PDF', 'knowledge/K/Bericht.PDF', 'knowledge/a.pdf?v=2',
      'knowledge/a.pdf#page=3', 'knowledge/a%2Epdf', 'Knowledge/x%20y.pdf', 'knowledge%2Fa.pdf']) {
      expect(isKnowledgeFrameUrl(`app://workspace/${p}`), p).toBe(false)
      expect(frameNavigationDecision(`app://workspace/${p}`), p).toBe('allow')
    }
    for (const p of ['knowledge/a.pdf/x.html', 'knowledge/a.pdf.html', 'knowledge/a.pdf%3Fx.html', 'knowledge/a.pdf/..',
      'knowledge/a.pdf%2F..', 'knowledge/a%2Epdf%00', 'knowledge/%ZZ.pdf', 'knowledge/a.pdfx', 'knowledge/a.pdf.',
      'knowledge/a.pdf%20', 'knowledge/a.pdf%00.html', 'knowledge/a%00.pdf', 'knowledge/a.pdf/', 'knowledge/a.pdf%2F']) {
      expect(isKnowledgeFrameUrl(`app://workspace/${p}`), p).toBe(true)
    }
    // ausgeliefert als Wissens-HTML (Content-Type text/html) oder laut Zustand eingesperrt: gesperrt trotz Namen
    expect(frameNavigationDecision('app://workspace/knowledge/a.pdf', true)).toBe('block')
    expect(frameNavigationDecision('app://workspace/knowledge/a.pdf', false, true)).toBe('block')
    expect(workspaceResponseDecision({ relPaths: ['knowledge/a.pdf'], contentType: 'text/html' })).toBe('konzept')
    expect(workspaceResponseDecision({ relPaths: ['knowledge/a.pdf'], contentType: 'application/pdf' })).toBe('unveraendert')
    // Rückhalt: Anfragen des PDF-Rahmens (Betrachter, chrome-extension:) laufen wie bisher
    const pdf = 'app://workspace/knowledge/a.pdf'
    expect(backstopDecision({
      url: 'chrome-extension://mhjfbmdgcfjbbpaeojofohoefgiehjai/index.html', resourceType: 'subFrame', frameId: 7,
      frameUrl: pdf, ancestorIds: [1], attached: true, chainConfinedByUrl: isKnowledgeFrameUrl(pdf),
      confined: new Set<number>(), recentlyConfined: new Map<number, number>(), now: 1000, appUrl: null,
    })).toBe(false)
  })
  it('isKnowledgeScopePath: erstes Segment knowledge, bei .. im Zweifel ja', () => {
    expect(isKnowledgeScopePath('knowledge/a.html')).toBe(true)
    expect(isKnowledgeScopePath('\\Knowledge\\x\\y.txt')).toBe(true)
    expect(isKnowledgeScopePath('bg-tasks/x/index.html')).toBe(false)
    expect(isKnowledgeScopePath('knowledge')).toBe(false)
    expect(isKnowledgeScopePath('bg-tasks/../knowledge/a.html')).toBe(true)
  })
  it('.xhtml gilt als HTML (Typ bleibt text/html)', () => {
    expect(isHtmlPath('knowledge/a.XHTML')).toBe(true)
    expect(workspaceResponseDecision({ relPaths: ['knowledge/a.xhtml'], contentType: null })).toBe('konzept')
    expect(htmlResponseHeaders()['Content-Type']).toBe('text/html; charset=utf-8')
    expect(frameNavigationDecision('app://workspace/knowledge/a.xhtml')).toBe('block')
  })
  it('workspaceResponseDecision: nur Wissens-HTML bekommt die Regel (R38)', () => {
    const d = workspaceResponseDecision
    expect(d({ relPaths: ['knowledge/a.html'], contentType: 'text/html' })).toBe('konzept')
    expect(d({ relPaths: ['knowledge/a.html'], contentType: 'application/octet-stream' })).toBe('konzept')
    // Hintergrund-Bericht und seine Geschwister: wie vor der Konzept-Fläche
    expect(d({ relPaths: ['bg-tasks/x/index.html', 'bg-tasks/x/index.html'], contentType: 'text/html' })).toBe('unveraendert')
    expect(d({ relPaths: ['bg-tasks/x/bild.png'], contentType: 'image/png' })).toBe('unveraendert')
    expect(d({ relPaths: ['bg-tasks/x/daten'], contentType: null })).toBe('unveraendert')
    // Symlink: angefragt außerhalb, Ziel im Wissensordner (oder umgekehrt) -> Regel
    expect(d({ relPaths: ['bg-tasks/link.html', 'knowledge/a.html'], contentType: 'text/html' })).toBe('konzept')
    expect(d({ relPaths: ['bg-tasks/x.txt', 'knowledge\\a.html'], contentType: 'text/plain' })).toBe('konzept')
    expect(d({ relPaths: ['knowledge/l.html', 'bg-tasks/x/index.html'], contentType: 'text/html' })).toBe('konzept')
    // im Wissensordner nach Content-Type; generisch -> nosniff; Medien unverändert
    expect(d({ relPaths: ['knowledge/seite'], contentType: 'Text/HTML; charset=utf-8' })).toBe('konzept')
    expect(d({ relPaths: ['knowledge/seite'], contentType: null })).toBe('nosniff')
    expect(d({ relPaths: ['knowledge/a.png'], contentType: 'image/png' })).toBe('unveraendert')
    // Fehlerantworten: nur der Content-Type zählt
    expect(d({ relPaths: ['knowledge/a.html'], contentType: 'text/plain', ok: false })).toBe('nosniff')
    expect(d({ relPaths: ['knowledge/a.html'], contentType: 'text/html', ok: false })).toBe('konzept')
    // der Fern-Server hat schon entschieden (z. B. über das Symlink-Ziel)
    expect(d({ relPaths: ['bg-tasks/link.html'], contentType: 'text/html', csp: WORKSPACE_HTML_CSP })).toBe('konzept')
    expect(d({ relPaths: ['bg-tasks/link.html'], contentType: 'text/html', csp: 'default-src *' })).toBe('unveraendert')
  })
  it('isConfinedCommit: knowledge/, ausgeliefert oder Kind eines eingesperrten Rahmens', () => {
    expect(isConfinedCommit('app://workspace/knowledge/a.html', false, false)).toBe(true)
    expect(isConfinedCommit('app://workspace/knowledge/a.pdf', false, false)).toBe(false) // R49
    expect(isConfinedCommit('app://workspace/knowledge/a.pdf', true, false)).toBe(true) // als Wissens-HTML ausgeliefert
    expect(isConfinedCommit('app://workspace/knowledge/seite', false, false)).toBe(true)
    expect(isConfinedCommit('about:srcdoc', false, true)).toBe(true)
    expect(isConfinedCommit('app://workspace/bg-tasks/l.html', true, false)).toBe(true)
    expect(isConfinedCommit('app://workspace/bg-tasks/x/index.html', false, false)).toBe(false)
    expect(isConfinedCommit('https://www.youtube.com/embed/x', false, false)).toBe(false)
  })
  it('Rückhalt-Zustand (R47): eingesperrt auf Lebenszeit, nur der Abbau entlässt, Ablauf nach 5 s', () => {
    const k = 'app://workspace/knowledge/a.html'
    const st = createBackstopState()
    noteFrameCommit(st, { frameId: 7, parentId: 1, url: k, now: 1000 })
    expect([...st.confined]).toEqual([7])
    // keine Navigation entlässt: In-Page, eigener Wechsel nach about:blank oder nach außen
    for (const url of ['https://example.com/#x', 'about:blank', 'https://www.figma.com/embed', 'app://-/index.html']) {
      noteFrameCommit(st, { frameId: 7, parentId: 1, url, now: 1100 })
      expect(st.confined.has(7), url).toBe(true)
    }
    expect(st.recentlyConfined.size).toBe(0)
    // In-Page innerhalb von knowledge/ und Kind eines eingesperrten Rahmens: aufgenommen
    noteFrameCommit(st, { frameId: 9, parentId: 1, url: `${k}#teil`, now: 1100 })
    noteFrameCommit(st, { frameId: 8, parentId: 7, url: 'about:srcdoc', now: 1150 })
    expect(st.confined.has(9) && st.confined.has(8)).toBe(true)
    // fremder Rahmen bleibt draußen
    noteFrameCommit(st, { frameId: 12, parentId: 1, url: 'https://www.youtube.com/embed/x', now: 1150 })
    expect(st.confined.has(12)).toBe(false)
    // Abbau: nicht mehr im Baum -> recentlyConfined
    noteFramesAttached(st, new Set([1, 7, 12]), 2000)
    expect([...st.confined]).toEqual([7])
    expect(st.recentlyConfined.get(8)).toBe(2000)
    expect(st.recentlyConfined.get(9)).toBe(2000)
    // Ablauf nach CONFINED_GRACE_MS
    noteFramesAttached(st, new Set([1, 7]), 2000 + CONFINED_GRACE_MS)
    expect(st.recentlyConfined.has(8)).toBe(true)
    noteFramesAttached(st, new Set([1, 7]), 2001 + CONFINED_GRACE_MS)
    expect(st.recentlyConfined.size).toBe(0)
    expect(CONFINED_GRACE_MS).toBe(5000)
  })
  it('Navigationssperre nach Zustand (R47): Rahmen oder Vorfahr eingesperrt -> block', () => {
    const confined = new Set([7])
    const recent = new Map([[9, 1000]])
    expect(isFrameChainConfined([7, 1], confined, recent, 1000)).toBe(true)
    expect(isFrameChainConfined([8, 7, 1], confined, recent, 1000)).toBe(true)
    expect(isFrameChainConfined([9, 1], confined, recent, 1000 + CONFINED_GRACE_MS)).toBe(true)
    expect(isFrameChainConfined([9, 1], confined, recent, 1001 + CONFINED_GRACE_MS)).toBe(false)
    expect(isFrameChainConfined([12, 1], confined, recent, 1000)).toBe(false)
    // Rahmen hat sich selbst nach about:blank / nach außen navigiert: Adresse sagt nichts mehr, der Zustand schon
    expect(frameNavigationDecision('about:blank', false, true)).toBe('block')
    expect(frameNavigationDecision('https://example.com/', false, true)).toBe('block')
    expect(frameNavigationDecision('https://www.youtube.com/embed/x', false, false)).toBe('allow')
  })
  it('backstopDecision (R44-R47): jeder Zweig, der Verweiser zählt nie', () => {
    const k = 'app://workspace/knowledge/a.html'
    const leer = { confined: new Set<number>(), recentlyConfined: new Map<number, number>() }
    const offen = { confined: new Set([7]), recentlyConfined: new Map<number, number>() }
    const kuerzlich = { confined: new Set<number>(), recentlyConfined: new Map([[7, 1000]]) }
    const basis = {
      url: 'https://example.com/x', resourceType: 'image', frameId: 7, frameUrl: k, ancestorIds: [1], attached: true,
      chainConfinedByUrl: true, ...offen, now: 1000, appUrl: 'http://localhost:5173',
    }
    // eingesperrter Rahmen: nur data:/blob:/about: und das eigene Dokument (ohne Query/Fragment)
    for (const url of ['data:,x', 'blob:null/1', 'about:blank', k, `${k}#teil`, `${k}?v=2`]) {
      expect(backstopDecision({ ...basis, url }), url).toBe(false)
    }
    for (const url of ['https://example.com/x', 'app://workspace/knowledge/b.html', 'app://workspace/knowledge/b.png',
      'app://-/index.html', 'devtools://devtools/x', 'chrome-extension://pdf/x.js', 'http://localhost:5173/src/x.ts',
      'http://127.0.0.1:8765/', 'kein-schema']) {
      expect(backstopDecision({ ...basis, url }), url).toBe(true)
    }
    // eingesperrt nur nach Zustand (Rahmen hat sich selbst nach about:blank/außen navigiert) oder über einen Vorfahr
    expect(backstopDecision({ ...basis, chainConfinedByUrl: false, frameUrl: 'about:blank' })).toBe(true)
    expect(backstopDecision({ ...basis, chainConfinedByUrl: false, frameUrl: 'https://example.com/x', url: 'https://example.com/y' })).toBe(true)
    // R48: "eigenes Dokument" nur für eine Adresse unter knowledge/
    expect(backstopDecision({ ...basis, chainConfinedByUrl: false, frameUrl: 'https://x/', url: 'https://x/?d=1' })).toBe(true)
    expect(backstopDecision({ ...basis, chainConfinedByUrl: false, frameUrl: 'https://x/', url: 'https://x/' })).toBe(true)
    expect(backstopDecision({ ...basis, frameUrl: `${k}?v=3`, url: `${k}?v=4` })).toBe(false)
    expect(backstopDecision({ ...basis, chainConfinedByUrl: false, frameId: 8, frameUrl: 'about:srcdoc', ancestorIds: [7, 1], url: 'app://-/x.js' })).toBe(true)
    expect(backstopDecision({ ...basis, chainConfinedByUrl: false, ...kuerzlich, now: 1000 + CONFINED_GRACE_MS })).toBe(true)
    // bekannter, angehängter, nicht eingesperrter Rahmen: alles frei, auch bei offenem Konzept
    const app = { ...basis, chainConfinedByUrl: false, frameId: 1, frameUrl: 'app://-/index.html', ancestorIds: [] }
    for (const url of ['https://api.example.com/v1', 'app://-/x.js', 'chrome-extension://pdf/x.js', 'wss://sync.example/']) {
      expect(backstopDecision({ ...app, url }), url).toBe(false)
    }
    expect(backstopDecision({ ...app, frameId: 12, ancestorIds: [1], frameUrl: 'https://www.youtube.com/embed/x' })).toBe(false)
    expect(backstopDecision({ ...basis, ...kuerzlich, chainConfinedByUrl: false, now: 1001 + CONFINED_GRACE_MS })).toBe(false)
    // Rahmen nicht identifizierbar, nicht lesbar oder nicht im Baum
    for (const rahmen of [{ frameId: null, frameUrl: null, ancestorIds: [] }, { frameId: 12, frameUrl: null, ancestorIds: [], attached: false }]) {
      const u = { ...basis, ...rahmen, chainConfinedByUrl: false }
      const n = JSON.stringify(rahmen)
      expect(backstopDecision(u), n).toBe(true)
      expect(backstopDecision({ ...u, ...kuerzlich, now: 2000 }), n).toBe(true)
      expect(backstopDecision({ ...u, ...kuerzlich, now: 1001 + CONFINED_GRACE_MS }), n).toBe(false)
      expect(backstopDecision({ ...u, ...leer }), n).toBe(false)
      for (const url of ['app://-/x.js', 'devtools://devtools/x', 'http://localhost:5173/src/x.ts', 'ws://localhost:5173/', 'data:,x']) {
        expect(backstopDecision({ ...u, url }), url).toBe(false)
      }
      for (const url of ['chrome-extension://pdf/x.js', 'http://localhost:9999/', 'kein-schema']) {
        expect(backstopDecision({ ...u, url }), url).toBe(true)
      }
    }
    // Laden eines Hauptrahmens: nie
    expect(backstopDecision({ ...basis, resourceType: 'mainFrame' })).toBe(false)
    expect(backstopDecision({ ...basis, resourceType: 'mainFrame', frameId: null, chainConfinedByUrl: false })).toBe(false)
  })
  it('needsNosniffOnly: fehlend oder generisch', () => {
    expect(needsNosniffOnly(null)).toBe(true)
    expect(needsNosniffOnly('')).toBe(true)
    expect(needsNosniffOnly('application/octet-stream')).toBe(true)
    expect(needsNosniffOnly('Text/Plain; charset=utf-8')).toBe(true)
    expect(needsNosniffOnly('image/png')).toBe(false)
    expect(needsNosniffOnly('video/mp4')).toBe(false)
    expect(needsNosniffOnly('application/pdf')).toBe(false)
  })
  it('hostile Eingaben bleiben linear schnell', () => {
    for (const eingabe of ['<'.repeat(200_000), '<script '.repeat(50_000), '<!--'.repeat(100_000), '<a title="'.repeat(50_000)]) {
      const t0 = Date.now()
      htmlTitle(eingabe)
      htmlVisibleText(eingabe)
      readKonzept(eingabe)
      expect(Date.now() - t0).toBeLessThan(1000)
    }
  })
  it('liest höchstens KONZEPT_LESE_MAX Zeichen', () => {
    const html = '<title>t</title>' + 'x'.repeat(KONZEPT_LESE_MAX + 10) + '<p>ende</p>'
    expect(htmlVisibleText(html).endsWith('ende')).toBe(false)
  })
})

describe('Gastbereich', () => {
  it('kennt Partition, Kennzeichen und Kanal', () => {
    expect(KONZEPT_PARTITION).toBe('konzept')
    expect(KONZEPT_GAST_KANAL).toBe('konzept')
    expect(KONZEPT_GAST_ARG).toBe('--rowboat-konzept-gast')
  })
  it('lässt nur Wissens-Dokumente als Gast zu', () => {
    expect(guestAttachAllowed('app://workspace/knowledge/Konzepte/k.html')).toBe(true)
    expect(guestAttachAllowed('app://workspace/knowledge/Konzepte/k.html?v=3')).toBe(true)
    expect(guestAttachAllowed('APP://WORKSPACE/knowledge/Kanal/q.html')).toBe(true)
    for (const u of ['https://example.com/', 'file:///c:/x.html', 'app://-/index.html', 'app://workspace/bg-tasks/r/index.html',
      'app://workspace/knowledge/../config/x.html', 'app://workspace/knowledge%2F..%2Fconfig/x.html', 'app://space-document/a/b/c', 'javascript:1', '', 'app://workspace/'])
      expect(guestAttachAllowed(u)).toBe(false)
  })
  it('lässt keine Grenzfälle als Gast zu', () => {
    for (const u of ['app://workspace/knowledge', 'app://workspace/knowledge/', 'app://workspace/knowledge/a/%E0%A4%A.html',
      'app://workspace/knowledge/a/%2e%2e/%2e%2e/config/x.html', 'app://workspace/knowledge\\..\\config\\x.html',
      'app://workspacex/knowledge/k.html', 'app://workspace.evil/knowledge/k.html', 'app://workspace@x/knowledge/k.html',
      ' app://workspace/knowledge/k.html', 'blob:app://workspace/knowledge/k.html'])
      expect(guestAttachAllowed(u), u).toBe(false)
    expect(guestAttachAllowed('app://workspace/Knowledge./Konzepte/k.html')).toBe(true)
    expect(guestAttachAllowed('app://workspace/knowledge/Konzepte/k%20k.html#x')).toBe(true)
  })
  it('liefert in der Gast-Sitzung nur Wissens-Adressen aus', () => {
    expect(guestProtocolAllowed('app://workspace/knowledge/Konzepte/k.html?v=1')).toBe(true)
    for (const u of ['app://-/index.html', 'app://workspace/config/x.json', 'app://workspace/knowledge/../config/x.json',
      'app://space-blob/a/b/c', 'app://file-preview/x', 'app://workspace/'])
      expect(guestProtocolAllowed(u), u).toBe(false)
  })
  it('erzwingt die Gast-Rechte', () => {
    expect(guestWebPreferences()).toEqual({ nodeIntegration: false, nodeIntegrationInSubFrames: false, contextIsolation: true, sandbox: true, plugins: false, webviewTag: false, partition: 'konzept' })
  })
  it('erlaubt in der Gast-Sitzung nur das eigene Dokument', () => {
    const doc = 'app://workspace/knowledge/Konzepte/k.html?v=2'
    expect(guestRequestAllowed('app://workspace/knowledge/Konzepte/k.html?v=2', doc)).toBe(true)
    expect(guestRequestAllowed('data:image/png;base64,AA', doc)).toBe(true)
    expect(guestRequestAllowed('blob:app://workspace/1', doc)).toBe(true)
    for (const u of ['app://workspace/knowledge/Konzepte/anderes.html', 'app://-/index.html', 'https://example.com/', 'http://127.0.0.1:8765/x', 'ws://127.0.0.1/', 'file:///c:/x'])
      expect(guestRequestAllowed(u, doc)).toBe(false)
    expect(guestRequestAllowed('app://workspace/knowledge/Konzepte/k.html', null)).toBe(true)   // erstes Laden: Wissens-Dokument
    expect(guestRequestAllowed('https://example.com/', null)).toBe(false)
  })
  it('vergleicht das eigene Dokument ohne Abfrage, Fragment und Groß/Klein von Schema und Host', () => {
    const doc = 'APP://WORKSPACE/knowledge/Konzepte/k%20k.html?v=2#oben'
    expect(guestRequestAllowed('app://workspace/knowledge/Konzepte/k%20k.html?v=9', doc)).toBe(true)
    expect(guestRequestAllowed('app://workspace/knowledge/Konzepte/k%20k.html', 'app://workspace/knowledge/Konzepte/k k.html')).toBe(false) // roh: abgelehnt
    expect(guestRequestAllowed('app://workspace/knowledge/Konzepte/k k.html', doc)).toBe(false)
    expect(guestRequestAllowed('app://workspace/knowledge/Konzepte/k%20k.html#unten', doc)).toBe(true)
    expect(guestRequestAllowed('app://workspace/knowledge/konzepte/k%20k.html', doc)).toBe(false)
    // Ein Dokument außerhalb von knowledge/ gilt nie als "eigenes Dokument"
    expect(guestRequestAllowed('https://example.com/a', 'https://example.com/a')).toBe(false)
    expect(guestRequestAllowed('app://-/index.html', 'app://-/index.html')).toBe(false)
    expect(guestRequestAllowed('app://workspace/knowledge/a/%E0%A4%A.html', 'app://workspace/knowledge/a/%E0%A4%A.html')).toBe(false)
    expect(guestRequestAllowed('about:blank', null)).toBe(true)
    expect(guestRequestAllowed('', null)).toBe(false)
    expect(guestRequestAllowed('devtools://devtools/x', doc)).toBe(false)
  })
  it('Stolperdraht: nur die angehängte Wissens-Adresse darf geladen werden', () => {
    const src = 'app://workspace/knowledge/Konzepte/k%20k.html?v=2'
    expect(guestNavigationAllowed(src, src)).toBe(true)
    expect(guestNavigationAllowed('APP://WORKSPACE/knowledge/Konzepte/k%20k.html?v=2', src)).toBe(true)
    expect(guestNavigationAllowed('app://workspace/knowledge/Konzepte/k%20k.html?v=3#x', src)).toBe(true) // ohne Query/Fragment
    for (const u of ['app://workspace/knowledge/Konzepte/anderes.html', 'app://workspace/knowledge/konzepte/k%20k.html',
      'file:///C:/Windows/win.ini', 'chrome://gpu', 'devtools://devtools/bundled/inspector.html', 'https://example.com/',
      'data:text/html,<script>1</script>', 'about:blank', 'blob:app://workspace/1', 'app://-/index.html', 'javascript:1', '',
      'app://workspace/knowledge/Konzepte/k%20k.html\t', 'app://workspace/knowledge/.\t./config/x.html'])
      expect(guestNavigationAllowed(u, src), u).toBe(false)
    expect(guestNavigationAllowed('app://workspace/config/x.html', 'app://workspace/config/x.html')).toBe(false)
    expect(guestNavigationAllowed(src, null)).toBe(false)
  })
  it('lehnt Adressen mit Steuerzeichen oder Leerraum überall ab', () => {
    const doc = 'app://workspace/knowledge/Konzepte/k.html'
    for (const z of ['\t', '\n', '\r', ' ', '\u0000', '\u007f']) {
      for (const u of [`app://workspace/knowledge/.${z}./config/x.html`, `app://workspace/knowledge/Konzepte/k${z}.html`, `${doc}${z}`]) {
        expect(guestAttachAllowed(u), JSON.stringify(u)).toBe(false)
        expect(guestProtocolAllowed(u), JSON.stringify(u)).toBe(false)
        expect(guestRequestAllowed(u, doc), JSON.stringify(u)).toBe(false)
        expect(guestRequestAllowed(u, null), JSON.stringify(u)).toBe(false)
        expect(guestNavigationAllowed(u, doc), JSON.stringify(u)).toBe(false)
        expect(guestNavigationAllowed(doc, u), JSON.stringify(u)).toBe(false)
      }
      // data:/blob:/about: bleiben im Dokument: Leerraum im Inhalt ist dort normal und wird nicht abgelehnt
      expect(guestRequestAllowed(`data:text/plain,a${z}b`, doc), JSON.stringify(z)).toBe(true)
      expect(guestInDocumentRequest(`about:blank${z}`), JSON.stringify(z)).toBe(true)
      // ... aber nicht vor dem Doppelpunkt (anderes Schema)
      expect(guestInDocumentRequest(`${z}data:text/plain,x`), JSON.stringify(z)).toBe(false)
      expect(guestInDocumentRequest(`da${z}ta:text/plain,x`), JSON.stringify(z)).toBe(false)
    }
    expect(guestAttachAllowed('app://workspace/knowledge/%2e%09%2e/config/x.html')).toBe(false)
  })
  it('erlaubt data:-Bilder mit Leerzeichen, aber nie data: als Gast-Dokument', () => {
    const doc = 'app://workspace/knowledge/Konzepte/k.html?v=1'
    const svg = 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"></svg>'
    expect(guestRequestAllowed(svg, doc)).toBe(true)
    expect(guestRequestAllowed(svg, null)).toBe(true)
    expect(guestInDocumentRequest(svg)).toBe(true)
    expect(guestRequestAllowed('DATA:image/svg+xml,<svg width="1"></svg>', doc)).toBe(true)
    const html = 'data:text/html,<b>x</b>'
    expect(guestAttachAllowed(html)).toBe(false)
    expect(guestProtocolAllowed(html)).toBe(false)
    expect(guestNavigationAllowed(html, doc)).toBe(false)
    expect(guestNavigationAllowed(svg, doc)).toBe(false)
  })
  it('verlangt die kanonische Gast-Adresse (wie der Betrachter sie baut)', () => {
    for (const u of ['app://workspace/knowledge/Konzepte/k.html', 'app://workspace/knowledge/Konzepte/Mein%20Konzept%20%C3%A4.html?v=2',
      'app://workspace/knowledge/Kanal/q.html#x', 'APP://WORKSPACE/knowledge/Kanal/q.html', 'app://workspace/knowledge/a.b/k.html'])
      expect(guestAttachAllowed(u), u).toBe(true)
    for (const u of ['app://workspace/knowledge/./Konzepte/k.html', 'app://workspace/knowledge/Konzepte/./k.html',
      'app://workspace/knowledge/Konzepte/%2e/k.html', 'app://workspace/knowledge/Konzepte/%2E/k.html', 'app://workspace/knowledge/Konzepte/k%2ehtml',
      'app://workspace/knowledge/Konzepte/.', 'app://workspace/knowledge\\Konzepte\\k.html', 'app://workspace/knowledge/Konzepte\\k.html',
      'app://workspace/knowledge/Konzepte%2Fk.html', 'app://workspace/knowledge/Konzepte%5ck.html', 'app://workspace/./knowledge/k.html'])
      expect(guestAttachAllowed(u), u).toBe(false)
    // Nicht-kanonisch angehängt und daher vom Stolperdraht beim ersten Laden getötet: gibt es nicht mehr
    expect(guestNavigationAllowed('app://workspace/knowledge/Konzepte/k.html?v=1', 'app://workspace/knowledge/./Konzepte/k.html?v=1')).toBe(false)
    expect(guestAttachAllowed('app://workspace/knowledge/./Konzepte/k.html?v=1')).toBe(false)
  })
  it('entscheidet Anfragen der Gast-Sitzung nach Zustand (eine Tabelle)', () => {
    const doc = 'app://workspace/knowledge/Konzepte/k.html?v=1'
    const basis = { dead: false, proxyConfirmed: true, hasWebContents: true, documentUrl: doc as string | null }
    const urls = ['app://workspace/knowledge/Konzepte/k.html?v=2', 'data:image/svg+xml,<svg width="1"></svg>', 'blob:app://workspace/1', 'about:blank',
      'app://workspace/knowledge/Konzepte/anderes.html', 'https://example.com/', 'file:///c:/x', 'chrome://gpu']
    const erwartet: Array<[Partial<typeof basis>, boolean[]]> = [
      [{}, [true, true, true, true, false, false, false, false]],
      [{ dead: true }, [false, false, false, false, false, false, false, false]],
      [{ proxyConfirmed: false }, [false, false, false, false, false, false, false, false]],
      [{ dead: true, proxyConfirmed: false }, [false, false, false, false, false, false, false, false]],
      [{ hasWebContents: false }, [false, true, true, true, false, false, false, false]],
      [{ documentUrl: null }, [false, true, true, true, false, false, false, false]],
      [{ documentUrl: 'https://example.com/' }, [false, true, true, true, false, false, false, false]],
    ]
    for (const [aenderung, ergebnisse] of erwartet)
      urls.forEach((url, i) =>
        expect(guestRequestDecision({ ...basis, ...aenderung, url }), `${JSON.stringify(aenderung)} ${url}`).toBe(ergebnisse[i]))
  })
  it('Stolperdraht nach Zustand: ein beendeter Gast navigiert nie wieder', () => {
    const src = 'app://workspace/knowledge/Konzepte/k.html?v=1'
    const n = (url: string, o: { dead?: boolean; isMainFrame?: boolean; isSameDocument?: boolean; attachedSrc?: string | null; pageInitiated?: boolean } = {}) =>
      guestTripwireDecision({ dead: false, isMainFrame: true, isSameDocument: false, attachedSrc: src, pageInitiated: false, url, ...o })
    expect(n(src)).toBe('erlauben')
    expect(n('app://workspace/knowledge/Konzepte/k.html?v=7')).toBe('erlauben')
    expect(n('https://example.com/')).toBe('beenden')
    expect(n('about:blank')).toBe('beenden')
    expect(n('chrome://gpu')).toBe('beenden')
    expect(n(src, { attachedSrc: null })).toBe('beenden')
    expect(n('https://example.com/', { attachedSrc: null, pageInitiated: false })).toBe('beenden')
    expect(n('https://example.com/', { isMainFrame: false })).toBe('erlauben') // Unterrahmen: will-frame-navigate sperrt
    expect(n(`${src}#unten`, { isSameDocument: true })).toBe('erlauben')
    expect(n(src, { dead: true })).toBe('beenden')
    expect(n(`${src}#unten`, { dead: true, isSameDocument: true })).toBe('beenden')
    expect(n('about:blank', { dead: true })).toBe('beenden')
    expect(n('https://example.com/', { dead: true, isMainFrame: false })).toBe('beenden')
  })
  it('Stolperdraht: von der Seite ausgeloest wird nur verhindert (ueberlassen), ausser about:', () => {
    const src = 'app://workspace/knowledge/Konzepte/k.html?v=1'
    const n = (url: string, o: { dead?: boolean; isMainFrame?: boolean; isSameDocument?: boolean; attachedSrc?: string | null; pageInitiated?: boolean }) =>
      guestTripwireDecision({ dead: false, isMainFrame: true, isSameDocument: false, attachedSrc: src, pageInitiated: false, url, ...o })
    const fremd = ['https://example.com/', 'app://workspace/knowledge/Konzepte/anderes.html', 'file:///C:/Windows/win.ini', 'chrome://gpu', 'data:text/html,<b>x</b>']
    for (const u of fremd) {
      expect(n(u, { pageInitiated: true }), `seite ${u}`).toBe('ueberlassen')
      expect(n(u, { pageInitiated: false }), `browser ${u}`).toBe('beenden')
      expect(n(u, { pageInitiated: true, dead: true }), `tot ${u}`).toBe('beenden')
      expect(n(u, { pageInitiated: false, dead: true }), `tot ${u}`).toBe('beenden')
    }
    for (const u of ['about:blank', 'ABOUT:blank', 'about:srcdoc', ' about:blank', '\u0000about:blank', '\tAbout:blank'])
      expect(n(u, { pageInitiated: true }), `about ${JSON.stringify(u)}`).toBe('beenden')
    for (const p of [true, false]) {
      expect(n(src, { pageInitiated: p })).toBe('erlauben')
      expect(n('https://example.com/', { pageInitiated: p, isMainFrame: false })).toBe('erlauben')
      expect(n(`${src}#x`, { pageInitiated: p, isSameDocument: true })).toBe('erlauben')
      expect(n('https://example.com/', { pageInitiated: p, dead: true, isMainFrame: false })).toBe('beenden')
    }
    expect(n('https://example.com/', { pageInitiated: true, attachedSrc: null })).toBe('ueberlassen')
  })
  it('gibt ohne bekannten Gast nur Anfragen frei, die im Dokument bleiben', () => {
    for (const u of ['data:image/png;base64,AA', 'blob:app://workspace/1', 'about:blank', 'ABOUT:blank'])
      expect(guestInDocumentRequest(u), u).toBe(true)
    for (const u of ['app://workspace/knowledge/Konzepte/k.html', 'https://example.com/', 'file:///c:/x', 'chrome://gpu', 'devtools://x', '', 'data'])
      expect(guestInDocumentRequest(u), u).toBe(false)
  })
  it('prüft weiterzureichende Nachrichten', () => {
    expect(forwardableGuestMessage({ type: 'rowboat:link-oeffnen', url: 'https://example.com/a' })).toEqual({ type: 'rowboat:link-oeffnen', url: 'https://example.com/a' })
    expect(forwardableGuestMessage({ type: 'rowboat:notiz-oeffnen', pfad: 'knowledge/Marke/Marke.md', extra: 1 })).toEqual({ type: 'rowboat:notiz-oeffnen', pfad: 'knowledge/Marke/Marke.md' })
    for (const d of [null, 'x', { type: 'anderes' }, { type: 'rowboat:link-oeffnen', url: 5 }, { type: 'rowboat:link-oeffnen', url: 'a'.repeat(4001) }, { type: 'rowboat:notiz-oeffnen' }])
      expect(forwardableGuestMessage(d)).toBeNull()
  })
  it('verwirft Nachrichten falscher Form', () => {
    expect(forwardableGuestMessage({ type: 'rowboat:link-oeffnen', url: 'a'.repeat(4000) })).toEqual({ type: 'rowboat:link-oeffnen', url: 'a'.repeat(4000) })
    for (const d of [undefined, 5, [], ['rowboat:link-oeffnen'], { type: 'rowboat:link-oeffnen', pfad: 'x' }, { type: 'rowboat:notiz-oeffnen', url: 'x' },
      { type: 'rowboat:notiz-oeffnen', pfad: { toString: () => 'x' } }, { type: 'rowboat:notiz-oeffnen', pfad: 'p'.repeat(4001) },
      { type: ['rowboat:link-oeffnen'], url: 'x' }])
      expect(forwardableGuestMessage(d)).toBeNull()
  })
  it('liest jedes Feld nur einmal (ein Getter kann nicht nach der Prüfung tauschen)', () => {
    let urlLesungen = 0
    const tauscher = {
      get type() { return 'rowboat:link-oeffnen' },
      get url() { urlLesungen++; return urlLesungen === 1 ? 'https://example.com/a' : 'x'.repeat(5000) },
    }
    expect(forwardableGuestMessage(tauscher)).toEqual({ type: 'rowboat:link-oeffnen', url: 'https://example.com/a' })
    expect(urlLesungen).toBe(1)
    let typLesungen = 0
    const typTauscher = {
      get type() { typLesungen++; return typLesungen === 1 ? 'rowboat:notiz-oeffnen' : 'rowboat:link-oeffnen' },
      pfad: 'knowledge/a.md',
      url: 'https://example.com/',
    }
    expect(forwardableGuestMessage(typTauscher)).toEqual({ type: 'rowboat:notiz-oeffnen', pfad: 'knowledge/a.md' })
    expect(typLesungen).toBe(1)
  })
  it('führt webrtc nicht mehr in der Regel', () => { expect(WORKSPACE_HTML_CSP).not.toContain('webrtc'); expect(WORKSPACE_HTML_CSP).toContain('sandbox allow-scripts') })
})

describe('konzeptBlockTexte / htmlSearchText (Suche)', () => {
  const video = (extra: Record<string, unknown> = {}) => ({
    thema: 'Thema Eins', zielgruppe: 'Zielgruppe Zwei', minuten: 4, hook: 'Hook Drei', kernaussage: 'Kern Vier',
    stufen: [
      { name: 'Stufe Fuenf', szenen: [{ kurz: 'Kurz Sechs', bildart: 'avatar_slide', notiz: 'Notiz Sieben' }, { kurz: 'Kurz Acht', bildart: 'clip' }] },
      { name: 'Stufe Neun', szenen: [{ kurz: 'Kurz Zehn', bildart: 'slide', notiz: '  Notiz\n Elf ' }] },
    ],
    abschluss: 'Abschluss Zwoelf', auftrag: null, ...extra,
  })
  const block = (daten: unknown) => SEITE(typeof daten === 'string' ? daten : JSON.stringify(daten))

  it('liefert alle Textfelder in Reihenfolge, eine Zeile je Feld', () => {
    const html = block({ art: 'videokonzept', bezuege: ['[[X]]'], video: video() })
    expect(konzeptBlockTexte(html)).toEqual([
      'Thema Eins', 'Zielgruppe Zwei', 'Hook Drei', 'Kern Vier', 'Stufe Fuenf', 'Kurz Sechs', 'Notiz Sieben', 'Kurz Acht',
      'Stufe Neun', 'Kurz Zehn', 'Notiz Elf', 'Abschluss Zwoelf',
    ])
  })

  it('htmlSearchText: erst der statische Text, dann die Felder, je Feld eine Zeile', () => {
    const html = block({ art: 'videokonzept', bezuege: [], video: video() })
    const t = htmlSearchText(html)
    expect(t.startsWith('Mein & Konzept Hallo Welt\nThema Eins\nZielgruppe Zwei\n')).toBe(true)
    expect(t.endsWith('\nAbschluss Zwoelf')).toBe(true)
  })

  it('bezuege, art, bildart, minuten, firma sind kein Text', () => {
    const html = block({ art: 'videokonzept', bezuege: ['[[Geheimbezug]]'], video: video({ firma: 'Firmenname' }) })
    const t = htmlSearchText(html)
    for (const nein of ['Geheimbezug', 'videokonzept', 'avatar_slide', 'clip', 'Firmenname', 'minuten']) expect(t).not.toContain(nein)
  })

  it('Werte sind Klartext: Tags und Entitaeten bleiben woertlich', () => {
    const html = block({ art: 'videokonzept', bezuege: [], video: video({ hook: '<b>fett</b> &amp; so' }) })
    expect(konzeptBlockTexte(html)).toContain('<b>fett</b> &amp; so')
  })

  it('ein Block von mehr als KONZEPT_BLOCK_MAX Zeichen fuegt nichts hinzu', () => {
    const gross = block({ art: 'videokonzept', bezuege: [], video: video({ hook: 'x'.repeat(KONZEPT_BLOCK_MAX) }) })
    expect(konzeptBlockTexte(gross)).toEqual([])
    expect(htmlSearchText(gross)).toBe('Mein & Konzept Hallo Welt')
    const knapp = JSON.stringify({ art: 'videokonzept', bezuege: [], video: video({ hook: 'y' }), fuell: '' })
    const auf = KONZEPT_BLOCK_MAX - knapp.length
    const genau = block(JSON.stringify({ art: 'videokonzept', bezuege: [], video: video({ hook: 'y' }), fuell: 'z'.repeat(auf) }))
    expect(konzeptBlockTexte(genau)).toContain('y')
    const eins = block(JSON.stringify({ art: 'videokonzept', bezuege: [], video: video({ hook: 'y' }), fuell: 'z'.repeat(auf + 1) }))
    expect(konzeptBlockTexte(eins)).toEqual([])
  })

  it('kaputtes JSON, kein video, video kein Objekt: nichts, kein Fehler', () => {
    expect(konzeptBlockTexte(block('{"art": "videokonzept", '))).toEqual([])
    expect(konzeptBlockTexte(block({ art: 'konzept', bezuege: [] }))).toEqual([])
    expect(konzeptBlockTexte(block({ art: 'videokonzept', video: 'text' }))).toEqual([])
    expect(konzeptBlockTexte(block({ art: 'videokonzept', video: [video()] }))).toEqual([])
    expect(konzeptBlockTexte(block('[1,2]'))).toEqual([])
    expect(konzeptBlockTexte('<p>kein Block</p>')).toEqual([])
    expect(htmlSearchText('<p>kein Block</p>')).toBe('kein Block')
  })

  it('nur der erste Block zaehlt', () => {
    const erster = JSON.stringify({ art: 'videokonzept', bezuege: [], video: video({ thema: 'Erstes Thema' }) })
    const zweiter = JSON.stringify({ art: 'videokonzept', bezuege: [], video: video({ thema: 'Zweites Thema' }) })
    const html = `<script type="application/json" id="rowboat-konzept">${erster}</script><script type="application/json" id="rowboat-konzept">${zweiter}</script>`
    const t = konzeptBlockTexte(html)
    expect(t).toContain('Erstes Thema')
    expect(t).not.toContain('Zweites Thema')
  })

  it('</script in einem Wert beendet den Block; was folgt, ist kein Blocktext', () => {
    const json = JSON.stringify({ art: 'videokonzept', bezuege: [], video: video({ hook: 'vor </script><p>Nachwort</p> nach' }) })
    const html = `<script type="application/json" id="rowboat-konzept">${json}</script>`
    expect(() => konzeptBlockTexte(html)).not.toThrow()
    expect(konzeptBlockTexte(html)).toEqual([]) // abgeschnittenes JSON ist unlesbar
    expect(htmlSearchText(html)).not.toContain('Thema Eins')
  })

  it('Werte, die keine Zeichenketten sind, entfallen; leere ebenso', () => {
    const v = {
      thema: 5, zielgruppe: '   ', hook: null, kernaussage: { a: 1 }, abschluss: ['x'],
      stufen: [{ name: 7, szenen: [{ kurz: 1, notiz: false }, 'quatsch', { kurz: 'Gueltig' }] }, 'nope', { name: 'Name', szenen: 'keine' }],
    }
    expect(konzeptBlockTexte(block({ art: 'videokonzept', bezuege: [], video: v }))).toEqual(['Gueltig', 'Name'])
  })

  it('auch ein Block, den readKonzept/checkVideoKonzept ablehnen, ist durchsuchbar (6 Szenen, ohne art)', () => {
    const v = video()
    v.stufen = [{ name: 'S', szenen: Array.from({ length: 6 }, (_, i) => ({ kurz: `Szene ${i}`, bildart: 'slide' })) }]
    const html = block({ video: { ...v, thema: 'Quittenprobe' } })
    expect(readKonzept(html).daten).toBeNull()
    expect(checkVideoKonzept(v).length).toBeGreaterThan(0)
    const t = konzeptBlockTexte(html)
    expect(t[0]).toBe('Quittenprobe')
    expect(t).toContain('Szene 5')
  })
})
