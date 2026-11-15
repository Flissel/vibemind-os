import { describe, expect, it } from 'vitest'
import { konzept } from '@x/shared'
import {
  buildKonzeptGraph, canCreateWikiTarget, escapeHtml, graphHtmlData, graphHtmlNode, graphLabelHtml, graphNodeLabel,
  graphResolver, knowledgeHtmlFiles, konzeptPerName, konzepteMitNotizBezug, namensIndex, notizGraph, notizZiele,
  resolveWikiTarget, wikiDateiPfad,
} from './graph-html'
import { buildHierarchyGraph } from './graph-hierarchy'
import { toKnowledgePath, wikiLabel } from './wiki-links'

describe('graph-html', () => {
  it('benennt Knoten', () => {
    expect(graphNodeLabel('knowledge/Konzepte/x.html', '<title>Mein Konzept</title>')).toBe('Mein Konzept')
    expect(graphNodeLabel('knowledge/Konzepte/x.HTM', '<p>ohne</p>')).toBe('x')
    expect(graphNodeLabel('knowledge/Konzepte/y.xhtml', '')).toBe('y')
  })

  it('benennt Notizen wie bisher', () => {
    expect(graphNodeLabel('knowledge/Notiz.md', '# Titel\n<title>nein</title>')).toBe('Notiz')
  })

  it('liefert den Titel als reinen Text, nicht als Markup', () => {
    expect(graphNodeLabel('knowledge/a.html', '<title>&lt;img src=x onerror=alert(1)&gt;</title>')).toBe('<img src=x onerror=alert(1)>')
  })

  it('kappt große Dateien', () => {
    expect(graphHtmlData('a '.repeat(konzept.KONZEPT_LESE_MAX)).length).toBeLessThanOrEqual(konzept.KONZEPT_LESE_MAX + 1)
    expect(graphHtmlData('<p>klein</p>')).toContain('klein')
  })

  it('liest bezuege und sichtbaren Text, nicht Skripte', () => {
    const html = '<script>var x = a[[0]]</script><p>siehe [[Notiz]]</p>'
      + '<script type="application/json" id="rowboat-konzept">{"art":"konzept","bezuege":["[[Marke/Marke]]"]}</script>'
    const data = graphHtmlData(html)
    expect(data).toContain('[[Marke/Marke]]')
    expect(data).toContain('[[Notiz]]')
    expect(data).not.toContain('[[0]]')
  })

  it('ergibt keine Kante aus Skript-Code', () => {
    const nodes = new Set(['knowledge/0.md', 'knowledge/k.html'])
    const { edges } = buildHierarchyGraph([
      { path: 'knowledge/k.html', data: graphHtmlData('<script>var x = a[[0]]</script><p>text</p>') },
    ], nodes, toKnowledgePath)
    expect(edges).toHaveLength(0)
  })

  it('ergibt Kanten aus bezuege und aus sichtbarem [[Notiz]]', () => {
    const html = '<p>siehe [[Notiz]]</p><script type="application/json" id="rowboat-konzept">{"art":"konzept","bezuege":["[[Marke/Marke]]"]}</script>'
    const nodes = new Set(['knowledge/Marke/Marke.md', 'knowledge/Notiz.md', 'knowledge/k.html'])
    const { edges } = buildHierarchyGraph([{ path: 'knowledge/k.html', data: graphHtmlData(html) }], nodes, toKnowledgePath)
    expect(edges).toHaveLength(2)
  })

  it('graphHtmlNode: Name aus dem rohen Titel, Kanten aus bezuege', () => {
    const html = '<title>Mein Konzept</title><script type="application/json" id="rowboat-konzept">{"art":"konzept","bezuege":["[[Marke/Marke]]"]}</script>'
    const node = graphHtmlNode('knowledge/Konzepte/k.html', html)
    expect(node.label).toBe('Mein Konzept')
    const nodes = new Set(['knowledge/Marke/Marke.md', 'knowledge/Konzepte/k.html'])
    const { edges } = buildHierarchyGraph([{ path: 'knowledge/Konzepte/k.html', data: node.data }], nodes, toKnowledgePath)
    expect(edges).toHaveLength(1)
  })

  it('graphHtmlNode: Titel aus Entitaeten im Text zaehlt nicht', () => {
    const node = graphHtmlNode('knowledge/Konzepte/k.html', '<p>&lt;title&gt;X&lt;/title&gt;</p>')
    expect(node.label).toBe('k')
  })

  it('escaped Knotennamen für HTML-Senken', () => {
    expect(escapeHtml(`&<>"'`)).toBe('&amp;&lt;&gt;&quot;&#39;')
    const out = graphLabelHtml('<img src=x onerror=alert(1)>')
    expect(out).toBe('&lt;img src=x onerror=alert(1)&gt;')
    expect(out).not.toContain('<')
  })

  it('erlaubt kein Anlegen von HTML-Zielen', () => {
    expect(canCreateWikiTarget('neu.html')).toBe(false)
    expect(canCreateWikiTarget('knowledge/neu.XHTML')).toBe(false)
    expect(canCreateWikiTarget('neu.md')).toBe(true)
  })

  it('loest Verweise ohne Endung auf', () => {
    const has = (s: string[]) => (p: string) => s.includes(p)
    expect(resolveWikiTarget('K/x', has(['knowledge/K/x.md', 'knowledge/K/x.html']))).toBe('knowledge/K/x.md')
    expect(resolveWikiTarget('K/x|Name', has(['knowledge/K/x.html']))).toBe('knowledge/K/x.html')
    expect(resolveWikiTarget('K/x', has(['knowledge/K/x.xhtml']))).toBe('knowledge/K/x.xhtml')
    expect(resolveWikiTarget('K/x', has([]))).toBe('knowledge/K/x.md')
    expect(resolveWikiTarget('K/x', has(['knowledge/K/x.html', 'knowledge/K/x.htm']))).toBe('knowledge/K/x.md')
    expect(resolveWikiTarget('K/x.md', has(['knowledge/K/x.html']))).toBe('knowledge/K/x.md')
    expect(resolveWikiTarget('K/x.html', has([]))).toBe('knowledge/K/x.html')
  })

  it('listet HTML-Dateien unter knowledge/ ohne Praefix und mit Endung', () => {
    expect(knowledgeHtmlFiles([
      'knowledge/Notiz.md',
      'knowledge/Konzepte/a.html',
      'knowledge/Konzepte/b.XHTML',
      'knowledge/Bild.png',
      'other/c.html',
    ])).toEqual(['Konzepte/a.html', 'Konzepte/b.XHTML'])
  })

  it('wikiLabel behält die HTML-Endung', () => {
    expect(wikiLabel('Konzepte/x.html')).toBe('x.html')
    expect(wikiLabel('Notiz.md')).toBe('Notiz')
  })

  it('loest Konzepte per Pfad ohne Endung und per Dateiname ohne Endung auf (genau ein Treffer)', () => {
    const pfade = ['knowledge/Konzepte/x.html', 'knowledge/Notiz.md']
    const has = (p: string) => pfade.includes(p)
    const namen = namensIndex(pfade)
    expect(resolveWikiTarget('Konzepte/x', has, namen)).toBe('knowledge/Konzepte/x.html')
    expect(resolveWikiTarget('x', has, namen)).toBe('knowledge/Konzepte/x.html')
    expect(resolveWikiTarget('x|Anzeige', has, namen)).toBe('knowledge/Konzepte/x.html')
    expect(resolveWikiTarget('x#Abschnitt', has, namen)).toBe('knowledge/Konzepte/x.html')
    // Groß/Klein wie der Editor: ohne Unterschied
    expect(resolveWikiTarget('X', has, namen)).toBe('knowledge/Konzepte/x.html')
    // ohne Namensindex wie bisher: [[x]] bleibt knowledge/x.md (nicht vorhanden)
    expect(resolveWikiTarget('x', has)).toBe('knowledge/x.md')
    // eine Notiz knowledge/x.md geht vor
    const wurzel = ['knowledge/Konzepte/x.html', 'knowledge/x.md']
    expect(resolveWikiTarget('x', (p) => wurzel.includes(p), namensIndex(wurzel))).toBe('knowledge/x.md')
    // Pfad mit Ordner sucht nicht per Dateiname
    expect(resolveWikiTarget('Anders/x', has, namen)).toBe('knowledge/Anders/x.md')
  })

  it('Kurzform [[x]]: mehrdeutig oder Notiz gleichen Namens -> unaufgeloest (wie bisher)', () => {
    const fall = (pfade: string[]) => resolveWikiTarget('x', (p) => pfade.includes(p), namensIndex(pfade))
    expect(fall(['knowledge/Konzepte/x.html', 'knowledge/Alt/x.htm'])).toBe('knowledge/x.md')
    expect(fall(['knowledge/Konzepte/x.html', 'knowledge/People/x.md'])).toBe('knowledge/x.md')
    // nur Groß/Klein verschieden zählt als derselbe Name
    expect(fall(['knowledge/Konzepte/x.html', 'knowledge/People/X.md'])).toBe('knowledge/x.md')
    // eine Meeting-Notiz gleichen Namens macht es mehrdeutig
    expect(fall(['knowledge/Konzepte/x.html', 'knowledge/Meetings/2026/x.md'])).toBe('knowledge/x.md')
    // eine Datei außerhalb von knowledge/ ist kein Konzept
    expect(konzeptPerName('x', namensIndex(['other/x.html']))).toBeNull()
  })

  it('Namensindex: je Verweis ein Nachschlagen, die Dateiliste wird nur einmal gelesen', () => {
    const pfade = Array.from({ length: 500 }, (_, i) => `knowledge/Notes/n${i}.md`).concat('knowledge/Konzepte/x.html')
    let gelesen = 0
    function* liste() {
      for (const p of pfade) {
        gelesen++
        yield p
      }
    }
    const nodeSet = new Set(pfade)
    const r = graphResolver(nodeSet, liste())
    for (let i = 0; i < 2000; i++) r.body(`fehlt${i}`)
    expect(r.body('x')).toBe('knowledge/Konzepte/x.html')
    expect(gelesen).toBe(pfade.length)
  })

  it('Editor-Klick: Kurzform nur bei eindeutigem Konzept, sonst wie bisher', () => {
    const klick = (dateien: string[], link = 'x') =>
      wikiDateiPfad(link, dateien, namensIndex(dateien.map((d) => `knowledge/${d}`)))
    expect(klick(['Konzepte/x.html', 'Notiz.md'])).toBe('knowledge/Konzepte/x.html')
    expect(klick(['Konzepte/x.html', 'People/X.md'])).toBe('knowledge/People/X.md')
    expect(klick(['Konzepte/x.html', 'Meetings/x.md'])).toBe('knowledge/Meetings/x.md')
    expect(klick(['Konzepte/x.html', 'People/X.md', 'Orgs/X.md'])).toBe('knowledge/x.md')
    expect(klick(['Notiz.md'], 'Neu')).toBe('knowledge/Neu.md')
    expect(klick(['Konzepte/x.html'], 'Konzepte/x')).toBe('knowledge/Konzepte/x.html')
  })
})

