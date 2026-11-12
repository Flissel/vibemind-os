// Deutsche Standardnamen der Bausteine (KERN + ZUSAETZE aus system/bausteine.py).
// Anzeige = schema.begriffe[b] ?? begriffe[b] ?? b. tests/begriffe.test.ts hält die
// Schlüssel gegen bausteine.py im Gleichschritt.
/** Kern-Bausteine jeder Kategorie (system/bausteine.py KERN); der Rest sind Zusätze. */
export const KERN: readonly string[] = [
  'aufgaben',
  'orga_team',
  'teilnehmende',
  'ablauf',
  'budget',
  'nachrichten',
  'verlauf',
]

export const begriffe: Readonly<Record<string, string>> = {
  aufgaben: 'Aufgaben',
  orga_team: 'Orga-Team',
  teilnehmende: 'Teilnehmende',
  ablauf: 'Ablauf',
  budget: 'Budget',
  nachrichten: 'Nachrichten',
  verlauf: 'Verlauf',
  wettbewerb_teams: 'Teams',
  abgaben: 'Abgaben',
  jury: 'Jury',
  aussteller: 'Aussteller',
  staende: 'Stände',
  sprecher: 'Sprecher',
  online_raum: 'Online-Raum',
  aufzeichnung: 'Aufzeichnung',
  protokoll: 'Protokoll',
  beschluesse: 'Beschlüsse',
  verpflegung: 'Verpflegung',
  programm: 'Programm',
  helferschichten: 'Helferschichten',
  buehnen: 'Bühnen',
}

/** Anzeigename eines Bausteins; `kategorieBegriffe` ist `schema.begriffe`. */
export function bausteinName(
  baustein: string,
  kategorieBegriffe?: Readonly<Record<string, string>>,
): string {
  return kategorieBegriffe?.[baustein] ?? begriffe[baustein] ?? baustein
}
