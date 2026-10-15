/**
 * Compact relative-time formatter — "just now", "5 m", "3 h", "2 d", "4 w",
 * "5 m" (months). Used by the chat sidebar's run list and the live-note pill.
 *
 * Returns an empty string for invalid timestamps so callers can fall back to
 * a default label.
 */
export function formatRelativeTime(ts: string): string {
  const date = new Date(ts)
  if (Number.isNaN(date.getTime())) return ""
  const now = Date.now()
  const diffMs = Math.max(0, now - date.getTime())
  const diffMinutes = Math.floor(diffMs / (1000 * 60))
  const diffHours = Math.floor(diffMinutes / 60)
  const diffDays = Math.floor(diffHours / 24)
  const diffWeeks = Math.floor(diffDays / 7)
  const diffMonths = Math.floor(diffDays / 30)

  if (diffMinutes < 1) return "just now"
  if (diffMinutes < 60) return `${diffMinutes} m`
  if (diffHours < 24) return `${diffHours} h`
  if (diffDays < 7) return `${diffDays} d`
  if (diffWeeks < 4) return `${diffWeeks} w`
  return `${Math.max(1, diffMonths)} m`
}
