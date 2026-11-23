# Auftragsbuch ausrollen (Plan-Tasks 8 bis 11; Stufe 4: Rowboat als Empfänger)

Schrittfolge mit Rückweg. Jeder Schritt, der auf dem Mini-PC, an der Datenbank, an OpenFang (Konfiguration,
Agenten-Ordner, Neustart, Nachricht an einen Agenten), am Betriebs-Checkout, am Gehirn (`brain-core`) oder in der
Root-`.env` des PC etwas ändert, startet oder stoppt, und jeder Schritt, der einen Zweig mergt oder pusht, trägt den
Marker **Eigenes Ja des Betreibers:** und braucht sein eigenes Ja. Das gilt auch für jede Aktion eines Rückwegs. Ein
Ja für eine Stufe oder für einen anderen Schritt gilt nicht. Reine Lese- und Prüfschritte tragen keinen Marker; sie
sagen das mit dem Satz „Dieser Schritt liest nur und ändert nichts.“ Ausnahmen davon stehen mit ihrem eigenen Satz
beim Schritt.

Das Repo ist öffentlich: Diese Anleitung nennt keine Adressen, Namen oder Schlüssel; sie stehen nur auf den Rechnern
selbst. Platzhalter stehen in `<…>`. Schlüssel und Passwörter stehen nie als Argument auf einer Kommandozeile, nie in
einer Ausgabe und in keinem Log: Sie entstehen auf dem Rechner, der sie braucht, und werden dort verbraucht, oder sie
reisen in einer Shell-Variable über das eingebaute `printf` in eine Pipe.

Beteiligte Orte:

- **Mini-PC** (kleiner Server): Supabase-PostgreSQL im Docker-Netz `debian_default`, später der Dienst. Der Dienst
  wird in einem eigenen Ordner `~/auftragsbuch-build` gebaut; alle Befehle dieser Anleitung für den Mini-PC laufen in
  `~/auftragsbuch-build/spaces/auftragsbuch`. Unter `~/vibemind-os` liegt dort ein Git-Checkout, aus dem andere
  Dienste laufen (z. B. der Event-Dienst). Dieser Checkout wird nicht angefasst.
- **PC** (Desktop): OpenFang, Gehirn, Root-`.env` des äußeren Repos. Alle Befehle am PC laufen in Git Bash.
- Der Weg PC → Mini-PC ist ein privates Netz: `tailscale serve` veröffentlicht den Dienst als https auf Port 8172
  („tailnet only“), mit Weiterleitung auf `http://127.0.0.1:8172`, wie beim Event-Dienst auf 8171.

Platzhalter:

- `<mini-pc>`: ssh-Ziel des Mini-PC; `<name-des-mini-pc-im-tailnet>`: sein Name im Tailnet, klein geschrieben.
- `<commit>`: der Commit des Zweigs `cc/auftragsvertrag`, der in Stufe 3 gemergt wird; `<Git-Kurz-SHA>` ist seine
  Kurzform. Die Pfade, die auf den Mini-PC kommen, sind darin dieselben wie im gemergten Stand.
- `<ZWEIG-CHECKOUT>`: ein Checkout von vibemind-os am PC auf `<commit>` (z. B. der Arbeitsbaum des Zweigs), nicht der
  Betriebs-Checkout. `<BETRIEBS-CHECKOUT>`: der Checkout von vibemind-os am PC, aus dem OpenFang die Brücke startet.
- `<PFAD-ZUR-VENV>`, `<PFAD-ZUM-AEUSSEREN-REPO>`: wie in `deploy/openfang-eintrag.beispiel.toml`.
- `<db-container>`, `<db-name>`: dieselben Werte wie `AUFTRAGSBUCH_DB_CONTAINER` und `AUFTRAGSBUCH_DB_NAME` in
  `laufzeit/werte.env`. `<psql>` steht für `docker exec -i <db-container> psql -X -v ON_ERROR_STOP=1 -U supabase_admin -d <db-name>`
  auf dem Mini-PC; SQL kommt immer über stdin.

Compose liest `AUFTRAGSBUCH_TAG` bei **jedem** Befehl; ohne Wert bricht schon `down` ab. Darum steht in dieser
Anleitung vor jedem Compose-Befehl die Zuweisung `AUFTRAGSBUCH_TAG=<Tag>`: bei `up` der neue Tag (`<Git-Kurz-SHA>`),
bei allen anderen Befehlen (`ps`, `logs`, `stop`, `start`, `down`) der laufende Tag
(`docker ps --filter name=auftragsbuch --format '{{.Image}}'` zeigt ihn). `AUFTRAGSBUCH_UID` und `AUFTRAGSBUCH_GID`
müssen bei jedem `up` gleich sein; sie stehen deshalb in der nicht versionierten Datei `.env` im Ordner des Dienstes
(Stufe 2, Schritt 2), die Compose bei jedem Befehl selbst liest.

Vorab, für alle Stufen: Claim in `002_Koordination_Live.md` (Mini-PC, Supabase, Schema `auftrag`, OpenFang) setzen
und sofort committen; bei fremdem aktivem Claim nichts anfassen und nachfragen.

## Stufe 1 (Plan-Task 8): Migration `100` einspielen

Die Migration legt zwei Spalten, eine Tabelle, drei Funktionen und die Rolle `brain_app` (`NOLOGIN`, bleibt so) an
und erweitert einen CHECK. Sie ist rein zusätzlich. Voraussetzung: `096` bis `099` sind eingespielt.

1. **Standard-Rechte prüfen:** `printf '%s\n' '\ddp' | <psql>` auf dem Mini-PC. Geben Standard-Rechte neuen
   Tabellen im Schema `auftrag` Rechte für `anon`, `authenticated` oder `sales_app`, anhalten und klären (die Migration
   entzieht sie der neuen Tabelle zwar, aber das gehört vorher besprochen). Dieser Schritt liest nur und ändert nichts.
2. **Sicherung** des Schemas `auftrag` nach dem Muster der bisherigen Sicherungen, z. B.
   `docker exec <db-container> pg_dump -U supabase_admin -d <db-name> -n auftrag > ~/backups/auftrag-vor-100-<datum>.sql`.
   Sie ändert nichts an der Datenbank, schreibt nur eine Sicherungsdatei unter `~/backups/` und ist vom Ja zur Probe
   mit abgedeckt (Schritt 3); dieses Ja wird darum vor der Sicherung eingeholt.
3. **Eigenes Ja des Betreibers:** Probe-Lauf auf der echten Datenbank: `100` und `verify_100` in **einer**
   Transaktion mit `ROLLBACK`, am PC im `<ZWEIG-CHECKOUT>` mit den `SUPABASE_*`-Werten wie bei `096` bis `099`:
   `python -m spaces.marketing.scripts.migration_probe spaces/marketing/db/100_auftragsbuch_brain.sql spaces/marketing/db/verify_100.sql`.
   Sie hinterlässt nichts, nimmt aber für ihre Dauer eine ausschließende Sperre auf `auftrag.auftraege` (wer
   wartet, bricht nach höchstens 5 Sekunden ab: `lock_timeout`). Erwartung: `PROBE OK (zurueckgerollt)`. Bei einem
   Fehler: anhalten, nichts einspielen. Hinweis: `verify_100` legt in der Probe vier Aufträge von `sales` an; ist
   Sales an dem Tag nahe an seiner Grenze von 20, scheitert die Probe aus fremdem Grund, dann später wiederholen.
4. **Eigenes Ja des Betreibers:** `100_auftragsbuch_brain.sql` einspielen, am PC im `<ZWEIG-CHECKOUT>`:
   `ssh <mini-pc> '<psql>' < spaces/marketing/db/100_auftragsbuch_brain.sql`. `098` wird danach **nicht** erneut
   eingespielt: Sein CHECK auf `art` lehnt Zeilen des Gehirns ab.
