import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { CommandPalette } from './command-palette'

// The shipped VibeMind configuration: EMAIL_ENABLED is off.
class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
}
;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub
;(Element.prototype as unknown as { hasPointerCapture: () => boolean }).hasPointerCapture = () => false
Element.prototype.scrollIntoView = () => {}

const invoke = vi.hoisted(() => vi.fn(async (channel: string) => {
    if (channel === 'codeMode:getConfig') return { enabled: false }
    throw new Error(`unexpected ipc ${channel}`)
}))

vi.mock('@/hooks/use-spaces', () => ({
    useSpacesOrgs: () => ({ orgs: [], loading: false, refresh: vi.fn() }),
    useSpaceFeeds: () => () => ({ topics: [], changeSets: [], loaded: true }),
}))
vi.mock('@/hooks/use-space-members', () => ({ useOrgRosters: () => new Map() }))
vi.mock('@/lib/spaces-visits', () => ({ spaceVisitedAt: () => null, useSpaceVisitsVersion: () => 0 }))
vi.mock('@/components/code/use-code-sessions', () => ({
    useCodeSessions: () => ({ sessions: [], projects: [], statuses: {}, statusOf: () => 'idle', loaded: true, refresh: vi.fn() }),
    projectLabel: () => '',
}))
vi.mock('@/lib/feature-flags', () => ({ SPACES_ENABLED: true, EMAIL_ENABLED: false, LOCAL_RUNTIME: true }))
vi.mock('@/lib/analytics', () => ({ searchOpened: vi.fn(), searchExecuted: vi.fn(), searchResultSelected: vi.fn() }))
vi.mock('posthog-js', () => ({ default: { people: { set_once: vi.fn() } } }))

beforeEach(() => {
    ;(window as unknown as { ipc: unknown }).ipc = { invoke, on: () => () => {}, send: () => undefined }
})
afterEach(cleanup)

describe('CommandPalette with EMAIL_ENABLED off', () => {
    it('offers no Email section row', async () => {
        render(<CommandPalette open onOpenChange={vi.fn()} chats={[]} notes={[]} onNavigate={vi.fn()} />)
        const goTo = await screen.findByText('Go to')
        const group = goTo.closest('[cmdk-group]') as HTMLElement
        const labels = within(group).getAllByRole('option').map((o) => o.textContent?.replace(/⌘\d|Ctrl\+\d/g, '').trim())
        expect(labels.length).toBeGreaterThan(0)
        expect(labels).not.toContain('Email')
        expect(labels[0]).toBe('Meetings')
    })
})
