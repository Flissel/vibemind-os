import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import ansichtQuelltext from './html-file-viewer.tsx?raw'
import { FileViewerSourceContext, type FileViewerSource } from './file-viewer-source'
import { HtmlFileViewer } from './html-file-viewer'

afterEach(() => {
  cleanup()
  mitGast(undefined)
})

/** Setzt das Kennzeichen des Hauptprozesses (Vorschalt-Skript): `true` = Gastbereich eingerichtet. */
function mitGast(flag: boolean | undefined) {
  window.featureFlags = { spaces: false, konzeptGast: flag }
}

const SEITE = '<!doctype html><title>Mein Konzept</title><p>Hallo</p>'

function quelle(size = SEITE.length): FileViewerSource {
  const nie = () => Promise.reject(new Error('nicht erwartet'))
  return {
    url: (path) => `app://workspace/${path.split('/').map(encodeURIComponent).join('/')}`,
    stat: async () => ({ kind: 'file', size, mtimeMs: 0, ctimeMs: 0 }) as unknown as Awaited<ReturnType<FileViewerSource['stat']>>,
    read: async () => ({ data: SEITE }) as unknown as Awaited<ReturnType<FileViewerSource['read']>>,
    write: nie,
    open: nie,
    exportCopy: nie,
    loadSheet: nie,
    findCells: nie,
    subscribe: () => () => {},
    workspace: true,
  }
}

function zeige(path: string, size?: number) {
  return render(
    <FileViewerSourceContext.Provider value={quelle(size)}>
      <HtmlFileViewer path={path} />
    </FileViewerSourceContext.Provider>,
  )
}

it('Wissens-HTML: Gast mit Adresse und Partition statt Rahmen, Kopfzeile ohne "In neuem Fenster öffnen"', async () => {
  mitGast(true)
  const { container } = zeige('knowledge/Konzepte/k.html')
  const gast = await waitFor(() => {
    const el = container.querySelector('webview')
    if (!el) throw new Error('kein Gast')
    return el
  })
  // Genau diese Attribute und nichts sonst: Adresse, Partition (nicht persistent) und Größe.
  expect(gast.getAttributeNames().sort()).toEqual(['partition', 'src', 'style'])
  expect(gast.getAttribute('src')).toBe('app://workspace/knowledge/Konzepte/k.html?v=0')
  expect(gast.getAttribute('partition')).toBe('konzept')
  expect(container.querySelector('iframe')).toBeNull()
  expect(await screen.findByText('Mein Konzept')).toBeInTheDocument()
  expect(screen.getByText('Im Graphen zeigen')).toBeInTheDocument()
  expect(screen.queryByText('In neuem Fenster öffnen')).not.toBeInTheDocument()
})

it('ohne Gastbereich: Hinweis statt Seite, weder Gast noch Rahmen, Kopfzeile bleibt', async () => {
  mitGast(undefined)
  const { container } = zeige('knowledge/Konzepte/k.html')
  expect(await screen.findByText('Konzepte lassen sich hier nicht anzeigen.')).toBeInTheDocument()
  expect(await screen.findByText('Mein Konzept')).toBeInTheDocument()
  expect(container.querySelector('webview')).toBeNull()
  expect(container.querySelector('iframe')).toBeNull()
})

it('Gast beendet: Element weg, Hinweis, "Neu laden" hängt einen neuen Gast mit ?v=1 ein', async () => {
  mitGast(true)
  const { container } = zeige('knowledge/Konzepte/k.html')
  const gast = await waitFor(() => {
    const el = container.querySelector('webview')
    if (!el) throw new Error('kein Gast')
    return el
  })
  act(() => {
    gast.dispatchEvent(new Event('render-process-gone'))
  })
  expect(await screen.findByText('Dieses Konzept wurde angehalten.')).toBeInTheDocument()
  expect(container.querySelector('webview')).toBeNull()
  fireEvent.click(screen.getByText('Neu laden'))
  const neu = await waitFor(() => {
    const el = container.querySelector('webview')
    if (!el) throw new Error('kein Gast')
    return el
  })
  expect(neu.getAttribute('src')).toBe('app://workspace/knowledge/Konzepte/k.html?v=1')
  expect(neu).not.toBe(gast)
})

