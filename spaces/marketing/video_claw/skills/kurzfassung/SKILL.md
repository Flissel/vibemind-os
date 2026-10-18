---
name: kurzfassung
description: "Langes Video kürzen: die stärksten 15–60 Sekunden, in denen die Person SELBST spricht, als Reel mit Originalton und Untertiteln aus ihrem Transkript; optional Titel- und Schlusskarte. Bei Aufträgen der Art 'kurzfassung'."
---

# Kurzfassung – die Person spricht selbst

1. **Video.** Das lange Video steht im Briefing (`video`). `material_suchen` liefert dazu `projekt_id` –
   **das Reel wird in diesem Projekt gebaut** (nur dort hat das Video sein Transkript). Kein
   `projekt_anlegen`, kein `material_uebernehmen`.
2. **Transkript lesen.** `transkript` – die Segmente tragen `start_frame` (Frame ÷ 30 = Sekunde).
3. **Stelle wählen.** Ein zusammenhängender Abschnitt (oder 2–3 kurze), der den einen Gedanken aus dem
   Thema trägt und **mit einem ganzen Satz beginnt und endet**. Zusammen 15–60 s (`dauer_s` aus dem
   Briefing). Nicht mitten im Wort schneiden: Start = `start_frame` eines Segments, Ende = Start des
   nächsten Segments.
4. **Optional rahmen.** Eine `titelkarte` mit dem Thema (≤ 6 Wörter, 2–3 s) davor und/oder eine
   Schlusskarte mit Handlung danach. Jeder Karten-Beat ist ein Beat mit `text` und wird **eingesprochen**
   (ein kurzer Satz, bei einer Karte von 2,5 s höchstens 6 Wörter). Ohne `sprechertext` im Briefing
   lieber ganz ohne Karten – dann besteht das Reel nur aus Originalton.
5. **Bauen.** `reel_bauen` mit Beats `{"originalton": true, "asset_id": <Video>, "src_in_frame": <Start>,
   "src_out_frame": <Ende>}` (und ggf. den Karten-Beats davor/danach), `hook` = Thema in einer Zeile.
6. Notiz: welche Stelle (Sekunden von–bis) und warum, ein Satz. Dann `EXPORT: <id>`.

Hinweis „Stimme: KI“ erscheint nur, wenn ein Karten-Beat eingesprochen ist – reiner Originalton bekommt
keinen KI-Hinweis.
