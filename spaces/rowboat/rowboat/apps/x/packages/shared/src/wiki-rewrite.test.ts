import { describe, expect, it } from 'vitest'
import { htmlUmschreibBereiche } from './konzept.js'
import {
  isRewritableKnowledgePath,
  rewriteWikiLinksInHtml,
  rewriteWikiLinksInMarkdown,
} from './wiki-rewrite.js'

const BLOCK = (bezuege: string) =>
  `<script type="application/json" id="rowboat-konzept">{"art":"konzept","bezuege":${bezuege}}</script>`

describe('htmlUmschreibBereiche', () => {
  it('liefert Datenblock und Text, nicht Tags, andere Skripte, Styles und Kommentare', () => {
    const html = `<p title="[[A]]">[[T1]]</p><!-- [[K]] --><style>a[[0]]{}</style><script>x[[0]]</script>${BLOCK('["[[B]]"]')}<b>[[T2]]</b>`
    const teile = htmlUmschreibBereiche(html).map((b) => `${b.art}:${html.slice(b.start, b.end)}`)
    expect(teile).toEqual(['text:[[T1]]', 'json:{"art":"konzept","bezuege":["[[B]]"]}', 'text:[[T2]]'])
  })
  it('nimmt nur den ersten Datenblock', () => {
    const html = `${BLOCK('["[[A]]"]')}${BLOCK('["[[B]]"]')}`
    expect(htmlUmschreibBereiche(html).filter((b) => b.art === 'json')).toHaveLength(1)
  })
  it('liest auch hinter 1 MB und bricht bei offenem Konstrukt ab', () => {
    const lang = 'x'.repeat(1_100_000)
    const html = `<p>${lang}</p><p>[[Spaet]]</p><!-- offen [[X]]`
    const teile = htmlUmschreibBereiche(html).map((b) => html.slice(b.start, b.end))
    expect(teile).toContain('[[Spaet]]')
    expect(teile.some((t) => t.includes('[[X]]'))).toBe(false)
  })
})

describe('rewriteWikiLinksInMarkdown', () => {
  it('Notiz umbenannt: Verweis ohne Endung bleibt ohne Endung, Alias und Anker bleiben', () => {
    expect(rewriteWikiLinksInMarkdown('[[Marke/Marke#Abschnitt|Label]] [[marke/marke.md]]', 'Marke/Marke.md', 'Marke/Leitbild.md')).toBe(
      '[[Marke/Leitbild#Abschnitt|Label]] [[Marke/Leitbild.md]]',
    )
  })
  it('Konzept umbenannt: Endung bleibt, Alias bleibt, Gross/Klein egal', () => {
    expect(
      rewriteWikiLinksInMarkdown('[[Konzepte/k.html|das Konzept]] und [[Konzepte/K.HTML]]', 'Konzepte/k.html', 'Konzepte/Neu Ä.html'),
    ).toBe('[[Konzepte/Neu Ä.html|das Konzept]] und [[Konzepte/Neu Ä.html]]')
  })
  it('.htm bleibt .htm im Verweis, Verweis auf html trifft nicht eine Notiz gleichen Namens', () => {
    expect(rewriteWikiLinksInMarkdown('[[k.htm]] [[k.html]]', 'k.htm', 'n.htm')).toBe('[[n.htm]] [[k.html]]')
    expect(rewriteWikiLinksInMarkdown('[[k.html]]', 'k.md', 'n.md')).toBe('[[k.html]]')
  })
  it('Verweis ohne Endung auf ein Konzept: ohne Endung, wenn keine Notiz gleichen Namens da ist', () => {
    expect(rewriteWikiLinksInMarkdown('[[Konzepte/k]]', 'Konzepte/k.html', 'Konzepte/neu.html')).toBe('[[Konzepte/neu]]')
  })
  it('Verweis ohne Endung wird nicht umgeschrieben, wenn eine gleichnamige Notiz existiert', () => {
    const mdExists = (s: string) => s === 'konzepte/k'
    expect(rewriteWikiLinksInMarkdown('[[Konzepte/k]]', 'Konzepte/k.html', 'Konzepte/neu.html', { mdExists })).toBe('[[Konzepte/k]]')
  })
  it('Ziel kollidiert mit einer Notiz: Verweis bekommt die Endung', () => {
    const mdExists = (s: string) => s === 'konzepte/neu'
    expect(rewriteWikiLinksInMarkdown('[[Konzepte/k]]', 'Konzepte/k.html', 'Konzepte/neu.html', { mdExists })).toBe(
      '[[Konzepte/neu.html]]',
    )
  })
  it('knowledge/-Präfix bleibt erhalten', () => {
    expect(rewriteWikiLinksInMarkdown('[[knowledge/Konzepte/k.html]]', 'Konzepte/k.html', 'Konzepte/n.html')).toBe(
      '[[knowledge/Konzepte/n.html]]',
    )
  })
  it('Bare-Name nur mit allowBareSelfNameMatch', () => {
    expect(rewriteWikiLinksInMarkdown('[[k.html]] [[k]]', 'a/k.html', 'a/n.html')).toBe('[[k.html]] [[k]]')
    expect(rewriteWikiLinksInMarkdown('[[k.html]] [[k]]', 'a/k.html', 'a/n.html', { allowBareSelfNameMatch: true })).toBe(
      '[[n.html]] [[n]]',
    )
  })
})

