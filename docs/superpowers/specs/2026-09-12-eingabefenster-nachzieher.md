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

**N2 — fester OAuth-Callback-Port 8976 gegen die Threadpool-Auslagerung.**
Seit beide Schreibwege im Threadpool laufen, können zwei OAuth-Flüsse
gleichzeitig laufen. Gemessen auf diesem Host: `HTTPServer.allow_reuse_address`
ist `1`, ein zweites Bind auf denselben Port **gelingt**, die Zustellung ist
undefiniert. Der `state`-Parameter verhindert, dass ein Code beim falschen
Fluss landet (also kein Leck), aber der richtige Fluss hängt 300 Sekunden mit
bereits verbranntem Token. Die Nebenläufigkeits-Analyse dieses Zweigs zählte
Modulzustand auf und übersah das Betriebsmittel daneben — das war eine echte
Lücke in der Analyse, nicht nur im Code.

**N3 — `eingabe_anfordern` bewacht jeden bekannten DB-Zustand, aber keine
offene schwebende Anfrage.** Gemessen: zwei Aufrufe ergeben zwei lebende
Token; die zweite Abgabe landet als roher Postgres-Constraint-Fehler auf der
Betreiberseite. Fail-closed, aber genau die Klasse „ein Link, der garantiert
kollidiert", gegen die die Kollisionswache geschrieben wurde.

## Ungenauigkeiten, die bleiben

**N4 — der 409-Fall trägt weiter den Normalfall-Text.**
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
weiterhin den Normalfall-Text „vom Anbieter abgelehnt" dazu. N4 bleibt
offen — dieser Task hat ausschließlich N1 behoben, N4 nicht angefasst.

**N5 — die „siebte Prüfstelle" in `E2E-PROOF.md` Teil VI deckt ihre eigene
Klasse nicht ab.** Der Live-Test prüft das Postgres-Containerlog, bleibt aber
bei einem vollen Rollback des C1-Fixes **grün**, weil der Glücksfall gar kein
scheiterndes Statement erzeugt. Die Klasse *ist* abgedeckt — von
`tests/test_ablage.py::test_wert_landet_nie_im_postgres_server_log`, das Teil
VI nie zitiert. Die Ergänzung war richtig gemeint und falsch platziert. Dazu
ein Zählfehler aus dem Plan: „sieben Stellen" zählt `status ==
"fehlgeschlagen"` mit, was keine Stelle ist, sondern eine Zusicherung.

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

- **N8** `run_in_threadpool` ist nur auf dem **oauth**-Pfad festgenagelt; die
  Mutation auf dem bearer-Pfad lässt alle Tests grün.
- **N9** README und `bootstrap.sh` sprechen von „0001-0003", angewandt sind
  fünf Migrationen.
- **N10** `server.py` scheitert beim Import hart ohne `spaces/rowboat/`.
- **N11** Der ausdrückliche `wert`-Scrub in `_openfang_uebernehmen` ist
  ungetestet.
- **N12** `E2E-PROOF.md` VI.6 zitiert §V.1 für eine Liste, die in der
  V-Präambel steht.
- **N13** `FENSTER_BASIS` folgt `PLUGIN_SETUP_MCP_PORT` nicht — wer den Port
  umstellt, bekommt Links auf den alten.

## Was am Zweig ausdrücklich NICHT gemessen wurde

Kein containerseitiger Aufruf (es lief keiner, und der geteilte Stack war
tabu) — die Aussage „`host.docker.internal` endet als `127.0.0.1`, die
Loopback-Wache trennt den Container also nicht vom Host" ist aus einer
früheren Messung dieses Zweigs übernommen, nicht erneut geprüft. Kein echter
OAuth-Umlauf gegen einen Anbieter. Keine echte OpenFang-Übergabe im
Live-Test. Keine Prüfung, ob ein laufender Container `config/openclaw.json`
wirklich lädt. `deploy/smoke.sh` und `anbinden.sh` wurden nicht ausgeführt,
nur syntaktisch geprüft.
