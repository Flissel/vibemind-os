export type Knopf = 'anmelden' | 'abmelden' | 'schluessel' | 'neuVerbinden' | 'entfernen'
export type Farbe = 'gruen' | 'gelb' | 'rot' | 'grau'
export interface KartenEingabe { zustand: string; detail: string | null; werkzeuge: number; statischerSchluessel: boolean }
export interface KartenModell { farbe: Farbe; text: string; knoepfe: Knopf[] }

const mitDetail = (t: string, d: string | null) => (d ? `${t} · ${d}` : t)

export function kartenModell(i: KartenEingabe): KartenModell {
  switch (i.zustand) {
    case 'verbunden':
      return { farbe: 'gruen', text: `verbunden · ${i.werkzeuge} Werkzeuge`,
        knoepfe: i.statischerSchluessel ? ['neuVerbinden', 'entfernen'] : ['abmelden', 'neuVerbinden', 'entfernen'] }
    case 'anmeldung_noetig':
      return { farbe: 'gelb', text: mitDetail('Anmeldung nötig', i.detail), knoepfe: ['anmelden', 'entfernen'] }
    case 'fehlt_schluessel':
      return { farbe: 'gelb', text: 'Schlüssel fehlt', knoepfe: ['schluessel', 'entfernen'] }
    case 'schluessel_abgelehnt':
      return { farbe: 'rot', text: 'Schlüssel abgelehnt', knoepfe: ['schluessel', 'neuVerbinden', 'entfernen'] }
    case 'nicht_erreichbar':
      return { farbe: 'rot', text: mitDetail('nicht erreichbar', i.detail),
        knoepfe: i.statischerSchluessel ? ['neuVerbinden', 'schluessel', 'entfernen'] : ['neuVerbinden', 'entfernen'] }
    case 'vorlage_ungueltig':
      return { farbe: 'grau', text: 'Vorlage ungültig', knoepfe: ['entfernen'] }
    case 'nicht_zugelassen':
      return { farbe: 'grau', text: 'nicht zugelassen', knoepfe: ['entfernen'] }
    default:
      return { farbe: 'grau', text: 'wird geprüft …', knoepfe: [] }
  }
}
