-- Ausfuehrungskette K1 (Spec docs/superpowers/specs/2026-10-08-ausfuehrungskette-k1-auftraege-design.md)
-- Agenten-Auftraege: Brain legt an, Ausfuehrer am PC arbeitet ab, Nachfasser prueft.
-- Nur Service-Rolle (RLS an, keine Rechte fuer anon/authenticated).
create table if not exists public.brain_agent_auftraege (
    id            uuid primary key default gen_random_uuid(),
    trace_id      text,
    plan_id       text,
    hop_id        text,
    capability    text not null,
    agent         text not null,
    auftrag       text not null,
    status        text not null default 'offen'
                  check (status in ('offen','laeuft','fertig','fehler','abgelehnt','abgelaufen')),
    ergebnis      text,
    fehler        text,
    grund         text,
    angelegt      timestamptz not null default now(),
    begonnen      timestamptz,
    beendet       timestamptz,
    frist         timestamptz not null default (now() + interval '10 minutes'),
    versuche      int not null default 0,
    pruefung      jsonb,
    plan_rest     jsonb,
    uebergabe     jsonb,
    antwortkanal  jsonb,
    zugestellt    timestamptz
);
create index if not exists brain_agent_auftraege_status_idx on public.brain_agent_auftraege (status, angelegt);
create index if not exists brain_agent_auftraege_trace_idx on public.brain_agent_auftraege (trace_id);
alter table public.brain_agent_auftraege enable row level security;
revoke all on public.brain_agent_auftraege from anon, authenticated;
grant all on public.brain_agent_auftraege to service_role;
comment on table public.brain_agent_auftraege is
  'Ausfuehrungskette K1: Agenten-Auftraege Brain -> OpenFang (Ausfuehrer am PC). Nur Service-Rolle.';
