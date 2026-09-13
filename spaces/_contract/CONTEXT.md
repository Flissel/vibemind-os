# CONTEXT — Begriffe des Space-Vertrags

Ein Glossar, kein Handbuch. Jeder Eintrag sagt, was der Begriff **bedeutet** —
nicht, wo er im Code steht oder wie er implementiert ist. Wer eine Datei sucht,
ist hier falsch; wer wissen will, ob „Notiz" und „Eintrag" dasselbe sind, richtig.

`_Vermeiden_` listet die Wörter, die denselben Begriff verwaschen. Sie sind nicht
verboten, aber sie bedeuten hier etwas anderes oder gar nichts — wer sie benutzt,
muss damit rechnen, dass zurückgefragt wird.

Der Intake der Space-Pipeline prüft die Beschreibung eines neuen Space gegen
dieses Glossar, **bevor** aus unscharfer Sprache Tool-Namen werden. Wenn hier ein
Begriff fehlt, den ein Space braucht, wird er hier ergänzt — nicht im Space
umschifft.

---

## Space

Eine abgegrenzte Zuständigkeit von VibeMind, die ihre eigenen Ereignisse besitzt
und von genau einem Agenten bedient wird. Ein Space ist eine Zuständigkeit, kein
Ort: Er wird daran erkannt, welche Ereignisse ihm gehören, nicht daran, wo seine
Dateien liegen.

_Vermeiden_: Modul, Plugin, App, Verzeichnis, Komponente

## Space-Vertrag

Die vollständige Beschreibung eines Space, aus der sich alles Übrige ableiten
lässt: seine Tools, seine Events, seine Oberfläche, seine Laufzeit. Der Vertrag
ist das Ziel der Beschreibung, nicht ihre Zusammenfassung — was nicht im Vertrag
steht, existiert für die Erzeugung nicht.

_Vermeiden_: Spezifikation, Konfiguration, Schema, Manifest

## Tool

Eine einzelne aufrufbare Operation eines Space, mit benannten Parametern, einer
benannten Rückgabe und einer Angabe, ob sie liest oder schreibt.

_Vermeiden_: Funktion, Endpunkt, Aktion, Befehl, Skill

## Event

Eine benannte Absicht im Namensraum eines Space, die auf genau ein Tool zeigt.
Das Event ist das, was der Brain routet; das Tool ist das, was dann läuft. Ein
Event ohne Tool ist keine halbe Verdrahtung, sondern keine.

_Vermeiden_: Nachricht, Trigger, Kommando, Signal, Intent

## Prefix

Der Namensraum, der einem Space gehört. Alle seine Events beginnen damit, und
kein zweiter Space darf ihn beanspruchen — sonst gewinnt beim Routen der später
eingetragene, still.

_Vermeiden_: Namensraum-Präfix, Kategorie, Gruppe, Bereich

## Capability

Die Einheit, über die der Brain eine Absicht auf eine Ausführung abbildet — mit
Erkennungsmustern, einem Ausführungsziel und optional einem Truth-Validator.
Eine Capability ist die Sicht des Brain auf eine Handlung; ein Tool ist die
Sicht des Space auf dieselbe.

_Vermeiden_: Fähigkeit (unscharf), Skill, Tool, Funktion

## Agent

Die identifizierte Instanz, die die Events eines Space entgegennimmt und dessen
Tools benutzen darf. Ein Agent hat einen Namen, einen Auftrag und eine
Werkzeug-Erlaubnis — er ist keine Person und kein Modell, sondern eine Rolle.

_Vermeiden_: Bot, Assistent, Modell, LLM, Worker

## Side effect: read / write

Die Angabe, ob ein Tool die Welt nur befragt oder sie verändert. `write` ist kein
Attribut, sondern eine Verpflichtung: Ein schreibendes Tool zieht Provenance-
und Truth-Pflichten nach sich, und ohne die wird sein Space gar nicht erst
erzeugt.

_Vermeiden_: Mutation, Update, Änderung, Schreibzugriff

## Provenance

