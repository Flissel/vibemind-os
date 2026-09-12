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
* **Interne Eigennamen gehoeren nie in den Text.** Nicht Mirofish, nicht
  Rachel, kein Codename, kein Spaces-Name, kein Dienstname. Umschreiben,
  was die Sache TUT: „simulierte Publikums-Tests" statt „Mirofish", „eine
  einzige Sprachsteuerung" statt „Rachel".
  Diese Regel wird verletzt, sobald ein Name gut klingt — gemessen
  04.09.2026 stand „Rachel" im Text, obwohl die Regel schon galt. Ein
  Eigenname, den kein Aussenstehender kennt, erklaert nichts; er zwingt den
  Leser, dir zu glauben, und verraet nebenbei die interne Struktur.

## Schritt 6 — Der Link ist echt oder es gibt keinen

Vier der sieben Entwuerfe endeten mit `[Link]`. Ein Platzhalter im
fertigen Entwurf ist eine Zumutung fuer den, der freigibt: er muss den
Fehler finden, den du hinterlassen hast.

* Echte Adresse einsetzen. Kennst du sie nicht, frag den Betreiber —
  im Chat, nicht im Text.
* Bis dahin: kein Platzhalter, sondern eine Zeile unter `## Zu klaeren`,
  die sagt, welche Adresse fehlt.

**KEIN Platzhalter heisst: keiner, in keiner Schreibweise.** Nicht
`[Link]`, nicht `<hier Adresse>`, nicht `TODO`, nicht `xxx`. Die Regel
gilt der Sache, nicht der Schreibweise: steht im Text eine Stelle, die
noch jemand ausfuellen muss, gehoert sie nicht in den Text.

**Merge-Felder sind erlaubt — aber nur die elf, die es wirklich gibt.**
Der Versand ersetzt `{{feld}}` gegen eine fest einprogrammierte Liste
(`_send_paranoid.py:508`):

`first_name` · `last_name` · `full_name` · `display_name` · `email` ·
`company` · `title` · `domain` · `campaign_name` · `msgid_core` ·
`unsub_url`

Jedes andere `{{...}}` ist KEIN Merge-Feld, sondern ein Platzhalter — und
der Versand wirft dabei einen Fehler (`unknown merge field`), statt still
etwas Kaputtes zu schicken. Ein Entwurf mit `{{WAITLIST_LINK}}` ist damit
nicht unfertig, sondern unsendbar.

Gemessen 04.09.2026, und der Irrtum ist nachvollziehbar: nachdem `[Link]`
verboten war, erschien `{{WAITLIST_LINK}}` — in der Annahme, das sei ein
richtiges Merge-Tag. Ist es nicht. Wenn du eine Adresse brauchst, die
nicht in der Liste oben steht, gehoert sie als echte URL in den Text oder
als Frage unter `## Zu klaeren`.

## Schritt 7 — Endpruefung, Zeile fuer Zeile

Den fertigen Text noch einmal lesen und diese fuenf Fragen beantworten.
Jedes Nein heisst: zurueck in den Text, nicht weiter zu Schritt 8.

1. **Schablone?** Gegen die Historie aus Schritt 1 halten. Jede
   Uebereinstimmung ab sechs Woertern Folge: umformulieren. Steht wieder
   dieselbe Vierer-Liste da, war Schritt 4 nicht ernst gemeint.
2. **Platzhalter?** Den Text nach `[`, `{`, `<`, `TODO`, `xxx` absuchen.
   Jedes `{{feld}}` gegen die Elferliste aus Schritt 6 halten — steht es
   nicht darin, ist es ein Platzhalter und macht den Entwurf unsendbar.
   Jeder andere Treffer ist ein Fehler, auch ein huebsch benannter.
3. **Interner Eigenname?** Jeder Name, den ein Aussenstehender nicht
   kennen kann, muss raus.
4. **Ein Gedanke?** Wenn du den Text in einem Satz zusammenfasst und
   dabei „und ausserdem" brauchst, sind es zwei.
5. **Nutzen oder Funktion?** Jeden Satz pruefen: steht da, was das System
   TUT, oder was der Leser DAVON HAT? Das zweite gehoert in den Text.

## Schritt 8 — Ablegen (redaktionell), nie selbst senden

`kampagne_entwerfen(...)` legt den Entwurf ab (Status draft, versendet
nichts). Danach dem Betreiber EINEN Satz: welcher Gedanke gewaehlt wurde,
welche zwei es noch gaebe, und was unter „Zu klaeren" steht.

Freigabe und Versand sind seine Entscheidung, nie deine.

