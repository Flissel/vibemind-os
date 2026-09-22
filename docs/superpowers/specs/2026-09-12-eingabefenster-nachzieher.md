# Eingabefenster — benannte Nachzieher

Stand 2026-09-12, nach dem Schluss-Review des Zweigs `1cfa5278..72f5e04a`
(18 Commits, `142 passed, 1 skipped`).

Das Schluss-Review hat den Zweig für abschließbar erklärt, **ohne kritische
Befunde**. Jede tragende Senke wurde mutiert statt gelesen — fünfzehn
Mutationen, jede am richtigen Test rot. Vier Befunde waren blockierend und
sind in `72f5e04a` behoben (W1, W2, W7-Anzeige, G2).

Diese Datei hält fest, was bewusst **nicht** behoben wurde. Sie existiert,
weil der Plan-Arbeitsbereich (`.superpowers/sdd/…`) gitignoriert ist und beim
Abschluss gelöscht wird — ohne sie wäre das hier der einzige Ort gewesen, an
dem ein echter vorbestehender Fehler notiert war, und er wäre mit dem
Verzeichnis verschwunden.

## Der Satz, der über den aufgeschobenen Strukturfix entscheidet

Das Restrisiko daraus, dass der Agent heute den Einmal-Token in die Hand
bekommt, ist Credential-**Substitution**, nicht Credential-**Offenlegung**:
auf keinem Pfad erfährt der Agent den Wert des Betreibers. Deshalb ist die
Zustellung der URL an ihm vorbei ein Nachzieher und kein Blocker. Vorher war
das eine Hoffnung; seit dem Schluss-Review ist es ein gemessenes Argument.

## Echte Fehler mit Betriebsfolgen

**N1 — behoben (`588384f8`, 2026-09-15).** `urllib.request.urlopen` wirft
für jeden Status ≥ 400 einen `HTTPError`, statt ihn zurückzugeben. Folge
war: in `werkzeuge._openfang_uebernehmen` war der `status != 200`-Pfad und
damit der gesamte 409-/`erfordert_betreiber_entscheidung`-Block toter Code.
Gemessen gegen einen lokalen Stellvertreter ergab ein echter 409:
`{"status": 0, "fehler": "OpenFang nicht erreichbar (HTTPError: HTTP Error 409: Conflict)"}`
— der Agent bekam `retryable: True` und wiederholte endlos, also genau das,
was der Fix jener Runde verhindern sollte. Dasselbe galt für `_rowboat`
(mit 403 gemessen).

Fix sitzt an EINER Stelle, `werkzeuge._roh_anfrage`: sie fängt jetzt genau
`urllib.error.HTTPError` (nicht `URLError` ohne Status — DNS/Verbindung/
Timeout fliegen bewusst weiter) und gibt sie wie jede andere Antwort als
`(status, rumpf)` zurück. Damit sind die Statuszweige beider Aufrufer
erreichbar, ohne dass ein Aufrufer sich ändern musste. Ein echter 409
ergibt jetzt `status: 409`, `erfordert_betreiber_entscheidung: True`,
`retryable: False`. Sicherheitsauflage eingehalten:
`_openfang_uebernehmen`s Fehlertext bleibt bei `f"OpenFang HTTP {status}"`
ohne Antwortkörper (der einzige Aufruf, dem ein Credential-WERT im
Anfragekörper mitgegeben wird); `_rowboat`s Körper darf weiterhin in den
Fehlertext, bleibt aber durch `_ohne_schluessel` gescrubt — dafür jetzt
extra getestet. Sechs neue Tests in `tests/test_werkzeuge.py`
(142 → 148 passed, 1 skipped); die Mutationsprobe (Fang entfernt) wurde
real ausgeführt und fiel mit `status: 0` / `retryable: True` rot.

