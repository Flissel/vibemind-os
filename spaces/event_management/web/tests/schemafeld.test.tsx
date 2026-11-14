import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SchemaFeld } from '@/components/formular/SchemaFeld'
import type { Feld } from '@/lib/typen'

function feld(o: Partial<Feld>): Feld {
  return { id: 'f', typ: 'text', beschriftung: 'Feld', pflicht: false, vorgabe: null, optionen: [], ...o }
}

describe('SchemaFeld', () => {
  it('text: input, meldet den Text, leer = nicht gesetzt', () => {
    const aendern = vi.fn()
    render(<SchemaFeld feld={feld({ typ: 'text', beschriftung: 'Motto' })} wert="Hallo" aendern={aendern} />)
    const el = screen.getByLabelText('Motto') as HTMLInputElement
    expect(el.tagName).toBe('INPUT')
    expect(el.value).toBe('Hallo')
    fireEvent.change(el, { target: { value: 'Neu' } })
    expect(aendern).toHaveBeenLastCalledWith('Neu')
    fireEvent.change(el, { target: { value: '' } })
    expect(aendern).toHaveBeenLastCalledWith(undefined)
  })

  it('langtext: textarea', () => {
    const aendern = vi.fn()
    render(<SchemaFeld feld={feld({ typ: 'langtext', beschriftung: 'Notiz' })} wert={undefined} aendern={aendern} />)
    const el = screen.getByLabelText('Notiz')
    expect(el.tagName).toBe('TEXTAREA')
    fireEvent.change(el, { target: { value: 'a\nb' } })
    expect(aendern).toHaveBeenCalledWith('a\nb')
  })

  it('zahl: number-Input, meldet eine Zahl, leer = nicht gesetzt', () => {
    const aendern = vi.fn()
    render(<SchemaFeld feld={feld({ typ: 'zahl', beschriftung: 'Plätze' })} wert={5} aendern={aendern} />)
    const el = screen.getByLabelText('Plätze') as HTMLInputElement
    expect(el.type).toBe('number')
    expect(el.value).toBe('5')
    fireEvent.change(el, { target: { value: '12' } })
    expect(aendern).toHaveBeenLastCalledWith(12)
    fireEvent.change(el, { target: { value: '' } })
    expect(aendern).toHaveBeenLastCalledWith(undefined)
  })

  it('ja_nein: Checkbox meldet boolean', () => {
    const aendern = vi.fn()
    render(<SchemaFeld feld={feld({ typ: 'ja_nein', beschriftung: 'Wichteln' })} wert={false} aendern={aendern} />)
    const el = screen.getByLabelText('Wichteln') as HTMLInputElement
    expect(el.type).toBe('checkbox')
    expect(el.checked).toBe(false)
    fireEvent.click(el)
    expect(aendern).toHaveBeenCalledWith(true)
  })

  it('datum, datumzeit und url haben den passenden Input-Typ', () => {
    const typen: Array<[Feld['typ'], string]> = [
      ['datum', 'date'],
      ['datumzeit', 'datetime-local'],
      ['url', 'url'],
    ]
    for (const [typ, erwartet] of typen) {
      const { unmount } = render(<SchemaFeld feld={feld({ typ, beschriftung: 'X' })} wert={undefined} aendern={vi.fn()} />)
      expect((screen.getByLabelText('X') as HTMLInputElement).type).toBe(erwartet)
      unmount()
    }
  })

  it('auswahl: select über die Optionen', () => {
    const aendern = vi.fn()
    render(
      <SchemaFeld
        feld={feld({ typ: 'auswahl', beschriftung: 'Plattform', optionen: ['Zoom', 'Jitsi'] })}
        wert="Zoom"
        aendern={aendern}
      />,
    )
    const el = screen.getByLabelText('Plattform') as HTMLSelectElement
    expect(el.tagName).toBe('SELECT')
    expect(el.value).toBe('Zoom')
    expect(Array.from(el.options).map((o) => o.value)).toEqual(['', 'Zoom', 'Jitsi'])
    fireEvent.change(el, { target: { value: 'Jitsi' } })
    expect(aendern).toHaveBeenCalledWith('Jitsi')
  })

  it('Pflicht wird mit * markiert', () => {
    render(<SchemaFeld feld={feld({ beschriftung: 'Thema', pflicht: true })} wert={undefined} aendern={vi.fn()} />)
    expect(screen.getByLabelText('Thema *')).toBeTruthy()
  })
})

describe('SchemaFeld zahl: Rohtext', () => {
  it('hält den Rohtext lokal; leer = undefined, sonst Zahl', () => {
    const aendern = vi.fn()
    render(<SchemaFeld feld={feld({ typ: 'zahl', beschriftung: 'Z' })} wert={undefined} aendern={aendern} />)
    const el = screen.getByLabelText('Z') as HTMLInputElement
    fireEvent.change(el, { target: { value: '-3' } })
    expect(aendern).toHaveBeenLastCalledWith(-3)
    expect(el.value).toBe('-3')
    fireEvent.change(el, { target: { value: '' } })
    expect(aendern).toHaveBeenLastCalledWith(undefined)
    expect(el.value).toBe('')
  })
})
