import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { EventKachel } from '@/components/katalog/EventKachel'
import { katalogEvent } from './katalog-daten'

describe('EventKachel', () => {
  it('zeigt Firma, Spanne, Ort, Fortschritt und verlinkt auf das Event', () => {
    render(<EventKachel event={katalogEvent()} />)
    expect(
      screen.getByText(/Beispiel GmbH · 20\.–21\.11\.2026 · Musterstraße 1, Berlin/),
    ).toBeTruthy()
    expect(screen.getByText('Aufgaben 3/4 · 7 Zusagen')).toBeTruthy()
    expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('3')
    expect(screen.getByRole('link').getAttribute('href')).toBe('/events/e1')
  })

  it('zeigt den Namen mit Script wörtlich, ohne Script-Element', () => {
    const { container } = render(
      <EventKachel event={katalogEvent({ name: '<script>alert(1)</script>' })} />,
    )
    expect(screen.getByText('<script>alert(1)</script>')).toBeTruthy()
    expect(container.querySelector('script')).toBeNull()
  })

  it('zeigt ohne Ort „Ort offen“, mit nur Link „online“', () => {
    const { unmount } = render(<EventKachel event={katalogEvent({ ort_adresse: null })} />)
    expect(screen.getByText(/· Ort offen$/)).toBeTruthy()
    unmount()
    render(
      <EventKachel
        event={katalogEvent({ ort_adresse: null, ort_link: 'https://example.org/x' })}
      />,
    )
    expect(screen.getByText(/· online$/)).toBeTruthy()
  })

  it('verlinkt Legacy mit https-URL in neuem Tab, mit javascript:-URL gar nicht', () => {
    const { unmount } = render(
      <EventKachel
        event={katalogEvent({ quelle: 'legacy', legacy_url: 'https://example.org/alt' })}
      />,
    )
    const a = screen.getByRole('link')
    expect(a.getAttribute('href')).toBe('https://example.org/alt')
    expect(a.getAttribute('target')).toBe('_blank')
    expect(a.getAttribute('rel')).toBe('noopener noreferrer')
    expect(screen.getByText('Legacy')).toBeTruthy()
    unmount()
    render(
      <EventKachel event={katalogEvent({ quelle: 'legacy', legacy_url: 'javascript:alert(1)' })} />,
    )
    expect(screen.queryByRole('link')).toBeNull()
  })

  it('zeigt bei 0/0 Aufgaben keinen Balken', () => {
    render(
      <EventKachel
        event={katalogEvent({
          kennzahlen: { aufgaben_offen: 0, aufgaben_gesamt: 0, teilnehmende_zugesagt: 0 },
        })}
      />,
    )
    expect(screen.queryByRole('progressbar')).toBeNull()
    expect(screen.getByText('Aufgaben 0/0 · 0 Zusagen')).toBeTruthy()
  })

  it('zeigt Badges für abgesagt, Phase und Kategorie', () => {
    render(<EventKachel event={katalogEvent({ status: 'abgesagt', phase: 'vergangen' })} />)
    expect(screen.getByText('abgesagt')).toBeTruthy()
    expect(screen.getByText('vorbei')).toBeTruthy()
    expect(screen.getByText('Hackathon')).toBeTruthy()
  })
})
