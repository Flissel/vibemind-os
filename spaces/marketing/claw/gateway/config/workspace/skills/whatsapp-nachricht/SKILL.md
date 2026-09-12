---
name: whatsapp-nachricht
description: "WhatsApp-Nachricht entwerfen — Einwilligung zuerst, sehr kurz, kein Werbeton, lesbar ohne Vorschau, immer nur ein Entwurf. Bei jeder WhatsApp-Ansprache nutzen."
metadata: { "openclaw": { "emoji": "💬" } }
---

# WhatsApp-Nachricht — der Ablauf

WhatsApp ist kein Kanal wie E-Mail. Der Posteingang gehoert dem
Privatleben; dieselbe Nachricht, die in einer Mail sachlich wirkt, wirkt
hier aufdringlich. Kuerzer, ruhiger, und nur an Menschen, die zugestimmt
haben.

Das Ergebnis ist IMMER ein Entwurf. Versendet wird ueber die Freigabe des
Betreibers.

## Schritt 0 — Einwilligung, sonst gar nicht

Ohne dokumentierte Einwilligung entsteht KEIN Entwurf. Nicht „vermutlich
in Ordnung", nicht „ist ja ein Bestandskunde" — dieselbe Regel wie im
Vertrieb, und sie ist keine Formalie: eine unverlangte Werbenachricht auf
WhatsApp ist in Deutschland ein Wettbewerbsverstoss (UWG § 7).

Ist die Einwilligung nicht belegt, sag das dem Betreiber und hoere auf.
Ein Entwurf, der nicht versendet werden darf, ist kein halber Erfolg,
sondern eine Falle fuer den, der freigibt.

## Schritt 1 — Historie lesen

`entwuerfe_lesen(kanal="whatsapp")`. Dieselbe Person zweimal mit derselben
Formulierung anzuschreiben ist schlimmer als gar nicht zu schreiben.

## Schritt 2 — Rohstoff holen

`wissen_fragen("<Frage zum Thema>")` fuer die Belege, `videos()` falls
Bewegtbild passt. Auch eine kurze Nachricht braucht eine wahre Aussage —
die Kuerze ersetzt nicht die Belegpflicht, sie macht sie nur wichtiger:
in drei Saetzen faellt jede unbelegte Behauptung auf.

## Schritt 3 — Schreiben

* **Hoechstens drei Saetze.** Wer mehr braucht, schreibt eine E-Mail.
* **Der erste Satz muss ohne Vorschau tragen.** WhatsApp zeigt in der
  Uebersicht nur die ersten paar Woerter. „Hallo! Wir haben grossartige
  Neuigkeiten …" verbraucht diesen Platz mit nichts.
* **Kein Werbeton.** Keine Emojis als Aufzaehlungszeichen, keine
  Ausrufezeichen-Ketten, kein „🚀", kein „Jetzt sichern!". Schreib, wie du
  einem Bekannten schreiben wuerdest, der Zeit hat, aber nicht viel.
* **Kein Anhang ohne Bezug.** Ein Bild oder Video nur, wenn es die
  Nachricht traegt — nicht als Schmuck. Wenn ein Video passt, nenn in
  einem Halbsatz, was darin zu sehen ist; ungefragte Medien wirken wie
  Spam.
* **Ein Link, hoechstens.** Und ein echter. Platzhalter sind verboten,
  in JEDER Schreibweise — nicht `[Link]`, nicht `<hier Adresse>`, nicht
  `TODO`. Fehlt die Adresse, gehoert sie unter „Zu klaeren" und nicht in
  den Text.
* **Merge-Felder nur aus der festen Liste** (`_send_paranoid.py:508`):
  first_name, last_name, full_name, display_name, email, company, title,
  domain, campaign_name, msgid_core, unsub_url. Jedes andere `{{...}}`
  ist ein Platzhalter, und der Versand wirft dabei einen Fehler — der
  Entwurf waere nicht unfertig, sondern unsendbar. Gemessen 04.09.2026
  entstand genau so ein `{{WAITLIST_LINK}}`.
* **Keine internen Eigennamen.** Kein Mirofish, kein Rachel, kein
  Codename. Umschreiben, was die Sache tut.

## Schritt 4 — Der Ausgang gehoert dem Empfaenger

Schliesse so, dass ein „Nein danke" leicht faellt — eine kurze Frage oder
ein Satz, auf den man einfach antworten kann. Wer keinen bequemen Ausweg
laesst, bekommt keine Antwort, sondern eine Blockierung.

## Schritt 5 — Uebergeben, nicht ablegen

**Dieser Space verschickt kein WhatsApp, und er legt dafuer auch keinen
Entwurf an.** Frueher stand hier `kampagne_entwerfen(..., kanal="whatsapp")`.
Das war falsch und still falsch: der Entwurf landete in der Datenbank, sah
fertig aus — und konnte nie zugestellt werden, weil der Kanal hier keinen
Versandweg hat (`/api/channels`: `enabled: false, send_implemented: false`,
gemessen 04.09. und 12.09.2026). Seit dem 12.09. weist `kampagne_entwerfen`
diesen Kanal ab und nennt den richtigen Weg.

WhatsApp GEHT in diesem Haus, nur woanders: **sales-claw** verschickt es
ueber openwa, pro KONTAKT statt als Rundnachricht, mit eigener
Einwilligungspruefung am Kontakt. Das ist keine Einschraenkung, sondern die
richtige Form — eine Rundnachricht auf WhatsApp waere ohnehin das, was
Schritt 0 verbietet.

Dein Ergebnis ist deshalb **Text plus Begruendung, kein Entwurf**:

* Gib dem Betreiber den fertigen Nachrichtentext, hoechstens drei Saetze.
* Nenn dazu: an wen er gehen soll, woraus die Einwilligung hervorgeht, und
  was unter „Zu klaeren" offen blieb.
* Gehoert eine Unterlage dazu, leg sie mit `post_ablegen` ab — sales-claw
  findet sie dort und kann sie anhaengen (pdf, png, jpg, mp4, mp3, ogg, ics).

Den Entwurf in der Warteschlange legt dann sales-claw an, mit der
Kontaktkennung, die es kennt und du nicht.
