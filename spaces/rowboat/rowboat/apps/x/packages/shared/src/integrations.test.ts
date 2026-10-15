import { describe, it, expect } from 'vitest'
import { IntegrationIdReq, IntegrationsStatusSchema, AktionsErgebnisSchema } from './integrations.js'
import { ipcSchemas } from './ipc.js'

describe('Integrationen-IPC', () => {
  it('ID-Regel wie OpenFang', () => {
    expect(IntegrationIdReq.safeParse({ id: 'vercel' }).success).toBe(true)
    expect(IntegrationIdReq.safeParse({ id: 'azure-mcp' }).success).toBe(true)
    for (const bad of ['', 'Vercel', '../x', '-x', 'a'.repeat(64), 'a/b']) expect(IntegrationIdReq.safeParse({ id: bad }).success).toBe(false)
    expect(IntegrationIdReq.safeParse({ id: 'x', extra: 1 }).success).toBe(false)
  })
  it('Status-Schema laesst keine Zusatzfelder durch', () => {
    const ok = { verbindung: 'ok', installiert: [{ id: 'g', name: 'G', icon: '', zustand: 'verbunden', detail: null, werkzeuge: 1, statischerSchluessel: true, offeneFreigaben: 0 }], verfuegbar: [] }
    expect(IntegrationsStatusSchema.safeParse(ok).success).toBe(true)
    expect(IntegrationsStatusSchema.safeParse({ ...ok, installiert: [{ ...ok.installiert[0], credential: 'X' }] }).success).toBe(false)
    expect(AktionsErgebnisSchema.safeParse({ ok: true, url: 'https://x' }).success).toBe(false)
  })
  it('Status-Schema lehnt ungueltige werkzeuge und Verbindung ab, Fehler-Ergebnis ist gueltig', () => {
    const inst = { id: 'g', name: 'G', icon: '', zustand: 'verbunden', detail: null, werkzeuge: 1, statischerSchluessel: true, offeneFreigaben: 0 }
    const mk = (i: object, verbindung = 'ok') => ({ verbindung, installiert: [i], verfuegbar: [] })
    expect(IntegrationsStatusSchema.safeParse(mk({ ...inst, werkzeuge: -1 })).success).toBe(false)
    expect(IntegrationsStatusSchema.safeParse(mk({ ...inst, werkzeuge: 1.5 })).success).toBe(false)
    expect(IntegrationsStatusSchema.safeParse(mk(inst, 'kaputt')).success).toBe(false)
    expect(AktionsErgebnisSchema.safeParse({ ok: false, zustand: 'anmeldung_noetig', meldung: 'x' }).success).toBe(true)
  })
  it('alle acht Kanaele sind registriert', () => {
    for (const k of ['status', 'add', 'anmelden', 'abmelden', 'schluessel', 'neuVerbinden', 'entfernen', 'freigabenOeffnen'])
      expect(Object.keys(ipcSchemas)).toContain(`integrations:${k}`)
  })
})
