# plugin-setup

Der eigenstaendige Space aus Aufgabe 6
(`.superpowers/sdd/2026-09-08-plugin-setup-agent/task-6-brief.md`): ein
MCP-Server (`server.py`, FastMCP auf `0.0.0.0:8131`) mit vier Werkzeugen
(`werkzeuge.py`), die Aufnahme/Verifikation/Uebernahme eines Plugin-
Credentials orchestrieren (`ablage.py` = Aufgabe 4/Supabase, `pruefung.py`
= Aufgabe 5, OpenFang-Uebergabe = Aufgabe 1, Rowboat-Install/Tool-Bindung
= Aufgaben 2/3).

`spaces/plugin-setup` traegt bewusst KEIN `__init__.py` (Bindestrich ist
kein gueltiger Python-Modulname) -- alle Module hier werden bare importiert
(`import werkzeuge`, `import ablage`, `from pruefung import pruefe`) und
der Server als eigenstaendiges Skript gestartet: `python server.py`, nicht
`-m spaces.plugin-setup...`.

## Umgebungsvariablen

| Variable | Bedeutung | Pflicht fuer |
|---|---|---|
| `ROWBOAT_URL` | Basis-URL der Rowboat-Instanz (z.B. `http://127.0.0.1:3000`) | `plugin_bedarf`, `plugin_installieren`, `plugin_werkzeug_binden` |
| `ROWBOAT_API_KEY` | Projekt-API-Key, als `Authorization: Bearer` gesendet. Der Key selbst ist an EIN Projekt gebunden (Rowboats `ExistingProjectApiKeyVerifier`) -- ein abweichender `projekt`-Parameter wird von Rowboat selbst mit `forbidden` abgelehnt, nicht von diesem Space. | s.o. |
| `ROWBOAT_CATALOG_DIGEST` | Optionaler Override des gepinnten Plugin-Katalog-Digests. Default = `PINNED_PLUGIN_CATALOG_DIGEST` aus `spaces/rowboat/rowboat/packages/openai-plugin-runtime/src/domain/catalog.ts` (gelesen 11.09.2026). | s.o. |
| `PLUGIN_SETUP_OPENFANG_URL` | Basis-URL des ISOLIERTEN OpenFang-Daemons dieses Tasks (`127.0.0.1:4273`, eigener `OPENFANG_HOME`). NIE `:4200`/`~/.openfang/` (Global Constraints). | `schluessel_entgegennehmen` (nur der letzte Schritt, nach bestandener Verifikation) |
| `PLUGIN_SETUP_OPENFANG_API_KEY` | API-Key des isolierten Daemons, als `Authorization: Bearer` gesendet. Eigener Name (nicht `OPENFANG_API_KEY`), um Verwechslung mit dem Key eines anderen Daemons auszuschliessen. | s.o. |
| `PLUGIN_SETUP_DB_CONTAINER` | Override fuer den supabase-db-Container-Namen. Default: Auto-Erkennung per Namenssubstring `supabase-db` (`docker ps`). | `ablage.py` (immer) |
| `PLUGIN_SETUP_DB_ROLE` | Override der DB-Rolle. Default `plugin_setup_agent` (`db/0003_least_privilege_role.sql`) -- **nie** `postgres`/`service_role`/`supabase_admin` produktiv setzen, das unterlaeuft die in `db/0002_state_machine.sql` erzwungene Zustandsmaschine (Review-Vorgabe #3). | `ablage.py` (immer) |
| `PLUGIN_SETUP_MCP_HOST` / `PLUGIN_SETUP_MCP_PORT` | Bind-Adresse des MCP-Servers. Default `0.0.0.0:8131`. | `server.py` |

Alle Variablen werden zusaetzlich aus der repo-`.env` nachgeladen (nie
ueberschrieben), wie bei den Nachbar-Sidecars (`spaces/marketing/claw/server.py`).

## Betrieb

1. `deploy/bootstrap.sh` -- Supabase-Migrationen anwenden (0001-0003),
   eigenstaendiges Compose-Projekt hochfahren.
2. `deploy/anbinden.sh` -- Saat einspielen, Gateway-Token setzen, Werkzeuge
   probieren.
3. `deploy/smoke.sh` -- alle vier Werkzeuge einmal aufrufen, Verbotsliste
   pruefen. Siehe der Skriptkopf fuer den ehrlichen Geltungsbereich (was
   auf dieser Maschine nicht mitgeprueft werden konnte).

## Tests

```
python -m pytest spaces/plugin-setup/tests -q
```

`test_eingang.py`/`test_pruefung.py` (Aufgaben 4/5, unveraendert),
`test_ablage.py` (Aufgabe 6, echte Supabase, beweist Review-Vorgabe #3),
`test_werkzeuge.py` (Aufgabe 6, komplett gemockt, keine echten Netz-/DB-Aufrufe).
