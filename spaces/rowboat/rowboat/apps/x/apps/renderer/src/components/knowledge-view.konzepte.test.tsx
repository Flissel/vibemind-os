import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { KnowledgeView, type KnowledgeViewActions } from './knowledge-view'

type Knoten = { path: string; name: string; kind: 'file' | 'dir'; children?: Knoten[]; stat?: { size: number; mtimeMs: number } }

const datei = (path: string, mtimeMs = 1): Knoten => ({ path, name: path.split('/').pop() ?? path, kind: 'file', stat: { size: 1, mtimeMs } })
const baum: Knoten[] = [
  {
    path: 'knowledge/Konzepte',
    name: 'Konzepte',
    kind: 'dir',
    children: [datei('knowledge/Konzepte/Sommer.html', 3), datei('knowledge/Konzepte/Notiz zum Sommer.md', 2)],
  },
  { path: 'knowledge/Notes', name: 'Notes', kind: 'dir', children: [datei('knowledge/Notes/n.md')] },
]

const aktionen: KnowledgeViewActions = {
  createNote: vi.fn(),
  addGoogleDoc: vi.fn(),
  createFolder: vi.fn(async () => 'knowledge/Neu'),
  rename: vi.fn(async () => {}),
  remove: vi.fn(async () => {}),
  copyPath: vi.fn(),
  revealInFileManager: vi.fn(),
}

function zeige(props: { folderPath?: string | null; onOpenNote?: (p: string) => void; onNewKonzept?: (t: string) => void }) {
  return render(
    <KnowledgeView
      tree={baum}
      actions={aktionen}
      mode="files"
      onModeChange={vi.fn()}
      graphContent={null}
      basisContent={null}
      folderPath={props.folderPath ?? null}
      onNavigateFolder={vi.fn()}
      onOpenNote={props.onOpenNote ?? vi.fn()}
      onOpenSearch={vi.fn()}
      onNewKonzept={props.onNewKonzept ?? vi.fn()}
    />,
  )
}

beforeEach(() => {
  ;(window as unknown as { ipc: { invoke: (...a: unknown[]) => Promise<unknown> } }).ipc = {
    invoke: vi.fn(async () => { throw new Error('kein ipc im Test') }),
  }
})
afterEach(cleanup)

describe('KnowledgeView: Konzepte als Kategorie', () => {
  it('zaehlt HTML-Konzepte im Ordner mit und zeigt sie ohne Endung in der Vorschau', () => {
    zeige({})
    expect(screen.getByText('2 notes')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Sommer' })).toBeInTheDocument()
  })

  it('listet ein Konzept im Ordner ohne .html mit Abzeichen und oeffnet es per Klick', () => {
    const onOpenNote = vi.fn()
    zeige({ folderPath: 'knowledge/Konzepte', onOpenNote })
    expect(screen.queryByText('Sommer.html')).toBeNull()
    const zeile = screen.getByText('Sommer')
    // neutrales Abzeichen: die Liste kennt den Dateiinhalt nicht (kein Lesen je Zeile)
    expect(screen.getAllByText('HTML-Seite')).toHaveLength(1)
    expect(screen.queryByText('Konzept')).toBeNull()
    fireEvent.click(zeile)
    expect(onOpenNote).toHaveBeenCalledWith('knowledge/Konzepte/Sommer.html')
  })

  it('.htm, .xhtml und grosse Endungen; Ordner nur mit HTML', () => {
    const nurHtml: Knoten[] = [
      {
        path: 'knowledge/Ideen',
        name: 'Ideen',
        kind: 'dir',
        children: [datei('knowledge/Ideen/Alt.htm'), datei('knowledge/Ideen/Neu.XHTML'), datei('knowledge/Ideen/Gross.HTML')],
      },
    ]
    render(
      <KnowledgeView
        tree={nurHtml}
        actions={aktionen}
        mode="files"
        onModeChange={vi.fn()}
        graphContent={null}
        basisContent={null}
        folderPath="knowledge/Ideen"
        onNavigateFolder={vi.fn()}
        onOpenNote={vi.fn()}
        onOpenSearch={vi.fn()}
      />,
    )
    for (const name of ['Alt', 'Neu', 'Gross']) expect(screen.getByText(name)).toBeInTheDocument()
    expect(screen.getAllByText('HTML-Seite')).toHaveLength(3)
    expect(screen.getByText('3 notes across 1 folder')).toBeInTheDocument()
  })

  it('Knopf „Neues Konzept" startet einen Chat mit der vorbereiteten Nachricht', () => {
    const onNewKonzept = vi.fn()
    zeige({ onNewKonzept })
    fireEvent.click(screen.getByRole('button', { name: 'Neues Konzept' }))
    expect(onNewKonzept).toHaveBeenCalledWith(
      'Lade bitte zuerst die Fähigkeit „konzept". Ich möchte ein neues Konzept als HTML-Seite. Frag mich kurz, wofür es sein soll.',
    )
  })
})
