import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { VerfuegbarListe, KATEGORIE_ANZEIGE } from './verfuegbar-liste'

const HINWEIS = 'Noch von niemandem eingerichtet. Funktioniert vermutlich, alle Werkzeuge brauchen eine Freigabe.'
const VORLAGEN = [
  { id: 'hubspot', name: 'HubSpot', icon: 'H', beschreibung: 'CRM', kategorie: 'Sales', verifiziert: false, stichworte: ['crm'] },
  { id: 'notion', name: 'Notion', icon: 'N', beschreibung: 'Notizen', kategorie: 'Productivity', verifiziert: true, stichworte: [] },
  { id: 'stripe', name: 'Stripe', icon: 'S', beschreibung: 'Zahlung', kategorie: 'Finance', verifiziert: false, stichworte: [] },
]
const rendern = (onHinzufuegen = vi.fn()) =>
  render(<VerfuegbarListe vorlagen={VORLAGEN} laufend={new Set()} ausgegraut={false} onHinzufuegen={onHinzufuegen} />)
afterEach(cleanup)

describe('VerfuegbarListe', () => {
  it('gruppiert alphabetisch nach deutscher Anzeige', () => {
    rendern()
    const h = screen.getAllByRole('heading', { level: 3 }).map((e) => e.textContent)
    expect(h).toEqual(['Finanzen', 'Produktivität', 'Vertrieb'])
  })
  it('Eintraege alphabetisch nach Name', () => {
    render(<VerfuegbarListe laufend={new Set()} ausgegraut={false} onHinzufuegen={vi.fn()}
      vorlagen={[{ ...VORLAGEN[0], id: 'b', name: 'Beta' }, { ...VORLAGEN[0], id: 'a', name: 'Alpha' }]} />)
    expect(screen.getAllByTestId('vorlage-name').map((e) => e.textContent)).toEqual(['Alpha', 'Beta'])
  })
  it('Suche nach Name', () => {
    rendern()
    fireEvent.change(screen.getByLabelText('Vorlagen durchsuchen'), { target: { value: 'hub' } })
    expect(screen.getByText('HubSpot')).toBeInTheDocument()
    expect(screen.queryByText('Notion')).not.toBeInTheDocument()
  })
  it('Suche nach Stichwort, Gross/klein egal', () => {
    rendern()
    fireEvent.change(screen.getByLabelText('Vorlagen durchsuchen'), { target: { value: 'CRM' } })
    expect(screen.getByText('HubSpot')).toBeInTheDocument()
    expect(screen.queryByText('Stripe')).not.toBeInTheDocument()
  })
  it('keine Treffer', () => {
    rendern()
    fireEvent.change(screen.getByLabelText('Vorlagen durchsuchen'), { target: { value: 'xyz' } })
    expect(screen.getByText('Keine Vorlage passt zur Suche.')).toBeInTheDocument()
  })
  it('Kennzeichen ungeprueft nur bei ungeprueften, mit Hinweis als title', () => {
    rendern()
    const k = screen.getAllByText('ungeprüft')
    expect(k).toHaveLength(2)
    expect(k[0]).toHaveAttribute('title', HINWEIS)
    const notion = screen.getByText('Notion').closest('[data-testid="vorlage"]') as HTMLElement
    expect(notion.textContent).not.toContain('ungeprüft')
  })
  it('Hinzufuegen ruft Callback', () => {
    const f = vi.fn()
    rendern(f)
    fireEvent.click(screen.getByRole('button', { name: 'HubSpot hinzufügen' }))
    expect(f).toHaveBeenCalledWith('hubspot')
  })
  it('unbekannte Kategorie unveraendert, bekannte uebersetzt', () => {
    expect(KATEGORIE_ANZEIGE['Dev Tools']).toBe('Entwicklung')
    render(<VerfuegbarListe laufend={new Set()} ausgegraut={false} onHinzufuegen={vi.fn()}
      vorlagen={[{ ...VORLAGEN[0], kategorie: 'Exotisch' }]} />)
    expect(screen.getByRole('heading', { level: 3 })).toHaveTextContent('Exotisch')
  })
})
