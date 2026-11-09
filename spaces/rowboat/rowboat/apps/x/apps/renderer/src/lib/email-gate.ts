import { EMAIL_ENABLED } from './feature-flags'

/**
 * VibeMind fork: the Email view is switched off (mail runs through sales-claw).
 * Every navigation path funnels a view through this, so a stray email view
 * (copilot, palette, menu, history, saved views) lands on Home instead.
 */
export function gateEmailView<V extends { type: string }>(
  view: V,
  enabled: boolean = EMAIL_ENABLED,
): V | { type: 'home' } {
  return !enabled && view.type === 'email' ? { type: 'home' } : view
}
