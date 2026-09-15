# plugin-setup

Der eigenstaendige Space aus Aufgabe 6
(`.superpowers/sdd/2026-09-08-plugin-setup-agent/task-6-brief.md`), seit
Aufgabe 5 des Entwurfs `2026-09-12-eingabefenster` um das Eingabefenster
erweitert: ein MCP-Server (`server.py`, FastMCP) mit FUENF Werkzeugen
(`werkzeuge.py`, `server.WERKZEUGE`), die Aufnahme/Verifikation/Uebernahme
eines Plugin-Credentials orchestrieren (`ablage.py` = Aufgabe 4/Supabase,
`pruefung.py` = Aufgabe 5, OpenFang-Uebergabe = Aufgabe 1, Rowboat-Install/
Tool-Bindung = Aufgaben 2/3). Eine sechste Funktion,
`schluessel_entgegennehmen`, bleibt im selben Modul, ist aber ABSICHTLICH
kein registriertes MCP-Werkzeug -- sie ist der interne Schreibweg des
Formulars unter `/fenster/{token}` (`fenster.py`/`anfragen.py`), das
derselbe Prozess neben `/mcp` bedient. Die Bind-Adresse ist geladen: sie
bindet `0.0.0.0`, nicht Loopback -- der Container muss `/mcp` erreichen
(s. `config/openclaw.json`, `host.docker.internal`). Das Formular hat
dieselbe Bindung; eine Loopback-Adressprüfung auf den Formular-Routen ist
auf einem Docker-Desktop/WSL-Mirrored-Host GEMESSEN keine Trennung
zwischen Betreiber und Container (s. `docs/superpowers/specs/
2026-09-12-eingabefenster-design.md` E3 und `.superpowers/sdd/
2026-09-12-eingabefenster/task-5-report.md`) -- was den Agenten heute
tatsaechlich davon abhaelt, das Formular selbst zu erreichen, ist seine
Tool-Policy (`config/openclaw.json`: `tools.deny` = `group:runtime`,
`group:fs`, `group:web`, `group:ui`, `group:automation`, `group:sessions`
-- `group:automation` bleibt denied wegen `cron` (zeitgesteuerte Turns) und
`gateway`s Neustart-/`update.run`-Flaeche, NICHT weil `gateway` `tools.deny`
per `config.patch` umschreiben koennte: Review Runde 3 Fix-Runde 3 hatte
das behauptet und las damit openclaw's Prosa-Doku falsch (als Denyliste
statt als die 18-Muster-ALLOWLIST, die der kompilierte Code tatsaechlich
durchsetzt -- korrigiert in Fix-Runde 4, s. `server.py` fuer die volle
Herleitung), nicht die Bindung. Das ist KONFIGURATION, nicht STRUKTUR --
`tests/test_openclaw_tool_policy.py` ist der Tripwire fuer eine Aenderung
an dieser Datei, aber weder Beweis, dass der laufende Container sie so
geladen hat, noch dass keine hier nicht genannte Gruppe denselben Weg
anderswo oeffnet (sechs Gruppen bleiben aus diesem Grund ungeprueft denied,
s. `server.py` fuer welche und warum das vorerst genuegt).

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
| `PLUGIN_SETUP_DB_ROLE` | Override der DB-Rolle. Default `plugin_setup_agent` (`db/0003_least_privilege_role.sql`, gehaertet in `db/0004_least_privilege_role_hardening.sql`) -- **nie** `postgres`/`service_role`/`supabase_admin` produktiv setzen, das unterlaeuft die in `db/0002_state_machine.sql` erzwungene Zustandsmaschine (Review-Vorgabe #3). | `ablage.py` (immer) |
| `PLUGIN_SETUP_MCP_HOST` / `PLUGIN_SETUP_MCP_PORT` | Bind-Adresse. Default `0.0.0.0:8131` -- gilt fuer `/mcp` UND `/fenster/{token}` (derselbe Prozess, dieselbe Bindung, s. oben). NICHT Loopback, trotz `docs/superpowers/specs/2026-09-12-eingabefenster-design.md` E3s urspruenglicher (korrigierter) Annahme. | `server.py` |

Alle Variablen werden zusaetzlich aus der repo-`.env` nachgeladen (nie
ueberschrieben), wie bei den Nachbar-Sidecars (`spaces/marketing/claw/server.py`).

## Betrieb

1. `deploy/bootstrap.sh` -- Supabase-Migrationen anwenden (0001-0005),
   eigenstaendiges Compose-Projekt hochfahren.
2. `deploy/anbinden.sh` -- Saat einspielen, Gateway-Token setzen, Werkzeuge
   probieren.
3. `deploy/smoke.sh` -- prueft in Schritt 1, dass der Server genau die
   fuenf registrierten Werkzeuge meldet, und ruft in Schritt 2 drei davon
   (`plugin_bedarf`, `plugin_installieren`, `plugin_werkzeug_binden`) plus
   die interne, nicht mehr registrierte Funktion
   `schluessel_entgegennehmen` direkt auf, dann die Verbotsliste.
   `eingabe_anfordern`/`einrichtung_status` selbst werden in Schritt 2
   NICHT aufgerufen (s. Markierung im Skriptkopf). Siehe dort auch fuer den
   ehrlichen Geltungsbereich (was
   auf dieser Maschine nicht mitgeprueft werden konnte).

## Tests

```
python -m pytest spaces/plugin-setup/tests -q
```

`test_eingang.py`/`test_pruefung.py` (Aufgaben 4/5, unveraendert),
`test_ablage.py` (Aufgabe 6, echte Supabase, beweist Review-Vorgabe #3),
`test_werkzeuge.py` (Aufgabe 6, komplett gemockt, keine echten Netz-/DB-Aufrufe),
`test_anfragen.py`/`test_fenster.py` (Entwurf `2026-09-12-eingabefenster`,
Aufgaben 2/4), `test_server_werkzeugliste.py` (die Werkzeugliste als
Sicherheitsgrenze -- prueft die ECHTE FastMCP-Registrierung, nicht nur das
Tupel), `test_server_formularrouten.py` (Loopback-Wache, oauth-
Zwischenstand, POST-Body-vs-Query -- echte Requests via Starlettes
`TestClient`, kein Mock der Routen selbst), `test_openclaw_tool_policy.py`
(Tripwire fuer `config/openclaw.json`s `tools.deny`/`browser.enabled` --
die Konfiguration, die den Agenten heute tatsaechlich vom Schreibweg
abhaelt, s. oben).
