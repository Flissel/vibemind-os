// Die Klassen stehen als ganze Texte hier, damit Tailwind sie beim Scannen findet.
export const VERLAEUFE: readonly string[] = [
  'from-indigo-500 to-violet-600',
  'from-sky-500 to-blue-600',
  'from-emerald-500 to-teal-600',
  'from-rose-500 to-pink-600',
  'from-amber-500 to-orange-600',
  'from-fuchsia-500 to-purple-600',
  'from-cyan-500 to-sky-600',
  'from-lime-500 to-green-600',
  'from-red-500 to-rose-600',
]

function hash(text: string): number {
  let h = 5381
  for (let i = 0; i < text.length; i++) h = (Math.imul(h, 33) + text.charCodeAt(i)) >>> 0
  return h
}

/** Fester Verlauf je Kategorie-Id (stabiler Hash; leer oder unbekannt ergibt trotzdem einen gültigen). */
export function kategorieVerlauf(kategorieId: string): string {
  return VERLAEUFE[hash(kategorieId) % VERLAEUFE.length] ?? VERLAEUFE[0] ?? ''
}
