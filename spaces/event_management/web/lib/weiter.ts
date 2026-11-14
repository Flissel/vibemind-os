/** Gibt einen sicheren, gleich-originären Zielpfad zurück; bei allem Unsicheren "/". */
export function sichererWeiter(weiter: string | null, origin: string): string {
  if (!weiter) return '/'
  // Steuerzeichen, Leerzeichen und Backslash: Browser entfernen/normalisieren sie.
  if (/[\x00-\x20\\]/.test(weiter)) return '/'
  if (!weiter.startsWith('/') || weiter.startsWith('//')) return '/'
  try {
    const u = new URL(weiter, origin)
    if (u.origin !== origin) return '/'
    return u.pathname + u.search + u.hash
  } catch {
    return '/'
  }
}
