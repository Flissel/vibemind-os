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

**N7 — der Strukturfix.** Dem Agenten den Einmal-Token gar nicht erst in die
Hand geben, sondern die URL außerhalb seines Kontexts zustellen. Siehe den
Satz ganz oben, warum das kein Blocker ist.

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

## Was am Zweig ausdrücklich NICHT gemessen wurde

Kein containerseitiger Aufruf (es lief keiner, und der geteilte Stack war
tabu) — die Aussage „`host.docker.internal` endet als `127.0.0.1`, die
Loopback-Wache trennt den Container also nicht vom Host" ist aus einer
früheren Messung dieses Zweigs übernommen, nicht erneut geprüft. Kein echter
OAuth-Umlauf gegen einen Anbieter. Keine echte OpenFang-Übergabe im
Live-Test. Keine Prüfung, ob ein laufender Container `config/openclaw.json`
wirklich lädt. `deploy/smoke.sh` und `anbinden.sh` wurden nicht ausgeführt,
nur syntaktisch geprüft.
