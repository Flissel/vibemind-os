import { describe, expect, it } from 'vitest'
import { istVertrauterMcpName, vertrauterAufrufPasst, vertrautesMcpWerkzeug } from './vertraute-mcp-werkzeuge.js'

const SERVER = 'rowboat-auftraege'
const WERKZEUG = 'video_auftrag_aus_konzept'
const URL_OK = 'http://127.0.0.1:8156/mcp'

describe('vertrautesMcpWerkzeug', () => {
  it('genau der eingetragene Server, das Werkzeug und die URL: vertraut', () => {
    expect(vertrautesMcpWerkzeug({ serverName: SERVER, toolName: WERKZEUG, serverUrl: URL_OK })).toBe(true)
  })

  it.each([
    ['anderer Servername', { serverName: 'rowboat-auftraege2', toolName: WERKZEUG, serverUrl: URL_OK }],
    ['Servername mit Großbuchstaben', { serverName: 'Rowboat-Auftraege', toolName: WERKZEUG, serverUrl: URL_OK }],
    ['Servername mit Leerzeichen', { serverName: ` ${SERVER}`, toolName: WERKZEUG, serverUrl: URL_OK }],
    ['anderes Werkzeug', { serverName: SERVER, toolName: 'video_auftrag_loeschen', serverUrl: URL_OK }],
    ['Werkzeug mit Großbuchstaben', { serverName: SERVER, toolName: 'Video_Auftrag_Aus_Konzept', serverUrl: URL_OK }],
    ['Werkzeug mit Anhang', { serverName: SERVER, toolName: `${WERKZEUG}_x`, serverUrl: URL_OK }],
  ])('%s: nicht vertraut', (_name, n) => {
    expect(vertrautesMcpWerkzeug(n)).toBe(false)
  })

  it.each([
    ['localhost', 'http://localhost:8156/mcp'],
    ['127.0.0.2', 'http://127.0.0.2:8156/mcp'],
    ['Domain', 'http://auftraege.example.com:8156/mcp'],
    ['Domain mit 127.0.0.1 als Teil', 'http://127.0.0.1.example.com:8156/mcp'],
    ['IPv6-Loopback', 'http://[::1]:8156/mcp'],
    ['anderer Port', 'http://127.0.0.1:8157/mcp'],
    ['ohne Port', 'http://127.0.0.1/mcp'],
    ['https', 'https://127.0.0.1:8156/mcp'],
    ['ws', 'ws://127.0.0.1:8156/mcp'],
    ['Pfad /mcp/', 'http://127.0.0.1:8156/mcp/'],
    ['Pfad /mcp2', 'http://127.0.0.1:8156/mcp2'],
    ['Pfad /MCP', 'http://127.0.0.1:8156/MCP'],
    ['Pfad /', 'http://127.0.0.1:8156/'],
    ['Pfad /x/mcp', 'http://127.0.0.1:8156/x/mcp'],
    ['Benutzer', 'http://nutzer@127.0.0.1:8156/mcp'],
    ['Benutzer und Passwort', 'http://nutzer:geheim@127.0.0.1:8156/mcp'],
    ['Query', 'http://127.0.0.1:8156/mcp?a=1'],
    ['leere Query', 'http://127.0.0.1:8156/mcp?'],
    ['Fragment', 'http://127.0.0.1:8156/mcp#x'],
    ['leeres Fragment', 'http://127.0.0.1:8156/mcp#'],
    ['leerer Text', ''],
    ['Unsinn', 'kein url'],
    ['nur Host', '127.0.0.1:8156/mcp'],
  ])('URL %s: nicht vertraut', (_name, serverUrl) => {
    expect(vertrautesMcpWerkzeug({ serverName: SERVER, toolName: WERKZEUG, serverUrl })).toBe(false)
  })

  // E1: absichtlich vertraut. Der Transport öffnet `new URL(config.url)` mit demselben WHATWG-Parser, jede
  // dieser Schreibweisen erreicht also genau http://127.0.0.1:8156/mcp. Kein roher Textvergleich.
  it.each([
    ['Schema groß', 'HTTP://127.0.0.1:8156/mcp'],
    ['Port mit führender Null', 'http://127.0.0.1:08156/mcp'],
    ['kurze IPv4-Form', 'http://127.1:8156/mcp'],
    ['IPv4 als Zahl', 'http://2130706433:8156/mcp'],
    ['IPv4 hexadezimal', 'http://0x7f.0.0.1:8156/mcp'],
    ['Punkt am Hostende', 'http://127.0.0.1.:8156/mcp'],
    ['Punkt-Segment', 'http://127.0.0.1:8156/./mcp'],
    ['Punkt-Punkt-Segment', 'http://127.0.0.1:8156/x/../mcp'],
    ['leere Zugangsdaten', 'http://@127.0.0.1:8156/mcp'],
    ['leere Zugangsdaten mit Doppelpunkt', 'http://:@127.0.0.1:8156/mcp'],
    ['Leerraum außen', '  http://127.0.0.1:8156/mcp  '],
  ])('normalisierte Schreibweise %s: vertraut (gleiche Gegenstelle)', (_name, serverUrl) => {
    expect(new URL(serverUrl).href).toBe(URL_OK)
    expect(vertrautesMcpWerkzeug({ serverName: SERVER, toolName: WERKZEUG, serverUrl })).toBe(true)
  })

  it.each([
    ['%-kodierter Pfad', 'http://127.0.0.1:8156/%6Dcp'],
    ['IPv4-gemappte IPv6', 'http://[::ffff:127.0.0.1]:8156/mcp'],
    ['0.0.0.0', 'http://0.0.0.0:8156/mcp'],
    ['Pfad mit Semikolon', 'http://127.0.0.1:8156/mcp;x'],
    ['Port 80 (Standardport fällt weg)', 'http://127.0.0.1:80/mcp'],
  ])('weitere URL %s: nicht vertraut', (_name, serverUrl) => {
    expect(vertrautesMcpWerkzeug({ serverName: SERVER, toolName: WERKZEUG, serverUrl })).toBe(false)
  })

  // E2: Unicode-Doppelgänger und unsichtbare Zeichen in den Namen.
  it.each([
    ['kyrillisches а im Servernamen', { serverName: 'rowboat-auftrаege', toolName: WERKZEUG }],
    ['Nullbreite im Servernamen', { serverName: `rowboat-​auftraege`, toolName: WERKZEUG }],
    ['Nullbreite am Werkzeugende', { serverName: SERVER, toolName: `${WERKZEUG}​` }],
    ['kyrillisches о im Werkzeug', { serverName: SERVER, toolName: 'video_auftrag_aus_kоnzept' }],
    ['NBSP am Servernamen', { serverName: `${SERVER} `, toolName: WERKZEUG }],
  ])('%s: nicht vertraut', (_name, n) => {
    expect(vertrautesMcpWerkzeug({ ...n, serverUrl: URL_OK })).toBe(false)
    expect(istVertrauterMcpName(n.serverName, n.toolName)).toBe(false)
  })

  it('ohne URL (stdio-Server oder kein Eintrag): nicht vertraut', () => {
    expect(vertrautesMcpWerkzeug({ serverName: SERVER, toolName: WERKZEUG, serverUrl: null })).toBe(false)
  })
})

