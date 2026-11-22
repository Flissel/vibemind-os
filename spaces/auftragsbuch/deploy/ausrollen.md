# Auftragsbuch ausrollen (Plan-Tasks 8 bis 10)

Schrittfolge mit Rückweg. Jeder Schritt, der auf dem Mini-PC, an der Datenbank, an OpenFang (Konfiguration,
Agenten-Ordner, Neustart), am Betriebs-Checkout, am Gehirn (`brain-core`) oder an `master` etwas ändert, startet
oder stoppt, trägt den Marker **Eigenes Ja des Betreibers:** und braucht sein eigenes Ja. Das gilt auch für jede
Aktion eines Rückwegs. Ein Ja für eine Stufe oder für einen anderen Schritt gilt nicht. Reine Lese- und
Prüfschritte (Probe in `ROLLBACK`, `\ddp`, Rauchproben, `GET`) brauchen keinen Marker.
Das Repo ist öffentlich: Diese Anleitung nennt keine Adressen, Namen oder Schlüssel; sie stehen nur auf den
Rechnern selbst. Platzhalter stehen in `<…>`.

Beteiligte Orte:

- **Mini-PC** (kleiner Server): Supabase-PostgreSQL im Docker-Netz `debian_default`, später der Dienst.
- **PC** (Desktop): OpenFang, Gehirn, Wurzel-`.env` des äußeren Repos.
- Der Weg PC → Mini-PC ist ein privates Netz (`tailscale serve` auf Port 8172, wie beim Event-Dienst auf 8171).

Compose liest `AUFTRAGSBUCH_TAG` bei **jedem** Befehl; ohne Wert bricht schon `down` ab. Darum steht
in dieser Anleitung vor jedem Compose-Befehl die Zuweisung `AUFTRAGSBUCH_TAG=<Tag>`: bei `up` der neue Tag
(Git-Kurz-SHA), bei allen anderen Befehlen (`ps`, `logs`, `down`) der laufende Tag
(`docker ps --filter name=auftragsbuch --format '{{.Image}}'` zeigt ihn).

Vorab, für alle Stufen: Claim in `002_Koordination_Live.md` (Mini-PC, Supabase, Schema `auftrag`) setzen und
sofort committen; bei fremdem aktivem Claim nichts anfassen und nachfragen.

## Stufe 1 (Plan-Task 8): Migration `100` einspielen

Die Migration legt zwei Spalten, eine Tabelle, drei Funktionen und eine Rolle an und erweitert einen CHECK. Sie ist
rein zusätzlich.

1. **Sicherung** des Schemas `auftrag` nach dem Muster der bisherigen Sicherungen unter `~/backups/`.
2. **Standard-Rechte prüfen:** In `psql` mit `\ddp` nachsehen, ob Standard-Rechte neuen Tabellen im Schema
   `auftrag` Rechte für `anon`, `authenticated` oder `sales_app` geben. Wenn ja: anhalten und klären, bevor
   eingespielt wird (die neue Tabelle `auftrag.brain_arten` darf diese Rollen nichts geben).
3. **Probe-Lauf auf der echten Datenbank:** `100` und `verify_100` in **einer** Transaktion mit `ROLLBACK`
   (`migration_probe`). Die Probe ändert nichts, läuft vor dem Einspielen und ist vom Ja zum Einspielen
   (Schritt 4) mit abgedeckt. Voraussetzung: `096` bis `099` sind eingespielt. Erwartung: kein Fehler. Bei einem
   Fehler: anhalten, nichts einspielen.
4. **Eigenes Ja des Betreibers:** `100_auftragsbuch_brain.sql` einspielen. `098` wird danach **nicht** erneut
   eingespielt: Sein CHECK auf `art` lehnt Zeilen des Gehirns ab.
5. **Prüfen:** `SELECT an, art, version FROM auftrag.brain_arten` liefert genau `brain | probe | 1`. Der
   Dienst `auftrag-bote` läuft weiter und sein Herzschlag ist frisch.
6. **Eigenes Ja des Betreibers:** Rolle `brain_app`: Anmeldung und Passwort setzen, das Passwort nur verdeckt per stdin, nie auf der
   Kommandozeile (Muster `spaces/event_management/deploy/pg_hilfe.py`). Das Passwort wird dabei per Skript erzeugt
   und nicht angezeigt; für Stufe 2 liegt es als DSN-Zeile bereit, ohne je sichtbar zu werden.

**Rückweg:**

- **Eigenes Ja des Betreibers:** `DROP FUNCTION` für die drei Funktionen.
- **Eigenes Ja des Betreibers:** `DROP TABLE auftrag.brain_arten`.
- **Eigenes Ja des Betreibers:** den alten CHECK `art IN ('reel', 'video')` wiederherstellen (nur möglich, solange keine Zeile mit
  `von = 'brain'` existiert).
