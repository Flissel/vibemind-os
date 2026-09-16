# Eine Autorität, zwei Auslieferungswege — Entwurf

Stand 2026-09-16. Ausgelöst von der Frage des Betreibers: *„machen 2
getrennte Welten Sinn?"* — gestellt, nachdem der erste echte Durchgang durch
das Eingabefenster gezeigt hatte, dass ein dort eingerichtetes Credential
außerhalb von Rowboat niemanden erreicht.

**Status: Vorschlag.** Er hängt an einer Entscheidung, die noch nicht
gefallen ist (D1 unten). Ohne sie ist alles Weitere gegenstandslos.

## Die Messung zuerst

Alles hier ist am 16.09.2026 gemessen, nicht aus Dokumentation übernommen.

**Der produktive Daemon kann heute keine Credentials ausgeben.** `:4200`
antwortet auf `POST /api/credentials/issue` und `/api/credentials/store` mit
**404** — er läuft ein Binary, das die Endpunkte nicht kennt. Derselbe
Aufruf gegen den isolierten `:4273` (neues Binary) ergibt **401**, also
„existiert, braucht einen Schlüssel". Zusätzlich beantwortet `:4200`
`GET /api/approvals` **unauthentifiziert** mit 200: er läuft ohne
`api_key`, im fail-open-Modus — und genau darin verweigert der
Credential-Endpunkt grundsätzlich seinen Dienst. Das ist das dokumentierte
Auth-Paradox, hier erneut am laufenden Prozess bestätigt.

**Es gibt genau einen Konsumenten.** Jeder Aufruf von
`/api/credentials/issue` im Repo liegt in `spaces/rowboat` (Resolver der
Plugin-Laufzeit, dessen Tests, die Betriebsdoku). Kein anderer Space, kein
Brain, kein Sidecar.

**Die Dateien sind der zweite Wohnort.** Root-`.env`: 221 Schlüssel.
`~/.openfang/secrets.env`: 10. In **beiden**: 9. Verglichen über
MD5-Prüfsummen, Werte nie gedruckt — **heute keine Abweichung**. Das ist
also kein gegenwärtiger Fehler. Es sind neun Werte, die von Hand an zwei
Orten synchron gehalten werden, ohne dass etwas das erzwingt; die Notiz zur
Drift-Falle existiert, weil es schon einmal auseinanderlief.

**Die Dienste holen sich ihre Schlüssel selbst.** Das Muster
`_load_env_fallback` (Dienst liest beim Start die repo-`.env` für fehlende
Schlüssel nach) findet sich in mindestens acht Dateien quer durch
`spaces/marketing` und in `spaces/plugin-setup` selbst.

## Die These

**Zwei Welten nach Mechanismus: ja. Zwei Welten nach Autorität: nein.**
Heute existiert das Zweite.

Die beiden Mechanismen beantworten verschiedene Fragen, und das soll so
bleiben:

- Eine Umgebungsvariable beim Start heißt: *dieser Dienst ist
  vertrauenswürdig, den Schlüssel für seine Laufzeit zu halten.*
- Eine Ausgabe pro Aufruf gegen eine Freigabe heißt: *dieser
  nichtdeterministische Akteur will einmal etwas Folgenreiches tun.*

Den ersten Fall in den zweiten zu zwingen, kauft nichts. Ein Dienst, der
pro Aufruf ein Credential ausgestellt bekommt, hält es trotzdem die ganze
Zeit im Speicher — Latenz und eine harte Abhängigkeit dazu, Exposition
unverändert. Und OpenFang würde zum Single Point of Failure für den ganzen
Stack. Dass er das heute nicht ist, ist belegt: `:4200` lag vom 13. bis zum
16.09. still, während die 54 Container weiterliefen.

Was **nicht** trägt, ist die doppelte Autorität. Zwei Orte zum Rotieren,
zwei zum Widerrufen, und keine Stelle, die beantwortet: *welche Geheimnisse
existieren und wer darf sie benutzen.*

Bemerkenswert daran: `plugin-setup` setzt genau diese Regel intern schon
durch. D2 der Vorgänger-Spec verbietet zwei Verwahrstellen für EINEN Wert,
`zwei_verwahrstellen` ist der eigene Alarmzustand dafür, und die
409-Kollisionsseite ist nichts anderes als diese Regel, die zuschlägt. Auf
Systemebene wird sie neunmal verletzt.

