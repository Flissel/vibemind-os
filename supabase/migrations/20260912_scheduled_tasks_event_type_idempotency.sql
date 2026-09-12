-- Migration: scheduled_tasks bekommt event_type und idempotency_key
-- Datum: 2026-09-12
-- Auftrag: Betreiber — "alles supabase"; schedule verlässt seinen SQLite-Laden.
--
-- Warum diese Migration klein ist
-- ------------------------------
-- `public.scheduled_tasks` existiert seit der Initial-Migration
-- (20260411_init_vibemind.sql, Zeile 246) und ist LEER. `spaces/schedule`
-- führte daneben einen zweiten, eigenen SQLite-Speicher mit fast demselben
-- Schema. Es fehlen deshalb nur die zwei Spalten, die SQLite hatte und
-- Supabase nicht:
--
--   event_type       trägt das kanonische Ereignis ("schedule.create",
--                    "openclaw.cron"). Die SQLite-Fassung hatte es NOT NULL;
--                    hier bleibt es nullable mit Vorgabewert, damit die
--                    bestehenden Spalten-Defaults der Tabelle nicht brechen
--                    und ein Schreiber ohne Ereignis weiterhin durchkommt.
--
--   idempotency_key  der Wiederholungsschutz von `create()`. Der Code prüft
--                    ihn per Lesen-dann-Schreiben; der eindeutige Index ist
--                    der Rückhalt gegen das Rennen zwischen zwei Aufrufen.
--                    UNIQUE auf einer nullable Spalte lässt in Postgres
--                    beliebig viele NULL zu — genau richtig, denn die
--                    meisten Aufgaben tragen keinen Schlüssel.
--
-- Additiv und umkehrbar: zwei Spalten, ein Index, auf einer leeren Tabelle.
-- Nichts wird umgeschrieben, keine fremde Tabelle berührt.

ALTER TABLE scheduled_tasks
    ADD COLUMN IF NOT EXISTS event_type TEXT DEFAULT 'schedule.create';

ALTER TABLE scheduled_tasks
    ADD COLUMN IF NOT EXISTS idempotency_key TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_sched_idempotency
    ON scheduled_tasks (idempotency_key)
    WHERE idempotency_key IS NOT NULL;