5. **Prüfen:** `printf '%s\n' 'SELECT an, art, version FROM auftrag.brain_arten;' | <psql>` liefert genau
   `brain | probe | 1`; `printf '%s\n' "SELECT rolcanlogin FROM pg_roles WHERE rolname = 'brain_app';" | <psql>`
   liefert `f`. Der Dienst `auftrag-bote` läuft weiter und sein Herzschlag ist frisch. Dieser Schritt liest nur und
   ändert nichts.

**Rückweg:**

- **Eigenes Ja des Betreibers:** zuerst die Anmelde-Rolle des Dienstes entfernen, falls Stufe 2 sie angelegt hat
  (vorher die Rückwege von Stufe 3 und 2): `printf '%s\n' 'DROP ROLE IF EXISTS auftragsbuch_dienst;' | <psql>`.
- **Eigenes Ja des Betreibers:** die Rechte von `brain_app` entziehen:
  `printf '%s\n' 'REVOKE EXECUTE ON FUNCTION auftrag.brain_anlegen(text, text, int, text, text, jsonb, text, int), auftrag.brain_lesen(uuid), auftrag.brain_probe_erledigen(uuid) FROM brain_app; REVOKE USAGE ON SCHEMA auftrag FROM brain_app;' | <psql>`.
- **Eigenes Ja des Betreibers:** die drei Funktionen entfernen:
  `printf '%s\n' 'DROP FUNCTION auftrag.brain_anlegen(text, text, int, text, text, jsonb, text, int), auftrag.brain_lesen(uuid), auftrag.brain_probe_erledigen(uuid);' | <psql>`.
- **Eigenes Ja des Betreibers:** die Tabelle entfernen: `printf '%s\n' 'DROP TABLE auftrag.brain_arten;' | <psql>`.
- **Normalfall:** Die Zeilen des Gehirns bleiben, und der erweiterte CHECK bleibt. Er ist rein zusätzlich: Für alle
  anderen Absender gilt weiter nur `reel` und `video`, er stört niemanden. Dieser Punkt ändert nichts.
- **Eigenes Ja des Betreibers:** nur statt des Normalfalls: die Proben-Zeilen löschen:
  `printf '%s\n' "DELETE FROM auftrag.auftraege WHERE von = 'brain' AND an = 'brain' AND art = 'probe';" | <psql>`.
- **Eigenes Ja des Betreibers:** nur nach dem Löschen: den alten CHECK setzen (scheitert, solange eine Zeile mit
  `von = 'brain'` existiert):
  `printf '%s\n' "ALTER TABLE auftrag.auftraege DROP CONSTRAINT auftraege_art_chk, ADD CONSTRAINT auftraege_art_chk CHECK (art IN ('reel', 'video'));" | <psql>`.
- **Eigenes Ja des Betreibers:** zuletzt die Rolle: `printf '%s\n' 'DROP ROLE brain_app;' | <psql>`.

Die zwei Spalten bleiben.

## Stufe 2 (Plan-Task 9): Dienst auf dem Mini-PC starten

Neu ist ein Container `auftragsbuch` in einem eigenen Compose-Projekt; kein anderer Dienst wird neu gestartet. Der
Dienst meldet sich an der Datenbank mit der eigenen Anmelde-Rolle `auftragsbuch_dienst` an (Mitglied von
`brain_app`); ihr Passwort entsteht auf dem Mini-PC und verlässt ihn nie.

1. **Eigenes Ja des Betreibers:** Bau-Ordner auf dem Mini-PC füllen, vom PC aus, in einem Git-Checkout, der
   `<commit>` kennt (`git archive` liest nur):
   `git -c core.autocrlf=false archive <commit> spaces/__init__.py spaces/auftragsbuch spaces/_contract | ssh <mini-pc> 'mkdir -p ~/auftragsbuch-build && tar -x -C ~/auftragsbuch-build'`.
   `laufzeit/` und `.env` kommen im Archiv nicht vor; ein späteres erneutes `tar -x` überschreibt sie nicht.
2. **Eigenes Ja des Betreibers:** Einstellungen auf dem Mini-PC anlegen, im Ordner des Dienstes:
   `mkdir -p laufzeit && chmod 700 laufzeit && cp deploy/werte.beispiel.env laufzeit/werte.env`, dann die
   Platzhalter in `laufzeit/werte.env` mit einem Editor ersetzen: `AUFTRAGSBUCH_LADEN`, `AUFTRAGSBUCH_MANDANT`,
   `AUFTRAGSBUCH_ZUSATZ_HOSTS=<name-des-mini-pc-im-tailnet>:8172` (diesen Host-Kopf sieht der Dienst hinter
   `tailscale serve`), `AUFTRAGSBUCH_DB_CONTAINER` (`docker ps` zeigt ihn), `AUFTRAGSBUCH_DB_HOST` (Name der
   Datenbank im Netz `debian_default`; seit Stufe 4 laufen die Dienste im Host-Netz, dann gelten Host und Port wie in
   Stufe 4, Schritt 8) und `AUFTRAGSBUCH_DB_NAME`. Dazu die Datei für Compose:
   `printf 'AUFTRAGSBUCH_UID=%s\nAUFTRAGSBUCH_GID=%s\n' "$(id -u)" "$(id -g)" > .env` (als derselbe Benutzer, der
   die folgenden Skripte ausführt; ihm gehören die Dateien in `laufzeit/`).
3. **Eigenes Ja des Betreibers:** Datenbank-Zugang auf dem Mini-PC: `bash deploy/db_zugang.sh`. Das Skript erzeugt
   ein Passwort nur in einer Shell-Variable, legt die Rolle `auftragsbuch_dienst` an (`LOGIN`, Mitglied von
   `brain_app`, sonst keine Rechte) oder setzt ihr Passwort neu, und schreibt `laufzeit/dsn` (Modus 600). psql sieht
   nur den SCRAM-Verifier, nie das Passwort. Wird es wiederholt, während der Dienst läuft, gilt das neue Passwort erst
   nach einem Neustart des Dienstes (eigenes Ja). Prüfen:
   `printf '%s\n' "SELECT rolname, rolcanlogin FROM pg_roles WHERE rolname IN ('brain_app', 'auftragsbuch_dienst') ORDER BY 1;" | <psql>`
   liefert `auftragsbuch_dienst | t` und `brain_app | f`.
4. **Eigenes Ja des Betreibers:** Schlüssel des Dienstes: Er entsteht am PC und wird nie angezeigt; auf den Mini-PC
   kommt nur sein Hash. In Git Bash am PC:
   `SCHLUESSEL=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')`, dann
   `printf '%s\n' "$SCHLUESSEL" | ssh <mini-pc> 'bash ~/auftragsbuch-build/spaces/auftragsbuch/deploy/anbinden.sh'`.
   `anbinden.sh` nimmt genau diese eine Zeile, schreibt nur `laufzeit/schluessel-hash` und bricht ab, wenn
   `laufzeit/dsn` fehlt. Diese Shell bleibt bis Schritt 8 offen (der Schlüssel steht nur in ihrer Variable). Geht sie
   verloren, Schritt 4 wiederholen; läuft der Dienst dann schon, braucht er danach einen Neustart (eigenes Ja).
5. **Eigenes Ja des Betreibers:** Image bauen und starten, im Ordner des Dienstes auf dem Mini-PC:
   `AUFTRAGSBUCH_TAG=<Git-Kurz-SHA> docker compose up -d --build`. `AUFTRAGSBUCH_UID`/`AUFTRAGSBUCH_GID` kommen aus
   `.env` (Schritt 2). Der Port ist nur an `127.0.0.1` des Mini-PC gebunden. Endet der Start mit
   `auftragsbuch: Datenbank nicht erreichbar (<Typ>)` oder `auftragsbuch: Vertrag nicht ladbar (<Typ>)` (in
   `AUFTRAGSBUCH_TAG=<Git-Kurz-SHA> docker compose logs auftragsbuch`), anhalten.
