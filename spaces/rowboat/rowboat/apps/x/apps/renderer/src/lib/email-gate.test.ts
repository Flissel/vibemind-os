import { describe, expect, it } from 'vitest'
import { EMAIL_ENABLED } from './feature-flags'
import { gateEmailView } from './email-gate'

describe('gateEmailView', () => {
  it('is off in the VibeMind build', () => { expect(EMAIL_ENABLED).toBe(false) })
  it('sends the email view home when disabled', () => {
    expect(gateEmailView({ type: 'email', threadId: 't1' }, false)).toEqual({ type: 'home' })
  })
  it('keeps every other view', () => {
    const v = { type: 'meetings' }
    expect(gateEmailView(v, false)).toBe(v)
  })
  it('keeps the email view when enabled', () => {
    const v = { type: 'email' }
    expect(gateEmailView(v, true)).toBe(v)
  })
})
