import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ThemeProvider } from '@/contexts/theme-context'
import { Toaster } from '@/components/ui/sonner'
import { IntegrationsPage } from './integrations-page'

const invoke = vi.fn()
const STATUS = {
  verbindung: 'ok',
  installiert: [
    { id: 'github', name: 'GitHub', icon: '🐙', zustand: 'verbunden', detail: null, werkzeuge: 46, statischerSchluessel: true, offeneFreigaben: 2 },
    { id: 'vercel', name: 'Vercel', icon: '▲', zustand: 'verbunden', detail: null, werkzeuge: 236, statischerSchluessel: false, offeneFreigaben: 0 },
  ],
  verfuegbar: [{ id: 'notion-remote', name: 'Notion', icon: 'N', beschreibung: 'Notizen', kategorie: 'productivity' }],
}
beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  Object.assign(window, { ipc: { invoke } })
  invoke.mockImplementation(async (ch: string) => (ch === 'integrations:status' ? STATUS : { ok: true }))
})
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers() })

describe('IntegrationsPage', () => {
  it('zeigt Karten, Werkzeuge, Freigaben-Abzeichen und Verfuegbar-Liste', async () => {
    render(<IntegrationsPage visible />)
    expect(await screen.findByText('GitHub')).toBeInTheDocument()
    expect(screen.getByText('verbunden · 46 Werkzeuge')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '2 Freigaben offen' })).toBeInTheDocument()
    expect(screen.getByText('Notion')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '2 Freigaben offen' }))
    await waitFor(() => expect(invoke).toHaveBeenCalledWith('integrations:freigabenOeffnen', null))
  })
  it('Hinweis bei openfang_aus, schluessel_fehlt, schluessel_falsch', async () => {
    for (const [v, t] of [['openfang_aus', 'OpenFang läuft nicht (127.0.0.1:4200)'],
      ['schluessel_fehlt', 'Rowboat hat keinen gültigen OpenFang-Schlüssel – Rowboat über start_desktop.py neu starten'],
      ['schluessel_falsch', 'Rowboat hat keinen gültigen OpenFang-Schlüssel – Rowboat über start_desktop.py neu starten']]) {
      invoke.mockImplementation(async () => ({ verbindung: v, installiert: [], verfuegbar: [] }))
      render(<IntegrationsPage visible />)
      expect(await screen.findByText(t)).toBeInTheDocument()
      cleanup()
    }
  })
  it('Entfernen und Abmelden fragen erst nach', async () => {
    render(<IntegrationsPage visible />)
    await screen.findByText('Vercel')
    fireEvent.click(screen.getByRole('button', { name: 'Vercel abmelden' }))
    expect(invoke).not.toHaveBeenCalledWith('integrations:abmelden', { id: 'vercel' })
    fireEvent.click(await screen.findByRole('button', { name: 'Abmelden' }))
    await waitFor(() => expect(invoke).toHaveBeenCalledWith('integrations:abmelden', { id: 'vercel' }))
    fireEvent.click(screen.getByRole('button', { name: 'GitHub entfernen' }))
    expect(invoke).not.toHaveBeenCalledWith('integrations:entfernen', { id: 'github' })
    fireEvent.click(await screen.findByRole('button', { name: 'Entfernen' }))
    await waitFor(() => expect(invoke).toHaveBeenCalledWith('integrations:entfernen', { id: 'github' }))
  })
  it('Knopf ist waehrend der Aktion gesperrt (kein Doppelklick)', async () => {
    let resolve!: (v: unknown) => void
    invoke.mockImplementation(async (ch: string) => (ch === 'integrations:status' ? STATUS : new Promise((r) => { resolve = r })))
    render(<IntegrationsPage visible />)
    const btn = await screen.findByRole('button', { name: 'Notion hinzufügen' })
    fireEvent.click(btn); fireEvent.click(btn)
    expect(invoke.mock.calls.filter((c) => c[0] === 'integrations:add')).toHaveLength(1)
    expect(btn).toBeDisabled()
    await act(async () => { resolve({ ok: true, zustand: 'anmeldung_noetig' }) })
  })
  it('Fehlermeldung als Toast-Text sichtbar', async () => {
    invoke.mockImplementation(async (ch: string) => (ch === 'integrations:status' ? STATUS : { ok: false, meldung: 'tresor nicht verfuegbar' }))
    render(<ThemeProvider defaultTheme="light"><Toaster /><IntegrationsPage visible /></ThemeProvider>)
    fireEvent.click(await screen.findByRole('button', { name: 'GitHub neu verbinden' }))
    expect(await screen.findByText('tresor nicht verfuegbar')).toBeInTheDocument()
  })
  it('Abfrage laeuft nur, solange die Seite sichtbar ist', async () => {
    vi.useFakeTimers()
    const { rerender } = render(<IntegrationsPage visible />)
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    const n0 = invoke.mock.calls.filter((c) => c[0] === 'integrations:status').length
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000) })
    expect(invoke.mock.calls.filter((c) => c[0] === 'integrations:status').length).toBe(n0 + 1)
    rerender(<IntegrationsPage visible={false} />)
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000) })
    expect(invoke.mock.calls.filter((c) => c[0] === 'integrations:status').length).toBe(n0 + 1)
  })
  it('M2 bei openfang_aus nur der Hinweis, keine Leer-Texte der Listen', async () => {
    invoke.mockImplementation(async () => ({ verbindung: 'openfang_aus', installiert: [], verfuegbar: [] }))
    render(<IntegrationsPage visible />)
    expect(await screen.findByText('OpenFang läuft nicht (127.0.0.1:4200)')).toBeInTheDocument()
    expect(screen.queryByText('Noch keine Integration installiert.')).not.toBeInTheDocument()
    expect(screen.queryByText('Keine weiteren Vorlagen verfügbar.')).not.toBeInTheDocument()
    expect(screen.queryByText('Installiert')).not.toBeInTheDocument()
  })
  it('nach erfolgreicher Browser-Aktion kommt die erste schnelle Abfrage nach 3 s', async () => {
    vi.useFakeTimers()
    render(<IntegrationsPage visible />)
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    const btn = screen.getByRole('button', { name: 'Notion hinzufügen' })
    await act(async () => { fireEvent.click(btn); await vi.advanceTimersByTimeAsync(0) })
    const n0 = invoke.mock.calls.filter((c) => c[0] === 'integrations:status').length
    await act(async () => { await vi.advanceTimersByTimeAsync(3_000) })
    expect(invoke.mock.calls.filter((c) => c[0] === 'integrations:status').length).toBe(n0 + 1)
  })
})