6. **Rauchprobe auf dem Mini-PC** gegen `127.0.0.1` (dort braucht es keinen Schlüssel):
   `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8172/gesund` liefert `200`;
   `curl -s -o /dev/null -w '%{http_code}\n' -X POST -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' http://127.0.0.1:8172/mcp`
   liefert `401`; `AUFTRAGSBUCH_TAG=<laufender Tag> docker compose ps` zeigt den Dienst als `healthy`. Dieser Schritt
   liest nur und ändert nichts.
7. **Eigenes Ja des Betreibers:** Netzweg einrichten, auf dem Mini-PC:
   `tailscale serve --bg --https=8172 http://127.0.0.1:8172` (wie beim Event-Dienst, ggf. mit `sudo`). Nie
   `tailscale serve reset`: Das nähme auch alle anderen Freigaben weg. Danach zeigt `tailscale serve status` 8172
   neben den bisherigen Freigaben, auch die des Event-Dienstes.
8. **Eigenes Ja des Betreibers:** Adresse, Schlüssel und Zertifikatsliste in die Root-`.env` des PC, in der Shell
   aus Schritt 4:
   `printf 'AUFTRAGSBUCH_URL=%s\nAUFTRAGSBUCH_SCHLUESSEL=%s\nAUFTRAGSBUCH_CA_DATEI=<PFAD-ZUR-VENV>/Lib/site-packages/certifi/cacert.pem\n' 'https://<name-des-mini-pc-im-tailnet>:8172/mcp' "$SCHLUESSEL" | "<PFAD-ZUR-VENV>/Scripts/python.exe" "<ZWEIG-CHECKOUT>/spaces/auftragsbuch/deploy/pc_env_setzen.py" "<PFAD-ZUM-AEUSSEREN-REPO>/.env"`,
   danach `unset SCHLUESSEL`. Die Adresse endet genau auf `/mcp`, ohne Schrägstrich (mit Schrägstrich antwortet der
   Dienst mit 307, die Brücke meldet dann `unerwartete Antwort (307)`). `pc_env_setzen.py` ersetzt vorhandene Zeilen
   statt doppelt anzuhängen und gibt keinen Wert aus. Die Root-`.env` ist die gewählte Ablage: Jeder Prozess, der
   diese Datei lesen kann, hält Adresse und Schlüssel des Dienstes. Die dritte Zeile, `AUFTRAGSBUCH_CA_DATEI`, ist
   kein Geheimnis, sondern der Pfad zur Zertifikatsliste (`cacert.pem`) im Paket `certifi` der Venv: Der Zertifikatsspeicher von
   Windows kann abgelaufene Zwischenzertifikate halten, die die TLS-Bibliothek dieses Pythons dann wählt (gemessen am
   22.11.), und die Brücke prüft dann nur gegen diese Liste. Fehlt die Zeile, meldet die Rauchprobe
   `Zertifikat nicht pruefbar`.
9. **Rauchprobe vom PC** über die echte Brücke, Adresse und Schlüssel aus der Root-`.env`:
   `"<PFAD-ZUR-VENV>/Scripts/python.exe" "<PFAD-ZUM-AEUSSEREN-REPO>/scripts/mcp_mit_env.py" --env AUFTRAGSBUCH_URL --env AUFTRAGSBUCH_SCHLUESSEL --env AUFTRAGSBUCH_CA_DATEI -- "<ZWEIG-CHECKOUT>/spaces/auftragsbuch/deploy/rauchprobe.py"`.
   Sie legt nichts an: `initialize`, `tools/list` und der `tools/call` von `sales_post_pruefen`, der mit
   `Unbekannte Auftragsart` scheitern muss, schreiben keine Zeile. Erwartung: drei Zeilen `ok …`
   (`initialize`, `tools/list (fuenf Werkzeuge)`, `tools/call sales_post_pruefen (Unbekannte Auftragsart)`). Danach
   auf dem Mini-PC `printf '%s\n' "SELECT count(*) FROM auftrag.auftraege WHERE von = 'brain' AND art <> 'probe';" | <psql>`
   liefert `0`, und `AUFTRAGSBUCH_TAG=<laufender Tag> docker compose logs auftragsbuch` nennt nur Werkzeugname,
   Ergebnis und Dauer.

**Rückweg:**

- **Eigenes Ja des Betreibers:** Adresse, Schlüssel und Zertifikatsliste aus der Root-`.env` des PC entfernen
  (vorher den Rückweg von Stufe 3, falls ausgeführt):
  `"<PFAD-ZUR-VENV>/Scripts/python.exe" "<ZWEIG-CHECKOUT>/spaces/auftragsbuch/deploy/pc_env_setzen.py" "<PFAD-ZUM-AEUSSEREN-REPO>/.env" --entfernen AUFTRAGSBUCH_URL AUFTRAGSBUCH_SCHLUESSEL AUFTRAGSBUCH_CA_DATEI`.
- **Eigenes Ja des Betreibers:** den Netzweg abschalten, auf dem Mini-PC: `tailscale serve --https=8172 off` (nur
  diese Freigabe).
- **Eigenes Ja des Betreibers:** `AUFTRAGSBUCH_TAG=<laufender Tag> docker compose down` im Ordner des Dienstes.
- **Eigenes Ja des Betreibers:** den Bau-Ordner samt Einstellungen löschen (`rm -r ~/auftragsbuch-build`), oder ihn
  bleiben lassen: Ohne Container liest ihn niemand. Die Anmelde-Rolle geht mit dem Rückweg von Stufe 1.

## Stufe 3 (Plan-Task 10): OpenFang und Gehirn anschließen

Jede der folgenden Handlungen braucht ihr eigenes Ja, jeweils vor dem Schritt, in dem sie steht. Der Eintrag des
Werkzeug-Servers `auftragsbuch` steht seit Task 7 in `openfang/openfang.vibemind.toml` (stdio, startet die Brücke
über `scripts/mcp_mit_env.py`, ohne Adresse und ohne Schlüssel); er wird nicht noch einmal eingetragen.

1. **Ausgangsstand festhalten**, am PC: `git -C <BETRIEBS-CHECKOUT> rev-parse --short HEAD` (notieren als
   `<AUSGANG-CHECKOUT>`), `git -C <BETRIEBS-CHECKOUT>/openfang rev-parse --short HEAD` (`<AUSGANG-OPENFANG>`),
   `docker service inspect vibemind_brain-core --format '{{.Spec.TaskTemplate.ContainerSpec.Image}}'`
   (`<AUSGANG-ABBILD>`), dazu im äußeren Repo der Vertragstest
   `python -m pytest tests/integration/test_openfang_mcp_authority_contract_v1.py -q` (Ausgangswert: 4 rot, 14 grün)
   und `grep -c '^AUFTRAGSBUCH_\(URL\|SCHLUESSEL\|CA_DATEI\)=' "<PFAD-ZUM-AEUSSEREN-REPO>/.env"` (muss `3` liefern,
   sonst zuerst Stufe 2, Schritt 8). Dieser Schritt liest nur und ändert nichts.
2. **Eigenes Ja des Betreibers:** openfang mergen und pushen: im Submodul `openfang` des `<ZWEIG-CHECKOUT>`
   `cc/auftragsvertrag` nach `main` (Fast-Forward, sonst ein echter Merge; kein Squash, kein Rebase, damit der
   Verweis von vibemind-os erreichbar bleibt), dann `git push origin main`. Prüfen:
   `git -C <ZWEIG-CHECKOUT>/openfang branch -r --contains "$(git -C <ZWEIG-CHECKOUT> rev-parse <commit>:openfang)"`
   zeigt `origin/main`.
