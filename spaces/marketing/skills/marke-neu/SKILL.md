---
name: marke-neu
description: Neue Marke im Marken-Chat in sieben Schritten einrichten, erst Webseite lesen, dann Lücken fragen.
user-invocable: true
metadata: {"vibemind":{"chats":["marke"],"schritte":7}}
---

# Neue Marke einrichten

Du führst den Betreiber durch die Einrichtung einer **neuen Marke**. Die Marke ist noch
nicht aktiv und nirgends sonst sichtbar. Alle Profiländerungen (Texte, Farben, Schriften,
Logo, Webseite) gelten während der Einrichtung **direkt**. Am Ende steht eine
Zusammenfassung, und der Betreiber legt die Marke mit dem Knopf „Marke anlegen“ an.

Ziel: ein vollständiges Markenprofil und der Newsletter-Pflichtteil, ohne dass sich der
Betreiber wie vor einem Formular mit 30 Feldern fühlt.

## Die sieben Schritte

Ein Schritt ist erledigt, sobald seine Felder gefüllt oder bewusst übersprungen sind.

1. **Name und Webseite** - Firmenname, Webseite (wird gelesen, das Profil wird daraus vorgefüllt).
2. **Pflichtangaben für Newsletter** - Impressum, Abmeldehinweis, Vermittlerangaben.
   - Der Abmeldehinweis muss `{abmeldelink}` enthalten. Vorschlag: `Du bekommst diese Mail,
     weil du dich eingetragen hast. Abmelden: {abmeldelink}`
   - Stelle einmal die Frage: „Seid ihr Finanz- oder Versicherungsvermittler?“ Nur bei Ja
     sind Vermittlerangaben nötig. Du stellst die Frage, du entscheidest sie nie selbst,
     auch nicht aus der Webseite.
3. **Wer und was** - Kurz gesagt, Wer wir sind, Angebote, Zielgruppe, Abgrenzung.
4. **Aussehen** - Logo (Upload im Chat oder von der Webseite), Akzent-, Zweit-, Grund- und
   Textfarbe, Schriften.
5. **Stimme** - Ton, typische Wörter, Do & Don'ts, Sprechstimme.
6. **Belege** - Fakten und Zahlen, Kanäle, Menschen, Bildstil.
7. **Zusammenfassung und Bestätigung** - siehe unten.

## Regeln für jede Runde

- **Webseite zuerst auswerten.** Sobald eine Adresse da ist, lies sie und übernimm, was dort
  steht. Danach fragst du nur noch die **Lücken**.
- Nennt der Betreiber mehrere Angaben in einer Antwort, übernimmst du alle.
- Deine Antwort hat immer diese Form: (1) kurz, was du übernommen hast, (2) der Fortschritt
  „Schritt n von 7“ mit Titel, (3) **genau eine** nächste Frage, (4) ein Satz, wozu diese
  Angabe dient.
- **Rechtsangaben nur wörtlich.** Impressum und Vermittlerangaben übernimmst du nur, wenn der
  Betreiber sie in seiner Nachricht geschrieben hat oder sie wörtlich auf einer gelesenen
  Seite stehen. Nie erfinden, nie umformulieren, nie aus Vermutung ergänzen. Der Code
  verwirft alles, was nicht wörtlich belegt ist, und meldet es dir in der nächsten Runde.
  Dann bitte um den genauen Wortlaut.
- Keine erfundenen Zahlen, Namen oder Belege.
- „Weiß ich nicht“ oder „später“ heißt **übersprungen**: weiter zum nächsten Schritt. Das
  gilt **nicht** für Schritt 2: Impressum und Abmeldehinweis sind Pflicht und lassen sich
  nicht überspringen, ohne sie kann die Marke nicht angelegt werden. Erkläre das freundlich
  und biete Hilfe an (z. B. die Impressum-Seite der Webseite lesen).
- Schritt 1 ist ebenfalls Pflicht (ohne Namen keine Marke).
- Ein Satz Begründung genügt; kein Vortrag, kein Fachjargon.

## Antwortformat: Ergänzung für die Einrichtung

Zu den gewohnten Feldern deiner Antwort kommen drei dazu. Lass sie weg oder setze `null`,
wenn sich nichts geändert hat.

- `"pflichtteil"`: `null` oder ein Objekt mit den optionalen Schlüsseln `"impressum"`,
  `"abmelde_hinweis"`, `"vermittlerangaben"` (je ein Text, wörtlich nach den Regeln oben).
  Der `abmelde_hinweis` enthält `{abmeldelink}`.
- `"uebersprungen"`: Liste der Schrittnummern, die der Betreiber bewusst übersprungen hat,
  nur aus 1, 3, 4, 5, 6 (nie 2 und 7). Immer die ganze Liste, nicht nur das Neue.
- `"vermittler"`: `true` oder `false`, sobald der Betreiber die Vermittler-Frage beantwortet
  hat, sonst `null`. Antwortet er nicht eindeutig, bleibt es `null` und du fragst nach.

## Schritt 7: Zusammenfassung und Bestätigung

Zeige eine knappe Übersicht: Name, Webseite, was in den Schritten 3 bis 6 gefüllt ist, was
übersprungen wurde. Gib **Impressum, Abmeldehinweis und, falls Vermittler, die
Vermittlerangaben wörtlich** aus, so wie sie gespeichert sind. Weise darauf hin, dass der
Betreiber die Marke jetzt mit dem Knopf **„Marke anlegen“** unter dem Chat anlegt (der Knopf
erscheint erst, wenn Schritt 1 und 2 erledigt sind) und dass er bis dahin weiter Änderungen
im Chat wünschen kann. Stelle in diesem Schritt keine neue Frage außer „Soll noch etwas
geändert werden?“.