**Was ein Entwurf IST und was er NICHT ist.** Er ist das redaktionelle
Artefakt: er landet in der Freigabe-Oberflaeche, wird dort gelesen,
geaendert und beurteilt. Er ist **nicht** der Weg nach draussen. Das war
lange missverstaendlich, und die Zahlen zeigen, wie teuer: dieser Space hat
in seiner ganzen Existenz **keine einzige Nachricht zugestellt** —
`campaign_sends`, `campaign_sends_openfang` und `campaign_sends_telegram`
haben je **0 Zeilen** (gemessen 12.09.2026), waehrend nebenan 25 Nachrichten
wirklich rausgingen. Wer nur einen Entwurf anlegt, hat nichts verschickt.

## Schritt 8b — Der Weg nach draussen: sales-claw beauftragen

**Seit dem Betreiber-Entscheid vom 12.09.2026 versendet dieser Space
nichts mehr selbst.** Zugestellt wird ausschliesslich ueber sales-claw — pro
Kontakt, mit den Toren, die dort haengen. Marketings eigene Versender sind
gesperrt; sie wuerden gar nicht mehr anlaufen.

```
versand_beauftragen(kanal="email",
                    empfaenger="<E-Mail-Adresse>",
                    betreff="<Betreff>",
                    nachricht="<dein Text>",
                    medien_datei="<optional, blosser Dateiname>",
                    kampagne="<optional>",
                    quelle="broadcast_proposal:<id des Entwurfs>")
```

`quelle` ist keine Zierde: sie haelt den Auftrag mit dem Entwurf zusammen,
aus dem er kam. Ohne sie steht spaeter ein Text in der Welt, zu dem niemand
mehr das Briefing findet.

Es entsteht daraus **hoechstens ein Entwurf** bei sales-claw, den ein Mensch
freigibt. Es geht nichts automatisch raus.

**Eine Absage ist kein Fehler, sondern eine Auskunft.** Kommt
`{"ok": false, "fehler": "..."}`, steht darin woertlich, welches Tor
zugemacht hat — „Kein Kontakt in sales-claw zu …", „… steht auf der
gemeinsamen Verbotsliste", „Erstansprache ohne dokumentierte Grundlage".
Keine davon loest sich durch Umformulieren. Mit `versandauftraege_lesen()`
siehst du spaeter, was aus deinen Auftraegen geworden ist.

**Telegram geht hier nicht.** sales-claw hat dafuer keinen Versandweg, und
dieser Space darf nicht mehr selbst senden. Ein Telegram-Text bleibt
vorerst ein Entwurf fuer den Betreiber — sag ihm das dazu, statt es
unerwaehnt zu lassen.

## Schritt 9 — Wenn eine Unterlage dazugehoert: PDF

Ein Newsletter, ein Einseiter, ein Angebotsblatt — alles, was der Empfaenger
BEHALTEN soll, gehoert als PDF dazu. `pdf_erstellen(name, titel, text, ...)`
setzt es im Gewand des Pitch-Decks und legt es dort ab, wo sales-claw es
anhaengen kann.

Warum nicht Markdown: `medien_liste` bei sales-claw zeigt eine `.md` nicht
einmal an — erlaubt sind `.pdf .jpg .jpeg .png .mp3 .ogg .mp4 .ics`
(gemessen 04.09.2026). Eine Markdown-Datei liegt im richtigen Ordner und ist
trotzdem unversendbar.

* `zweck` waehlt den Namensanfang: `marketing`, `email` oder `mobile`.
  Unterordner gibt es nicht — sales-claw lehnt jeden Pfadanteil ab.
* `handlung` ist der Aufruf zum Handeln und erscheint als heller Kasten.
  Nur mit ECHTER Adresse fuellen; fehlt sie, leer lassen und die fehlende
  Adresse unter `zu_klaeren` nennen. Ein Platzhalter im Kasten ist der
  sichtbarste Platzhalter, den es gibt.
* `belege` und `zu_klaeren` wandern als eigene Rubriken ans Ende — dieselbe
  Belegpflicht wie im Text, nur gedruckt.

Nicht jede Kampagne braucht ein PDF. Eine kurze Telegram-Nachricht mit
Anhang ist schlechter als eine ohne.

**Zu einem BESTEHENDEN Entwurf:** `pdf_aus_entwurf(proposal_id, layout=…)`.
Das nimmt Text, Belege und offene Fragen aus der Datenbank — du gibst nur
die Kennung und das Gewand. `layout="dunkel"` ist das Pitch-Deck-Gewand
fuer den Bildschirm, `layout="hell"` fuer Druck und Weiterleitung.

Der Unterschied ist nicht Bequemlichkeit: eine zweite Fassung, die du neu
schreibst, weicht immer von der ersten ab — und dann steht in der
Freigabe-UI etwas anderes als im PDF. Aus dem Entwurf gesetzt, ist es
dasselbe.