3. **Eigenes Ja des Betreibers:** vibemind-os mergen und pushen, erst nach Schritt 2: `cc/auftragsvertrag` nach
   `master`, wie am 21.11. (`docs/superpowers/specs/2026-11-20-zurueck-ideen-bubbles-design.md`, 7h: läuft `master`
   inzwischen weiter, ihn erst einmergen, Verweis `openfang` auf den gemergten Stand von `main` nachziehen, Tests auf
   dem gemergten Stand), dann `git push origin master`.
4. **Eigenes Ja des Betreibers:** im äußeren Repo den Verweis auf vibemind-os nachziehen (wie 7h), committen und
   pushen.
5. **Eigenes Ja des Betreibers:** Betriebs-Checkout heben, wie am 21.11. (dieselbe Spec, 7k):
   `git -C <BETRIEBS-CHECKOUT> fetch origin`, `git -C <BETRIEBS-CHECKOUT> checkout --detach origin/master`,
   `git -C <BETRIEBS-CHECKOUT> submodule update openfang` (nur dieses Submodul). Damit wird der Eintrag
   `auftragsbuch` in der wirksamen Datei beim nächsten Neustart von OpenFang wirksam, auch bei einem Neustart durch
   den Wächter. Voraussetzung: `AUFTRAGSBUCH_URL`, `AUFTRAGSBUCH_SCHLUESSEL` und `AUFTRAGSBUCH_CA_DATEI` stehen in der
   Root-`.env` (Schritt 1).
6. **Prüfen** nach dem Heben: `grep -c '^name = "auftragsbuch"$' <BETRIEBS-CHECKOUT>/openfang/openfang.vibemind.toml`
   liefert genau `1`; der Vertragstest aus Schritt 1 erneut, verglichen mit dem Ausgangswert 4 rot / 14 grün (ein
   neuer roter Test ist ein Befund für den Betreiber, der Vertrag dort wird nicht stillschweigend geändert). Dieser
   Schritt liest nur und ändert nichts.
7. **Eigenes Ja des Betreibers:** Rechteträger `brain-sales` in den Agenten-Ordner von OpenFang kopieren, und zwar
   **unverändert**, mit dem Anbieter, wie der Generator ihn erzeugt hat:
   `mkdir -p ~/.openfang/agents/brain-sales && cp <BETRIEBS-CHECKOUT>/openfang/agents/brain-sales/agent.toml ~/.openfang/agents/brain-sales/agent.toml`,
   danach `cmp` beider Dateien ohne Ausgabe. Den Anbieter nicht umstellen (nicht auf `claude-code` wie bei
   `brain-ideas`): Dieser Treiber startete eine Claude-CLI-Sitzung am PC, vor der nur ein Prompt steht.
8. **Eigenes Ja des Betreibers:** OpenFang neu starten (nicht gleichzeitig mit `brain-core`).
9. **Prüfen** nach dem Neustart, über die OpenFang-API bzw. das Dashboard. Dieser Schritt liest nur und ändert
   nichts.
   - `GET /api/mcp/servers` zeigt `auftragsbuch` als verbunden mit fuenf Werkzeugen. Wenn nicht: anhalten, die
     Ursache beheben (meist fehlt ein Wert in der Root-`.env`), dann zehn Minuten lang alle zwei Minuten nachsehen
     und notieren, ob OpenFang einen stdio-Server, dessen Start fehlschlug, von selbst neu verbindet. Belegt ist
     das nicht; bis dahin braucht es einen erneuten Neustart (eigenes Ja).
   - `GET /api/agents` zeigt `brain-sales` mit `mcp_servers_mode: allowlist`; seine Werkzeug-Liste enthält genau
     `mcp_auftragsbuch_auftrag_probe` und `mcp_auftragsbuch_auftrag_lesen`.
   - Welche Agenten führen ein Werkzeug `mcp_auftragsbuch_*`? Am PC, der Schlüssel von OpenFang kommt aus der
     Root-`.env` und steht nie auf der Kommandozeile:
     `"<PFAD-ZUR-VENV>/Scripts/python.exe" "<PFAD-ZUM-AEUSSEREN-REPO>/scripts/mcp_mit_env.py" --env OPENFANG_API_KEY --set OPENFANG_URL=http://127.0.0.1:4200 -- "<ZWEIG-CHECKOUT>/spaces/auftragsbuch/deploy/openfang_aufruf.py" werkzeuge-je-agent auftragsbuch`.
     Das Hilfsprogramm holt die Agentenliste und je Agent dessen Detail (die Liste trägt keine Werkzeuge) und gibt
     zwei Gruppen aus. Gruppe 1, ausdrücklich: Agenten mit einem Werkzeug `mcp_auftragsbuch_*` in
     `capabilities.tools`, je Zeile Name und diese Werkzeuge (gleichnamige Agenten stehen beide da, mit
     `(Kennung …)` am Namen). Erwartet ist genau eine Zeile
     `brain-sales: mcp_auftragsbuch_auftrag_lesen, mcp_auftragsbuch_auftrag_probe`; nur dann Exit 0, jeder weitere
     Agent oder eine abweichende Werkzeugmenge ergibt Exit 1. Gruppe 2 unter der Überschrift
     `offener Zugang (sieht alle Werkzeuge des Servers):`: Agenten mit leerer oder `*`-Werkzeugliste, die den Server
     sehen (`mcp_servers_mode` `all`, `auftragsbuch` in `mcp_servers` oder `mcp_servers` leer), je Zeile nur der
     Name. Sie wird immer ausgegeben und ändert den Exit-Code nicht (bekannte Lücke, siehe „Offene Punkte für
     Stufe 2“); jeder dort genannte Agent wird notiert und dem Betreiber gemeldet. Scheitert ein Detail-Abruf,
     endet der Befehl mit Exit 1 und `unerwartet (<Status>)`. Ein Agent mit leerem `mcp_servers` sieht alle Werkzeug-Server, mit
     leerem `capabilities.tools` auch alle ihre Werkzeuge; nach dem Repo (Test
     `tests/test_openfang_offene_mcp_zugaenge.py`) gilt das für `capability-gap-filer` und
     `learning-course-factory`, sofern sie im Betrieb geladen sind. Die Sperre je Auftragsart in OpenFang gilt nur
     für die Identität `brain-sales`, und wer den Schlüssel von OpenFang hat, kann jede registrierte Identität
     wählen. In Stufe 1 hält die Datenbank echte Aufträge auf (`Unbekannte Auftragsart`); Proben (höchstens 50 am
     Tag) wären für solche Agenten aber möglich.
10. **Eigenes Ja des Betreibers:** eine Nachricht an `brain-sales` über OpenFang schicken (beliebiger Text).
    Erwartung: ein Fehler oder die feste Ablehnung `brain-sales accepts no messages.`, nie ein Werkzeug-Aufruf (in
    `auftrag.auftraege` keine neue Zeile). Das Ergebnis wird notiert.
11. **Eigenes Ja des Betreibers:** `brain-core` mit dem neuen Abbild ausrollen, erst nachdem OpenFang alle
    Werkzeug-Server verbunden hat, auf dem Weg vom 21.11. (dieselbe Spec, 7n und 7r; Lehre aus dem Zwischenfall
    dort: nie gleichzeitig mit dem Neustart von OpenFang, und nur mit genug freiem Speicher).

Der Rückweg arbeitet mit den in Schritt 1 notierten Werten, nie mit festen.

**Rückweg:**