**N2 — behoben (`12a89a93`, 2026-09-15).** Fester OAuth-Callback-Port 8976
gegen die Threadpool-Auslagerung. Seit beide Schreibwege im Threadpool
laufen, können zwei OAuth-Flüsse gleichzeitig laufen. Gemessen auf diesem
Host: `HTTPServer.allow_reuse_address` ist (geerbt) `1`, ein zweites Bind
auf denselben Port **gelang**, die Zustellung war undefiniert. Der
`state`-Parameter verhinderte, dass ein Code beim falschen Fluss landet
(also kein Leck), aber der richtige Fluss hing bis zu 300 Sekunden mit
bereits verbranntem Token, still. Die Nebenläufigkeits-Analyse dieses
Zweigs zählte Modulzustand auf und übersah das Betriebsmittel daneben —
das war eine echte Lücke in der Analyse, nicht nur im Code.

Fix 1: `bind_callback_server()` bindet auf Port 0 (das Betriebssystem
vergibt einen freien) **vor** der RFC-7591-Registrierung, statt einen
Port zu raten; `redirect_uri` wird aus dem tatsächlich gebundenen Port
(`server.server_address[1]`) abgeleitet und an Registrierung,
`authorize_url` und Token-Tausch durchgereicht. `--callback-port N` ist
das Ventil für den seltenen Anbieter mit vorregistrierter fester
Redirect-URI; `--dry-run` geht denselben Bind-vor-Registrierung-Weg, Rest
der Ausgabe bleibt byte-genau bis auf die Portnummer in der URL.

Fix 2: `allow_reuse_address = False` auf der Serverklasse. GEMESSEN
(nicht angenommen) auf diesem Windows-Host: ein zweiter Bind auf einen
bereits belegten FESTEN Port scheitert damit tatsächlich mit `OSError`
(`WinError 10048`, „Only one usage of each socket address is normally
permitted") — anders als befürchtet macht `SO_REUSEADDR`s
Windows-Eigenheit den Fix hier NICHT wirkungslos; kein Ausweichen auf
`SO_EXCLUSIVEADDRUSE` nötig.

`token_holen(mcp_url) -> tuple[str, str]` (die Naht, die `fenster.py` via
`server.py` benutzt) bleibt signaturunverändert. Vier neue Tests in
`tests/test_provisioner_callback_port.py` (155 → 159 passed, 1 skipped);
die Mutationsprobe für „zwei gleichzeitig gebundene Server bekommen
verschiedene Ports" wurde real ausgeführt (Port wieder fest auf 8976
gesetzt) und fiel rot: `OSError: [WinError 10048]` beim zweiten Bind.

Dazu, weil derselbe Testbereich: `anfragen._OFFEN` ist geteilter
Prozesszustand über die ganze Testsitzung; die N3-Schweben-Wache macht
das erstmals beobachtbar (ein Test in `test_werkzeuge.py` lässt bewusst
einen offenen, nie verbrauchten Eintrag für `PYTEST_SCHWEBEND_A` liegen).
Eine neue `autouse`-Fixture in `tests/conftest.py` leert `_OFFEN` vor und
nach jedem Test. Geprüft in beiden Dateireihenfolgen (normal und
umgekehrt) — beide grün; mit der Fixture testweise deaktiviert blieb die
umgekehrte Reihenfolge in diesem Suite-Stand ebenfalls grün, weil aktuell
kein anderer Test dieselbe `referenz` wiederverwendet. Die Zeitbombe ist
damit real (belegter Leck-Fall), aber in der heutigen Testdaten-Kombination
noch nicht scharf — die Fixture ist vorbeugend, kein Beleg für einen
beobachteten Fehlschlag.

**N3 — behoben (`6c217efc`, 2026-09-15).** `eingabe_anfordern` bewachte
jeden bekannten DB-Zustand, aber keine offene schwebende Anfrage. Gemessen:
zwei Aufrufe ergaben zwei lebende Token; die zweite Abgabe landete als roher
Postgres-Constraint-Fehler auf der Betreiberseite. Fail-closed, aber genau
die Klasse „ein Link, der garantiert kollidiert", gegen die die
Kollisionswache geschrieben wurde.