- **Eigenes Ja des Betreibers:** `DROP ROLE brain_app`.

Die zwei Spalten bleiben.

## Stufe 2 (Plan-Task 9): Dienst auf dem Mini-PC starten

Neu ist ein Container `auftragsbuch` in einem eigenen Compose-Projekt; kein anderer Dienst wird neu gestartet.

1. **Eigenes Ja des Betreibers:** Code auf den Mini-PC bringen (Checkout des Zweigs im Ordner des Betreibers); der Dienst liegt in
   `<ordner>/spaces/auftragsbuch`.
2. **Eigenes Ja des Betreibers:** Schlüssel und DSN verdeckt ablegen, derselbe Schlüssel auf beiden Rechnern. Der Schlüssel entsteht am PC
   und wird nie angezeigt. Ein Skript am PC (Beispiel: `bash` oder `python`) tut alles in einem Lauf, ohne dass
   ein Wert auf dem Bildschirm, in der Prozessliste oder in der Shell-Historie steht:
   - Es erzeugt den Schlüssel in eine Shell-Variable (z. B. `SCHLUESSEL=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')`).
   - Es hängt `AUFTRAGSBUCH_SCHLUESSEL=…` an die Wurzel-`.env` des äußeren Repos am PC an (per Redirect, nicht in
     den OpenFang-Tresor, nicht in eine Datei im Repo). Dort kommt in Stufe 3 auch `AUFTRAGSBUCH_URL=…` hinzu.
   - Die DSN von `brain_app` stammt aus dem verdeckten Weg von Stufe 1, Schritt 6 (Variable `DSN`, nie ausgegeben).
   - Es schickt beide Werte über stdin an den Mini-PC:
     `printf '%s\n%s\n' "$DSN" "$SCHLUESSEL" | ssh <mini-pc> 'cd <ordner>/spaces/auftragsbuch && bash deploy/anbinden.sh'`.
     `anbinden.sh` legt `laufzeit/` (Modus 700) an und schreibt `laufzeit/dsn` sowie nur den sha256-Hash des
     Schlüssels nach `laufzeit/schluessel-hash`.
   - Zuletzt `unset DSN SCHLUESSEL`.
3. **Eigenes Ja des Betreibers:** `laufzeit/werte.env` auf dem Mini-PC aus `deploy/werte.beispiel.env` füllen (`AUFTRAGSBUCH_LADEN`,
   `AUFTRAGSBUCH_MANDANT`, `AUFTRAGSBUCH_ZUSATZ_HOSTS` mit dem Namen, unter dem der PC den Dienst aufruft).
4. **Eigenes Ja des Betreibers:** Image bauen und starten, nur im Ordner `<ordner>/spaces/auftragsbuch`:
   `AUFTRAGSBUCH_TAG=<Git-Kurz-SHA> docker compose up -d --build`. Gehören die Dateien in `laufzeit/` nicht der
   UID 1000, vorher `AUFTRAGSBUCH_UID` und `AUFTRAGSBUCH_GID` auf deren Besitzer setzen (`id -u`, `id -g`). Der Port
   ist nur an `127.0.0.1` des Mini-PC gebunden.
5. **Eigenes Ja des Betreibers:** Netzweg einrichten wie in Task 0 gemessen: `tailscale serve` auf Port 8172 zum lokalen Port.
6. **Rauchprobe vom PC** (Adresse und Schlüssel aus der Wurzel-`.env`, nichts davon in die Shell-Historie):
   - `GET /gesund` ohne Schlüssel: HTTP 200.
   - MCP-Weg ohne Schlüssel: HTTP 401.
   - mit Schlüssel `tools/list`: genau `auftrag_probe`, `auftrag_lesen`, `rowboat_vorschlaege_anlegen`,
     `sales_post_pruefen`.
   - `tools/call sales_post_pruefen` mit gültiger Eingabe: Fehler `Unbekannte Auftragsart`, und in
     `auftrag.auftraege` ist keine Zeile entstanden.
   - `AUFTRAGSBUCH_TAG=<laufender Tag> docker compose logs auftragsbuch` nennt nur Werkzeugname, Ergebnis und Dauer.

**Rückweg:**

- **Eigenes Ja des Betreibers:** `AUFTRAGSBUCH_TAG=<laufender Tag> docker compose down` im Ordner des Dienstes.
- **Eigenes Ja des Betreibers:** den Netzweg entfernen (`tailscale serve` für Port 8172 abschalten).
- **Eigenes Ja des Betreibers:** den Schlüssel am PC aus der Wurzel-`.env` entfernen; `laufzeit/` auf dem Mini-PC kann bleiben oder gelöscht
  werden.