- **Eigenes Ja des Betreibers:** `brain-core` mit `<AUSGANG-ABBILD>` ausrollen, auf dem Weg vom 21.11. (7n, 7r),
  nicht gleichzeitig mit einem Neustart von OpenFang.
- **Eigenes Ja des Betreibers:** den Betriebs-Checkout zurücksetzen: `git -C <BETRIEBS-CHECKOUT> checkout --detach <AUSGANG-CHECKOUT>`.
- **Eigenes Ja des Betreibers:** das Submodul dort zurücksetzen:
  `git -C <BETRIEBS-CHECKOUT>/openfang checkout --detach <AUSGANG-OPENFANG>`. Damit fehlt der Eintrag
  `auftragsbuch` beim nächsten Neustart wieder.
- **Eigenes Ja des Betreibers:** den Rechteträger aus dem Agenten-Ordner entfernen: `rm -r ~/.openfang/agents/brain-sales`.
- **Eigenes Ja des Betreibers:** OpenFang neu starten (auch der Rückweg-Neustart braucht es).
- **Prüfen:** `GET /api/mcp/servers` ohne `auftragsbuch`, `GET /api/agents` ohne `brain-sales`. Nach dem Code stellt
  OpenFang Agenten aus seiner eigenen Datenbank wieder her, auch ohne `agent.toml`; dann steht `brain-sales` nach dem
  Neustart noch da. Dieser Schritt liest nur und ändert nichts.
- **Eigenes Ja des Betreibers:** nur wenn `brain-sales` noch da ist: ihn über die OpenFang-API abmelden. Das
  Hilfsprogramm löst die Kennung selbst auf und sendet `DELETE /api/agents/<kennung>`; es akzeptiert nur den Namen
  `brain-sales`: `"<PFAD-ZUR-VENV>/Scripts/python.exe" "<PFAD-ZUM-AEUSSEREN-REPO>/scripts/mcp_mit_env.py" --env OPENFANG_API_KEY --set OPENFANG_URL=http://127.0.0.1:4200 -- "<ZWEIG-CHECKOUT>/spaces/auftragsbuch/deploy/openfang_aufruf.py" agent-entfernen brain-sales`. Ausgabe `entfernt` oder `nicht vorhanden` (je Exit 0),
  sonst `unerwartet (<Status>)` (Exit 1). Danach erneut prüfen.

Die Merges auf `main`, `master` und im äußeren Repo bleiben; sie zurückzunehmen wäre ein eigener Schritt mit eigenem
Ja und ist hier nicht vorgesehen.

## Abschluss (Plan-Task 11)

Probe und Lesen sind zwei getrennte Pläne: Das Gehirn legt die Antwort eines Werkzeugs ungeparst ab
(`content[0].text`), ein Platzhalter `{{state.probe.auftrag_id}}` bliebe leer.

1. **Eigenes Ja des Betreibers:** Plan 1, Probe über das Gehirn: `POST /api/multihop/execute` mit genau einem
   Schritt ohne Absicht: `step_id: probe`, `execution_target: mcp:brain-sales:auftragsbuch:auftrag_probe`,
   `arg_template: {}`, `output_var: probe`. Erwartung: Schritt `ok`; aus dem abgelegten Ergebnis (JSON-Text) die
   Kennung `auftrag_id` und den `vertrag_hash` notieren.
2. **Eigenes Ja des Betreibers:** Plan 2, Lesen: ein Schritt `step_id: lesen`,
   `execution_target: mcp:brain-sales:auftragsbuch:auftrag_lesen`, `arg_template: {"id": "<auftrag_id aus Plan 1>"}`
   (die Kennung von Hand eingesetzt). Erwartung: Schritt `ok`; `stand == "fertig"`, `an == "brain"`,
   `art == "probe"`.
3. **Belege nachlesen**, unabhängig vom Gehirn: in `auftrag.auftraege` genau eine neue Zeile `von = brain, an = brain,
   art = probe, stand = fertig`; in der OpenFang-Datenbank zu beiden Schritten eine Freigabe und ein Beleg im Namen
   von `brain-sales`. Dieser Schritt liest nur und ändert nichts.
4. **Eigenes Ja des Betreibers:** Gegenprobe im Gehirn: ein Plan wie in Schritt 1, dessen einziger Schritt
   `execution_target: mcp:brain-sales:auftragsbuch:sales_post_pruefen` nennt. Erwartung: Er scheitert im Gehirn,
   weil das Ziel nicht registriert ist; an OpenFang wird nichts gesendet, in `auftrag.auftraege` entsteht keine
   Zeile.
5. **Eigenes Ja des Betreibers:** Gegenprobe direkt an OpenFang: derselbe Aufruf von `sales_post_pruefen` im Namen
   von `brain-sales` (das Hilfsprogramm löst die Kennung selbst auf; Schlüssel aus der Root-`.env`, nie auf der
   Kommandozeile): `"<PFAD-ZUR-VENV>/Scripts/python.exe" "<PFAD-ZUM-AEUSSEREN-REPO>/scripts/mcp_mit_env.py" --env OPENFANG_API_KEY --set OPENFANG_URL=http://127.0.0.1:4200 -- "<ZWEIG-CHECKOUT>/spaces/auftragsbuch/deploy/openfang_aufruf.py" gegenprobe brain-sales sales_post_pruefen`.
   Das Hilfsprogramm prüft vorab über `GET /api/mcp/servers`, dass der Server `auftragsbuch` verbunden ist und das
   Werkzeug führt (sonst `unerwartet (Server auftragsbuch nicht verbunden)` bzw.
   `unerwartet (Werkzeug am Server nicht vorhanden)`, ohne Aufruf): Ohne verbundenen Server fehlte das Werkzeug
   überall und die Antwort sähe wie eine Ablehnung aus. Als Ablehnung zählt nur der JSON-RPC-Fehler `-32602` mit der
   Nachricht `Tool not permitted for caller agent: mcp_auftragsbuch_sales_post_pruefen`.
   Erwartung: `abgelehnt von OpenFang (erwartet)` und Exit 0, weil das Werkzeug für `brain-sales` nicht
   freigeschaltet ist; in `auftrag.auftraege` entsteht keine Zeile. Kommt stattdessen
   `DURCHGELASSEN: Unbekannte Auftragsart - anhalten` (Exit 1), hat OpenFang durchgelassen (erst die Datenbank hat
   abgelehnt); kommt `DURCHGELASSEN: Recht vorhanden, nur die Freigabe fehlte - anhalten` (Exit 1), war das Recht da
   und nur die Freigabe fehlte, die Sperre hat nicht gehalten: in beiden Fällen anhalten und melden.
   `unerwartet (<Grund>)` (Exit 1), auch `unerwartet (JSON-RPC-Fehler <Code>)` und jeder HTTP-Fehler, ist ebenfalls
   ein Befund für den Betreiber, keine Ablehnung.
6. **Eigenes Ja des Betreibers:** Dienst stoppen, auf dem Mini-PC: `AUFTRAGSBUCH_TAG=<laufender Tag> docker compose stop`.
   Dann Plan 1 erneut: Der Schritt scheitert sofort mit einer Meldung, die den Server nennt, hinter `tailscale serve`
   eher `auftragsbuch: unerwartete Antwort (502)`, sonst `auftragsbuch nicht erreichbar`; kein anderer Weg wird
   versucht.
7. **Eigenes Ja des Betreibers:** Dienst wieder starten: `AUFTRAGSBUCH_TAG=<laufender Tag> docker compose start`.
   Prüfen: Die beiden `curl` aus Stufe 2, Schritt 6 liefern wieder `200` und `401`, und die Rauchprobe aus Stufe 2,
   Schritt 9 (jetzt mit `<BETRIEBS-CHECKOUT>` statt `<ZWEIG-CHECKOUT>`) liefert wieder drei Zeilen `ok`.
