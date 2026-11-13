// Deutsche Anzeige für Spalten, Auswahlwerte und Verlaufstexte. Der gesendete Rohwert
// bleibt unverändert; nur die Anzeige wird abgebildet, unbekannte Werte erscheinen roh.
import { STATUS } from '@/lib/status'

const SPALTEN: Readonly<Record<string, string>> = {
  faellig: 'Fällig',
  betrag_cent: 'Betrag',
  kategorie_id: 'Kategorie',
  verantwortlich_id: 'Verantwortlich',
  kontakt_id: 'Kontakt',
  beginn: 'Beginn',
  ende: 'Ende',
  titel: 'Titel',
  beschreibung: 'Beschreibung',
  rolle: 'Rolle',
  zusage: 'Zusage',
  art: 'Art',
  status: 'Status',
  ort: 'Ort',
  name: 'Name',
}

const WERTE: Readonly<Record<string, string>> = {
  offen: 'offen',
  in_arbeit: 'in Arbeit',
  erledigt: 'erledigt',
  zugesagt: 'zugesagt',
  vielleicht: 'vielleicht',
  abgesagt: 'abgesagt',
  kosten: 'Kosten',
  einnahme: 'Einnahme',
  geplant: 'geplant',
  bestaetigt: 'bestätigt',
  bezahlt: 'bezahlt',
}

const AKTIONEN: Readonly<Record<string, string>> = {
  angelegt: 'angelegt',
  geaendert: 'geändert',
  geloescht: 'gelöscht',
}

const QUELLEN: Readonly<Record<string, string>> = {
  oberflaeche: 'Oberfläche',
  bot: 'Bot',
  system: 'System',
}

/** Spaltenüberschrift: feste deutsche Namen, sonst `_id`/`_cent` ab, erster Buchstabe groß. */
export function spaltenName(spalte: string): string {
  const fest = SPALTEN[spalte]
  if (fest) return fest
  const text = spalte.replace(/_(id|cent)$/, '').replace(/_/g, ' ')
  return text.charAt(0).toUpperCase() + text.slice(1)
}

/** Anzeige eines Auswahlwerts (`auswahl:`-Spalten); Rohwert als Rückfall. */
export function wertText(wert: string): string {
  return WERTE[wert] ?? wert
}

/** Anzeige einer Verlaufsaktion; `status_<s>` wird zu „Status: <Name>". */
export function aktionText(aktion: string): string {
  const fest = AKTIONEN[aktion]
  if (fest) return fest
  if (aktion.startsWith('status_')) {
    const roh = aktion.slice('status_'.length)
    const name = STATUS[roh as keyof typeof STATUS]?.text
    return `Status: ${name ?? roh}`
  }
  return aktion
}

export function quelleText(quelle: string): string {
  return QUELLEN[quelle] ?? quelle
}
