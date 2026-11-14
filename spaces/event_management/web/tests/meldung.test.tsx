import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { fehlerText, Meldung } from '@/components/Meldung'
import { ApiFehler } from '@/lib/api'

describe('fehlerText (R4)', () => {
  it('nutzt die exakten Texte', () => {
    expect(fehlerText(new ApiFehler(403, 'Keine Berechtigung fuer event_absagen'))).toBe(
      'Keine Berechtigung',
    )
    expect(fehlerText(new ApiFehler(404, 'Nicht gefunden'))).toBe('Nicht gefunden')
    expect(fehlerText(new ApiFehler(422, "Pflichtfeld 'titel' fehlt"))).toBe(
      "Pflichtfeld 'titel' fehlt",
    )
  })

  it('alles andere ist "Interner Fehler"', () => {
    for (const status of [0, 400, 401, 409, 500, 502]) {
      expect(fehlerText(new ApiFehler(status, 'irgendein Detail'))).toBe('Interner Fehler')
    }
    expect(fehlerText(new Error('x'))).toBe('Interner Fehler')
    expect(fehlerText('kaputt')).toBe('Interner Fehler')
  })

  it('Meldung zeigt Fehlertext bzw. Erfolg', () => {
    const { rerender } = render(<Meldung fehler={new ApiFehler(403, 'x')} />)
    expect(screen.getByRole('alert').textContent).toBe('Keine Berechtigung')
    rerender(<Meldung erfolg="Gespeichert" />)
    expect(screen.getByRole('status').textContent).toBe('Gespeichert')
  })
})