8. **Vertrags-Hash vergleichen:** den `vertrag_hash` aus Plan 1 mit
   `python -c "from pathlib import Path; from spaces.auftragsbuch.vertrag import lade_auftragsarten, vertrag_hash; print(vertrag_hash(lade_auftragsarten(Path('spaces/_contract'))))"`
   im `<BETRIEBS-CHECKOUT>`. Dieser Schritt liest nur und ändert nichts.

Danach den Claim in `002_Koordination_Live.md` schließen (Plan-Task 11, Step 5).

## Stufe 4: Rowboat als Empfänger

Neu sind die Migration `101` (Empfang für Aufträge des Gehirns, Rolle `rowboat_empfang`), die Anmelde-Rolle
`rowboat_abholer`, zwei enge Rowboat-Schlüssel und der zweite Dienst `rowboat-abholer` im Compose-Projekt
`auftragsbuch` (dasselbe Abbild). Beide Dienste laufen ab hier im Host-Netz des Mini-PC, denn Rowboat lauscht nur auf
`127.0.0.1:3220`: Der Dienst `auftragsbuch` verlässt das Netz `debian_default`, bindet selbst nur `127.0.0.1:8172` und
erreicht die Datenbank über ihren Port am Host; in seiner DSN (`laufzeit/dsn`) ändern sich darum nur Host und Port,
Benutzer und Passwort bleiben.
Die Freigabe von `tailscale serve` zeigt weiter auf `http://127.0.0.1:8172` und wird nicht angefasst. Die Checkouts
~/vibemind-os und ~/sales-claw auf dem Mini-PC bleiben unberührt, Dienste werden nur namentlich gebaut, gestartet und
entfernt, und es läuft kein Aufräumbefehl von Docker. Die Migration 098_auftragsbuch_video.sql wird auch hier nicht
erneut eingespielt.

Platzhalter: `<commit-2>` ist der Commit des Zweigs `cc/auftragsvertrag-stufe-2`, der in Plan-Task 10 gemergt wird,
`<Kurz-SHA-2>` seine Kurzform (der neue Tag). `<AUSGANG-TAG>` ist der Tag des laufenden Dienstes aus Schritt 1; er
ist zugleich der Commit, aus dem der Bau-Ordner zuletzt gefüllt wurde. `<db-port-am-host>` ist der Port, an dem die
Datenbank auf dem Mini-PC lauscht. Die Befehle auf dem Mini-PC laufen wie oben in
`~/auftragsbuch-build/spaces/auftragsbuch`.

1. **Stand vorher notieren**, auf dem Mini-PC: `docker ps --filter name=auftragsbuch --format '{{.Image}}'`
   (Tag notieren als `<AUSGANG-TAG>`), `tailscale serve status` (8172 zeigt auf `http://127.0.0.1:8172`, notieren) und
   `printf '%s\n' 'SELECT an, art, version FROM auftrag.brain_arten;' | <psql>` (genau `brain | probe | 1`). Dieser
   Schritt liest nur und ändert nichts.
2. **Vorab zählen**, auf dem Mini-PC, gegen ein falsches Rot von `verify_101`: Die Prüfdatei läuft nur in der Probe
   (Schritt 4) und erwartet, dass `naechster('marketing', …)` und `video_naechster` ihre eigenen Probe-Zeilen nicht
   vergeben; wartet ein echter älterer Auftrag an Marketing (vor allem ein Video), vergibt die Probe diesen und meldet
   `101.4` rot.
   `printf '%s\n' "SELECT art = 'video' AS video, count(*) FROM auftrag.auftraege WHERE an = 'marketing' AND von <> 'brain' AND frist_bis > now() AND (stand = 'offen' OR (stand = 'in_arbeit' AND vergeben_bis < now())) GROUP BY 1;" | <psql>`
   liefert erwartet `(0 rows)`. Sonst die Probe später wiederholen; läuft sie trotzdem, ist ein Fehler `101.4: …`
   dort ein erwartetes falsches Rot (die Probe rollt zurück, kein Schaden), aber kein Grund, 101 auszurollen: Nur eine
   Probe mit `PROBE OK (zurueckgerollt)` gilt. Dieser Schritt liest nur und ändert nichts.
3. **Eigenes Ja des Betreibers:** Sicherung, auf dem Mini-PC: das Schema `auftrag` mit
   `docker exec <db-container> pg_dump -U supabase_admin -d <db-name> -n auftrag > ~/backups/auftrag-vor-101-<datum>.sql`,
   die Einstellungen mit `cp -p laufzeit/werte.env laufzeit/werte.env.vor-stufe-4` und die DSN des Dienstes mit
   `cp -p laufzeit/dsn laufzeit/dsn.vor-stufe-4` (behält Modus 600). Alles schreibt nur Dateien, die Datenbank bleibt
   unverändert.
4. **Eigenes Ja des Betreibers:** Probe-Lauf auf der echten Datenbank: `101` und `verify_101` in **einer**
   Transaktion mit `ROLLBACK`, am PC in einem Checkout auf `<commit-2>` mit den `SUPABASE_*`-Werten wie bei `100`:
   `python -m spaces.marketing.scripts.migration_probe spaces/marketing/db/101_auftragsbuch_empfang.sql spaces/marketing/db/verify_101.sql`.
   Sie hinterlässt nichts, nimmt aber für ihre Dauer Sperren auf `auftrag.auftraege` (wer wartet, bricht nach
   höchstens 5 Sekunden ab: `lock_timeout`). Erwartung: `PROBE OK (zurueckgerollt)`. Bei einem Fehler anhalten
   (zu `101.4` siehe Schritt 2).
5. **Eigenes Ja des Betreibers:** Bau-Ordner auf `<commit-2>` heben, vom PC aus (`git archive` liest nur, ssh-stdin
   trägt nur Bytes):
   `git -c core.autocrlf=false archive <commit-2> spaces/__init__.py spaces/auftragsbuch spaces/_contract spaces/marketing/db/101_auftragsbuch_empfang.sql | ssh <mini-pc> 'tar -x -C ~/auftragsbuch-build'`.
   `laufzeit/` und `.env` bleiben, der laufende Dienst merkt nichts. Danach auf dem Mini-PC
   `grep -c $'\r' deploy/db_zugang.sh deploy/db_zugang.py docker-compose.yml` liefert je Datei `0`.
6. **Eigenes Ja des Betreibers:** `101` einspielen, auf dem Mini-PC: zuerst
   `grep -c $'\r' ../marketing/db/101_auftragsbuch_empfang.sql` (muss `0` liefern, sonst anhalten), dann
   `<psql> < ../marketing/db/101_auftragsbuch_empfang.sql`. Die Datei schaltet keine Auftragsart frei. Die Migration
   098_auftragsbuch_video.sql wird nicht erneut eingespielt.
7. **Prüfen:** `printf '%s\n' "SELECT rolname, rolcanlogin FROM pg_roles WHERE rolname = 'rowboat_empfang';" | <psql>`
   liefert `rowboat_empfang | f`; `printf '%s\n' 'SELECT an, art, version FROM auftrag.brain_arten;' | <psql>` liefert
   weiter genau `brain | probe | 1`. Der Sales-Bote holt weiter seine Aufträge (sein Herzschlag ist frisch). Dieser
   Schritt liest nur und ändert nichts.
8. **Eigenes Ja des Betreibers:** Einstellungen für das Host-Netz, auf dem Mini-PC in `laufzeit/werte.env` mit einem
   Editor: `AUFTRAGSBUCH_DB_HOST=127.0.0.1`, neu `AUFTRAGSBUCH_DB_PORT=<db-port-am-host>` (`docker port <db-container>`
   zeigt ihn) und neu `ABHOLER_LADEN` mit demselben Wert wie `AUFTRAGSBUCH_LADEN` (siehe `deploy/werte.beispiel.env`).
   Der laufende Dienst liest die Datei erst bei seinem nächsten Start.