describe('Frontmatter loest nie per Name auf', () => {
  const pfade = ['knowledge/Notiz.md', 'knowledge/Konzepte/Marke.html']
  const nodeSet = new Set(pfade)
  const notiz = '---\nlevel: 2\nparent: "[[Marke]]"\nthema: "[[Marke]]"\n---\n# Notiz\n'

  it('parent und Relationen im Frontmatter zeigen nicht auf ein Konzept', () => {
    const r = graphResolver(nodeSet, pfade)
    const { edges, info } = buildHierarchyGraph([{ path: 'knowledge/Notiz.md', data: notiz }], nodeSet, r.body, undefined, r.frontMatter)
    expect(info.get('knowledge/Notiz.md')?.parentId).toBeUndefined()
    expect(edges).toEqual([])
  })

  it('ein Verweis im Rumpf darf es', () => {
    const r = graphResolver(nodeSet, pfade)
    const { edges } = buildHierarchyGraph([{ path: 'knowledge/Notiz.md', data: `${notiz}siehe [[Marke]]\n` }], nodeSet, r.body, undefined, r.frontMatter)
    expect(edges).toEqual([{ source: 'knowledge/Notiz.md', target: 'knowledge/Konzepte/Marke.html', kind: 'link' }])
  })

  it('notizZiele: Frontmatter streng aufgeloest vorn, Rumpf roh', () => {
    const r = graphResolver(nodeSet, pfade)
    expect(notizZiele(`${notiz}siehe [[Marke]]`, r.frontMatter)).toEqual(['Marke'])
    expect(notizZiele('---\nparent: "[[Konzepte/Marke]]"\n---\n', r.frontMatter)).toEqual(['knowledge/Konzepte/Marke.html'])
  })

  it('Konzepte-Graph: Frontmatter-parent per Name ergibt keine Kante', () => {
    const r = graphResolver(nodeSet, pfade)
    const g = buildKonzeptGraph(
      [{ pfad: 'knowledge/Konzepte/Marke.html', html: '<title>Marke</title>' }],
      [{ pfad: 'knowledge/Notiz.md', ziele: notizZiele(notiz, r.frontMatter) }],
      r.body,
      (p) => konzept.isKnowledgeHtmlPath(p),
    )
    expect(g.edges).toEqual([])
  })
})