describe('rewriteWikiLinksInHtml', () => {
  it('zieht Verweise im Datenblock nach und lässt das JSON gültig, auch bei Anführungszeichen', () => {
    const html = `<html>${BLOCK('["[[Marke/Marke]]","[[Marke/Marke|Alias]]"]')}</html>`
    const neu = rewriteWikiLinksInHtml(html, 'Marke/Marke.md', 'Marke/Neu "Ä" \\ x.md')
    const roh = /<script[^>]*>([\s\S]*?)<\/script>/.exec(neu)![1]
    expect(JSON.parse(roh)).toEqual({ art: 'konzept', bezuege: ['[[Marke/Neu "Ä" \\ x]]', '[[Marke/Neu "Ä" \\ x|Alias]]'] })
  })
  it('lässt Skriptcode, Styles, Kommentare und Attribute unberührt', () => {
    const html = `<p title="[[Marke/Marke]]">[[Marke/Marke]]</p><!-- [[Marke/Marke]] --><style>a[[Marke/Marke]]{}</style><script>var a=b[[Marke/Marke]]</script>`
    const neu = rewriteWikiLinksInHtml(html, 'Marke/Marke.md', 'Marke/Neu.md')
    expect(neu).toBe(html.replace('>[[Marke/Marke]]</p>', '>[[Marke/Neu]]</p>'))
  })
  it('sichtbarer Text: Entitäten werden erkannt und beim Schreiben maskiert', () => {
    const neu = rewriteWikiLinksInHtml('<p>[[A &amp; B]]</p>', 'A & B.md', 'C < D.md')
    expect(neu).toBe('<p>[[C &lt; D]]</p>')
  })
  it('ungültiger Block und fehlender Block stürzen nicht ab', () => {
    const kaputt = '<script type="application/json" id="rowboat-konzept">{ "bezuege": ["[[Marke/Marke]]" </script><p>x</p>'
    expect(rewriteWikiLinksInHtml(kaputt, 'Marke/Marke.md', 'Marke/N.md')).toContain('[[Marke/N]]')
    expect(rewriteWikiLinksInHtml('<p>nichts</p>', 'a.md', 'b.md')).toBe('<p>nichts</p>')
  })
  it('Konzept benennt sich selbst um: Verweise auf sich ziehen mit', () => {
    const html = `<p>[[Konzepte/k.html]]</p>`
    expect(rewriteWikiLinksInHtml(html, 'Konzepte/k.html', 'Konzepte/n.html')).toBe('<p>[[Konzepte/n.html]]</p>')
  })
})

describe('rewriteWikiLinksInHtml: Datenblock nur in JSON-Zeichenketten', () => {
  it('verschachtelte Arrays wie [[null]] und [[0]] sind kein Verweis', () => {
    const html = `<script type="application/json" id="rowboat-konzept">{"art":"konzept","x":[[null]],"y":[[0],[1,2]],"bezuege":["[[null]]"]}</script>`
    const neu = rewriteWikiLinksInHtml(html, 'null.md', 'neu.md')
    expect(neu).toBe(html.replace('"bezuege":["[[null]]"]', '"bezuege":["[[neu]]"]'))
    expect(() => JSON.parse(/>([\s\S]*)<\/script>/.exec(neu)![1])).not.toThrow()
  })
  it('<, > und & im neuen Namen werden als Unicode-Escapes geschrieben, unberührte Teile bleiben', () => {
    const html = `<script type="application/json" id="rowboat-konzept">{"art":"konzept","bezuege":["[[Marke/Marke|A & B <x>]]","a & <b>"]}</script>`
    const neu = rewriteWikiLinksInHtml(html, 'Marke/Marke.md', 'Marke/A & B <neu>.md')
    expect(neu).toContain('"[[Marke/A \\u0026 B \\u003cneu\\u003e|A & B <x>]]"')
    expect(neu).toContain('"a & <b>"')
    expect(JSON.parse(/>([\s\S]*)<\/script>/.exec(neu)![1]).bezuege[0]).toBe('[[Marke/A & B <neu>|A & B <x>]]')
  })
})