9. **Eigenes Ja des Betreibers:** Anmelde-Rolle des Abholers, auf dem Mini-PC: `bash deploy/db_zugang.sh abholer`.
   Das Skript prüft zuerst Zugang und Werte, erzeugt ein Passwort nur in einer Shell-Variable, legt die Rolle
   `rowboat_abholer` an (`LOGIN`, Mitglied von `rowboat_empfang`, sonst keine Rechte) oder setzt ihr Passwort neu und
   schreibt `laufzeit/abholer-dsn` (Modus 600). psql sieht nur den SCRAM-Verifier. Prüfen:
   `printf '%s\n' "SELECT u.rolname, u.rolcanlogin, g.rolname FROM pg_roles u JOIN pg_auth_members m ON m.member = u.oid JOIN pg_roles g ON g.oid = m.roleid WHERE u.rolname = 'rowboat_abholer';" | <psql>`
   liefert genau `rowboat_abholer | t | rowboat_empfang`.
10. **CLI nachsehen**, auf dem Mini-PC: Rowboat legt enge Schlüssel mit dem Unterbefehl `scoped-key` seines
    eigenen Server-Bündels `rowboat-server.cjs` an (Quelle `apps/x/apps/server/src/scoped-key-cli.ts`); es speichert
    nur den sha256 in `scoped-keys.json` in seinem Arbeitsordner und liest die Datei bei jeder Anfrage, ein Widerruf
    wirkt darum ohne Neustart. Das Anlegen gibt den Schlüssel nur auf stdout aus, sonst nirgends.
    Zuerst `docker exec rowboat-headless-rowboat-server-1 grep -cF '[scoped-key] fatal:' rowboat-server.cjs`: Die
    Zeichenkette steht nur im Zweig, der den Unterbefehl annimmt (Quelle `apps/x/apps/server/src/standalone.ts`), nicht
    im Dateinamen `scoped-keys.json`, den auch ältere Bündel kennen. Er muss mindestens `1` liefern; bei `0` anhalten
    und keinen `scoped-key`-Befehl aufrufen: Ein älteres Bündel kennt den Unterbefehl nicht und startete statt dessen
    einen zweiten Rowboat-Server im laufenden Container gegen `/data`. Dann `docker exec rowboat-headless-rowboat-server-1 node rowboat-server.cjs scoped-key list`: zeigt Namen und
    Kanäle, nie einen Schlüssel. Stehen `auftragsbuch-beleg` oder `gehirn-abholer` schon da, anhalten und klären
    (`add` ersetzt einen gleichnamigen Schlüssel, der alte wird sofort ungültig). Dieser Schritt liest nur und ändert
    nichts.
11. **Eigenes Ja des Betreibers:** enger Schlüssel für den Beleg des Dienstes, auf dem Mini-PC, nur `todo:get` und
    `todo:plannerSignals`; der Schlüssel geht von stdout direkt in die Datei und wird nie angezeigt:
    `(umask 077 && docker exec rowboat-headless-rowboat-server-1 node rowboat-server.cjs scoped-key add auftragsbuch-beleg todo:get,todo:plannerSignals > laufzeit/rowboat-beleg-schluessel)`,
    dann `chmod 600 laufzeit/rowboat-beleg-schluessel`.
12. **Eigenes Ja des Betreibers:** enger Schlüssel für den Abholer, auf dem Mini-PC, nur `todo:get`,
    `todo:plannerSignals` und `todo:addSuggestions`, ebenso direkt in die Datei:
    `(umask 077 && docker exec rowboat-headless-rowboat-server-1 node rowboat-server.cjs scoped-key add gehirn-abholer todo:get,todo:plannerSignals,todo:addSuggestions > laufzeit/rowboat-abholer-schluessel)`,
    dann `chmod 600 laufzeit/rowboat-abholer-schluessel`.
13. **Prüfen**, ohne einen Schlüssel zu zeigen: `wc -l < laufzeit/rowboat-beleg-schluessel` und
    `wc -l < laufzeit/rowboat-abholer-schluessel` liefern je `1`;
    `stat -c %a laufzeit/rowboat-beleg-schluessel laufzeit/rowboat-abholer-schluessel` liefert zweimal `600`;
    `grep -c $'\r' laufzeit/rowboat-beleg-schluessel laufzeit/rowboat-abholer-schluessel` liefert je Datei `0`;
    `docker exec rowboat-headless-rowboat-server-1 node rowboat-server.cjs scoped-key list` zeigt
    `auftragsbuch-beleg` mit `todo:get,todo:plannerSignals` und `gehirn-abholer` mit
    `todo:get,todo:plannerSignals,todo:addSuggestions`, sonst unverändert. Dieser Schritt liest nur und ändert nichts.
14. **Eigenes Ja des Betreibers:** Abbild mit neuem Tag bauen, auf dem Mini-PC:
    `AUFTRAGSBUCH_TAG=<Kurz-SHA-2> docker compose build auftragsbuch`. Der laufende Dienst läuft mit `<AUSGANG-TAG>`
    weiter; der Abholer nutzt dasselbe Abbild und wird nie gezogen (`pull_policy: never`).
15. **Eigenes Ja des Betreibers:** DSN des Dienstes für das Host-Netz, auf dem Mini-PC:
    `python3 deploy/db_zugang.py dsn-ort`. Der Befehl schreibt in `laufzeit/dsn` nur Host und Port aus Schritt 8 um,
    in einem Zug und mit Modus 600; Benutzer, Passwort und Datenbank bleiben, die Datenbank selbst wird nicht
    angefasst und nichts wird ausgegeben außer einer Zeile ohne Werte. Fehlt die Kopie aus Schritt 3, legt er
    `laufzeit/dsn.vor-stufe-4` vorher an; eine vorhandene Kopie bleibt unverändert. Der laufende Dienst liest die DSN
    erst bei seinem nächsten Start und arbeitet bis Schritt 16 unverändert weiter.
16. **Eigenes Ja des Betreibers:** Dienst `auftragsbuch` neu starten, im Host-Netz, auf dem Mini-PC:
    `AUFTRAGSBUCH_TAG=<Kurz-SHA-2> docker compose up -d --no-deps auftragsbuch`. Voraussetzung: Die Dateien aus
    Schritt 11 bis 13 sind da (fehlt eine eingebundene Datei, legte Docker an ihrer Stelle einen Ordner an). Prüfen:
    die beiden `curl` aus Stufe 2, Schritt 6 liefern `200` und `401`; `ss -ltn 'sport = :8172'` zeigt nur
    `127.0.0.1:8172` (nie `0.0.0.0` oder `*`); `tailscale serve status` zeigt 8172 unverändert wie in Schritt 1. Endet
    der Start mit `auftragsbuch: Datenbank nicht erreichbar (<Typ>)` oder scheitert eine dieser Prüfungen, anhalten
    und den kurzen Rückweg unten gehen (nur der Dienst zurück auf den Stand von Schritt 1).
17. **Eigenes Ja des Betreibers:** `rowboat-abholer` namentlich starten, auf dem Mini-PC:
    `AUFTRAGSBUCH_TAG=<Kurz-SHA-2> docker compose up -d rowboat-abholer`. Endet er mit
    `rowboat-abholer: <Meldung>` (z. B. `ABHOLER_LADEN fehlt` oder `Datenbank nicht erreichbar (<Typ>)`), anhalten.
