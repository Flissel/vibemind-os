import { describe, expect, it } from 'vitest'
import { konzept } from '@x/shared'
import { canCreateWikiTarget, escapeHtml, graphHtmlData, graphHtmlNode, graphLabelHtml, graphNodeLabel, knowledgeHtmlFiles, resolveWikiTarget } from './graph-html'
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
})
