// VibeMind: IPC-Vertrag der Integrationen-Seite (Teilprojekt 2). Strikte Objekte:
// Zusatzfelder (Referenznamen, Links, Tokens) werden abgelehnt.
import { z } from 'zod'

export const INTEGRATION_ID = z.string().regex(/^[a-z0-9][a-z0-9-]{0,62}$/)
export const IntegrationIdReq = z.object({ id: INTEGRATION_ID }).strict()

export const InstallierteIntegrationSchema = z.object({
  id: INTEGRATION_ID,
  name: z.string(),
  icon: z.string(),
  zustand: z.string(),
  detail: z.string().nullable(),
  werkzeuge: z.number().int().nonnegative(),
  statischerSchluessel: z.boolean(),
  offeneFreigaben: z.number().int().nonnegative(),
}).strict()

export const VerfuegbareVorlageSchema = z.object({
  id: INTEGRATION_ID, name: z.string(), icon: z.string(), beschreibung: z.string(), kategorie: z.string(),
}).strict()

export const IntegrationsStatusSchema = z.object({
  verbindung: z.enum(['ok', 'openfang_aus', 'schluessel_fehlt', 'schluessel_falsch']),
  installiert: z.array(InstallierteIntegrationSchema),
  verfuegbar: z.array(VerfuegbareVorlageSchema),
}).strict()

export const AktionsErgebnisSchema = z.object({
  ok: z.boolean(), zustand: z.string().optional(), meldung: z.string().optional(),
}).strict()

export type IntegrationsStatusDto = z.infer<typeof IntegrationsStatusSchema>
export type InstallierteIntegrationDto = z.infer<typeof InstallierteIntegrationSchema>
export type VerfuegbareVorlageDto = z.infer<typeof VerfuegbareVorlageSchema>
export type AktionsErgebnisDto = z.infer<typeof AktionsErgebnisSchema>