function holeGast(container: HTMLElement) {
  return waitFor(() => {
    const el = container.querySelector('webview')
    if (!el) throw new Error('kein Gast')
    return el
  })
}

function sende(gast: Element, channel: string, args: unknown[]) {
  const ev = Object.assign(new Event('ipc-message'), { channel, args })
  act(() => {
    gast.dispatchEvent(ev)
  })
}

const LINK = { type: 'rowboat:link-oeffnen', url: 'https://a.example/x' }

it('Gast ohne dom-ready nach 5 s: Hinweis "nicht anzeigen" mit "Neu laden"', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  try {
    mitGast(true)
    const { container } = zeige('knowledge/Konzepte/k.html')
    await holeGast(container)
    act(() => {
      vi.advanceTimersByTime(4900)
    })
    expect(container.querySelector('webview')).not.toBeNull()
    act(() => {
      vi.advanceTimersByTime(200)
    })
    expect(await screen.findByText('Konzepte lassen sich hier nicht anzeigen.')).toBeInTheDocument()
    expect(screen.getByText('Neu laden')).toBeInTheDocument()
    expect(container.querySelector('webview')).toBeNull()
  } finally {
    vi.useRealTimers()
  }
})

it('Zeitüberschreitung setzt eine offene Bestätigung zurück', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  try {
    mitGast(true)
    const { container } = zeige('knowledge/Konzepte/k.html')
    const gast = await holeGast(container)
    sende(gast, 'konzept', [LINK])
    expect(await screen.findByText('Link öffnen?')).toBeInTheDocument()
    act(() => {
      vi.advanceTimersByTime(5100)
    })
    expect(await screen.findByText('Konzepte lassen sich hier nicht anzeigen.')).toBeInTheDocument()
    expect(screen.queryByText('Link öffnen?')).not.toBeInTheDocument()
  } finally {
    vi.useRealTimers()
  }
})

it('dom-ready vor Ablauf der Frist: kein Hinweis', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  try {
    mitGast(true)
    const { container } = zeige('knowledge/Konzepte/k.html')
    const gast = await holeGast(container)
    act(() => {
      gast.dispatchEvent(new Event('dom-ready'))
    })
    act(() => {
      vi.advanceTimersByTime(6000)
    })
    expect(container.querySelector('webview')).not.toBeNull()
    expect(screen.queryByText('Konzepte lassen sich hier nicht anzeigen.')).not.toBeInTheDocument()
  } finally {
    vi.useRealTimers()
  }
})

it('close und destroyed führen wie render-process-gone zu "angehalten"', async () => {
  for (const name of ['close', 'destroyed']) {
    mitGast(true)
    const { container } = zeige('knowledge/Konzepte/k.html')
    const gast = await holeGast(container)
    sende(gast, 'konzept', [LINK])
    expect(await screen.findByText('Link öffnen?')).toBeInTheDocument()
    act(() => {
      gast.dispatchEvent(new Event(name))
    })
    expect(await screen.findByText('Dieses Konzept wurde angehalten.'), name).toBeInTheDocument()
    expect(screen.queryByText('Link öffnen?'), name).not.toBeInTheDocument()
    expect(container.querySelector('webview'), name).toBeNull()
    cleanup()
  }
})

it('eine Nachricht eines alten Elements nach dem Neu-Einhängen wird ignoriert', async () => {
  mitGast(true)
  const { container } = zeige('knowledge/Konzepte/k.html')
  const alt = await holeGast(container)
  act(() => {
    alt.dispatchEvent(new Event('close'))
  })
  fireEvent.click(await screen.findByText('Neu laden'))
  const neu = await waitFor(() => {
    const el = container.querySelector('webview')
    if (!el || el === alt) throw new Error('noch kein neuer Gast')
    return el
  })
  sende(alt, 'konzept', [LINK])
  expect(screen.queryByText('Link öffnen?')).not.toBeInTheDocument()
  sende(neu, 'konzept', [LINK])
  expect(await screen.findByText('Link öffnen?')).toBeInTheDocument()
})

