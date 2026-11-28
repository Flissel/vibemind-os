-- Blue Rose als Personal Space, Stufe 1 (brain/the_brain/core/blue_rose.py).
-- Befinden aus der eigenen Antwort des Nutzers und die Schalter des Bereichs.
-- Nur Service-Rolle (RLS an, keine Rechte fuer anon/authenticated): persoenliche Daten.
-- Kein freier Text: fuenf feste Woerter und eine Zahl.
create table if not exists public.blue_rose_befinden (
    id         uuid primary key default gen_random_uuid(),
    stimmung   text not null
               check (stimmung in ('energized','focused','calm','tired','anxious')),
    energie    int  not null check (energie between 1 and 10),
    stunde     int  not null check (stunde between 0 and 23),
    tageszeit  text not null
               check (tageszeit in ('early_morning','morning','midday','afternoon','evening','night')),
    angelegt   timestamptz not null default now()
);
create index if not exists blue_rose_befinden_angelegt_idx on public.blue_rose_befinden (angelegt desc);
alter table public.blue_rose_befinden enable row level security;
revoke all on public.blue_rose_befinden from anon, authenticated;
grant all on public.blue_rose_befinden to service_role;
comment on table public.blue_rose_befinden is
  'Blue Rose: Befinden aus eigener Antwort des Nutzers. Nur Service-Rolle.';

create table if not exists public.blue_rose_einstellungen (
    schluessel  text primary key check (schluessel in ('analyse')),
    wert        jsonb not null,
    geaendert   timestamptz not null default now()
);
alter table public.blue_rose_einstellungen enable row level security;
revoke all on public.blue_rose_einstellungen from anon, authenticated;
grant all on public.blue_rose_einstellungen to service_role;
comment on table public.blue_rose_einstellungen is
  'Blue Rose: Schalter des Bereichs (analyse an/aus). Nur Service-Rolle.';