describe('ohne Konzepte bleibt der Notizen-Graph wie vorher', () => {
  it('Knoten, Kanten und Hierarchie gleich dem alten Aufbau', () => {
    const contents = [
      { path: 'knowledge/People/Anna.md', data: '---\nlevel: 2\nparent: "[[Orgs/Firma]]"\nkennt: "[[People/Ben]]"\n---\nsiehe [[Projekt]] und [[People/Ben|Ben]]' },
      { path: 'knowledge/People/Ben.md', data: '[[Anna]] [[Fehlt]] [[Projekt#Teil]]' },
      { path: 'knowledge/Orgs/Firma.md', data: '---\nlevel: 1\ndomain: Arbeit\n---\n' },
      { path: 'knowledge/Projekt.md', data: '[[People/Anna]]' },
    ]
    const pfade = contents.map((c) => c.path)
    const nodeSet = new Set(pfade)
    const alt = buildHierarchyGraph(contents, nodeSet, (raw) => resolveWikiTarget(raw, (p) => nodeSet.has(p)))
    const r = graphResolver(nodeSet, [...pfade, 'knowledge/Meetings/m.md'])
    const neu = buildHierarchyGraph(contents, nodeSet, r.body, undefined, r.frontMatter)
    const istKonzept = (p: string) => konzept.isKnowledgeHtmlPath(p)
    const notizen = notizGraph(pfade, neu.edges, istKonzept)
    expect(notizen.pfade).toEqual(pfade)
    expect(notizen.kanten).toEqual(alt.edges)
    expect(neu.info).toEqual(alt.info)
    expect(alt.edges.length).toBeGreaterThan(2)
  })
})

