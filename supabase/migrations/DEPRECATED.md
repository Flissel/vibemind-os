# DEPRECATED — Dieser Ordner ist ein Duplikat

**TODO(duplicate-migrations):** Dieser Ordner (`supabase/migrations/`)
enthaelt eine byte-identische Kopie der Migration aus
`supabase/supabase/migrations/20260411000000_init_vibemind.sql`.

## Warum zwei Ordner?

Historisch haben unterschiedliche Setups unterschiedliche Pfade erwartet:

- `supabase/migrations/` — Legacy-Pfad, wurde frueh genutzt, als das
  Supabase-Projektroot noch direkt `supabase/` war.
- `supabase/supabase/migrations/` — aktueller Pfad, passend zum
  `supabase/supabase/config.toml`, den die Supabase-CLI erwartet.

Bei einer Umstrukturierung wurde `supabase/migrations/` nicht geloescht,
sondern als Kopie stehen gelassen. Beide Dateien sind aktuell identisch
(per `diff` geprueft), koennen aber jederzeit driften — wenn jemand die
"falsche" Datei editiert, merkt das niemand.

## Plan

- **Kanonisch:** `supabase/supabase/migrations/20260411000000_init_vibemind.sql`
- **Dieser Ordner** (`supabase/migrations/`) soll geloescht werden.

Vor dem Loeschen sicherstellen, dass:
1. Keine CI-Pipeline, kein Build-Script und kein Docker-Mount diesen
   Pfad direkt referenziert.
2. `supabase db reset` / `supabase migration up` von
   `supabase/supabase/` aus funktioniert.

Siehe: `docs/portion-03-database.md` §14.3.