Fix: eine neue Wache VOR dem Anlegen (nach den bestehenden DB-Wachen, vor
`anfragen.anlegen`), die `anfragen.offen_fuer(referenz)` prüft und ablehnt,
solange schon ein nicht abgelaufenes Token für dieselbe `referenz`
existiert — mit der Ablaufzeit im Fehlertext. Entscheidung (wie im Brief
vorgeschlagen): ablehnen statt denselben Link erneut auszugeben, denn zwei
Wege zu einem Geheimnis sind einer zu viel, und der bestehende Link bleibt
ohnehin gültig und im Transkript des Agenten auffindbar — ein zweiter wäre
reine Verdopplung ohne Nutzen. Test + Mutationsprobe (Wache deaktiviert,
real rot: der zweite Aufruf lieferte `ok: True` statt der erwarteten
Ablehnung) in `tests/test_werkzeuge.py`.

## Ungenauigkeiten, die bleiben

**N4 — behoben (`b7794373`, 2026-09-15).** Ursprünglicher Befund: der
409-Fall trug weiter den Normalfall-Text.
`erfordert_betreiber_entscheidung` läuft in `fenster.ergebnisseite` unter
„vom Anbieter abgelehnt", was dort ebenfalls nicht zutrifft. Beim Zuschnitt
des Schluss-Fixes auf zwei Seiten übersehen; vom Implementierer gemeldet
statt stillschweigend mitgemacht. Hängt an N1 — solange der Block
unerreichbar ist, ist der Text ohnehin toter Pfad.

**Nachtrag (N1-Fix `588384f8`, 2026-09-15): N4 ist damit keine theoretische
Ungenauigkeit mehr, sondern eine echte.** Vor dem N1-Fix lief der
409-/`erfordert_betreiber_entscheidung`-Block nie in Produktion (der Status
kam nie über `status: 0` hinaus), also war der falsche Text in
`fenster.ergebnisseite` toter Pfad. N1 macht genau diesen Pfad ERSTMALS
erreichbar — ein Betreiber, der heute einen echten 409 bekommt, sieht
`erfordert_betreiber_entscheidung: True` mit einem Hinweistext, der die
Betreiber-Entscheidung nennt, aber `fenster.ergebnisseite` zeigt ihm
weiterhin den Normalfall-Text „vom Anbieter abgelehnt" dazu. (Zum
Zeitpunkt des N1-Fixes blieb N4 noch offen — der damalige Task hatte
ausschließlich N1 behoben, N4 nicht angefasst.)