describe('buildKonzeptGraph', () => {
  const istKonzept = (p: string) => konzept.isKnowledgeHtmlPath(p)
  const block = (art: string, bezuege: string[]) =>
    `<script type="application/json" id="rowboat-konzept">${JSON.stringify(art === 'videokonzept' ? { art, bezuege, video: {} } : { art, bezuege })}</script>`
  const seite = (titel: string, art: string, bezuege: string[], text = '') =>
    `<title>${titel}</title>${block(art, bezuege)}<p>${text}</p>`
  const A = 'knowledge/Konzepte/a.html'
  const B = 'knowledge/Konzepte/b.html'
  const M = 'knowledge/Marke.md'
  const N = 'knowledge/Notiz.md'
  const tabelle: Record<string, string> = {
    'Konzepte/a': A,
    'Konzepte/b': B,
    Marke: M,
    Notiz: N,
    'Andere': 'knowledge/Andere.md',
  }
  const resolve = (ziel: string) => tabelle[ziel.split('|')[0].trim()] ?? null

  it('Konzept -> Notiz: die Notiz erscheint als notiz', () => {
    const g = buildKonzeptGraph([{ pfad: A, html: seite('Konzept A', 'konzept', ['[[Marke]]']) }], [], resolve, istKonzept)
    expect(g.nodes).toEqual([
      { id: A, label: 'Konzept A', art: 'konzept' },
      { id: M, label: 'Marke', art: 'notiz' },
    ])
    expect(g.edges).toEqual([{ source: A, target: M, kind: 'verweis' }])
  })

  it('Notiz -> Konzept: Kante in Richtung Notiz -> Konzept', () => {
    const g = buildKonzeptGraph(
      [{ pfad: A, html: seite('A', 'konzept', []) }],
      [{ pfad: N, ziele: ['Konzepte/a'] }],
      resolve,
      istKonzept,
    )
    expect(g.nodes.map((n) => [n.id, n.art])).toEqual([[A, 'konzept'], [N, 'notiz']])
    expect(g.edges).toEqual([{ source: N, target: A, kind: 'verweis' }])
  })

  it('zwei Konzepte mit derselben Notiz: beide an einem Notiz-Knoten, keine direkte Kante', () => {
    const g = buildKonzeptGraph(
      [
        { pfad: A, html: seite('A', 'konzept', ['[[Marke]]']) },
        { pfad: B, html: seite('B', 'konzept', [], 'siehe [[Marke]]') },
      ],
      [],
      resolve,
      istKonzept,
    )
    expect(g.nodes.filter((n) => n.art === 'notiz').map((n) => n.id)).toEqual([M])
    expect(g.edges).toEqual([
      { source: A, target: M, kind: 'verweis' },
      { source: B, target: M, kind: 'verweis' },
    ])
  })

  it('Konzept -> Konzept', () => {
    const g = buildKonzeptGraph(
      [
        { pfad: B, html: seite('B', 'konzept', []) },
        { pfad: A, html: seite('A', 'videokonzept', ['[[Konzepte/b]]']) },
      ],
      [],
      resolve,
      istKonzept,
    )
    expect(g.nodes).toEqual([
      { id: A, label: 'A', art: 'videokonzept' },
      { id: B, label: 'B', art: 'konzept' },
    ])
    expect(g.edges).toEqual([{ source: A, target: B, kind: 'verweis' }])
  })

  it('Konzepte verweisen gegenseitig: je Richtung eine Kante', () => {
    const g = buildKonzeptGraph(
      [
        { pfad: A, html: seite('A', 'konzept', ['[[Konzepte/b]]']) },
        { pfad: B, html: seite('B', 'konzept', [], 'zurück zu [[Konzepte/a]]') },
      ],
      [],
      resolve,
      istKonzept,
    )
    expect(g.edges).toEqual([
      { source: A, target: B, kind: 'verweis' },
      { source: B, target: A, kind: 'verweis' },
    ])
  })

  it('Notiz ohne Konzept-Bezug fehlt; Konzept ohne Bezug ist ein einzelner Knoten', () => {
    const g = buildKonzeptGraph(
      [{ pfad: A, html: seite('A', 'konzept', []) }],
      [{ pfad: N, ziele: ['Marke', 'Andere'] }],
      resolve,
      istKonzept,
    )
    expect(g.nodes).toEqual([{ id: A, label: 'A', art: 'konzept' }])
    expect(g.edges).toEqual([])
  })

  it('ignoriert unaufgeloeste Ziele und Selbstbezuege, fasst doppelte Kanten zusammen', () => {
    const g = buildKonzeptGraph(
      [{ pfad: A, html: seite('A', 'konzept', ['[[Gibtsnicht]]', '[[Konzepte/a]]', '[[Marke]]'], '[[Marke|M]] [[Marke]]') }],
      [{ pfad: N, ziele: ['Gibtsnicht', 'Konzepte/a', 'Konzepte/a'] }],
      resolve,
      istKonzept,
    )
    expect(g.edges).toEqual([
      { source: A, target: M, kind: 'verweis' },
      { source: N, target: A, kind: 'verweis' },
    ])
  })

  it('HTML ohne Datenblock -> html, nicht lesbare Datei -> Dateiname', () => {
    const g = buildKonzeptGraph(
      [
        { pfad: A, html: '<title>Nur Seite</title><p>x</p>' },
        { pfad: B, html: '' },
      ],
      [],
      resolve,
      istKonzept,
    )
    expect(g.nodes).toEqual([
      { id: A, label: 'Nur Seite', art: 'html' },
      { id: B, label: 'b', art: 'html' },
    ])
  })

  it('feindliche Titel bleiben reine Zeichenketten', () => {
    const g = buildKonzeptGraph(
      [{ pfad: A, html: '<title>&lt;img src=x onerror=alert(1)&gt;</title>' }],
      [],
      resolve,
      istKonzept,
    )
    expect(g.nodes[0].label).toBe('<img src=x onerror=alert(1)>')
    expect(typeof g.nodes[0].label).toBe('string')
  })
})