describe('vertrauterAufrufPasst (Prüfung der Ausführung direkt vor dem Verbinden)', () => {
  const aufruf = { serverName: SERVER, toolName: WERKZEUG, url: URL_OK }

  it('unveränderter Eintrag: passt', () => {
    expect(vertrauterAufrufPasst(aufruf, { serverName: SERVER, toolName: WERKZEUG, serverUrl: URL_OK })).toBe(true)
    // Andere Schreibweise derselben Adresse: dieselbe Gegenstelle.
    expect(
      vertrauterAufrufPasst(aufruf, { serverName: SERVER, toolName: WERKZEUG, serverUrl: 'HTTP://127.1:8156/mcp' }),
    ).toBe(true)
  })

  it('Eintrag inzwischen auf andere Adresse oder stdio: passt nicht', () => {
    for (const serverUrl of ['http://127.0.0.1:9999/mcp', 'http://evil.example.com/mcp', null]) {
      expect(vertrauterAufrufPasst(aufruf, { serverName: SERVER, toolName: WERKZEUG, serverUrl }), String(serverUrl)).toBe(
        false,
      )
    }
  })

  it('anderer Server oder anderes Werkzeug als geprüft: passt nicht', () => {
    expect(vertrauterAufrufPasst(aufruf, { serverName: 'andere', toolName: WERKZEUG, serverUrl: URL_OK })).toBe(false)
    expect(vertrauterAufrufPasst(aufruf, { serverName: SERVER, toolName: 'anderes', serverUrl: URL_OK })).toBe(false)
  })

  it('geprüfte Adresse war selbst nicht vertraut (gefälschte Markierung): passt nicht', () => {
    expect(
      vertrauterAufrufPasst(
        { ...aufruf, url: 'http://evil.example.com/mcp' },
        { serverName: SERVER, toolName: WERKZEUG, serverUrl: URL_OK },
      ),
    ).toBe(false)
  })
})

describe('istVertrauterMcpName', () => {
  it('nur das eingetragene Paar aus Servername und Werkzeug', () => {
    expect(istVertrauterMcpName(SERVER, WERKZEUG)).toBe(true)
    expect(istVertrauterMcpName(SERVER, 'anderes')).toBe(false)
    expect(istVertrauterMcpName('anderer', WERKZEUG)).toBe(false)
    expect(istVertrauterMcpName(WERKZEUG, SERVER)).toBe(false)
  })
})