describe('rewriteWikiLinksInHtml: sichtbarer Text', () => {
  it('nur das Ziel wird entschlüsselt, Alias und Anker bleiben Byte für Byte', () => {
    const html = '<p>[[a#h&nbsp;1|x&nbsp;y &amp; &#228;]]</p>'
    expect(rewriteWikiLinksInHtml(html, 'a.md', 'b.md')).toBe('<p>[[b#h&nbsp;1|x&nbsp;y &amp; &#228;]]</p>')
  })
})

describe('rewriteWikiLinksInHtml: Entitäten, Schlüssel, Laufzeit', () => {
  it('Ziele mit numerischen Entitäten (&#x27;, &#252;) werden gefunden, Alias bleibt Byte für Byte', () => {
    expect(rewriteWikiLinksInHtml("<p>[[Felix&#x27;s Plan]] [[Gr&#252;&#223;e|x&#228;]] [[a#h&#228;]]</p>", "Felix's Plan.md", 'Neu.md')).toBe(
      "<p>[[Neu]] [[Gr&#252;&#223;e|x&#228;]] [[a#h&#228;]]</p>",
    )
    expect(rewriteWikiLinksInHtml('<p>[[Gr&#252;&#223;e|x&#228;]] [[Gr&#252;&#223;e#h]]</p>', 'Grüße.md', 'Neu.md')).toBe(
      '<p>[[Neu|x&#228;]] [[Neu#h]]</p>',
    )
  })
  it('Objektschlüssel im Datenblock bleiben, Werte (auch verschachtelt) werden umgeschrieben', () => {
    const html = '<script type="application/json" id="rowboat-konzept">{"[[x]]" : 1, "art":"konzept","bezuege":["[[x]]"],"video":{"thema":"[[x]]","[[x]]":"[[x]]"}}</script>'
    const neu = rewriteWikiLinksInHtml(html, 'x.md', 'y.md')
    expect(neu).toBe(html.replace('"bezuege":["[[x]]"]', '"bezuege":["[[y]]"]').replace('"thema":"[[x]]"', '"thema":"[[y]]"').replace('"[[x]]":"[[x]]"', '"[[x]]":"[[y]]"'))
    expect(() => JSON.parse(/>([\s\S]*)<\/script>/.exec(neu)![1])).not.toThrow()
  })
  it('Block über 20 000 Zeichen wird nicht angefasst', () => {
    const html = `<script type="application/json" id="rowboat-konzept">{"a":"${'x'.repeat(20_000)}","bezuege":["[[x]]"]}</script>`
    expect(rewriteWikiLinksInHtml(html, 'x.md', 'y.md')).toBe(html)
  })
  it('kaputter Block bleibt linear: offene Anführungszeichen, viele maskierte Anführungszeichen', () => {
    for (const block of [`{"a":"${'\\"'.repeat(80_000)}`, '"'.repeat(200_000), `"[[x]]" "${'\\"'.repeat(8_000)}`]) {
      const html = `<script type="application/json" id="rowboat-konzept">${block}</script><p>[[x]]</p>`
      const t = Date.now()
      const neu = rewriteWikiLinksInHtml(html, 'x.md', 'y.md')
      expect(Date.now() - t).toBeLessThan(1000)
      expect(neu).toContain('<p>[[y]]</p>')
    }
  })
})

describe('Verweis ohne Endung neben gleichnamigen HTML-Dateien', () => {
  it('schreibt die Endung, wenn nach dem Umbenennen eine zweite HTML-Datei denselben Stamm trägt', () => {
    const htmlStemCount = (stem: string) => (stem === 'k/neu' ? 2 : 0)
    expect(rewriteWikiLinksInMarkdown('[[k/alt]]', 'k/alt.html', 'k/neu.html', { htmlStemCount })).toBe('[[k/neu.html]]')
  })
  it('lässt einen Verweis ohne Endung in Ruhe, der schon vorher zwischen mehreren HTML-Dateien mehrdeutig war', () => {
    const htmlStemCount = (stem: string) => (stem === 'k/alt' ? 1 : stem === 'k/neu' ? 1 : 0)
    expect(rewriteWikiLinksInMarkdown('[[k/alt]]', 'k/alt.html', 'k/neu.html', { htmlStemCount })).toBe('[[k/alt]]')
  })
})

