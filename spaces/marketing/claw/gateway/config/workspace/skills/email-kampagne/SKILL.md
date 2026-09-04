---
name: email-kampagne
description: "Kampagnentext entwerfen (E-Mail, Telegram, Newsletter) — fester Ablauf: Historie lesen, Rohstoff holen, Leser vor Produkt, EIN Gedanke, Belege ohne Abschreiben, echter Link, Entwurf zur Freigabe. Bei jeder bestellten Kampagne nutzen."
metadata: { "openclaw": { "emoji": "✉️" } }
---

# Kampagnentext — der Ablauf

Gilt fuer E-Mail, Telegram und Newsletter. Das Ergebnis ist IMMER ein
Entwurf. Versendet wird ausschliesslich ueber die Freigabe des Betreibers.

Dieser Ablauf ist aus sieben echten Entwuerfen entstanden (02.–04.09.2026,
alle zur selben Kampagne). Jeder Schritt steht gegen einen Fehler, der in
diesen sieben nachweisbar drinsteht.

## Schritt 1 — Historie lesen (Pflicht, zuerst)

`entwuerfe_lesen()` aufrufen. Zwei Dinge herausschreiben:

* Welche **Betreffzeilen** gab es schon? Ein neuer Entwurf zum selben
  Thema braucht einen anderen Einstieg, nicht dieselbe Zeile mit anderem
  Verb.
* Welche **Saetze und Aufzaehlungspunkte** wiederholen sich? Die sind
  verbraucht. Anders formulieren oder weglassen.

Der Befund, der diesen Schritt erzwingt: sieben Entwuerfe, siebenmal
dieselben vier Punkte — Freigabe-Gate, Publikums-Simulation,
Sicherheits-Check, Launcher-Topologie. Nur die Emojis wechselten
(✅ → – → •). Das ist eine Schablone, und Schablonen erkennt jeder Leser.

## Schritt 2 — Rohstoff holen, bevor du schreibst

* `wissen_fragen("<deine Frage zum Thema>")` — befragt ALLE Quellen und
  liefert Belege mit Quelle und Dokument. Nicht `dokumente()` benutzen,
  um eine einzelne Quelle zu lesen: genau so entstand der Zustand, in dem
  sich alle Entwuerfe auf ein einziges Dokument beriefen.
* `videos()` — gibt es Bewegtbild zum Thema? Wenn ja,
  `video_transkript(kennung)` lesen: der gesprochene Text ist oft
  konkreter als jedes Konzeptdokument, und ein Video im Beitrag schlaegt
  jede Aufzaehlung.
* Frage mehr als einmal. Eine Frage bringt eine Antwort; drei Fragen aus
  verschiedenen Richtungen bringen den Blickwinkel, den noch niemand hatte.

## Schritt 3 — Den Leser finden, nicht das Produkt

Bevor eine Zeile Text entsteht, beantworte schriftlich fuer dich:

* Wer liest das (Rolle, Alltag, Werkzeuge)?
* Was kostet ihn HEUTE das Problem — Zeit, Geld, Nerven, Kunden?
* Woran erkennt er sich in der ersten Zeile wieder?

Der Text beginnt mit dieser Szene. Das Produkt kommt danach, als Antwort.
„VibeMind ist ein Agentic OS fuer …" als Einstieg ist verboten — genau so
begannen vier der sieben Entwuerfe.

## Schritt 4 — EIN Gedanke, keine Funktionsliste

Der haeufigste und teuerste Fehler in den sieben Entwuerfen: vier bis
fuenf Funktionen nebeneinander, jede in einem Punkt, keine davon
ausgefuehrt. Das liest sich wie ein Datenblatt und ueberzeugt niemanden.

* **Ein** Gedanke pro Kampagne. Die anderen drei Funktionen sind Stoff
  fuer die naechste — schreib sie dir auf, aber nicht in diesen Text.
* Jede Aussage aus der Sicht des Lesers, nicht der Technik.
  Nicht: „Deployments laufen ueber einen Launcher, der die gesamte
  Systemtopologie kennt."
  Sondern: „Du musst nicht mehr raten, was gerade wo laeuft."
* Kurze Zeilen, Absaetze nach ein bis zwei Saetzen.
* E-Mail und Telegram: deutlich unter 1500 Zeichen. Kuerzer ist fast
  immer besser.

## Schritt 5 — Belegen, ohne abzuschreiben

Die Belegpflicht gilt unveraendert: jede Produktaussage nennt Quelle und
Dokument. ABER — und das ist der Punkt, an dem die Entwuerfe kippten:

**Belegt wird die Aussage, formuliert wird der Satz selbst.**

Als die Belegpflicht kam, wurde aus den Entwuerfen eine Abschrift der
Quelle: die Punkte des Ausgangsdokuments standen der Reihe nach im Text,
teils woertlich („built in plain sight"). Das ist belegt und trotzdem
schlecht. Ein Beleg sagt, dass etwas WAHR ist — nicht, wie es klingen muss.

* Der `## Belege`-Abschnitt traegt das Zitat.
* Der Fliesstext traegt deinen eigenen Satz.
* Was du nicht belegen kannst, kommt unter `## Zu klaeren` — als Frage an
  den Betreiber, nie als Behauptung im Text. Ein leeres „Zu klaeren" ist
  fast immer ein Zeichen, dass du zu wenig gefragt hast.
* Interne Eigennamen (Mirofish, Rachel, Codenamen) gehoeren nie in den
  Text. Umschreiben — „simulierte Publikums-Tests" statt „Mirofish".

## Schritt 6 — Der Link ist echt oder es gibt keinen

Vier der sieben Entwuerfe endeten mit `[Link]`. Ein Platzhalter im
fertigen Entwurf ist eine Zumutung fuer den, der freigibt: er muss den
Fehler finden, den du hinterlassen hast.

* Echte Adresse einsetzen. Kennst du sie nicht, frag den Betreiber —
  im Chat, nicht im Text.
* Bis dahin: kein Platzhalter, sondern eine Zeile unter `## Zu klaeren`,
  die sagt, welche Adresse fehlt.

## Schritt 7 — Selbstpruefung gegen Schritt 1

Den fertigen Text noch einmal gegen die Historie halten. Jede
Uebereinstimmung ab sechs Woertern Folge: umformulieren. Steht wieder
dieselbe Vierer-Liste da, hast du Schritt 4 nicht gemacht.

## Schritt 8 — Ablegen, nie senden

`kampagne_entwerfen(...)` legt den Entwurf ab (Status draft, versendet
nichts). Danach dem Betreiber EINEN Satz: welcher Gedanke gewaehlt wurde,
welche zwei es noch gaebe, und was unter „Zu klaeren" steht.

Freigabe und Versand sind seine Entscheidung, nie deine.