**Nachtrag (N4-Fix `b7794373`, 2026-09-15): N4 ist behoben.**
`fenster.ergebnisseite` unterscheidet jetzt drei Fälle statt zwei
(`ok`/`zwei_verwahrstellen`/`erfordert_betreiber_entscheidung`), durchgereicht
von beiden Aufrufern (`entgegennehmen`, `oauth_entgegennehmen`), nach
demselben Muster wie `zwei_verwahrstellen`. Der Text nennt jetzt die
Kollision bei OpenFang statt einer Ablehnung durch den Anbieter, und die
nötige Betreiber-Entscheidung statt eines nahegelegten Retry. Test +
Mutationsprobe (dritter Fall wieder mit dem Normalfall zusammengelegt,
real rot: „vom Anbieter abgelehnt ... Der Agent kann eine neue Eingabe
anfordern" erschien statt des korrekten Hinweises) in
`tests/test_fenster.py`, für beide Aufrufer.

**N5 — behoben (`ca365916`, 2026-09-15, nur Dokumentation).** Ursprünglicher
Befund: die „siebte Prüfstelle" in `E2E-PROOF.md` Teil VI deckte ihre eigene
Klasse nicht ab. Der Live-Test prüft das Postgres-Containerlog, blieb aber
bei einem vollen Rollback des C1-Fixes **grün**, weil der Glücksfall gar kein
scheiterndes Statement erzeugt. Die Klasse *ist* abgedeckt — von
`tests/test_ablage.py::test_wert_landet_nie_im_postgres_server_log`, das Teil
VI nie zitierte. Die Ergänzung war richtig gemeint und falsch platziert. Dazu
ein Zählfehler aus dem Plan: „sieben Stellen" zählte `status ==
"fehlgeschlagen"` mit, was keine Stelle ist, sondern eine Zusicherung.

Fix: `E2E-PROOF.md` §VI.3 (und die Kopfzeile §VI.1) umgeschrieben auf die
richtige Zahl — sechs Stellen plus eine Zusicherung, nicht sieben Stellen.
Zwei explizite Korrekturen ergänzt: (a) der Live-Test-Check selbst deckt
die C1-Klasse NICHT ab (sein Szenario löst nie das scheiternde Statement
aus, das den Wert überhaupt erst in den Log brächte — bei einem vollen
C1-Rollback bliebe genau dieser Test grün), und (b)
`tests/test_ablage.py::test_wert_landet_nie_im_postgres_server_log` wird
jetzt als der Test genannt, der die Klasse tatsächlich abdeckt (echte
referenz_name-Kollision, eigene Positiv-Kontrolle). Keine Schönfärberei:
die Korrektur benennt explizit, dass Teil VI's eigener Live-Test schwächer
ist, als er sich liest.

**N6 — `_ziel_pruefen` bindet nur den Host von `ziel`, nie `issuer` oder
`authorization_endpoint`.** Beide kommen aus den Metadaten des
agentengewählten Ziels. Der Schluss-Fix zeigt dem Betreiber jetzt die
Adresse, bevor er klickt — das ist informierte Zustimmung, nicht Bindung.
Die Bindung selbst ist ein echter Entwurfseingriff mit Folgekosten
(legitime Anbieter delegieren die Anmeldung regelmäßig an einen anderen
Host) und braucht eine eigene Runde.

**N7 — behoben (2026-09-22, "Listen-Seite am Sidecar").** Der Strukturfix:
`eingabe_anfordern` gibt seit diesem Fix kein `url`-Feld mehr zurück, nur
noch `hinweis` mit der festen, tokenlosen Adresse einer neuen Listen-Seite
(`GET /anfragen`, `server._anfragen_zeigen`, `fenster.listenseite`,
`anfragen.alle_offenen`). Der Mensch öffnet diese Adresse selbst in seinem
eigenen Browser (hinter derselben Loopback-Wache wie die Formular-Routen),
findet dort jede offene Anfrage mit ihrem echten `/fenster/{token}`-Link,
und klickt sich von dort weiter. Der Agent sieht den Einmal-Link damit
strukturell nie — vorher war das eine Konfigurationsfrage (Tool-Policy des
Agenten, s. `server.py`-Moduldoku), jetzt eine, die die Antwortform selbst
nicht mehr hergibt.

Damit ändert sich auch die Einordnung im Satz ganz oben: das
Substitutions-Restrisiko (der Agent hält den Token und *könnte* ihn
missbrauchen) entfällt für den Normalfall vollständig, weil der Token die
Werkzeug-Antwort nie erreicht. Es bleibt nur, was schon vorher galt und
strukturell nicht anders lösbar ist: der Agent kann dem Betreiber eine
falsche Referenz oder ein falsches `ziel` NENNEN (Social Engineering über
den Chat, nicht über einen gestohlenen Link) — dagegen schützt weiterhin
nur, dass der Betreiber Referenzname und Ziel-Adresse selbst liest, bevor
er einträgt (E5/E6 der Spec).

Live bewiesen (`tests/test_fenster_live.py`): die Werkzeug-Antwort trägt
kein `url`-Feld mehr, und der echte `/fenster/{token}`-Link kommt
ausschließlich von der Listen-Seite, gescrapt wie ein Mensch es täte.
`AGENTS.md`/`README.md` sind auf den neuen Vertrag nachgezogen (der Agent
nennt nur noch die feste Listen-Seiten-Adresse, nie einen Link).

## Kleines

- **N8 — behoben (`7de911ec`, 2026-09-15).** `run_in_threadpool` war nur auf
  dem **oauth**-Pfad festgenagelt; die Mutation auf dem bearer-Pfad ließ alle
  Tests grün. Neue Probe in `tests/test_server_formularrouten.py`, nach dem
  Muster der bestehenden oauth-Probe (blockierender `schreiber`-Ersatz, GET
  und POST nebenläufig per `asyncio.gather`, Beweis ist die
  Fertigstellungsreihenfolge). Mutationsprobe real ausgeführt und rot
  gewesen (Reihenfolge kippte zu `["post", "get"]`, GET brauchte 5s statt
  Millisekunden), danach zurückgenommen.
- **N9 — behoben (`2cd70a14`, 2026-09-15, nur Dokumentation).** README und
  `bootstrap.sh` sprachen von „0001-0003", angewandt sind fünf Migrationen.
  Geprüft: `bootstrap.sh`s Schleife wandte schon immer alle fünf Dateien an
  — reiner Textfehler in Kommentar/README, keine Funktionslücke im Skript.
  Beide Stellen jetzt auf „0001-0005" korrigiert.
- **N10 — behoben (`7de911ec`, 2026-09-15).** `server.py` scheiterte beim
  Import hart ohne `spaces/rowboat/`, mit einem nackten `FileNotFoundError`
  auf den rohen Pfad. Verhalten bleibt fail-closed (unverändert) — jetzt mit
  einem expliziten Existenz-Check und einer Meldung, die sagt, was fehlt
  (`spaces/rowboat/`) und warum (der oauth-Zweig braucht den echten
  Provisioner dort, keine Abschrift).
- **N11 — behoben (`6c217efc`, 2026-09-15).** Der ausdrückliche `wert`-Scrub
  in `_openfang_uebernehmen` war ungetestet. Neuer Test in
  `tests/test_werkzeuge.py`: `_roh_anfrage` (nicht `_openfang_uebernehmen`
  selbst) gemockt, wirft eine Ausnahme mit dem Wert im Text; Zusicherung,
  dass der Rückgabetext den Wert nicht, aber `<wert>` enthält. Mutationsprobe
  (Scrub deaktiviert) real rot gewesen, danach zurückgenommen.
- **N12 — behoben (`ca365916`, 2026-09-15, nur Dokumentation).**
  `E2E-PROOF.md` VI.6 zitierte §V.1 für eine Liste, die tatsächlich in der
  unnummerierten Präambel vor §V.0 steht (der „Fix round 1"-Absatz direkt
  unter der Teil-V-Überschrift); §V.1 ist eine andere Liste. Verweis
  korrigiert.
- **N13 — behoben (`6c217efc`, 2026-09-15).** `FENSTER_BASIS` folgte
  `PLUGIN_SETUP_MCP_PORT` nicht — wer den Port umstellte, bekam Links auf
  den alten, still. Der Vorgabewert leitet sich jetzt aus
  `PLUGIN_SETUP_MCP_PORT` ab (demselben Wert, den `server.py` für seinen
  Port liest); ein ausdrücklich gesetztes `PLUGIN_SETUP_FENSTER_BASIS`
  gewinnt weiterhin. Zwei neue Tests in `tests/test_werkzeuge.py` (frischer
  Subprozess, da der Wert einmal beim Modulimport berechnet wird).

## Stand (2026-09-15, Nachzieher-Bündel A)

N1, N2, N3, N4, N5, N8, N9, N10, N11, N12, N13 sind behoben (Commits oben
bei den jeweiligen Punkten). **N6 und N7 bleiben ausdrücklich offen** —
sie waren nicht im Umfang dieses Bündels (Entwurfseingriffe mit
Folgekosten, jeweils eine eigene Runde wert) und wurden nicht angefasst.
Das Bündel ist damit NICHT vollständig.

## Stand (2026-09-22)

**N7 ist jetzt ebenfalls behoben** (eigener Abschnitt oben, "Listen-Seite
am Sidecar"). **N6 bleibt weiterhin offen** — die Bindung von `_ziel_pruefen`
an `issuer`/`authorization_endpoint` der Anbieter-Metadaten ist ein
eigener Entwurfseingriff mit Folgekosten und war nicht Teil dieser Runde.

## Was am Zweig ausdrücklich NICHT gemessen wurde

Kein containerseitiger Aufruf (es lief keiner, und der geteilte Stack war
tabu) — die Aussage „`host.docker.internal` endet als `127.0.0.1`, die
Loopback-Wache trennt den Container also nicht vom Host" ist aus einer
früheren Messung dieses Zweigs übernommen, nicht erneut geprüft. Kein echter
OAuth-Umlauf gegen einen Anbieter. Keine echte OpenFang-Übergabe im
Live-Test. Keine Prüfung, ob ein laufender Container `config/openclaw.json`
wirklich lädt. `deploy/smoke.sh` und `anbinden.sh` wurden nicht ausgeführt,
nur syntaktisch geprüft.

## Nachtrag (2026-09-16) — die Sackgasse, die erst der erste ECHTE Durchgang fand

Am 16.09.2026 ist zum ersten Mal ein echtes Credential durch das
Eingabefenster gegangen (ein GitHub-PAT, vom Betreiber eingetippt, GitHub
hat mit 200 bestätigt, OpenFang hat übernommen, die Supabase-Kopie wurde
gelöscht). Auf dem Weg dorthin hat dieser Lauf einen Entwurfsfehler
freigelegt, den keine der bisherigen Test-Runden (auch nicht die oben
dokumentierten N1-N13) gefunden hat — weil alle bisherigen Tests die
ANTWORTFORM eines OpenFang-409 prüften (Statuscode, `retryable`,
Fehlertext), nicht, wie ein Betreiber danach weiterkommt.

**Der Befund:** `verifiziert` war beim Entwurf (0002/0005) als
DURCHGANGS-Zustand gedacht — die Erwartung war, dass er binnen Sekunden in
`uebernommen` übergeht, sobald OpenFang den Wert entgegennimmt. Lehnt
OpenFang stattdessen ab (409), oder scheitert der `uebernommen()`-Übergang
nach einer erfolgreichen Verifikation, bleibt die Zeile auf `verifiziert`
stehen. Vor diesem Nachtrag gab es von dort keinen Ausgang:
`fehlschlagen()` nimmt nur `entgegengenommen`, `uebernommen()` setzt
voraus, dass OpenFang den Wert tatsächlich hat, `neu_aufnehmen()` nahm nur
`fehlgeschlagen`, und `eingabe_anfordern` lehnte jeden bekannten Zustand
außer `fehlgeschlagen` ab. Der Betreiber kam ohne Handgriff in der
Datenbank nicht weiter — gemessen im Live-Lauf, zweimal.

**Warum das ein Entwurfsfehler ist, kein Implementierungsfehler:** jede
einzelne Wache tat exakt, was sie sollte, und jeder bestehende Test blieb
grün, weil er genau das prüfte, was er sollte. Die Lücke lag zwischen den
Bausteinen — im Zustandsdiagramm selbst hatte `verifiziert` einen
Ausgangspfeil zu wenig. Das findet keine Mutationsprobe eines einzelnen
Bausteins, weil an keinem einzelnen Baustein etwas falsch war; es
brauchte einen echten Durchlauf, der den Automaten tatsächlich in diesen
Zustand brachte und dann versuchte, von dort weiterzukommen.

**Fix:** `neu_aufnehmen(referenz)` akzeptiert seit
`db/0006_neu_aufnehmen_ab_verifiziert.sql` zusätzlich `verifiziert` als
Ausgangszustand (dieselbe Löschen-und-neu-Semantik wie beim bestehenden
`fehlgeschlagen`-Pfad), und `werkzeuge.eingabe_anfordern` behandelt
`verifiziert` jetzt wie `fehlgeschlagen`: ein neuer Link wird ausgegeben,
nachdem die alte Zeile freigegeben wurde. `entgegengenommen` und
`uebernommen` bleiben unverändert ohne Ausweg — das sind die beiden
Zustände, bei denen ein automatischer Neustart tatsächlich etwas
verwerfen würde (eine laufende Aufnahme bzw. eine fertige Einrichtung).

Details, die zwei Unterfälle (OpenFang hat abgelehnt vs. OpenFang hat
angenommen aber der Supabase-Übergang scheiterte) und warum keine direkte
Kante `verifiziert → fehlgeschlagen` gewählt wurde, stehen im
Migrations-Kopfkommentar von `db/0006_neu_aufnehmen_ab_verifiziert.sql`
und im Docstring von `werkzeuge.eingabe_anfordern`.