describe('rewriteWikiLinksInMarkdown: Verhalten für Notizen (Charakterisierung)', () => {
  const r = (md: string, from: string, to: string, opts?: Parameters<typeof rewriteWikiLinksInMarkdown>[3]) =>
    rewriteWikiLinksInMarkdown(md, from, to, opts)
  it('Alias, Anker und beides', () => {
    expect(r('[[a|b]] [[a#h]] [[a#h|b]]', 'a.md', 'c.md')).toBe('[[c|b]] [[c#h]] [[c#h|b]]')
  })
  it('Leerraum in den Klammern bleibt', () => {
    expect(r('[[ a | b ]] [[ a ]]', 'a.md', 'c.md')).toBe('[[ c | b ]] [[ c ]]')
  })
  it('knowledge/-Präfix und explizites .md bleiben, ohne Endung bleibt ohne Endung', () => {
    expect(r('[[knowledge/a]] [[knowledge/a.md]] [[a.md]] [[a]]', 'a.md', 'c.md')).toBe('[[knowledge/c]] [[knowledge/c.md]] [[c.md]] [[c]]')
  })
  it('Verweis nur mit dem Namen: nur in der umbenannten Datei selbst', () => {
    expect(r('[[a]] [[x/a]]', 'x/a.md', 'x/c.md')).toBe('[[a]] [[x/c]]')
    expect(r('[[a]] [[x/a]]', 'x/a.md', 'x/c.md', { allowBareSelfNameMatch: true })).toBe('[[c]] [[x/c]]')
  })
  it('Gross/Klein egal, auch bei explizitem .MD', () => {
    expect(r('[[X/A]] [[x/a.MD]]', 'x/a.md', 'x/c.md')).toBe('[[x/c]] [[x/c.md]]')
  })
  it('nur der Ordner ändert sich', () => {
    expect(r('[[a/n]] [[a/n.md|N]]', 'a/n.md', 'b/n.md')).toBe('[[b/n]] [[b/n.md|N]]')
  })
  it('nur Gross/Klein ändert sich (Kern tut dann nichts, die Funktion schreibt den neuen Namen)', () => {
    expect(r('[[a]]', 'a.md', 'A.md')).toBe('[[A]]')
  })
  it('Namen mit Zeichen, die in regulären Ausdrücken Sonderbedeutung haben', () => {
    expect(r('[[a+b (1).md]] [[aXb]] [[a.b]]', 'a+b (1).md', 'c.md')).toBe('[[c.md]] [[aXb]] [[a.b]]')
    expect(r('[[aXb]] [[a.b]]', 'a.b.md', 'c.md')).toBe('[[aXb]] [[c]]')
  })
  it('bekannte Abweichung 1: Datei aus der Wurzel in einen Ordner, Verweis auf sich bekommt den Ordner', () => {
    expect(r('[[a]] [[a.md]]', 'a.md', 'Ordner/b.md', { allowBareSelfNameMatch: true })).toBe('[[Ordner/b]] [[Ordner/b.md]]')
  })
  it('bekannte Abweichung 2: Notiz k.html.md, [[k.html]] meint jetzt die HTML-Datei, [[k.html.md]] wird umgeschrieben', () => {
    expect(r('[[k.html]] [[k.html.md]]', 'k.html.md', 'n.md')).toBe('[[k.html]] [[n.md]]')
  })
})

describe('isRewritableKnowledgePath', () => {
  it('Notiz oder HTML unter knowledge/', () => {
    expect(isRewritableKnowledgePath('knowledge/a.md')).toBe(true)
    expect(isRewritableKnowledgePath('knowledge/Konzepte/a.HTML')).toBe(true)
    expect(isRewritableKnowledgePath('knowledge/a.htm')).toBe(true)
    expect(isRewritableKnowledgePath('other/a.html')).toBe(false)
    expect(isRewritableKnowledgePath('knowledge/a.txt')).toBe(false)
    expect(isRewritableKnowledgePath('knowledge/../a.html')).toBe(false)
  })
})
