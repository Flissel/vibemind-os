# Tests fuer "Zurueck" (Stufe A)

Brauchen ein echtes Postgres, weil Ausloeser und plpgsql in SQLite nicht existieren.
Sie laufen nie gegen die Supabase des Betriebs: `conftest.py` bricht ab, wenn der DSN
dorthin zeigt, und ueberspringt alles, wenn `ZURUECK_TEST_DSN` fehlt.

Wegwerf-Postgres am PC (lokal installiertes PostgreSQL, kein Docker noetig):

```powershell
$bin = 'C:\Program Files\PostgreSQL\16\bin'; $d = "$env:LOCALAPPDATA\VibeMind\zurueck-testdb"
& "$bin\initdb.exe" -D $d -U postgres -A trust -E UTF8 --locale=C
& "$bin\pg_ctl.exe" -D $d -l "$d\server.log" -o "-p 55432 -c listen_addresses=127.0.0.1" start
& "$bin\psql.exe" -h 127.0.0.1 -p 55432 -U postgres -c "create database zurueck_test"
$env:ZURUECK_TEST_DSN = 'host=127.0.0.1 port=55432 dbname=zurueck_test user=postgres'
python -m pytest supabase/tests/zurueck -q
& "$bin\pg_ctl.exe" -D $d stop
```

`schema_auszug.sql` ist ein gekuerzter `pg_dump --schema-only` der zwoelf ueberwachten
Tabellen (Stand im Dateikopf). Bei Schema-Aenderungen neu ziehen, nicht von Hand pflegen.

Nicht abgedeckt: der Weg ueber PostgREST (`test_postgrest.py` wird ohne
`ZURUECK_TEST_REST_URL` uebersprungen) und die echte `vector`-Spalte (im Auszug `text`).
