import { describe, expect, it } from 'vitest'
import { sichererWeiter } from '@/lib/weiter'

const O = 'https://events.example.org'

describe('sichererWeiter', () => {
  it('lässt relative Pfade durch', () => {
    expect(sichererWeiter('/events/x?reiter=a', O)).toBe('/events/x?reiter=a')
  })
  it.each([
    ['//boese.example'],
    [decodeURIComponent('/%09/evil.example')],
    ['/\n/evil'],
    ['/\\evil'],
    ['https://boese.example'],
    ['javascript:alert(1)'],
    [''],
    [null],
  ])('weist %j auf "/" ab', (w) => {
    expect(sichererWeiter(w, O)).toBe('/')
  })
})