describe('Notizen-Graph: Konzepte nur mit Notiz-Bezug', () => {
  const istKonzept = (p: string) => konzept.isKnowledgeHtmlPath(p)
  const A = 'knowledge/Konzepte/a.html'
  const B = 'knowledge/Konzepte/b.html'
  const C = 'knowledge/Konzepte/c.html'
  const N = 'knowledge/Notiz.md'
  const O = 'knowledge/Other.md'

  it('konzepteMitNotizBezug: nur Konzepte mit einer Kante zu einer Notiz (beide Richtungen)', () => {
    const kanten = [
      { source: A, target: N },
      { source: O, target: B },
      { source: A, target: C },
    ]
    expect([...konzepteMitNotizBezug(kanten, istKonzept)].sort()).toEqual([A, B])
  })

  it('notizGraph: Konzept ohne Notiz-Bezug fehlt, Konzept-Konzept-Kanten fehlen, Notizen bleiben', () => {
    const kanten = [
      { source: A, target: N, kind: 'link' as const },
      { source: A, target: C, kind: 'link' as const },
      { source: N, target: O, kind: 'parent' as const },
    ]
    const g = notizGraph([N, O, A, C], kanten, istKonzept)
    expect(g.pfade).toEqual([N, O, A])
    expect(g.kanten).toEqual([kanten[0], kanten[2]])
  })
})
