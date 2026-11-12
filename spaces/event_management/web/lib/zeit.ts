// Zeit: immer Europe/Berlin per Intl, nie die Zeitzone des Browsers.

const ZONE = 'Europe/Berlin'

const TEILE = new Intl.DateTimeFormat('de-DE', {
  timeZone: ZONE,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})

interface Wandzeit {
  jahr: string
  monat: string
  tag: string
  stunde: string
  minute: string
}

const MIT_OFFSET = /(Z|[+-]\d{2}:?\d{2})$/i

function wandzeit(iso: string): Wandzeit {
  // Ohne Offset läse `Date` die Zeit in der Zeitzone des Browsers; die API liefert immer einen.
  if (!MIT_OFFSET.test(iso)) throw new Error(`Zeit ohne Zeitzone: ${iso}`)
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) throw new Error(`Ungültige Zeit: ${iso}`)
  const teile: Record<string, string> = {}
  for (const t of TEILE.formatToParts(d)) teile[t.type] = t.value
  return {
    jahr: teile.year ?? '',
    monat: teile.month ?? '',
    tag: teile.day ?? '',
    stunde: teile.hour ?? '',
    minute: teile.minute ?? '',
  }
}

/** „12.11.2026" */
export function zeigeDatum(iso: string): string {
  const w = wandzeit(iso)
  return `${w.tag}.${w.monat}.${w.jahr}`
}

/** „23:30" */
export function zeigeZeit(iso: string): string {
  const w = wandzeit(iso)
  return `${w.stunde}:${w.minute}`
}

/** „14.–15.05.2026", „28.05.–02.06.2026", „12.11.2026, 18:00–23:30" (Tage nach Berliner Zeit). */
export function zeigeSpanne(beginnIso: string, endeIso: string): string {
  const b = wandzeit(beginnIso)
  const e = wandzeit(endeIso)
  if (b.jahr === e.jahr && b.monat === e.monat && b.tag === e.tag) {
    return `${b.tag}.${b.monat}.${b.jahr}, ${b.stunde}:${b.minute}–${e.stunde}:${e.minute}`
  }
  if (b.jahr === e.jahr && b.monat === e.monat) {
    return `${b.tag}.–${e.tag}.${e.monat}.${e.jahr}`
  }
  if (b.jahr === e.jahr) {
    return `${b.tag}.${b.monat}.–${e.tag}.${e.monat}.${e.jahr}`
  }
  return `${b.tag}.${b.monat}.${b.jahr}–${e.tag}.${e.monat}.${e.jahr}`
}

const EINGABE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$/

/** `YYYY-MM-DDTHH:mm` in Berliner Wandzeit, für `<input type="datetime-local">`. */
export function alsEingabe(iso: string): string {
  const w = wandzeit(iso)
  return `${w.jahr}-${w.monat}-${w.tag}T${w.stunde}:${w.minute}`
}

/** Wert eines `datetime-local`-Feldes für die API: dieselbe Wandzeit ohne Offset
 *  (die API liest einen Wert ohne Offset als Europe/Berlin). */
export function ausEingabe(wert: string): string {
  if (!EINGABE.test(wert)) throw new Error('Ungültige Zeiteingabe')
  return wert
}