## Stufe 3 (Plan-Task 10): OpenFang und Gehirn anschließen

Jede der folgenden Handlungen braucht ihr eigenes Ja, jeweils vor dem Schritt, in dem sie steht.

1. **Eigenes Ja des Betreibers:** einmal je Merge, (a) Merge `cc/auftragsvertrag` nach `master` (vibemind-os) samt
   Verweis im äußeren Repo, danach (b) Merge des Manifests im Submodul `openfang`. Wie am 21.11. mergen und
   Verweise nachziehen (`docs/superpowers/specs/2026-11-20-zurueck-ideen-bubbles-design.md`, 7h und 7k). Den
   Betriebs-Checkout auf den neuen `master` heben.
2. **Eigenes Ja des Betreibers:** Eintrag des Werkzeug-Servers (stdio). OpenFang lädt Werkzeug-Server nur aus
   `openfang/openfang.vibemind.toml`, und diese Datei liegt im öffentlichen Repo. Darum bekommt sie den
   stdio-Eintrag aus `deploy/openfang-eintrag.beispiel.toml`, **ohne** Adresse und ohne Schlüssel. Die
   Platzhalter für Pfade werden am PC eingesetzt. Die Adresse (`AUFTRAGSBUCH_URL`) und der Schlüssel
   (`AUFTRAGSBUCH_SCHLUESSEL`) stehen in der Wurzel-`.env` des PC; `scripts/mcp_mit_env.py` reicht sie der Brücke
   `bruecke.py`, die jeden Aufruf an den Dienst weiterleitet (ohne Proxy, ohne Weiterleitung). Dasselbe Muster
   nutzen `spaces-ideas` und `laura`.
3. **Eigenes Ja des Betreibers:** Rechteträger `brain-sales`: Das Manifest wird zusätzlich **von Hand** nach
   `~/.openfang/agents/brain-sales/agent.toml` gelegt (OpenFang lädt Agenten von dort).
4. **Eigenes Ja des Betreibers:** OpenFang neu starten. Prüfen:
   - `GET /api/mcp/servers` zeigt `auftragsbuch` als verbunden mit vier Werkzeugen;
   - `GET /api/agents` zeigt `brain-sales` mit `mcp_servers_mode: allowlist`;
   - die Werkzeug-Liste im Namen von `brain-sales` enthält genau `auftrag_probe` und `auftrag_lesen`.
5. **Eigenes Ja des Betreibers:** `brain-core` mit dem neuen Abbild ausrollen, erst nachdem OpenFang alle
   Werkzeug-Server verbunden hat, wie am 21.11. (dieselbe Spec, 7n und 7r). Lehre aus dem Zwischenfall dort: nie
   gleichzeitig mit dem Neustart von OpenFang.

**Rückweg:**

- **Eigenes Ja des Betreibers:** den Betriebs-Checkout zurück auf `dfc67d54` setzen.
- **Eigenes Ja des Betreibers:** `brain-core` mit dem alten Abbild `vibemind-brain-core:zurueck-dfc67d54` ausrollen.
- **Eigenes Ja des Betreibers:** den Eintrag des Servers aus der OpenFang-Datei entfernen.
- **Eigenes Ja des Betreibers:** den Rechteträger unter `~/.openfang/agents/` entfernen.
- **Eigenes Ja des Betreibers:** OpenFang neu starten (auch der Rückweg-Neustart braucht es).

## Abschluss (Plan-Task 11)

Probe von Anfang bis Ende über das Gehirn: `auftrag_probe`, dann `auftrag_lesen` (`stand == "fertig"`,
`an == "brain"`, `art == "probe"`).

Gegenproben:

- `sales_post_pruefen` ist im Gehirn nicht registriert und bei OpenFang für `brain-sales` nicht freigeschaltet.
- **Eigenes Ja des Betreibers:** Dienst stoppen: `AUFTRAGSBUCH_TAG=<laufender Tag> docker compose stop`.
  Der Plan-Schritt scheitert sofort mit `auftragsbuch nicht erreichbar`.
- **Eigenes Ja des Betreibers:** Dienst wieder starten: `AUFTRAGSBUCH_TAG=<laufender Tag> docker compose start`.
  Prüfen: `GET /gesund` vom PC antwortet wieder mit HTTP 200, und ein `tools/list` mit Schlüssel liefert wieder
  die vier Werkzeuge.

Danach den Vertrags-Hash aus der Probe mit `vertrag_hash(lade_auftragsarten(…))` im Betriebs-Checkout
vergleichen.