18. **Rauchprobe** auf dem Mini-PC, ohne Auftrag: `AUFTRAGSBUCH_TAG=<Kurz-SHA-2> docker compose ps` zeigt beide
    Dienste als `healthy` (der Abholer nach höchstens etwa einer Minute);
    `AUFTRAGSBUCH_TAG=<Kurz-SHA-2> docker compose logs rowboat-abholer` beginnt mit genau einer Zeile
    `abholer=start laden=<LADEN> arten=vorschlaege_anlegen:<acht Zeichen>` und nennt danach nichts (kein Titel, kein
    Link); die acht Zeichen sind der Anfang von `art_hash` der Art im Vertrag von `<commit-2>`.
    `printf '%s\n' "SELECT count(*) FROM auftrag.auftraege WHERE an = 'rowboat';" | <psql>` liefert `0`: Ohne Zeile in
    `auftrag.brain_arten` lehnt die Datenbank jeden Auftrag an Rowboat ab. Dieser Schritt liest nur und ändert nichts.
19. **Eigenes Ja des Betreibers:** Freischaltung, erst hinter der Schwelle: Die Koordination meldet harte Sperre,
    Herkunft und Ergebnistext ohne Personendaten als im Betrieb gemessen, und eigene Messung bestätigt es
    (die Prüfung der Werkzeuge je Agent aus Stufe 3, Schritt 9 nennt keinen offenen Zugang mehr; ein Plan ohne
    Eingangs-Kopf mit Ziel
    `…:rowboat_vorschlaege_anlegen` scheitert mit „Nur aus einer Eingabe des Betreibers“). Erst dann, auf dem Mini-PC:
    `printf '%s\n' "INSERT INTO auftrag.brain_arten (an, art, version) VALUES ('rowboat', 'vorschlaege_anlegen', 2);" | <psql>`.
    Danach liefert `printf '%s\n' 'SELECT an, art, version FROM auftrag.brain_arten ORDER BY 1;' | <psql>` genau
    `brain | probe | 1` und `rowboat | vorschlaege_anlegen | 2`.

**Kurzer Rückweg, falls Schritt 16 scheitert:**

- **Eigenes Ja des Betreibers:** den Bau-Ordner zurück auf `<AUSGANG-TAG>` heben, vom PC aus:
  `git -c core.autocrlf=false archive <AUSGANG-TAG> spaces/__init__.py spaces/auftragsbuch spaces/_contract | ssh <mini-pc> 'tar -x -C ~/auftragsbuch-build'`
  (dann gilt wieder die Compose-Datei mit dem Netz `debian_default`).
- **Eigenes Ja des Betreibers:** die Einstellungen zurück, auf dem Mini-PC:
  `cp -p laufzeit/werte.env.vor-stufe-4 laufzeit/werte.env`.
- **Eigenes Ja des Betreibers:** die DSN des Dienstes zurück, auf dem Mini-PC (dasselbe Passwort, kein Eingriff in
  die Datenbank): `cp -p laufzeit/dsn.vor-stufe-4 laufzeit/dsn`.
- **Eigenes Ja des Betreibers:** nur den Dienst `auftragsbuch` mit dem Ausgangs-Abbild starten, auf dem Mini-PC:
  `AUFTRAGSBUCH_TAG=<AUSGANG-TAG> docker compose up -d --no-deps auftragsbuch`. Prüfen wie in Stufe 2, Schritt 6.

Migration `101`, Rolle und DSN des Abholers und die beiden Schlüssel stören den alten Dienst nicht; sie bleiben, bis
der Fehler geklärt ist, oder gehen mit dem ganzen Rückweg.

**Rückweg:**

- **Eigenes Ja des Betreibers:** Freischaltung zurücknehmen (neue Aufträge an Rowboat lehnt die Datenbank danach mit
  `Unbekannte Auftragsart` ab):
  `printf '%s\n' "DELETE FROM auftrag.brain_arten WHERE an = 'rowboat' AND art = 'vorschlaege_anlegen' AND version = 2;" | <psql>`.
- **Eigenes Ja des Betreibers:** den Abholer anhalten und entfernen, nur ihn:
  `AUFTRAGSBUCH_TAG=<Kurz-SHA-2> docker compose rm -s -f rowboat-abholer`. Ein Auftrag, den er gerade hielt, wird
  nach der Leihdauer `fehler` oder beim Lesen `abgelaufen`.
- **Eigenes Ja des Betreibers:** den Schlüssel des Abholers widerrufen (wirkt sofort, ohne Neustart):
  `docker exec rowboat-headless-rowboat-server-1 node rowboat-server.cjs scoped-key remove gehirn-abholer`, danach
  `rm laufzeit/rowboat-abholer-schluessel`.
- **Eigenes Ja des Betreibers:** die Anmelde-Rolle des Abholers entfernen:
  `printf '%s\n' 'DROP ROLE IF EXISTS rowboat_abholer;' | <psql>`, danach `rm laufzeit/abholer-dsn`.
- **Eigenes Ja des Betreibers:** den Dienst `auftragsbuch` zurück auf den Stand von Schritt 1: die vier Punkte des
  kurzen Rückwegs oben in ihrer Reihenfolge (Bau-Ordner, Einstellungen, DSN aus `laufzeit/dsn.vor-stufe-4`, Start nur
  dieses Dienstes); entfällt, wenn der kurze Rückweg schon gegangen ist.
- **Eigenes Ja des Betreibers:** den Beleg-Schlüssel widerrufen:
  `docker exec rowboat-headless-rowboat-server-1 node rowboat-server.cjs scoped-key remove auftragsbuch-beleg`,
  danach `rm laufzeit/rowboat-beleg-schluessel`.
- **Eigenes Ja des Betreibers:** die vier Hüllen für Rowboat entfernen (ihre Rechte gehen mit):
  `printf '%s\n' 'DROP FUNCTION auftrag.rowboat_empfang_naechster(text, interval), auftrag.rowboat_empfang_abschliessen(uuid, jsonb, text), auftrag.rowboat_empfang_fehler(uuid, text, text), auftrag.rowboat_empfang_bekannt(text, text[]);' | <psql>`.
- **Eigenes Ja des Betreibers:** zuletzt die Rolle `rowboat_empfang`:
  `printf '%s\n' 'REVOKE USAGE ON SCHEMA auftrag FROM rowboat_empfang; DROP ROLE rowboat_empfang;' | <psql>`.

Kern (`auftrag._empfang_*`), Sales-Hüllen, `auftrag._vergeben` und `auftrag.brain_lesen` aus `101` bleiben: Sales
holt seine Aufträge dort mit demselben Verhalten wie vorher, und Aufträge des Gehirns vergibt der alte Weg nie. Ein
Zurück auf den Stand von `100` bräuchte die Datei der Sales-Sitzung und ist hier nicht vorgesehen. Die Dateien unter
`~/backups/`, `laufzeit/werte.env.vor-stufe-4` und `laufzeit/dsn.vor-stufe-4` bleiben liegen.

## Offene Punkte für Stufe 2

- Herkunft erzwingen (Spec 8.3), bevor die erste echte Auftragsart freigeschaltet wird.
- Kein anderer OpenFang-Agent sieht `mcp_auftragsbuch_*`: eine harte Sperre statt der Prüfung in Stufe 3, Schritt 9.
- Rechteträger ohne startbares Modell (heute trägt `brain-sales` Modell, Ablehnungs-Prompt und 2000 Tokens je Stunde).
- Das Gehirn weigert sich, einem Rechteträger eine Nachricht zu schicken.
- `auftrag.naechster(p_an)` kennt keine Arten: Vor einer `brain_arten`-Zeile für einen Empfänger braucht es dort eine
  Sperre.
- Rechteträger `brain-rowboat`.
- Übergänge `in_arbeit → fertig | fehler` für Empfänger.
- `mcp_discovery` des Gehirns startet die Brücke beim Hochfahren selbst.
