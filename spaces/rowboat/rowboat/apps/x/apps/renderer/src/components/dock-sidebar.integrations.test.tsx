import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// window.ipc wird schon beim Import von dock-sidebar gebraucht (spaces-feed.ts), daher vor dem Import setzen.
vi.hoisted(() => {
  Object.assign(window, { ipc: { invoke: () => Promise.resolve(null), on: () => () => {}, send: () => {} } })
})
vi.mock('react-tweet', () => ({ Tweet: () => null }))
import { DockSidebar } from './dock-sidebar'

const knowledgeActions = { createNote: vi.fn(), openKnowledgeView: vi.fn(), openWorkspaceAt: vi.fn() }

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('DockSidebar Integrationen', () => {
  it('zeigt die Kachel Integrationen und ruft onOpenIntegrations', () => {
    const onOpenIntegrations = vi.fn()
    render(<DockSidebar tree={[]} knowledgeActions={knowledgeActions} onOpenIntegrations={onOpenIntegrations} activeNav="integrations" />)
    fireEvent.click(screen.getByLabelText('Integrationen'))
    expect(onOpenIntegrations).toHaveBeenCalledTimes(1)
  })
})