Der mitgeführte Nachweis, dass eine schreibende Handlung gedeckt war — wer sie
freigegeben hat, wogegen sie abgerechnet wird. Provenance beantwortet „durfte
das passieren", nicht „ist es passiert".

_Vermeiden_: Log, Audit-Trail, Metadaten, Historie, Nachweis (allein)

## Truth-Validator

Eine **unabhängige** Rückfrage an die Welt, ob der behauptete Endzustand
tatsächlich eingetreten ist. Unabhängig heißt: Er fragt die Quelle neu, statt die
Antwort der Operation zu glauben. Der Selbstbericht einer Operation ist nie ein
Truth-Validator, auch wenn er dasselbe behauptet.

_Vermeiden_: Test, Check, Prüfung, Bestätigung, Verifikation, Assertion

## Postcondition

Der konkrete, nachlesbare Endzustand, den ein Truth-Validator abfragt — welche
Quelle, welcher Filter, ob vorhanden oder abwesend. Eine Postcondition ohne
Filter ist keine schwächere Prüfung, sondern keine.

_Vermeiden_: Ergebnis, Rückgabe, Erwartung, Zielzustand

## Registry

Die eine Stelle, die entscheidet, welcher Agent ein Event bekommt und welche
Tools er dafür sehen darf. Die Registry routet; sie prüft nichts und garantiert
nichts.

_Vermeiden_: Konfiguration, Katalog, Index, Verzeichnis, Liste

## Manifest

Die Selbstauskunft eines Agenten: welchen Namensraum er besitzt und welche
Events er beansprucht. Ein Manifest beschreibt Zugehörigkeit, es erteilt keine
Rechte — die Bindung an Tools entsteht anderswo.

_Vermeiden_: Profil, Definition, Beschreibung, Konfiguration

## Sidecar

Ein eigenständiger Prozess, der neben dem Hauptsystem läuft, von ihm gestartet
und beendet wird und über eine eigene Adresse erreichbar ist. Ein Sidecar gehört
zu einem Space, ist aber nicht Teil desselben Prozesses.

_Vermeiden_: Service, Dienst, Daemon, Worker, Container

## Status-Probe

Der Nachweis, dass ein Space gerade wirklich läuft, geführt von außen gegen
seine eigene Adresse. Dass ein Prozess existiert, ist keine Status-Probe.

_Vermeiden_: Healthcheck, Ping, Liveness, Heartbeat

## Artefakt

Eine der Dateien, aus denen ein Space besteht — jede mit einer festen Form, die
sich aus dem Vertrag ableiten lässt. „Artefakt" meint immer etwas Erzeugtes und
Prüfbares, nie ein Nebenprodukt.

_Vermeiden_: Output, Ergebnis, Datei, Deliverable, Erzeugnis

## Bubble

Ein Themenraum für Ideen: die Sammelstelle, in der Material zu einem Vorhaben
liegt, bevor daraus Anforderungen werden. Eine Bubble ist ein Thema, kein
Vorhaben — aus einer Bubble kann ein Projekt werden, sie ist aber keines.

_Vermeiden_: Projekt, Ordner, Notiz, Board, Sammlung

## Requirement

Eine einzelne, überprüfbare Forderung an ein zu bauendes System, abgeleitet aus
dem Material einer Bubble. Ein Requirement ist überprüfbar oder es ist keines —
eine Absichtserklärung, an der man nicht messen kann, gehört in die Bubble
zurück.

_Vermeiden_: Feature, Wunsch, Ziel, Story, Idee

## Job

Ein einzelner Lauf der coding-engine über einen Satz Requirements, mit eigenem
Fortschritt und eigenem Ergebnis. Ein Job gehört zu einem Projekt; er ist keines.

_Vermeiden_: Projekt, Auftrag, Run, Build

## Task

Eine Arbeitseinheit innerhalb eines Jobs, mit Abhängigkeiten zu anderen Tasks
desselben Jobs.

_Vermeiden_: Job, Schritt, Ticket, Aufgabe (allein), Subtask