## Die Form

**Ein Tresor als Autorität, zwei Auslieferungswege daraus.**

**Weg A — Materialisierung beim Start.** Der Launcher liest den Tresor und
setzt die Umgebung für den Dienst, den er hochfährt. Für den Dienst ändert
sich nichts; er sieht weiterhin seine Variable und braucht keine Kenntnis
vom Tresor. Die Verfügbarkeitssorge ist damit erledigt: Tresor aus heißt
*neue Starts blockiert*, nicht *laufende Dienste tot*.

**Weg B — Ausgabe pro Aufruf gegen Freigabe.** Unverändert, wie Rowboat es
heute nutzt. Für das, was ein Agent tut.

Danach gibt es eine Stelle zum Rotieren, eine zum Widerrufen und ein
Inventar. Die Mechanismen bleiben getrennt, weil sie getrennt gehören — die
Wahrheit nicht.

## Offene Entscheidungen — nicht erfunden, sondern benannt

**D1 (das Tor): bekommt `:4200` einen `api_key`?** Ohne ihn kann der
produktive Daemon keine Credentials ausgeben, heute nicht und nach einem
Binary-Tausch auch nicht. Mit ihm muss **jeder** Client auf dieser Maschine
auf Bearer-Auth umgestellt werden. Das ist eine Entscheidung über die
Maschine, kein Deployment-Schritt, und sie gehört dem Betreiber. Alles
Weitere hängt daran.

**D2: welcher Tresor ist die Autorität?** Naheliegend ist OpenFangs eigener
— er existiert, ist verschlüsselt, hat eine CLI und einen Endpunkt. Dagegen
spricht, dass damit die Verfügbarkeit *aller Starts* an OpenFang hängt.
Alternative wäre ein eigener Speicher, aus dem OpenFang seinerseits gespeist
wird. Nicht entschieden.

**D3: welche Konsumenten wandern, in welcher Reihenfolge — und welche nie?**
Weg B lohnt nur dort, wo ein Aufruf wirklich durch ein Freigabe-Tor soll.
Für einen Dienst, der beim Start einen Schlüssel braucht und ihn dann hält,
ist Weg A richtig und bleibt richtig. Die Frage ist also nicht „wie kommt
der Schlüssel überall hin", sondern „welche Aufrufe sollen überhaupt
genehmigt werden".

## Was dieser Entwurf NICHT behauptet

- Er behauptet nicht, dass die neun doppelten Schlüssel heute auseinander
  sind. Sie sind es nicht, gemessen.
- Er behauptet nicht, dass OpenFangs Tresor der richtige Ort ist. Das ist
  D2 und offen.
- Er behauptet nicht, dass alle Dienste auf Ausgabe pro Aufruf gehören. Das
  Gegenteil steht oben.
- Er sagt nichts über die VM oder andere Maschinen. Gemessen wurde nur
  dieser Host.

## Betriebsbefunde vom selben Tag, die hierher gehören

**CLI und laufender Daemon teilen den Tresor-Zustand nicht.** `vault remove`
schrieb `vault.enc` (Zeitstempel bestätigt), aber der seit 20 Minuten
laufende Daemon antwortete weiter aus seinem Speicherstand — der Eintrag galt
für ihn als vorhanden, und ein `store` scheiterte erneut mit 409. Erst ein
Neustart ließ ihn die Datei neu lesen. Beim Herunterfahren schrieb er
**nicht** zurück (geprüft, bevor neu gestartet wurde — sonst wäre der
Eintrag wiederauferstanden).

**Die Lehre daraus ist allgemeiner als der Befund.** Die Gegenprobe zur
Löschung lief gegen die CLI — also gegen die Instanz, die die *Datei* liest,
nicht gegen die, die die *Frage beantwortet*. Sie war korrekt und bewies das
Falsche. Wer künftig einen Tresorzustand prüft, muss ihn gegen den
laufenden Daemon prüfen, nicht gegen die CLI.

**`verifiziert` war als Durchgangszustand entworfen und ist ein
Ruhezustand.** Siehe den Nachtrag in
`2026-09-12-eingabefenster-nachzieher.md`; behoben in `0006`. Gefunden hat
das nicht die Testsuite, sondern der erste echte Durchgang — die Tests
prüften die Antwortform des 409, nicht die Fortsetzung danach.
