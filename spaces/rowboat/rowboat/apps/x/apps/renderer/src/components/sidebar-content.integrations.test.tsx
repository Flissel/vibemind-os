import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// window.ipc wird schon beim Import gebraucht (spaces-feed.ts), daher vor dem Import setzen.
vi.hoisted(() => {
  Object.assign(window, { ipc: { invoke: () => Promise.resolve(null), on: () => () => {}, send: () => {} } })
})
vi.mock('react-tweet', () => ({ Tweet: () => null }))
import { SidebarContentPanel } from './sidebar-content'
import { SidebarProvider } from '@/components/ui/sidebar'

const knowledgeActions = {
  createNote: vi.fn(), createFolder: vi.fn(), openGraph: vi.fn(), expand: vi.fn(), collapse: vi.fn(), sort: vi.fn(),
  rename: vi.fn(), remove: vi.fn(), copyPath: vi.fn(), openInNewTab: vi.fn(),
} as never

beforeEach(() => {
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }))
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('SidebarContentPanel Integrationen', () => {
  it('zeigt den Eintrag Integrationen und ruft onOpenIntegrations', () => {
    const onOpenIntegrations = vi.fn()
    render(
      <SidebarProvider>
        <SidebarContentPanel
          tree={[]}
          onSelectFile={vi.fn()}
          knowledgeActions={knowledgeActions}
          onOpenIntegrations={onOpenIntegrations}
          activeNav="integrations"
        />
      </SidebarProvider>,
    )
    fireEvent.click(screen.getByText('Integrationen'))
    expect(onOpenIntegrations).toHaveBeenCalledTimes(1)
  })
})