it('Pfad mit großem "Knowledge/": kein Gast, Hinweis (fail-closed)', async () => {
  mitGast(true)
  const { container } = zeige('Knowledge/Konzepte/k.html')
  // Das Gate der Ansicht (isKnowledgeHtmlPath) lässt den Pfad durch, eine Gast-Adresse gibt es nicht.
  expect(await screen.findByText('Konzepte lassen sich hier nicht anzeigen.')).toBeInTheDocument()
  expect(container.querySelector('webview')).toBeNull()
  expect(container.querySelector('iframe')).toBeNull()
})


it('ipc-message: nur Kanal "konzept" mit genau einem gültigen Argument zeigt die Bestätigung', async () => {
  mitGast(true)
  const { container } = zeige('knowledge/Konzepte/k.html')
  const gast = await waitFor(() => {
    const el = container.querySelector('webview')
    if (!el) throw new Error('kein Gast')
    return el
  })
  const link = { type: 'rowboat:link-oeffnen', url: 'https://a.example/x' }
  sende(gast, 'anderer', [link])
  sende(gast, 'konzept', [link, link])
  sende(gast, 'konzept', [])
  sende(gast, 'konzept', [{ type: 'rowboat:link-oeffnen', url: 42 }])
  sende(gast, 'konzept', [{ type: 'unbekannt', url: 'https://a.example/x' }])
  expect(screen.queryByText('Link öffnen?')).not.toBeInTheDocument()
  sende(gast, 'konzept', [link])
  expect(await screen.findByText('Link öffnen?')).toBeInTheDocument()
  expect(screen.getByText('a.example')).toBeInTheDocument()
})

it('verborgener Betrachter hat keinen Listener und setzt die Bestätigung zurück', async () => {
  mitGast(true)
  const { container, rerender } = zeige('knowledge/Konzepte/k.html')
  const gast = await waitFor(() => {
    const el = container.querySelector('webview')
    if (!el) throw new Error('kein Gast')
    return el
  })
  sende(gast, 'konzept', [{ type: 'rowboat:link-oeffnen', url: 'https://a.example/x' }])
  expect(await screen.findByText('Link öffnen?')).toBeInTheDocument()
  rerender(
    <FileViewerSourceContext.Provider value={quelle()}>
      <HtmlFileViewer path="knowledge/Konzepte/k.html" active={false} />
    </FileViewerSourceContext.Provider>,
  )
  await waitFor(() => expect(screen.queryByText('Link öffnen?')).not.toBeInTheDocument())
  sende(gast, 'konzept', [{ type: 'rowboat:link-oeffnen', url: 'https://b.example/x' }])
  expect(screen.queryByText('Link öffnen?')).not.toBeInTheDocument()
})

it('Quelltext der neuen Ansicht: kein Rahmen und keine erweiterten Gast-Rechte', () => {
  for (const verboten of ['<iframe', 'allowpopups', 'nodeintegration', 'preload=', 'webpreferences=', 'sandbox=']) {
    expect(ansichtQuelltext.includes(verboten), verboten).toBe(false)
  }
})

it('Hintergrund-Bericht außerhalb von knowledge/: alte Ansicht wie vorher (R38)', async () => {
  zeige('bg-tasks/x/index.html')
  const rahmen = await screen.findByTitle('HTML preview')
  expect(rahmen.getAttribute('sandbox')).toBe('allow-scripts allow-popups')
  expect(screen.queryByText('Im Graphen zeigen')).not.toBeInTheDocument()
})

it('zu große Wissens-HTML: nur der Größenhinweis, kein "Open in system" (R50); alte Ansicht behält den Knopf', async () => {
  const gross = 6 * 1024 * 1024
  zeige('knowledge/Konzepte/riesig.html', gross)
  expect(await screen.findByText('File too large to preview')).toBeInTheDocument()
  expect(screen.queryByText('Open in system')).not.toBeInTheDocument()
  expect(screen.queryByTitle('HTML preview')).not.toBeInTheDocument()
  cleanup()
  zeige('bg-tasks/x/index.html', gross)
  expect(await screen.findByText('File too large to preview')).toBeInTheDocument()
  expect(screen.getByText('Open in system')).toBeInTheDocument()
})
