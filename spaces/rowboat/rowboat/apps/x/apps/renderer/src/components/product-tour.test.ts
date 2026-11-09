import { describe, expect, it } from 'vitest'
import { TOUR_STEPS } from './product-tour'

describe('TOUR_STEPS', () => {
    it('drops the email stop while EMAIL_ENABLED is off', () => {
        expect(TOUR_STEPS.length).toBeGreaterThan(0)
        expect(TOUR_STEPS.map((s) => s.id)).not.toContain('email')
    })
})
